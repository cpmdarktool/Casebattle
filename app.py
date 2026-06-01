import random, os, re, json, threading, urllib.request, sqlite3, hashlib, time
from functools import wraps
from flask import Flask, render_template_string, request, jsonify, session, redirect

app = Flask(__name__)
app.secret_key = 'casebattle_secret_k3y_9x'

CRYPTO_BOT_TOKEN = '573098:AAx9n0XEj0mIxM5TEcyIHV5k6OX6KABMe9N'
CRYPTO_BOT_API   = 'https://pay.crypt.bot/api'
CRYPTO_BOT_UA    = 'Mozilla/5.0 (compatible; CaseBattle/1.0)'
TON_RATE         = 140.0
TON_MIN_RUB      = 50.0    # Minimum deposit in RUB
WITHDRAW_MIN_RUB = 200.0   # Minimum withdrawal in RUB

# ── DB ─────────────────────────────────────────────────────────────────────
_data_dir = os.environ.get('DB_DIR', '/data')
os.makedirs(_data_dir, exist_ok=True)
DB = os.path.join(_data_dir, 'casebattle.db')

def get_db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with get_db() as c:
        c.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                balance  REAL DEFAULT 100.0,
                is_admin INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id  INTEGER NOT NULL,
                skin_name TEXT NOT NULL,
                skin_img  TEXT NOT NULL DEFAULT '',
                price     REAL NOT NULL,
                acquired_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS deposits (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                rub_amount REAL NOT NULL,
                ton_amount REAL NOT NULL,
                invoice_id TEXT DEFAULT '',
                pay_url    TEXT DEFAULT '',
                status     TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS withdrawals (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL,
                wallet_address TEXT NOT NULL,
                rub_amount     REAL NOT NULL,
                ton_amount     REAL NOT NULL,
                status         TEXT DEFAULT 'pending',
                created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
        ''')
        pw = hashlib.sha256('7890p'.encode()).hexdigest()
        c.execute('INSERT OR IGNORE INTO users (username,password,balance,is_admin) VALUES (?,?,?,?)',
                  ('fsociety', pw, 999999.0, 1))
        c.commit()
    # Migrations for older DB versions
    for col_def in [
        'ALTER TABLE deposits ADD COLUMN invoice_id TEXT DEFAULT ""',
        'ALTER TABLE deposits ADD COLUMN pay_url TEXT DEFAULT ""',
    ]:
        try:
            with get_db() as c:
                c.execute(col_def); c.commit()
        except Exception:
            pass

init_db()

# ── CryptoBot helpers ──────────────────────────────────────────────────────
def cb_request(method, payload=None):
    url = f'{CRYPTO_BOT_API}/{method}'
    headers = {'Crypto-Pay-API-Token': CRYPTO_BOT_TOKEN, 'User-Agent': CRYPTO_BOT_UA}
    if payload:
        data = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, headers=headers)
    else:
        req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def poll_cryptobot():
    """Background thread: auto-confirm paid CryptoBot invoices every 20s."""
    while True:
        try:
            with get_db() as c:
                rows = c.execute(
                    "SELECT invoice_id, id, user_id, rub_amount FROM deposits "
                    "WHERE status='pending' AND invoice_id != '' LIMIT 50"
                ).fetchall()
            if rows:
                ids = ','.join(r['invoice_id'] for r in rows)
                url = f'{CRYPTO_BOT_API}/getInvoices?status=paid&invoice_ids={ids}'
                req = urllib.request.Request(url, headers={
                    'Crypto-Pay-API-Token': CRYPTO_BOT_TOKEN, 'User-Agent': CRYPTO_BOT_UA})
                with urllib.request.urlopen(req, timeout=12) as r:
                    result = json.loads(r.read().decode())
                if result.get('ok'):
                    for inv in result.get('result', {}).get('items', []):
                        if inv.get('status') == 'paid':
                            inv_id = str(inv['invoice_id'])
                            with get_db() as c:
                                dep = c.execute(
                                    "SELECT * FROM deposits WHERE invoice_id=? AND status='pending'",
                                    (inv_id,)).fetchone()
                                if dep:
                                    _set_balance(dep['user_id'],
                                                 _get_balance(dep['user_id']) + dep['rub_amount'])
                                    c.execute("UPDATE deposits SET status='approved' WHERE id=?",
                                              (dep['id'],))
                                    c.commit()
                                    print(f"[cb] invoice {inv_id} paid → +{dep['rub_amount']}₽ user {dep['user_id']}")
        except Exception as e:
            print(f"[cb poller] {e}")
        time.sleep(20)

_poll_thread = threading.Thread(target=poll_cryptobot, daemon=True)
_poll_thread.start()

# ── Skins + images ─────────────────────────────────────────────────────────
skins_data = [
    ("P250 | Sand Dune", 1), ("AK-47 | Safari Mesh (BS)", 50), ("Glock-18 | Oxide Blaze", 50),
    ("MP7 | Motherboard", 50), ("M4A4 | Tornado", 55), ("SSG 08 | Lichen Dashed", 60),
    ("USP-S | Forest Leaves", 65), ("Desert Eagle | Mudder", 70), ("AUG | Storm", 75),
    ("Galil AR | Sage Spray", 80), ("FAMAS | Doomkitty", 85), ("P90 | Sand Spray", 90),
    ("M4A1-S | Flashback", 95), ("SCAR-20 | Contractor", 100), ("MAC-10 | Candy Apple", 110),
    ("Five-SeveN | Forest Night", 120), ("UMP-45 | Delusion", 130), ("AWP | Safari Mesh (BS)", 140),
    ("P250 | Metallic DDPAT (MW)", 150), ("XM1014 | Bone Machine (BS)", 200),
    ("PP-Bizon | Harvester", 250), ("MP9 | Setting Sun", 300), ("Nova | Rising Skull", 350),
    ("M249 | Spectre", 400), ("M4A1-S | Brass", 500), ("Desert Eagle | Corinthian", 550),
    ("FAMAS | Sergeant", 600), ("MP7 | Impire", 700), ("USP-S | Guardian", 800),
    ("Glock-18 | Water Elemental (BS)", 900), ("AK-47 | Predator", 1000),
    ("M4A4 | Evil Daimyo", 1100), ("AWP | PAW", 1200),
    ("\u2605 Gut Knife | Scorched (BS)", 4500), ("\u2605 Navaja Knife | Safari Mesh (BS)", 4800),
    ("\u2605 Shadow Daggers | Safari Mesh (BS)", 5000),
    ("\u2605 Falchion Knife | Boreal Forest (BS)", 5500),
    ("\u2605 Huntsman Knife | Forest DDPAT (BS)", 6000),
    ("\u2605 Paracord Knife | Urban Masked (BS)", 6500),
    ("\u2605 Survival Knife | Night Stripe (BS)", 7000),
    ("\u2605 Nomad Knife | Safari Mesh (BS)", 8000),
    ("\u2605 Gut Knife | Vanilla", 9000),
    ("\u2605 Falchion Knife | Stained (FT)", 12000),
    ("\u2605 Shadow Daggers | Black Laminate (FT)", 15000),
    ("Bayonet | Rust Coat (BS)", 25000),
]

SKIN_IMAGES = {}
def _fetch_images():
    try:
        req = urllib.request.Request(
            'https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/skins.json',
            headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            for item in json.loads(r.read().decode()):
                n, img = item.get('name',''), item.get('image','')
                if n and img: SKIN_IMAGES[n] = img
        print(f"[images] {len(SKIN_IMAGES)} loaded")
    except Exception as e: print(f"[images] {e}")
_t = threading.Thread(target=_fetch_images, daemon=True); _t.start(); _t.join(timeout=12)

def _get_img(name):
    base = re.sub(r'\s*\([^)]*\)\s*$', '', name).strip()
    c = re.sub(r'_+', '_', re.sub(r'\([^)]*\)', '',
        name.replace('\u2605','').replace('|','_')).strip().replace(' ','_'))
    return SKIN_IMAGES.get(name) or SKIN_IMAGES.get(base) or \
           f"https://csgobackpack.net/img/items/png/{c}.png"

skin_list = [{"id":i,"name":n,"base_price":p,"image":_get_img(n)}
             for i,(n,p) in enumerate(skins_data)]
available_percents = [5,10,15,20,25,30,35,40,45,50,55,60,65,70]

def get_mult(p):
    if p==5: return 10.0
    if p==70: return 1.15
    return round(100/p, 2)

# ── Auth helpers ───────────────────────────────────────────────────────────
def hash_pw(p): return hashlib.sha256(p.encode()).hexdigest()

def current_user():
    uid = session.get('user_id')
    if not uid: return None
    with get_db() as c:
        return c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()

def login_required(f):
    @wraps(f)
    def w(*a, **kw):
        if not session.get('user_id'): return redirect('/login')
        return f(*a, **kw)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a, **kw):
        u = current_user()
        if not u or not u['is_admin']: return redirect('/')
        return f(*a, **kw)
    return w

def _get_balance(uid):
    with get_db() as c:
        return c.execute('SELECT balance FROM users WHERE id=?',(uid,)).fetchone()['balance']

def _set_balance(uid, val):
    with get_db() as c:
        c.execute('UPDATE users SET balance=? WHERE id=?',(round(val,2),uid)); c.commit()

# ══════════════════════════════════════════════════════════════════════════
# TEMPLATES
# ══════════════════════════════════════════════════════════════════════════
AUTH_HTML = """<!DOCTYPE html><html lang="ru"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Case Battle</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
  body{background:#0a0f1e;color:#eee;font-family:'Segoe UI',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center}
  .box{background:#111827;border:1px solid #1f2937;border-radius:20px;padding:36px 32px;width:360px;max-width:95vw}
  .logo{color:#ffaa33;font-size:1.7rem;font-weight:800;text-align:center;margin-bottom:24px}
  .logo span{color:#fff}
  .tab-btn{background:none;border:none;color:#6b7280;font-weight:600;padding:8px 20px;border-bottom:2px solid transparent;cursor:pointer}
  .tab-btn.active{color:#ffaa33;border-color:#ffaa33}
  .fc{background:#1f2937;border:1px solid #374151;color:#eee;border-radius:10px}
  .fc:focus{background:#1f2937;border-color:#ffaa33;color:#eee;box-shadow:none}
  .btn-a{background:linear-gradient(135deg,#ffaa33,#ff6b00);border:none;color:#000;font-weight:800;border-radius:20px;padding:10px;width:100%}
  .err{color:#f87171;font-size:.85rem}
</style></head><body>
<div class="box">
  <div class="logo">CASE <span>BATTLE</span></div>
  <div class="d-flex border-bottom border-secondary mb-4">
    <button class="tab-btn active" id="tL" onclick="sw('l')">Вход</button>
    <button class="tab-btn" id="tR" onclick="sw('r')">Регистрация</button>
  </div>
  {% if error %}<div class="err mb-2">{{ error }}</div>{% endif %}
  <div id="fL">
    <form method="POST" action="/login">
      <input type="hidden" name="action" value="login">
      <div class="mb-3"><input class="form-control fc" name="username" placeholder="Логин" required></div>
      <div class="mb-3"><input class="form-control fc" type="password" name="password" placeholder="Пароль" required></div>
      <button class="btn-a">Войти</button>
    </form>
  </div>
  <div id="fR" style="display:none">
    <form method="POST" action="/login">
      <input type="hidden" name="action" value="register">
      <div class="mb-3"><input class="form-control fc" name="username" placeholder="Логин" required></div>
      <div class="mb-3"><input class="form-control fc" type="password" name="password" placeholder="Пароль" required></div>
      <div class="mb-3"><input class="form-control fc" type="password" name="password2" placeholder="Повторите пароль" required></div>
      <button class="btn-a">Зарегистрироваться</button>
    </form>
  </div>
</div>
<script>
function sw(t){
  document.getElementById('tL').classList.toggle('active',t==='l');
  document.getElementById('tR').classList.toggle('active',t==='r');
  document.getElementById('fL').style.display=t==='l'?'':'none';
  document.getElementById('fR').style.display=t==='r'?'':'none';
}
{% if tab=='reg' %}sw('r');{% endif %}
</script></body></html>"""

INV_HTML = """<!DOCTYPE html><html lang="ru"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Case Battle — Инвентарь</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
<style>
  body{background:#0a0f1e;color:#eee;font-family:'Segoe UI',sans-serif}
  .tb{background:#111827;border-bottom:1px solid #1f2937;padding:10px 16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  .logo{color:#ffaa33;font-weight:800}
  .nl{color:#9ca3af;text-decoration:none;font-size:.88rem;padding:4px 12px;border-radius:12px}
  .nl:hover{background:#1f2937;color:#eee}
  .ic{background:#111827;border:1px solid #1f2937;border-radius:12px;padding:10px;text-align:center}
  .ic img{height:76px;width:100%;object-fit:contain}
  .in{font-size:.72rem;color:#9ca3af;margin-top:3px;line-height:1.2}
  .ip{color:#ffaa33;font-weight:700;font-size:.88rem}
  .sp{color:#6b7280;font-size:.7rem}
  .bp{background:#ffaa33;border:none;color:#000;font-weight:700;border-radius:12px;padding:3px 9px;font-size:.76rem;cursor:pointer}
  .bs{background:#ef4444;border:none;color:#fff;font-weight:700;border-radius:12px;padding:3px 9px;font-size:.76rem;cursor:pointer}
  .mw{background:#14532d;border:1px solid #22c55e;color:#86efac;border-radius:10px;padding:10px 14px;display:none}
  .ml{background:#450a0a;border:1px solid #ef4444;color:#fca5a5;border-radius:10px;padding:10px 14px;display:none}
</style></head><body>
<div class="tb">
  <span class="logo"><i class="fas fa-dice"></i> CASE BATTLE</span>
  <span style="flex:1"></span>
  <span style="color:#ffaa33;font-weight:700" id="bd">{{ balance|int }} &#8381;</span>
  <a href="/" class="nl"><i class="fas fa-gamepad"></i> Играть</a>
  <a href="/inventory" class="nl" style="background:#1f2937;color:#ffaa33"><i class="fas fa-box-open"></i> Инвентарь</a>
  {% if is_admin %}<a href="/admin" class="nl" style="color:#f87171"><i class="fas fa-crown"></i> Admin</a>{% endif %}
  <a href="/logout" class="nl"><i class="fas fa-sign-out-alt"></i> Выйти</a>
</div>
<div class="container-fluid px-3 py-3">
  <h5 class="text-warning mb-1"><i class="fas fa-box-open"></i> Инвентарь</h5>
  <p class="text-secondary" style="font-size:.8rem"><b style="color:#ffaa33">Играть</b> — поставить скин на кон &nbsp;|&nbsp; <b style="color:#ef4444">Продать</b> — скупка -15%</p>
  <div id="msg" class="mb-3"></div>
  {% if items %}
  <div class="row g-2">
    {% for it in items %}
    <div class="col-4 col-sm-3 col-md-2" id="ir{{ it.id }}">
      <div class="ic">
        <img src="{{ it.skin_img }}" onerror="this.src='https://via.placeholder.com/80x56/1f2937/9ca3af?text=CS2'">
        <div class="in">{{ it.skin_name[:26] }}</div>
        <div class="ip">{{ it.price|int }} &#8381;</div>
        <div class="sp">Скупка: {{ (it.price*0.85)|int }} &#8381;</div>
        <div class="d-flex gap-1 justify-content-center mt-1">
          <button class="bp" onclick="play({{ it.id }},this)"><i class="fas fa-play"></i></button>
          <button class="bs" onclick="sell({{ it.id }},this)"><i class="fas fa-tag"></i> -15%</button>
        </div>
      </div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="text-center py-5" style="color:#374151"><i class="fas fa-box-open fa-3x mb-3"></i><br>Инвентарь пуст.</div>
  {% endif %}
</div>
<script>
function showMsg(t,ok){const e=document.getElementById('msg');e.className=ok?'mw':'ml';e.style.display='block';e.innerHTML=t;}
async function sell(id,btn){
  btn.disabled=true;
  const r=await fetch('/sell_inventory',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({item_id:id})});
  const d=await r.json();
  if(d.success){showMsg('\u2705 '+d.message,true);document.getElementById('bd').textContent=Math.floor(d.balance)+' \u20bd';document.getElementById('ir'+id).style.opacity='.3';btn.textContent='Продано';}
  else{showMsg('\u274c '+(d.error||'Ошибка'),false);btn.disabled=false;}
}
async function play(id,btn){
  btn.disabled=true;
  const r=await fetch('/inventory/activate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({item_id:id})});
  const d=await r.json();
  if(d.success)window.location.href='/';
  else{showMsg('\u274c '+(d.error||'Ошибка'),false);btn.disabled=false;}
}
</script></body></html>"""

ADMIN_HTML = """<!DOCTYPE html><html lang="ru"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin — Case Battle</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
<style>
  body{background:#0a0f1e;color:#eee;font-family:'Segoe UI',sans-serif}
  .tb{background:#111827;border-bottom:1px solid #1f2937;padding:10px 16px;display:flex;align-items:center;gap:12px}
  .nl{color:#9ca3af;text-decoration:none;font-size:.88rem;padding:4px 12px;border-radius:12px}
  .nl:hover{background:#1f2937;color:#eee}
  .panel{background:#111827;border:1px solid #1f2937;border-radius:14px;padding:18px;margin-bottom:18px}
  .ph{color:#ffaa33;font-weight:700;margin-bottom:12px}
  .tbl{font-size:.82rem}.tbl th{color:#6b7280;border-color:#1f2937}.tbl td{border-color:#1f2937;vertical-align:middle}
  .ba{background:#7c3aed;color:#fff;border-radius:6px;padding:1px 7px;font-size:.7rem}
  .bu{background:#1f2937;color:#9ca3af;border-radius:6px;padding:1px 7px;font-size:.7rem}
  .bpend{background:#92400e;color:#fde68a;border-radius:6px;padding:1px 7px;font-size:.7rem}
  .bok{background:#14532d;color:#86efac;border-radius:6px;padding:1px 7px;font-size:.7rem}
  .inp{background:#1f2937;border:1px solid #374151;color:#eee;border-radius:8px;padding:4px 9px;font-size:.82rem;width:95px}
  .bg{background:#22c55e;border:none;color:#000;font-weight:700;border-radius:8px;padding:3px 9px;font-size:.77rem;cursor:pointer}
  .br{background:#ef4444;border:none;color:#fff;font-weight:700;border-radius:8px;padding:3px 9px;font-size:.77rem;cursor:pointer}
  .sb{background:#1f2937;border-radius:12px;padding:14px;text-align:center}
  .sn{font-size:1.4rem;font-weight:800;color:#ffaa33}.sl{font-size:.78rem;color:#6b7280}
  .tx{font-size:.7rem;color:#9ca3af;word-break:break-all;max-width:140px}
</style></head><body>
<div class="tb">
  <span style="color:#ffaa33;font-weight:800"><i class="fas fa-crown" style="color:#f87171"></i> ADMIN PANEL</span>
  <span style="flex:1"></span>
  <a href="/" class="nl"><i class="fas fa-gamepad"></i> Игра</a>
  <a href="/logout" class="nl"><i class="fas fa-sign-out-alt"></i> Выйти</a>
</div>
<div class="container-fluid px-3 py-3">
  <div class="row g-3 mb-3">
    <div class="col-3"><div class="sb"><div class="sn">{{ st.users }}</div><div class="sl">Игроков</div></div></div>
    <div class="col-3"><div class="sb"><div class="sn">{{ st.pending }}</div><div class="sl">Ожид. депозитов</div></div></div>
    <div class="col-3"><div class="sb"><div class="sn">{{ st.pending_wd }}</div><div class="sl">Ожид. выводов</div></div></div>
    <div class="col-3"><div class="sb"><div class="sn">{{ "%.0f"|format(st.bal) }}</div><div class="sl">Баланс всего ₽</div></div></div>
  </div>
  {% if msg %}<div class="alert alert-success py-2 mb-3">{{ msg }}</div>{% endif %}
  <div class="panel">
    <div class="ph"><i class="fas fa-coins" style="color:#3b82f6"></i> Депозиты TON (ожидают / последние)</div>
    {% if deps %}
    <table class="table tbl table-dark table-hover">
      <thead><tr><th>#</th><th>Юзер</th><th>₽</th><th>TON</th><th>Invoice ID</th><th>Статус</th><th>Дата</th><th>Действие</th></tr></thead>
      <tbody>
      {% for d in deps %}
      <tr>
        <td style="color:#374151">{{ d.id }}</td><td>{{ d.username }}</td>
        <td style="color:#ffaa33;font-weight:700">{{ d.rub_amount|int }}₽</td>
        <td style="color:#3b82f6;font-weight:700">{{ "%.4f"|format(d.ton_amount) }}</td>
        <td><div class="tx">{{ d.invoice_id or '—' }}</div></td>
        <td>{% if d.status=='approved' %}<span class="bok">Зачислен</span>{% elif d.status=='rejected' %}<span style="color:#f87171;font-size:.7rem">Откл.</span>{% else %}<span class="bpend">Ожидает</span>{% endif %}</td>
        <td style="color:#6b7280;font-size:.73rem">{{ d.created_at[:16] }}</td>
        <td>
          {% if d.status=='pending' %}
          <form method="POST" action="/admin/deposits/approve" style="display:inline">
            <input type="hidden" name="dep_id" value="{{ d.id }}">
            <button class="bg" type="submit"><i class="fas fa-check"></i></button>
          </form>
          <form method="POST" action="/admin/deposits/reject" style="display:inline;margin-left:3px">
            <input type="hidden" name="dep_id" value="{{ d.id }}">
            <button class="br" type="submit"><i class="fas fa-times"></i></button>
          </form>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
    {% else %}<p class="text-secondary mb-0" style="font-size:.84rem">Нет депозитов.</p>{% endif %}
  </div>
  <div class="panel">
    <div class="ph"><i class="fas fa-arrow-up" style="color:#a78bfa"></i> Заявки на вывод</div>
    {% if wds %}
    <table class="table tbl table-dark table-hover">
      <thead><tr><th>#</th><th>Юзер</th><th>₽</th><th>TON</th><th>Кошелёк</th><th>Статус</th><th>Дата</th><th>Действие</th></tr></thead>
      <tbody>
      {% for w in wds %}
      <tr>
        <td style="color:#374151">{{ w.id }}</td><td>{{ w.username }}</td>
        <td style="color:#ffaa33;font-weight:700">{{ w.rub_amount|int }}₽</td>
        <td style="color:#a78bfa;font-weight:700">{{ "%.4f"|format(w.ton_amount) }}</td>
        <td><div class="tx">{{ w.wallet_address }}</div></td>
        <td>{% if w.status=='approved' %}<span class="bok">Выплачен</span>{% elif w.status=='rejected' %}<span style="color:#f87171;font-size:.7rem">Откл.</span>{% else %}<span class="bpend">Ожидает</span>{% endif %}</td>
        <td style="color:#6b7280;font-size:.73rem">{{ w.created_at[:16] }}</td>
        <td>
          {% if w.status=='pending' %}
          <form method="POST" action="/admin/withdrawals/approve" style="display:inline">
            <input type="hidden" name="wd_id" value="{{ w.id }}">
            <button class="bg" type="submit" title="Подтвердить"><i class="fas fa-check"></i></button>
          </form>
          <form method="POST" action="/admin/withdrawals/reject" style="display:inline;margin-left:3px">
            <input type="hidden" name="wd_id" value="{{ w.id }}">
            <button class="br" type="submit" title="Отклонить и вернуть средства"><i class="fas fa-times"></i></button>
          </form>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
    {% else %}<p class="text-secondary mb-0" style="font-size:.84rem">Нет заявок на вывод.</p>{% endif %}
  </div>
  <div class="panel">
    <div class="ph"><i class="fas fa-users"></i> Пользователи</div>
    <table class="table tbl table-dark table-hover">
      <thead><tr><th>#</th><th>Логин</th><th>Роль</th><th>Баланс</th><th>Начислить</th></tr></thead>
      <tbody>
      {% for u in users %}
      <tr>
        <td style="color:#374151">{{ u.id }}</td><td>{{ u.username }}</td>
        <td>{% if u.is_admin %}<span class="ba">Admin</span>{% else %}<span class="bu">User</span>{% endif %}</td>
        <td style="color:#ffaa33;font-weight:700">{{ "%.2f"|format(u.balance) }}₽</td>
        <td>{% if not u.is_admin %}
          <form method="POST" action="/admin/credit" style="display:flex;gap:5px;align-items:center">
            <input type="hidden" name="user_id" value="{{ u.id }}">
            <input class="inp" type="number" name="amount" min="1" placeholder="Сумма">
            <button class="bg" type="submit"><i class="fas fa-plus"></i></button>
          </form>{% else %}<span style="color:#374151;font-size:.8rem">—</span>{% endif %}</td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div></body></html>"""

GAME_HTML = r"""<!DOCTYPE html><html lang="ru"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Case Battle</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
<style>
  *{box-sizing:border-box}
  body{background:#0a0f1e;color:#eee;font-family:'Segoe UI',sans-serif;min-height:100vh}
  .tb{background:#111827;border-bottom:1px solid #1f2937;padding:10px 16px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .logo{color:#ffaa33;font-weight:800;font-size:1.05rem}
  .bv{color:#ffaa33;font-weight:700;font-size:1.05rem}
  .nl{color:#9ca3af;text-decoration:none;font-size:.86rem;padding:4px 11px;border-radius:12px;transition:.15s}
  .nl:hover{background:#1f2937;color:#eee}
  .btn-plus{background:linear-gradient(135deg,#22c55e,#16a34a);border:none;color:#fff;font-weight:900;font-size:1.1rem;width:28px;height:28px;border-radius:50%;cursor:pointer;line-height:1;padding:0;display:inline-flex;align-items:center;justify-content:center}
  .btn-withdraw{background:linear-gradient(135deg,#7c3aed,#6d28d9);border:none;color:#fff;font-weight:700;font-size:.78rem;padding:5px 11px;border-radius:14px;cursor:pointer}
  /* active skin */
  .ac{background:#111827;border:2px solid #1f2937;border-radius:16px;padding:16px;min-height:150px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;transition:.3s}
  .ac.hs{border-color:#ffaa33}
  .ai{width:110px;height:78px;object-fit:contain}
  .an{font-size:.87rem;text-align:center;color:#d1d5db}
  .ap{font-size:1.35rem;font-weight:700;color:#ffaa33}
  .bco{background:#22c55e;border:none;color:#000;font-weight:700;border-radius:20px;padding:6px 18px;cursor:pointer;display:none;width:100%;margin-top:7px}
  /* pct */
  .pb{background:#1f2937;border:1px solid #374151;color:#9ca3af;border-radius:24px;padding:6px 13px;margin:3px;cursor:pointer;font-size:.82rem;transition:.15s}
  .pb:hover{background:#374151;color:#eee}
  .pb.a{background:#ffaa33;color:#000;border-color:#ffaa33;font-weight:700}
  .bsp{background:linear-gradient(135deg,#ffaa33,#ff6b00);border:none;color:#000;font-weight:800;font-size:1.05rem;padding:12px 36px;border-radius:40px;cursor:pointer;transition:.15s;width:100%}
  .bsp:hover{transform:scale(1.03)}
  .bsp:disabled{background:#374151;color:#6b7280;transform:none;cursor:not-allowed}
  #msgBox{border-radius:12px;padding:11px 15px;font-size:.92rem;display:none;margin-bottom:8px}
  .mw{background:#14532d;border:1px solid #22c55e;color:#86efac}
  .ml{background:#450a0a;border:1px solid #ef4444;color:#fca5a5}
  .mi{background:#1e3a5f;border:1px solid #3b82f6;color:#93c5fd}
  /* skins */
  .sc{background:#111827;border:1px solid #1f2937;border-radius:10px;cursor:pointer;padding:7px;text-align:center;transition:.15s;height:100%}
  .sc:hover{border-color:#374151;background:#1f2937}
  .sc.sel{border-color:#ffaa33}
  .sc img{height:68px;width:100%;object-fit:contain}
  .sp{color:#ffaa33;font-weight:700;font-size:.82rem}
  .sn{font-size:.69rem;color:#9ca3af;line-height:1.2;margin-top:2px}
  .sc.na{opacity:.33;cursor:not-allowed;pointer-events:none}
  .ibadge{background:#7c3aed;color:#fff;font-size:.68rem;border-radius:5px;padding:1px 5px;margin-left:3px}
  /* @send Modal */
  .mbg{position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:1000;display:none;align-items:center;justify-content:center}
  .mbg.open{display:flex}
  .mbox{background:#111827;border:1px solid #1f2937;border-radius:20px;padding:26px;width:370px;max-width:96vw}
  .mbox h5{color:#ffaa33;font-weight:800;margin-bottom:4px}
  .ti{background:#1f2937;border:1px solid #374151;color:#eee;border-radius:10px;padding:8px 12px;width:100%;font-size:1rem;outline:none}
  .ti:focus{border-color:#2563eb}
  .cres{background:#1f2937;border-radius:12px;padding:13px;margin:13px 0;font-size:.88rem}
  .cr{display:flex;justify-content:space-between;margin-bottom:5px}
  .cl{color:#6b7280}.cv{color:#eee;font-weight:700}
  .cv.b{color:#60a5fa}.cv.o{color:#ffaa33}
  .bpay{background:linear-gradient(135deg,#2563eb,#1d4ed8);border:none;color:#fff;font-weight:800;border-radius:14px;padding:10px;width:100%;font-size:.93rem;cursor:pointer;margin-bottom:7px}
  .bpay:disabled{background:#374151;color:#6b7280;cursor:not-allowed}
  .bcx{background:none;border:none;color:#6b7280;font-size:1.2rem;cursor:pointer;line-height:1}
  .note{font-size:.75rem;color:#6b7280;text-align:center;margin-top:6px}
  .sts-paid{color:#86efac;font-weight:700}
  .sts-wait{color:#fde68a}
</style></head><body>

<!-- Deposit modal -->
<div class="mbg" id="sendModal">
  <div class="mbox">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
      <h5 class="mb-0"><i class="fas fa-wallet" style="color:#22c55e"></i>&nbsp;Пополнение баланса</h5>
      <button class="bcx" onclick="closeDeposit()">&#10005;</button>
    </div>
    <p style="color:#6b7280;font-size:.79rem;margin-bottom:13px">Курс: 1 TON = 140 &#8381;&nbsp;&nbsp;|&nbsp;&nbsp;Мин. 50 &#8381; (≈ 0.36 TON)</p>

    <!-- Step 1 -->
    <div id="s1">
      <label style="font-size:.83rem;color:#9ca3af;margin-bottom:3px;display:block">Сумма в рублях:</label>
      <input class="ti" type="number" id="rubIn" min="50" step="1" placeholder="Например: 280" oninput="calcSend()">
      <div id="rubInErr" style="color:#f87171;font-size:.78rem;margin-top:4px;display:none">Минимальная сумма пополнения: 50 &#8381;</div>
      <div class="cres" id="cres" style="display:none">
        <div class="cr"><span class="cl">Сумма ₽</span><span class="cv o" id="cRub">—</span></div>
        <div class="cr"><span class="cl">К оплате TON</span><span class="cv b" id="cTon">—</span></div>
        <div class="cr" style="margin-bottom:0"><span class="cl">Способ оплаты</span><span class="cv">CryptoBot (@CryptoBot)</span></div>
      </div>
      <button class="bpay" id="bCreate" style="display:none" onclick="createInvoice()">
        <i class="fas fa-receipt"></i>&nbsp;Создать счёт
      </button>
    </div>

    <!-- Step 2 -->
    <div id="s2" style="display:none">
      <div class="cres">
        <div class="cr"><span class="cl">Счёт создан на</span><span class="cv b" id="s2Ton">—</span></div>
        <div class="cr" style="margin-bottom:0"><span class="cl">Статус</span><span id="s2Status" class="sts-wait">Ожидает оплаты...</span></div>
      </div>
      <a class="bpay d-block text-center text-decoration-none" id="s2Link" href="#" target="_blank">
        <i class="fas fa-telegram"></i>&nbsp;Оплатить в CryptoBot
      </a>
      <div class="note" id="s2Note">Баланс зачислится автоматически после оплаты (≈20 сек)</div>
    </div>
  </div>
</div>

<!-- Withdrawal modal -->
<div class="mbg" id="wdModal">
  <div class="mbox">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
      <h5 class="mb-0"><i class="fas fa-arrow-up" style="color:#a78bfa"></i>&nbsp;Заявка на вывод</h5>
      <button class="bcx" onclick="closeWithdraw()">&#10005;</button>
    </div>
    <p style="color:#6b7280;font-size:.79rem;margin-bottom:13px">Средства будут списаны сразу. Вывод обрабатывается администратором.</p>
    <div id="wdForm">
      <label style="font-size:.83rem;color:#9ca3af;margin-bottom:3px;display:block">TON-кошелёк:</label>
      <input class="ti" type="text" id="wdWallet" placeholder="UQ...">
      <label style="font-size:.83rem;color:#9ca3af;margin:10px 0 3px;display:block">Сумма в рублях:</label>
      <input class="ti" type="number" id="wdRub" min="200" step="1" placeholder="Например: 280" oninput="calcWd()">
      <div id="wdRubErr" style="color:#f87171;font-size:.78rem;margin-top:4px;display:none">Минимальная сумма вывода: 200 &#8381;</div>
      <div class="cres" id="wdRes" style="display:none;margin-top:10px">
        <div class="cr"><span class="cl">Спишется с баланса</span><span class="cv o" id="wdRubDisp">—</span></div>
        <div class="cr" style="margin-bottom:0"><span class="cl">К получению TON</span><span class="cv b" id="wdTonDisp">—</span></div>
      </div>
      <button class="bpay" id="wdBtn" style="margin-top:12px;display:none" onclick="submitWithdraw()">
        <i class="fas fa-paper-plane"></i>&nbsp;Отправить заявку
      </button>
      <div class="note" id="wdNote"></div>
    </div>
  </div>
</div>

<div class="tb">
  <span class="logo"><i class="fas fa-dice"></i> CASE BATTLE</span>
  <span style="flex:1"></span>
  <span style="color:#d1d5db;font-size:.86rem"><i class="fas fa-user" style="color:#6b7280"></i> {{ username }}</span>
  <span class="bv" id="balDisp">{{ balance|int }} &#8381;</span>
  <button class="btn-plus" onclick="openDeposit()" title="Пополнить баланс">+</button>
  <button class="btn-withdraw" onclick="openWithdraw()"><i class="fas fa-arrow-up"></i> Вывод</button>
  <a href="/inventory" class="nl"><i class="fas fa-box-open"></i> Инвентарь</a>
  {% if is_admin %}<a href="/admin" class="nl" style="color:#f87171"><i class="fas fa-crown"></i> Admin</a>{% endif %}
  <a href="/logout" class="nl"><i class="fas fa-sign-out-alt"></i> Выйти</a>
</div>

<div class="container-fluid py-3 px-3">
  <div id="msgBox"></div>
  <div class="row g-3 mb-3">
    <div class="col-md-4">
      <div class="ac" id="ac"><i class="fas fa-crosshairs fa-2x" style="color:#374151"></i><span style="color:#6b7280">Скин не выбран</span></div>
      <button class="bco" id="bco" onclick="doCashout()"><i class="fas fa-box-open"></i> В инвентарь</button>
    </div>
    <div class="col-md-4 d-flex flex-column align-items-center justify-content-center gap-2">
      <canvas id="wc" width="200" height="200"></canvas>
      <div style="background:#1f2937;border-radius:8px;padding:4px 10px;font-size:.79rem;color:#d1d5db">Множитель: <span style="color:#ffaa33;font-weight:700" id="mDisp">x3.33</span></div>
    </div>
    <div class="col-md-4 d-flex flex-column justify-content-center gap-3">
      <div>
        <div class="text-secondary mb-1" style="font-size:.77rem">ШАНС ПОБЕДЫ</div>
        <div id="pList" class="d-flex flex-wrap">
          {% for p in percents %}<button class="pb{% if p==30 %} a{% endif %}" data-p="{{ p }}">{{ p }}%</button>{% endfor %}
        </div>
      </div>
      <button class="bsp" id="bsp" onclick="doSpin()" disabled><i class="fas fa-sync-alt"></i> КРУТИТЬ</button>
    </div>
  </div>
  <h6 class="text-secondary mt-2 mb-2" style="font-size:.83rem"><i class="fas fa-th"></i> Магазин скинов</h6>
  <div class="row g-2">
    {% for s in skins %}
    <div class="col-4 col-sm-3 col-md-2">
      <div class="sc" data-id="{{ s.id }}" data-price="{{ s.base_price }}" onclick="selSkin(this)">
        <img src="{{ s.image }}" loading="lazy" onerror="this.src='https://via.placeholder.com/80x56/1f2937/9ca3af?text=CS2'">
        <div class="sn">{{ s.name[:26] }}</div>
        <div class="sp">{{ s.base_price }} &#8381;</div>
      </div>
    </div>
    {% endfor %}
  </div>
</div>

<script>
const UID={{ user_id }};
let pct=30,skin=null,bal={{ balance }},spin=false,aa=0,fromInv=false;
let depId=null,pollTimer=null;

// ── Wheel ──────────────────────────────────────────────────
const cv=document.getElementById('wc'),cx2=cv.getContext('2d'),PI2=Math.PI*2;
function dw(p,a){
  const W=cv.width,H=cv.height,x=W/2,y=H/2,r=W/2-8;
  cx2.clearRect(0,0,W,H);
  const sa=-Math.PI/2,we=sa+(p/100)*PI2;
  cx2.beginPath();cx2.moveTo(x,y);cx2.arc(x,y,r,we,sa+PI2);cx2.fillStyle='#1a2a3a';cx2.fill();
  cx2.beginPath();cx2.moveTo(x,y);cx2.arc(x,y,r,sa,we);cx2.fillStyle='#b45309';cx2.fill();
  cx2.beginPath();cx2.moveTo(x,y);cx2.arc(x,y,r,sa,we);cx2.fillStyle='rgba(255,170,51,0.22)';cx2.fill();
  cx2.beginPath();cx2.arc(x,y,r,0,PI2);cx2.strokeStyle='#374151';cx2.lineWidth=2;cx2.stroke();
  cx2.beginPath();cx2.arc(x,y,r*.28,0,PI2);cx2.fillStyle='#0a0f1e';cx2.fill();
  cx2.strokeStyle='#374151';cx2.lineWidth=2;cx2.stroke();
  function lbl(ang,t,c){cx2.save();cx2.translate(x+Math.cos(ang)*r*.65,y+Math.sin(ang)*r*.65);cx2.rotate(ang+Math.PI/2);cx2.fillStyle=c;cx2.font='bold 11px Segoe UI';cx2.textAlign='center';cx2.fillText(t,0,0);cx2.restore();}
  if(p>=8)lbl((sa+we)/2,p+'%','#fde68a');
  if((100-p)>=8)lbl((we+sa+PI2)/2,(100-p)+'%','#6b7280');
  cx2.save();cx2.translate(x,y);cx2.rotate(a);
  cx2.shadowColor='rgba(0,0,0,.7)';cx2.shadowBlur=8;
  cx2.beginPath();cx2.moveTo(0,-r*.68);cx2.lineTo(-7,7);cx2.lineTo(0,0);cx2.lineTo(7,7);cx2.closePath();cx2.fillStyle='#fff';cx2.fill();
  cx2.beginPath();cx2.arc(0,0,6,0,PI2);cx2.fillStyle='#ffaa33';cx2.fill();
  cx2.shadowBlur=0;cx2.restore();
}
dw(30,0);
document.querySelectorAll('.pb').forEach(b=>{b.onclick=()=>{if(spin)return;pct=parseInt(b.dataset.p);document.querySelectorAll('.pb').forEach(x=>x.classList.remove('a'));b.classList.add('a');document.getElementById('mDisp').textContent='x'+gm(pct);dw(pct,aa);}});
function gm(p){if(p===5)return'10.00';if(p===70)return'1.15';return(100/p).toFixed(2);}
document.getElementById('mDisp').textContent='x'+gm(30);

// ── Balance ────────────────────────────────────────────────
function upBal(v){bal=v;document.getElementById('balDisp').textContent=Math.floor(v).toLocaleString('ru-RU')+' \u20bd';rfAff();}
function rfAff(){document.querySelectorAll('.sc').forEach(c=>{if(parseFloat(c.dataset.price)>bal)c.classList.add('na');else c.classList.remove('na');});}

// ── Select skin ────────────────────────────────────────────
async function selSkin(c){
  if(spin)return;
  if(skin){showMsg('Сначала обналичьте или проиграйте текущий скин','l');return;}
  const id=parseInt(c.dataset.id),pr=parseFloat(c.dataset.price);
  if(pr>bal){showMsg('Недостаточно баланса','l');return;}
  const r=await fetch('/select_skin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({skin_id:id})});
  const d=await r.json();
  if(!d.success){showMsg(d.error||'Ошибка','l');return;}
  skin={id,name:d.skin_name,price:d.price};fromInv=false;
  upBal(d.balance);
  document.querySelectorAll('.sc').forEach(x=>x.classList.remove('sel'));c.classList.add('sel');
  renderSkin(d.skin_name,c.querySelector('img').src,d.price,false);
  document.getElementById('bsp').disabled=false;hideMsg();
}
function renderSkin(nm,img,pr,inv){
  const b=document.getElementById('ac');b.classList.add('hs');
  const bd=inv?'<span class="ibadge">инвентарь</span>':'';
  b.innerHTML='<img src="'+img+'" class="ai" onerror="this.src=\'https://via.placeholder.com/110x80/1f2937/9ca3af?text=CS2\'"><div class="an">'+nm+bd+'</div><div class="ap">'+Math.round(pr).toLocaleString('ru-RU')+' \u20bd</div>';
  document.getElementById('bco').style.display='block';
}
function clearSkin(){
  skin=null;fromInv=false;
  const b=document.getElementById('ac');b.classList.remove('hs');
  b.innerHTML='<i class="fas fa-crosshairs fa-2x" style="color:#374151"></i><span style="color:#6b7280">\u0421\u043a\u0438\u043d \u043d\u0435 \u0432\u044b\u0431\u0440\u0430\u043d</span>';
  document.getElementById('bco').style.display='none';document.getElementById('bsp').disabled=true;
  document.querySelectorAll('.sc').forEach(x=>x.classList.remove('sel'));rfAff();
}

// ── Cashout ────────────────────────────────────────────────
async function doCashout(){
  if(!skin||spin)return;
  const r=await fetch('/cashout',{method:'POST'});const d=await r.json();
  if(d.success){showMsg('\u2705 Скин в инвентаре: '+Math.round(skin.price).toLocaleString('ru-RU')+' \u20bd','w');clearSkin();}
}

// ── Spin ───────────────────────────────────────────────────
async function doSpin(){
  if(!skin||spin)return;spin=true;document.getElementById('bsp').disabled=true;hideMsg();
  const r=await fetch('/spin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({percent:pct})});
  const d=await r.json();
  if(!d.success){showMsg(d.error||'Ошибка','l');spin=false;document.getElementById('bsp').disabled=false;return;}
  const wa=(pct/100)*PI2,rot=(4+Math.floor(Math.random()*3))*PI2;
  let tgt;
  if(d.win){const b=wa*.08;tgt=b+Math.random()*(wa-b*2);}
  else{const rm=PI2-wa;const b=rm*.08;tgt=wa+b+Math.random()*(rm-b*2);}
  const tot=rot+tgt,dur=3000,t0=performance.now(),st=aa;
  function fr(now){
    const p=Math.min((now-t0)/dur,1),e=1-Math.pow(1-p,3);
    aa=st+tot*e;dw(pct,aa);
    if(p<1){requestAnimationFrame(fr);return;}
    aa=st+tot;dw(pct,aa);
    if(d.win){skin.price=d.new_price;const el=document.querySelector('#ac .ap');if(el)el.textContent=Math.round(d.new_price).toLocaleString('ru-RU')+' \u20bd';showMsg('\u2705 '+d.msg,'w');}
    else{showMsg('\u274c '+d.msg,'l');clearSkin();}
    spin=false;if(skin)document.getElementById('bsp').disabled=false;
  }
  requestAnimationFrame(fr);
}

// ── Messages ───────────────────────────────────────────────
function showMsg(t,type){const e=document.getElementById('msgBox');e.className=type==='w'?'mw':type==='i'?'mi':'ml';e.style.display='block';e.innerHTML=t;}
function hideMsg(){document.getElementById('msgBox').style.display='none';}

// ── Deposit Modal ──────────────────────────────────────────
function openDeposit(){document.getElementById('sendModal').classList.add('open');document.getElementById('rubIn').value='';calcSend();}
function closeDeposit(){
  document.getElementById('sendModal').classList.remove('open');
  if(pollTimer){clearInterval(pollTimer);pollTimer=null;}
  depId=null;
  const ri=document.getElementById('rubIn');ri.value='';ri.style.borderColor='';
  document.getElementById('rubInErr').style.display='none';
  document.getElementById('s1').style.display='';document.getElementById('s2').style.display='none';
  document.getElementById('cres').style.display='none';document.getElementById('bCreate').style.display='none';
}
// ── Withdrawal Modal ───────────────────────────────────────
function openWithdraw(){document.getElementById('wdModal').classList.add('open');document.getElementById('wdWallet').value='';const wr=document.getElementById('wdRub');wr.value='';wr.style.borderColor='';document.getElementById('wdRubErr').style.display='none';document.getElementById('wdRes').style.display='none';document.getElementById('wdBtn').style.display='none';document.getElementById('wdNote').innerHTML='';}
function closeWithdraw(){document.getElementById('wdModal').classList.remove('open');}
function calcWd(){
  const inp=document.getElementById('wdRub');
  const rub=parseFloat(inp.value);
  const hasVal=inp.value!=='';
  const ok=rub>=200;
  const belowMin=hasVal&&!ok;
  inp.style.borderColor=belowMin?'#ef4444':'';
  document.getElementById('wdRubErr').style.display=belowMin?'block':'none';
  document.getElementById('wdRes').style.display=ok?'block':'none';
  document.getElementById('wdBtn').style.display=ok?'block':'none';
  if(ok){
    document.getElementById('wdRubDisp').textContent=Math.floor(rub).toLocaleString('ru-RU')+' \u20bd';
    document.getElementById('wdTonDisp').textContent=(rub/140).toFixed(4)+' TON';
  }
}
async function submitWithdraw(){
  const wallet=document.getElementById('wdWallet').value.trim();
  const rub=parseFloat(document.getElementById('wdRub').value);
  if(!wallet){document.getElementById('wdNote').innerHTML='<span style="color:#f87171">\u274c Укажите адрес кошелька</span>';return;}
  if(!rub||rub<200){document.getElementById('wdNote').innerHTML='<span style="color:#f87171">\u274c Минимум 200 ₽</span>';return;}
  const btn=document.getElementById('wdBtn');btn.disabled=true;btn.textContent='Отправляем...';
  const r=await fetch('/withdraw_request',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({wallet_address:wallet,rub_amount:rub})});
  const d=await r.json();
  if(d.success){
    document.getElementById('wdNote').innerHTML='<span style="color:#86efac">\u2705 Заявка отправлена! Ожидайте подтверждения администратора.</span>';
    upBal(d.new_balance);
    btn.style.display='none';
    setTimeout(closeWithdraw,3000);
  } else {
    document.getElementById('wdNote').innerHTML='<span style="color:#f87171">\u274c '+(d.error||'Ошибка')+'</span>';
    btn.disabled=false;btn.innerHTML='<i class="fas fa-paper-plane"></i>&nbsp;Отправить заявку';
  }
}
function calcSend(){
  const inp=document.getElementById('rubIn');
  const rub=parseFloat(inp.value);
  const hasVal=inp.value!=='';
  const ok=rub>=50;
  const belowMin=hasVal&&!ok;
  inp.style.borderColor=belowMin?'#ef4444':'';
  document.getElementById('rubInErr').style.display=belowMin?'block':'none';
  document.getElementById('cres').style.display=ok?'block':'none';
  document.getElementById('bCreate').style.display=ok?'block':'none';
  if(ok){
    document.getElementById('cRub').textContent=Math.floor(rub).toLocaleString('ru-RU')+' \u20bd';
    document.getElementById('cTon').textContent=(rub/140).toFixed(4)+' TON';
  }
}
async function createInvoice(){
  const rub=parseFloat(document.getElementById('rubIn').value);
  if(!rub||rub<50){return;}
  const btn=document.getElementById('bCreate');btn.disabled=true;btn.textContent='Создаём счёт...';
  const r=await fetch('/create_invoice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rub_amount:rub})});
  const d=await r.json();
  if(!d.success){btn.disabled=false;btn.innerHTML='<i class="fas fa-receipt"></i>&nbsp;Создать счёт';document.getElementById('sendModal').querySelector('.note')&&(document.getElementById('sendModal').querySelector('.note').innerHTML='<span style="color:#f87171">\u274c '+(d.error||'Ошибка')+'</span>');return;}
  depId=d.dep_id;
  document.getElementById('s2Ton').textContent=d.ton_amount.toFixed(4)+' TON';
  document.getElementById('s2Link').href=d.pay_url;
  document.getElementById('s1').style.display='none';
  document.getElementById('s2').style.display='block';
  // Auto-poll
  pollTimer=setInterval(async()=>{
    const pr=await fetch('/check_deposit/'+depId);const pd=await pr.json();
    if(pd.status==='approved'){
      clearInterval(pollTimer);pollTimer=null;
      document.getElementById('s2Status').className='sts-paid';
      document.getElementById('s2Status').textContent='\u2705 Оплачено! +'+Math.floor(pd.rub_amount).toLocaleString('ru-RU')+' \u20bd';
      document.getElementById('s2Note').innerHTML='<span style="color:#86efac">Баланс пополнен!</span>';
      document.getElementById('s2Link').style.display='none';
      // Update balance
      const sr=await fetch('/get_state');const sd=await sr.json();upBal(sd.balance);
      setTimeout(closeSend,3000);
    }
  },5000);
}

// ── Init ───────────────────────────────────────────────────
(async()=>{
  const r=await fetch('/get_state');const d=await r.json();
  upBal(d.balance);
  if(d.active){
    skin=d.active;fromInv=d.active.from_inventory||false;
    const ie=document.querySelector('[data-id="'+d.active.id+'"] img');
    renderSkin(d.active.name,ie?ie.src:'',d.active.price,fromInv);
    document.getElementById('bsp').disabled=false;
  }
})();
</script></body></html>"""

# ══════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════
@app.route('/login', methods=['GET','POST'])
def login():
    error, tab = None, 'login'
    if request.method == 'POST':
        action = request.form.get('action')
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        if action == 'login':
            with get_db() as c:
                u = c.execute('SELECT * FROM users WHERE username=? AND password=?',
                              (username, hash_pw(password))).fetchone()
            if u:
                session.clear(); session['user_id'] = u['id']; return redirect('/')
            error = 'Неверный логин или пароль'
        elif action == 'register':
            pw2 = request.form.get('password2',''); tab = 'reg'
            if len(username)<3: error='Имя минимум 3 символа'
            elif len(password)<4: error='Пароль минимум 4 символа'
            elif password!=pw2: error='Пароли не совпадают'
            else:
                try:
                    with get_db() as c:
                        c.execute('INSERT INTO users (username,password,balance) VALUES (?,?,?)',
                                  (username, hash_pw(password), 100.0)); c.commit()
                        uid = c.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()['id']
                    session.clear(); session['user_id']=uid; return redirect('/')
                except sqlite3.IntegrityError: error='Имя пользователя уже занято'
    return render_template_string(AUTH_HTML, error=error, tab=tab)

@app.route('/logout')
def logout(): session.clear(); return redirect('/login')

@app.route('/')
@login_required
def index():
    u = current_user()
    return render_template_string(GAME_HTML, skins=skin_list, percents=available_percents,
                                  username=u['username'], balance=u['balance'],
                                  user_id=u['id'], is_admin=bool(u['is_admin']))

@app.route('/get_state')
@login_required
def get_state():
    uid = session['user_id']
    a = session.get('active_skin')
    return jsonify({'balance': _get_balance(uid),
                    'active': {'id':a['id'],'name':skin_list[a['id']]['name'],
                               'price':a['current_price'],'from_inventory':a.get('from_inventory',False)} if a else None})

@app.route('/select_skin', methods=['POST'])
@login_required
def select_skin():
    if session.get('active_skin'): return jsonify({'success':False,'error':'Уже есть активный скин'})
    sid = request.get_json().get('skin_id')
    if sid is None or not (0<=int(sid)<len(skin_list)): return jsonify({'success':False,'error':'Неверный скин'})
    sid=int(sid); price=skin_list[sid]['base_price']
    uid=session['user_id']; bal=_get_balance(uid)
    if bal<price: return jsonify({'success':False,'error':'Недостаточно баланса'})
    _set_balance(uid, bal-price)
    session['active_skin']={'id':sid,'current_price':price,'from_inventory':False}
    session.modified=True
    return jsonify({'success':True,'skin_name':skin_list[sid]['name'],'price':price,'balance':_get_balance(uid)})

@app.route('/cashout', methods=['POST'])
@login_required
def cashout():
    a=session.get('active_skin')
    if not a: return jsonify({'success':False,'error':'Нет активного скина'})
    uid=session['user_id']; sid=a['id']; price=a['current_price']
    with get_db() as c:
        c.execute('INSERT INTO inventory (user_id,skin_name,skin_img,price) VALUES (?,?,?,?)',
                  (uid, skin_list[sid]['name'], skin_list[sid]['image'], price)); c.commit()
    session['active_skin']=None; session.modified=True
    return jsonify({'success':True,'price':price})

@app.route('/spin', methods=['POST'])
@login_required
def spin_route():
    a=session.get('active_skin')
    if not a: return jsonify({'success':False,'error':'Выберите скин'})
    p=request.get_json().get('percent')
    if p not in available_percents: return jsonify({'success':False,'error':'Неверный процент'})
    roll=random.randint(1,100); win=roll<=p
    sid=a['id']; cur=a['current_price']
    if win:
        mult=get_mult(p); new_price=round(cur*mult,2)
        session['active_skin']['current_price']=new_price; session.modified=True
        return jsonify({'success':True,'win':True,'new_price':new_price,
                        'msg':f'Победа! Выпало {roll} (\u2264{p}) | \xd7{mult} | {new_price:,.0f}\u20bd'})
    else:
        session['active_skin']=None; session.modified=True
        return jsonify({'success':True,'win':False,
                        'msg':f'Потеря! Выпало {roll} (>{p}) | {skin_list[sid]["name"]} утерян'})

# ── Inventory ──────────────────────────────────────────────────────────────
@app.route('/inventory')
@login_required
def inventory():
    uid=session['user_id']; u=current_user()
    with get_db() as c:
        items=c.execute('SELECT * FROM inventory WHERE user_id=? ORDER BY acquired_at DESC',(uid,)).fetchall()
    return render_template_string(INV_HTML, items=items, username=u['username'],
                                  balance=u['balance'], is_admin=bool(u['is_admin']))

@app.route('/sell_inventory', methods=['POST'])
@login_required
def sell_inventory():
    uid=session['user_id']; item_id=request.get_json().get('item_id')
    with get_db() as c:
        item=c.execute('SELECT * FROM inventory WHERE id=? AND user_id=?',(item_id,uid)).fetchone()
        if not item: return jsonify({'success':False,'error':'Не найдено'})
        sp=round(item['price']*0.85,2)
        c.execute('DELETE FROM inventory WHERE id=?',(item_id,)); c.commit()
    _set_balance(uid, _get_balance(uid)+sp)
    return jsonify({'success':True,'message':f'Продано за {sp:,.0f}\u20bd (-15%)','balance':_get_balance(uid)})

@app.route('/inventory/activate', methods=['POST'])
@login_required
def inventory_activate():
    if session.get('active_skin'):
        return jsonify({'success':False,'error':'Уже есть активный скин'})
    uid=session['user_id']; item_id=request.get_json().get('item_id')
    with get_db() as c:
        item=c.execute('SELECT * FROM inventory WHERE id=? AND user_id=?',(item_id,uid)).fetchone()
        if not item: return jsonify({'success':False,'error':'Предмет не найден'})
        sid=next((s['id'] for s in skin_list if s['name']==item['skin_name']),0)
        price=item['price']
        c.execute('DELETE FROM inventory WHERE id=?',(item_id,)); c.commit()
    session['active_skin']={'id':sid,'current_price':price,'from_inventory':True}
    session.modified=True
    return jsonify({'success':True,'skin_name':item['skin_name'],'price':price})

# ── CryptoBot / @send ──────────────────────────────────────────────────────
@app.route('/create_invoice', methods=['POST'])
@login_required
def create_invoice():
    uid=session['user_id']
    rub=float(request.get_json().get('rub_amount',0))
    if rub < TON_MIN_RUB:
        return jsonify({'success':False,'error':f'Минимум {TON_MIN_RUB:.0f} ₽'})
    ton=round(rub/TON_RATE, 4)
    try:
        res=cb_request('createInvoice',{
            'asset':'TON','amount':str(ton),
            'description':f'Case Battle +{int(rub)}\u20bd',
            'payload':f'uid:{uid}:rub:{int(rub)}',
            'expires_in':3600
        })
        if not res.get('ok'):
            return jsonify({'success':False,'error':res.get('error',{}).get('name','API Error')})
        inv=res['result']
        inv_id=str(inv['invoice_id']); pay_url=inv['pay_url']
        with get_db() as c:
            c.execute('INSERT INTO deposits (user_id,rub_amount,ton_amount,invoice_id,pay_url) VALUES (?,?,?,?,?)',
                      (uid,round(rub,2),ton,inv_id,pay_url)); c.commit()
            dep_id=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        return jsonify({'success':True,'pay_url':pay_url,'dep_id':dep_id,'ton_amount':ton,'invoice_id':inv_id})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)})

@app.route('/check_deposit/<int:dep_id>')
@login_required
def check_deposit(dep_id):
    uid=session['user_id']
    with get_db() as c:
        dep=c.execute('SELECT * FROM deposits WHERE id=? AND user_id=?',(dep_id,uid)).fetchone()
    if not dep: return jsonify({'success':False,'error':'Не найдено'})
    return jsonify({'success':True,'status':dep['status'],'rub_amount':dep['rub_amount']})

# ── Admin ──────────────────────────────────────────────────────────────────
@app.route('/admin')
@admin_required
def admin():
    with get_db() as c:
        users=c.execute('SELECT * FROM users ORDER BY is_admin DESC,id').fetchall()
        deps=c.execute('''SELECT d.*,u.username FROM deposits d JOIN users u ON u.id=d.user_id
                          ORDER BY d.created_at DESC LIMIT 50''').fetchall()
        wds=c.execute('''SELECT w.*,u.username FROM withdrawals w JOIN users u ON u.id=w.user_id
                         ORDER BY w.created_at DESC LIMIT 50''').fetchall()
        st={
            'users': c.execute('SELECT COUNT(*) FROM users WHERE is_admin=0').fetchone()[0],
            'pending': c.execute("SELECT COUNT(*) FROM deposits WHERE status='pending'").fetchone()[0],
            'pending_wd': c.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'").fetchone()[0],
            'bal': c.execute('SELECT COALESCE(SUM(balance),0) FROM users WHERE is_admin=0').fetchone()[0],
        }
    return render_template_string(ADMIN_HTML, users=users, deps=deps, wds=wds, st=st,
                                  msg=request.args.get('msg'))

@app.route('/admin/credit', methods=['POST'])
@admin_required
def admin_credit():
    uid=int(request.form.get('user_id',0)); amt=float(request.form.get('amount',0) or 0)
    if amt>0 and uid: _set_balance(uid, _get_balance(uid)+amt)
    return redirect('/admin?msg=Баланс+начислен')

@app.route('/admin/deposits/approve', methods=['POST'])
@admin_required
def dep_approve():
    dep_id=int(request.form.get('dep_id',0))
    with get_db() as c:
        dep=c.execute("SELECT * FROM deposits WHERE id=? AND status='pending'",(dep_id,)).fetchone()
        if dep:
            _set_balance(dep['user_id'], _get_balance(dep['user_id'])+dep['rub_amount'])
            c.execute("UPDATE deposits SET status='approved' WHERE id=?",(dep_id,)); c.commit()
    return redirect('/admin?msg=Депозит+подтверждён')

@app.route('/admin/deposits/reject', methods=['POST'])
@admin_required
def dep_reject():
    dep_id=int(request.form.get('dep_id',0))
    with get_db() as c:
        c.execute("UPDATE deposits SET status='rejected' WHERE id=?",(dep_id,)); c.commit()
    return redirect('/admin?msg=Депозит+отклонён')

# ── Withdraw ───────────────────────────────────────────────────────────────
@app.route('/withdraw_request', methods=['POST'])
@login_required
def withdraw_request():
    uid = session['user_id']
    data = request.get_json()
    wallet = (data.get('wallet_address') or '').strip()
    rub = float(data.get('rub_amount', 0))
    if not wallet:
        return jsonify({'success': False, 'error': 'Укажите адрес кошелька'})
    if rub < WITHDRAW_MIN_RUB:
        return jsonify({'success': False, 'error': f'Минимум {WITHDRAW_MIN_RUB:.0f} ₽'})
    bal = _get_balance(uid)
    if bal < rub:
        return jsonify({'success': False, 'error': 'Недостаточно средств'})
    ton = round(rub / TON_RATE, 4)
    _set_balance(uid, bal - rub)
    with get_db() as c:
        c.execute('INSERT INTO withdrawals (user_id,wallet_address,rub_amount,ton_amount) VALUES (?,?,?,?)',
                  (uid, wallet, round(rub, 2), ton))
        c.commit()
    return jsonify({'success': True, 'new_balance': _get_balance(uid)})

@app.route('/admin/withdrawals/approve', methods=['POST'])
@admin_required
def wd_approve():
    wd_id = int(request.form.get('wd_id', 0))
    with get_db() as c:
        c.execute("UPDATE withdrawals SET status='approved' WHERE id=?", (wd_id,))
        c.commit()
    return redirect('/admin?msg=Вывод+подтверждён')

@app.route('/admin/withdrawals/reject', methods=['POST'])
@admin_required
def wd_reject():
    wd_id = int(request.form.get('wd_id', 0))
    with get_db() as c:
        wd = c.execute("SELECT * FROM withdrawals WHERE id=? AND status='pending'", (wd_id,)).fetchone()
        if wd:
            _set_balance(wd['user_id'], _get_balance(wd['user_id']) + wd['rub_amount'])
            c.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wd_id,))
            c.commit()
    return redirect('/admin?msg=Вывод+отклонён+средства+возвращены')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

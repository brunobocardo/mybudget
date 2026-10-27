"""MyBudget web: serves the page and a small JSON API backed by SQLite, with optional Google sign-in.

Configuration comes from environment variables (see .env.example):
  DATA_DIR                folder for mybudget.db (default: ../data next to this file)
  HOST / PORT             where to listen (default 127.0.0.1:8765; Docker uses 0.0.0.0)
  BASE_URL                public address, used for the Google redirect (default http://localhost:PORT)
  GOOGLE_CLIENT_ID/SECRET turn on "Sign in with Google"; without them the app runs unlocked
  ALLOWED_EMAILS          comma-separated Google accounts allowed in (required when Google is on)
  ANTHROPIC_API_KEY       turns on AI advice
"""
import base64, json, os, secrets, sqlite3, time, uuid, urllib.parse, urllib.request, urllib.error
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", HERE.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / "mybudget.db"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", 8765))
BASE_URL = os.environ.get("BASE_URL", f"http://localhost:{PORT}").rstrip("/")
GOOGLE_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
ALLOWED = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}
AUTH_ON = bool(GOOGLE_ID and GOOGLE_SECRET)
SESSION_DAYS = 30
ICONS = {"/favicon.ico": "favicon.png", "/favicon.png": "favicon.png", "/apple-touch-icon.png": "apple-touch-icon.png", "/icon.png": "icon.png"}
MODEL = "claude-sonnet-5-5"

PROMPT = """You are a practical household budget coach. Below is a family's budget data as JSON:
monthly totals per category (with budgets), spending per account/card and per person, spending by day of
week and by part of the month, recurring charges, and savings goals with progress. Find concrete ways to
save more money. Answer in {lang}. Use short sections with bullet points:
1. Where to cut (category, how much per month, why)
2. When money leaks (days/weeks/times of the month with high spending)
3. Card/account habits worth changing
4. Goals: which are on track or behind, and how much to redirect each month
Use real numbers from the data. Be specific and brief; no generic advice.

DATA:
{data}"""

TABLES = {
    "accounts": ["name", "kind", "balance", "owner"],
    "categories": ["name", "budget", "kind", "icon"],
    "entries": ["date", "type", "amount", "category_id", "account_id", "note", "entered_by"],
    "goals": ["name", "target", "due"],
    "contributions": ["goal_id", "date", "amount", "from_account_id", "note"],
}

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init():
    with conn() as c:
        for t, cols in TABLES.items():
            c.execute(f"CREATE TABLE IF NOT EXISTS {t} (id TEXT PRIMARY KEY, {', '.join(cols)})")
            have = {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
            for col in cols:
                if col not in have:  # upgrade older databases in place
                    c.execute(f"ALTER TABLE {t} ADD COLUMN {col}")
        c.execute("CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name, picture, last_login)")
        c.execute("CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, email, expires REAL)")
        c.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        # Fill in type and emoji for categories created before these columns existed.
        for n, k, i in [("Groceries", "flexible", "🛒"), ("Rent / Mortgage", "fixed", "🏠"), ("House insurance", "fixed", "🛡️"),
                        ("Car loan", "fixed", "🚗"), ("Car insurance", "fixed", "🚙"), ("Kids school", "fixed", "🎒"),
                        ("Utilities", "fixed", "💡"), ("Salary", "income", "💼")]:
            c.execute("UPDATE categories SET kind=COALESCE(kind,?), icon=COALESCE(icon,?) WHERE name=?", (k, i, n))
        c.execute("UPDATE categories SET kind='flexible' WHERE kind IS NULL")
        c.execute("UPDATE accounts SET balance=0 WHERE balance IS NULL")
        if not c.execute("SELECT 1 FROM accounts").fetchone():
            for n, k in [("Checking", "bank"), ("Savings", "savings"), ("Credit Card A", "card"), ("Credit Card B", "card")]:
                c.execute("INSERT INTO accounts (id, name, kind, balance) VALUES (?,?,?,0)", (uuid.uuid4().hex, n, k))
            for n, b, k, i in [("Groceries", 800, "flexible", "🛒"), ("Rent / Mortgage", 2000, "fixed", "🏠"),
                               ("House insurance", 100, "fixed", "🛡️"), ("Car loan", 400, "fixed", "🚗"),
                               ("Car insurance", 150, "fixed", "🚙"), ("Kids school", 600, "fixed", "🎒"),
                               ("Utilities", 250, "fixed", "💡"), ("Dining out", 300, "flexible", "🍽️"),
                               ("Salary", 0, "income", "💼"), ("Other income", 0, "income", "💵")]:
                c.execute("INSERT INTO categories (id, name, budget, kind, icon) VALUES (?,?,?,?,?)", (uuid.uuid4().hex, n, b, k, i))

def upsert(c, t, row):
    row["id"] = row.get("id") or uuid.uuid4().hex
    cols = ["id"] + TABLES[t]
    c.execute(f"INSERT OR REPLACE INTO {t} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row.get(k) for k in cols])
    return row

# ---------- AI ----------
def ask_claude(summary, lang):
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return 400, {"error": "AI advice is off. Add ANTHROPIC_API_KEY to the .env file in the MyBudget folder and restart."}
    body = {"model": MODEL, "max_tokens": 2000,
            "messages": [{"role": "user", "content": PROMPT.format(lang=lang, data=json.dumps(summary))}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(), method="POST",
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            res = json.load(r)
        return 200, {"text": "".join(b.get("text", "") for b in res.get("content", []))}
    except urllib.error.HTTPError as e:
        return 502, {"error": f"AI request failed ({e.code}): {e.read().decode()[:300]}"}
    except Exception as e:
        return 502, {"error": f"AI request failed: {e}"}

# ---------- Google sign-in (OAuth 2.0 authorization code flow) ----------
def google_exchange(code):
    """Swap the code for tokens. The ID token comes straight from Google over TLS, so its claims can be read directly."""
    data = urllib.parse.urlencode({"code": code, "client_id": GOOGLE_ID, "client_secret": GOOGLE_SECRET,
                                   "redirect_uri": BASE_URL + "/auth/callback", "grant_type": "authorization_code"}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=data), timeout=20) as r:
        tok = json.load(r)
    payload = tok["id_token"].split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    if claims.get("aud") != GOOGLE_ID or claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise ValueError("token was not issued for this app")
    if not claims.get("email_verified"):
        raise ValueError("Google email is not verified")
    return claims

LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MyBudget – Sign in</title><link rel="icon" type="image/png" href="/favicon.png"><link rel="apple-touch-icon" href="/apple-touch-icon.png"><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;font:15px Inter,-apple-system,system-ui,sans-serif;
background:radial-gradient(circle at 50% 35%,#2ec48455,transparent 60%),linear-gradient(160deg,#17a37a,#0b2a27);color:#fff}
.c{text-align:center;padding:40px 32px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);border-radius:24px;backdrop-filter:blur(8px);max-width:360px;width:calc(100% - 32px)}
.logo{width:84px;height:84px;margin:0 auto 14px;border-radius:22px;background:linear-gradient(135deg,#2ec484,#0b5452);display:grid;place-items:center;font-size:40px;box-shadow:0 10px 30px rgba(0,0,0,.3)}
h1{margin:0;font-size:28px}p{opacity:.8;font-style:italic;margin:6px 0 28px}
a.g{display:flex;gap:10px;align-items:center;justify-content:center;background:#fff;color:#1f1f1f;text-decoration:none;font-weight:600;padding:12px 18px;border-radius:12px}
.err{background:rgba(217,72,63,.25);border-radius:10px;padding:10px;margin-bottom:16px;font-size:13.5px}
</style></head><body><div class="c"><img class="logo" src="/icon.png" alt=""><h1>MyBudget</h1><p>You've got to have a plan...</p>{err}
<a class="g" href="/auth/login"><svg width="18" height="18" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.9 6.1C12.5 13.6 17.8 9.5 24 9.5z"/><path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.4 5.7c4.3-4 6.9-9.9 6.9-17.1z"/><path fill="#FBBC05" d="M10.6 28.6c-.5-1.4-.8-3-.8-4.6s.3-3.2.8-4.6l-7.9-6.1C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.9-6.1z"/><path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.4-5.7c-2.1 1.4-4.8 2.3-8.5 2.3-6.2 0-11.5-4.1-13.4-9.9l-7.9 6.1C6.6 42.6 14.6 48 24 48z"/></svg>Sign in with Google</a>
</div></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json", headers=()):
        data = body if isinstance(body, bytes) else (body.encode() if isinstance(body, str) else json.dumps(body).encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, to, cookies=()):
        self.send(302, b"", "text/plain", [("Location", to), *[("Set-Cookie", c) for c in cookies]])

    def cookie(self, name):
        m = SimpleCookie(self.headers.get("Cookie", "")).get(name)
        return m.value if m else None

    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def user(self):
        """The signed-in user, or a local stand-in when Google sign-in is off."""
        if not AUTH_ON:
            return {"email": None, "name": None, "auth": False}
        tok = self.cookie("mb_session")
        if not tok:
            return None
        with conn() as c:
            r = c.execute("SELECT u.email, u.name, u.picture FROM sessions s JOIN users u ON u.email=s.email "
                          "WHERE s.token=? AND s.expires>?", (tok, time.time())).fetchone()
        return {**dict(r), "auth": True} if r else None

    def path_only(self):
        return urllib.parse.urlparse(self.path).path

    # ----- routes -----
    def do_GET(self):
        p = self.path_only()
        if p == "/auth/login":
            if not AUTH_ON:
                return self.redirect("/")
            state = secrets.token_urlsafe(24)
            q = urllib.parse.urlencode({"client_id": GOOGLE_ID, "redirect_uri": BASE_URL + "/auth/callback", "response_type": "code",
                                        "scope": "openid email profile", "state": state, "prompt": "select_account"})
            return self.redirect("https://accounts.google.com/o/oauth2/v2/auth?" + q,
                                 [f"mb_state={state}; Path=/; HttpOnly; SameSite=Lax; Max-Age=600"])
        if p == "/auth/callback":
            return self.callback()
        if p == "/auth/logout":
            with conn() as c:
                c.execute("DELETE FROM sessions WHERE token=?", (self.cookie("mb_session") or "",))
            return self.redirect("/", ["mb_session=; Path=/; Max-Age=0"])
        if p == "/health":
            return self.send(200, {"ok": True})
        if p in ICONS:  # served without sign-in so the browser tab and login page show the logo
            return self.send(200, (HERE / ICONS[p]).read_bytes(), "image/png", [("Cache-Control", "max-age=86400")])

        u = self.user()
        if p in ("/", "/index.html"):
            if u is None:
                return self.send(200, LOGIN_PAGE.replace("{err}", ""), "text/html; charset=utf-8")
            return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if u is None:
            return self.send(401, {"error": "Please sign in"})
        if p == "/api/me":
            return self.send(200, u)
        if p == "/api/data":
            with conn() as c:
                data = {t: [dict(r) for r in c.execute(f"SELECT * FROM {t}")] for t in TABLES}
                data["users"] = [dict(r) for r in c.execute("SELECT email, name, picture FROM users")]
            return self.send(200, data)
        self.send(404, {"error": "not found"})

    def callback(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        fail = lambda msg: self.send(403, LOGIN_PAGE.replace("{err}", f'<div class="err">{msg}</div>'), "text/html; charset=utf-8")
        if not AUTH_ON or not q.get("code") or q.get("state") != self.cookie("mb_state"):
            return fail("Sign-in expired. Please try again.")
        try:
            claims = google_exchange(q["code"])
        except Exception as e:
            return fail(f"Google sign-in failed: {e}")
        email = claims["email"].lower()
        if email not in ALLOWED:
            return fail(f"{email} is not allowed. Add it to ALLOWED_EMAILS in .env.")
        token = secrets.token_urlsafe(32)
        with conn() as c:
            c.execute("INSERT OR REPLACE INTO users VALUES (?,?,?,?)",
                      (email, claims.get("given_name") or claims.get("name") or email, claims.get("picture"), time.time()))
            c.execute("INSERT INTO sessions VALUES (?,?,?)", (token, email, time.time() + SESSION_DAYS * 86400))
        secure = "; Secure" if BASE_URL.startswith("https") else ""
        self.redirect("/", [f"mb_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_DAYS*86400}{secure}",
                            "mb_state=; Path=/; Max-Age=0"])

    def parts(self):
        p = self.path_only().strip("/").split("/")
        return (p[1], p[2] if len(p) > 2 else None) if len(p) >= 2 and p[0] == "api" and p[1] in TABLES else (None, None)

    def do_POST(self):
        if self.user() is None:
            return self.send(401, {"error": "Please sign in"})
        p = self.path_only()
        if p == "/api/insights":
            req = self.body()
            return self.send(*ask_claude(req.get("summary", {}), req.get("lang", "English")))
        if p == "/api/bulk/entries":  # statement import: many rows in one transaction
            rows = self.body().get("rows", [])
            with conn() as c:
                for r in rows:
                    upsert(c, "entries", r)
            return self.send(200, {"imported": len(rows)})
        t, _ = self.parts()
        if not t:
            return self.send(404, {"error": "not found"})
        with conn() as c:
            row = upsert(c, t, self.body())
        self.send(200, row)

    def do_DELETE(self):  # /api/<table>/<id>
        if self.user() is None:
            return self.send(401, {"error": "Please sign in"})
        t, rid = self.parts()
        if not t or not rid:
            return self.send(404, {"error": "not found"})
        with conn() as c:
            c.execute(f"DELETE FROM {t} WHERE id=?", (rid,))
            if t == "goals":
                c.execute("DELETE FROM contributions WHERE goal_id=?", (rid,))
        self.send(200, {"ok": True})

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    init()
    if AUTH_ON and not ALLOWED:
        raise SystemExit("Google sign-in is on but ALLOWED_EMAILS is empty. Add your household's Google accounts to .env.")
    print(f"MyBudget running at {BASE_URL}  (data: {DB}, Google sign-in: {'on' if AUTH_ON else 'off'})", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

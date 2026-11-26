import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta
from urllib.parse import urlparse

from config import (DB_PATH, DEFAULT_PREFIXES, TZ, ADMIN_CHANNEL, USER_CHANNEL,
                    platform_emoji)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    first_name TEXT,
    last_name TEXT,
    username TEXT,
    balance REAL NOT NULL DEFAULT 0,
    is_banned INTEGER NOT NULL DEFAULT 0,
    banned_until TEXT,
    joined TEXT,
    lang TEXT
);
CREATE TABLE IF NOT EXISTS categories (
    name TEXT PRIMARY KEY,
    refill_days INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created TEXT
);
CREATE TABLE IF NOT EXISTS services (
    key TEXT PRIMARY KEY,
    platform TEXT,
    name TEXT NOT NULL,
    emoji TEXT NOT NULL DEFAULT '🛍',
    panel_id INTEGER NOT NULL,
    price REAL NOT NULL,
    min_qty INTEGER NOT NULL DEFAULT 100,
    max_qty INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created TEXT,
    photo_id TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    service_key TEXT,
    service_name TEXT,
    link TEXT,
    quantity INTEGER,
    cost REAL,
    panel_order TEXT,
    status TEXT,
    created TEXT,
    display_no TEXT,
    refill_id TEXT
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    amount REAL NOT NULL,
    note TEXT,
    created TEXT
);
CREATE TABLE IF NOT EXISTS redeem_codes (
    code TEXT PRIMARY KEY,
    amount REAL NOT NULL,
    max_uses INTEGER NOT NULL DEFAULT 0,
    used_count INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created TEXT
);
CREATE TABLE IF NOT EXISTS redemptions (
    code TEXT,
    user_id INTEGER,
    created TEXT,
    PRIMARY KEY (code, user_id)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def db():
    conn = sqlite3.connect(DB_PATH, isolation_level=None, timeout=15)  # autocommit, ট্রানজ্যাকশন ম্যানুয়াল
    conn.row_factory = sqlite3.Row
    return conn


def now_dt():
    return datetime.now(TZ)


def now_str():
    return now_dt().strftime("%d %b %Y, %I:%M %p") + " (BD)"


def parse_dt(s):
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def fmt_time(iso):
    dt = parse_dt(iso)
    return dt.strftime("%d %b %Y, %I:%M %p") if dt else "—"


def _ensure_col(c, table, col, ddl):
    cols = [r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def init_db():
    with closing(db()) as c:
        c.executescript(SCHEMA)
        # পুরোনো ডেটাবেসে নতুন কলাম যোগ
        _ensure_col(c, "users", "lang", "TEXT")
        _ensure_col(c, "users", "voice", "INTEGER")
        _ensure_col(c, "services", "photo_id", "TEXT")
        _ensure_col(c, "services", "emoji", "TEXT NOT NULL DEFAULT '🛍'")
        _ensure_col(c, "orders", "display_no", "TEXT")
        _ensure_col(c, "orders", "refill_id", "TEXT")
        c.execute("UPDATE orders SET display_no = 'SP' || printf('%06d', id) WHERE display_no IS NULL")


# ---------- Settings ----------
def get_setting(key):
    with closing(db()) as c:
        r = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return r["value"] if r else None


def set_setting(key, value):
    with closing(db()) as c:
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def brand():
    return get_setting("brand_name") or ""


def support_username():
    return (get_setting("support_username") or "").lstrip("@")


def channel_setting(kind):
    """kind: 'public' (চ্যানেল ১ - সকল ইউজার) বা 'detail' (চ্যানেল ২ - অ্যাডমিন).
    সেটিং না থাকলে স্থায়ী ডিফল্ট; 'off' বা খালি হলে বন্ধ।"""
    default = {"public": USER_CHANNEL, "detail": ADMIN_CHANNEL}[kind]
    value = get_setting(f"channel_{kind}")
    if value is None:
        value = default
    return None if value in ("", "off") else value


def join_channel():
    value = get_setting("join_channel")
    if value is None:
        value = USER_CHANNEL
    return None if value in ("", "off") else value


def join_link():
    value = get_setting("join_link")
    if value is None:
        chan = join_channel()
        return f"https://t.me/{chan.lstrip('@')}" if chan and chan.startswith("@") else None
    return value or None


def order_prefixes():
    raw = get_setting("order_prefixes") or DEFAULT_PREFIXES
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or DEFAULT_PREFIXES.split(",")


def order_mode():
    return get_setting("order_no_mode") or "bot"


def panel_site():
    from config import PANEL_API_URL
    p = urlparse(PANEL_API_URL)
    return f"{p.scheme}://{p.netloc}" if p.netloc else "https://"


# ---------- Users ----------
def upsert_user(u):
    with closing(db()) as c:
        c.execute(
            """INSERT INTO users (id, first_name, last_name, username, joined)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   first_name = excluded.first_name,
                   last_name = excluded.last_name,
                   username = excluded.username""",
            (u.id, u.first_name or "", u.last_name or "", u.username, now_dt().isoformat()),
        )


def get_user(uid):
    with closing(db()) as c:
        return c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def user_lang(uid):
    row = get_user(uid)
    return (row["lang"] if row and row["lang"] else None)


def set_lang(uid, lang):
    with closing(db()) as c:
        c.execute("UPDATE users SET lang = ? WHERE id = ?", (lang, uid))


def find_user(ident):
    ident = ident.strip()
    with closing(db()) as c:
        if ident.lstrip("-").isdigit():
            return c.execute("SELECT * FROM users WHERE id = ?", (int(ident),)).fetchone()
        return c.execute("SELECT * FROM users WHERE LOWER(username) = ?",
                         (ident.lstrip("@").lower(),)).fetchone()


def all_users():
    with closing(db()) as c:
        return c.execute("SELECT * FROM users ORDER BY joined DESC").fetchall()


def set_ban(uid, until_iso, flag):
    with closing(db()) as c:
        c.execute("UPDATE users SET is_banned = ?, banned_until = ? WHERE id = ?",
                  (1 if flag else 0, until_iso, uid))


def full_name(row):
    return f"{row['first_name'] or ''} {row['last_name'] or ''}".strip() or "নাম নেই"


def status_key(row):
    if not row["is_banned"]:
        return "status_active"
    if parse_dt(row["banned_until"]):
        return "status_temp"
    return "status_perm"


# ---------- Balance & ledger ----------
def adjust_balance(uid, amount, kind, note=""):
    with closing(db()) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, uid))
            c.execute(
                "INSERT INTO transactions (user_id, kind, amount, note, created) VALUES (?, ?, ?, ?, ?)",
                (uid, kind, amount, note, now_dt().isoformat()),
            )
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise


def charge_order(uid, cost):
    with closing(db()) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            cur = c.execute(
                "UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ? AND is_banned = 0",
                (cost, uid, cost),
            )
            if cur.rowcount != 1:
                c.execute("ROLLBACK")
                return False
            c.execute(
                "INSERT INTO transactions (user_id, kind, amount, note, created) VALUES (?, ?, ?, ?, ?)",
                (uid, "order", -cost, "", now_dt().isoformat()),
            )
            c.execute("COMMIT")
            return True
        except Exception:
            c.execute("ROLLBACK")
            raise


def user_stats(uid):
    with closing(db()) as c:
        r = c.execute(
            """SELECT
                COALESCE(SUM(CASE WHEN kind = 'deposit' THEN amount END), 0) AS deposits,
                COALESCE(SUM(CASE WHEN kind = 'redeem' THEN amount END), 0) AS redeems,
                COALESCE(SUM(CASE WHEN kind = 'order' THEN amount END), 0) AS order_sum,
                COALESCE(SUM(CASE WHEN kind = 'refund' THEN amount END), 0) AS refund_sum
               FROM transactions WHERE user_id = ?""",
            (uid,),
        ).fetchone()
        orders = c.execute("SELECT COUNT(*) AS n FROM orders WHERE user_id = ?", (uid,)).fetchone()["n"]
    return {
        "deposits": r["deposits"],
        "redeems": r["redeems"],
        "spent": -r["order_sum"] - r["refund_sum"],
        "orders": orders,
    }


# ---------- Categories ----------
def list_categories():
    with closing(db()) as c:
        return c.execute("SELECT * FROM categories WHERE active = 1 ORDER BY created").fetchall()


def get_category(name):
    with closing(db()) as c:
        return c.execute("SELECT * FROM categories WHERE name = ?", (name,)).fetchone()


def add_category(name, refill_days):
    with closing(db()) as c:
        try:
            c.execute("INSERT INTO categories (name, refill_days, active, created) VALUES (?, ?, 1, ?)",
                      (name, refill_days, now_dt().isoformat()))
            return True
        except sqlite3.IntegrityError:
            c.execute("UPDATE categories SET active = 1 WHERE name = ?", (name,))
            return False


def set_category_refill(name, days):
    with closing(db()) as c:
        c.execute("UPDATE categories SET refill_days = ? WHERE name = ?", (days, name))


def deactivate_category(name):
    with closing(db()) as c:
        c.execute("UPDATE categories SET active = 0 WHERE name = ?", (name,))


def logo_key(platform):
    return f"logo:{(platform or '').lower().strip()}"


def category_logo(name):
    return get_setting(logo_key(name))


def cat_emoji(name):
    return platform_emoji(name)


# ---------- Services ----------
def list_services(active_only=True, platform=None):
    q = "SELECT * FROM services WHERE 1=1"
    args = []
    if active_only:
        q += " AND active = 1"
    if platform is not None:
        q += " AND platform = ?"
        args.append(platform)
    q += " ORDER BY created"
    with closing(db()) as c:
        return c.execute(q, args).fetchall()


def get_service(key):
    with closing(db()) as c:
        return c.execute("SELECT * FROM services WHERE key = ?", (key,)).fetchone()


def add_service(name, platform, panel_id, price, min_qty, max_qty, photo_id, emoji):
    key = "s" + uuid.uuid4().hex[:8]
    with closing(db()) as c:
        c.execute(
            """INSERT INTO services (key, platform, name, emoji, panel_id, price, min_qty, max_qty,
                                     active, created, photo_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (key, platform, name, emoji or platform_emoji(platform), panel_id, price, min_qty, max_qty,
             now_dt().isoformat(), photo_id),
        )
    return key


def set_service_price(key, price):
    with closing(db()) as c:
        c.execute("UPDATE services SET price = ? WHERE key = ?", (price, key))


def set_service_photo(key, photo_id):
    with closing(db()) as c:
        c.execute("UPDATE services SET photo_id = ? WHERE key = ?", (photo_id, key))


def deactivate_service(key):
    with closing(db()) as c:
        cur = c.execute("UPDATE services SET active = 0 WHERE key = ?", (key,))
        return cur.rowcount == 1


def service_title(srv):
    platform = srv["platform"] or ""
    return f"{platform} · {srv['name']}".strip(" ·") if platform else srv["name"]


# ---------- Orders ----------
def make_display_no(oid, panel_id):
    if order_mode() == "panel" and panel_id:
        return str(panel_id)
    prefixes = order_prefixes()
    return f"{prefixes[oid % len(prefixes)]}{oid:06d}"


def save_order(uid, srv, link, qty, cost, panel_id):
    with closing(db()) as c:
        cur = c.execute(
            """INSERT INTO orders (user_id, service_key, service_name, link, quantity, cost,
                                   panel_order, status, created)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, srv["key"], service_title(srv), link, qty, cost, str(panel_id), "placed",
             now_dt().isoformat()),
        )
        oid = cur.lastrowid
    display = make_display_no(oid, panel_id)
    with closing(db()) as c:
        c.execute("UPDATE orders SET display_no = ? WHERE id = ?", (display, oid))
    return oid, display


def order_label(row):
    return row["display_no"] or str(row["id"])


def get_order(oid):
    with closing(db()) as c:
        return c.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()


def find_order(text):
    t = (text or "").strip().lstrip("#")
    if not t:
        return None
    with closing(db()) as c:
        return c.execute(
            "SELECT * FROM orders WHERE display_no = ? OR panel_order = ? ORDER BY id DESC LIMIT 1",
            (t, t),
        ).fetchone()


def user_orders(uid, limit=10):
    with closing(db()) as c:
        return c.execute("SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                         (uid, limit)).fetchall()


def set_refill(oid, refill_id):
    with closing(db()) as c:
        c.execute("UPDATE orders SET refill_id = ? WHERE id = ?", (refill_id, oid))


def refill_days_for(row):
    srv = get_service(row["service_key"]) if row["service_key"] else None
    if not srv:
        return 0
    cat = get_category(srv["platform"] or "")
    return cat["refill_days"] if cat else 0


def refill_deadline(row):
    created = parse_dt(row["created"])
    days = refill_days_for(row)
    if not created or days <= 0:
        return None
    return created + timedelta(days=days)


def refill_eligible(row):
    deadline = refill_deadline(row)
    if deadline is None or row["refill_id"]:
        return False
    return now_dt() <= deadline


# ---------- Redeem codes ----------
def create_code(code, amount, max_uses):
    with closing(db()) as c:
        try:
            c.execute("INSERT INTO redeem_codes (code, amount, max_uses, created) VALUES (?, ?, ?, ?)",
                      (code, amount, max_uses, now_dt().isoformat()))
            return True
        except sqlite3.IntegrityError:
            return False


def deactivate_code(code):
    with closing(db()) as c:
        cur = c.execute("UPDATE redeem_codes SET active = 0 WHERE code = ?", (code,))
        return cur.rowcount == 1


def list_codes():
    with closing(db()) as c:
        return c.execute("SELECT * FROM redeem_codes ORDER BY created DESC").fetchall()


def redeem(uid, code):
    with closing(db()) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            row = c.execute("SELECT * FROM redeem_codes WHERE code = ?", (code,)).fetchone()
            if not row:
                c.execute("ROLLBACK")
                return "not_found", 0
            if not row["active"] or (row["max_uses"] and row["used_count"] >= row["max_uses"]):
                c.execute("ROLLBACK")
                return "expired", 0
            if c.execute("SELECT 1 FROM redemptions WHERE code = ? AND user_id = ?", (code, uid)).fetchone():
                c.execute("ROLLBACK")
                return "already", 0
            c.execute("INSERT INTO redemptions (code, user_id, created) VALUES (?, ?, ?)",
                      (code, uid, now_dt().isoformat()))
            c.execute("UPDATE redeem_codes SET used_count = used_count + 1 WHERE code = ?", (code,))
            c.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (row["amount"], uid))
            c.execute(
                "INSERT INTO transactions (user_id, kind, amount, note, created) VALUES (?, ?, ?, ?, ?)",
                (uid, "redeem", row["amount"], code, now_dt().isoformat()),
            )
            c.execute("COMMIT")
            return "ok", row["amount"]
        except Exception:
            c.execute("ROLLBACK")
            raise


# ---------- Admin stats ----------
def admin_stats():
    with closing(db()) as c:
        users = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        o = c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(cost), 0) AS s FROM orders").fetchone()
        today = c.execute("SELECT COUNT(*) AS n FROM orders WHERE substr(created, 1, 10) = ?",
                          (now_dt().date().isoformat(),)).fetchone()["n"]
    return users, o["n"], o["s"], today


# ---------- ভয়েস গাইড পছন্দ (NULL = এখনো জিজ্ঞেস করা হয়নি, 1 = চালু, 0 = বন্ধ) ----------
def voice_pref(uid):
    with closing(db()) as c:
        r = c.execute("SELECT voice FROM users WHERE id = ?", (uid,)).fetchone()
        return None if (r is None or r["voice"] is None) else int(r["voice"])


def set_voice_pref(uid, on):
    with closing(db()) as c:
        c.execute("UPDATE users SET voice = ? WHERE id = ?", (1 if on else 0, uid))

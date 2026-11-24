import asyncio
import hashlib
import html
import logging
import os
import re
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import wraps
from io import BytesIO
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageDraw, ImageFont
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    TypeHandler,
    filters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sp-panel")

# ============================================================
# Render-এ শুধু চারটি Environment Variable লাগে।
# বাকি সব সেটিং অ্যাডমিন প্যানেল (/admin) থেকে।
# ============================================================
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
PANEL_API_URL = os.environ["PANEL_API_URL"]
PANEL_API_KEY = os.environ["PANEL_API_KEY"]

# Render Persistent Disk /var/data-তে মাউন্ট করা থাকলে ডেটা স্থায়ী থাকবে
DB_PATH = "/var/data/bot.db" if os.path.isdir("/var/data") else "bot.db"

CURRENCY = "৳"
TZ = timezone(timedelta(hours=6))  # বাংলাদেশ সময়
QTY_PRESETS = [100, 400, 1000, 5000]
DEFAULT_PREFIXES = "54,18"
MEMBER_CACHE_SECONDS = 15
DIV = "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"
SPINNER = ["◜", "◠", "◝", "◞", "◡", "◟"]

PLATFORM_EMOJI = {
    "telegram": "✈️", "whatsapp": "💬", "facebook": "📘", "instagram": "📸",
    "tiktok": "🎵", "youtube": "▶️", "imo": "📞", "x": "🐦", "twitter": "🐦",
    "snapchat": "👻", "linkedin": "💼", "website": "🌐",
}

# ক্যাটাগরি অনুযায়ী ব্র্যান্ড রং ও চিহ্ন (লোগো নিজে থেকে তৈরি হয়)
BRANDS = {
    "telegram": ("#229ED9", "plane"),
    "whatsapp": ("#25D366", "bubble"),
    "facebook": ("#1877F2", "f"),
    "instagram": ("#E1306C", "ring"),
    "tiktok": ("#0B0B0F", "♪"),
    "youtube": ("#FF0000", "play"),
    "imo": ("#1BA3E0", "imo"),
    "x": ("#111111", "X"),
    "twitter": ("#111111", "X"),
    "snapchat": ("#FFFC00", "S"),
    "linkedin": ("#0A66C2", "in"),
}


def platform_emoji(platform):
    return PLATFORM_EMOJI.get((platform or "").lower().strip(), "🛍")


def logo_key(platform):
    return f"logo:{(platform or '').lower().strip()}"


# ============================================================
# Logo generator (ক্যাটাগরির নাম দিলেই লোগো)
# ============================================================
def _font(size):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _hex(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def make_logo_png(name):
    key = (name or "").lower().strip()
    if key in BRANDS:
        color, sym = BRANDS[key]
        rgb = _hex(color)
    else:
        digest = hashlib.md5(key.encode()).digest()
        rgb = (60 + digest[0] % 150, 60 + digest[1] % 150, 60 + digest[2] % 150)
        sym = (name.strip()[:2] or "SP").upper()
    S = 512
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=118, fill=rgb + (255,))
    white = (255, 255, 255, 255)
    dark = (17, 17, 17, 255)
    ink = dark if rgb[0] > 200 and rgb[1] > 200 else white
    if sym == "plane":
        d.polygon([(118, 250), (396, 132), (346, 378), (256, 302), (214, 352),
                   (214, 282), (334, 196), (200, 270)], fill=white)
    elif sym == "bubble":
        d.ellipse([110, 100, 402, 352], fill=white)
        d.polygon([(150, 330), (120, 410), (210, 350)], fill=white)
    elif sym == "ring":
        d.rounded_rectangle([110, 110, 402, 402], radius=90, outline=white, width=34)
        d.ellipse([200, 200, 312, 312], outline=white, width=34)
    elif sym == "play":
        d.rounded_rectangle([96, 150, 416, 362], radius=50, fill=white)
        d.polygon([(226, 200), (226, 312), (318, 256)], fill=rgb + (255,))
    else:
        size = 250 if len(sym) == 1 else 180
        d.text((S / 2, S / 2), sym, font=_font(size), fill=ink, anchor="mm")
    buf = BytesIO()
    img.convert("RGB").save(buf, "PNG")
    buf.seek(0)
    buf.name = "logo.png"
    return buf


# ============================================================
# Database
# ============================================================
def db():
    conn = sqlite3.connect(DB_PATH, isolation_level=None)  # autocommit, ট্রানজ্যাকশন ম্যানুয়াল
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


def init_db():
    with closing(db()) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            username TEXT,
            balance REAL NOT NULL DEFAULT 0,
            is_banned INTEGER NOT NULL DEFAULT 0,
            banned_until TEXT,
            joined TEXT
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
        """)
        svc_cols = [r["name"] for r in c.execute("PRAGMA table_info(services)").fetchall()]
        if "photo_id" not in svc_cols:
            c.execute("ALTER TABLE services ADD COLUMN photo_id TEXT")
        ord_cols = [r["name"] for r in c.execute("PRAGMA table_info(orders)").fetchall()]
        if "display_no" not in ord_cols:
            c.execute("ALTER TABLE orders ADD COLUMN display_no TEXT")
            c.execute("UPDATE orders SET display_no = 'SP' || printf('%06d', id) WHERE display_no IS NULL")
        if "refill_id" not in ord_cols:
            c.execute("ALTER TABLE orders ADD COLUMN refill_id TEXT")


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
    """kind: 'public' (চ্যানেল ১ - মেম্বার) বা 'detail' (চ্যানেল ২ - অ্যাডমিন)।"""
    value = get_setting(f"channel_{kind}")
    if not value or value == "off":
        return None
    return value


def join_channel():
    value = get_setting("join_channel")
    if not value or value == "off":
        return None
    return value


def join_link():
    return get_setting("join_link") or None


def order_prefixes():
    raw = get_setting("order_prefixes") or DEFAULT_PREFIXES
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or DEFAULT_PREFIXES.split(",")


def order_mode():
    return get_setting("order_no_mode") or "bot"


def panel_site():
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


def status_text(row):
    if not row["is_banned"]:
        return "✅ সক্রিয়"
    if parse_dt(row["banned_until"]):
        return f"⏳ সাময়িক ব্যান (শেষ: {fmt_time(row['banned_until'])})"
    return "🚫 স্থায়ী ব্যান"


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


def add_service(name, platform, panel_id, price, min_qty, max_qty, photo_id):
    key = "s" + uuid.uuid4().hex[:8]
    with closing(db()) as c:
        c.execute(
            """INSERT INTO services (key, platform, name, emoji, panel_id, price, min_qty, max_qty,
                                     active, created, photo_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (key, platform, name, cat_emoji(platform), panel_id, price, min_qty, max_qty,
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


def refill_eligible(row):
    days = refill_days_for(row)
    if days <= 0 or row["refill_id"]:
        return False
    created = parse_dt(row["created"])
    if not created:
        return False
    return now_dt() - created <= timedelta(days=days)


def refill_status(row):
    if row["refill_id"]:
        return f"✅ অনুরোধ দেওয়া হয়েছে (আইডি {row['refill_id']})"
    if refill_eligible(row):
        return f"🟢 সক্রিয়, {refill_days_for(row)} দিনের মধ্যে"
    return "⚪ এই অর্ডারে রিফিল নেই"


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


# ============================================================
# Panel API
# ============================================================
async def panel(params):
    data = {"key": PANEL_API_KEY, **params}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(PANEL_API_URL, data=data)
    try:
        return r.json()
    except ValueError:
        return {"error": r.text[:200]}


# ============================================================
# Channels & membership
# ============================================================
def target_value(v):
    v = str(v).strip()
    return int(v) if re.fullmatch(r"-?\d+", v) else v


def normalize_channel_input(text):
    t = text.strip()
    if re.fullmatch(r"-?\d+", t):
        return t
    t = t.split("t.me/", 1)[-1].split("/", 1)[0]
    return "@" + t.lstrip("@")


def parse_link_target(link):
    m = re.search(r"t\.me/c/(\d+)", link or "")
    if m:
        return None, int(f"-100{m.group(1)}")
    m = re.search(r"t\.me/([A-Za-z][A-Za-z0-9_]{3,})", link or "")
    if m and m.group(1).lower() not in ("joinchat", "share", "addstickers", "s"):
        return m.group(1), None
    return None, None


async def describe_link_channel(context, link):
    username, chat_id = parse_link_target(link)
    if not username and not chat_id:
        return "শনাক্ত করা যায়নি"
    cid = chat_id
    if username:
        url = f"https://t.me/{username}"
        try:
            cid = (await context.bot.get_chat(f"@{username}")).id
        except Exception:
            cid = None
        name = f"@{username}"
    else:
        url = f"https://t.me/c/{str(chat_id)[4:]}"
        name = "প্রাইভেট চ্যানেল"
    idline = f"<code>{cid}</code>" if cid else "পাওয়া যায়নি"
    return f"{h(name)} · {idline}\n🌐 {h(url)}"


async def send_safe(context, target, text):
    try:
        await context.bot.send_message(target_value(target), text,
                                       link_preview_options=LinkPreviewOptions(is_disabled=True))
    except Exception as e:
        log.error("channel post to %s failed: %s", target, e)
        await notify_user(context, ADMIN_ID, card(
            "চ্যানেলে বার্তা যায়নি",
            f"লক্ষ্য: <code>{h(target)}</code>\nকারণ: {h(e)}\n\n"
            "বটকে ওই চ্যানেলের অ্যাডমিন বানান (মেসেজ পাঠানোর অনুমতিসহ)।", icon="⚠️"))


def public_order_text(srv, link, qty, cost):
    """চ্যানেল ১ (সকল ইউজার): শুধু সার্ভিস, পরিমাণ, টাকা ও হিডেন লিংক।"""
    return (
        "🆕 <b>নতুন অর্ডার</b>\n"
        f"{DIV}\n"
        f"<blockquote>🏷 সার্ভিস: <b>{h(service_title(srv))}</b>\n"
        f"📊 অর্ডার পরিমাণ: <b>{qty}</b>\n"
        f"💵 এই অর্ডারের খরচ: <b>{money(cost)}</b>\n"
        f"🔗 লিংক: <a href=\"{h(link)}\">হিডেন</a></blockquote>"
    )


def admin_order_text(label, user, srv, link, qty, cost, panel_id, when, chan_line):
    """চ্যানেল ২ (শুধু অ্যাডমিন): ইউজারের সব তথ্য ও অর্ডারের পুরো বিবরণ।"""
    uname = f"@{h(user['username'])}" if user["username"] else "নেই"
    return (
        "📑 <b>বিস্তারিত অর্ডার রিপোর্ট</b>\n"
        f"{DIV}\n"
        f"<blockquote>🔢 অর্ডার নম্বর: <code>{h(label)}</code>\n"
        f"👤 নাম: {h(full_name(user))}\n"
        f"🏷 ইউজারনেম: {uname}\n"
        f"🪪 ইউজার আইডি: <code>{user['id']}</code>\n"
        f"💰 ইউজারের ব্যালেন্স: {money(user['balance'])}\n"
        f"🛍 সার্ভিস: {h(service_title(srv))}\n"
        f"📢 যে চ্যানেলে দিয়েছে: {chan_line}\n"
        f"🔗 লিংক: {h(link)}\n"
        f"📊 পরিমাণ: <b>{qty}</b>\n"
        f"💵 মূল্য: <b>{money(cost)}</b>\n"
        f"🏦 প্যানেল অর্ডার: <code>{h(panel_id)}</code>\n"
        f"🕒 সময়: {h(when)}</blockquote>"
    )


async def post_order_channels(context, label, user, srv, link, qty, cost, panel_id):
    public = channel_setting("public")
    detail = channel_setting("detail")
    if public:
        await send_safe(context, public, public_order_text(srv, link, qty, cost))
    if detail:
        chan_line = await describe_link_channel(context, link)
        await send_safe(context, detail,
                        admin_order_text(label, user, srv, link, qty, cost, panel_id, now_str(), chan_line))


_member_cache = {}


async def is_member(bot, uid):
    chan = join_channel()
    if not chan:
        return True
    cached = _member_cache.get(uid)
    if cached and time.time() - cached[1] < MEMBER_CACHE_SECONDS:
        return cached[0]
    try:
        m = await bot.get_chat_member(target_value(chan), uid)
        ok = m.status in ("creator", "administrator", "member") or (
            m.status == "restricted" and bool(getattr(m, "is_member", False)))
    except Exception as e:
        log.warning("join check failed (allowing this time): %s", e)
        return True
    _member_cache[uid] = (ok, time.time())
    return ok


def join_markup():
    rows = []
    link = join_link()
    if link:
        rows.append([InlineKeyboardButton("🔗 চ্যানেলে জয়েন করুন", url=link)])
    rows.append([btn("✅ জয়েন করেছি — শুরু করুন", "joincheck")])
    return InlineKeyboardMarkup(rows)


JOIN_TEXT = (
    "🔐 <b>চ্যানেলে জয়েন করা বাধ্যতামূলক</b>\n"
    f"{DIV}\n"
    "বট ব্যবহার করতে আগে আমাদের চ্যানেলে জয়েন করুন।\n"
    "জয়েন করার পর নিচের বাটনে চাপুন, বট তখনই চালু হবে।"
)


# ============================================================
# Premium UI helpers
# ============================================================
def h(text):
    return html.escape(str(text))


def fmt(x):
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return s or "0"


def money(x):
    return f"{CURRENCY}{fmt(x)}"


def btn(text, data):
    return InlineKeyboardButton(text, callback_data=data)


def copy_btn(text, value):
    return InlineKeyboardButton(text, copy_text=CopyTextButton(text=str(value)))


def card(title, body, hint=None, icon="💠"):
    out = f"{icon} <b>{title}</b>\n{DIV}\n<blockquote>{body}</blockquote>"
    if hint:
        out += f"\n<i>{hint}</i>"
    return out


def data_of(q):
    try:
        return (q.data or "")[:60]
    except Exception:
        return ""


def is_admin_id(uid):
    return uid == ADMIN_ID


def is_admin(update):
    u = update.effective_user
    return bool(u) and is_admin_id(u.id)


def admin_only(func):
    @wraps(func)
    async def wrapper(update, context):
        if not is_admin(update):
            return
        return await func(update, context)
    return wrapper


def main_menu():
    rows = [[btn(f"{cat_emoji(c['name'])}  {c['name']}", f"cat_u:{c['name']}")] for c in list_categories()]
    rows.append([btn("💳 ব্যালেন্স", "balance"), btn("➕ টাকা যুক্ত", "deposit")])
    rows.append([btn("🎁 রিডিম কোড", "redeem"), btn("📦 আমার অর্ডার", "orders")])
    rows.append([btn("🏠 মেনু", "home")])
    return InlineKeyboardMarkup(rows)


def after_order_markup(oid):
    return InlineKeyboardMarkup(
        main_menu().inline_keyboard + [[btn("🔁 অর্ডার এগেইন", f"again:{oid}"), btn("📝 রিপোর্ট", f"report:{oid}")]]
    )


def greet_name(user):
    if user.username:
        return f"@{user.username}"
    name = " ".join(p for p in (user.first_name, user.last_name) if p)
    return name.strip() or "বন্ধু"


def welcome_text(user):
    head = f"💠 <b>{h(brand())}</b>\n" if brand() else "💠 "
    lines = [
        head + "<i>দ্রুত • নিরাপদ • স্বয়ংক্রিয় প্ল্যাটফর্ম</i>",
        DIV,
        f"👋 স্বাগতম, <b>{h(greet_name(user))}</b>",
        "",
        "◈ <b>প্রাইস তালিকা</b>",
    ]
    rows = []
    for cat in list_categories():
        for s in list_services(platform=cat["name"]):
            rows.append(f"{s['emoji']} {h(service_title(s))}  ·  <b>{money(s['price'])}</b> / ১০০০")
    if rows:
        lines.append("<blockquote>" + "\n".join(rows) + "</blockquote>")
    else:
        lines.append("<blockquote>এখন কোনো সার্ভিস চালু নেই।</blockquote>")
    lines += [DIV, "▸ <i>নিচের মেনু থেকে বেছে নিন</i>"]
    return "\n".join(lines)


def cancel_kb():
    return InlineKeyboardMarkup([[btn("✖ বাতিল", "flowcancel")]])


# ---------- Loading (দ্রুত, থিম-ভিত্তিক) ----------
def _bar(step):
    filled = "▰" * step
    return filled + "▱" * (4 - step)


def loading_frames(label):
    return [f"{SPINNER[i % len(SPINNER)]}  {label}\n{_bar(i + 1)}" for i in range(4)]


async def show_loading(context, chat_id, label, delay=0.12):
    """অ্যাডমিনের দেওয়া GIF/ভিডিও থাকলে সেটা, নাহলে স্পিনার অ্যানিমেশন। খুব দ্রুত।"""
    frames = loading_frames(label)
    anim = get_setting("loading_anim")
    kind = get_setting("loading_kind") or "animation"
    msg = None
    is_media = False
    if anim:
        try:
            sender = context.bot.send_video if kind == "video" else context.bot.send_animation
            msg = await sender(chat_id, anim, caption=label)
            is_media = True
        except Exception as e:
            log.warning("loading animation failed: %s", e)
            msg = None
    if msg is None:
        msg = await context.bot.send_message(chat_id, frames[0])
    for f in frames[1:]:
        await asyncio.sleep(delay)
        try:
            if is_media:
                await msg.edit_caption(caption=f)
            else:
                await msg.edit_text(f)
        except BadRequest:
            pass
        except Exception as e:
            log.warning("loading frame skipped: %s", e)
            break
    return msg


async def finish_loading(msg):
    try:
        await msg.delete()
    except Exception:
        pass


async def notify_user(context, uid, text):
    try:
        await context.bot.send_message(uid, text)
    except Exception as e:
        log.warning("notify %s failed: %s", uid, e)


def redeem_message(status, amount):
    if status == "ok":
        return card("কোড সফলভাবে ক্লেইম হয়েছে", f"{money(amount)} আপনার ব্যালেন্সে যুক্ত হয়েছে।", icon="🎉")
    if status == "already":
        return card("ইতিমধ্যে ব্যবহৃত", "আপনি এই কোডটি আগেই ব্যবহার করেছেন।", icon="⚠️")
    if status == "expired":
        return card("কোডের মেয়াদ শেষ", "এই কোডের মেয়াদ শেষ বা ব্যবহারের সীমা পূর্ণ হয়েছে।", icon="⌛")
    return card("কোড সঠিক নয়", "কোডটি মেলেনি। আবার চেষ্টা করুন।", icon="❌")


def deposit_text(uid):
    sup = support_username()
    contact = f"👤 সাপোর্ট: @{h(sup)}" if sup else "👤 সাপোর্ট: অ্যাডমিনের সাথে যোগাযোগ করুন"
    body = (
        "১️⃣ পেমেন্ট করুন\n"
        "২️⃣ সাপোর্টে আপনার আইডি জানান\n"
        "৩️⃣ ব্যালেন্স যুক্ত হলে বার্তা পাবেন\n\n"
        f"{contact}\n🪪 আপনার আইডি: <code>{uid}</code>"
    )
    return card("টাকা যুক্ত করুন", body, icon="➕")


def balance_text(uid):
    row = get_user(uid)
    st = user_stats(uid)
    body = (
        f"💰 বর্তমান ব্যালেন্স: <b>{money(row['balance'])}</b>\n"
        f"📥 মোট জমা: {money(st['deposits'])}\n"
        f"📤 মোট খরচ: {money(st['spent'])}\n"
        f"🎁 রিডিম থেকে: {money(st['redeems'])}\n"
        f"📦 মোট অর্ডার: {st['orders']}"
    )
    return card("আপনার অ্যাকাউন্ট", body, icon="💳")


def order_line(r):
    return (f"🔹 <code>{h(order_label(r))}</code>  {h(r['service_name'])}  ·  "
            f"{r['quantity']}  ·  {money(r['cost'])}")


def user_info_text(row):
    st = user_stats(row["id"])
    uname = f"@{h(row['username'])}" if row["username"] else "—"
    body = (
        f"নাম: {h(full_name(row))}\n"
        f"ইউজারনেম: {uname}\n"
        f"আইডি: <code>{row['id']}</code>\n"
        f"যোগদান: {h(fmt_time(row['joined']))}\n"
        f"স্ট্যাটাস: {status_text(row)}\n\n"
        f"বর্তমান ব্যালেন্স: <b>{money(row['balance'])}</b>\n"
        f"মোট জমা: {money(st['deposits'])}\n"
        f"মোট খরচ: {money(st['spent'])}\n"
        f"রিডিম থেকে: {money(st['redeems'])}\n"
        f"মোট অর্ডার: {st['orders']}"
    )
    return card("ইউজার তথ্য", body, icon="👤")


def order_info_text(r):
    user = get_user(r["user_id"])
    who = f"{h(full_name(user))} (<code>{r['user_id']}</code>)" if user else f"<code>{r['user_id']}</code>"
    body = (
        f"ইউজার: {who}\n"
        f"সার্ভিস: {h(r['service_name'])}\n"
        f"লিংক: {h(r['link'])}\n"
        f"পরিমাণ: {r['quantity']}\n"
        f"মূল্য: {money(r['cost'])}\n"
        f"প্যানেল অর্ডার: <code>{h(r['panel_order'])}</code>\n"
        f"সময়: {h(fmt_time(r['created']))}\n"
        f"রিফিল: {h(refill_status(r))}"
    )
    return card(f"অর্ডার {h(order_label(r))}", body, icon="🔎")


def general_text():
    sup = support_username()
    body = (
        f"🏷 ব্র্যান্ড নাম: {h(brand()) if brand() else 'সেট করা নেই'}\n"
        f"👤 সাপোর্ট ইউজারনেম: {('@' + h(sup)) if sup else 'সেট করা নেই'}"
    )
    return card("সাধারণ সেটিং", body, icon="⚙️")


def channels_text():
    def show(v):
        return f"<code>{h(v)}</code>" if v else "বন্ধ"
    body = (
        f"📢 চ্যানেল ১ (সকল ইউজার): {show(channel_setting('public'))}\n"
        f"🔒 চ্যানেল ২ (শুধু অ্যাডমিন): {show(channel_setting('detail'))}\n"
        f"🔐 বাধ্যতামূলক জয়েন: {show(join_channel())}"
    )
    return card("চ্যানেল সেটিং", body, hint="বন্ধ করতে চ্যানেল পাঠানোর সময় <code>off</code> লিখুন।", icon="📣")


def order_settings_text():
    mode = "বট নম্বর" if order_mode() == "bot" else "প্যানেল নম্বর"
    example = make_display_no(1, 0) if order_mode() == "bot" else "14759 (প্যানেলের আইডি)"
    body = (
        f"মোড: {mode}\n"
        f"প্রিফিক্স: {h(', '.join(order_prefixes()))}\n"
        f"উদাহরণ: <code>{h(example)}</code>"
    )
    return card("অর্ডার নম্বর", body, hint="বট নম্বর = প্রিফিক্স + ক্রমিক নম্বর। প্যানেল নম্বর = ওয়েবসাইটের নম্বর।",
                icon="🔢")


async def show_service(context, chat_id, srv):
    mx = f"{srv['max_qty']}" if srv["max_qty"] else "সীমাহীন"
    text = card(
        service_title(srv),
        f"💵 মূল্য: <b>{money(srv['price'])}</b> / ১০০০\n"
        f"📏 সর্বনিম্ন: {srv['min_qty']}  ·  সর্বোচ্চ: {mx}",
        hint="ধাপ ১/৩ — আপনার পোস্ট বা চ্যানেলের লিংক পাঠান",
        icon=srv["emoji"],
    )
    cat = get_category(srv["platform"] or "")
    photo = srv["photo_id"] or (category_logo(srv["platform"]) if srv["platform"] else None)
    if photo:
        try:
            await context.bot.send_photo(chat_id, photo, caption=text, reply_markup=cancel_kb())
            return
        except Exception as e:
            log.warning("service photo send failed: %s", e)
    await context.bot.send_message(chat_id, text, reply_markup=cancel_kb())


# ============================================================
# Gate: ব্যান, বাধ্যতামূলক জয়েন ও ইউজার আপডেট
# ============================================================
async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None:
        return
    upsert_user(user)
    if is_admin_id(user.id):
        return
    row = get_user(user.id)
    if row and row["is_banned"]:
        until = parse_dt(row["banned_until"])
        if until and now_dt() >= until:
            set_ban(user.id, None, 0)
        else:
            text = (f"⛔ আপনার অ্যাকাউন্ট {fmt_time(row['banned_until'])} পর্যন্ত সীমিত করা হয়েছে।"
                    if until else "⛔ আপনার অ্যাকাউন্ট স্থায়ীভাবে সীমিত করা হয়েছে।")
            if update.callback_query:
                await update.callback_query.answer(text, show_alert=True)
            elif update.message:
                await update.message.reply_text(text)
            raise ApplicationHandlerStop

    if not await is_member(context.bot, user.id):
        chat_id = update.effective_chat.id if update.effective_chat else user.id
        if update.callback_query:
            await update.callback_query.answer("আগে চ্যানেলে জয়েন করুন।", show_alert=True)
        await context.bot.send_message(chat_id, JOIN_TEXT, reply_markup=join_markup())
        raise ApplicationHandlerStop


# ============================================================
# Commands
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("flow", None)
    chat_id = update.effective_chat.id
    loader = await show_loading(context, chat_id, "প্রস্তুত হচ্ছে")
    await finish_loading(loader)
    await context.bot.send_message(chat_id, welcome_text(update.effective_user), reply_markup=main_menu())


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    context.user_data.pop("flow", None)
    await update.message.reply_text(**admin_home())


async def show_category(context, chat_id, name):
    srvs = list_services(platform=name)
    if not srvs:
        await context.bot.send_message(chat_id, card("এখন সার্ভিস নেই", f"<b>{h(name)}</b>-এ এখন কোনো সার্ভিস চালু নেই।",
                                                     icon="⚠️"), reply_markup=main_menu())
        return
    lines = [f"{s['emoji']} <b>{h(s['name'])}</b>\n    {money(s['price'])} / ১০০০  ·  সর্বনিম্ন {s['min_qty']}"
             for s in srvs]
    text = card(f"{cat_emoji(name)} {h(name)}", "\n".join(lines), hint="একটি সার্ভিস বেছে নিন", icon="📂")
    kb = [[btn(f"{s['emoji']}  {s['name']}", f"svc:{s['key']}")] for s in srvs]
    kb.append([btn("‹ মেনু", "home")])
    logo = category_logo(name)
    if logo:
        try:
            await context.bot.send_photo(chat_id, logo, caption=text, reply_markup=InlineKeyboardMarkup(kb))
            return
        except Exception as e:
            log.warning("category logo send failed: %s", e)
    await context.bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(kb))


async def send_qty_prompt(context, chat_id, srv):
    mx = srv["max_qty"]
    limit = f"সর্বনিম্ন {srv['min_qty']}" + (f", সর্বোচ্চ {mx}" if mx else ", ১০০-এর বেশি যত ইচ্ছা")
    presets = [n for n in QTY_PRESETS if n >= srv["min_qty"] and (mx == 0 or n <= mx)]
    kb = [[btn(str(n), f"qty:{n}") for n in presets[i:i + 2]] for i in range(0, len(presets), 2)]
    await context.bot.send_message(
        chat_id,
        card("পরিমাণ নির্বাচন", f"পরিমাণ লিখুন ({limit}) অথবা নিচ থেকে বেছে নিন।",
             hint="ধাপ ২/৩", icon="📊"),
        reply_markup=InlineKeyboardMarkup(kb) if kb else None,
    )


async def set_qty_summary(context, chat_id, flow, qty, uid):
    d = flow["data"]
    srv = get_service(d["key"])
    if not srv or not srv["active"]:
        context.user_data.pop("flow", None)
        await context.bot.send_message(chat_id, card("সার্ভিস পাওয়া যাচ্ছে না", "এই সার্ভিসটি এখন চালু নেই।", icon="⚠️"),
                                       reply_markup=main_menu())
        return
    if qty < srv["min_qty"] or (srv["max_qty"] and qty > srv["max_qty"]):
        await context.bot.send_message(
            chat_id,
            f"⚠️ পরিমাণ {srv['min_qty']}" + (f" থেকে {srv['max_qty']}" if srv["max_qty"] else " বা তার বেশি")
            + " হতে হবে।",
        )
        return
    cost = round(qty * srv["price"] / 1000, 4)
    d.update(qty=qty, cost=cost)
    flow["step"] = "confirm"
    balance = get_user(uid)["balance"]
    warn = "" if balance >= cost else "\n\n⚠️ ব্যালেন্স যথেষ্ট নয়। আগে টাকা যুক্ত করুন।"
    body = (
        f"🏷 সার্ভিস: {h(service_title(srv))}\n"
        f"🔗 লিংক: <i>{h(d['link'])}</i>\n"
        f"📊 পরিমাণ: <b>{qty}</b>\n"
        f"💵 মূল্য: <b>{money(cost)}</b>"
    )
    text = card("অর্ডার সারাংশ", body, hint=f"ধাপ ৩/৩ — কনফার্ম করুন  ·  ব্যালেন্স: {money(balance)}{warn}", icon="📋")
    kb = InlineKeyboardMarkup([
        [btn("✅ কনফার্ম", "order_confirm"), btn("❌ বাতিল", "order_cancel")],
        [copy_btn("📋 লিংক কপি", d["link"])],
    ])
    await context.bot.send_message(chat_id, text, reply_markup=kb,
                                   link_preview_options=LinkPreviewOptions(is_disabled=True))


async def place_order(q, context, flow):
    d = flow["data"]
    context.user_data.pop("flow", None)
    uid = q.from_user.id
    chat_id = q.message.chat.id
    srv = get_service(d["key"])
    if not srv or not srv["active"]:
        await context.bot.send_message(chat_id, card("সার্ভিস পাওয়া যাচ্ছে না", "এই সার্ভিসটি এখন চালু নেই।", icon="⚠️"),
                                       reply_markup=main_menu())
        return
    if not charge_order(uid, d["cost"]):
        await context.bot.send_message(chat_id, card("ব্যালেন্স যথেষ্ট নয়", "অর্ডার করতে আগে টাকা যুক্ত করুন।", icon="❌"),
                                       reply_markup=main_menu())
        return

    loader = await show_loading(context, chat_id, "অর্ডার পাঠানো হচ্ছে")

    try:
        resp = await panel({"action": "add", "service": srv["panel_id"], "link": d["link"], "quantity": d["qty"]})
    except httpx.HTTPError as e:
        resp = {"error": str(e)}

    if "order" not in resp:
        adjust_balance(uid, d["cost"], "refund", "ব্যর্থ অর্ডার ফেরত")
        log.error("panel add failed: %s", resp)
        await notify_user(context, ADMIN_ID,
                          card("প্যানেল অর্ডার ব্যর্থ", f"ইউজার: <code>{uid}</code>\nসার্ভিস: {h(service_title(srv))}\n"
                               f"কারণ: {h(resp)}", icon="⚠️"))
        await finish_loading(loader)
        await context.bot.send_message(
            chat_id,
            card("অর্ডার সম্পন্ন হয়নি", "টাকা আপনার ব্যালেন্সে ফেরত দেওয়া হয়েছে। কিছুক্ষণ পর আবার চেষ্টা করুন।", icon="❌"),
            reply_markup=main_menu(),
        )
        return

    panel_id = str(resp["order"])
    try:
        oid, label = save_order(uid, srv, d["link"], d["qty"], d["cost"], panel_id)
    except Exception:
        log.exception("save_order failed after panel order %s", panel_id)
        await finish_loading(loader)
        await notify_user(context, ADMIN_ID, card("অর্ডার সেভ হয়নি",
                          f"প্যানেল অর্ডার: <code>{h(panel_id)}</code>\nইউজার: <code>{uid}</code>", icon="⚠️"))
        await context.bot.send_message(chat_id, card("অর্ডার গৃহীত হয়েছে",
                                                     "সাপোর্টে যোগাযোগ করুন, অর্ডার নম্বর জানিয়ে দেওয়া হবে।", icon="✅"),
                                       reply_markup=main_menu())
        return
    await finish_loading(loader)
    body = (
        f"🔢 অর্ডার নম্বর: <code>{h(label)}</code>\n"
        f"🏷 সার্ভিস: {h(service_title(srv))}\n"
        f"📊 পরিমাণ: <b>{d['qty']}</b>\n"
        f"💵 কাটা হয়েছে: <b>{money(d['cost'])}</b>"
    )
    try:
        await context.bot.send_message(chat_id, card("অর্ডার সম্পন্ন হয়েছে", body, icon="✅"),
                                       reply_markup=after_order_markup(oid))
    except Exception:
        log.exception("success message failed (order %s)", label)
        await context.bot.send_message(chat_id, card("অর্ডার সম্পন্ন হয়েছে", body, icon="✅"),
                                       reply_markup=main_menu())

    try:
        user = get_user(uid)
        await post_order_channels(context, label, user, srv, d["link"], d["qty"], d["cost"], panel_id)
    except Exception:
        log.exception("channel posting failed (order %s)", label)


async def do_redeem(context, chat_id, uid, code):
    loader = await show_loading(context, chat_id, "কোড যাচাই হচ্ছে")
    status, amount = redeem(uid, code)
    await finish_loading(loader)
    await context.bot.send_message(chat_id, redeem_message(status, amount), reply_markup=main_menu())


async def send_report(context, chat_id, row):
    body = (
        f"🏷 সার্ভিস: {h(row['service_name'])}\n"
        f"📊 পরিমাণ: {row['quantity']}\n"
        f"💵 মূল্য: {money(row['cost'])}\n"
        f"📅 সময়: {h(fmt_time(row['created']))}\n"
        f"🔄 রিফিল: {h(refill_status(row))}"
    )
    text = card(f"রিপোর্ট · {h(order_label(row))}", body, icon="📝")
    rows = [[InlineKeyboardButton("🚀 স্পিড আপ", url=panel_site())]]
    if refill_eligible(row):
        rows.append([btn("🔄 রিফিল করুন", f"refill:{row['id']}")])
    rows.append([btn("🏠 মেনু", "home")])
    await context.bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup(rows))


async def do_refill(context, chat_id, uid, oid):
    row = get_order(oid)
    if not row or row["user_id"] != uid:
        await context.bot.send_message(chat_id, card("অর্ডার পাওয়া যায়নি", "এই অর্ডারটি আপনার নয়।", icon="⚠️"),
                                       reply_markup=main_menu())
        return
    if not refill_eligible(row):
        await context.bot.send_message(chat_id, card("রিফিল সম্ভব নয়", "সময়সীমা শেষ, অথবা আগেই রিফিল করা হয়েছে।", icon="⚠️"),
                                       reply_markup=main_menu())
        return
    loader = await show_loading(context, chat_id, "রিফিল পাঠানো হচ্ছে")
    try:
        resp = await panel({"action": "refill", "order": row["panel_order"]})
    except httpx.HTTPError as e:
        resp = {"error": str(e)}
    await finish_loading(loader)
    if "refill" in resp:
        set_refill(oid, str(resp["refill"]))
        await context.bot.send_message(
            chat_id,
            card("রিফিল অনুরোধ দেওয়া হয়েছে",
                 f"🔢 অর্ডার: <code>{h(order_label(row))}</code>\n🔄 রিফিল আইডি: <code>{h(resp['refill'])}</code>",
                 icon="✅"),
            reply_markup=main_menu(),
        )
    else:
        log.error("refill failed: %s", resp)
        await context.bot.send_message(
            chat_id,
            card("রিফিল হয়নি", "অনুরোধটি গ্রহণ করা হয়নি। কিছুক্ষণ পর আবার চেষ্টা করুন।", icon="❌"),
            reply_markup=main_menu(),
        )


async def send_orders_list(context, chat_id, uid):
    rows = user_orders(uid)
    if not rows:
        await context.bot.send_message(chat_id, card("আমার অর্ডার", "আপনার এখনো কোনো অর্ডার নেই।", icon="📦"),
                                       reply_markup=main_menu())
        return
    body = "\n".join(order_line(r) for r in rows)
    kb = [[btn(f"📝 {order_label(r)}", f"report:{r['id']}")] for r in rows[:5]]
    kb.append([btn("🏠 মেনু", "home")])
    await context.bot.send_message(chat_id, card("আমার অর্ডার", body, icon="📦"),
                                   reply_markup=InlineKeyboardMarkup(kb))


# ============================================================
# Callbacks (ইউজার)
# ============================================================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat.id
    loader = await show_loading(context, chat_id, "একটু অপেক্ষা করুন")
    try:
        await route_callback(q, context, chat_id)
    except Exception as e:
        log.exception("callback failed")
        await context.bot.send_message(chat_id, card("সমস্যা হয়েছে", "কিছু একটা ভুল হয়েছে। আবার চেষ্টা করুন।", icon="⚠️"),
                                       reply_markup=main_menu())
        await notify_user(context, ADMIN_ID, card("ত্রুটি ধরা পড়েছে",
                          f"<code>{h(type(e).__name__)}: {h(str(e))[:300]}</code>\nবাটন: <code>{h(data_of(q))}</code>",
                          icon="🐞"))
    finally:
        await finish_loading(loader)


async def route_callback(q, context, chat_id):
    data = q.data or ""
    uid = q.from_user.id
    flow = context.user_data.get("flow")

    if data in ("home", "joincheck"):
        context.user_data.pop("flow", None)
        await context.bot.send_message(chat_id, welcome_text(q.from_user), reply_markup=main_menu())

    elif data.startswith("cat_u:"):
        await show_category(context, chat_id, data.split(":", 1)[1])

    elif data == "balance":
        await context.bot.send_message(chat_id, balance_text(uid), reply_markup=main_menu())

    elif data == "deposit":
        sup = support_username()
        rows = [[copy_btn("📋 আইডি কপি", uid)]]
        if sup:
            rows.insert(0, [copy_btn("📋 সাপোর্ট ইউজারনেম কপি", f"@{sup}")])
            rows.append([InlineKeyboardButton("💬 সাপোর্টে মেসেজ", url=f"https://t.me/{sup}")])
        rows.append([btn("🏠 মেনু", "home")])
        await context.bot.send_message(chat_id, deposit_text(uid), reply_markup=InlineKeyboardMarkup(rows))

    elif data == "orders":
        await send_orders_list(context, chat_id, uid)

    elif data == "redeem":
        context.user_data["flow"] = {"name": "redeem", "step": "code", "data": {}}
        await context.bot.send_message(chat_id, card("রিডিম কোড", "আপনার কোডটি নিচের ঘরে লিখে পাঠান।", icon="🎁"),
                                       reply_markup=cancel_kb())

    elif data.startswith("svc:"):
        srv = get_service(data.split(":", 1)[1])
        if not srv or not srv["active"]:
            await context.bot.send_message(chat_id, card("সার্ভিস পাওয়া যাচ্ছে না", "এটি এখন চালু নেই।", icon="⚠️"),
                                           reply_markup=main_menu())
            return
        context.user_data["flow"] = {"name": "order", "step": "link", "data": {"key": srv["key"]}}
        await show_service(context, chat_id, srv)

    elif data.startswith("again:"):
        row = get_order(int(data.split(":", 1)[1]))
        if not row or row["user_id"] != uid:
            await context.bot.send_message(chat_id, card("অর্ডার পাওয়া যায়নি", "এই অর্ডারটি আপনার নয়।", icon="⚠️"),
                                           reply_markup=main_menu())
            return
        srv = get_service(row["service_key"]) if row["service_key"] else None
        if not srv or not srv["active"]:
            await context.bot.send_message(chat_id, card("সার্ভিস নেই", "এই সার্ভিসটি এখন চালু নেই।", icon="⚠️"),
                                           reply_markup=main_menu())
            return
        context.user_data["flow"] = {"name": "order", "step": "link", "data": {"key": srv["key"]}}
        await show_service(context, chat_id, srv)

    elif data.startswith("report:"):
        row = get_order(int(data.split(":", 1)[1]))
        if not row or row["user_id"] != uid:
            await context.bot.send_message(chat_id, card("অর্ডার পাওয়া যায়নি", "এই অর্ডারটি আপনার নয়।", icon="⚠️"),
                                           reply_markup=main_menu())
            return
        await send_report(context, chat_id, row)

    elif data.startswith("refill:"):
        await do_refill(context, chat_id, uid, int(data.split(":", 1)[1]))

    elif data.startswith("qty:"):
        if flow and flow["name"] == "order" and flow["step"] == "qty":
            await set_qty_summary(context, chat_id, flow, int(data.split(":", 1)[1]), uid)
        else:
            await context.bot.send_message(chat_id, card("আগে সার্ভিস বেছে নিন", "মেনু থেকে একটি সার্ভিস বাছাই করুন।", icon="⚠️"),
                                           reply_markup=main_menu())

    elif data == "order_confirm":
        if flow and flow["name"] == "order" and flow["step"] == "confirm":
            await place_order(q, context, flow)
        else:
            await context.bot.send_message(chat_id, card("অর্ডার পাওয়া যায়নি", "আবার সার্ভিস বেছে নিন।", icon="⚠️"),
                                           reply_markup=main_menu())

    elif data in ("order_cancel", "flowcancel"):
        context.user_data.pop("flow", None)
        await context.bot.send_message(chat_id, card("বাতিল করা হয়েছে", "আবার শুরু করতে মেনু থেকে বেছে নিন।", icon="❌"),
                                       reply_markup=main_menu())

    elif data.startswith("adm:") or data.startswith("cat:") or data.startswith("catrefill:") \
            or data.startswith("catlogo:") or data.startswith("catsvc:") or data.startswith("catdel:") \
            or data.startswith("newcat_refill:") or data.startswith("chset:") or data.startswith("gen:") \
            or data.startswith("svcprice:") or data.startswith("svcdel:") or data.startswith("svcphoto:") \
            or data in ("ban_perm_yes", "ban_perm_no"):
        if not is_admin_id(uid):
            return
        await admin_route(q, context, data, chat_id)


# ============================================================
# Admin panel (সেকশনে সাজানো)
# ============================================================
def admin_stats():
    with closing(db()) as c:
        users = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        o = c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(cost), 0) AS s FROM orders").fetchone()
        today = c.execute("SELECT COUNT(*) AS n FROM orders WHERE substr(created, 1, 10) = ?",
                          (now_dt().date().isoformat(),)).fetchone()["n"]
    return users, o["n"], o["s"], today


def admin_home():
    users, orders, spent, today = admin_stats()
    stats = (f"👥 ইউজার: <b>{users}</b>  ·  🧾 অর্ডার: <b>{orders}</b>\n"
             f"💰 মোট বিক্রি: <b>{money(spent)}</b>  ·  📅 আজ: <b>{today}</b>\n{DIV}\n")
    body = stats + (
        "👥 ইউজার ও ব্যান\n"
        "💰 ব্যালেন্স ও রিডিম কোড\n"
        "🛍 সার্ভিস ও ক্যাটাগরি\n"
        "📣 চ্যানেল ও জয়েন\n"
        "⚙️ সাধারণ সেটিং ও অ্যানিমেশন\n"
        "🔎 খোঁজ ও প্যানেল"
    )
    return {
        "text": card("অ্যাডমিন প্যানেল", body, hint="একটি বিভাগ বেছে নিন।", icon="🛠"),
        "parse_mode": ParseMode.HTML,
        "reply_markup": InlineKeyboardMarkup([
            [btn("👥 ইউজার", "adm:m:users"), btn("💰 ব্যালেন্স", "adm:m:money")],
            [btn("🛍 সার্ভিস", "adm:m:services"), btn("📣 চ্যানেল", "adm:m:channels")],
            [btn("⚙️ সেটিং", "adm:m:settings"), btn("🔎 খোঁজ", "adm:m:search")],
            [btn("✖ বন্ধ করুন", "adm:close")],
        ]),
    }


def admin_section(section):
    back = [btn("‹ ফিরে যান", "adm:m:home")]
    if section == "users":
        return (card("ইউজার ব্যবস্থাপনা", "ইউজার তালিকা, তথ্য, ব্যান ও আনব্যান।", icon="👥"),
                InlineKeyboardMarkup([
                    [btn("👥 ইউজার তালিকা", "adm:users"), btn("👤 ইউজার তথ্য", "adm:userinfo")],
                    [btn("🚫 ব্যান", "adm:ban"), btn("✅ আনব্যান", "adm:unban")],
                    back]))
    if section == "money":
        return (card("ব্যালেন্স ও রিডিম কোড", "টাকা যোগ/কাটা এবং রিডিম কোড ব্যবস্থাপনা।", icon="💰"),
                InlineKeyboardMarkup([
                    [btn("➕ ব্যালেন্স যোগ", "adm:addbal"), btn("➖ ব্যালেন্স কাটা", "adm:subbal")],
                    [btn("🎁 নতুন রিডিম কোড", "adm:codenew"), btn("🗂 কোড তালিকা", "adm:codes")],
                    [btn("⛔ কোড বন্ধ", "adm:codedel")],
                    back]))
    if section == "services":
        return (card("সার্ভিস ও ক্যাটাগরি", "ক্যাটাগরি যোগ করলে লোগো ও রিফিল সেটিং নিজে হবে।", icon="🛍"),
                InlineKeyboardMarkup([
                    [btn("➕ নতুন ক্যাটাগরি", "adm:newcat"), btn("🗂 ক্যাটাগরি তালিকা", "adm:cats")],
                    [btn("📋 সব সার্ভিস", "adm:svclist"), btn("💰 প্রাইস বদল", "adm:svcprice")],
                    [btn("🖼 সার্ভিসের ছবি", "adm:svcphoto"), btn("🗑 সার্ভিস বন্ধ", "adm:svcdel")],
                    back]))
    if section == "channels":
        return (channels_text(),
                InlineKeyboardMarkup([
                    [btn("📢 চ্যানেল ১", "chset:public"), btn("🔒 চ্যানেল ২", "chset:detail")],
                    [btn("🔐 জয়েন চ্যানেল", "adm:join")],
                    [btn("🧪 চ্যানেল পরীক্ষা", "adm:chtest")],
                    back]))
    if section == "settings":
        return (card("সাধারণ সেটিং", "ব্র্যান্ড, সাপোর্ট, অর্ডার নম্বর ও লোডিং অ্যানিমেশন।", icon="⚙️"),
                InlineKeyboardMarkup([
                    [btn("🏷 ব্র্যান্ড নাম", "gen:brand"), btn("👤 সাপোর্ট", "gen:support")],
                    [btn("🔢 অর্ডার নম্বর", "adm:ordset"), btn("🎞 লোডিং GIF", "adm:loadanim")],
                    back]))
    if section == "search":
        return (card("খোঁজ ও প্যানেল", "অর্ডার খুঁজুন বা প্যানেলের ব্যালেন্স দেখুন।", icon="🔎"),
                InlineKeyboardMarkup([
                    [btn("🔎 অর্ডার খুঁজুন", "adm:orderfind"), btn("🏦 প্যানেল ব্যালেন্স", "adm:panelbal")],
                    back]))
    return None


async def admin_show(q, context, text, markup):
    try:
        await q.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    except BadRequest:
        await context.bot.send_message(q.message.chat.id, text, reply_markup=markup)


async def admin_route(q, context, data, chat_id):
    if data == "adm:m:home":
        home = admin_home()
        await admin_show(q, context, home["text"], home["reply_markup"])
        return
    if data.startswith("adm:m:"):
        sec = admin_section(data.split(":", 2)[2])
        if sec:
            await admin_show(q, context, sec[0], sec[1])
        return

    prompts = {
        "adm:addbal": ("adm_adjust", {"mode": "add"},
                       card("ব্যালেন্স যোগ", "ইউজারের <b>ইউজারনেম</b>, <b>আইডি</b> বা <b>নম্বর</b> লিখুন।", icon="➕")),
        "adm:subbal": ("adm_adjust", {"mode": "sub"},
                       card("ব্যালেন্স কাটা", "ইউজারের <b>ইউজারনেম</b> বা <b>আইডি</b> লিখুন।", icon="➖")),
        "adm:ban": ("adm_ban", {}, card("ইউজার ব্যান", "ইউজারনেম, আইডি বা নম্বর লিখুন।", icon="🚫")),
        "adm:unban": ("adm_unban", {}, card("ইউজার আনব্যান", "ইউজারনেম বা আইডি লিখুন।", icon="✅")),
        "adm:userinfo": ("adm_userinfo", {}, card("ইউজার তথ্য", "ইউজারনেম বা আইডি লিখুন।", icon="👤")),
        "adm:orderfind": ("adm_orderfind", {}, card("অর্ডার খুঁজুন", "অর্ডার নম্বর লিখুন (যেমন 54000123)।", icon="🔎")),
    }
    if data in prompts:
        name, d, text = prompts[data]
        context.user_data["flow"] = {"name": name, "step": "ident", "data": d}
        await context.bot.send_message(chat_id, text, reply_markup=cancel_kb())
        return

    if data == "adm:codenew":
        context.user_data["flow"] = {"name": "adm_codenew", "step": "code", "data": {}}
        await context.bot.send_message(chat_id, card("নতুন রিডিম কোড", "কোডের নাম লিখুন (ইংরেজি অক্ষর, সংখ্যা, _)।", icon="🎁"),
                                       reply_markup=cancel_kb())
        return
    if data == "adm:codedel":
        context.user_data["flow"] = {"name": "adm_codedel", "step": "code", "data": {}}
        await context.bot.send_message(chat_id, card("কোড বন্ধ", "যে কোডটি বন্ধ করবেন তার নাম লিখুন।", icon="⛔"),
                                       reply_markup=cancel_kb())
        return
    if data == "adm:codes":
        await send_codes(context, chat_id)
        return
    if data == "adm:users":
        await send_users(context, chat_id)
        return
    if data == "adm:svclist":
        await send_services(context, chat_id)
        return
    if data == "adm:chtest":
        lines = []
        for kind, label in (("public", "চ্যানেল ১"), ("detail", "চ্যানেল ২")):
            target = channel_setting(kind)
            if not target:
                lines.append(f"{label}: সেট করা নেই")
                continue
            try:
                await context.bot.send_message(target_value(target), "✅ চ্যানেল সংযুক্ত। বার্তা পৌঁছাচ্ছে।")
                lines.append(f"{label} (<code>{h(target)}</code>): ✅ বার্তা গেছে")
            except Exception as e:
                lines.append(f"{label} (<code>{h(target)}</code>): ❌ {h(e)}")
        jc = join_channel()
        if jc:
            try:
                m = await context.bot.get_chat_member(target_value(jc), context.bot.id)
                lines.append(f"জয়েন চ্যানেল: {'✅ বট অ্যাডমিন' if m.status in ('administrator', 'creator') else '❌ বট অ্যাডমিন নয়'}")
            except Exception as e:
                lines.append(f"জয়েন চ্যানেল: ❌ {h(e)}")
        await context.bot.send_message(chat_id, card("চ্যানেল পরীক্ষা", "\n".join(lines), icon="🧪"),
                                       reply_markup=admin_home()["reply_markup"])
        return

    if data == "adm:panelbal":
        try:
            resp = await panel({"action": "balance"})
            await context.bot.send_message(
                chat_id,
                card("প্যানেল ব্যালেন্স", f"{h(resp.get('balance', '?'))} {h(resp.get('currency', ''))}", icon="🏦"),
                reply_markup=admin_home()["reply_markup"],
            )
        except httpx.HTTPError as e:
            await context.bot.send_message(chat_id, card("সংযোগ নেই", h(e), icon="❌"),
                                           reply_markup=admin_home()["reply_markup"])
        return

    if data == "adm:newcat":
        context.user_data["flow"] = {"name": "adm_newcat", "step": "name", "data": {}}
        await context.bot.send_message(
            chat_id,
            card("নতুন ক্যাটাগরি",
                 "ক্যাটাগরির নাম লিখুন। উদাহরণ: <code>Telegram</code>, <code>WhatsApp</code>, <code>IMO</code>, "
                 "<code>TikTok</code>।\nনাম লিখলে লোগো নিজে থেকে তৈরি হবে।", icon="🆕"),
            reply_markup=cancel_kb())
        return
    if data == "adm:cats":
        await send_categories(context, chat_id)
        return

    if data.startswith("cat:"):
        await send_category_detail(context, chat_id, data.split(":", 1)[1])
        return
    if data.startswith("catsvc:"):
        name = data.split(":", 1)[1]
        context.user_data["flow"] = {"name": "adm_svcadd", "step": "name", "data": {"cat": name}}
        await context.bot.send_message(
            chat_id,
            card("নতুন সার্ভিস", f"ক্যাটাগরি: <b>{h(name)}</b>\nসার্ভিসের নাম লিখুন (যেমন Post Views বা Members)।", icon="🛍"),
            reply_markup=cancel_kb())
        return
    if data.startswith("catrefill:"):
        _, name, mode = data.split(":", 2)
        if mode == "off":
            set_category_refill(name, 0)
            await context.bot.send_message(chat_id, card("রিফিল বন্ধ", f"<b>{h(name)}</b>-এর রিফিল বন্ধ করা হয়েছে।", icon="⛔"),
                                           reply_markup=admin_home()["reply_markup"])
        else:
            context.user_data["flow"] = {"name": "adm_catrefill", "step": "days", "data": {"cat": name}}
            await context.bot.send_message(chat_id, card("রিফিল সময়সীমা", "কত দিনের জন্য রিফিল চালু থাকবে? (দিন সংখ্যা লিখুন)",
                                                         icon="🔄"), reply_markup=cancel_kb())
        return
    if data.startswith("catlogo:"):
        name = data.split(":", 1)[1]
        context.user_data["flow"] = {"name": "adm_catlogo", "step": "photo", "data": {"cat": name}}
        await context.bot.send_message(chat_id, card("লোগো বদলান", f"<b>{h(name)}</b>-এর নতুন লোগো ছবি পাঠান।", icon="🖼"),
                                       reply_markup=cancel_kb())
        return
    if data.startswith("catdel:"):
        name = data.split(":", 1)[1]
        deactivate_category(name)
        await context.bot.send_message(chat_id, card("ক্যাটাগরি বন্ধ", f"<b>{h(name)}</b> বন্ধ করা হয়েছে।", icon="🗑"),
                                       reply_markup=admin_home()["reply_markup"])
        return
    if data.startswith("newcat_refill:"):
        flow = context.user_data.get("flow")
        if not flow or flow["name"] != "adm_newcat" or flow["step"] != "refill":
            await context.bot.send_message(chat_id, card("আগে নাম দিন", "নতুন ক্যাটাগরি শুরু করতে নাম লিখুন।", icon="⚠️"),
                                           reply_markup=cancel_kb())
            return
        if data.endswith(":off"):
            await finish_newcat(context, chat_id, flow, 0)
        else:
            flow["step"] = "days"
            await context.bot.send_message(chat_id, card("রিফিল সময়সীমা", "কত দিনের জন্য রিফিল দেওয়া হবে? (দিন সংখ্যা লিখুন)",
                                                         icon="🔄"), reply_markup=cancel_kb())
        return

    if data.startswith("svcprice:") or data.startswith("svcdel:") or data.startswith("svcphoto:"):
        action, key = data.split(":", 1)
        srv = get_service(key)
        if not srv:
            await context.bot.send_message(chat_id, card("সার্ভিস নেই", "এটি খুঁজে পাওয়া যায়নি।", icon="⚠️"),
                                           reply_markup=admin_home()["reply_markup"])
            return
        if action == "svcprice":
            context.user_data["flow"] = {"name": "adm_svcprice", "step": "price", "data": {"key": key}}
            await context.bot.send_message(chat_id, card("প্রাইস বদল", f"<b>{h(service_title(srv))}</b>\nবর্তমান: "
                                                         f"{money(srv['price'])} / ১০০০\n\nনতুন মূল্য লিখুন।", icon="💰"),
                                           reply_markup=cancel_kb())
        elif action == "svcphoto":
            context.user_data["flow"] = {"name": "adm_svcphoto", "step": "photo", "data": {"key": key}}
            await context.bot.send_message(chat_id, card("সার্ভিসের ছবি", f"<b>{h(service_title(srv))}</b>-এর জন্য ছবি পাঠান।",
                                                         icon="🖼"), reply_markup=cancel_kb())
        else:
            deactivate_service(key)
            await context.bot.send_message(chat_id, card("সার্ভিস বন্ধ", f"<b>{h(service_title(srv))}</b> বন্ধ করা হয়েছে।",
                                                         icon="🗑"), reply_markup=admin_home()["reply_markup"])
        return

    if data == "adm:svcphoto":
        await send_pick(context, chat_id, "svcphoto", "কোন সার্ভিসের ছবি দিবেন?", "🖼")
        return
    if data == "adm:svcprice":
        await send_pick(context, chat_id, "svcprice", "কোন সার্ভিসের প্রাইস বদলাবেন?", "💰")
        return
    if data == "adm:svcdel":
        await send_pick(context, chat_id, "svcdel", "কোন সার্ভিসটি বন্ধ করবেন?", "🗑")
        return

    if data.startswith("chset:"):
        kind = data.split(":", 1)[1]
        context.user_data["flow"] = {"name": "adm_chset", "step": "value", "data": {"kind": kind}}
        label = "চ্যানেল ১ (সকল ইউজার)" if kind == "public" else "চ্যানেল ২ (শুধু অ্যাডমিন)"
        await context.bot.send_message(chat_id, card(label, "চ্যানেলের <code>@username</code> বা আইডি পাঠান।\n"
                                                     "বটকে ওই চ্যানেলের অ্যাডমিন বানাতে হবে।\nবন্ধ করতে <code>off</code> লিখুন।",
                                                     icon="📣"), reply_markup=cancel_kb())
        return
    if data == "adm:join":
        context.user_data["flow"] = {"name": "adm_join", "step": "value", "data": {}}
        await context.bot.send_message(chat_id, card("বাধ্যতামূলক জয়েন", "যে চ্যানেলে জয়েন বাধ্যতামূলক, তার "
                                                     "<code>@username</code> পাঠান।\nবন্ধ করতে <code>off</code> লিখুন।",
                                                     icon="🔐"), reply_markup=cancel_kb())
        return
    if data == "adm:ordset":
        await context.bot.send_message(chat_id, order_settings_text(), reply_markup=InlineKeyboardMarkup([
            [btn("✏️ প্রিফিক্স বদলাও", "adm:ordprefix"), btn("🔁 মোড বদলাও", "adm:ordmode")],
            [btn("‹ ফিরে যান", "adm:m:settings")]]))
        return
    if data == "adm:ordprefix":
        context.user_data["flow"] = {"name": "adm_ordset", "step": "prefixes", "data": {}}
        await context.bot.send_message(chat_id, card("প্রিফিক্স", "নতুন প্রিফিক্স লিখুন, কমা দিয়ে। উদাহরণ: <code>54,18</code>",
                                                     icon="✏️"), reply_markup=cancel_kb())
        return
    if data == "adm:ordmode":
        set_setting("order_no_mode", "panel" if order_mode() == "bot" else "bot")
        await context.bot.send_message(chat_id, order_settings_text(), reply_markup=admin_home()["reply_markup"])
        return
    if data == "adm:loadanim":
        context.user_data["flow"] = {"name": "adm_loadanim", "step": "media", "data": {}}
        await context.bot.send_message(chat_id, card("লোডিং GIF", "একটি GIF বা ছোট ভিডিও পাঠান। প্রতিটি বাটনে ক্লিকে এটি "
                                                     "দেখাবে।\nবন্ধ করতে <code>off</code> লিখুন।", icon="🎞"),
                                       reply_markup=cancel_kb())
        return
    if data.startswith("gen:"):
        kind = data.split(":", 1)[1]
        context.user_data["flow"] = {"name": "adm_general", "step": "value", "data": {"kind": kind}}
        prompts_gen = {
            "brand": f"🏷 নতুন ব্র্যান্ড নাম লিখুন (১-৪০ অক্ষর)। বর্তমান: <b>{h(brand()) or 'নেই'}</b>",
            "support": "👤 সাপোর্ট ইউজারনেম লিখুন (@ সহ বা ছাড়া)। বন্ধ করতে <code>off</code> লিখুন।",
        }
        await context.bot.send_message(chat_id, card("সাধারণ সেটিং", prompts_gen[kind], icon="⚙️"), reply_markup=cancel_kb())
        return

    if data == "adm:close":
        context.user_data.pop("flow", None)
        await context.bot.send_message(chat_id, card("প্যানেল বন্ধ", "আবার খুলতে /admin লিখুন।", icon="🔒"))
        return

    if data in ("ban_perm_yes", "ban_perm_no"):
        await ban_choice_route(q, context, data, chat_id)
        return


async def send_pick(context, chat_id, action, title, icon):
    srvs = list_services(active_only=True)
    if not srvs:
        await context.bot.send_message(chat_id, card("সার্ভিস নেই", "প্রথমে একটি ক্যাটাগরি ও সার্ভিস যোগ করুন।", icon="⚠️"),
                                       reply_markup=admin_home()["reply_markup"])
        return
    kb = [[btn(f"{icon} {service_title(s)} · {money(s['price'])}", f"{action}:{s['key']}")] for s in srvs]
    kb.append([btn("✖ বাতিল", "flowcancel")])
    await context.bot.send_message(chat_id, card(title, "একটি সার্ভিস বেছে নিন।", icon=icon),
                                   reply_markup=InlineKeyboardMarkup(kb))


async def send_categories(context, chat_id):
    cats = list_categories()
    if not cats:
        await context.bot.send_message(chat_id, card("ক্যাটাগরি নেই", "‘নতুন ক্যাটাগরি’ দিয়ে শুরু করুন।", icon="🗂"),
                                       reply_markup=admin_home()["reply_markup"])
        return
    kb = []
    for c in cats:
        kb.append([btn(f"{cat_emoji(c['name'])} {c['name']}", f"cat:{c['name']}")])
    kb.append([btn("‹ ফিরে যান", "adm:m:home")])
    await context.bot.send_message(chat_id, card("ক্যাটাগরি তালিকা", "একটি ক্যাটাগরি বেছে নিন।", icon="🗂"),
                                   reply_markup=InlineKeyboardMarkup(kb))


async def send_category_detail(context, chat_id, name):
    cat = get_category(name)
    if not cat:
        await context.bot.send_message(chat_id, card("ক্যাটাগরি নেই", "এটি খুঁজে পাওয়া যায়নি।", icon="⚠️"))
        return
    refill = f"{cat['refill_days']} দিন" if cat["refill_days"] > 0 else "বন্ধ"
    count = len(list_services(active_only=False, platform=name))
    body = (f"📂 নাম: <b>{h(name)}</b>\n🖼 লোগো: {'আছে' if category_logo(name) else 'নেই'}\n"
            f"🔄 রিফিল: {refill}\n🛍 সার্ভিস সংখ্যা: {count}")
    kb = InlineKeyboardMarkup([
        [btn("➕ এখানে সার্ভিস যোগ", f"catsvc:{name}")],
        [btn("🔄 রিফিল চালু", f"catrefill:{name}:on"), btn("⛔ রিফিল বন্ধ", f"catrefill:{name}:off")],
        [btn("🖼 লোগো বদলান", f"catlogo:{name}"), btn("🗑 ক্যাটাগরি বন্ধ", f"catdel:{name}")],
        [btn("‹ ক্যাটাগরি তালিকা", "adm:cats")],
    ])
    await context.bot.send_message(chat_id, card("ক্যাটাগরির বিবরণ", body, icon=cat_emoji(name)), reply_markup=kb)


async def finish_newcat(context, chat_id, flow, days):
    name = flow["data"]["name"]
    context.user_data.pop("flow", None)
    created = add_category(name, days)
    logo_note = ""
    try:
        buf = make_logo_png(name)
        sent = await context.bot.send_photo(ADMIN_ID, buf, caption=f"🖼 {name} · স্বয়ংক্রিয় লোগো")
        set_setting(logo_key(name), sent.photo[-1].file_id)
        logo_note = "🖼 লোগো স্বয়ংক্রিয়ভাবে তৈরি হয়েছে।"
    except Exception as e:
        log.error("logo generation failed: %s", e)
        logo_note = "⚠️ লোগো তৈরি হয়নি, পরে লোগো বদলাতে পারবেন।"
    refill = f"{days} দিন" if days > 0 else "বন্ধ"
    body = (f"📂 ক্যাটাগরি: <b>{h(name)}</b>\n🔄 রিফিল: {refill}\n{logo_note}")
    head = "ক্যাটাগরি যোগ হয়েছে" if created else "ক্যাটাগরি আপডেট হয়েছে"
    kb = InlineKeyboardMarkup([
        [btn("➕ এখানে সার্ভিস যোগ", f"catsvc:{name}")],
        [btn("‹ প্যানেলে ফিরে যান", "adm:m:home")],
    ])
    await context.bot.send_message(chat_id, card(head, body, icon="✅"), reply_markup=kb)


async def send_users(context, chat_id):
    rows = all_users()
    if not rows:
        await context.bot.send_message(chat_id, card("ইউজার নেই", "এখনো কোনো ইউজার বট চালু করেনি।", icon="👥"),
                                       reply_markup=admin_home()["reply_markup"])
        return
    lines = [
        f"<code>{r['id']}</code> · {h(full_name(r))} · @{h(r['username'] or '-')} · {money(r['balance'])} · "
        f"{'🚫' if r['is_banned'] else '✅'}"
        for r in rows
    ]
    await context.bot.send_message(chat_id, card("ইউজার তালিকা", f"মোট: <b>{len(rows)}</b> জন", icon="👥"))
    for i in range(0, len(lines), 25):
        await context.bot.send_message(chat_id, "\n".join(lines[i:i + 25]))


async def send_codes(context, chat_id):
    rows = list_codes()
    if not rows:
        await context.bot.send_message(chat_id, card("কোড নেই", "এখনো কোনো রিডিম কোড তৈরি হয়নি।", icon="🎁"),
                                       reply_markup=admin_home()["reply_markup"])
        return
    lines = []
    for r in rows:
        limit = "∞" if r["max_uses"] == 0 else str(r["max_uses"])
        ok = r["active"] and (r["max_uses"] == 0 or r["used_count"] < r["max_uses"])
        lines.append(f"{'🟢' if ok else '🔴'} <code>{h(r['code'])}</code> · {money(r['amount'])} · "
                     f"ব্যবহার {r['used_count']}/{limit}")
    await context.bot.send_message(chat_id, card("রিডিম কোড", "\n".join(lines), icon="🎁"),
                                   reply_markup=admin_home()["reply_markup"])


async def send_services(context, chat_id):
    rows = list_services(active_only=False)
    if not rows:
        await context.bot.send_message(chat_id, card("সার্ভিস নেই", "প্রথমে একটি ক্যাটাগরি যোগ করুন।", icon="🛍"),
                                       reply_markup=admin_home()["reply_markup"])
        return
    lines = []
    for r in rows:
        mx = r["max_qty"] or "সীমাহীন"
        lines.append(
            f"{r['emoji']} <b>{h(service_title(r))}</b> {'' if r['active'] else '(বন্ধ)'}\n"
            f"   প্যানেল আইডি: <code>{r['panel_id']}</code> · {money(r['price'])} / ১০০০\n"
            f"   সীমা: {r['min_qty']} – {mx}"
        )
    await context.bot.send_message(chat_id, card("সার্ভিস তালিকা", "\n".join(lines) +
                                                 "\n\n<i>প্যানেল আইডি শুধু অ্যাডমিন দেখেন।</i>", icon="📋"),
                                   reply_markup=admin_home()["reply_markup"])


async def ban_choice_route(q, context, data, chat_id):
    flow = context.user_data.get("flow")
    if not flow or flow["name"] != "adm_ban" or flow["step"] != "choice":
        await context.bot.send_message(chat_id, card("আগে ইউজার নির্বাচন", "ইউজারনেম বা আইডি লিখুন।", icon="⚠️"),
                                       reply_markup=admin_home()["reply_markup"])
        return
    uid = flow["data"]["uid"]
    if data == "ban_perm_yes":
        set_ban(uid, None, 1)
        context.user_data.pop("flow", None)
        await context.bot.send_message(chat_id, card("স্থায়ী ব্যান", f"<code>{uid}</code> স্থায়ীভাবে ব্যান হয়েছে।", icon="🚫"),
                                       reply_markup=admin_home()["reply_markup"])
        await notify_user(context, uid, "⛔ আপনার অ্যাকাউন্ট স্থায়ীভাবে সীমিত করা হয়েছে।")
    else:
        flow["step"] = "days"
        await context.bot.send_message(chat_id, card("নির্দিষ্ট দিন", "কত দিনের জন্য ব্যান করবেন? (দিন সংখ্যা লিখুন)",
                                                     icon="⏳"), reply_markup=cancel_kb())


# ============================================================
# Text flows
# ============================================================
async def flow_redeem(update, context, flow):
    code = update.message.text.strip().upper()
    context.user_data.pop("flow", None)
    await do_redeem(context, update.message.chat.id, update.effective_user.id, code)


async def flow_order(update, context, flow):
    text = update.message.text.strip()
    chat_id = update.message.chat.id
    if flow["step"] == "link":
        if "t.me/" not in text.lower():
            await update.message.reply_text("⚠️ সঠিক টেলিগ্রাম লিংক দিন, যেমন: https://t.me/...")
            return
        flow["data"]["link"] = text
        flow["step"] = "qty"
        srv = get_service(flow["data"]["key"])
        await send_qty_prompt(context, chat_id, srv)
    elif flow["step"] == "qty":
        if not text.isdigit():
            await update.message.reply_text("⚠️ শুধু সংখ্যা লিখুন।")
            return
        await set_qty_summary(context, chat_id, flow, int(text), update.effective_user.id)
    else:
        await update.message.reply_text("▸ নিচের বাটন থেকে কনফার্ম বা বাতিল করুন।")


async def flow_adm_adjust(update, context, flow):
    text = update.message.text.strip()
    if flow["step"] == "ident":
        row = find_user(text)
        if not row:
            await update.message.reply_text(card("ইউজার নেই", "এই পরিচয়ে ইউজার পাওয়া যায়নি। আবার লিখুন।", icon="⚠️"),
                                            reply_markup=cancel_kb())
            return
        flow["data"]["uid"] = row["id"]
        flow["step"] = "amount"
        verb = "যোগ" if flow["data"]["mode"] == "add" else "কাটা"
        await update.message.reply_text(
            card("পরিমাণ", f"ইউজার: <b>{h(full_name(row))}</b>\nবর্তমান ব্যালেন্স: {money(row['balance'])}\n\n"
                 f"কত টাকা {verb} করবেন? (সংখ্যা লিখুন)", icon="💰"),
            reply_markup=cancel_kb())
        return
    try:
        amt = float(text)
        assert amt > 0
    except (ValueError, AssertionError):
        await update.message.reply_text("⚠️ সঠিক পরিমাণ লিখুন (০-এর বেশি সংখ্যা)।", reply_markup=cancel_kb())
        return
    uid = flow["data"]["uid"]
    context.user_data.pop("flow", None)
    if flow["data"]["mode"] == "add":
        adjust_balance(uid, amt, "deposit", "অ্যাডমিন যোগ")
        head, notice = f"{money(amt)} যোগ হয়েছে", f"➕ আপনার অ্যাকাউন্টে {money(amt)} যুক্ত হয়েছে।"
    else:
        adjust_balance(uid, -amt, "withdraw", "অ্যাডমিন কাটা")
        head, notice = f"{money(amt)} কাটা হয়েছে", f"➖ আপনার অ্যাকাউন্ট থেকে {money(amt)} কাটা হয়েছে।"
    bal = get_user(uid)["balance"]
    await update.message.reply_text(card("সফল", f"<code>{uid}</code> · {head}\nনতুন ব্যালেন্স: <b>{money(bal)}</b>", icon="✅"),
                                    reply_markup=admin_home()["reply_markup"])
    await notify_user(context, uid, f"{notice}\nবর্তমান ব্যালেন্স: {money(bal)}")


async def flow_adm_ban(update, context, flow):
    text = update.message.text.strip()
    if flow["step"] == "ident":
        row = find_user(text)
        if not row:
            await update.message.reply_text(card("ইউজার নেই", "এই পরিচয়ে ইউজার পাওয়া যায়নি।", icon="⚠️"),
                                            reply_markup=cancel_kb())
            return
        flow["data"]["uid"] = row["id"]
        flow["step"] = "choice"
        await update.message.reply_text(
            card("ব্যান ধরন", f"ইউজার: <b>{h(full_name(row))}</b> (<code>{row['id']}</code>)\n\nস্থায়ী ব্যান হবে?", icon="🚫"),
            reply_markup=InlineKeyboardMarkup([
                [btn("♾ স্থায়ী", "ban_perm_yes"), btn("⏳ নির্দিষ্ট দিন", "ban_perm_no")],
                [btn("✖ বাতিল", "flowcancel")],
            ]),
        )
    elif flow["step"] == "choice":
        await update.message.reply_text("▸ নিচের বাটন থেকে বেছে নিন।")
    elif flow["step"] == "days":
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("⚠️ সঠিক দিন সংখ্যা লিখুন।", reply_markup=cancel_kb())
            return
        days = int(text)
        uid = flow["data"]["uid"]
        until = now_dt() + timedelta(days=days)
        set_ban(uid, until.isoformat(), 1)
        context.user_data.pop("flow", None)
        await update.message.reply_text(
            card("সাময়িক ব্যান", f"<code>{uid}</code> · {days} দিন\nশেষ: {until.strftime('%d %b %Y, %I:%M %p')}", icon="⏳"),
            reply_markup=admin_home()["reply_markup"])
        await notify_user(context, uid, f"⛔ আপনার অ্যাকাউন্ট {days} দিনের জন্য সীমিত করা হয়েছে।")


async def flow_adm_unban(update, context, flow):
    row = find_user(update.message.text)
    if not row:
        await update.message.reply_text(card("ইউজার নেই", "এই পরিচয়ে ইউজার পাওয়া যায়নি।", icon="⚠️"),
                                        reply_markup=cancel_kb())
        return
    set_ban(row["id"], None, 0)
    context.user_data.pop("flow", None)
    await update.message.reply_text(card("আনব্যান", f"<b>{h(full_name(row))}</b> আনব্যান হয়েছে।", icon="✅"),
                                    reply_markup=admin_home()["reply_markup"])
    await notify_user(context, row["id"], "✅ আপনার অ্যাকাউন্টের সীমাবদ্ধতা তুলে নেওয়া হয়েছে।")


async def flow_adm_userinfo(update, context, flow):
    row = find_user(update.message.text)
    if not row:
        await update.message.reply_text(card("ইউজার নেই", "এই পরিচয়ে ইউজার পাওয়া যায়নি।", icon="⚠️"),
                                        reply_markup=cancel_kb())
        return
    context.user_data.pop("flow", None)
    await update.message.reply_text(user_info_text(row), reply_markup=admin_home()["reply_markup"])


async def flow_adm_orderfind(update, context, flow):
    row = find_order(update.message.text)
    if not row:
        await update.message.reply_text(card("অর্ডার নেই", "এই নম্বরে অর্ডার পাওয়া যায়নি।", icon="⚠️"),
                                        reply_markup=cancel_kb())
        return
    context.user_data.pop("flow", None)
    await update.message.reply_text(order_info_text(row), reply_markup=admin_home()["reply_markup"])


async def flow_adm_codenew(update, context, flow):
    text = update.message.text.strip()
    if flow["step"] == "code":
        code = text.upper()
        if not re.fullmatch(r"[A-Z0-9_]{3,32}", code):
            await update.message.reply_text("⚠️ কোড হতে হবে ৩-৩২ অক্ষরের (A-Z, 0-9, _)।", reply_markup=cancel_kb())
            return
        flow["data"]["code"] = code
        flow["step"] = "amount"
        await update.message.reply_text("💵 কোডের মূল্য কত? (সংখ্যা লিখুন)", reply_markup=cancel_kb())
    elif flow["step"] == "amount":
        try:
            amt = float(text)
            assert amt > 0
        except (ValueError, AssertionError):
            await update.message.reply_text("⚠️ সঠিক মূল্য লিখুন।", reply_markup=cancel_kb())
            return
        flow["data"]["amount"] = amt
        flow["step"] = "uses"
        await update.message.reply_text("👥 কতজন ইউজার নিতে পারবে? (সীমাহীনের জন্য 0)", reply_markup=cancel_kb())
    elif flow["step"] == "uses":
        if not text.isdigit():
            await update.message.reply_text("⚠️ শুধু সংখ্যা লিখুন (সীমাহীনের জন্য 0)।", reply_markup=cancel_kb())
            return
        d = flow["data"]
        context.user_data.pop("flow", None)
        if not create_code(d["code"], d["amount"], int(text)):
            await update.message.reply_text(card("আগে থেকেই আছে", "এই কোডটি ইতিমধ্যে তৈরি হয়েছে।", icon="⚠️"),
                                            reply_markup=admin_home()["reply_markup"])
            return
        limit = "সীমাহীন" if int(text) == 0 else text
        body = (f"🎁 কোড: <code>{h(d['code'])}</code>\n💵 মূল্য: {money(d['amount'])}\n"
                f"👥 ব্যবহার সীমা: {limit}\n\nপ্রতি ইউজার একবারই ব্যবহার করতে পারবে।")
        await update.message.reply_text(card("কোড তৈরি হয়েছে", body, icon="✅"),
                                        reply_markup=InlineKeyboardMarkup([[copy_btn("📋 কোড কপি", d["code"])]]
                                                                          + admin_home()["reply_markup"].inline_keyboard))


async def flow_adm_codedel(update, context, flow):
    code = update.message.text.strip().upper()
    context.user_data.pop("flow", None)
    if deactivate_code(code):
        await update.message.reply_text(card("কোড বন্ধ", f"<code>{h(code)}</code> বন্ধ করা হয়েছে।", icon="⛔"),
                                        reply_markup=admin_home()["reply_markup"])
    else:
        await update.message.reply_text(card("কোড নেই", "এই নামে কোড পাওয়া যায়নি।", icon="⚠️"),
                                        reply_markup=admin_home()["reply_markup"])


async def flow_adm_newcat(update, context, flow):
    text = update.message.text.strip()
    if flow["step"] == "name":
        name = text[:40]
        if not name or not re.fullmatch(r"[\w .+-]{1,40}", name):
            await update.message.reply_text("⚠️ ক্যাটাগরির নাম ইংরেজি বা বাংলা অক্ষর ও সংখ্যায় লিখুন।",
                                            reply_markup=cancel_kb())
            return
        flow["data"]["name"] = name
        flow["step"] = "refill"
        await update.message.reply_text(
            card("রিফিল সুবিধা", f"<b>{h(name)}</b> ক্যাটাগরিতে রিফিল সুবিধা চালু করবেন?", icon="🔄"),
            reply_markup=InlineKeyboardMarkup([
                [btn("🟢 অন", "newcat_refill:on"), btn("⛔ অফ", "newcat_refill:off")],
                [btn("✖ বাতিল", "flowcancel")],
            ]))
    elif flow["step"] == "refill":
        await update.message.reply_text("▸ নিচের বাটন থেকে অন বা অফ বেছে নিন।")
    elif flow["step"] == "days":
        if not text.isdigit() or int(text) <= 0 or int(text) > 365:
            await update.message.reply_text("⚠️ ১ থেকে ৩৬৫ এর মধ্যে দিন সংখ্যা লিখুন।", reply_markup=cancel_kb())
            return
        await finish_newcat(context, update.message.chat.id, flow, int(text))


async def flow_adm_catrefill(update, context, flow):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0 or int(text) > 365:
        await update.message.reply_text("⚠️ ১ থেকে ৩৬৫ এর মধ্যে দিন সংখ্যা লিখুন।", reply_markup=cancel_kb())
        return
    name = flow["data"]["cat"]
    set_category_refill(name, int(text))
    context.user_data.pop("flow", None)
    await update.message.reply_text(card("রিফিল চালু", f"<b>{h(name)}</b> · {text} দিন", icon="🔄"),
                                    reply_markup=admin_home()["reply_markup"])


async def ask_svc_name(message):
    await message.reply_text(card("সার্ভিসের নাম", "সার্ভিসের নাম লিখুন (যেমন Post Views বা Members)।", icon="🛍"),
                             reply_markup=cancel_kb())


async def finish_svcadd(message, context, d, photo_id):
    context.user_data.pop("flow", None)
    key = add_service(d["name"], d["cat"], d["panel_id"], d["price"], d["min_qty"], d["max_qty"], photo_id)
    body = (f"📂 ক্যাটাগরি: <b>{h(d['cat'])}</b>\n🏷 নাম: {h(d['name'])}\n"
            f"🔢 প্যানেল আইডি: <code>{d['panel_id']}</code> (ইউজার দেখবে না)\n"
            f"💰 মূল্য: {money(d['price'])} / ১০০০\n📏 সীমা: {d['min_qty']} – {d['max_qty'] or 'সীমাহীন'}\n"
            f"🆔 কী: <code>{key}</code>")
    await message.reply_text(card("সার্ভিস যোগ হয়েছে", body, icon="✅"), reply_markup=admin_home()["reply_markup"])


async def flow_adm_svcadd(update, context, flow):
    text = update.message.text.strip()
    d = flow["data"]
    step = flow["step"]
    if step == "name":
        if not text:
            await update.message.reply_text("⚠️ নাম লিখুন।", reply_markup=cancel_kb())
            return
        d["name"] = text
        flow["step"] = "panel"
        await update.message.reply_text(card("প্যানেল আইডি", "ওয়েবসাইটের সার্ভিস আইডি লিখুন (যেমন 14759)।", icon="🔢"),
                                        reply_markup=cancel_kb())
    elif step == "panel":
        if not text.isdigit():
            await update.message.reply_text("⚠️ শুধু সংখ্যা আইডি লিখুন।", reply_markup=cancel_kb())
            return
        d["panel_id"] = int(text)
        flow["step"] = "price"
        await update.message.reply_text(card("মূল্য", "প্রতি ১০০০-এর বিক্রয় মূল্য লিখুন (যেমন 0.998)।", icon="💰"),
                                        reply_markup=cancel_kb())
    elif step == "price":
        try:
            d["price"] = float(text)
            assert d["price"] > 0
        except (ValueError, AssertionError):
            await update.message.reply_text("⚠️ সঠিক মূল্য লিখুন।", reply_markup=cancel_kb())
            return
        flow["step"] = "min"
        await update.message.reply_text(card("সর্বনিম্ন", "সর্বনিম্ন পরিমাণ লিখুন (সাধারণত 100)।", icon="📏"),
                                        reply_markup=cancel_kb())
    elif step == "min":
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("⚠️ সঠিক সংখ্যা লিখুন।", reply_markup=cancel_kb())
            return
        d["min_qty"] = int(text)
        flow["step"] = "max"
        await update.message.reply_text(card("সর্বোচ্চ", "সর্বোচ্চ পরিমাণ লিখুন (সীমাহীনের জন্য 0)।", icon="📐"),
                                        reply_markup=cancel_kb())
    elif step == "max":
        if not text.isdigit():
            await update.message.reply_text("⚠️ শুধু সংখ্যা লিখুন (সীমাহীনের জন্য 0)।", reply_markup=cancel_kb())
            return
        d["max_qty"] = int(text)
        flow["step"] = "photo"
        await update.message.reply_text(card("ছবি (ঐচ্ছিক)", "সার্ভিসের নিজস্ব ছবি পাঠান। না চাইলে <code>skip</code> লিখুন।",
                                             icon="🖼"), reply_markup=cancel_kb())
    elif step == "photo":
        if text.lower() == "skip":
            await finish_svcadd(update.message, context, d, None)
        else:
            await update.message.reply_text("⚠️ ছবি পাঠান, অথবা <code>skip</code> লিখুন।", reply_markup=cancel_kb())


async def flow_adm_svcprice(update, context, flow):
    try:
        price = float(update.message.text.strip())
        assert price > 0
    except (ValueError, AssertionError):
        await update.message.reply_text("⚠️ সঠিক মূল্য লিখুন।", reply_markup=cancel_kb())
        return
    key = flow["data"]["key"]
    set_service_price(key, price)
    context.user_data.pop("flow", None)
    srv = get_service(key)
    await update.message.reply_text(card("প্রাইস আপডেট", f"<b>{h(service_title(srv))}</b>\nনতুন মূল্য: "
                                         f"{money(price)} / ১০০০", icon="✅"), reply_markup=admin_home()["reply_markup"])


async def flow_adm_loadanim(update, context, flow):
    if update.message.text.strip().lower() == "off":
        set_setting("loading_anim", None)
        set_setting("loading_kind", None)
        context.user_data.pop("flow", None)
        await update.message.reply_text(card("GIF বন্ধ", "এখন থেকে স্পিনার অ্যানিমেশন দেখাবে।", icon="🎞"),
                                        reply_markup=admin_home()["reply_markup"])
    else:
        await update.message.reply_text("⚠️ একটি GIF বা ভিডিও পাঠান, অথবা <code>off</code> লিখুন।",
                                        reply_markup=cancel_kb())


async def flow_adm_chset(update, context, flow):
    text = update.message.text.strip()
    kind = flow["data"]["kind"]
    if text.lower() == "off":
        set_setting(f"channel_{kind}", "off")
        context.user_data.pop("flow", None)
        await update.message.reply_text(card("চ্যানেল বন্ধ", "চ্যানেলটি বন্ধ করা হয়েছে।", icon="📣"),
                                        reply_markup=admin_home()["reply_markup"])
        return
    result = await _validate_bot_admin(update, context, normalize_channel_input(text))
    if not result:
        return
    chat, value = result
    set_setting(f"channel_{kind}", value)
    context.user_data.pop("flow", None)
    await update.message.reply_text(card("চ্যানেল সেট হয়েছে", f"<code>{h(value)}</code>", icon="✅"),
                                    reply_markup=admin_home()["reply_markup"])


async def _validate_bot_admin(update, context, target):
    try:
        chat = await context.bot.get_chat(target_value(target))
    except Exception:
        await update.message.reply_text(card("চ্যানেল পাওয়া যায়নি",
                                             "@username বা আইডি সঠিক কিনা দেখুন, এবং বটকে চ্যানেলে যোগ করুন।",
                                             icon="❌"), reply_markup=cancel_kb())
        return None
    try:
        member = await context.bot.get_chat_member(chat.id, context.bot.id)
        is_admin_there = member.status in ("administrator", "creator")
    except Exception:
        is_admin_there = False
    if not is_admin_there:
        await update.message.reply_text(card("বট অ্যাডমিন নয়",
                                             "বটকে এই চ্যানেলের অ্যাডমিন বানান (মেসেজ পাঠানোর অনুমতিসহ)।",
                                             icon="⚠️"), reply_markup=cancel_kb())
        return None
    value = f"@{chat.username}" if chat.username else str(chat.id)
    return chat, value


async def flow_adm_join(update, context, flow):
    text = update.message.text.strip()
    if text.lower() == "off":
        set_setting("join_channel", "off")
        set_setting("join_link", "")
        _member_cache.clear()
        context.user_data.pop("flow", None)
        await update.message.reply_text(card("জয়েন বন্ধ", "বাধ্যতামূলক জয়েন বন্ধ করা হয়েছে।", icon="🔓"),
                                        reply_markup=admin_home()["reply_markup"])
        return
    result = await _validate_bot_admin(update, context, normalize_channel_input(text))
    if not result:
        return
    chat, value = result
    if chat.username:
        link = f"https://t.me/{chat.username}"
    else:
        try:
            link = await context.bot.export_chat_invite_link(chat.id)
        except Exception:
            await update.message.reply_text(card("লিংক তৈরি হয়নি",
                                                 "বটকে ‘Invite users’ অনুমতি দিন, অথবা চ্যানেলটি public করে @username দিন।",
                                                 icon="❌"), reply_markup=cancel_kb())
            return
    set_setting("join_channel", value)
    set_setting("join_link", link)
    _member_cache.clear()
    context.user_data.pop("flow", None)
    await update.message.reply_text(card("জয়েন চ্যানেল সেট", f"<code>{h(value)}</code>\n"
                                         "এখন থেকে জয়েন ছাড়া কেউ বট ব্যবহার করতে পারবে না।", icon="🔐"),
                                    reply_markup=admin_home()["reply_markup"])


async def flow_adm_ordset(update, context, flow):
    parts = [p.strip() for p in update.message.text.split(",") if p.strip()]
    if not parts or any((not p.isdigit()) or len(p) > 3 for p in parts):
        await update.message.reply_text("⚠️ প্রিফিক্স হতে হবে সংখ্যা, কমা দিয়ে আলাদা। উদাহরণ: <code>54,18</code>",
                                        reply_markup=cancel_kb())
        return
    set_setting("order_prefixes", ",".join(parts))
    context.user_data.pop("flow", None)
    await update.message.reply_text(order_settings_text(), reply_markup=admin_home()["reply_markup"])


async def flow_adm_general(update, context, flow):
    text = update.message.text.strip()
    kind = flow["data"]["kind"]
    if kind == "brand":
        if not text or len(text) > 40:
            await update.message.reply_text("⚠️ ব্র্যান্ড নাম ১-৪০ অক্ষরের মধ্যে লিখুন।", reply_markup=cancel_kb())
            return
        set_setting("brand_name", text)
        shown = f"<b>{h(text)}</b>"
    else:
        if text.lower() == "off":
            set_setting("support_username", "")
            shown = "বন্ধ"
        else:
            uname = text.lstrip("@").strip()
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", uname):
                await update.message.reply_text("⚠️ সঠিক ইউজারনেম লিখুন (৫-৩২ অক্ষর, অক্ষর দিয়ে শুরু)।",
                                                reply_markup=cancel_kb())
                return
            set_setting("support_username", uname)
            shown = f"@{h(uname)}"
    context.user_data.pop("flow", None)
    await update.message.reply_text(card("সেটিং আপডেট", shown, icon="✅"), reply_markup=admin_home()["reply_markup"])


FLOW_HANDLERS = {
    "redeem": flow_redeem,
    "order": flow_order,
    "adm_adjust": flow_adm_adjust,
    "adm_ban": flow_adm_ban,
    "adm_unban": flow_adm_unban,
    "adm_userinfo": flow_adm_userinfo,
    "adm_orderfind": flow_adm_orderfind,
    "adm_codenew": flow_adm_codenew,
    "adm_codedel": flow_adm_codedel,
    "adm_newcat": flow_adm_newcat,
    "adm_catrefill": flow_adm_catrefill,
    "adm_svcadd": flow_adm_svcadd,
    "adm_svcprice": flow_adm_svcprice,
    "adm_loadanim": flow_adm_loadanim,
    "adm_chset": flow_adm_chset,
    "adm_join": flow_adm_join,
    "adm_ordset": flow_adm_ordset,
    "adm_general": flow_adm_general,
}


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flow = context.user_data.get("flow")
    if not flow:
        return
    if flow["name"].startswith("adm_") and not is_admin(update):
        context.user_data.pop("flow", None)
        return
    handler = FLOW_HANDLERS.get(flow["name"])
    if not handler:
        context.user_data.pop("flow", None)
        return
    await handler(update, context, flow)


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    flow = context.user_data.get("flow")
    if not flow:
        return
    msg = update.message
    name = flow["name"]

    if name == "adm_loadanim" and (msg.animation or msg.video):
        if msg.animation:
            set_setting("loading_anim", msg.animation.file_id)
            set_setting("loading_kind", "animation")
        else:
            set_setting("loading_anim", msg.video.file_id)
            set_setting("loading_kind", "video")
        context.user_data.pop("flow", None)
        await msg.reply_text(card("GIF সেট হয়েছে", "এখন থেকে প্রতিটি বাটনে ক্লিকে এটি দেখাবে।", icon="🎞"),
                             reply_markup=admin_home()["reply_markup"])

    elif name == "adm_svcphoto" and msg.photo:
        set_service_photo(flow["data"]["key"], msg.photo[-1].file_id)
        context.user_data.pop("flow", None)
        srv = get_service(flow["data"]["key"])
        await msg.reply_text(card("ছবি সেট হয়েছে", f"<b>{h(service_title(srv))}</b>", icon="🖼"),
                             reply_markup=admin_home()["reply_markup"])

    elif name == "adm_catlogo" and msg.photo:
        set_setting(logo_key(flow["data"]["cat"]), msg.photo[-1].file_id)
        context.user_data.pop("flow", None)
        await msg.reply_text(card("লোগো বদলানো হয়েছে", f"<b>{h(flow['data']['cat'])}</b>", icon="🖼"),
                             reply_markup=admin_home()["reply_markup"])

    elif name == "adm_svcadd" and flow["step"] == "photo" and msg.photo:
        await finish_svcadd(msg, context, flow["data"], msg.photo[-1].file_id)

    else:
        await msg.reply_text(card("ফাইল মিলছে না", "এই ধাপে এই ধরনের ফাইল প্রয়োজন নেই।", icon="⚠️"),
                             reply_markup=cancel_kb())


@admin_only
async def cmd_users_regex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_users(context, update.message.chat.id)


# ============================================================
# Startup
# ============================================================
PUBLIC_COMMANDS = [BotCommand("start", "মেনু খুলুন")]
ADMIN_COMMANDS = [
    BotCommand("start", "মেনু খুলুন"),
    BotCommand("admin", "অ্যাডমিন প্যানেল"),
]


async def post_init(app: Application):
    await app.bot.set_my_commands(PUBLIC_COMMANDS)
    await app.bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=ADMIN_ID))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error", exc_info=context.error)


def main():
    init_db()
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .defaults(Defaults(parse_mode=ParseMode.HTML))
        .post_init(post_init)
        .build()
    )

    app.add_handler(TypeHandler(Update, gate), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^[#/]user\b"), cmd_users_regex))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.ANIMATION | filters.VIDEO, on_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    app.run_polling()


if __name__ == "__main__":
    main()

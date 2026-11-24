import asyncio
import html
import logging
import os
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import wraps

import httpx
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
log = logging.getLogger("sp-bot")

# ============================================================
# Render-এ শুধু এই চারটি Environment Variable লাগে।
# বাকি সব সেটিং অ্যাডমিন প্যানেল থেকে বদলানো যায়।
# ============================================================
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
PANEL_API_URL = os.environ["PANEL_API_URL"]
PANEL_API_KEY = os.environ["PANEL_API_KEY"]

# Render Persistent Disk /var/data-তে মাউন্ট করা থাকলে ডেটা স্থায়ী থাকবে
DB_PATH = "/var/data/bot.db" if os.path.isdir("/var/data") else "bot.db"

CURRENCY = "৳"
TZ = timezone(timedelta(hours=6))  # বাংলাদেশ সময়
MIN_QTY = 100
QTY_PRESETS = [100, 400, 1000, 5000]
DEFAULT_PREFIXES = "54,18"
DEFAULT_BRAND = "SP Tech Zone"

PLATFORM_EMOJI = {
    "telegram": "✈️",
    "facebook": "📘",
    "instagram": "📸",
    "youtube": "▶️",
    "tiktok": "🎵",
    "whatsapp": "💬",
    "twitter": "🐦",
    "x": "🐦",
    "website": "🌐",
}


def platform_emoji(platform):
    return PLATFORM_EMOJI.get((platform or "").lower().strip(), "🛍")


def logo_key(platform):
    return f"logo:{(platform or '').lower().strip()}"


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
    return datetime.fromisoformat(s) if s else None


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
            display_no TEXT
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
        # পুরনো ডাটাবেসে নতুন কলাম না থাকলে যোগ হবে
        svc_cols = [r["name"] for r in c.execute("PRAGMA table_info(services)").fetchall()]
        if "photo_id" not in svc_cols:
            c.execute("ALTER TABLE services ADD COLUMN photo_id TEXT")
        if "platform" not in svc_cols:
            c.execute("ALTER TABLE services ADD COLUMN platform TEXT")
        ord_cols = [r["name"] for r in c.execute("PRAGMA table_info(orders)").fetchall()]
        if "display_no" not in ord_cols:
            c.execute("ALTER TABLE orders ADD COLUMN display_no TEXT")
            c.execute("UPDATE orders SET display_no = 'SP' || printf('%06d', id) WHERE display_no IS NULL")


# ---------- Settings (সব সেটিং অ্যাডমিন প্যানেল থেকে) ----------
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
    return get_setting("brand_name") or DEFAULT_BRAND


def support_username():
    return (get_setting("support_username") or "").lstrip("@")


def channel_setting(kind):
    """kind: 'public' (চ্যানেল ১) বা 'detail' (চ্যানেল ২). সেট না থাকলে বা off হলে None।"""
    value = get_setting(f"channel_{kind}")
    if not value or value == "off":
        return None
    return value


def order_prefixes():
    raw = get_setting("order_prefixes") or DEFAULT_PREFIXES
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or DEFAULT_PREFIXES.split(",")


def order_mode():
    return get_setting("order_no_mode") or "bot"


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
    until = parse_dt(row["banned_until"])
    if until:
        return f"⏳ সাময়িক ব্যান (শেষ: {until.strftime('%d %b %Y, %I:%M %p')})"
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


# ---------- Services ----------
def list_services(active_only=True):
    with closing(db()) as c:
        q = "SELECT * FROM services" + (" WHERE active = 1" if active_only else "") + " ORDER BY created"
        return c.execute(q).fetchall()


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
            (key, platform, name, platform_emoji(platform), panel_id, price, min_qty, max_qty,
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
    """ইউজারকে দেখানোর নাম: ক্যাটাগরি + সার্ভিস নেম। সার্ভিস আইডি কখনো দেখানো হয় না।"""
    platform = srv["platform"] or ""
    return f"{platform} {srv['name']}".strip()


# ---------- Orders ----------
def make_display_no(oid, panel_id):
    if order_mode() == "panel" and panel_id:
        return str(panel_id)
    prefixes = order_prefixes()
    prefix = prefixes[oid % len(prefixes)]
    return f"{prefix}{oid:06d}"


def save_order(uid, srv, link, qty, cost, panel_id):
    """Returns (order_id, display_no)."""
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
    """Returns (status, amount). status: ok | not_found | expired | already"""
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
# Channel helpers
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
    """লিংক থেকে চ্যানেল বের করে: (username, numeric_chat_id)।"""
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
            chat = await context.bot.get_chat(f"@{username}")
            cid = chat.id
        except Exception:
            cid = None
        name = f"@{username}"
    else:
        url = f"https://t.me/c/{str(chat_id)[4:]}"
        name = "প্রাইভেট চ্যানেল"
    idline = f"<code>{cid}</code>" if cid else "পাওয়া যায়নি"
    return f"{h(name)} · আইডি: {idline} · {h(url)}"


async def send_safe(context, target, text):
    try:
        await context.bot.send_message(target_value(target), text,
                                       link_preview_options=LinkPreviewOptions(is_disabled=True))
    except Exception as e:
        log.error("channel post to %s failed: %s", target, e)


def channel_public_text(srv, link, cost):
    """চ্যানেল ১ (মেম্বারদের জন্য): সার্ভিস, হিডেন লিংক, টাকার পরিমাণ।"""
    return (
        "🆕 <b>নতুন অর্ডার</b>\n\n"
        f"<blockquote>🏷 সার্ভিস: {h(service_title(srv))}\n"
        f"🔗 লিংক: <a href=\"{h(link)}\">হিডেন</a>\n"
        f"💵 মূল্য: {money(cost)}</blockquote>"
    )


def channel_detail_text(label, user, srv, link, qty, cost, panel_id, when, chan_line):
    uname = f"@{h(user['username'])}" if user["username"] else "নেই"
    return (
        "📑 <b>অর্ডারের বিস্তারিত</b>\n\n"
        f"<blockquote>🔢 অর্ডার নম্বর: <code>{h(label)}</code>\n"
        f"👤 নাম: {h(full_name(user))}\n"
        f"🏷 ইউজারনেম: {uname}\n"
        f"🪪 ইউজার আইডি: <code>{user['id']}</code>\n"
        f"🛍 সার্ভিস: {h(service_title(srv))}\n"
        f"📢 অর্ডার দেওয়া চ্যানেল: {chan_line}\n"
        f"🔗 লিংক: {h(link)}\n"
        f"📊 পরিমাণ: {qty}\n"
        f"💵 মূল্য: {money(cost)}\n"
        f"🏦 প্যানেল অর্ডার আইডি: <code>{h(panel_id)}</code>\n"
        f"🕒 সময়: {h(when)}</blockquote>"
    )


async def post_order_channels(context, label, user, srv, link, qty, cost, panel_id):
    when = now_str()
    public = channel_setting("public")
    detail = channel_setting("detail")
    if public:
        await send_safe(context, public, channel_public_text(srv, link, cost))
    if detail:
        chan_line = await describe_link_channel(context, link)
        await send_safe(context, detail,
                        channel_detail_text(label, user, srv, link, qty, cost, panel_id, when, chan_line))


# ============================================================
# Helpers & UI
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
    rows = [[btn(f"{s['emoji']}  {service_title(s)}", f"svc:{s['key']}")] for s in list_services()]
    rows.append([btn("💳 ব্যালেন্স", "balance"), btn("➕ ব্যালেন্স যুক্ত", "deposit")])
    rows.append([btn("🎁 রিডিম কোড", "redeem"), btn("📦 আমার অর্ডার", "orders")])
    rows.append([btn("🏠 Start", "home")])
    return InlineKeyboardMarkup(rows)


def greet_name(user):
    """ইউজারনেম থাকলে @username, না থাকলে নাম।"""
    if user.username:
        return f"@{user.username}"
    name = " ".join(p for p in (user.first_name, user.last_name) if p)
    return name.strip() or "বন্ধু"


def welcome_text(user):
    lines = [
        f"<b>✨ {h(brand())}</b>",
        "<i>দ্রুত, নির্ভরযোগ্য ও স্বয়ংক্রিয় সার্ভিস</i>",
        "",
        f"👋 স্বাগতম, <b>{h(greet_name(user))}</b>",
        "",
        "<b>💎 আমাদের প্রাইস</b>",
    ]
    services = list_services()
    if services:
        for s in services:
            lines.append(f"{s['emoji']} {h(service_title(s))}  ·  {money(s['price'])} / ১০০০")
    else:
        lines.append("এখন কোনো সার্ভিস চালু নেই।")
    lines += ["", "নিচের অপশন থেকে বেছে নিন 👇"]
    return "\n".join(lines)


def cancel_kb():
    return InlineKeyboardMarkup([[btn("✖ বাতিল", "flowcancel")]])


def admin_menu():
    return InlineKeyboardMarkup([
        [btn("➕ ব্যালেন্স যোগ", "adm:addbal"), btn("➖ ব্যালেন্স কাটা", "adm:subbal")],
        [btn("🚫 ব্যান", "adm:ban"), btn("✅ আনব্যান", "adm:unban")],
        [btn("👤 ইউজার তথ্য", "adm:userinfo"), btn("👥 ইউজার তালিকা", "adm:users")],
        [btn("🎁 নতুন রিডিম কোড", "adm:codenew"), btn("🗂 কোড তালিকা", "adm:codes")],
        [btn("⛔ কোড বন্ধ", "adm:codedel")],
        [btn("🛍 সার্ভিস যোগ", "adm:svcadd"), btn("📋 সার্ভিস তালিকা", "adm:svclist")],
        [btn("💰 প্রাইস পরিবর্তন", "adm:svcprice"), btn("🗑 সার্ভিস বন্ধ", "adm:svcdel")],
        [btn("🖼 সার্ভিসের ছবি", "adm:svcphoto"), btn("🎞 লোডিং অ্যানিমেশন", "adm:loadanim")],
        [btn("📣 চ্যানেল সেটিং", "adm:chans"), btn("🔢 অর্ডার নম্বর", "adm:ordset")],
        [btn("⚙️ সাধারণ সেটিং", "adm:general"), btn("🔎 অর্ডার খুঁজুন", "adm:orderfind")],
        [btn("🏦 প্যানেল ব্যালেন্স", "adm:panelbal"), btn("✖ প্যানেল বন্ধ", "adm:close")],
    ])


def frames_for(label):
    return [f"{label} ▰▱▱▱", f"{label} ▰▰▱▱", f"{label} ▰▰▰▱", f"{label} ▰▰▰▰"]


async def show_loading(context, chat_id, label, delay=0.4):
    """অ্যাডমিনের দেওয়া GIF/ভিডিও থাকলে সেটা, নাহলে টেক্সট অ্যানিমেশন দেখায়।"""
    frames = frames_for(label)
    anim = get_setting("loading_anim")
    kind = get_setting("loading_kind") or "animation"
    msg = None
    is_media = False
    if anim:
        try:
            sender = context.bot.send_video if kind == "video" else context.bot.send_animation
            msg = await sender(chat_id, anim, caption=frames[0])
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
        return f"🎉 <b>অভিনন্দন!</b>\n\n{money(amount)} আপনার ব্যালেন্সে যুক্ত হয়েছে।"
    if status == "already":
        return "⚠️ আপনি এই কোডটি আগেই ব্যবহার করেছেন।"
    if status == "expired":
        return "⌛ এই কোডের মেয়াদ শেষ বা ব্যবহারের সীমা পূর্ণ হয়েছে।"
    return "❌ কোডটি সঠিক নয়। আবার চেষ্টা করুন।"


def deposit_text(uid):
    sup = support_username()
    contact = f"👤 সাপোর্ট: @{h(sup)}" if sup else "👤 সাপোর্ট: অ্যাডমিনের সাথে যোগাযোগ করুন"
    return (
        "➕ <b>ব্যালেন্স যুক্ত করুন</b>\n\n"
        "পেমেন্ট করে সাপোর্টে যোগাযোগ করুন। নিচের বাটন থেকে এক ক্লিকে কপি করতে পারবেন।\n\n"
        f"{contact}\n"
        f"🪪 আপনার আইডি: <code>{uid}</code>\n\n"
        "পেমেন্টের পর আপনার আইডি জানালে ব্যালেন্স যুক্ত করা হবে।"
    )


def balance_text(uid):
    row = get_user(uid)
    st = user_stats(uid)
    return (
        "💳 <b>আপনার অ্যাকাউন্ট</b>\n\n"
        f"<blockquote>💰 বর্তমান ব্যালেন্স: {money(row['balance'])}\n"
        f"📥 মোট জমা: {money(st['deposits'])}\n"
        f"📤 মোট খরচ: {money(st['spent'])}\n"
        f"🎁 রিডিম থেকে পেয়েছেন: {money(st['redeems'])}\n"
        f"📦 মোট অর্ডার: {st['orders']}</blockquote>"
    )


def order_line(r):
    return (f"🆔 <code>{h(order_label(r))}</code> · {h(r['service_name'])} · "
            f"{r['quantity']} · {money(r['cost'])}")


def user_info_text(row):
    st = user_stats(row["id"])
    uname = f"@{h(row['username'])}" if row["username"] else "—"
    return (
        "👤 <b>ইউজার তথ্য</b>\n\n"
        f"<blockquote>নাম: {h(full_name(row))}\n"
        f"ইউজারনেম: {uname}\n"
        f"আইডি: <code>{row['id']}</code>\n"
        f"যোগদান: {h(row['joined'] or '—')}\n"
        f"স্ট্যাটাস: {status_text(row)}</blockquote>\n\n"
        "<b>💰 হিসাব</b>\n"
        f"<blockquote>বর্তমান ব্যালেন্স: {money(row['balance'])}\n"
        f"মোট জমা: {money(st['deposits'])}\n"
        f"মোট খরচ: {money(st['spent'])}\n"
        f"রিডিম থেকে: {money(st['redeems'])}\n"
        f"মোট অর্ডার: {st['orders']}</blockquote>"
    )


def order_info_text(r):
    user = get_user(r["user_id"])
    who = f"{h(full_name(user))} (<code>{r['user_id']}</code>)" if user else f"<code>{r['user_id']}</code>"
    return (
        f"🔎 <b>অর্ডার {h(order_label(r))}</b>\n\n"
        f"<blockquote>ইউজার: {who}\n"
        f"সার্ভিস: {h(r['service_name'])}\n"
        f"লিংক: {h(r['link'])}\n"
        f"পরিমাণ: {r['quantity']}\n"
        f"মূল্য: {money(r['cost'])}\n"
        f"প্যানেল অর্ডার: <code>{h(r['panel_order'])}</code>\n"
        f"সময়: {h(r['created'])}</blockquote>"
    )


def channels_text():
    def show(kind):
        v = channel_setting(kind)
        return f"<code>{h(v)}</code>" if v else "বন্ধ"
    return (
        "📣 <b>চ্যানেল সেটিং</b>\n\n"
        f"<blockquote>📢 চ্যানেল ১ (মেম্বারদের, লিংক হিডেন):\n{show('public')}\n\n"
        f"🔒 চ্যানেল ২ (শুধু অ্যাডমিনের):\n{show('detail')}</blockquote>\n\n"
        "পরিবর্তন করতে নিচের বাটন ব্যবহার করুন। বন্ধ করতে <code>off</code> লিখুন।"
    )


def order_settings_text():
    mode = "বট নম্বর" if order_mode() == "bot" else "প্যানেল নম্বর"
    prefixes = ", ".join(order_prefixes())
    example = make_display_no(1, 0) if order_mode() == "bot" else "14759 (প্যানেলের আইডি)"
    return (
        "🔢 <b>অর্ডার নম্বর সেটিং</b>\n\n"
        f"<blockquote>মোড: {mode}\n"
        f"প্রিফিক্স: {h(prefixes)}\n"
        f"উদাহরণ: <code>{h(example)}</code></blockquote>\n\n"
        "<b>বট নম্বর:</b> প্রিফিক্স + ক্রমিক নম্বর, যেমন 54000123।\n"
        "<b>প্যানেল নম্বর:</b> ওয়েবসাইটের অর্ডার নম্বর হুবহু দেখানো হবে।"
    )


def general_text():
    sup = support_username()
    return (
        "⚙️ <b>সাধারণ সেটিং</b>\n\n"
        f"<blockquote>🏷 ব্র্যান্ড নাম: {h(brand())}\n"
        f"👤 সাপোর্ট ইউজারনেম: {('@' + h(sup)) if sup else 'সেট করা নেই'}</blockquote>\n\n"
        "পরিবর্তন করতে নিচের বাটন ব্যবহার করুন।"
    )


async def show_service(context, chat_id, srv):
    """ইউজারকে সার্ভিস দেখায়। সার্ভিস আইডি কখনো দেখায় না।
    ছবি: সার্ভিসের নিজের ছবি, না থাকলে ক্যাটাগরির লোগো।"""
    mx = f"{srv['max_qty']}" if srv["max_qty"] else "সীমাহীন"
    text = (
        f"<b>{srv['emoji']} {h(service_title(srv))}</b>\n\n"
        f"💵 মূল্য: {money(srv['price'])} / ১০০০\n"
        f"📏 সর্বনিম্ন: {srv['min_qty']}  ·  সর্বোচ্চ: {mx}\n\n"
        "🔗 আপনার পোস্ট বা চ্যানেলের টেলিগ্রাম লিংক পাঠান:"
    )
    photo = srv["photo_id"] or get_setting(logo_key(srv["platform"]))
    if photo:
        try:
            await context.bot.send_photo(chat_id, photo, caption=text, reply_markup=cancel_kb())
            return
        except Exception as e:
            log.warning("service photo send failed: %s", e)
    await context.bot.send_message(chat_id, text, reply_markup=cancel_kb())


# ============================================================
# Gate: ব্যান চেক ও ইউজার আপডেট
# ============================================================
async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None:
        return
    upsert_user(user)
    if is_admin_id(user.id):
        return
    row = get_user(user.id)
    if not row or not row["is_banned"]:
        return
    until = parse_dt(row["banned_until"])
    if until and now_dt() >= until:
        set_ban(user.id, None, 0)
        return
    if until:
        text = f"⛔ আপনার অ্যাকাউন্ট {until.strftime('%d %b %Y, %I:%M %p')} পর্যন্ত সীমিত করা হয়েছে।"
    else:
        text = "⛔ আপনার অ্যাকাউন্ট স্থায়ীভাবে সীমিত করা হয়েছে।"
    if update.callback_query:
        await update.callback_query.answer(text, show_alert=True)
    elif update.message:
        await update.message.reply_text(text)
    raise ApplicationHandlerStop


# ============================================================
# User commands
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("flow", None)
    chat_id = update.effective_chat.id
    loader = await show_loading(context, chat_id, "✨ প্রস্তুত হচ্ছে", 0.35)
    await finish_loading(loader)
    await context.bot.send_message(chat_id, welcome_text(update.effective_user), reply_markup=main_menu())


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    context.user_data.pop("flow", None)
    await update.message.reply_text(
        "🛠 <b>অ্যাডমিন প্যানেল</b>\n\nনিচের অপশন থেকে বেছে নিন।",
        reply_markup=admin_menu(),
    )


async def send_qty_prompt(context, chat_id, srv):
    mx = srv["max_qty"]
    limit = f"সর্বনিম্ন {srv['min_qty']}" + (f", সর্বোচ্চ {mx}" if mx else ", ১০০-এর বেশি যত ইচ্ছা")
    presets = [n for n in QTY_PRESETS if n >= srv["min_qty"] and (mx == 0 or n <= mx)]
    kb = [[btn(str(n), f"qty:{n}") for n in presets[i:i + 2]] for i in range(0, len(presets), 2)]
    await context.bot.send_message(
        chat_id,
        f"📊 পরিমাণ লিখুন ({limit}) অথবা নিচ থেকে বেছে নিন:",
        reply_markup=InlineKeyboardMarkup(kb) if kb else None,
    )


async def set_qty_summary(context, chat_id, flow, qty, uid):
    d = flow["data"]
    srv = get_service(d["key"])
    if not srv or not srv["active"]:
        context.user_data.pop("flow", None)
        await context.bot.send_message(chat_id, "⚠️ এই সার্ভিসটি এখন পাওয়া যাচ্ছে না।", reply_markup=main_menu())
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
    warn = "" if balance >= cost else "\n\n⚠️ ব্যালেন্স যথেষ্ট নয়। আগে ব্যালেন্স যুক্ত করুন।"
    text = (
        "📋 <b>অর্ডার সারাংশ</b>\n\n"
        f"<blockquote>🏷 সার্ভিস: {h(service_title(srv))}\n"
        f"🔗 লিংক: {h(d['link'])}\n"
        f"📊 পরিমাণ: {qty}\n"
        f"💵 মূল্য: {money(cost)}</blockquote>\n\n"
        f"💳 আপনার ব্যালেন্স: {money(balance)}{warn}"
    )
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
        await context.bot.send_message(chat_id, "⚠️ এই সার্ভিসটি এখন পাওয়া যাচ্ছে না।", reply_markup=main_menu())
        return
    if not charge_order(uid, d["cost"]):
        await context.bot.send_message(chat_id, "❌ ব্যালেন্স যথেষ্ট নয়। আগে ব্যালেন্স যুক্ত করুন।",
                                       reply_markup=main_menu())
        return

    loader = await show_loading(context, chat_id, "🚀 প্যানেলে পাঠানো হচ্ছে", 0.45)

    try:
        resp = await panel({"action": "add", "service": srv["panel_id"], "link": d["link"], "quantity": d["qty"]})
    except httpx.HTTPError as e:
        resp = {"error": str(e)}

    if "order" not in resp:
        adjust_balance(uid, d["cost"], "refund", "ব্যর্থ অর্ডার ফেরত")
        log.error("panel add failed: %s", resp)
        await notify_user(context, ADMIN_ID,
                          f"⚠️ <b>প্যানেল অর্ডার ব্যর্থ</b>\n\nইউজার: <code>{uid}</code>\n"
                          f"সার্ভিস: {h(service_title(srv))}\nকারণ: {h(resp)}")
        await finish_loading(loader)
        await context.bot.send_message(
            chat_id,
            "❌ অর্ডার সম্পন্ন হয়নি। টাকা আপনার ব্যালেন্সে ফেরত দেওয়া হয়েছে। কিছুক্ষণ পর আবার চেষ্টা করুন।",
            reply_markup=main_menu(),
        )
        return

    panel_id = str(resp["order"])
    oid, label = save_order(uid, srv, d["link"], d["qty"], d["cost"], panel_id)
    await finish_loading(loader)
    success_kb = InlineKeyboardMarkup(
        [[copy_btn("📋 অর্ডার নম্বর কপি", label)]] + main_menu().inline_keyboard
    )
    await context.bot.send_message(
        chat_id,
        "✅ <b>অর্ডার সফল হয়েছে!</b>\n\n"
        f"<blockquote>🔢 অর্ডার নম্বর: <code>{h(label)}</code>\n"
        f"🏷 সার্ভিস: {h(service_title(srv))}\n"
        f"📊 পরিমাণ: {d['qty']}\n"
        f"💵 কাটা হয়েছে: {money(d['cost'])}</blockquote>\n\n"
        "এখনই আরেকটি অর্ডার দিতে পারেন।",
        reply_markup=success_kb,
    )

    user = get_user(uid)
    await post_order_channels(context, label, user, srv, d["link"], d["qty"], d["cost"], panel_id)


async def do_redeem(context, chat_id, uid, code):
    loader = await show_loading(context, chat_id, "🎁 কোড যাচাই হচ্ছে", 0.4)
    status, amount = redeem(uid, code)
    await finish_loading(loader)
    await context.bot.send_message(chat_id, redeem_message(status, amount), reply_markup=main_menu())


# ============================================================
# Callbacks
# ============================================================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    uid = q.from_user.id
    chat_id = q.message.chat.id
    flow = context.user_data.get("flow")

    if data == "home":
        context.user_data.pop("flow", None)
        loader = await show_loading(context, chat_id, "✨ প্রস্তুত হচ্ছে", 0.35)
        await finish_loading(loader)
        await context.bot.send_message(chat_id, welcome_text(q.from_user), reply_markup=main_menu())

    elif data == "balance":
        await context.bot.send_message(chat_id, balance_text(uid))

    elif data == "deposit":
        sup = support_username()
        rows = [[copy_btn("📋 আইডি কপি", uid)]]
        if sup:
            rows.insert(0, [copy_btn("📋 সাপোর্ট ইউজারনেম কপি", f"@{sup}")])
            rows.append([InlineKeyboardButton("💬 সাপোর্টে মেসেজ", url=f"https://t.me/{sup}")])
        await context.bot.send_message(chat_id, deposit_text(uid), reply_markup=InlineKeyboardMarkup(rows))

    elif data == "orders":
        rows = user_orders(uid)
        if not rows:
            text = "📦 আপনার এখনো কোনো অর্ডার নেই।"
        else:
            text = "📦 <b>আপনার সাম্প্রতিক অর্ডার</b>\n\n" + "\n".join(order_line(r) for r in rows)
        await context.bot.send_message(chat_id, text)

    elif data == "redeem":
        context.user_data["flow"] = {"name": "redeem", "step": "code", "data": {}}
        await context.bot.send_message(chat_id, "🎁 <b>রিডিম কোড</b>\n\nআপনার কোডটি নিচের ঘরে লিখে পাঠান:",
                                       reply_markup=cancel_kb())

    elif data.startswith("svc:"):
        srv = get_service(data.split(":", 1)[1])
        if not srv or not srv["active"]:
            await context.bot.send_message(chat_id, "⚠️ এই সার্ভিসটি এখন পাওয়া যাচ্ছে না।", reply_markup=main_menu())
            return
        context.user_data["flow"] = {"name": "order", "step": "link", "data": {"key": srv["key"]}}
        await show_service(context, chat_id, srv)

    elif data.startswith("qty:"):
        if flow and flow["name"] == "order" and flow["step"] == "qty":
            await set_qty_summary(context, chat_id, flow, int(data.split(":", 1)[1]), uid)
        else:
            await context.bot.send_message(chat_id, "⚠️ আগে সার্ভিস বেছে নিন।", reply_markup=main_menu())

    elif data == "order_confirm":
        if flow and flow["name"] == "order" and flow["step"] == "confirm":
            await place_order(q, context, flow)
        else:
            await context.bot.send_message(chat_id, "⚠️ কোনো অর্ডার পাওয়া যায়নি। আবার সার্ভিস বেছে নিন।",
                                           reply_markup=main_menu())

    elif data in ("order_cancel", "flowcancel"):
        context.user_data.pop("flow", None)
        await context.bot.send_message(chat_id, "❌ বাতিল করা হয়েছে।", reply_markup=main_menu())

    elif data.startswith("adm:"):
        if not is_admin_id(uid):
            return
        await admin_callback(q, context, data[4:])

    elif data.startswith("chset:"):
        if not is_admin_id(uid):
            return
        kind = data.split(":", 1)[1]
        context.user_data["flow"] = {"name": "adm_chset", "step": "value", "data": {"kind": kind}}
        label = "চ্যানেল ১ (মেম্বারদের)" if kind == "public" else "চ্যানেল ২ (শুধু অ্যাডমিনের)"
        await context.bot.send_message(
            chat_id,
            f"📣 <b>{label}</b>\n\nচ্যানেলের <code>@username</code> বা আইডি (যেমন -100...) পাঠান।\n"
            "বটকে ওই চ্যানেলের অ্যাডমিন বানাতে হবে।\nবন্ধ করতে <code>off</code> লিখুন।",
            reply_markup=cancel_kb(),
        )

    elif data.startswith("gen:"):
        if not is_admin_id(uid):
            return
        kind = data.split(":", 1)[1]
        context.user_data["flow"] = {"name": "adm_general", "step": "value", "data": {"kind": kind}}
        if kind == "brand":
            text = f"🏷 নতুন ব্র্যান্ড নাম লিখুন। বর্তমান: <b>{h(brand())}</b>"
        else:
            text = ("👤 সাপোর্ট ইউজারনেম লিখুন (@ ছাড়া বা সহ)।\n"
                    "বন্ধ করতে <code>off</code> লিখুন।")
        await context.bot.send_message(chat_id, text, reply_markup=cancel_kb())

    elif data in ("ban_perm_yes", "ban_perm_no"):
        if not is_admin_id(uid):
            return
        await ban_choice(q, context, data)

    elif data.split(":", 1)[0] in ("svcprice", "svcdel", "svcphoto"):
        if not is_admin_id(uid):
            return
        action, key = data.split(":", 1)
        srv = get_service(key)
        if not srv:
            await context.bot.send_message(chat_id, "❌ সার্ভিস পাওয়া যায়নি।", reply_markup=admin_menu())
            return
        if action == "svcprice":
            context.user_data["flow"] = {"name": "adm_svcprice", "step": "price", "data": {"key": key}}
            await context.bot.send_message(
                chat_id,
                f"💰 <b>{h(service_title(srv))}</b>\nবর্তমান মূল্য: {money(srv['price'])} / ১০০০\n\nনতুন মূল্য লিখুন:",
                reply_markup=cancel_kb(),
            )
        elif action == "svcphoto":
            context.user_data["flow"] = {"name": "adm_svcphoto", "step": "photo", "data": {"key": key}}
            await context.bot.send_message(
                chat_id,
                f"🖼 <b>{h(service_title(srv))}</b>-এর জন্য একটি ছবি পাঠান:",
                reply_markup=cancel_kb(),
            )
        else:
            deactivate_service(key)
            await context.bot.send_message(chat_id, f"✅ <b>{h(service_title(srv))}</b> বন্ধ করা হয়েছে।",
                                           reply_markup=admin_menu())


async def admin_callback(q, context, action):
    chat_id = q.message.chat.id
    prompts = {
        "addbal": ("adm_adjust", {"mode": "add"},
                   "➕ <b>ব্যালেন্স যোগ</b>\n\nইউজারের <b>ইউজারনেম</b>, <b>টেলিগ্রাম আইডি</b> বা <b>নম্বর</b> লিখুন:"),
        "subbal": ("adm_adjust", {"mode": "sub"},
                   "➖ <b>ব্যালেন্স কাটা</b>\n\nইউজারের <b>ইউজারনেম</b> বা <b>আইডি</b> লিখুন:"),
        "ban": ("adm_ban", {}, "🚫 <b>ইউজার ব্যান</b>\n\nইউজারনেম, আইডি বা নম্বর লিখুন:"),
        "unban": ("adm_unban", {}, "✅ <b>ইউজার আনব্যান</b>\n\nইউজারনেম বা আইডি লিখুন:"),
        "userinfo": ("adm_userinfo", {}, "👤 <b>ইউজার তথ্য</b>\n\nইউজারনেম বা আইডি লিখুন:"),
        "orderfind": ("adm_orderfind", {}, "🔎 <b>অর্ডার খুঁজুন</b>\n\nঅর্ডার নম্বর লিখুন (যেমন 54000123):"),
    }
    if action in prompts:
        name, data, text = prompts[action]
        context.user_data["flow"] = {"name": name, "step": "ident", "data": data}
        await context.bot.send_message(chat_id, text, reply_markup=cancel_kb())
    elif action == "codenew":
        context.user_data["flow"] = {"name": "adm_codenew", "step": "code", "data": {}}
        await context.bot.send_message(chat_id, "🎁 <b>নতুন রিডিম কোড</b>\n\nকোডের নাম লিখুন (ইংরেজি অক্ষর, সংখ্যা বা _):",
                                       reply_markup=cancel_kb())
    elif action == "codedel":
        context.user_data["flow"] = {"name": "adm_codedel", "step": "code", "data": {}}
        await context.bot.send_message(chat_id, "⛔ যে কোডটি বন্ধ করবেন তার নাম লিখুন:", reply_markup=cancel_kb())
    elif action == "svcadd":
        context.user_data["flow"] = {"name": "adm_svcadd", "step": "platform", "data": {}}
        await context.bot.send_message(
            chat_id,
            "🛍 <b>নতুন সার্ভিস</b>\n\n"
            "প্রথমে ক্যাটাগরি লিখুন, যেমন <code>Facebook</code>, <code>Telegram</code>, <code>Instagram</code>, "
            "<code>YouTube</code>, <code>TikTok</code>।",
            reply_markup=cancel_kb(),
        )
    elif action in ("svcprice", "svcdel", "svcphoto"):
        srvs = list_services()
        if not srvs:
            await context.bot.send_message(chat_id, "কোনো সার্ভিস নেই।", reply_markup=admin_menu())
            return
        labels = {"svcprice": "💰", "svcdel": "🗑", "svcphoto": "🖼"}
        kb = [[btn(f"{labels[action]} {service_title(s)} · {money(s['price'])}", f"{action}:{s['key']}")]
              for s in srvs]
        kb.append([btn("✖ বাতিল", "flowcancel")])
        titles = {"svcprice": "কোন সার্ভিসের প্রাইস বদলাবেন?",
                  "svcdel": "কোন সার্ভিসটি বন্ধ করবেন?",
                  "svcphoto": "কোন সার্ভিসের ছবি দিবেন?"}
        await context.bot.send_message(chat_id, titles[action], reply_markup=InlineKeyboardMarkup(kb))
    elif action == "loadanim":
        context.user_data["flow"] = {"name": "adm_loadanim", "step": "media", "data": {}}
        await context.bot.send_message(
            chat_id,
            "🎞 <b>লোডিং অ্যানিমেশন</b>\n\nএকটি GIF বা ছোট ভিডিও পাঠান। এটি প্রতিবার লোডিংয়ের সময় দেখাবে।\n"
            "বন্ধ করতে <code>off</code> লিখুন।",
            reply_markup=cancel_kb(),
        )
    elif action == "chans":
        await context.bot.send_message(
            chat_id,
            channels_text(),
            reply_markup=InlineKeyboardMarkup([
                [btn("📢 চ্যানেল ১ বদলাও", "chset:public"), btn("🔒 চ্যানেল ২ বদলাও", "chset:detail")],
                [btn("✖ বাতিল", "flowcancel")],
            ]),
        )
    elif action == "ordset":
        await context.bot.send_message(
            chat_id,
            order_settings_text(),
            reply_markup=InlineKeyboardMarkup([
                [btn("✏️ প্রিফিক্স বদলাও", "adm:ordprefix"), btn("🔁 মোড বদলাও", "adm:ordmode")],
                [btn("✖ বাতিল", "flowcancel")],
            ]),
        )
    elif action == "ordprefix":
        context.user_data["flow"] = {"name": "adm_ordset", "step": "prefixes", "data": {}}
        await context.bot.send_message(
            chat_id,
            "✏️ নতুন প্রিফিক্স লিখুন, কমা দিয়ে আলাদা করে। উদাহরণ: <code>54,18</code>",
            reply_markup=cancel_kb(),
        )
    elif action == "ordmode":
        new_mode = "panel" if order_mode() == "bot" else "bot"
        set_setting("order_no_mode", new_mode)
        await context.bot.send_message(chat_id, order_settings_text(), reply_markup=admin_menu())
    elif action == "general":
        await context.bot.send_message(
            chat_id,
            general_text(),
            reply_markup=InlineKeyboardMarkup([
                [btn("🏷 ব্র্যান্ড নাম", "gen:brand"), btn("👤 সাপোর্ট ইউজারনেম", "gen:support")],
                [btn("✖ বাতিল", "flowcancel")],
            ]),
        )
    elif action == "users":
        await send_users(context, chat_id)
    elif action == "codes":
        await send_codes(context, chat_id)
    elif action == "svclist":
        await send_services(context, chat_id)
    elif action == "panelbal":
        try:
            resp = await panel({"action": "balance"})
            await context.bot.send_message(
                chat_id,
                f"🏦 প্যানেল ব্যালেন্স: <b>{h(resp.get('balance', '?'))} {h(resp.get('currency', ''))}</b>",
                reply_markup=admin_menu(),
            )
        except httpx.HTTPError as e:
            await context.bot.send_message(chat_id, f"❌ প্যানেলে সংযোগ হয়নি: {h(e)}", reply_markup=admin_menu())
    elif action == "close":
        context.user_data.pop("flow", None)
        await context.bot.send_message(chat_id, "🔒 অ্যাডমিন প্যানেল বন্ধ করা হয়েছে। আবার খুলতে /admin লিখুন।")


async def send_users(context, chat_id):
    rows = all_users()
    if not rows:
        await context.bot.send_message(chat_id, "এখনো কোনো ইউজার নেই।", reply_markup=admin_menu())
        return
    lines = [
        f"<code>{r['id']}</code> · {h(full_name(r))} · @{h(r['username'] or '-')} · {money(r['balance'])} · "
        f"{'🚫' if r['is_banned'] else '✅'}"
        for r in rows
    ]
    await context.bot.send_message(chat_id, f"👥 <b>মোট ইউজার: {len(rows)}</b>")
    for i in range(0, len(lines), 25):
        await context.bot.send_message(chat_id, "\n".join(lines[i:i + 25]))


async def send_codes(context, chat_id):
    rows = list_codes()
    if not rows:
        await context.bot.send_message(chat_id, "এখনো কোনো রিডিম কোড নেই।", reply_markup=admin_menu())
        return
    lines = ["🎁 <b>রিডিম কোড তালিকা</b>", ""]
    for r in rows:
        limit = "∞" if r["max_uses"] == 0 else str(r["max_uses"])
        ok = r["active"] and (r["max_uses"] == 0 or r["used_count"] < r["max_uses"])
        lines.append(f"{'🟢' if ok else '🔴'} <code>{h(r['code'])}</code> · {money(r['amount'])} · "
                     f"ব্যবহার {r['used_count']}/{limit}")
    await context.bot.send_message(chat_id, "\n".join(lines), reply_markup=admin_menu())


async def send_services(context, chat_id):
    rows = list_services(active_only=False)
    if not rows:
        await context.bot.send_message(
            chat_id, "কোনো সার্ভিস নেই। <b>🛍 সার্ভিস যোগ</b> থেকে প্রথম সার্ভিস যোগ করুন।",
            reply_markup=admin_menu())
        return
    lines = ["📋 <b>সার্ভিস তালিকা</b>", "<i>(প্যানেল আইডি শুধু অ্যাডমিন দেখতে পান)</i>", ""]
    for r in rows:
        mx = r["max_qty"] or "সীমাহীন"
        lines.append(
            f"{r['emoji']} <b>{h(service_title(r))}</b> {'' if r['active'] else '(বন্ধ)'} "
            f"{'🖼' if r['photo_id'] else ''}\n"
            f"   প্যানেল আইডি: <code>{r['panel_id']}</code> · মূল্য: {money(r['price'])} / ১০০০\n"
            f"   সীমা: {r['min_qty']} – {mx}"
        )
    await context.bot.send_message(chat_id, "\n".join(lines), reply_markup=admin_menu())


async def ban_choice(q, context, data):
    chat_id = q.message.chat.id
    flow = context.user_data.get("flow")
    if not flow or flow["name"] != "adm_ban" or flow["step"] != "choice":
        await context.bot.send_message(chat_id, "⚠️ আগে ইউজার নির্বাচন করুন।", reply_markup=admin_menu())
        return
    uid = flow["data"]["uid"]
    if data == "ban_perm_yes":
        set_ban(uid, None, 1)
        context.user_data.pop("flow", None)
        await context.bot.send_message(chat_id, f"🚫 <code>{uid}</code> স্থায়ীভাবে ব্যান করা হয়েছে।",
                                       reply_markup=admin_menu())
        await notify_user(context, uid, "⛔ আপনার অ্যাকাউন্ট স্থায়ীভাবে সীমিত করা হয়েছে।")
    else:
        flow["step"] = "days"
        await context.bot.send_message(chat_id, "⏳ কত দিনের জন্য ব্যান করবেন? (দিন সংখ্যা লিখুন)",
                                       reply_markup=cancel_kb())


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
        await update.message.reply_text("⚠️ নিচের বাটন থেকে কনফার্ম বা বাতিল করুন।")


async def flow_adm_adjust(update, context, flow):
    text = update.message.text.strip()
    if flow["step"] == "ident":
        row = find_user(text)
        if not row:
            await update.message.reply_text("❌ ইউজার পাওয়া যায়নি। আবার লিখুন:", reply_markup=cancel_kb())
            return
        flow["data"]["uid"] = row["id"]
        flow["step"] = "amount"
        verb = "যোগ" if flow["data"]["mode"] == "add" else "কাটা"
        await update.message.reply_text(
            f"👤 <b>{h(full_name(row))}</b>\nবর্তমান ব্যালেন্স: {money(row['balance'])}\n\n"
            f"কত টাকা {verb} করবেন? (সংখ্যা লিখুন)",
            reply_markup=cancel_kb(),
        )
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
        msg = f"✅ {uid}-কে {money(amt)} যোগ করা হয়েছে।"
        notice = f"➕ আপনার অ্যাকাউন্টে {money(amt)} যুক্ত করা হয়েছে।"
    else:
        adjust_balance(uid, -amt, "withdraw", "অ্যাডমিন কাটা")
        msg = f"✅ {uid} থেকে {money(amt)} কাটা হয়েছে।"
        notice = f"➖ আপনার অ্যাকাউন্ট থেকে {money(amt)} কাটা হয়েছে।"
    await update.message.reply_text(f"{msg}\nনতুন ব্যালেন্স: {money(get_user(uid)['balance'])}",
                                    reply_markup=admin_menu())
    await notify_user(context, uid, f"{notice}\nবর্তমান ব্যালেন্স: {money(get_user(uid)['balance'])}")


async def flow_adm_ban(update, context, flow):
    text = update.message.text.strip()
    if flow["step"] == "ident":
        row = find_user(text)
        if not row:
            await update.message.reply_text("❌ ইউজার পাওয়া যায়নি। আবার লিখুন:", reply_markup=cancel_kb())
            return
        flow["data"]["uid"] = row["id"]
        flow["step"] = "choice"
        await update.message.reply_text(
            f"👤 <b>{h(full_name(row))}</b> (<code>{row['id']}</code>)\n\nএটি কি স্থায়ী ব্যান হবে?",
            reply_markup=InlineKeyboardMarkup([
                [btn("♾ হ্যাঁ, স্থায়ী", "ban_perm_yes"), btn("⏳ না, নির্দিষ্ট দিন", "ban_perm_no")],
                [btn("✖ বাতিল", "flowcancel")],
            ]),
        )
    elif flow["step"] == "choice":
        await update.message.reply_text("⚠️ নিচের বাটন থেকে বেছে নিন।")
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
            f"⏳ <code>{uid}</code> {days} দিনের জন্য ব্যান করা হয়েছে।\nশেষ: {until.strftime('%d %b %Y, %I:%M %p')}",
            reply_markup=admin_menu(),
        )
        await notify_user(context, uid, f"⛔ আপনার অ্যাকাউন্ট {days} দিনের জন্য সীমিত করা হয়েছে।")


async def flow_adm_unban(update, context, flow):
    row = find_user(update.message.text)
    if not row:
        await update.message.reply_text("❌ ইউজার পাওয়া যায়নি। আবার লিখুন:", reply_markup=cancel_kb())
        return
    set_ban(row["id"], None, 0)
    context.user_data.pop("flow", None)
    await update.message.reply_text(f"✅ <b>{h(full_name(row))}</b> আনব্যান করা হয়েছে।", reply_markup=admin_menu())
    await notify_user(context, row["id"], "✅ আপনার অ্যাকাউন্টের সীমাবদ্ধতা তুলে নেওয়া হয়েছে।")


async def flow_adm_userinfo(update, context, flow):
    row = find_user(update.message.text)
    if not row:
        await update.message.reply_text("❌ ইউজার পাওয়া যায়নি। আবার লিখুন:", reply_markup=cancel_kb())
        return
    context.user_data.pop("flow", None)
    await update.message.reply_text(user_info_text(row), reply_markup=admin_menu())


async def flow_adm_orderfind(update, context, flow):
    row = find_order(update.message.text)
    if not row:
        await update.message.reply_text("❌ অর্ডার পাওয়া যায়নি। আবার লিখুন:", reply_markup=cancel_kb())
        return
    context.user_data.pop("flow", None)
    await update.message.reply_text(order_info_text(row), reply_markup=admin_menu())


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
            await update.message.reply_text("⚠️ এই কোডটি আগে থেকেই আছে।", reply_markup=admin_menu())
            return
        limit = "সীমাহীন" if int(text) == 0 else text
        await update.message.reply_text(
            "✅ <b>কোড তৈরি হয়েছে</b>\n\n"
            f"<blockquote>🎁 কোড: <code>{h(d['code'])}</code>\n"
            f"💵 মূল্য: {money(d['amount'])}\n"
            f"👥 ব্যবহার সীমা: {limit}</blockquote>\n\n"
            "প্রতি ইউজার একবারই ব্যবহার করতে পারবে।",
            reply_markup=InlineKeyboardMarkup([[copy_btn("📋 কোড কপি", d["code"])]] + admin_menu().inline_keyboard),
        )


async def flow_adm_codedel(update, context, flow):
    code = update.message.text.strip().upper()
    context.user_data.pop("flow", None)
    if deactivate_code(code):
        await update.message.reply_text(f"✅ <code>{h(code)}</code> বন্ধ করা হয়েছে।", reply_markup=admin_menu())
    else:
        await update.message.reply_text("❌ কোডটি পাওয়া যায়নি।", reply_markup=admin_menu())


async def ask_svc_name(message):
    await message.reply_text("🏷 সার্ভিসের নাম লিখুন (যেমন Post Views বা Members):", reply_markup=cancel_kb())


async def finish_svcadd(message, context, d, photo_id):
    context.user_data.pop("flow", None)
    key = add_service(d["name"], d["platform"], d["panel_id"], d["price"], d["min_qty"], d["max_qty"], photo_id)
    await message.reply_text(
        "✅ <b>সার্ভিস যোগ হয়েছে</b>\n\n"
        f"<blockquote>📂 ক্যাটাগরি: {h(d['platform'])}\n"
        f"🏷 নাম: {h(d['name'])}\n"
        f"🔢 প্যানেল আইডি: <code>{d['panel_id']}</code> (ইউজার দেখবে না)\n"
        f"💰 মূল্য: {money(d['price'])} / ১০০০\n"
        f"📏 সীমা: {d['min_qty']} – {d['max_qty'] or 'সীমাহীন'}\n"
        f"🖼 সার্ভিসের ছবি: {'আছে' if photo_id else 'নেই'}\n"
        f"🆔 ভেতরের কী: <code>{key}</code></blockquote>",
        reply_markup=admin_menu(),
    )


async def flow_adm_svcadd(update, context, flow):
    text = update.message.text.strip()
    d = flow["data"]
    step = flow["step"]

    if step == "platform":
        if not text:
            await update.message.reply_text("⚠️ ক্যাটাগরির নাম লিখুন।", reply_markup=cancel_kb())
            return
        d["platform"] = text[:40]
        if get_setting(logo_key(d["platform"])):
            flow["step"] = "name"
            await ask_svc_name(update.message)
        else:
            flow["step"] = "logo"
            await update.message.reply_text(
                f"🖼 <b>{h(d['platform'])}</b> ক্যাটাগরির লোগো পাঠান (ছোট আকারে দেখাবে, একবারই লাগবে)।\n"
                "লোগো ছাড়া যোগ করতে <code>skip</code> লিখুন।",
                reply_markup=cancel_kb(),
            )

    elif step == "logo":
        if text.lower() == "skip":
            flow["step"] = "name"
            await ask_svc_name(update.message)
        else:
            await update.message.reply_text("⚠️ লোগো হিসেবে একটি ছবি পাঠান, অথবা <code>skip</code> লিখুন।",
                                            reply_markup=cancel_kb())

    elif step == "name":
        if not text:
            await update.message.reply_text("⚠️ নাম লিখুন।", reply_markup=cancel_kb())
            return
        d["name"] = text
        flow["step"] = "panel"
        await update.message.reply_text("🔢 ওয়েবসাইটের সার্ভিস আইডি লিখুন (যেমন 14759):", reply_markup=cancel_kb())

    elif step == "panel":
        if not text.isdigit():
            await update.message.reply_text("⚠️ শুধু সংখ্যা আইডি লিখুন।", reply_markup=cancel_kb())
            return
        d["panel_id"] = int(text)
        flow["step"] = "price"
        await update.message.reply_text("💰 প্রতি ১০০০-এর বিক্রয় মূল্য লিখুন (যেমন 0.998):", reply_markup=cancel_kb())

    elif step == "price":
        try:
            d["price"] = float(text)
            assert d["price"] > 0
        except (ValueError, AssertionError):
            await update.message.reply_text("⚠️ সঠিক মূল্য লিখুন।", reply_markup=cancel_kb())
            return
        flow["step"] = "min"
        await update.message.reply_text("📏 সর্বনিম্ন পরিমাণ লিখুন (সাধারণত 100):", reply_markup=cancel_kb())

    elif step == "min":
        if not text.isdigit() or int(text) <= 0:
            await update.message.reply_text("⚠️ সঠিক সংখ্যা লিখুন।", reply_markup=cancel_kb())
            return
        d["min_qty"] = int(text)
        flow["step"] = "max"
        await update.message.reply_text("📐 সর্বোচ্চ পরিমাণ লিখুন (সীমাহীনের জন্য 0):", reply_markup=cancel_kb())

    elif step == "max":
        if not text.isdigit():
            await update.message.reply_text("⚠️ শুধু সংখ্যা লিখুন (সীমাহীনের জন্য 0)।", reply_markup=cancel_kb())
            return
        d["max_qty"] = int(text)
        flow["step"] = "photo"
        await update.message.reply_text(
            "🖼 এই সার্ভিসের নিজস্ব ছবি পাঠান (ঐচ্ছিক)।\nছবি ছাড়া যোগ করতে <code>skip</code> লিখুন।",
            reply_markup=cancel_kb(),
        )

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
    await update.message.reply_text(f"✅ <b>{h(service_title(srv))}</b>: নতুন মূল্য {money(price)} / ১০০০",
                                    reply_markup=admin_menu())


async def flow_adm_loadanim(update, context, flow):
    if update.message.text.strip().lower() == "off":
        set_setting("loading_anim", None)
        set_setting("loading_kind", None)
        context.user_data.pop("flow", None)
        await update.message.reply_text("✅ লোডিং অ্যানিমেশন বন্ধ করা হয়েছে। এখন টেক্সট অ্যানিমেশন দেখাবে।",
                                        reply_markup=admin_menu())
    else:
        await update.message.reply_text("⚠️ একটি GIF বা ভিডিও পাঠান, অথবা <code>off</code> লিখুন।",
                                        reply_markup=cancel_kb())


async def flow_adm_chset(update, context, flow):
    text = update.message.text.strip()
    kind = flow["data"]["kind"]
    if text.lower() == "off":
        set_setting(f"channel_{kind}", "off")
        context.user_data.pop("flow", None)
        await update.message.reply_text("✅ চ্যানেলটি বন্ধ করা হয়েছে।", reply_markup=admin_menu())
        return
    target = normalize_channel_input(text)
    try:
        chat = await context.bot.get_chat(target_value(target))
    except Exception:
        await update.message.reply_text(
            "❌ চ্যানেল পাওয়া যায়নি। @username বা আইডি সঠিক কিনা দেখুন এবং বটকে চ্যানেলে যোগ করুন।",
            reply_markup=cancel_kb(),
        )
        return
    try:
        member = await context.bot.get_chat_member(chat.id, context.bot.id)
        is_admin_there = member.status in ("administrator", "creator")
    except Exception:
        is_admin_there = False
    if not is_admin_there:
        await update.message.reply_text(
            "⚠️ বটকে এই চ্যানেলের অ্যাডমিন বানান (মেসেজ পাঠানোর অনুমতিসহ), তারপর আবার চেষ্টা করুন।",
            reply_markup=cancel_kb(),
        )
        return
    value = f"@{chat.username}" if chat.username else str(chat.id)
    set_setting(f"channel_{kind}", value)
    context.user_data.pop("flow", None)
    await update.message.reply_text(
        f"✅ চ্যানেল সেট হয়েছে: <code>{h(value)}</code>\nআগামী অর্ডার থেকে এখানে পোস্ট হবে।",
        reply_markup=admin_menu(),
    )


async def flow_adm_ordset(update, context, flow):
    parts = [p.strip() for p in update.message.text.split(",") if p.strip()]
    if not parts or any((not p.isdigit()) or len(p) > 3 for p in parts):
        await update.message.reply_text(
            "⚠️ প্রিফিক্স হতে হবে সংখ্যা, কমা দিয়ে আলাদা। উদাহরণ: <code>54,18</code>",
            reply_markup=cancel_kb(),
        )
        return
    set_setting("order_prefixes", ",".join(parts))
    context.user_data.pop("flow", None)
    await update.message.reply_text(order_settings_text(), reply_markup=admin_menu())


async def flow_adm_general(update, context, flow):
    text = update.message.text.strip()
    kind = flow["data"]["kind"]
    if kind == "brand":
        if not text or len(text) > 40:
            await update.message.reply_text("⚠️ ব্র্যান্ড নাম ১-৪০ অক্ষরের মধ্যে লিখুন।", reply_markup=cancel_kb())
            return
        set_setting("brand_name", text)
        value_text = f"<b>{h(text)}</b>"
    else:
        if text.lower() == "off":
            set_setting("support_username", "")
            value_text = "বন্ধ"
        else:
            uname = text.lstrip("@").strip()
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", uname):
                await update.message.reply_text("⚠️ সঠিক ইউজারনেম লিখুন (৫-৩২ অক্ষর, অক্ষর দিয়ে শুরু)।",
                                                reply_markup=cancel_kb())
                return
            set_setting("support_username", uname)
            value_text = f"@{h(uname)}"
    context.user_data.pop("flow", None)
    await update.message.reply_text(f"✅ সেট হয়েছে: {value_text}", reply_markup=admin_menu())


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
    "adm_svcadd": flow_adm_svcadd,
    "adm_svcprice": flow_adm_svcprice,
    "adm_loadanim": flow_adm_loadanim,
    "adm_chset": flow_adm_chset,
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
        await msg.reply_text("✅ লোডিং অ্যানিমেশন সেট হয়েছে। এখন থেকে এটি দেখাবে।", reply_markup=admin_menu())

    elif name == "adm_svcphoto" and msg.photo:
        set_service_photo(flow["data"]["key"], msg.photo[-1].file_id)
        context.user_data.pop("flow", None)
        srv = get_service(flow["data"]["key"])
        await msg.reply_text(f"✅ <b>{h(service_title(srv))}</b>-এর ছবি সেট হয়েছে।", reply_markup=admin_menu())

    elif name == "adm_svcadd" and flow["step"] == "logo" and msg.photo:
        set_setting(logo_key(flow["data"]["platform"]), msg.photo[-1].file_id)
        flow["step"] = "name"
        await msg.reply_text(f"✅ <b>{h(flow['data']['platform'])}</b> লোগো সেট হয়েছে।")
        await ask_svc_name(msg)

    elif name == "adm_svcadd" and flow["step"] == "photo" and msg.photo:
        await finish_svcadd(msg, context, flow["data"], msg.photo[-1].file_id)

    else:
        await msg.reply_text("⚠️ এই ধাপে এই ধরনের ফাইল প্রয়োজন নেই। নির্দেশনা অনুযায়ী ফাইল পাঠান।",
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

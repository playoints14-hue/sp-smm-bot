import html
import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from functools import wraps

import httpx
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
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
# Config (সব মান Environment Variable থেকে আসে, কোডে কিছু লেখা নেই)
# ============================================================
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "").lstrip("@")
CHANNEL = os.environ.get("CHANNEL", "@SPTechZone")
PANEL_API_URL = os.environ["PANEL_API_URL"]
PANEL_API_KEY = os.environ["PANEL_API_KEY"]
DB_PATH = os.environ.get("DB_PATH", "bot.db")
BRAND = os.environ.get("BRAND_NAME", "SP Tech Zone")
CURRENCY = "৳"

SERVICES = {}
if os.environ.get("VIEWS_SERVICE_ID"):
    SERVICES["views"] = {
        "name": "Telegram Post Views",
        "emoji": "👁",
        "service_id": int(os.environ["VIEWS_SERVICE_ID"]),
        "price": float(os.environ.get("VIEWS_PRICE", "0.998")),
        "min": 100,
        "max": 100000,
    }
if os.environ.get("MEMBERS_SERVICE_ID"):
    SERVICES["members"] = {
        "name": "Telegram Members",
        "emoji": "👥",
        "service_id": int(os.environ["MEMBERS_SERVICE_ID"]),
        "price": float(os.environ.get("MEMBERS_PRICE", "50")),
        "min": 100,
        "max": 50000,
    }
if not SERVICES:
    raise SystemExit("কমপক্ষে একটি সার্ভিস আইডি (VIEWS_SERVICE_ID বা MEMBERS_SERVICE_ID) সেট করুন।")

QTY_PRESETS = [100, 400, 1000, 5000]


# ============================================================
# Database
# ============================================================
def db():
    conn = sqlite3.connect(DB_PATH, isolation_level=None)  # autocommit; ট্রানজ্যাকশন ম্যানুয়ালি
    conn.row_factory = sqlite3.Row
    return conn


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def init_db():
    with closing(db()) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            username TEXT,
            balance REAL NOT NULL DEFAULT 0,
            banned INTEGER NOT NULL DEFAULT 0,
            joined TEXT
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            service TEXT,
            link TEXT,
            quantity INTEGER,
            cost REAL,
            panel_order TEXT,
            status TEXT,
            created TEXT
        );
        CREATE TABLE IF NOT EXISTS prices (
            service TEXT PRIMARY KEY,
            price REAL NOT NULL
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
        """)
        for key, s in SERVICES.items():
            c.execute("INSERT OR IGNORE INTO prices (service, price) VALUES (?, ?)", (key, s["price"]))


def upsert_user(u):
    with closing(db()) as c:
        c.execute(
            """INSERT INTO users (id, first_name, last_name, username, joined)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   first_name = excluded.first_name,
                   last_name = excluded.last_name,
                   username = excluded.username""",
            (u.id, u.first_name or "", u.last_name or "", u.username, now()),
        )


def get_user(uid):
    with closing(db()) as c:
        return c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def full_name(row):
    return f"{row['first_name']} {row['last_name']}".strip() or "নাম নেই"


def get_price(key):
    with closing(db()) as c:
        return c.execute("SELECT price FROM prices WHERE service = ?", (key,)).fetchone()["price"]


def set_price(key, price):
    with closing(db()) as c:
        c.execute("UPDATE prices SET price = ? WHERE service = ?", (price, key))


def add_balance(uid, amount):
    with closing(db()) as c:
        c.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, uid))


def deduct(uid, amount):
    # চেক ও কাটা একসাথে, যাতে একই সময়ে দুই অর্ডারে ব্যালেন্স মাইনাস না হয়
    with closing(db()) as c:
        cur = c.execute(
            "UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ? AND banned = 0",
            (amount, uid, amount),
        )
        return cur.rowcount == 1


def set_banned(uid, flag):
    with closing(db()) as c:
        c.execute("UPDATE users SET banned = ? WHERE id = ?", (1 if flag else 0, uid))


def save_order(uid, service, link, qty, cost, panel_id):
    with closing(db()) as c:
        c.execute(
            """INSERT INTO orders (user_id, service, link, quantity, cost, panel_order, status, created)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, service, link, qty, cost, panel_id, "placed", now()),
        )


def user_orders(uid, limit=10):
    with closing(db()) as c:
        return c.execute(
            "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ?", (uid, limit)
        ).fetchall()


def create_code(code, amount, max_uses):
    with closing(db()) as c:
        try:
            c.execute(
                "INSERT INTO redeem_codes (code, amount, max_uses, created) VALUES (?, ?, ?, ?)",
                (code, amount, max_uses, now()),
            )
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
            c.execute("INSERT INTO redemptions (code, user_id, created) VALUES (?, ?, ?)", (code, uid, now()))
            c.execute("UPDATE redeem_codes SET used_count = used_count + 1 WHERE code = ?", (code,))
            c.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (row["amount"], uid))
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
# Helpers
# ============================================================
def h(text):
    return html.escape(str(text))


def unit(x):
    return f"{x:.4f}".rstrip("0").rstrip(".")


def is_admin(update):
    u = update.effective_user
    return bool(u) and u.id == ADMIN_ID


def admin_only(func):
    @wraps(func)
    async def wrapper(update, context):
        if not is_admin(update):
            return
        return await func(update, context)
    return wrapper


def price_block():
    lines = ["<b>💎 আমাদের প্রাইস</b>"]
    for key, s in SERVICES.items():
        lines.append(f"{s['emoji']} {s['name']}  ·  {CURRENCY}{unit(get_price(key))} / ১০০০")
    return "\n".join(lines)


def welcome_text():
    return (
        f"<b>✨ {h(BRAND)}-এ স্বাগতম</b>\n\n"
        f"{price_block()}\n\n"
        "নিচের অপশন থেকে বেছে নিন 👇"
    )


def main_menu():
    rows = [[InlineKeyboardButton(f"{s['emoji']}  {s['name']}", callback_data=f"svc:{key}")]
            for key, s in SERVICES.items()]
    rows.append([
        InlineKeyboardButton("💳 ব্যালেন্স", callback_data="balance"),
        InlineKeyboardButton("➕ ব্যালেন্স যুক্ত", callback_data="addfund"),
    ])
    rows.append([
        InlineKeyboardButton("🎁 রিডিম কোড", callback_data="redeem"),
        InlineKeyboardButton("📦 আমার অর্ডার", callback_data="orders"),
    ])
    rows.append([InlineKeyboardButton("🏠 Start", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def balance_text(uid):
    return f"💳 <b>আপনার ব্যালেন্স</b>\n\n{CURRENCY}{get_user(uid)['balance']:.2f}"


def add_fund_text(uid):
    lines = [
        "➕ <b>ব্যালেন্স যুক্ত করতে</b>",
        "",
        "নিচের অ্যাডমিনের সাথে যোগাযোগ করুন:",
        "",
        f"🆔 টেলিগ্রাম আইডি: <code>{ADMIN_ID}</code>",
    ]
    if ADMIN_USERNAME:
        lines.append(f"👤 ইউজারনেম: @{h(ADMIN_USERNAME)}")
        lines.append(f"🔗 লিংক: https://t.me/{h(ADMIN_USERNAME)}")
    lines += [
        "",
        f"আপনার আইডি: <code>{uid}</code>",
        "পেমেন্টের পর আপনার আইডিটি জানালে ব্যালেন্স যুক্ত করা হবে।",
    ]
    return "\n".join(lines)


def orders_text(uid):
    rows = user_orders(uid)
    if not rows:
        return "📦 আপনার এখনো কোনো অর্ডার নেই।"
    lines = ["📦 <b>আপনার সাম্প্রতিক অর্ডার</b>", ""]
    for r in rows:
        name = SERVICES[r["service"]]["name"] if r["service"] in SERVICES else r["service"]
        lines.append(f"🆔 <code>{h(r['panel_order'])}</code> · {h(name)} · {r['quantity']} · {CURRENCY}{r['cost']:.2f}")
    return "\n".join(lines)


async def notify_user(context, uid, text):
    try:
        await context.bot.send_message(uid, text)
    except Exception as e:
        log.warning("notify %s failed: %s", uid, e)


async def announce(context, text):
    try:
        await context.bot.send_message(CHANNEL, text)
    except Exception as e:
        log.error("channel post failed: %s", e)


# ============================================================
# Gate: ব্যান করা ইউজারকে আটকায়, সবার ইউজার তথ্য আপডেট রাখে
# ============================================================
async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None:
        return
    upsert_user(user)
    row = get_user(user.id)
    if row["banned"] and user.id != ADMIN_ID:
        if update.callback_query:
            await update.callback_query.answer("আপনার অ্যাকাউন্ট সীমিত করা হয়েছে।", show_alert=True)
        elif update.message:
            await update.message.reply_text("⛔ আপনার অ্যাকাউন্ট সীমিত করা হয়েছে। এই বট ব্যবহার করা যাবে না।")
        raise ApplicationHandlerStop


# ============================================================
# User commands
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(welcome_text(), reply_markup=main_menu())


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(balance_text(update.effective_user.id))


async def cmd_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(orders_text(update.effective_user.id))


async def cmd_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if context.args:
        await update.message.reply_text(redeem_message(*redeem(uid, context.args[0].strip().upper())))
        return
    context.user_data.clear()
    context.user_data["step"] = "redeem"
    await update.message.reply_text("🎁 আপনার রিডিম কোডটি লিখুন:")


def redeem_message(status, amount):
    if status == "ok":
        return f"🎉 <b>অভিনন্দন!</b>\n\n{CURRENCY}{amount:.2f} আপনার ব্যালেন্সে যুক্ত হয়েছে।"
    if status == "already":
        return "⚠️ আপনি এই কোডটি আগেই ব্যবহার করেছেন।"
    if status == "expired":
        return "⌛ এই কোডের মেয়াদ শেষ বা ব্যবহারের সীমা পূর্ণ হয়েছে।"
    return "❌ কোডটি সঠিক নয়। আবার চেষ্টা করুন।"


async def set_quantity(message, context, qty, uid):
    key = context.user_data["service"]
    s = SERVICES[key]
    if not (s["min"] <= qty <= s["max"]):
        await message.reply_text(f"⚠️ পরিমাণ {s['min']} থেকে {s['max']} এর মধ্যে হতে হবে।")
        return
    cost = round(qty / 1000 * get_price(key), 2)
    context.user_data.update(step="confirm", quantity=qty, cost=cost)
    balance = get_user(uid)["balance"]
    warn = "" if balance >= cost else "\n\n⚠️ ব্যালেন্স যথেষ্ট নয়। আগে ব্যালেন্স যুক্ত করুন।"
    text = (
        "<b>📋 অর্ডার সারাংশ</b>\n\n"
        f"🏷 সার্ভিস: {s['name']}\n"
        f"🔗 লিংক: {h(context.user_data['link'])}\n"
        f"📊 পরিমাণ: {qty}\n"
        f"💵 মূল্য: {CURRENCY}{cost:.2f}\n\n"
        f"💳 আপনার ব্যালেন্স: {CURRENCY}{balance:.2f}{warn}"
    )
    kb = [[
        InlineKeyboardButton("✅ কনফার্ম", callback_data="confirm"),
        InlineKeyboardButton("❌ বাতিল", callback_data="cancel"),
    ]]
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), disable_web_page_preview=True)


async def place_order(q, context):
    d = context.user_data
    if d.get("step") != "confirm":
        await q.message.reply_text("⚠️ কোনো অর্ডার পাওয়া যায়নি। /start দিয়ে আবার শুরু করুন।")
        return

    uid = q.from_user.id
    key, link, qty, cost = d["service"], d["link"], d["quantity"], d["cost"]
    context.user_data.clear()

    if not deduct(uid, cost):
        await q.message.reply_text("❌ ব্যালেন্স যথেষ্ট নয়। আগে ব্যালেন্স যুক্ত করুন।", reply_markup=main_menu())
        return

    s = SERVICES[key]
    try:
        resp = await panel({"action": "add", "service": s["service_id"], "link": link, "quantity": qty})
    except httpx.HTTPError as e:
        resp = {"error": str(e)}

    if "order" not in resp:
        add_balance(uid, cost)  # ব্যর্থ হলে টাকা ফেরত
        log.error("panel add failed: %s", resp)
        await notify_user(context, ADMIN_ID, f"⚠️ <b>প্যানেল অর্ডার ব্যর্থ</b>\n\nইউজার: <code>{uid}</code>\nসার্ভিস: {h(s['name'])}\nকারণ: {h(resp)}")
        await q.message.reply_text("❌ অর্ডার সম্পন্ন হয়নি। টাকা আপনার ব্যালেন্সে ফেরত দেওয়া হয়েছে। কিছুক্ষণ পর আবার চেষ্টা করুন।", reply_markup=main_menu())
        return

    panel_id = str(resp["order"])
    save_order(uid, key, link, qty, cost, panel_id)
    await q.message.reply_text(
        "✅ <b>অর্ডার সফল হয়েছে!</b>\n\n"
        f"🆔 অর্ডার আইডি: <code>{h(panel_id)}</code>\n"
        f"💵 কাটা হয়েছে: {CURRENCY}{cost:.2f}",
        reply_markup=main_menu(),
    )

    user = get_user(uid)
    await announce(
        context,
        "🆕 <b>নতুন অর্ডার</b>\n\n"
        f"🆔 অর্ডার: <code>{h(panel_id)}</code>\n"
        f"👤 ইউজার: {h(full_name(user))} (@{h(user['username'] or '-')})\n"
        f"🪪 আইডি: <code>{uid}</code>\n"
        f"🏷 সার্ভিস: {h(s['name'])}\n"
        f"🔗 লিংক: {h(link)}\n"
        f"📊 পরিমাণ: {qty}\n"
        f"💵 মূল্য: {CURRENCY}{cost:.2f}\n"
        f"🕒 {now()}",
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    if data == "home":
        context.user_data.clear()
        await q.message.reply_text(welcome_text(), reply_markup=main_menu())
    elif data == "balance":
        await q.message.reply_text(balance_text(uid))
    elif data == "addfund":
        await q.message.reply_text(add_fund_text(uid))
    elif data == "orders":
        await q.message.reply_text(orders_text(uid))
    elif data == "redeem":
        context.user_data.clear()
        context.user_data["step"] = "redeem"
        await q.message.reply_text("🎁 আপনার রিডিম কোডটি লিখুন:")
    elif data.startswith("svc:"):
        key = data.split(":", 1)[1]
        if key not in SERVICES:
            return
        context.user_data.clear()
        context.user_data.update(step="link", service=key)
        s = SERVICES[key]
        await q.message.reply_text(
            f"<b>{s['emoji']} {s['name']}</b>\n\n"
            f"💵 মূল্য: {CURRENCY}{unit(get_price(key))} প্রতি ১০০০\n"
            f"📏 সীমা: {s['min']} – {s['max']}\n\n"
            "🔗 আপনার পোস্ট বা চ্যানেলের টেলিগ্রাম লিংক পাঠান (t.me/...):"
        )
    elif data.startswith("qty:"):
        if context.user_data.get("step") == "qty":
            await set_quantity(q.message, context, int(data.split(":", 1)[1]), uid)
        else:
            await q.message.reply_text("⚠️ আগে সার্ভিস বেছে নিন।", reply_markup=main_menu())
    elif data == "confirm":
        await place_order(q, context)
    elif data == "cancel":
        context.user_data.clear()
        await q.message.reply_text("❌ অর্ডার বাতিল করা হয়েছে।", reply_markup=main_menu())


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = context.user_data
    step = d.get("step")
    if not step:
        return
    text = update.message.text.strip()
    uid = update.effective_user.id

    if step == "redeem":
        d.clear()
        await update.message.reply_text(redeem_message(*redeem(uid, text.upper())))
        return

    if step == "link":
        if "t.me/" not in text.lower():
            await update.message.reply_text("⚠️ সঠিক টেলিগ্রাম লিংক দিন, যেমন: https://t.me/...")
            return
        d.update(link=text, step="qty")
        key = d["service"]
        s = SERVICES[key]
        presets = [
            InlineKeyboardButton(str(n), callback_data=f"qty:{n}")
            for n in QTY_PRESETS if s["min"] <= n <= s["max"]
        ]
        kb = [presets[i:i + 2] for i in range(0, len(presets), 2)]
        await update.message.reply_text(
            f"📊 পরিমাণ লিখুন ({s['min']} – {s['max']}) অথবা নিচ থেকে বেছে নিন:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if step == "qty":
        if not text.isdigit():
            await update.message.reply_text("⚠️ শুধু সংখ্যা লিখুন।")
            return
        await set_quantity(update.message, context, int(text), uid)


# ============================================================
# Admin commands (শুধু ADMIN_ID-এর জন্য)
# ============================================================
def parse_uid_amount(args):
    try:
        return int(args[0]), float(args[1])
    except (IndexError, ValueError):
        return None


@admin_only
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>🛠 অ্যাডমিন প্যানেল</b>\n\n"
        "<b>ইউজার</b>\n"
        "#user বা /user — সব ইউজারের তালিকা\n"
        "/addbal &lt;user_id&gt; &lt;amount&gt; — ব্যালেন্স যোগ\n"
        "/subbal &lt;user_id&gt; &lt;amount&gt; — ব্যালেন্স কাটা\n"
        "/ban &lt;user_id&gt; — ব্যান\n"
        "/unban &lt;user_id&gt; — আনব্যান\n\n"
        "<b>প্রাইস ও প্যানেল</b>\n"
        "/setprice &lt;views|members&gt; &lt;price&gt; — প্রতি ১০০০-এর দাম\n"
        "/panelbal — প্যানেলের ব্যালেন্স\n\n"
        "<b>রিডিম কোড</b>\n"
        "/createcode &lt;CODE&gt; &lt;amount&gt; [max_uses] — কোড তৈরি (max_uses ০ = সীমাহীন)\n"
        "/delcode &lt;CODE&gt; — কোড বন্ধ\n"
        "/codes — সব কোডের তালিকা"
    )


@admin_only
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with closing(db()) as c:
        rows = c.execute("SELECT * FROM users ORDER BY joined DESC").fetchall()
    if not rows:
        await update.message.reply_text("এখনো কোনো ইউজার নেই।")
        return
    lines = [
        f"<code>{r['id']}</code> | {h(full_name(r))} | @{h(r['username'] or '-')} | {CURRENCY}{r['balance']:.2f} | {'🚫' if r['banned'] else '✅'}"
        for r in rows
    ]
    await update.message.reply_text(f"👥 <b>মোট ইউজার: {len(rows)}</b>")
    for i in range(0, len(lines), 30):
        await update.message.reply_text("\n".join(lines[i:i + 30]))


@admin_only
async def cmd_addbal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parsed = parse_uid_amount(context.args)
    if not parsed or parsed[1] <= 0:
        await update.message.reply_text("ব্যবহার: /addbal &lt;user_id&gt; &lt;amount&gt;")
        return
    uid, amt = parsed
    if not get_user(uid):
        await update.message.reply_text("❌ ইউজার পাওয়া যায়নি।")
        return
    add_balance(uid, amt)
    await update.message.reply_text(f"✅ {uid}-কে {CURRENCY}{amt:.2f} যোগ করা হয়েছে।\nনতুন ব্যালেন্স: {CURRENCY}{get_user(uid)['balance']:.2f}")
    await notify_user(context, uid, f"➕ আপনার অ্যাকাউন্টে {CURRENCY}{amt:.2f} যুক্ত করা হয়েছে।\nবর্তমান ব্যালেন্স: {CURRENCY}{get_user(uid)['balance']:.2f}")


@admin_only
async def cmd_subbal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parsed = parse_uid_amount(context.args)
    if not parsed or parsed[1] <= 0:
        await update.message.reply_text("ব্যবহার: /subbal &lt;user_id&gt; &lt;amount&gt;")
        return
    uid, amt = parsed
    if not get_user(uid):
        await update.message.reply_text("❌ ইউজার পাওয়া যায়নি।")
        return
    add_balance(uid, -amt)
    await update.message.reply_text(f"✅ {uid} থেকে {CURRENCY}{amt:.2f} কাটা হয়েছে।\nনতুন ব্যালেন্স: {CURRENCY}{get_user(uid)['balance']:.2f}")
    await notify_user(context, uid, f"➖ আপনার অ্যাকাউন্ট থেকে {CURRENCY}{amt:.2f} কাটা হয়েছে।\nবর্তমান ব্যালেন্স: {CURRENCY}{get_user(uid)['balance']:.2f}")


async def _set_ban(update, context, flag):
    try:
        uid = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("ব্যবহার: /ban &lt;user_id&gt; বা /unban &lt;user_id&gt;")
        return
    if not get_user(uid):
        await update.message.reply_text("❌ ইউজার পাওয়া যায়নি।")
        return
    set_banned(uid, flag)
    await update.message.reply_text(f"✅ {uid} {'ব্যান' if flag else 'আনব্যান'} করা হয়েছে।")


@admin_only
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_ban(update, context, True)


@admin_only
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_ban(update, context, False)


@admin_only
async def cmd_setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        key = context.args[0].lower()
        price = float(context.args[1])
        assert key in SERVICES and price > 0
    except (IndexError, ValueError, AssertionError):
        await update.message.reply_text("ব্যবহার: /setprice &lt;views|members&gt; &lt;price&gt;")
        return
    set_price(key, price)
    await update.message.reply_text(f"✅ {SERVICES[key]['name']}: প্রতি ১০০০ = {CURRENCY}{unit(price)}")


@admin_only
async def cmd_panelbal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        resp = await panel({"action": "balance"})
        await update.message.reply_text(f"🏦 প্যানেল ব্যালেন্স: {h(resp.get('balance', '?'))} {h(resp.get('currency', ''))}")
    except httpx.HTTPError as e:
        await update.message.reply_text(f"❌ প্যানেলে সংযোগ হয়নি: {h(e)}")


@admin_only
async def cmd_createcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        code = context.args[0].upper()
        amount = float(context.args[1])
        max_uses = int(context.args[2]) if len(context.args) > 2 else 0
        assert re.fullmatch(r"[A-Z0-9_]{3,32}", code) and amount > 0 and max_uses >= 0
    except (IndexError, ValueError, AssertionError):
        await update.message.reply_text("ব্যবহার: /createcode &lt;CODE&gt; &lt;amount&gt; [max_uses]\nউদাহরণ: /createcode EID50 50 100")
        return
    if not create_code(code, amount, max_uses):
        await update.message.reply_text("⚠️ এই কোডটি আগে থেকেই আছে।")
        return
    limit = "সীমাহীন" if max_uses == 0 else str(max_uses)
    await update.message.reply_text(
        f"✅ কোড তৈরি হয়েছে\n\n🎁 কোড: <code>{h(code)}</code>\n💵 মূল্য: {CURRENCY}{amount:.2f}\n👥 ব্যবহার সীমা: {limit}\n\nপ্রতি ইউজার একবারই ব্যবহার করতে পারবে।"
    )


@admin_only
async def cmd_delcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("ব্যবহার: /delcode &lt;CODE&gt;")
        return
    code = context.args[0].upper()
    if deactivate_code(code):
        await update.message.reply_text(f"✅ কোড <code>{h(code)}</code> বন্ধ করা হয়েছে।")
    else:
        await update.message.reply_text("❌ কোডটি পাওয়া যায়নি।")


@admin_only
async def cmd_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = list_codes()
    if not rows:
        await update.message.reply_text("এখনো কোনো রিডিম কোড নেই।")
        return
    lines = ["<b>🎁 রিডিম কোড তালিকা</b>", ""]
    for r in rows:
        limit = "∞" if r["max_uses"] == 0 else str(r["max_uses"])
        status = "🟢" if r["active"] and (r["max_uses"] == 0 or r["used_count"] < r["max_uses"]) else "🔴"
        lines.append(f"{status} <code>{h(r['code'])}</code> · {CURRENCY}{r['amount']:.2f} · ব্যবহার {r['used_count']}/{limit}")
    await update.message.reply_text("\n".join(lines))


# ============================================================
# Startup: কমান্ড মেনু স্বয়ংক্রিয়ভাবে সেট হয়
# ============================================================
PUBLIC_COMMANDS = [
    BotCommand("start", "মেনু খুলুন"),
    BotCommand("balance", "আমার ব্যালেন্স দেখুন"),
    BotCommand("redeem", "রিডিম কোড ব্যবহার করুন"),
    BotCommand("orders", "আমার অর্ডার দেখুন"),
]

ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    BotCommand("admin", "অ্যাডমিন কমান্ড তালিকা"),
    BotCommand("users", "সব ইউজারের তালিকা"),
    BotCommand("addbal", "ইউজারের ব্যালেন্স যোগ"),
    BotCommand("subbal", "ইউজারের ব্যালেন্স কাটা"),
    BotCommand("ban", "ইউজার ব্যান"),
    BotCommand("unban", "ইউজার আনব্যান"),
    BotCommand("setprice", "প্রাইস পরিবর্তন"),
    BotCommand("panelbal", "প্যানেলের ব্যালেন্স"),
    BotCommand("createcode", "রিডিম কোড তৈরি"),
    BotCommand("delcode", "রিডিম কোড বন্ধ"),
    BotCommand("codes", "রিডিম কোডের তালিকা"),
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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(CommandHandler("redeem", cmd_redeem))

    app.add_handler(CommandHandler("admin", admin_help))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("addbal", cmd_addbal))
    app.add_handler(CommandHandler("subbal", cmd_subbal))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("setprice", cmd_setprice))
    app.add_handler(CommandHandler("panelbal", cmd_panelbal))
    app.add_handler(CommandHandler("createcode", cmd_createcode))
    app.add_handler(CommandHandler("delcode", cmd_delcode))
    app.add_handler(CommandHandler("codes", cmd_codes))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^[#/]user\b"), cmd_users))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    app.run_polling()


if __name__ == "__main__":
    main()

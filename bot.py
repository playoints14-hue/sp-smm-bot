import asyncio
import html
import logging
import os
import re
import time
from datetime import timedelta

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

from config import *  # noqa: F401,F403  (BOT_TOKEN, ADMIN_ID, USER_CHANNEL ... )
from db import *  # noqa: F401,F403
from i18n import both, fancy, tr
from logo import make_logo_png
from media import LABELS, SCREENS, clear_voice, image_ref, screen_default, set_image, set_voice_media, voice_ref

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sp-panel")

ADMIN_PREFIXES = ("adm:", "cat:", "catrefill:", "catlogo:", "catsvc:", "catdel:", "newcat_refill:",
                  "chset:", "gen:", "svcprice:", "svcdel:", "svcphoto:", "svcemo:",
                  "scr:", "imgset:", "imgdel:", "voiceset:", "voicedel:", "svcvoice:")


# ============================================================
# Basic helpers
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


def card(title, body, hint=None, icon="💠", lang="bn"):
    """সব বার্তার একই ডিজাইন: আইকন + শিরোনাম + ডিভাইডার + ব্লককোট বডি।
    ইংরেজিতে শিরোনাম বোল্ড ফন্টে দেখায়।"""
    t = h(fancy(title) if lang == "en" else title)
    out = f"{icon} <b>{t}</b>\n{DIV}\n<blockquote>{body}</blockquote>"
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
    async def wrapper(update, context):
        if not is_admin(update):
            return
        return await func(update, context)
    return wrapper


def L(uid):
    """ইউজারের ভাষা। অ্যাডমিনসহ যাদের সেট নেই, তাদের জন্য বাংলা।"""
    return user_lang(uid) or "bn"


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
    return f"{h(name)} · {idline}"


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


def user_channel_text(label, user, srv, link, qty, cost, when, lang="bn"):
    """চ্যানেল ১ (সকল ইউজার): পোস্ট ক্রেতার নিজের ভাষায়। লিংক লুকানো থাকে।"""
    who = f"<code>{user['id']}</code>"  # পাবলিক চ্যানেলে ইউজারনেম নয়, শুধু আইডি
    return (
        f"{tr(lang, 'ch_new_order')}\n"
        f"{DIV}\n"
        f"{tr(lang, 'ch_order_no')}: <code>{h(label)}</code>\n"
        f"{tr(lang, 'ch_buyer')}: {who}\n"
        f"<blockquote>{tr(lang, 'ch_category')}: <b>{h(srv['platform'] or '—')}</b>\n"
        f"{tr(lang, 'ch_service')}: <b>{h(srv['name'])}</b>\n"
        f"{tr(lang, 'ch_link')}: {tr(lang, 'ch_link_hidden')}\n"
        f"{tr(lang, 'ch_qty')}: <b>{qty}</b></blockquote>\n"
        f"{tr(lang, 'ch_cost')}: <b>{money(cost)}</b>\n"
        f"{tr(lang, 'ch_time')}: {h(when)}"
    )


def admin_channel_text(label, panel_id, user, srv, link, qty, cost, when, chan_line, balance):
    """চ্যানেল ২ (শুধু অ্যাডমিন): ইউজারনেম/আইডি, প্যানেল অর্ডার আইডি, পূর্ণ লিংক ও বিবরণ।"""
    if user["username"]:
        who = f"@{h(user['username'])}"
        if user["id"]:
            who += f"  ·  <code>{user['id']}</code>"
    else:
        who = f"<code>{user['id']}</code>"
    return (
        "📑 <b>বিস্তারিত অর্ডার রিপোর্ট  ·  Order report</b>\n"
        f"{DIV}\n"
        f"👤 ইউজার: {who}\n"
        f"🔢 ওয়ার্ডার আইডি: <code>{h(panel_id)}</code>\n"
        f"🧾 বট নম্বর: <code>{h(label)}</code>\n"
        f"<blockquote>📂 ক্যাটাগরি: <b>{h(srv['platform'] or '—')}</b>\n"
        f"🛍 সার্ভিস: <b>{h(srv['name'])}</b>  ·  আইডি <code>{h(srv['panel_id'])}</code>\n"
        f"🔗 লিংক: {h(link)}\n"
        f"📊 কোয়ান্টিটি: <b>{qty}</b></blockquote>\n"
        f"💵 মূল্য: <b>{money(cost)}</b>\n"
        f"💰 ইউজারের বাকি ব্যালেন্স: {money(balance)}\n"
        f"📢 লিংকের চ্যানেল: {chan_line}\n"
        f"🕒 সময়: {h(when)}"
    )


async def post_order_channels(context, label, user, srv, link, qty, cost, panel_id):
    when = now_str()
    public = channel_setting("public")
    detail = channel_setting("detail")
    if public:
        buyer_lang = user_lang(user["id"]) or "bn"
        await send_safe(context, public, user_channel_text(label, user, srv, link, qty, cost, when, buyer_lang))
    if detail:
        chan_line = await describe_link_channel(context, link)
        bal = get_user(user["id"])["balance"]
        await send_safe(context, detail,
                        admin_channel_text(label, panel_id, user, srv, link, qty, cost, when, chan_line, bal))


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
        rows.append([InlineKeyboardButton("🔗 চ্যানেলে জয়েন · Join channel", url=link)])
    rows.append([btn(tr("bn", "btn_joined"), "joincheck")])
    return InlineKeyboardMarkup(rows)


JOIN_TEXT = card(tr("bn", "join_title"), both("join_body"), icon="🔐")
LANG_TEXT = card(tr("bn", "lang_title"), both("lang_body"), icon="🌐")


def lang_markup():
    return InlineKeyboardMarkup([[
        btn(tr("bn", "btn_lang_bn"), "lang:bn"),
        btn(tr("bn", "btn_lang_en"), "lang:en"),
    ]])


# ============================================================
# UI builders
# ============================================================
def main_menu(lang="bn"):
    rows = [[btn(f"{cat_emoji(c['name'])}  {c['name']}", f"cat_u:{c['name']}")] for c in list_categories()]
    rows.append([btn(tr(lang, "btn_balance"), "balance"), btn(tr(lang, "btn_deposit"), "deposit")])
    rows.append([btn(tr(lang, "btn_redeem"), "redeem"), btn(tr(lang, "btn_orders"), "orders")])
    rows.append([btn(tr(lang, "btn_language"), "lang_menu"), btn(tr(lang, "btn_menu"), "home")])
    return InlineKeyboardMarkup(rows)


def after_order_markup(oid, lang="bn"):
    """অর্ডার সফল হওয়ার পর: প্রথমে 'পুনরায় এই অর্ডারটি করুন', তারপর বিস্তারিত ও মেনু।"""
    return InlineKeyboardMarkup([
        [btn(tr(lang, "btn_again"), f"again:{oid}")],
        [btn(tr(lang, "btn_report"), f"report:{oid}"), btn(tr(lang, "btn_menu"), "home")],
    ])


def greet_name(user):
    if user.username:
        return f"@{user.username}"
    name = " ".join(p for p in (user.first_name, user.last_name) if p)
    return name.strip() or "বন্ধু"


def welcome_text(user, lang="bn"):
    b = brand()
    head = f"📡 <b>{h(fancy(b) if lang == 'en' and b else (b or 'SMS PANEL'))}</b>"
    lines = [
        head,
        f"<i>{tr(lang, 'welcome_tag')}</i>",
        DIV,
        tr(lang, "welcome_hi", name=h(greet_name(user))),
        "",
        f"◈ <b>{tr(lang, 'price_head')}</b>",
    ]
    rows = []
    for cat in list_categories():
        for s in list_services(platform=cat["name"]):
            rows.append(tr(lang, "price_line", emoji=s["emoji"], title=h(service_title(s)),
                           price=money(s["price"])))
    if rows:
        lines.append("<blockquote>" + "\n".join(rows) + "</blockquote>")
    else:
        lines.append(f"<blockquote>{tr(lang, 'price_empty')}</blockquote>")
    lines += [DIV, f"<i>{tr(lang, 'menu_hint')}</i>"]
    return "\n".join(lines)


def cancel_kb(lang="bn"):
    return InlineKeyboardMarkup([[btn(tr(lang, "btn_cancel"), "flowcancel")]])


def voice_ask_text(lang):
    return card(tr(lang, "voice_title"), tr(lang, "voice_body"), icon="🔊", lang=lang)


def voice_markup(lang):
    return InlineKeyboardMarkup([[btn(tr(lang, "btn_voice_on"), "voice:on"),
                                  btn(tr(lang, "btn_voice_off"), "voice:off")]])


def settings_text(lang, uid):
    state = tr(lang, "voice_now_on" if voice_pref(uid) == 1 else "voice_now_off")
    return card(tr(lang, "settings_title"), f"{tr(lang, 'settings_body')}\n\n{state}", icon="⚙️", lang=lang)


def settings_markup(lang, uid):
    voice_btn = (btn(tr(lang, "btn_voice_off"), "voice:off") if voice_pref(uid) == 1
                 else btn(tr(lang, "btn_voice_on"), "voice:on"))
    return InlineKeyboardMarkup([
        [btn(tr(lang, "btn_lang_bn"), "lang:bn"), btn(tr(lang, "btn_lang_en"), "lang:en")],
        [voice_btn],
        [btn(tr(lang, "btn_menu"), "home")],
    ])


async def send_start_screens(context, chat_id, user, lang):
    """ভয়েস পছন্দ এখনো না জানা থাকলে আগে তা জিজ্ঞেস করে, নাহলে মেনু দেখায়।"""
    if voice_pref(chat_id) is None:
        await say(context, chat_id, "voice_ask", voice_ask_text(lang), voice_markup(lang))
        return
    await say(context, chat_id, "welcome", welcome_text(user, lang), main_menu(lang))


# ---------- Loading (ব্র্যান্ডেড স্পিনার + ২০ ঘরের প্রগ্রেস বার) ----------
LOAD_STAGES = ["🔐 যাচাই হচ্ছে", "📡 প্যানেলে পাঠানো হচ্ছে", "✅ নিশ্চিত হচ্ছে"]
LOAD_DONE = "✅ সম্পন্ন হয়েছে"
# (শতাংশ, ধাপের নাম): প্রথম ধাপে কাজের নাম, পরে প্যানেলের ধাপগুলো
LOAD_STEPS = [(15, 0), (45, 0), (70, 1), (90, 2), (100, 3)]


def panel_head():
    """উপরের ব্র্যান্ড লাইন: অ্যাডমিনের দেওয়া নাম, না থাকলে SMS PANEL।"""
    return f"📡 <b>{h(brand() or 'SMS PANEL')}</b>"


def loading_frames(label):
    """ব্র্যান্ড লাইন, ধাপের নাম, প্রগ্রেস বার ও শতাংশ। শেষে ✅ সম্পন্ন।"""
    frames = []
    for pct, stage in LOAD_STEPS:
        filled = round(pct / 5)
        bar = "▰" * filled + "▱" * (20 - filled)
        if stage == 0:
            text = h(label)
        elif stage == 3:
            text = LOAD_DONE
        else:
            text = LOAD_STAGES[stage]
        frames.append(f"{panel_head()}\n<b>{text}</b>\n<code>{bar}</code>  <b>{pct}%</b>")
    return frames


async def show_loading(context, chat_id, label, delay=0.2):
    """অ্যাডমিনের দেওয়া GIF/ভিডিও থাকলে সেটা, নাহলে স্পিনার + প্রগ্রেস বার।"""
    frames = loading_frames(label)
    anim = get_setting("loading_anim")
    kind = get_setting("loading_kind") or "animation"
    msg = None
    is_media = False
    if anim:
        try:
            sender = context.bot.send_video if kind == "video" else context.bot.send_animation
            msg = await sender(chat_id, anim, caption=f"⏳ {h(label)}")
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


async def notify_key(context, uid, key, **kw):
    """ইউজারের নিজের ভাষায় বিজ্ঞপ্তি পাঠায়।"""
    await notify_user(context, uid, tr(L(uid), key, **kw))


# ============================================================
# Screen sender: ছবি + লেখা + (ভয়েস চালু থাকলে) ভয়েস
# ============================================================
_photo_cache = {}  # লোকাল ডিফল্ট ছবির path -> Telegram file_id (একবার আপলোড হলেই যথেষ্ট)


async def _send_photo(context, chat_id, ref, caption, markup):
    if os.path.isfile(ref):
        if ref in _photo_cache:
            return await context.bot.send_photo(chat_id, _photo_cache[ref], caption=caption, reply_markup=markup)
        with open(ref, "rb") as f:
            msg = await context.bot.send_photo(chat_id, f, caption=caption, reply_markup=markup)
        _photo_cache[ref] = msg.photo[-1].file_id
        return msg
    return await context.bot.send_photo(chat_id, ref, caption=caption, reply_markup=markup)


async def _send_voice(context, chat_id, scopes):
    found = voice_ref(scopes)
    if not found:
        return
    kind, fid = found
    try:
        if kind == "audio":
            await context.bot.send_audio(chat_id, fid)
        else:
            await context.bot.send_voice(chat_id, fid)
    except Exception as e:
        log.warning("voice send failed (%s): %s", scopes, e)


async def say(context, chat_id, key, text, markup=None, photo=None, voice_scopes=()):
    """একটি ফিচার-স্ক্রিন পাঠায়: ছবি (অ্যাডমিনের দেওয়া, নাহলে ডিফল্ট) + লেখা।
    লেখা বড় হলে ছবি আলাদা যায় আর লেখা পরে যায়। ইউজার ভয়েস চালু রাখলে
    এই স্ক্রিনের ভয়েস বার্তাও যায়। ব্যক্তিগত চ্যাটে chat_id-ই ইউজারের আইডি।"""
    ref = photo or image_ref(key) or screen_default(key)
    short = len(text) <= 900
    sent = None
    try:
        sent = await _send_photo(context, chat_id, ref, text if short else None, markup if short else None)
    except Exception as e:
        log.warning("screen photo failed (%s): %s", key, e)
        sent = None
    if sent is None or not short:
        sent = await context.bot.send_message(chat_id, text, reply_markup=markup,
                                              link_preview_options=LinkPreviewOptions(is_disabled=True))
    if voice_pref(chat_id) == 1:
        await _send_voice(context, chat_id, (*voice_scopes, key))
    return sent


# ============================================================
# Gate: ব্যান, বাধ্যতামূলক জয়েন, ভাষা বাছাই
# ============================================================
async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None:
        return
    upsert_user(user)
    if is_admin_id(user.id):
        return
    row = get_user(user.id)
    lang = row["lang"] if row and row["lang"] else None
    ui_lang = lang or "bn"

    if row and row["is_banned"]:
        until = parse_dt(row["banned_until"])
        if until and now_dt() >= until:
            set_ban(user.id, None, 0)
        else:
            text = (tr(ui_lang, "banned_until", until=fmt_time(row["banned_until"]))
                    if until else tr(ui_lang, "banned_perm"))
            if update.callback_query:
                await update.callback_query.answer(text, show_alert=True)
            elif update.message:
                await update.message.reply_text(text)
            raise ApplicationHandlerStop

    chat_id = update.effective_chat.id if update.effective_chat else user.id
    if not await is_member(context.bot, user.id):
        if update.callback_query:
            await update.callback_query.answer(both("join_alert", " · "), show_alert=True)
        await say(context, chat_id, "join", JOIN_TEXT, join_markup())
        raise ApplicationHandlerStop

    data = update.callback_query.data if update.callback_query else ""
    if lang is None and not (data or "").startswith("lang:"):
        if update.callback_query:
            await update.callback_query.answer()
        await say(context, chat_id, "lang", LANG_TEXT, lang_markup())
        raise ApplicationHandlerStop


# ============================================================
# Commands
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("flow", None)
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    lang = L(uid)
    loader = await show_loading(context, chat_id, tr(lang, "load_prep"))
    await finish_loading(loader)
    await send_start_screens(context, chat_id, update.effective_user, lang)


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    context.user_data.pop("flow", None)
    await update.message.reply_text(**admin_home())


@admin_only
async def cmd_users_regex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_users(context, update.message.chat.id)


# ============================================================
# User views
# ============================================================
def order_line(r):
    return (f"🔹 <code>{h(order_label(r))}</code>  {h(r['service_name'])}  ·  "
            f"{r['quantity']}  ·  {money(r['cost'])}")


def refill_text(row, lang="bn"):
    if row["refill_id"]:
        return tr(lang, "refill_done", rid=h(row["refill_id"]))
    if refill_eligible(row):
        return tr(lang, "refill_active", days=refill_days_for(row))
    return tr(lang, "refill_none")


async def show_category(context, chat_id, name, lang):
    srvs = list_services(platform=name)
    if not srvs:
        await context.bot.send_message(
            chat_id, card(tr(lang, "cat_empty_title"), tr(lang, "cat_empty_body", name=h(name)),
                          icon="⚠️", lang=lang),
            reply_markup=main_menu(lang))
        return
    lines = [tr(lang, "svc_list_line", emoji=s["emoji"], name=h(s["name"]),
                price=money(s["price"]), min=s["min_qty"]) for s in srvs]
    text = card(f"{cat_emoji(name)} {name}", "\n".join(lines), hint=tr(lang, "cat_hint"), icon="📂", lang=lang)
    kb = [[btn(f"{s['emoji']}  {s['name']}", f"svc:{s['key']}")] for s in srvs]
    kb.append([btn(tr(lang, "btn_back"), "home")])
    markup = InlineKeyboardMarkup(kb)
    await say(context, chat_id, "category", text, markup, photo=category_logo(name),
              voice_scopes=(f"cat:{name}",))


async def send_qty_prompt(context, chat_id, srv, lang):
    mx = srv["max_qty"]
    if mx:
        limit = tr(lang, "qty_body_minmax", min=srv["min_qty"], max=mx)
    else:
        limit = tr(lang, "qty_body_min", min=srv["min_qty"])
    presets = [n for n in QTY_PRESETS if n >= srv["min_qty"] and (mx == 0 or n <= mx)]
    kb = [[btn(str(n), f"qty:{n}") for n in presets[i:i + 2]] for i in range(0, len(presets), 2)]
    kb.append([btn(tr(lang, "btn_cancel"), "flowcancel")])
    await say(context, chat_id, "qty",
              card(tr(lang, "qty_title"), limit, hint=tr(lang, "step2_hint"), icon="📊", lang=lang),
              InlineKeyboardMarkup(kb))


async def show_service(context, chat_id, srv, lang):
    body = tr(lang, "svc_info", price=money(srv["price"]), min=srv["min_qty"],
              max=(srv["max_qty"] or tr(lang, "unlimited")))
    text = card(service_title(srv), body, hint=tr(lang, "step1_hint"), icon=srv["emoji"], lang=lang)
    photo = srv["photo_id"] or (category_logo(srv["platform"]) if srv["platform"] else None)
    await say(context, chat_id, "service", text, cancel_kb(lang), photo=photo,
              voice_scopes=(f"svc:{srv['key']}",))


async def start_order_flow(context, chat_id, uid, srv, lang):
    if not srv or not srv["active"]:
        context.user_data.pop("flow", None)
        await context.bot.send_message(
            chat_id, card(tr(lang, "svc_gone_title"), tr(lang, "svc_gone_body"), icon="⚠️", lang=lang),
            reply_markup=main_menu(lang))
        return
    context.user_data["flow"] = {"name": "order", "step": "link", "data": {"key": srv["key"]}}
    await show_service(context, chat_id, srv, lang)


async def set_qty_summary(context, chat_id, flow, qty, uid, lang):
    d = flow["data"]
    srv = get_service(d["key"])
    if not srv or not srv["active"]:
        context.user_data.pop("flow", None)
        await context.bot.send_message(
            chat_id, card(tr(lang, "svc_gone_title"), tr(lang, "svc_gone_body"), icon="⚠️", lang=lang),
            reply_markup=main_menu(lang))
        return
    if qty < srv["min_qty"] or (srv["max_qty"] and qty > srv["max_qty"]):
        msg = (tr(lang, "qty_range", min=srv["min_qty"], max=srv["max_qty"]) if srv["max_qty"]
               else tr(lang, "qty_range_min", min=srv["min_qty"]))
        await context.bot.send_message(chat_id, msg)
        return
    cost = round(qty * srv["price"] / 1000, 4)
    d.update(qty=qty, cost=cost)
    flow["step"] = "confirm"
    balance = get_user(uid)["balance"]
    after = balance - cost
    warn = "" if balance >= cost else tr(lang, "low_balance_note")
    body = tr(lang, "summary_body", cat=h(srv["platform"] or "—"), svc=h(srv["name"]), link=h(d["link"]),
              qty=qty, cost=money(cost), bal=money(balance),
              after=money(after) if after >= 0 else "—")
    text = card(tr(lang, "summary_title"), body, hint=tr(lang, "step3_hint"), icon="📋", lang=lang) + warn
    kb = InlineKeyboardMarkup([
        [btn(tr(lang, "btn_confirm"), "order_confirm"), btn(tr(lang, "btn_decline"), "order_cancel")],
        [copy_btn(tr(lang, "btn_copy_link"), d["link"])],
    ])
    await say(context, chat_id, "summary", text, kb)


_order_locks = {}


def _lock_for(uid):
    if uid not in _order_locks:
        _order_locks[uid] = asyncio.Lock()
    return _order_locks[uid]


async def strip_order_buttons(q):
    """সারাংশ কার্ডের 'নিশ্চিত করুন' ও 'বাতিল' বাটন সরায়।
    লেখা ও 'লিংক কপি' বাটন যেমন আছে তেমনই থাকে। ফলে একই বাটন দুইবার চাপার সুযোগ থাকে না।"""
    try:
        markup = q.message.reply_markup
        rows = []
        for row in (markup.inline_keyboard if markup else []):
            if any(getattr(b, "callback_data", None) in ("order_confirm", "order_cancel") for b in row):
                continue
            rows.append(row)
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows) if rows else None)
    except BadRequest:
        pass
    except Exception as e:
        log.warning("strip order buttons failed: %s", e)


async def place_order(q, context, flow):
    d = flow["data"]
    context.user_data.pop("flow", None)
    uid = q.from_user.id
    chat_id = q.message.chat.id
    lang = L(uid)
    srv = get_service(d["key"])
    if not srv or not srv["active"]:
        await context.bot.send_message(
            chat_id, card(tr(lang, "svc_gone_title"), tr(lang, "svc_gone_body"), icon="⚠️", lang=lang),
            reply_markup=main_menu(lang))
        return
    if not charge_order(uid, d["cost"]):
        await context.bot.send_message(
            chat_id, card(tr(lang, "low_balance_title"), tr(lang, "low_balance_body"), icon="❌", lang=lang),
            reply_markup=main_menu(lang))
        return

    loader = await show_loading(context, chat_id, tr(lang, "load_order"))
    try:
        resp = await panel({"action": "add", "service": str(srv["panel_id"]), "link": d["link"],
                            "quantity": d["qty"]})
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
            chat_id, card(tr(lang, "order_fail_title"), tr(lang, "order_fail_body"), icon="❌", lang=lang),
            reply_markup=main_menu(lang))
        return

    panel_id = str(resp["order"])
    try:
        oid, label = save_order(uid, srv, d["link"], d["qty"], d["cost"], panel_id)
    except Exception:
        log.exception("save_order failed after panel order %s", panel_id)
        await finish_loading(loader)
        await notify_user(context, ADMIN_ID, card("অর্ডার সেভ হয়নি",
                          f"প্যানেল অর্ডার: <code>{h(panel_id)}</code>\nইউজার: <code>{uid}</code>", icon="⚠️"))
        await context.bot.send_message(
            chat_id, card(tr(lang, "order_saved_title"), tr(lang, "order_saved_body"), icon="✅", lang=lang),
            reply_markup=main_menu(lang))
        return

    await finish_loading(loader)
    bal = get_user(uid)["balance"]
    body = tr(lang, "order_ok_body", no=h(label), cat=h(srv["platform"] or "—"), svc=h(srv["name"]),
              qty=d["qty"], cost=money(d["cost"]), bal=money(bal), time=h(now_str()))
    try:
        await say(context, chat_id, "success",
                  card(tr(lang, "order_ok_title"), body, hint=tr(lang, "success_hint"), icon="✅", lang=lang),
                  after_order_markup(oid, lang))
    except Exception:
        log.exception("success message failed (order %s)", label)

    try:
        user = get_user(uid)
        await post_order_channels(context, label, user, srv, d["link"], d["qty"], d["cost"], panel_id)
    except Exception:
        log.exception("channel posting failed (order %s)", label)


def redeem_message(status, amount, lang):
    if status == "ok":
        return card(tr(lang, "redeem_ok_title"), tr(lang, "redeem_ok_body", amt=money(amount)), icon="🎉", lang=lang)
    if status == "already":
        return card(tr(lang, "redeem_used_title"), tr(lang, "redeem_used_body"), icon="⚠️", lang=lang)
    if status == "expired":
        return card(tr(lang, "redeem_expired_title"), tr(lang, "redeem_expired_body"), icon="⌛", lang=lang)
    return card(tr(lang, "redeem_bad_title"), tr(lang, "redeem_bad_body"), icon="❌", lang=lang)


async def do_redeem(context, chat_id, uid, code):
    lang = L(uid)
    loader = await show_loading(context, chat_id, tr(lang, "load_code"))
    status, amount = redeem(uid, code)
    await finish_loading(loader)
    await say(context, chat_id, "redeem", redeem_message(status, amount, lang), main_menu(lang))


async def send_report(context, chat_id, row, lang):
    body = tr(lang, "report_body", svc=h(row["service_name"]), qty=row["quantity"],
              cost=money(row["cost"]), time=h(fmt_time(row["created"])), refill=refill_text(row, lang))
    if refill_eligible(row):
        deadline = refill_deadline(row)
        body += "\n" + tr(lang, "refill_timer", date=deadline.strftime("%d %b %Y, %I:%M %p"))
    text = card(tr(lang, "report_title", no=order_label(row)), body, icon="📝", lang=lang)
    rows = []
    if refill_eligible(row):
        rows.append([btn(tr(lang, "btn_refill"), f"refill:{row['id']}")])
    rows.append([btn(tr(lang, "btn_menu"), "home")])
    await say(context, chat_id, "report", text, InlineKeyboardMarkup(rows))


async def do_refill(context, chat_id, uid, oid):
    lang = L(uid)
    row = get_order(oid)
    if not row or row["user_id"] != uid:
        await context.bot.send_message(
            chat_id, card(tr(lang, "order_forbidden_title"), tr(lang, "order_forbidden_body"), icon="⚠️", lang=lang),
            reply_markup=main_menu(lang))
        return
    if not refill_eligible(row):
        await context.bot.send_message(
            chat_id, card(tr(lang, "refill_unavailable_title"), tr(lang, "refill_unavailable_body"),
                          icon="⚠️", lang=lang),
            reply_markup=main_menu(lang))
        return
    loader = await show_loading(context, chat_id, tr(lang, "load_refill"))
    try:
        resp = await panel({"action": "refill", "order": row["panel_order"]})
    except httpx.HTTPError as e:
        resp = {"error": str(e)}
    await finish_loading(loader)
    if "refill" in resp:
        set_refill(oid, str(resp["refill"]))
        await say(context, chat_id, "refill",
                  card(tr(lang, "refill_ok_title"),
                       tr(lang, "refill_ok_body", no=h(order_label(row)), rid=h(resp["refill"])),
                       icon="✅", lang=lang),
                  main_menu(lang))
    else:
        log.error("refill failed: %s", resp)
        await context.bot.send_message(
            chat_id, card(tr(lang, "refill_fail_title"), tr(lang, "refill_fail_body"), icon="❌", lang=lang),
            reply_markup=main_menu(lang))


async def send_orders_list(context, chat_id, uid, lang):
    rows = user_orders(uid)
    if not rows:
        await say(context, chat_id, "orders",
                  card(tr(lang, "orders_title"), tr(lang, "orders_empty"), icon="📦", lang=lang),
                  main_menu(lang))
        return
    body = "\n".join(order_line(r) for r in rows)
    kb = [[btn(f"📝 {order_label(r)}", f"report:{r['id']}")] for r in rows[:5]]
    kb.append([btn(tr(lang, "btn_menu"), "home")])
    await say(context, chat_id, "orders", card(tr(lang, "orders_title"), body, icon="📦", lang=lang),
              InlineKeyboardMarkup(kb))


def balance_text(uid, lang):
    row = get_user(uid)
    st = user_stats(uid)
    body = tr(lang, "balance_body", bal=money(row["balance"]), dep=money(st["deposits"]),
              spent=money(st["spent"]), red=money(st["redeems"]), n=st["orders"])
    return card(tr(lang, "balance_title"), body, icon="💳", lang=lang)


def deposit_text(uid, lang):
    sup = support_username()
    contact = tr(lang, "contact_support", sup=h(sup)) if sup else tr(lang, "contact_none")
    body = f"{tr(lang, 'deposit_body')}\n\n{contact}\n{tr(lang, 'your_id', uid=uid)}"
    return card(tr(lang, "deposit_title"), body, icon="➕", lang=lang)


# ============================================================
# Callbacks (ইউজার)
# ============================================================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat.id
    lang = L(q.from_user.id)
    loader = await show_loading(context, chat_id, tr(lang, "load_wait"))
    try:
        await route_callback(q, context, chat_id)
    except Exception as e:
        log.exception("callback failed")
        await context.bot.send_message(chat_id, card(tr(lang, "err_title"), tr(lang, "err_body"), icon="⚠️", lang=lang),
                                       reply_markup=main_menu(lang))
        await notify_user(context, ADMIN_ID, card("ত্রুটি ধরা পড়েছে",
                          f"<code>{h(type(e).__name__)}: {h(str(e))[:300]}</code>\nবাটন: <code>{h(data_of(q))}</code>",
                          icon="🐞"))
    finally:
        await finish_loading(loader)


async def route_callback(q, context, chat_id):
    data = q.data or ""
    uid = q.from_user.id
    lang = L(uid)
    flow = context.user_data.get("flow")

    if data in ("home", "joincheck"):
        context.user_data.pop("flow", None)
        await say(context, chat_id, "home", welcome_text(q.from_user, lang), main_menu(lang))

    elif data.startswith("lang:"):
        code = data.split(":", 1)[1]
        if code not in ("bn", "en"):
            return
        set_lang(uid, code)
        lang = code
        await send_start_screens(context, chat_id, q.from_user, lang)

    elif data.startswith("voice:"):
        set_voice_pref(chat_id, data.endswith(":on"))
        await say(context, chat_id, "welcome", welcome_text(q.from_user, lang), main_menu(lang))

    elif data == "lang_menu":
        await say(context, chat_id, "settings", settings_text(lang, uid), settings_markup(lang, uid))

    elif data.startswith("cat_u:"):
        await show_category(context, chat_id, data.split(":", 1)[1], lang)

    elif data == "balance":
        await say(context, chat_id, "balance", balance_text(uid, lang), main_menu(lang))

    elif data == "deposit":
        sup = support_username()
        rows = [[copy_btn(tr(lang, "btn_copy_id"), uid)]]
        if sup:
            rows.insert(0, [copy_btn(tr(lang, "btn_copy_support"), f"@{sup}")])
            rows.append([InlineKeyboardButton(tr(lang, "btn_support"), url=f"https://t.me/{sup}")])
        rows.append([btn(tr(lang, "btn_menu"), "home")])
        await say(context, chat_id, "deposit", deposit_text(uid, lang), InlineKeyboardMarkup(rows))

    elif data == "orders":
        await send_orders_list(context, chat_id, uid, lang)

    elif data == "redeem":
        context.user_data["flow"] = {"name": "redeem", "step": "code", "data": {}}
        await say(context, chat_id, "redeem",
                  card(tr(lang, "redeem_title"), tr(lang, "redeem_prompt"), icon="🎁", lang=lang),
                  cancel_kb(lang))

    elif data.startswith("svc:"):
        srv = get_service(data.split(":", 1)[1])
        await start_order_flow(context, chat_id, uid, srv, lang)

    elif data.startswith("again:"):
        row = get_order(int(data.split(":", 1)[1]))
        if not row or row["user_id"] != uid:
            await context.bot.send_message(
                chat_id, card(tr(lang, "order_forbidden_title"), tr(lang, "order_forbidden_body"), icon="⚠️", lang=lang),
                reply_markup=main_menu(lang))
            return
        srv = get_service(row["service_key"]) if row["service_key"] else None
        await start_order_flow(context, chat_id, uid, srv, lang)

    elif data.startswith("report:"):
        row = get_order(int(data.split(":", 1)[1]))
        if not row or row["user_id"] != uid:
            await context.bot.send_message(
                chat_id, card(tr(lang, "order_forbidden_title"), tr(lang, "order_forbidden_body"), icon="⚠️", lang=lang),
                reply_markup=main_menu(lang))
            return
        await send_report(context, chat_id, row, lang)

    elif data.startswith("refill:"):
        await do_refill(context, chat_id, uid, int(data.split(":", 1)[1]))

    elif data.startswith("qty:"):
        if flow and flow["name"] == "order" and flow["step"] == "qty":
            await set_qty_summary(context, chat_id, flow, int(data.split(":", 1)[1]), uid, lang)
        else:
            await context.bot.send_message(
                chat_id, card(tr(lang, "choose_first_title"), tr(lang, "choose_first_body"), icon="⚠️", lang=lang),
                reply_markup=main_menu(lang))

    elif data == "order_confirm":
        await strip_order_buttons(q)  # বাটন প্রথমেই সরবে, তাই দ্বিতীয়বার চাপার সুযোগ নেই
        async with _lock_for(uid):
            flow = context.user_data.get("flow")  # লক পাওয়ার পর আবার যাচাই
            if flow and flow["name"] == "order" and flow["step"] == "confirm":
                await place_order(q, context, flow)
            else:
                await context.bot.send_message(
                    chat_id, card(tr(lang, "stale_title"), tr(lang, "stale_body"), icon="⚠️", lang=lang),
                    reply_markup=main_menu(lang))

    elif data in ("order_cancel", "flowcancel"):
        if data == "order_cancel":
            await strip_order_buttons(q)
        context.user_data.pop("flow", None)
        await context.bot.send_message(
            chat_id, card(tr(lang, "cancelled_title"), tr(lang, "cancelled_body"), icon="❌", lang=lang),
            reply_markup=main_menu(lang))

    elif data.startswith(ADMIN_PREFIXES) or data in ("ban_perm_yes", "ban_perm_no"):
        if not is_admin_id(uid):
            return
        await admin_route(q, context, data, chat_id)


# ============================================================
# Admin panel (বাংলা)
# ============================================================
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
                    [btn("🎙 সার্ভিসের ভয়েস", "adm:svcvoice")],
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
                    [btn("🖼 স্ক্রিন ছবি ও ভয়েস", "adm:m:media")],
                    back]))
    if section == "media":
        rows = [[btn(lbl, f"scr:{k}") for k, lbl in SCREENS[i:i + 2]] for i in range(0, len(SCREENS), 2)]
        rows.append([btn("‹ সেটিং", "adm:m:settings")])
        return (card("স্ক্রিন ছবি ও ভয়েস",
                     "প্রতিটি স্ক্রিনের জন্য আলাদা ছবি ও ভয়েস দিতে পারবেন। ছবি না দিলে ডিফল্ট ব্র্যান্ড ছবি যাবে।",
                     icon="🖼"), InlineKeyboardMarkup(rows))
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


async def ask_svc_emoji(context, chat_id, cat):
    """সার্ভিসের ইমোজি পিকার: অ্যাডমিন একটি ইমোজি বেছে নেয়।"""
    kb, row = [], []
    for i, e in enumerate(EMOJI_CHOICES):
        row.append(btn(e, f"svcemo:{i}"))
        if len(row) == 6:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([btn(f"↺ ডিফল্ট {cat_emoji(cat)}", "svcemo:default")])
    kb.append([btn("✖ বাতিল", "flowcancel")])
    await context.bot.send_message(
        chat_id,
        card("সার্ভিসের ইমোজি", "একটি ইমোজি বেছে নিন। এটি সার্ভিসের পাশে ইউজারের কাছে দেখাবে।", icon="🎨"),
        reply_markup=InlineKeyboardMarkup(kb))


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

    if data.startswith("scr:"):
        await send_scope_card(context, chat_id, data.split(":", 1)[1])
        return
    if data == "adm:svcvoice":
        await send_pick(context, chat_id, "svcvoice", "কোন সার্ভিসের ভয়েস দিবেন?", "🎙")
        return
    if data.startswith("svcvoice:"):
        await send_scope_card(context, chat_id, "svc:" + data.split(":", 1)[1])
        return
    if data.startswith("imgset:") or data.startswith("voiceset:"):
        kind, scope = data.split(":", 1)
        if kind == "imgset":
            context.user_data["flow"] = {"name": "adm_imgset", "step": "photo", "data": {"scope": scope}}
            prompt = card("স্ক্রিনের ছবি", f"<b>{h(scope_label(scope))}</b>-এর জন্য একটি <b>ছবি</b> পাঠান।",
                          icon="🖼")
        else:
            context.user_data["flow"] = {"name": "adm_voiceset", "step": "voice", "data": {"scope": scope}}
            prompt = card("স্ক্রিনের ভয়েস", f"<b>{h(scope_label(scope))}</b>-এর জন্য একটি <b>ভয়েস বার্তা</b> "
                          "(Voice note) পাঠান।\nMP3 বা অডিও ফাইল হলে সেটিও চলবে।", icon="🎙")
        await context.bot.send_message(chat_id, prompt, reply_markup=cancel_kb())
        return
    if data.startswith("imgdel:") or data.startswith("voicedel:"):
        kind, scope = data.split(":", 1)
        clear_scope(scope, "img" if kind == "imgdel" else "voice")
        await send_scope_card(context, chat_id, scope)
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
                 "<code>TikTok</code>।\nনাম লিখলে লোগো ও ইমোজি নিজে থেকে তৈরি হবে।", icon="🆕"),
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

    if data.startswith("svcemo:"):
        flow = context.user_data.get("flow")
        if not flow or flow["name"] != "adm_svcadd" or flow["step"] != "emoji":
            await context.bot.send_message(chat_id, card("আগে সার্ভিসের নাম দিন", "নতুন সার্ভিস শুরু করতে নাম লিখুন।",
                                                         icon="⚠️"), reply_markup=cancel_kb())
            return
        val = data.split(":", 1)[1]
        if val == "default":
            emoji = cat_emoji(flow["data"]["cat"])
        else:
            try:
                emoji = EMOJI_CHOICES[int(val)]
            except (ValueError, IndexError):
                return
        flow["data"]["emoji"] = emoji
        flow["step"] = "panel"
        await context.bot.send_message(
            chat_id,
            card("প্যানেল আইডি", f"{emoji}  ইমোজি সেট হয়েছে।\nওয়েবসাইটের সার্ভিস আইডি লিখুন (যেমন 14759 বা 147.59)।",
                 icon="🔢"),
            reply_markup=cancel_kb())
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


def scope_label(scope):
    if scope.startswith("cat:"):
        return f"ক্যাটাগরি: {scope[4:]}"
    if scope.startswith("svc:"):
        srv = get_service(scope[4:])
        return f"সার্ভিস: {service_title(srv)}" if srv else scope
    return f"স্ক্রিন: {LABELS.get(scope, scope)}"


def scope_image(scope):
    if scope.startswith("cat:"):
        return category_logo(scope[4:])
    if scope.startswith("svc:"):
        srv = get_service(scope[4:])
        return srv["photo_id"] if srv else None
    return image_ref(scope)


def scope_back(scope):
    if scope.startswith("cat:"):
        return scope
    if scope.startswith("svc:"):
        return "adm:svcvoice"
    return "adm:m:media"


def clear_scope(scope, kind):
    if kind == "voice":
        clear_voice(scope)
    elif scope.startswith("cat:"):
        set_setting(logo_key(scope[4:]), "")
    elif scope.startswith("svc:"):
        set_service_photo(scope[4:], None)
    else:
        set_image(scope, "")


async def send_scope_card(context, chat_id, scope):
    img = "✅ আছে" if scope_image(scope) else "❌ নেই (ডিফল্ট ছবি যাবে)"
    voice = "✅ আছে" if voice_ref([scope]) else "❌ নেই"
    body = f"📂 {h(scope_label(scope))}\n🖼 ছবি: {img}\n🎙 ভয়েস: {voice}"
    kb = InlineKeyboardMarkup([
        [btn("🖼 ছবি দিন", f"imgset:{scope}"), btn("🗑 ছবি মুছুন", f"imgdel:{scope}")],
        [btn("🎙 ভয়েস দিন", f"voiceset:{scope}"), btn("🗑 ভয়েস মুছুন", f"voicedel:{scope}")],
        [btn("‹ ফিরে যান", scope_back(scope))],
    ])
    await context.bot.send_message(chat_id, card("স্ক্রিন সেটিং", body, icon="🖼"), reply_markup=kb)


async def send_pick(context, chat_id, action, title, icon):
    srvs = list_services(active_only=True)
    if not srvs:
        await context.bot.send_message(chat_id, card("সার্ভিস নেই", "প্রথমে একটি ক্যাটাগরি ও সার্ভিস যোগ করুন।", icon="⚠️"),
                                       reply_markup=admin_home()["reply_markup"])
        return
    kb = [[btn(f"{s['emoji']} {service_title(s)} · {money(s['price'])}", f"{action}:{s['key']}")] for s in srvs]
    kb.append([btn("✖ বাতিল", "flowcancel")])
    await context.bot.send_message(chat_id, card(title, "একটি সার্ভিস বেছে নিন।", icon=icon),
                                   reply_markup=InlineKeyboardMarkup(kb))


async def send_categories(context, chat_id):
    cats = list_categories()
    if not cats:
        await context.bot.send_message(chat_id, card("ক্যাটাগরি নেই", "‘নতুন ক্যাটাগরি’ দিয়ে শুরু করুন।", icon="🗂"),
                                       reply_markup=admin_home()["reply_markup"])
        return
    kb = [[btn(f"{cat_emoji(c['name'])} {c['name']}", f"cat:{c['name']}")] for c in cats]
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
        [btn("🎙 ক্যাটাগরির ভয়েস", f"scr:cat:{name}")],
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
    body = (f"📂 ক্যাটাগরি: <b>{h(name)}</b>\n🎨 ইমোজি: {cat_emoji(name)}\n"
            f"🔄 রিফিল: {refill}\n{logo_note}")
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
            f"   প্যানেল আইডি: <code>{h(r['panel_id'])}</code> · {money(r['price'])} / ১০০০\n"
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
        await notify_key(context, uid, "banned_perm")
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
    uid = update.effective_user.id
    lang = L(uid)
    if flow["step"] == "link":
        if "t.me/" not in text.lower():
            await update.message.reply_text(tr(lang, "link_invalid"))
            return
        flow["data"]["link"] = text
        srv = get_service(flow["data"]["key"])
        if not srv or not srv["active"]:
            context.user_data.pop("flow", None)
            await context.bot.send_message(
                chat_id, card(tr(lang, "svc_gone_title"), tr(lang, "svc_gone_body"), icon="⚠️", lang=lang),
                reply_markup=main_menu(lang))
            return
        flow["step"] = "qty"
        await send_qty_prompt(context, chat_id, srv, lang)
    elif flow["step"] == "qty":
        if not text.isdigit():
            await update.message.reply_text(tr(lang, "numbers_only"))
            return
        await set_qty_summary(context, chat_id, flow, int(text), uid, lang)
    else:
        await update.message.reply_text(tr(lang, "use_buttons"))


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
        head, key = f"{money(amt)} যোগ হয়েছে", "notify_added"
    else:
        adjust_balance(uid, -amt, "withdraw", "অ্যাডমিন কাটা")
        head, key = f"{money(amt)} কাটা হয়েছে", "notify_cut"
    bal = get_user(uid)["balance"]
    await update.message.reply_text(card("সফল", f"<code>{uid}</code> · {head}\nনতুন ব্যালেন্স: <b>{money(bal)}</b>", icon="✅"),
                                    reply_markup=admin_home()["reply_markup"])
    await notify_key(context, uid, key, amt=money(amt), bal=money(bal))


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
        await notify_key(context, uid, "notify_ban_days", days=days)


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
    await notify_key(context, row["id"], "notify_unban")


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


async def finish_svcadd(message, context, d, photo_id):
    context.user_data.pop("flow", None)
    key = add_service(d["name"], d["cat"], d["panel_id"], d["price"], d["min_qty"], d["max_qty"], photo_id,
                      d.get("emoji"))
    srv = get_service(key)
    body = (f"📂 ক্যাটাগরি: <b>{h(d['cat'])}</b>\n{srv['emoji']} নাম: {h(d['name'])}\n"
            f"🔢 প্যানেল আইডি: <code>{h(d['panel_id'])}</code> (ইউজার দেখবে না)\n"
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
        flow["step"] = "emoji"
        await ask_svc_emoji(context, update.message.chat.id, d["cat"])
    elif step == "emoji":
        await update.message.reply_text("▸ নিচের ইমোজি থেকে একটি বেছে নিন।", reply_markup=cancel_kb())
    elif step == "panel":
        if not re.fullmatch(r"\d+(\.\d+)?", text):
            await update.message.reply_text("⚠️ সার্ভিস আইডি সংখ্যায় লিখুন (যেমন 14759)।", reply_markup=cancel_kb())
            return
        d["panel_id"] = text
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


def user_info_text(row):
    st = user_stats(row["id"])
    uname = f"@{h(row['username'])}" if row["username"] else "—"
    body = (
        f"নাম: {h(full_name(row))}\n"
        f"ইউজারনেম: {uname}\n"
        f"আইডি: <code>{row['id']}</code>\n"
        f"যোগদান: {h(fmt_time(row['joined']))}\n"
        f"স্ট্যাটাস: {tr('bn', status_key(row))}\n"
        f"ভাষা: {'English' if row['lang'] == 'en' else 'বাংলা'}\n\n"
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
    srv = get_service(r["service_key"]) if r["service_key"] else None
    body = (
        f"ইউজার: {who}\n"
        f"ক্যাটাগরি: {h(srv['platform'] if srv else '—')}\n"
        f"সার্ভিস: {h(r['service_name'])}\n"
        f"লিংক: {h(r['link'])}\n"
        f"পরিমাণ: {r['quantity']}\n"
        f"মূল্য: {money(r['cost'])}\n"
        f"প্যানেল অর্ডার: <code>{h(r['panel_order'])}</code>\n"
        f"সময়: {h(fmt_time(r['created']))}\n"
        f"রিফিল: {h(refill_text(r, 'bn'))}"
    )
    return card(f"অর্ডার {order_label(r)}", body, icon="🔎")


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

    elif name == "adm_imgset" and msg.photo:
        scope = flow["data"]["scope"]
        if scope.startswith("cat:"):
            set_setting(logo_key(scope[4:]), msg.photo[-1].file_id)
        elif scope.startswith("svc:"):
            set_service_photo(scope[4:], msg.photo[-1].file_id)
        else:
            set_image(scope, msg.photo[-1].file_id)
        context.user_data.pop("flow", None)
        await msg.reply_text(card("ছবি সেট হয়েছে", h(scope_label(scope)), icon="🖼"),
                             reply_markup=InlineKeyboardMarkup([[btn("‹ ফিরে যান", f"scr:{scope}")]]))

    elif name == "adm_voiceset" and (msg.voice or msg.audio):
        scope = flow["data"]["scope"]
        if msg.voice:
            set_voice_media(scope, "voice", msg.voice.file_id)
        else:
            set_voice_media(scope, "audio", msg.audio.file_id)
        context.user_data.pop("flow", None)
        await msg.reply_text(card("ভয়েস সেট হয়েছে", h(scope_label(scope)), icon="🎙"),
                             reply_markup=InlineKeyboardMarkup([[btn("‹ ফিরে যান", f"scr:{scope}")]]))

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


# ============================================================
# Startup
# ============================================================
PUBLIC_COMMANDS = [BotCommand("start", "মেনু খুলুন · Open menu")]
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
    app.add_handler(MessageHandler(filters.PHOTO | filters.ANIMATION | filters.VIDEO
                                   | filters.VOICE | filters.AUDIO, on_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    app.run_polling()


if __name__ == "__main__":
    main()

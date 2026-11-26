import os
from datetime import timedelta, timezone

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
MEMBER_CACHE_SECONDS = 5
DIV = "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"

# ============================================================
# স্থায়ী চ্যানেল (কোডে বসানো)। অ্যাডমিন প্যানেল থেকে পরে বদলানো যাবে,
# সেটিং দেওয়া হলে সেটিই কার্যকর থাকবে।
# ============================================================
USER_CHANNEL = "@smspanelprirvitesp"   # চ্যানেল ১: সকল ইউজার + বট স্টার্টে বাধ্যতামূলক জয়েন
ADMIN_CHANNEL = "@SP_ORDER_UPDATE"     # চ্যানেল ২: শুধু অ্যাডমিন, পূর্ণ বিবরণ

# ক্যাটাগরির নাম অনুযায়ী ইমোজি (নাম লিখলে নিজে থেকে মিলে যাবে)
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
    "tiktok": ("#111111", "♪"),
    "youtube": ("#FF0000", "play"),
    "imo": ("#1BA3E0", "imo"),
    "x": ("#111111", "X"),
    "twitter": ("#111111", "X"),
    "snapchat": ("#FFFC00", "S"),
    "linkedin": ("#0A66C2", "in"),
}

# সার্ভিসের জন্য ইমোজি পিকার (অ্যাডমিন বেছে নেয়)
EMOJI_CHOICES = [
    "🚀", "⚡", "🔥", "👁", "👥", "❤️", "💎", "⭐", "📈", "🎯",
    "🎬", "📸", "🎵", "💬", "🛒", "🔗", "🌟", "🏆", "💫", "🎁",
    "🛡", "📦", "🔔", "📢", "🧿", "💠", "✨", "🪙", "🌐", "🧩",
    "🎖", "💰", "🔹", "🔸", "🟢", "🟣",
]


def platform_emoji(platform):
    return PLATFORM_EMOJI.get((platform or "").lower().strip(), "🛍")

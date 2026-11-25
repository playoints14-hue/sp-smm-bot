"""ফিচার-স্ক্রিনের ছবি ও ভয়েস।

ছবি ও ভয়েস দুটোই অ্যাডমিন প্যানেল থেকে দেওয়া যায় (⚙️ সেটিং → 🖼 স্ক্রিন ছবি ও ভয়েস)।
কিছু না দিলে ডিফল্ট ব্র্যান্ড ছবি দেখাবে, আর ভয়েস কিছু যাবে না।

সেটিংস টেবিলে সংরক্ষণ:
  img:<স্ক্রিন>        -> ছবির file_id
  voice:<স্কোপ>       -> "voice|<file_id>" বা "audio|<file_id>"
স্কোপ: স্ক্রিনের নাম (welcome, home ...), ক্যাটাগরি "cat:<নাম>", সার্ভিস "svc:<key>"।
"""
import os

from db import get_setting, set_setting

ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
DEFAULT_IMAGE = os.path.join(ASSET_DIR, "brand.png")

# (কী, অ্যাডমিনে দেখানো নাম) — এই ক্রমে বাটন দেখাবে
SCREENS = [
    ("welcome", "ওয়েলকাম"), ("join", "জয়েন গেট"), ("lang", "ভাষা বাছাই"), ("voice_ask", "ভয়েস প্রশ্ন"),
    ("home", "মেনু"), ("category", "ক্যাটাগরি"), ("service", "সার্ভিস"), ("qty", "পরিমাণ"),
    ("summary", "অর্ডার সারাংশ"), ("success", "অর্ডার সফল"), ("balance", "আমার অ্যাকাউন্ট"),
    ("deposit", "টাকা যোগ"), ("orders", "আমার অর্ডার"), ("report", "অর্ডার বিবরণ"),
    ("refill", "রিফিল"), ("redeem", "রিডিম কোড"), ("settings", "সেটিংস"),
]
LABELS = dict(SCREENS)


def image_ref(scope):
    return get_setting(f"img:{scope}") or None


def set_image(scope, file_id):
    set_setting(f"img:{scope}", file_id)


def voice_ref(scopes):
    """স্কোপগুলোর মধ্যে প্রথম যেটায় ভয়েস আছে, সেটি (kind, file_id) হিসেবে দেয়।"""
    for scope in scopes:
        raw = get_setting(f"voice:{scope}")
        if raw:
            kind, _, fid = raw.partition("|")
            if fid:
                return kind, fid
    return None


def set_voice_media(scope, kind, file_id):
    set_setting(f"voice:{scope}", f"{kind}|{file_id}")


def clear_voice(scope):
    set_setting(f"voice:{scope}", "")

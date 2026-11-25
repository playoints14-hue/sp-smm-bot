"""ইউজার-সাইডের সব লেখা: বাংলা (bn) ও ইংরেজি (en)।

অ্যাডমিন প্যানেল শুধু বাংলায় থাকবে।
"""

S = {
    # ---------- জয়েন ও ভাষা ----------
    "join_title": ("চ্যানেলে যোগ দিন · Join to continue", None),
    "join_body": ("বট ব্যবহার করতে প্রথমে আমাদের চ্যানেলে যোগ দিন।\nযোগ দেওয়ার পর নিচের বাটনে চাপুন।",
                  "Join our channel to start using the bot.\nTap the button below once you have joined."),
    "btn_join": ("🔗 চ্যানেলে যোগ দিন", "🔗 Join channel"),
    "btn_joined": ("✅ যোগ দিয়েছি · Joined", None),
    "join_alert": ("আগে চ্যানেলে যোগ দিন।", "Please join the channel first."),
    "lang_title": ("ভাষা বেছে নিন · Choose your language", None),
    "lang_body": ("আপনার পছন্দের ভাষা নির্বাচন করুন।\nSelect your preferred language.", None),
    "btn_lang_bn": ("🇧🇩 বাংলা", None),
    "btn_lang_en": ("🇬🇧 English", None),
    "lang_set": ("ভাষা সেট হয়েছে", "Language saved"),

    # ---------- ভয়েস গাইড ও সেটিংস ----------
    "voice_title": ("ভয়েস গাইড · Voice guide", None),
    "voice_body": ("ভয়েস চালু থাকলে প্রতিটি ধাপে ছোট ভয়েস বার্তায় সাহায্য পাবেন।\n"
                   "With voice on, each step plays a short voice guide.", None),
    "btn_voice_on": ("🔊 ভয়েস চালু · On", None),
    "btn_voice_off": ("🔇 ভয়েস বন্ধ · Off", None),
    "voice_now_on": ("ভয়েস গাইড: 🔊 চালু · Voice guide: On", None),
    "voice_now_off": ("ভয়েস গাইড: 🔇 বন্ধ · Voice guide: Off", None),
    "settings_title": ("সেটিংস · Settings", None),
    "settings_body": ("ভাষা ও ভয়েস গাইড এখান থেকে বদলাতে পারবেন।\nChange language and voice guide here.", None),

    # ---------- স্বাগত ও মেনু ----------
    "welcome_tag": ("দ্রুত • নিরাপদ • স্বয়ংক্রিয়", "Fast • Secure • Automated"),
    "welcome_hi": ("👋 স্বাগতম, <b>{name}</b>", "👋 Welcome back, <b>{name}</b>"),
    "price_head": ("প্রাইস তালিকা", "Price list"),
    "price_line": ("{emoji} {title}  ·  <b>{price}</b> / ১০০০", "{emoji} {title}  ·  <b>{price}</b> / 1000"),
    "price_empty": ("এখন কোনো সার্ভিস চালু নেই।", "No services are available right now."),
    "menu_hint": ("▸ নিচের মেনু থেকে একটি অপশন বেছে নিন", "▸ Choose an option from the menu below"),
    "btn_balance": ("💳 ব্যালেন্স", "💳 Balance"),
    "btn_deposit": ("➕ টাকা যোগ", "➕ Add funds"),
    "btn_redeem": ("🎁 রিডিম কোড", "🎁 Redeem code"),
    "btn_orders": ("📦 আমার অর্ডার", "📦 My orders"),
    "btn_language": ("🌐 Language · ভাষা", None),
    "btn_menu": ("🏠 মেনু", "🏠 Menu"),
    "btn_back": ("‹ মেনুতে ফিরুন", "‹ Back to menu"),
    "btn_cancel": ("✖ বাতিল", "✖ Cancel"),
    "btn_confirm": ("✅ নিশ্চিত করুন", "✅ Confirm"),
    "btn_decline": ("❌ বাতিল", "❌ Cancel"),
    "btn_copy_link": ("📋 লিংক কপি", "📋 Copy link"),
    "btn_copy_id": ("📋 আইডি কপি", "📋 Copy ID"),
    "btn_copy_support": ("📋 সাপোর্ট কপি", "📋 Copy support"),
    "btn_support": ("💬 সাপোর্টে মেসেজ", "💬 Message support"),
    "btn_speed": ("🚀 স্পিড আপ", "🚀 Speed up"),
    "btn_refill": ("🔄 রিফিল অনুরোধ", "🔄 Request refill"),
    "btn_again": ("🔁 আবার অর্ডার", "🔁 Order again"),
    "btn_report": ("📝 বিস্তারিত", "📝 Details"),

    # ---------- ক্যাটাগরি ও সার্ভিস ----------
    "cat_hint": ("একটি সার্ভিস বেছে নিন", "Choose a service"),
    "cat_empty_title": ("সার্ভিস পাওয়া যাচ্ছে না", "No services available"),
    "cat_empty_body": ("<b>{name}</b>-এ এখন কোনো সার্ভিস চালু নেই।", "<b>{name}</b> has no active services right now."),
    "svc_list_line": ("{emoji} <b>{name}</b>\n     {price} / ১০০০  ·  সর্বনিম্ন {min}",
                      "{emoji} <b>{name}</b>\n     {price} / 1000  ·  Min {min}"),
    "svc_info": ("💵 মূল্য: <b>{price}</b> / ১০০০\n📏 সর্বনিম্ন: {min}  ·  সর্বোচ্চ: {max}",
                 "💵 Price: <b>{price}</b> / 1000\n📏 Min: {min}  ·  Max: {max}"),
    "unlimited": ("সীমাহীন", "Unlimited"),
    "step1_hint": ("ধাপ ১/৩ · আপনার পোস্ট বা চ্যানেলের লিংক পাঠান", "Step 1 of 3 · Send your post or channel link"),
    "link_invalid": ("⚠️ একটি সঠিক টেলিগ্রাম লিংক পাঠান, যেমন: https://t.me/...",
                     "⚠️ Please send a valid Telegram link, e.g. https://t.me/..."),
    "qty_title": ("পরিমাণ নির্বাচন", "Select quantity"),
    "qty_body_minmax": ("পরিমাণ লিখুন (সর্বনিম্ন {min}, সর্বোচ্চ {max}) অথবা নিচ থেকে বেছে নিন।",
                        "Enter a quantity (min {min}, max {max}) or pick one below."),
    "qty_body_min": ("পরিমাণ লিখুন (সর্বনিম্ন {min}) অথবা নিচ থেকে বেছে নিন।",
                     "Enter a quantity (min {min}) or pick one below."),
    "step2_hint": ("ধাপ ২/৩ · পরিমাণ নির্বাচন", "Step 2 of 3 · Quantity"),
    "numbers_only": ("⚠️ শুধু সংখ্যা লিখুন।", "⚠️ Please enter numbers only."),
    "qty_range": ("⚠️ পরিমাণ {min} থেকে {max}-এর মধ্যে হতে হবে।", "⚠️ Quantity must be between {min} and {max}."),
    "qty_range_min": ("⚠️ পরিমাণ {min} বা তার বেশি হতে হবে।", "⚠️ Quantity must be {min} or more."),
    "summary_title": ("অর্ডার সারাংশ", "Order summary"),
    "summary_body": (
        "📂 ক্যাটাগরি: <b>{cat}</b>\n🛍 সার্ভিস: <b>{svc}</b>\n🔗 লিংক: <i>{link}</i>\n"
        "📊 পরিমাণ: <b>{qty}</b>\n💵 মূল্য: <b>{cost}</b>\n💳 বর্তমান ব্যালেন্স: {bal}\n🧾 অর্ডারের পর: {after}",
        "📂 Category: <b>{cat}</b>\n🛍 Service: <b>{svc}</b>\n🔗 Link: <i>{link}</i>\n"
        "📊 Quantity: <b>{qty}</b>\n💵 Cost: <b>{cost}</b>\n💳 Current balance: {bal}\n🧾 After order: {after}"),
    "step3_hint": ("ধাপ ৩/৩ · নিশ্চিত করে অর্ডার দিন", "Step 3 of 3 · Confirm to place your order"),
    "low_balance_note": ("\n\n⚠️ ব্যালেন্স যথেষ্ট নয়। আগে টাকা যোগ করুন।",
                         "\n\n⚠️ Insufficient balance. Please add funds first."),
    "svc_gone_title": ("সার্ভিস পাওয়া যাচ্ছে না", "Service unavailable"),
    "svc_gone_body": ("এই সার্ভিসটি এখন চালু নেই।", "This service is not active right now."),
    "choose_first_title": ("আগে সার্ভিস বেছে নিন", "Choose a service first"),
    "choose_first_body": ("মেনু থেকে একটি সার্ভিস বেছে নিন।", "Please pick a service from the menu."),
    "cancelled_title": ("বাতিল করা হয়েছে", "Cancelled"),
    "cancelled_body": ("আবার শুরু করতে মেনু থেকে বেছে নিন।", "Pick an option from the menu to start again."),

    # ---------- অর্ডার ----------
    "order_ok_title": ("অর্ডার সফল হয়েছে", "Order placed successfully"),
    "order_ok_body": (
        "🔢 অর্ডার নম্বর: <code>{no}</code>\n📂 ক্যাটাগরি: {cat}\n🛍 সার্ভিস: {svc}\n📊 পরিমাণ: <b>{qty}</b>\n"
        "💵 কাটা হয়েছে: <b>{cost}</b>\n💳 বাকি ব্যালেন্স: <b>{bal}</b>\n🕒 সময়: {time}",
        "🔢 Order no: <code>{no}</code>\n📂 Category: {cat}\n🛍 Service: {svc}\n📊 Quantity: <b>{qty}</b>\n"
        "💵 Charged: <b>{cost}</b>\n💳 Remaining balance: <b>{bal}</b>\n🕒 Time: {time}"),
    "order_fail_title": ("অর্ডার সম্পন্ন হয়নি", "Order could not be completed"),
    "order_fail_body": ("আপনার টাকা ব্যালেন্সে ফেরত দেওয়া হয়েছে। কিছুক্ষণ পর আবার চেষ্টা করুন।",
                        "Your balance has been refunded. Please try again shortly."),
    "order_saved_title": ("অর্ডার গৃহীত হয়েছে", "Order received"),
    "order_saved_body": ("সাপোর্টে যোগাযোগ করুন, আমরা অর্ডার নম্বর জানিয়ে দেব।",
                         "Please contact support and we will share your order number."),
    "low_balance_title": ("ব্যালেন্স যথেষ্ট নয়", "Insufficient balance"),
    "low_balance_body": ("অর্ডার করতে আগে টাকা যোগ করুন।", "Please add funds before placing an order."),
    "orders_title": ("আমার অর্ডার", "My orders"),
    "orders_empty": ("আপনার এখনো কোনো অর্ডার নেই।", "You have no orders yet."),
    "order_forbidden_title": ("অর্ডার পাওয়া যায়নি", "Order not found"),
    "order_forbidden_body": ("এই অর্ডারটি আপনার অ্যাকাউন্টের নয়।", "This order does not belong to your account."),
    "report_title": ("অর্ডার বিবরণ · {no}", "Order details · {no}"),
    "report_body": ("🏷 সার্ভিস: {svc}\n📊 পরিমাণ: {qty}\n💵 মূল্য: {cost}\n📅 তারিখ: {time}\n🔄 রিফিল: {refill}",
                    "🏷 Service: {svc}\n📊 Quantity: {qty}\n💵 Cost: {cost}\n📅 Date: {time}\n🔄 Refill: {refill}"),
    "refill_done": ("✅ অনুরোধ জমা হয়েছে (আইডি {rid})", "✅ Request submitted (ID {rid})"),
    "refill_active": ("🟢 সক্রিয়, {days} দিনের মধ্যে", "🟢 Available within {days} days"),
    "refill_none": ("⚪ এই অর্ডারে রিফিল নেই", "⚪ No refill available for this order"),
    "refill_timer": ("⏱ রিফিল সময়সীমা শেষ: {date}", "⏱ Refill window closes: {date}"),
    "refill_unavailable_title": ("রিফিল সম্ভব নয়", "Refill unavailable"),
    "refill_unavailable_body": ("সময়সীমা শেষ হয়েছে, অথবা আগেই রিফিল অনুরোধ করা হয়েছে।",
                                "The window has closed, or a refill was already requested."),
    "refill_ok_title": ("রিফিল অনুরোধ জমা হয়েছে", "Refill request submitted"),
    "refill_ok_body": ("🔢 অর্ডার: <code>{no}</code>\n🔄 রিফিল আইডি: <code>{rid}</code>",
                       "🔢 Order: <code>{no}</code>\n🔄 Refill ID: <code>{rid}</code>"),
    "refill_fail_title": ("রিফিল হয়নি", "Refill failed"),
    "refill_fail_body": ("অনুরোধটি গ্রহণ করা হয়নি। কিছুক্ষণ পর আবার চেষ্টা করুন।",
                         "The request was not accepted. Please try again shortly."),

    # ---------- ব্যালেন্স, জমা, রিডিম ----------
    "balance_title": ("আমার অ্যাকাউন্ট", "My account"),
    "balance_body": (
        "💰 বর্তমান ব্যালেন্স: <b>{bal}</b>\n📥 মোট জমা: {dep}\n📤 মোট খরচ: {spent}\n🎁 রিডিম থেকে: {red}\n📦 মোট অর্ডার: {n}",
        "💰 Current balance: <b>{bal}</b>\n📥 Total added: {dep}\n📤 Total spent: {spent}\n🎁 From redeem codes: {red}\n📦 Total orders: {n}"),
    "deposit_title": ("টাকা যোগ করুন", "Add funds"),
    "deposit_body": ("১️⃣ পেমেন্ট সম্পন্ন করুন\n২️⃣ সাপোর্টে আপনার আইডি জানান\n৩️⃣ ব্যালেন্স যোগ হলে বার্তা পাবেন",
                     "1️⃣ Complete the payment\n2️⃣ Send your ID to support\n3️⃣ You will be notified once funds are added"),
    "contact_support": ("👤 সাপোর্ট: @{sup}", "👤 Support: @{sup}"),
    "contact_none": ("👤 সাপোর্ট: অ্যাডমিনের সাথে যোগাযোগ করুন", "👤 Support: please contact the admin"),
    "your_id": ("🪪 আপনার আইডি: <code>{uid}</code>", "🪪 Your ID: <code>{uid}</code>"),
    "redeem_title": ("রিডিম কোড", "Redeem code"),
    "redeem_prompt": ("আপনার কোডটি লিখে পাঠান।", "Send your code in the chat."),
    "redeem_ok_title": ("কোড সফলভাবে যোগ হয়েছে", "Code redeemed"),
    "redeem_ok_body": ("{amt} আপনার ব্যালেন্সে যোগ হয়েছে।", "{amt} has been added to your balance."),
    "redeem_used_title": ("কোডটি আগেই ব্যবহৃত", "Already redeemed"),
    "redeem_used_body": ("আপনি এই কোডটি আগেই ব্যবহার করেছেন।", "You have already used this code."),
    "redeem_expired_title": ("কোডের মেয়াদ শেষ", "Code expired"),
    "redeem_expired_body": ("এই কোডের মেয়াদ শেষ, অথবা ব্যবহারের সীমা পূর্ণ হয়েছে।",
                            "This code has expired or reached its usage limit."),
    "redeem_bad_title": ("কোড সঠিক নয়", "Invalid code"),
    "redeem_bad_body": ("কোডটি মেলেনি। আবার চেষ্টা করুন।", "That code was not found. Please try again."),

    # ---------- লোডিং, সাধারণ ----------
    "load_prep": ("প্রস্তুত হচ্ছে", "Preparing"),
    "load_order": ("অর্ডার জমা দেওয়া হচ্ছে", "Submitting your order"),
    "load_code": ("কোড যাচাই হচ্ছে", "Verifying code"),
    "load_refill": ("রিফিল অনুরোধ পাঠানো হচ্ছে", "Sending refill request"),
    "load_wait": ("একটু অপেক্ষা করুন", "Please wait"),
    "err_title": ("কিছু একটা ভুল হয়েছে", "Something went wrong"),
    "err_body": ("অনুগ্রহ করে আবার চেষ্টা করুন।", "Please try again."),
    "use_buttons": ("▸ নিচের বাটন থেকে নিশ্চিত বা বাতিল করুন।", "▸ Use the buttons below to confirm or cancel."),
    "banned_perm": ("⛔ আপনার অ্যাকাউন্ট স্থায়ীভাবে সীমিত করা হয়েছে।", "⛔ Your account has been permanently restricted."),
    "banned_until": ("⛔ আপনার অ্যাকাউন্ট {until} পর্যন্ত সীমিত করা হয়েছে।", "⛔ Your account is restricted until {until}."),
    "notify_added": ("➕ আপনার অ্যাকাউন্টে {amt} যোগ হয়েছে।\nবর্তমান ব্যালেন্স: {bal}",
                     "➕ {amt} has been added to your account.\nCurrent balance: {bal}"),
    "notify_cut": ("➖ আপনার অ্যাকাউন্ট থেকে {amt} কাটা হয়েছে।\nবর্তমান ব্যালেন্স: {bal}",
                   "➖ {amt} has been deducted from your account.\nCurrent balance: {bal}"),
    "notify_ban_days": ("⛔ আপনার অ্যাকাউন্ট {days} দিনের জন্য সীমিত করা হয়েছে।",
                        "⛔ Your account has been restricted for {days} days."),
    "notify_unban": ("✅ আপনার অ্যাকাউন্টের সীমাবদ্ধতা তুলে নেওয়া হয়েছে।",
                     "✅ Your account restriction has been lifted."),

    # ---------- ইউজার চ্যানেলের অর্ডার পোস্ট (ক্রেতার ভাষায়) ----------
    "ch_new_order": ("🆕 নতুন অর্ডার", "🆕 New order"),
    "ch_order_no": ("🔢 অর্ডার নম্বর", "🔢 Order no"),
    "ch_buyer": ("👤 ক্রেতা", "👤 Buyer"),
    "ch_category": ("📂 ক্যাটাগরি", "📂 Category"),
    "ch_service": ("🛍 সার্ভিস", "🛍 Service"),
    "ch_link": ("🔗 লিংক", "🔗 Link"),
    "ch_link_hidden": ("লুকানো", "Hidden"),
    "ch_qty": ("📊 পরিমাণ", "📊 Quantity"),
    "ch_cost": ("💵 মোট খরচ", "💵 Total cost"),
    "ch_time": ("⏱ সময়", "⏱ Time"),

    # ---------- স্ট্যাটাস ----------
    "status_active": ("✅ সক্রিয়", "✅ Active"),
    "status_temp": ("⏳ সাময়িক সীমাবদ্ধ", "⏳ Temporarily restricted"),
    "status_perm": ("🚫 স্থায়ী সীমাবদ্ধ", "🚫 Permanently restricted"),
}


def tr(lang, key, **kw):
    """lang: 'bn' বা 'en'। ইংরেজি না থাকলে বাংলা দেখাবে।"""
    bn, en = S[key]
    text = en if (lang == "en" and en) else bn
    return text.format(**kw) if kw else text


def both(key, sep="\n\n"):
    """ভাষা না জানা অবস্থায় (জয়েন, ভাষা বাছাই) দুই ভাষাই দেখানো।"""
    bn, en = S[key]
    return f"{bn}{sep}{en}" if en else bn


def fancy(text):
    """ইংরেজি শিরোনামে বোল্ড ফন্ট। বাংলা অক্ষর অপরিবর্তিত থাকে।"""
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(0x1D400 + ord(ch) - 65))
        elif "a" <= ch <= "z":
            out.append(chr(0x1D41A + ord(ch) - 97))
        elif "0" <= ch <= "9":
            out.append(chr(0x1D7CE + ord(ch) - 48))
        else:
            out.append(ch)
    return "".join(out)

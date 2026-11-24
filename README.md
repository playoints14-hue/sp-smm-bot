# SP Tech Zone – Telegram SMM Bot

Telegram বট, যার মাধ্যমে ইউজার ব্যালেন্স দিয়ে Telegram Views ও Members অর্ডার দিতে পারে।

## লিঙ্কসমূহ

| বিষয় | লিঙ্ক |
|---|---|
| GitHub রিপোজিটরি | https://github.com/playoints14-hue/sp-smm-bot |
| Render (লাইভ সার্ভার) | https://sp-smm-bot.onrender.com |
| অর্ডার লগ চ্যানেল | https://t.me/SPTechZone |
| অ্যাডমিন যোগাযোগ | https://t.me/SPDeveloperOfficial0 |
| SMM প্যানেল | https://my.smmgen.com/account |

## ফাইল

| ফাইল | কাজ |
|---|---|
| `bot.py` | বটের মূল কোড: মেনু, অর্ডার, ব্যালেন্স, রিডিম কোড, অ্যাডমিন কমান্ড |
| `run.py` | সার্ভার সচল রাখে (keep-alive) ও বট ক্র্যাশ করলে আবার চালু করে |
| `requirements.txt` | প্রয়োজনীয় Python লাইব্রেরি |
| `.gitignore` | গোপন ফাইল ও ডাটাবেস GitHub-এ যেতে বাধা দেয় |

## Render সেটিংস

- **Service type:** Web Service
- **Runtime:** Python 3
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python run.py`
- **Environment Variable:** `PYTHON_VERSION` = `3.12.8`

## Environment Variables

গোপন তথ্য কোডে রাখা হয়নি। এগুলো শুধু Render-এর **Environment** অংশে যোগ করতে হবে:

`BOT_TOKEN`, `ADMIN_ID`, `ADMIN_USERNAME`, `CHANNEL`, `BRAND_NAME`, `PANEL_API_URL`, `PANEL_API_KEY`, `VIEWS_SERVICE_ID`, `VIEWS_PRICE`, `MEMBERS_SERVICE_ID`, `MEMBERS_PRICE`, `DB_PATH`

## সতর্কতা

Telegram-এ ভিউ ও মেম্বার বিক্রির ক্ষেত্রে অ্যাকাউন্ট বা চ্যানেল সীমিত হওয়ার ঝুঁকি আছে। বিষয়টি বিবেচনায় রাখুন।

import hashlib
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from config import BRANDS

OUT = 512      # চূড়ান্ত লোগোর আকার
SS = 2         # সুপারস্যাম্পলিং: ধার মসৃণ করতে বড় করে এঁকে ছোট করা হয়
TELEGRAM_PLANE = [(104, 246), (410, 122), (352, 392), (262, 320),
                  (214, 372), (220, 286), (372, 172), (190, 278)]


def _font(size):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def _scaled(points, k):
    return [(x * k, y * k) for x, y in points]


def make_logo_png(name):
    """ক্যাটাগরির নাম থেকে গোলাকার ব্র্যান্ড-লোগো PNG তৈরি করে।"""
    key = (name or "").lower().strip()
    if key in BRANDS:
        color, sym = BRANDS[key]
        rgb = _rgb(color)
    else:
        digest = hashlib.md5(key.encode()).digest()
        rgb = (60 + digest[0] % 150, 60 + digest[1] % 150, 60 + digest[2] % 150)
        sym = (name.strip()[:2] or "SP").upper()

    k = SS
    canvas = OUT * SS
    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, canvas - 1, canvas - 1], radius=118 * k, fill=rgb + (255,))

    white = (255, 255, 255, 255)
    dark = (17, 17, 17, 255)
    light_bg = rgb[0] > 200 and rgb[1] > 200
    ink = dark if light_bg else white

    if sym == "plane":
        d.polygon(_scaled(TELEGRAM_PLANE, k), fill=white)
    elif sym == "bubble":
        d.ellipse([110 * k, 100 * k, 402 * k, 352 * k], fill=white)
        d.polygon(_scaled([(150, 330), (120, 410), (210, 350)], k), fill=white)
    elif sym == "ring":
        d.rounded_rectangle([110 * k, 110 * k, 402 * k, 402 * k], radius=90 * k, outline=white, width=34 * k)
        d.ellipse([200 * k, 200 * k, 312 * k, 312 * k], outline=white, width=34 * k)
    elif sym == "play":
        d.rounded_rectangle([96 * k, 150 * k, 416 * k, 362 * k], radius=50 * k, fill=white)
        d.polygon(_scaled([(226, 200), (226, 312), (318, 256)], k), fill=rgb + (255,))
    else:
        size = (250 if len(sym) == 1 else 170) * k
        d.text((canvas / 2, canvas / 2), sym, font=_font(size), fill=ink, anchor="mm")

    img = img.resize((OUT, OUT), Image.LANCZOS)
    bg = Image.new("RGB", (OUT, OUT), (255, 255, 255))
    bg.paste(img, mask=img.split()[3])
    buf = BytesIO()
    bg.save(buf, "PNG")
    buf.seek(0)
    buf.name = "logo.png"
    return buf

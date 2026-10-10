"""Generate the built-in ORIGINAL banner images (soft gradients, glow, sakura petals, sparkles).

Run:  python scripts/make_banners.py      (needs Pillow; output goes to assets/banners/*.jpg)
The bot uploads them to Telegram once with /uploadbanners. Replace any of them with your own art using /setbanner.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "assets" / "banners"
W, H = 1280, 640
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
CJK = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"

# key: (title, subtitle, japanese flourish, top colour, bottom colour, accent)
SPECS = {
    "menu": ("MAIN MENU", "everything in one place", "メニュー", (255, 140, 190), (88, 70, 190), (255, 225, 240)),
    "welcome": ("WELCOME", "so happy you're here", "ようこそ", (255, 170, 150), (150, 80, 200), (255, 240, 220)),
    "help": ("HELP CENTER", "browse every command", "ヘルプ", (120, 200, 255), (70, 80, 200), (225, 245, 255)),
    "economy": ("ECONOMY", "earn · save · spend", "経済", (255, 200, 90), (200, 90, 90), (255, 245, 205)),
    "games": ("GAME ARCADE", "play, win, repeat", "ゲーム", (140, 120, 255), (40, 40, 120), (230, 225, 255)),
    "entertainment": ("FUN ZONE", "jokes · facts · vibes", "楽しい", (255, 120, 170), (120, 60, 180), (255, 225, 240)),
    "dating": ("LOVE LOUNGE", "meet someone lovely", "恋", (255, 120, 150), (170, 50, 130), (255, 225, 232)),
    "moderation": ("MODERATION", "keeping things safe", "守る", (110, 160, 230), (30, 50, 110), (220, 235, 255)),
    "management": ("GROUP TOOLS", "your community, your way", "管理", (120, 220, 200), (40, 100, 140), (220, 255, 245)),
    "utility": ("UTILITIES", "handy little helpers", "便利", (130, 210, 255), (60, 110, 190), (225, 245, 255)),
    "rentals": ("PLANS", "pick your power", "プラン", (255, 190, 120), (170, 80, 140), (255, 240, 220)),
    "vip": ("VIP CLUB", "exclusive access", "特別", (255, 215, 110), (110, 60, 160), (255, 245, 210)),
    "verification": ("VERIFICATION", "trusted members only", "認証", (130, 220, 170), (40, 110, 130), (225, 255, 238)),
    "payments": ("PAYMENTS", "simple and secure", "支払い", (150, 200, 255), (70, 70, 170), (230, 240, 255)),
    "settings": ("SETTINGS", "make it yours", "設定", (190, 170, 255), (70, 60, 150), (238, 232, 255)),
    "owner": ("OWNER PANEL", "full control", "管理者", (255, 120, 120), (80, 30, 90), (255, 225, 225)),
    "admin": ("ADMIN TOOLS", "roles · modules · logs", "管理", (140, 190, 255), (60, 60, 150), (228, 238, 255)),
}


def gradient(top, bottom) -> Image.Image:
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        for x in range(W):
            t = min(1.0, max(0.0, (y / H) * 0.75 + (x / W) * 0.25))
            px[x, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return img


def glow(base: Image.Image, rng: random.Random, accent) -> Image.Image:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(14):
        r = rng.randint(40, 140)
        x, y = rng.randint(0, W), rng.randint(0, H)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(*accent, rng.randint(25, 70)))
    layer = layer.filter(ImageFilter.GaussianBlur(22))
    return Image.alpha_composite(base.convert("RGBA"), layer)


def petal(size: int, color) -> Image.Image:
    p = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(p)
    d.ellipse((size * 0.55, size * 0.25, size * 1.45, size * 1.75), fill=(*color, 210))
    d.ellipse((size * 0.85, size * 0.25, size * 1.15, size * 0.75), fill=(255, 255, 255, 70))
    return p


def sparkle(d: ImageDraw.ImageDraw, x: int, y: int, r: int, color) -> None:
    pts = []
    for i in range(8):
        ang = math.pi / 4 * i
        rad = r if i % 2 == 0 else r * 0.28
        pts.append((x + math.cos(ang) * rad, y + math.sin(ang) * rad))
    d.polygon(pts, fill=(*color, 235))


def render(key: str, spec: tuple) -> Image.Image:
    title, sub, jp, top, bottom, accent = spec
    rng = random.Random(key)  # same picture every run
    img = glow(gradient(top, bottom), rng, accent)
    for _ in range(26):  # drifting petals
        size = rng.randint(14, 34)
        p = petal(size, tuple(min(255, c + 40) for c in accent)).rotate(rng.randint(0, 359), expand=True)
        img.alpha_composite(p, (rng.randint(-20, W - 20), rng.randint(-20, H - 20)))
    d = ImageDraw.Draw(img)
    for _ in range(18):
        sparkle(d, rng.randint(30, W - 30), rng.randint(30, H - 30), rng.randint(6, 16), (255, 255, 255))
    # frame
    d.rounded_rectangle((26, 26, W - 26, H - 26), radius=36, outline=(255, 255, 255, 170), width=3)
    d.rounded_rectangle((44, 44, W - 44, H - 44), radius=28, outline=(255, 255, 255, 70), width=1)
    # text
    f_title, f_sub, f_jp = (ImageFont.truetype(BOLD, 112), ImageFont.truetype(BOLD, 42), ImageFont.truetype(CJK, 52))
    for font, text, y, fill in ((f_jp, jp, 150, (255, 255, 255, 235)), (f_title, title, 235, (255, 255, 255, 255)),
                                (f_sub, sub, 395, (255, 255, 255, 230))):
        w = d.textlength(text, font=font)
        x = (W - w) / 2
        d.text((x + 4, y + 5), text, font=font, fill=(40, 20, 80, 120))  # soft shadow
        d.text((x, y), text, font=font, fill=fill)
    line_w = 360
    d.rounded_rectangle(((W - line_w) / 2, 372, (W + line_w) / 2, 378), radius=3, fill=(*accent, 255))
    return img.convert("RGB")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for key, spec in SPECS.items():
        render(key, spec).save(OUT / f"{key}.jpg", quality=86, optimize=True)
        print("wrote", key)


if __name__ == "__main__":
    main()

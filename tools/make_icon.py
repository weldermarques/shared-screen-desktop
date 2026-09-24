"""Gera assets/icon.ico (monitor estilizado). Rode uma vez: python tools/make_icon.py"""

from pathlib import Path

from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 8, S - 8, S - 8), radius=56, fill=(91, 140, 255, 255))
# monitor
d.rounded_rectangle((48, 58, S - 48, 170), radius=14, fill=(255, 255, 255, 255))
d.rounded_rectangle((60, 70, S - 60, 158), radius=8, fill=(13, 15, 20, 255))
d.rectangle((116, 170, 140, 194), fill=(255, 255, 255, 255))
d.rounded_rectangle((84, 192, S - 84, 206), radius=6, fill=(255, 255, 255, 255))
# ondas de transmissão
for i, r in enumerate((18, 34, 50)):
    box = (128 - r, 114 - r, 128 + r, 114 + r)
    d.arc(box, 220, 320, fill=(91, 140, 255, 255 - i * 60), width=8)
d.ellipse((120, 106, 136, 122), fill=(91, 140, 255, 255))

out = Path(__file__).resolve().parents[1] / "assets" / "icon.ico"
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save(out.with_suffix(".png"))
print("ok", out)

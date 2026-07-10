"""Generate a simple square PostPilot logo (logo.png). Run: py make_logo.py"""
from PIL import Image, ImageDraw, ImageFont

img = Image.new("RGB", (400, 400), "#0f1117")
d = ImageDraw.Draw(img)
d.rounded_rectangle([20, 20, 380, 380], radius=70, fill="#4f8ff7")
try:
    font = ImageFont.truetype("arialbd.ttf", 220)
except Exception:
    font = ImageFont.load_default()
d.text((200, 185), "P", font=font, anchor="mm", fill="white")
img.save("logo.png")
print("Saved logo.png - upload this on the LinkedIn app form.")

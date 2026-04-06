"""Download Poppins font family from Google Fonts GitHub."""
import os
import ssl
import urllib.request

FONTS_DIR = os.path.dirname(os.path.abspath(__file__))

# Poppins - premium geometric sans-serif, open source (OFL)
BASE = "https://github.com/google/fonts/raw/main/ofl/poppins"
FONTS = {
    "Poppins-Bold.ttf": f"{BASE}/Poppins-Bold.ttf",
    "Poppins-SemiBold.ttf": f"{BASE}/Poppins-SemiBold.ttf",
    "Poppins-Medium.ttf": f"{BASE}/Poppins-Medium.ttf",
    "Poppins-Regular.ttf": f"{BASE}/Poppins-Regular.ttf",
}

ctx = ssl.create_default_context()

for name, url in FONTS.items():
    path = os.path.join(FONTS_DIR, name)
    if os.path.exists(path) and os.path.getsize(path) > 10000:
        print(f"  [skip] {name} already exists ({os.path.getsize(path)} bytes)")
        continue
    print(f"  Downloading {name}...")
    try:
        urllib.request.urlretrieve(url, path)
        print(f"  -> {os.path.getsize(path)} bytes")
    except Exception as e:
        print(f"  FAILED: {e}")

print("Font download complete!")

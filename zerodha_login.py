from kiteconnect import KiteConnect
import webbrowser

API_KEY = "uajn1bjuesx08yga"
API_SECRET = "p41kb36oyutrfx0r048t0j4fsj54ecax"

kite = KiteConnect(api_key=API_KEY)

print("Opening Zerodha login page...")
webbrowser.open(kite.login_url())

request_token = input("Paste request_token from redirected URL: ").strip()

data = kite.generate_session(request_token, api_secret=API_SECRET)
kite.set_access_token(data["access_token"])

with open("access_token.txt", "w") as f:
    f.write(data["access_token"])

print("✅ Access token generated successfully")
print("ACCESS TOKEN:", data["access_token"])
print("✅ Token saved to access_token.txt")

import requests
import urllib.parse

# 1. Credentials
CLIENT_ID = "YOUR_CLIENT_ID"
CLIENT_SECRET = "YOUR_CLIENT_SECRET"

# 2. PASTE THE CODE FROM YOUR BROWSER URL HERE:
RAW_AUTH_CODE = "YOUR_AUTH_CODE"

# 3. Make the exchange
url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
data = {
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "code": urllib.parse.unquote(RAW_AUTH_CODE),
    "grant_type": "authorization_code",
    "redirect_uri": "http://localhost:8080",
    "scope": "https://ads.microsoft.com/msads.manage offline_access"
}

print("Trading Auth Code for Tokens...")
response = requests.post(url, data=data)
tokens = response.json()

if "refresh_token" in tokens:
    print("\n✅ SUCCESS! Here is your true Refresh Token:\n")
    print(tokens["refresh_token"])
    print("\n>>> COPY THE STRING ABOVE THIS LINE AND PUT IT IN SECRET MANAGER <<<")
else:
    print("\n❌ FAILED. Microsoft responded with:")
    print(tokens)
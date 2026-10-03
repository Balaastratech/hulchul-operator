import os
import json
import urllib.request
import dotenv
import google.auth
import google.auth.transport.requests

dotenv.load_dotenv(r"C:\Balaastra\hulchul-operator\.env")
project = os.environ.get("GOOGLE_CLOUD_PROJECT", "ai-negotiation-copilot")
location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
model = "gemini-2.5-flash"

creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
req = google.auth.transport.requests.Request()
creds.refresh(req)

url = f"https://aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent"
body = {
    "contents": [{"role": "user", "parts": [{"text": 'Return JSON: {"ping": "pong"}'}]}],
    "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}
}
req_obj = urllib.request.Request(
    url,
    json.dumps(body).encode("utf-8"),
    {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}
)
resp = urllib.request.urlopen(req_obj, timeout=30)
data = json.load(resp)
print("HTTP", resp.status)
print("Text:", data["candidates"][0]["content"]["parts"][0]["text"])
print("Usage:", data.get("usageMetadata"))

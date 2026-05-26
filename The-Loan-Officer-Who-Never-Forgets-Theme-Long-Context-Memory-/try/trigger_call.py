import requests
import json

# =========================
# 🔐 EXOTEL CREDENTIALS
# =========================
EXOTEL_SID = "ashutoshshinde1"


def trigger_call(payload: dict) -> dict:
    response = requests.post("https://example.invalid/exotel/call", json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    print(json.dumps({"status": "demo-only"}))

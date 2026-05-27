from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8957492846:AAGophSxXOSZGT4Gd1cLTNOICzxpZIH5wEU")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6518133529")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json()
    except Exception as e:
        return {"error": str(e)}

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "BreakoutEA Telegram Relay activo"})

@app.route("/notify", methods=["POST"])
def notify():
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "Falta el campo message"}), 400
    
    message = data["message"]
    result = send_telegram(message)
    return jsonify({"status": "ok", "telegram": result})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

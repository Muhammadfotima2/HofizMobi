from flask import Flask, request, jsonify

app = Flask(__name__)

# Проверка, что сервер жив
@app.route("/", methods=["GET"])
def home():
    return "✅ Сервер работает! Попробуй /send-fcm-test"

# GET для быстрой проверки в браузере (должен вернуть OK)
@app.route("/send-fcm-test", methods=["GET"])
def send_fcm_test():
    return jsonify({"ok": True, "hint": "Для отправки пуша используйте POST на /send-fcm"})

# Тот самый маршрут, который вы дергаете CURL'ом
@app.route("/send-fcm", methods=["POST"])
def send_fcm():
    try:
        data = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "received": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

from flask import Flask, request, jsonify

app = Flask(__name__)

# Тестовый маршрут (чтобы проверить, что сервер живой)
@app.route("/", methods=["GET"])
def home():
    return "✅ Сервер работает! Попробуй /send-fcm"

# Маршрут для пушей
@app.route("/send-fcm", methods=["POST"])
def send_fcm():
    try:
        data = request.json

        # Забираем поля из запроса
        customer_name = data.get("customerName", "Неизвестный")
        phone = data.get("phone", "—")
        comment = data.get("comment", "—")
        total = data.get("total", "0")
        currency = data.get("currency", "TJS")

        # Здесь будет логика отправки FCM (пока только выводим)
        print("📩 Новый заказ:")
        print("Имя:", customer_name)
        print("Телефон:", phone)
        print("Комментарий:", comment)
        print("Сумма:", total, currency)

        return jsonify({
            "status": "ok",
            "message": "Уведомление обработано",
            "data": data
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

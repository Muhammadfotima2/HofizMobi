import os, json
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, messaging

app = Flask(__name__)

# --- Инициализация Firebase Admin ---
_firebase_app = None
try:
    sa_raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if sa_raw:
        cred = credentials.Certificate(json.loads(sa_raw))
        _firebase_app = firebase_admin.initialize_app(cred)
        print("✅ Firebase Admin инициализирован")
    else:
        print("⚠️ FIREBASE_SERVICE_ACCOUNT не задан")
except Exception as e:
    print("⚠️ Ошибка Firebase Admin:", e)


# --- Эндпоинт для отправки пушей ---
@app.post("/send-fcm")
def send_fcm():
    if _firebase_app is None and not firebase_admin._apps:
        return jsonify({"ok": False, "error": "Firebase Admin не инициализирован"}), 500

    data = request.get_json(silent=True) or {}

    # Достаём поля
    customer = str(data.get("customerName", "")).strip()
    phone    = str(data.get("phone", "")).strip()
    comment  = str(data.get("comment", "")).strip()
    total    = str(data.get("total", "")).strip()
    currency = str(data.get("currency", "TJS")).strip()

    # --- Аккуратный текст с эмодзи ---
    body_text = f"👤 Имя: {customer}\n📞 Номер: {phone}\n💬 Комментарий: {comment}\n💰 Сумма: {total} {currency}"

    # --- Формируем сообщение ---
    msg = messaging.Message(
        topic="admin",
        notification=messaging.Notification(
            title="📦 Новый заказ",
            body=body_text
        ),
        android=messaging.AndroidConfig(
            notification=messaging.AndroidNotification(
                channel_id="orders_high",
                sound="default",
            )
        ),
        apns=messaging.APNSConfig(
            payload=messaging.APNSPayload(
                aps=messaging.Aps(sound="default")
            )
        ),
        data={
            "customerName": customer,
            "phone": phone,
            "comment": comment,
            "total": total,
            "currency": currency,
        }
    )

    try:
        resp = messaging.send(msg)
        return jsonify({"ok": True, "message_id": resp, "sent": body_text})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# --- Запуск ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

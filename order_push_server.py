# order_push_server.py
import os
import json
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, messaging

# === Firebase Admin инициализация из ENV ===
svc_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
if not svc_json:
    raise RuntimeError("FIREBASE_SERVICE_ACCOUNT env var is missing")
cred = credentials.Certificate(json.loads(svc_json))
firebase_admin.initialize_app(cred)

app = Flask(__name__)

def send_push_to_admin(title: str, body: str, data: dict | None = None):
    """Отправить уведомление всем, кто подписан на тему 'admin'."""
    msg = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        topic="admin",
        data={k: str(v) for k, v in (data or {}).items()},
        android=messaging.AndroidConfig(
            notification=messaging.AndroidNotification(channel_id="default_channel")
        ),
    )
    resp = messaging.send(msg)
    print("✅ FCM sent (topic admin):", resp)
    return resp

def format_body(customer: str, phone: str, comment: str, total: str, currency: str) -> str:
    """Собираем текст уведомления красиво, без пустых строк"""
    lines = [
        f"Имя: {customer}",
        f"Номер: {phone}",
    ]
    if comment:  # только если комментарий не пустой
        lines.append(f"Комментарий: {comment}")
    lines.append(f"Сумма: {total} {currency}")
    return "\n".join(lines)

@app.post("/send-order")
def send_order():
    """Принять заказ и отправить пуш в тему 'admin'."""
    p = request.get_json(force=True, silent=True) or {}
    order_id = p.get("orderId", "N/A")
    customer = p.get("customerName", "Клиент")
    phone = p.get("phone", "—")
    comment = p.get("comment", "")
    total = p.get("total", 0)
    currency = p.get("currency", "TJS")

    title = "📦 Новый заказ"
    body  = format_body(customer, phone, comment, total, currency)

    try:
        send_push_to_admin(title, body, {"orderId": order_id})
        return jsonify({"ok": True}), 200
    except Exception as e:
        print("❌ FCM error:", e)
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/subscribe-token")
def subscribe_token():
    """Подписать конкретный FCM-токен на тему 'admin'."""
    p = request.get_json(force=True, silent=True) or {}
    token = p.get("token")
    if not token:
        return jsonify({"ok": False, "error": "no token"}), 400
    res = messaging.subscribe_to_topic([token], "admin")
    res_dict = getattr(res, '__dict__', {})
    return jsonify({"ok": True, "res": res_dict}), 200

@app.post("/send-to-token")
def send_to_token():
    """Отправить пуш напрямую на указанный токен (для тестов)."""
    p = request.get_json(force=True, silent=True) or {}
    token = p.get("token")
    title = p.get("title", "Тест")
    body  = p.get("body", "Привет!")
    if not token:
        return jsonify({"ok": False, "error": "no token"}), 400
    msg = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        token=token,
        android=messaging.AndroidConfig(
            notification=messaging.AndroidNotification(channel_id="default_channel")
        ),
    )
    resp = messaging.send(msg)
    print("✅ FCM sent (token):", resp)
    return jsonify({"ok": True, "resp": resp}), 200

@app.get("/")
def root():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)

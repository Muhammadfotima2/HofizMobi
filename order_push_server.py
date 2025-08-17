# order_push_server.py
import os
import json
from flask import Flask, request, jsonify, Response
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
        notification=messaging.Notification(
            title=title,
            body=body
        ),
        topic="admin",
        data={k: str(v) for k, v in (data or {}).items()},
        android=messaging.AndroidConfig(
            notification=messaging.AndroidNotification(
                channel_id="orders_high"
            )
        ),
    )
    resp = messaging.send(msg)
    print("✅ FCM sent (topic admin):", resp)
    return resp

def format_body(customer: str, phone: str, comment: str, total: str, currency: str) -> str:
    """Формируем текст уведомления c эмодзи"""
    lines = []
    if customer:
        lines.append(f"👤 Имя: {customer}")
    if phone:
        lines.append(f"📞 Номер: {phone}")
    if comment:
        lines.append(f"💬 Комментарий: {comment}")
    if total:
        lines.append(f"💵 Сумма: {total} {currency}")
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

    title = "💼 Новый заказ"
    body  = format_body(customer, phone, comment, total, currency)

    try:
        send_push_to_admin(title, body, {"orderId": order_id})
        return Response(json.dumps({"ok": True}, ensure_ascii=False),
                        content_type="application/json; charset=utf-8")
    except Exception as e:
        print("❌ FCM error:", e)
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False),
                        status=500, content_type="application/json; charset=utf-8")

@app.post("/subscribe-token")
def subscribe_token():
    """Подписать конкретный FCM-токен на тему 'admin'."""
    p = request.get_json(force=True, silent=True) or {}
    token = p.get("token")
    if not token:
        return Response(json.dumps({"ok": False, "error": "no token"}, ensure_ascii=False),
                        status=400, content_type="application/json; charset=utf-8")
    res = messaging.subscribe_to_topic([token], "admin")
    res_dict = getattr(res, '__dict__', {})
    return Response(json.dumps({"ok": True, "res": res_dict}, ensure_ascii=False),
                    content_type="application/json; charset=utf-8")

@app.post("/send-to-token")
def send_to_token():
    """Отправить пуш напрямую на указанный токен (для тестов)."""
    p = request.get_json(force=True, silent=True) or {}
    token = p.get("token")
    title = p.get("title", "Тест")
    body  = p.get("body", "Привет!")
    if not token:
        return Response(json.dumps({"ok": False, "error": "no token"}, ensure_ascii=False),
                        status=400, content_type="application/json; charset=utf-8")
    msg = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        token=token,
        android=messaging.AndroidConfig(
            notification=messaging.AndroidNotification(channel_id="orders_high")
        ),
    )
    resp = messaging.send(msg)
    print("✅ FCM sent (token):", resp)
    return Response(json.dumps({"ok": True, "resp": resp}, ensure_ascii=False),
                    content_type="application/json; charset=utf-8")

@app.get("/")
def root():
    return Response("OK", content_type="text/plain; charset=utf-8")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)

# order_push_server.py
import os
import json
import base64
from flask import Flask, request, Response

import firebase_admin
from firebase_admin import credentials, messaging

# --- Инициализация Firebase Admin (устойчиво к разным форматам ключа) ---
def _load_firebase_cred():
    """
    Пытаемся взять ключ из:
    1) FIREBASE_SERVICE_ACCOUNT (RAW JSON)
    2) FIREBASE_SERVICE_ACCOUNT_B64 (base64 от JSON)
    3) serviceAccountKey.json (локальный файл — для локальной разработки)
    """
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    b64 = os.environ.get("FIREBASE_SERVICE_ACCOUNT_B64")

    # 1) RAW JSON в переменной
    if raw:
        try:
            data = json.loads(raw)
            return credentials.Certificate(data)
        except Exception as e:
            print("⚠️  FIREBASE_SERVICE_ACCOUNT: не смог распарсить JSON:", e)

    # 2) base64 JSON в переменной
    if b64:
        try:
            decoded = base64.b64decode(b64).decode("utf-8")
            data = json.loads(decoded)
            return credentials.Certificate(data)
        except Exception as e:
            print("⚠️  FIREBASE_SERVICE_ACCOUNT_B64: не смог декодировать/распарсить JSON:", e)

    # 3) Файл (локально)
    if os.path.exists("serviceAccountKey.json"):
        try:
            return credentials.Certificate("serviceAccountKey.json")
        except Exception as e:
            print("⚠️  serviceAccountKey.json найден, но не читается:", e)

    raise RuntimeError(
        "Не найден Firebase service account. "
        "Задайте ENV FIREBASE_SERVICE_ACCOUNT (RAW JSON) или FIREBASE_SERVICE_ACCOUNT_B64 (base64 JSON), "
        "или положите файл serviceAccountKey.json."
    )

# Инициализация (избегаем повторной инициализации под gunicorn)
if not firebase_admin._apps:
    cred = _load_firebase_cred()
    firebase_admin.initialize_app(cred)

app = Flask(__name__)

def send_push_to_admin(title: str, customer: str, phone: str, comment: str, total: str, currency: str, data: dict | None = None):
    # Тело уведомления
    lines = []
    if customer:
        lines.append(f"👤 Имя: {customer}")
    if phone:
        lines.append(f"📞 Номер: {phone}")
    if comment:
        lines.append(f"💬 Комментарий: {comment}")
    if total:
        lines.append(f"💵 Сумма: {total} {currency}")
    body_text = "\n".join(lines) if lines else "Новый заказ"

    msg = messaging.Message(
        notification=messaging.Notification(title=title, body=body_text),
        topic="admin",
        data={k: str(v) for k, v in (data or {}).items()},
        android=messaging.AndroidConfig(
            notification=messaging.AndroidNotification(channel_id="orders_high")
        ),
    )
    resp = messaging.send(msg)
    print("✅ FCM sent (topic=admin):", resp)
    return resp

@app.post("/send-order")
def send_order():
    p = request.get_json(force=True, silent=True) or {}

    order_id = p.get("orderId", "N/A")
    customer = p.get("customerName", "Клиент")
    phone = p.get("phone") or p.get("phoneNumber") or p.get("number") or "—"
    comment = p.get("comment", "")
    total = str(p.get("total", ""))  # строка
    currency = p.get("currency", "TJS")
    title = "💼 Новый заказ"

    try:
        send_push_to_admin(
            title=title,
            customer=customer,
            phone=phone,
            comment=comment,
            total=total,
            currency=currency,
            data={"orderId": order_id}
        )
        return Response(json.dumps({"ok": True}, ensure_ascii=False),
                        content_type="application/json; charset=utf-8")
    except Exception as e:
        print("❌ FCM error:", e)
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False),
                        status=500, content_type="application/json; charset=utf-8")

@app.post("/subscribe-token")
def subscribe_token():
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
    p = request.get_json(force=True, silent=True) or {}
    token = p.get("token")
    title = p.get("title", "Тест")
    customer = p.get("customer", "—")
    phone = p.get("phone") or p.get("phoneNumber") or p.get("number") or "—"
    comment = p.get("comment", "")
    total = str(p.get("total", ""))
    currency = p.get("currency", "TJS")

    if not token:
        return Response(json.dumps({"ok": False, "error": "no token"}, ensure_ascii=False),
                        status=400, content_type="application/json; charset=utf-8")

    lines = []
    if customer:
        lines.append(f"👤 Имя: {customer}")
    if phone:
        lines.append(f"📞 Номер: {phone}")
    if comment:
        lines.append(f"💬 Комментарий: {comment}")
    if total:
        lines.append(f"💵 Сумма: {total} {currency}")
    body_text = "\n".join(lines) if lines else "Сообщение"

    msg = messaging.Message(
        notification=messaging.Notification(title=title, body=body_text),
        data={
            "title": title,
            "body": body_text,
            "customer": customer,
            "phone": str(phone),
            "comment": comment,
            "total": str(total),
            "currency": currency
        },
        token=token,
        android=messaging.AndroidConfig(
            notification=messaging.AndroidNotification(channel_id="orders_high")
        ),
    )
    resp = messaging.send(msg)
    print("✅ FCM sent (token):", resp)
    return Response(json.dumps({"ok": True, "resp": resp}, ensure_ascii=False),
                    content_type="application/json; charset=utf-8")

@app.get("/health")
def health():
    return Response("OK", content_type="text/plain; charset=utf-8")

@app.get("/")
def root():
    return Response("OK", content_type="text/plain; charset=utf-8")

if __name__ == "__main__":
    # Для локального запуска: python order_push_server.py
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)

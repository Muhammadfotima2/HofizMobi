# order_push_server.py
import os, json
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, messaging

# === Firebase Admin из переменной окружения ===
svc_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
if not svc_json:
    raise RuntimeError("FIREBASE_SERVICE_ACCOUNT env var is missing")
cred = credentials.Certificate(json.loads(svc_json))
firebase_admin.initialize_app(cred)

app = Flask(__name__)

# ---------- УТИЛИТА ОТПРАВКИ НА ТЕМУ admin (БЕЗ ФОТО, БЕЗ ЭМОДЗИ) ----------
def send_push_to_admin(*, customer_name: str, phone: str, comment: str, total: str, currency: str = "TJS"):
    title = "Новый заказ"  # стандартный заголовок без эмодзи
    # Аккуратный текст по строкам (системное уведомление само выравнивает)
    body = (
        f"Имя: {customer_name}\n"
        f"Номер: {phone}\n"
        f"Комментарий: {comment}\n"
        f"Сумма: {total} {currency}"
    )

    msg = messaging.Message(
        topic="admin",
        notification=messaging.Notification(
            title=title,
            body=body,
            image=None  # гарантированно без картинок
        ),
        android=messaging.AndroidConfig(
            notification=messaging.AndroidNotification(
                channel_id="orders_high",  # ваш звуковой канал в приложении
                sound="default"
            )
        ),
        apns=messaging.APNSConfig(
            payload=messaging.APNSPayload(
                aps=messaging.Aps(sound="default")
            )
        ),
        # дублируем данные для вашего overlay в приложении (если нужно)
        data={
            "customerName": customer_name or "",
            "phone": phone or "",
            "comment": comment or "",
            "total": total or "",
            "currency": currency or "TJS",
        }
    )
    resp = messaging.send(msg)
    print("✅ FCM sent (topic admin):", resp)
    return resp

# ---------- РОУТ ДЛЯ ПРИЁМА ЗАКАЗА И ОТПРАВКИ ПУША ----------
@app.post("/send-order")
def send_order():
    p = request.get_json(force=True, silent=True) or {}
    customer = str(p.get("customerName", "")).strip()
    phone    = str(p.get("phone", "")).strip()
    comment  = str(p.get("comment", "")).strip()
    total    = str(p.get("total", "")).strip()
    currency = str(p.get("currency", "TJS")).strip() or "TJS"

    try:
        send_push_to_admin(
            customer_name=customer,
            phone=phone,
            comment=comment,
            total=total,
            currency=currency,
        )
        return jsonify({"ok": True})
    except Exception as e:
        print("❌ FCM error:", e)
        return jsonify({"ok": False, "error": str(e)}), 500

# ---------- ОСТАЛЬНЫЕ ПОЛЕЗНЫЕ РОУТЫ (по желанию) ----------
@app.post("/subscribe-token")
def subscribe_token():
    p = request.get_json(force=True, silent=True) or {}
    token = p.get("token")
    if not token:
        return jsonify({"ok": False, "error": "no token"}), 400
    res = messaging.subscribe_to_topic([token], "admin")
    return jsonify({"ok": True, "res": getattr(res, "__dict__", {})})

@app.post("/send-to-token")
def send_to_token():
    p = request.get_json(force=True, silent=True) or {}
    token = p.get("token")
    customer = str(p.get("customerName", "")).strip()
    phone    = str(p.get("phone", "")).strip()
    comment  = str(p.get("comment", "")).strip()
    total    = str(p.get("total", "")).strip()
    currency = str(p.get("currency", "TJS")).strip() or "TJS"
    if not token:
        return jsonify({"ok": False, "error": "no token"}), 400

    title = "Новый заказ"
    body = (
        f"Имя: {customer}\n"
        f"Номер: {phone}\n"
        f"Комментарий: {comment}\n"
        f"Сумма: {total} {currency}"
    )

    msg = messaging.Message(
        token=token,
        notification=messaging.Notification(title=title, body=body, image=None),
        android=messaging.AndroidConfig(
            notification=messaging.AndroidNotification(channel_id="orders_high", sound="default")
        ),
        apns=messaging.APNSConfig(payload=messaging.APNSPayload(aps=messaging.Aps(sound="default"))),
        data={"customerName": customer, "phone": phone, "comment": comment, "total": total, "currency": currency},
    )
    resp = messaging.send(msg)
    return jsonify({"ok": True, "resp": resp})

@app.get("/")
def root():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)

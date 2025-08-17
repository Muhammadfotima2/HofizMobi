import os
import json
import base64
from flask import Flask, request, Response

import firebase_admin
from firebase_admin import credentials, messaging
from firebase_admin._messaging_utils import UnregisteredError

# --- Загрузка service account ---
def _load_firebase_cred():
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    b64 = os.environ.get("FIREBASE_SERVICE_ACCOUNT_B64")

    if raw:
        return credentials.Certificate(json.loads(raw))
    if b64:
        decoded = base64.b64decode(b64).decode("utf-8")
        return credentials.Certificate(json.loads(decoded))
    if os.path.exists("serviceAccountKey.json"):
        return credentials.Certificate("serviceAccountKey.json")

    raise RuntimeError("Нет ключа Firebase (ENV или serviceAccountKey.json)")

# --- Инициализация Firebase ---
if not firebase_admin._apps:
    cred = _load_firebase_cred()
    firebase_admin.initialize_app(cred)

app = Flask(__name__)

# --- Утилиты ---
def first_nonempty(d: dict, *keys) -> str | None:
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None

def format_body(customer: str, phone: str, comment: str, total: str, currency: str) -> str:
    lines = []
    if customer:
        lines.append(f"👤 Имя: {customer}")
    if phone:
        lines.append(f"📞 Номер: {phone}")
    if comment:
        lines.append(f"💬 Комментарий: {comment}")
    if total:
        lines.append(f"💵 Сумма: {total} {currency}")
    return "\n".join(lines) if lines else "Сообщение"

def send_push_to_admin(title: str, customer: str, phone: str, comment: str, total: str, currency: str, data: dict | None = None):
    body_text = format_body(customer, phone, comment, total, currency)
    msg = messaging.Message(
        notification=messaging.Notification(title=title, body=body_text),
        topic="admin",
        data={k: str(v) for k, v in (data or {}).items()},
        # если канал настроен в приложении, можно вернуть channel_id
        android=messaging.AndroidConfig(priority="high"),
    )
    resp = messaging.send(msg)
    print("✅ FCM sent (topic=admin):", resp, flush=True)
    return resp

# --- Роуты ---
@app.post("/send-order")
def send_order():
    p = request.get_json(force=True, silent=True) or {}
    print("📥 /send-order payload:", p, flush=True)

    order_id = first_nonempty(p, "orderId", "order_id", "id") or "N/A"
    customer = first_nonempty(p, "customerName", "customer_name", "name", "customer") or "Клиент"
    phone = first_nonempty(p, "phone", "phoneNumber", "number", "tel", "contact") or "—"
    comment = first_nonempty(p, "comment", "comments", "remark", "note") or ""
    total = first_nonempty(p, "total", "sum", "amount") or ""
    currency = first_nonempty(p, "currency", "curr") or "TJS"
    title = "💼 Новый заказ"

    try:
        send_push_to_admin(
            title=title,
            customer=customer,
            phone=phone,
            comment=comment,
            total=str(total),
            currency=currency,
            data={"orderId": order_id}
        )
        return Response(json.dumps({"ok": True}, ensure_ascii=False),
                        content_type="application/json; charset=utf-8")
    except Exception as e:
        print("❌ FCM error:", e, flush=True)
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False),
                        status=500, content_type="application/json; charset=utf-8")

@app.post("/subscribe-token")
def subscribe_token():
    p = request.get_json(force=True, silent=True) or {}
    print("📥 /subscribe-token payload:", p, flush=True)

    token = p.get("token")
    if not token:
        return Response(json.dumps({"ok": False, "error": "no token"}, ensure_ascii=False),
                        status=400, content_type="application/json; charset=utf-8")
    try:
        res = messaging.subscribe_to_topic([token], "admin")
        # Сериализуем только примитивы, иначе ErrorInfo ломает json.dumps
        out = {
            "success_count": getattr(res, "success_count", 0),
            "failure_count": getattr(res, "failure_count", 0),
            "errors": []
        }
        errors = getattr(res, "errors", []) or []
        for e in errors:
            out["errors"].append({
                "index": getattr(e, "index", None),
                "reason": getattr(e, "reason", None),
                "error_code": getattr(e, "error_code", None),
                "message": str(e),
            })
        return Response(json.dumps({"ok": True, "res": out}, ensure_ascii=False),
                        content_type="application/json; charset=utf-8")
    except Exception as ex:
        print("❌ subscribe-token error:", ex, flush=True)
        return Response(json.dumps({"ok": False, "error": str(ex)}, ensure_ascii=False),
                        status=500, content_type="application/json; charset=utf-8")

@app.post("/send-to-token")
def send_to_token():
    p = request.get_json(force=True, silent=True) or {}
    print("📥 /send-to-token payload:", p, flush=True)

    token = p.get("token")
    if not token:
        return Response(json.dumps({"ok": False, "error": "no token"}, ensure_ascii=False),
                        status=400, content_type="application/json; charset=utf-8")

    title = p.get("title", "Тест")
    customer = p.get("customer", "—")
    phone = first_nonempty(p, "phone", "phoneNumber", "number", "tel", "contact") or "—"
    comment = p.get("comment", "")
    total = str(p.get("total", ""))
    currency = p.get("currency", "TJS")

    body_text = format_body(customer, phone, comment, total, currency)
    msg = messaging.Message(
        notification=messaging.Notification(title=title, body=body_text),
        token=token,
        android=messaging.AndroidConfig(priority="high"),
        data={
            "title": title, "body": body_text,
            "customer": customer, "phone": str(phone),
            "comment": comment, "total": str(total), "currency": currency
        },
    )
    try:
        resp = messaging.send(msg)
        print("✅ FCM sent (to token):", resp, flush=True)
        return Response(json.dumps({"ok": True, "resp": resp}, ensure_ascii=False),
                        content_type="application/json; charset=utf-8")
    except UnregisteredError as ue:
        print("❌ Unregistered token:", ue, flush=True)
        return Response(json.dumps({
            "ok": False,
            "error": "unregistered_token",
            "hint": "Получите новый FirebaseMessaging.getToken() в приложении и повторите."
        }, ensure_ascii=False), status=400, content_type="application/json; charset=utf-8")
    except Exception as e:
        print("❌ send-to-token error:", e, flush=True)
        return Response(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False),
                        status=500, content_type="application/json; charset=utf-8")

@app.get("/health")
def health():
    return Response("OK", content_type="text/plain; charset=utf-8")

@app.get("/")
def root():
    return Response("OK", content_type="text/plain; charset=utf-8")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)

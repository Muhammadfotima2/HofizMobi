import os
import json
import base64
import concurrent.futures
from flask import Flask, request, Response

import firebase_admin
from firebase_admin import credentials, messaging, firestore
from firebase_admin._messaging_utils import UnregisteredError

# === Пул потоков для фоновых задач ===
EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)

# === Загрузка service account ===
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

# === Инициализация Firebase ===
if not firebase_admin._apps:
    cred = _load_firebase_cred()
    firebase_admin.initialize_app(cred)
db = firestore.client()

app = Flask(__name__)

# === Утилиты ===
def first_nonempty(d: dict, *keys) -> str | None:
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None

def to_str(v) -> str:
    # Нормализуем числа/None к строке, чтобы "0" не терялась
    if v is None:
        return ""
    return str(v)

def format_body(customer: str, phone: str, comment: str, total: str, currency: str) -> str:
    lines = []
    if customer:
        lines.append(f"👤 Имя: {customer}")
    if phone:
        lines.append(f"📞 Номер: {phone}")
    if comment:
        lines.append(f"💬 Комментарий: {comment}")
    # total может быть "0" — тоже показываем
    lines.append(f"💵 Сумма: {total} {currency}")
    return "\n".join(lines)

# === Отправка пуша админу ===
def send_push_to_admin(order_id: str, customer: str, phone: str,
                       comment: str, total: str, currency: str):
    title = "💼 Новый заказ"
    body_text = format_body(customer, phone, comment, total, currency)

    data_payload = {
        "title": title,
        "orderId": str(order_id),
        "customer": customer,
        "phone": phone,
        "comment": comment,
        "total": total,
        "currency": currency,
    }

    msg = messaging.Message(
        notification=messaging.Notification(
            title=title,
            body=body_text
        ),
        topic="admin",
        data=data_payload,
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                channel_id="orders_high"  # должен совпадать с клиентом
            ),
        ),
    )
    resp = messaging.send(msg)
    print(f"✅ FCM sent (topic=admin): {resp} | data={data_payload}", flush=True)
    return resp

# === Роуты ===
@app.post("/send-order")
def send_order():
    p = request.get_json(force=True, silent=True) or {}
    print("📥 /send-order payload:", p, flush=True)

    order_id = first_nonempty(p, "orderId", "order_id", "id") or "N/A"
    customer = first_nonempty(p, "customerName", "customer_name", "name", "customer") or "Клиент"

    phone_keys = [
        "phone", "phoneNumber", "phone_number", "customerPhone", "customer_phone",
        "number", "tel", "contact"
    ]
    phone = first_nonempty(p, *phone_keys) or "—"
    matched_key = next((k for k in phone_keys if str(p.get(k) or "").strip()), None)
    print(f"ℹ️ phone matched_key={matched_key} value={phone}", flush=True)

    comment  = to_str(first_nonempty(p, "comment", "comments", "remark", "note") or "")
    total    = to_str(first_nonempty(p, "total", "sum", "amount"))
    if total == "":  # гарантируем хотя бы "0"
        total = "0"
    currency = to_str(first_nonempty(p, "currency", "curr") or "TJS")

    # === Сохраняем заказ в Firestore (обязательно статус new и userId=system) ===
    try:
        doc_ref = db.collection("orders").document(str(order_id))
        order_doc = {
            "orderId": order_id,
            "customer": customer,
            "phone": phone,
            "comment": comment,
            "total": total,
            "currency": currency,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "status": "new",      # 👈 ключевое поле для админ-экрана
            "userId": "system",   # 👈 чтобы пройти правило allow create
        }
        doc_ref.set(order_doc)
        print(f"💾 Order saved to Firestore [order_id={order_id}] → {order_doc}", flush=True)
    except Exception as e:
        print("❌ Firestore save error:", e, flush=True)

    # === Пуш админу (фоном) ===
    def push_job():
        try:
            msg_id = send_push_to_admin(order_id, customer, phone, comment, total, currency)
            print(f"✅ push queued OK [order_id={order_id}] → msg_id={msg_id}", flush=True)
        except Exception as e:
            print(f"❌ push error [order_id={order_id}]: {e}", flush=True)

    EXECUTOR.submit(push_job)

    return Response(
        json.dumps({"ok": True, "queued": True}, ensure_ascii=False),
        content_type="application/json; charset=utf-8"
    )

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
        out = {
            "success_count": getattr(res, "success_count", 0),
            "failure_count": getattr(res, "failure_count", 0),
        }
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

    title    = to_str(p.get("title", "Тест"))
    customer = to_str(p.get("customer", "—"))
    phone    = to_str(first_nonempty(p, "phone", "phoneNumber", "phone_number", "number") or "—")
    comment  = to_str(p.get("comment", ""))
    total    = to_str(p.get("total", "0"))
    currency = to_str(p.get("currency", "TJS"))

    body_text = format_body(customer, phone, comment, total, currency)

    def push_job():
        try:
            msg = messaging.Message(
                notification=messaging.Notification(title=title, body=body_text),
                token=token,
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(channel_id="orders_high"),
                ),
                data={
                    "title": title,
                    "orderId": "test",
                    "customer": customer,
                    "phone": phone,
                    "comment": comment,
                    "total": total,
                    "currency": currency,
                },
            )
            resp = messaging.send(msg)
            print(f"✅ FCM sent (to token): {resp}", flush=True)
        except UnregisteredError as ue:
            print("❌ Unregistered token:", ue, flush=True)
        except Exception as e:
            print("❌ send-to-token error:", e, flush=True)

    EXECUTOR.submit(push_job)

    return Response(
        json.dumps({"ok": True, "queued": True}, ensure_ascii=False),
        content_type="application/json; charset=utf-8"
    )

@app.get("/orders")
def list_orders():
    """Вернуть список заказов из Firestore"""
    try:
        docs = db.collection("orders").order_by("createdAt", direction=firestore.Query.DESCENDING).stream()
        orders = [doc.to_dict() for doc in docs]
        return Response(json.dumps(orders, ensure_ascii=False, indent=2),
                        content_type="application/json; charset=utf-8")
    except Exception as e:
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

# order_push_server.py
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
    return "" if v is None else str(v)

def parse_total_number(v) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    s = s.replace("\u00A0", " ")
    filtered = [ch for ch in s if ch.isdigit() or ch in [".", ",", "-"]]
    s = "".join(filtered)
    if not s:
        return None
    if "." in s and "," in s:
        s = s.replace(",", "")
    elif "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None

def format_body(customer: str, phone: str, comment: str, total_text: str, currency: str) -> str:
    lines = []
    if customer: lines.append(f"👤 Имя: {customer}")
    if phone: lines.append(f"📞 Номер: {phone}")
    if comment: lines.append(f"💬 Комментарий: {comment}")
    lines.append(f"💵 Сумма: {total_text} {currency}")
    return "\n".join(lines)

# === Отправка пуша админу (только DATA) ===
def send_push_to_admin(order_id: str, customer: str, phone: str,
                       comment: str, total_text: str, currency: str):
    data_payload = {
        "title": "💼 Новый заказ",
        "orderId": str(order_id),
        "customer": customer,
        "phone": phone,
        "comment": comment,
        "total": total_text,
        "currency": currency,
    }
    msg = messaging.Message(
        topic="admin",
        data=data_payload,
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(channel_id="orders_high"),
        ),
        apns=messaging.APNSConfig(headers={"apns-priority": "10"}),
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
    customer = first_nonempty(p, "customerName", "name", "customer") or "Клиент"
    phone = first_nonempty(p, "phone", "phoneNumber", "customerPhone", "number") or "—"
    comment = to_str(first_nonempty(p, "comment", "note", "remark") or "")
    total_input = first_nonempty(p, "total", "sum", "amount")
    currency = to_str(first_nonempty(p, "currency", "curr") or "TJS")

    total_num = parse_total_number(total_input)
    if total_num is None:
        return Response(json.dumps({"ok": False, "error": "total required"}, ensure_ascii=False),
                        status=400, content_type="application/json; charset=utf-8")
    total_text = str(total_input).strip() if total_input else str(total_num)

    try:
        doc_ref = db.collection("orders").document(str(order_id))
        order_doc = {
            "orderId": order_id,
            "customer": customer,
            "phone": phone,
            "comment": comment,
            "currency": currency,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "status": "new",
            "userId": "system",
            "total": total_num,
            "totalText": total_text,
        }
        doc_ref.set(order_doc)
        print(f"💾 Order saved [id={order_id}] → {order_doc}", flush=True)
    except Exception as e:
        print("❌ Firestore save error:", e, flush=True)

    def push_job():
        try:
            send_push_to_admin(order_id, customer, phone, comment, total_text, currency)
        except Exception as e:
            print(f"❌ push error: {e}", flush=True)

    EXECUTOR.submit(push_job)
    return Response(json.dumps({"ok": True, "queued": True}, ensure_ascii=False),
                    content_type="application/json; charset=utf-8")

@app.post("/subscribe-token")
def subscribe_token():
    p = request.get_json(force=True, silent=True) or {}
    token = p.get("token")
    if not token:
        return Response(json.dumps({"ok": False, "error": "no token"}, ensure_ascii=False),
                        status=400, content_type="application/json; charset=utf-8")
    try:
        res = messaging.subscribe_to_topic([token], "admin")
        return Response(json.dumps({"ok": True, "res": {"success": res.success_count}}, ensure_ascii=False),
                        content_type="application/json; charset=utf-8")
    except Exception as ex:
        return Response(json.dumps({"ok": False, "error": str(ex)}, ensure_ascii=False),
                        status=500, content_type="application/json; charset=utf-8")

@app.get("/orders")
def list_orders():
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

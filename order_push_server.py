import os
import json
import threading
from flask import Flask, request, Response, send_from_directory
import firebase_admin
from firebase_admin import credentials, messaging

# --- Инициализация Firebase ---
if not firebase_admin._apps:
    if os.path.exists("serviceAccountKey.json"):
        cred = credentials.Certificate("serviceAccountKey.json")
    else:
        cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred)

app = Flask(__name__, static_folder="public")

# --- 🔑 Токен (жёстко зашит) ---
HARDCODED_ADMIN_TOKENS = [
    "d7xVXOxYQZC7orWjDN8IOu:APA91bHErCA6MYw0ZSSKy-R4deLPNRtcqOikcG4yY1CCNxNvteqabEYyWwx0UZoDGQRFozzvBMybR_3FJ6kQaPu1D5j15Qz5zACUn1wrF7hKJUzbddWDQeY"
]

def first_nonempty(d: dict, *keys):
    for k in keys:
        if k in d and d[k]:
            return d[k]
    return None

def format_body(customer, phone, comment, total, currency):
    return f"👤 {customer}\n📞 {phone}\n💰 {total} {currency}\n📝 {comment}"

def send_push_to_tokens(title: str, body_text: str, tokens: list[str], data=None):
    sent = 0
    last_msg_id = None
    for t in tokens:
        msg = messaging.Message(
            notification=messaging.Notification(title=title, body=body_text),
            token=t,
            android=messaging.AndroidConfig(priority="high"),
            data={k: str(v) for k, v in (data or {}).items()},
        )
        msg_id = messaging.send(msg)
        sent += 1
        last_msg_id = msg_id
        print(f"✅ FCM sent (to token): {t[:20]}... → {msg_id}", flush=True)
    return {"sent": sent, "last_msg_id": last_msg_id}

# --- healthcheck ---
@app.get("/")
def root():
    return "✅ Server is running"

# --- отправка заказа ---
@app.post("/send-order")
def send_order():
    p = request.get_json(force=True, silent=True) or {}
    print("📥 /send-order payload:", p, flush=True)

    order_id = first_nonempty(p, "orderId", "order_id", "id") or "N/A"
    customer = first_nonempty(p, "customerName", "customer_name", "name", "customer") or "Клиент"
    phone = first_nonempty(
        p,
        "phone", "phoneNumber", "phone_number", "customerPhone", "customer_phone",
        "number", "tel", "contact"
    ) or "—"
    comment = first_nonempty(p, "comment", "comments", "remark", "note") or ""
    total = first_nonempty(p, "total", "sum", "amount") or ""
    currency = first_nonempty(p, "currency", "curr") or "TJS"
    title = "💼 Новый заказ"

    def push_job():
        try:
            body_text = format_body(customer, phone, comment, str(total), currency)
            res = send_push_to_tokens(
                title=title,
                body_text=body_text,
                tokens=HARDCODED_ADMIN_TOKENS,
                data={"orderId": order_id}
            )
            print(f"✅ order push sent [order_id={order_id}] sent={res['sent']} last_msg_id={res['last_msg_id']}", flush=True)
        except Exception as e:
            print(f"❌ push error [order_id={order_id}]: {e}", flush=True)

    threading.Thread(target=push_job, daemon=True).start()

    return Response(
        json.dumps({"ok": True, "queued": True}, ensure_ascii=False),
        content_type="application/json; charset=utf-8"
    )

# --- отправка напрямую в токен (для теста) ---
@app.post("/send-to-token")
def send_to_token():
    p = request.get_json(force=True, silent=True) or {}
    token = first_nonempty(p, "token") or HARDCODED_ADMIN_TOKENS[0]
    title = first_nonempty(p, "title") or "🔔 Test"
    customer = first_nonempty(p, "customer", "name") or "Клиент"
    phone = first_nonempty(p, "phone") or "—"
    total = first_nonempty(p, "total") or ""
    currency = first_nonempty(p, "currency") or "TJS"
    comment = first_nonempty(p, "comment") or ""

    def push_job():
        try:
            body_text = format_body(customer, phone, comment, str(total), currency)
            res = send_push_to_tokens(title, body_text, [token], {"test": "1"})
            print(f"✅ test push sent to token → {res}", flush=True)
        except Exception as e:
            print(f"❌ push error (test): {e}", flush=True)

    threading.Thread(target=push_job, daemon=True).start()
    return Response(json.dumps({"ok": True, "queued": True}, ensure_ascii=False),
                    content_type="application/json; charset=utf-8")

# --- статика ---
@app.route("/<path:path>")
def static_proxy(path):
    return send_from_directory(app.static_folder, path)

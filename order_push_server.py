# ✅ Полный, рабочий order_push_server.py
# Поддержка заказов + products API (add/edit/delete)

import os, json, base64
import concurrent.futures
from flask import Flask, request, Response

import firebase_admin
from firebase_admin import credentials, messaging
from firebase_admin._messaging_utils import UnregisteredError

EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)

# 🔐 Firebase инициализация
def _load_firebase_cred():
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    b64 = os.environ.get("FIREBASE_SERVICE_ACCOUNT_B64")
    if raw:
        return credentials.Certificate(json.loads(raw))
    if b64:
        return credentials.Certificate(json.loads(base64.b64decode(b64).decode("utf-8")))
    if os.path.exists("serviceAccountKey.json"):
        return credentials.Certificate("serviceAccountKey.json")
    raise RuntimeError("Нет ключа Firebase")

if not firebase_admin._apps:
    firebase_admin.initialize_app(_load_firebase_cred())

app = Flask(__name__)

# 🧠 Утилиты
def first_nonempty(d, *keys):
    for k in keys:
        s = str(d.get(k) or "").strip()
        if s: return s
    return None

def format_body(customer, phone, comment, total, currency):
    lines = []
    if customer: lines.append(f"👤 Имя: {customer}")
    if phone: lines.append(f"📞 Номер: {phone}")
    if comment: lines.append(f"💬 Комментарий: {comment}")
    if total: lines.append(f"💵 Сумма: {total} {currency}")
    return "\n".join(lines) if lines else "Сообщение"

def send_push_to_admin(title, customer, phone, comment, total, currency, data=None):
    body = format_body(customer, phone, comment, total, currency)
    msg = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        topic="admin",
        data={k: str(v) for k, v in (data or {}).items()},
        android=messaging.AndroidConfig(priority="high"),
    )
    return messaging.send(msg)

# 🔔 API: Отправка заказа
@app.post("/send-order")
def send_order():
    p = request.get_json(force=True, silent=True) or {}
    order_id = first_nonempty(p, "orderId", "order_id", "id") or "N/A"
    customer = first_nonempty(p, "customer", "name") or "Клиент"
    phone = first_nonempty(p, "phone", "number", "tel", "contact") or "—"
    comment = first_nonempty(p, "comment", "note") or ""
    total = first_nonempty(p, "total", "amount") or ""
    currency = first_nonempty(p, "currency") or "TJS"

    def push_job():
        try:
            msg_id = send_push_to_admin("💼 Новый заказ", customer, phone, comment, total, currency, {"orderId": order_id})
            print(f"✅ PUSH: {msg_id}")
        except Exception as e:
            print(f"❌ PUSH ERROR: {e}")

    EXECUTOR.submit(push_job)

    return Response(json.dumps({"ok": True, "queued": True}, ensure_ascii=False), content_type="application/json")

# 🔔 Subscribe токен
@app.post("/subscribe-token")
def subscribe_token():
    p = request.get_json(force=True, silent=True) or {}
    token = p.get("token")
    if not token:
        return Response(json.dumps({"ok": False, "error": "no token"}), status=400, content_type="application/json")
    try:
        res = messaging.subscribe_to_topic([token], "admin")
        return Response(json.dumps({"ok": True, "res": res.__dict__}), content_type="application/json")
    except Exception as e:
        return Response(json.dumps({"ok": False, "error": str(e)}), status=500, content_type="application/json")

# 🩺 Пинг
@app.get("/health")
@app.get("/")
def health():
    return Response("OK", content_type="text/plain")

# === ✅ Products API ===
PRODUCTS_FILE = "products.json"

def load_products():
    if os.path.exists(PRODUCTS_FILE):
        with open(PRODUCTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_products(products):
    with open(PRODUCTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(products, f, ensure_ascii=False, indent=2)

@app.get("/products")
def get_products():
    return Response(json.dumps(load_products(), ensure_ascii=False), content_type="application/json")

@app.post("/products")
def add_product():
    p = request.get_json(force=True, silent=True) or {}
    required = ["brand", "model", "quality", "price", "stock"]
    if not all(k in p for k in required):
        return Response(json.dumps({"ok": False, "error": "Missing fields"}), status=400, content_type="application/json")
    products = load_products()
    new_product = {
        "id": int(__import__('time').time()),
        "brand": p["brand"],
        "model": p["model"],
        "quality": p["quality"],
        "price": float(p["price"]),
        "stock": int(p["stock"])
    }
    products.append(new_product)
    save_products(products)
    return Response(json.dumps({"ok": True, "product": new_product}, ensure_ascii=False), content_type="application/json")

@app.put("/products/<int:pid>")
def update_product(pid):
    p = request.get_json(force=True, silent=True) or {}
    products = load_products()
    for prod in products:
        if prod["id"] == pid:
            prod.update({
                "brand": p.get("brand", prod["brand"]),
                "model": p.get("model", prod["model"]),
                "quality": p.get("quality", prod["quality"]),
                "price": float(p.get("price", prod["price"])),
                "stock": int(p.get("stock", prod["stock"])),
            })
            save_products(products)
            return Response(json.dumps({"ok": True, "product": prod}, ensure_ascii=False), content_type="application/json")
    return Response(json.dumps({"ok": False, "error": "Product not found"}), status=404, content_type="application/json")

@app.delete("/products/<int:pid>")
def delete_product(pid):
    products = load_products()
    new_list = [p for p in products if p["id"] != pid]
    if len(products) == len(new_list):
        return Response(json.dumps({"ok": False, "error": "Product not found"}), status=404, content_type="application/json")
    save_products(new_list)
    return Response(json.dumps({"ok": True, "deleted_id": pid}, ensure_ascii=False), content_type="application/json")

# 🏁 Flask старт
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# order_push_server.py
import os
import json
import base64
import concurrent.futures
from typing import Any, Dict, List, Optional
from flask import Flask, request, Response

import firebase_admin
from firebase_admin import credentials, messaging, firestore
from flask_cors import CORS  # CORS для всех роутов

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
    raise RuntimeError("Missing FIREBASE_SERVICE_ACCOUNT")

# === Инициализация Firebase ===
if not firebase_admin._apps:
    cred = _load_firebase_cred()
    firebase_admin.initialize_app(cred)
db = firestore.client()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# === Утилиты ===
def first_nonempty(d: Dict[str, Any], *keys) -> Optional[str]:
    for k in keys:
        if k in d and d[k] is not None:
            s = str(d[k]).strip()
            if s:
                return s
    return None

def to_str(v) -> str:
    return "" if v is None else str(v)

def _parse_num(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    s = s.replace("\u00A0", " ")
    filtered = [ch for ch in s if ch.isdigit() or ch in [".", ",", "-"]]
    if not filtered:
        return None
    s = "".join(filtered)
    if "." in s and "," in s:
        s = s.replace(",", "")
    elif "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None

def _num_currency(x: float) -> float:
    v = round(x * 100) / 100.0
    return float(int(v)) if abs(v - int(v)) < 1e-9 else v

def _normalize_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    price = _parse_num(raw.get("price")) or _parse_num(raw.get("unitPrice")) or _parse_num(raw.get("amount")) or 0.0
    qty   = _parse_num(raw.get("qty"))   or _parse_num(raw.get("quantity"))  or _parse_num(raw.get("count"))  or 1.0
    name = first_nonempty(raw, "name", "title", "product", "fullName", "display")
    if not name:
        brand = to_str(raw.get("brand")).strip()
        model = first_nonempty(raw, "model", "code")
        variant = to_str(raw.get("variant")).strip()
        parts = [p for p in [brand or None, model, f"({variant})" if variant else None] if p]
        name = " ".join(parts) if parts else "Товар"
    item: Dict[str, Any] = {
        "name": name,
        "price": _num_currency(max(0.0, float(price))),
        "qty": _num_currency(max(1.0, float(qty))),
    }
    for k in ["brand", "variant", "quality", "badge"]:
        v = raw.get(k)
        if v is not None and str(v).strip():
            item[k] = str(v).strip()
    return item

def _normalize_items(v: Any) -> Optional[List[Dict[str, Any]]]:
    if not v or not isinstance(v, list):
        return None
    out: List[Dict[str, Any]] = []
    for it in v:
        if isinstance(it, dict):
            out.append(_normalize_item(it))
    return out or None

def _compute_total_from_items(items: List[Dict[str, Any]]) -> float:
    s = 0.0
    for it in items:
        s += float(it.get("price", 0.0)) * float(it.get("qty", 1.0))
    return _num_currency(s)

# === Отправка пуша админу (DATA-only) ===
def send_push_to_admin(order_id: str, customer: str, phone: str,
                       comment: str, total_text: str, currency: str):
    data_payload = {
        "title": "💼 Новый заказ",
        "orderId": str(order_id),
        "customer": to_str(customer),
        "phone": to_str(phone),
        "comment": to_str(comment),
        "total": to_str(total_text),
        "currency": to_str(currency),
    }
    msg = messaging.Message(topic="admin", data=data_payload)
    resp = messaging.send(msg)
    print(f"✅ FCM sent (topic=admin): {resp} | data={data_payload}", flush=True)
    return resp

# === Роут: регистрация токена (по вызову из клиента) ===
@app.post("/subscribe-token")
def subscribe_token():
    try:
        p = request.get_json(force=True, silent=True) or {}
        token = to_str(p.get("token")).strip()
        uid = to_str(p.get("uid")).strip()
        if not token:
            return Response(json.dumps({"ok": False, "error": "token required"}, ensure_ascii=False),
                            status=400, content_type="application/json; charset=utf-8")
        # Здесь можно (опционально) что-то логировать/валидировать
        print(f"🔗 subscribe-token ← uid={uid or '—'} token={token[:12]}…", flush=True)
        return Response(json.dumps({"ok": True}, ensure_ascii=False),
                        content_type="application/json; charset=utf-8")
    except Exception as e:
        print("❌ subscribe-token error:", e, flush=True)
        return Response(json.dumps({"ok": False, "error": "subscribe failed"}, ensure_ascii=False),
                        status=500, content_type="application/json; charset=utf-8")

# === Роут /send-order ===
@app.post("/send-order")
def send_order():
    p = request.get_json(force=True, silent=True) or {}
    print("📥 /send-order payload:", p, flush=True)

    order_id = first_nonempty(p, "orderId", "order_id", "id") or "N/A"
    customer = first_nonempty(p, "customerName", "name", "customer") or "Клиент"
    email    = first_nonempty(p, "email") or ""   # email клиента
    phone    = first_nonempty(p, "phone", "phoneNumber") or "—"
    comment  = to_str(first_nonempty(p, "comment", "note", "remark") or "")
    currency = to_str(first_nonempty(p, "currency", "curr") or "TJS")
    fcmToken = to_str(p.get("fcmToken") or "").strip()  # клиентский FCM
    # --- ДОБАВЛЕНО ДЛЯ ПРАВИЛ: userId (для чтения клиентом и допуска create по правилам)
    user_id  = first_nonempty(p, "uid", "userId") or "system"

    items = _normalize_items(p.get("items"))
    total_input = first_nonempty(p, "total", "sum", "amount")
    total_num = _parse_num(total_input)
    if total_num is None and items:
        total_num = _compute_total_from_items(items)
    if total_num is None:
        return Response(json.dumps({"ok": False, "error": "total required"}, ensure_ascii=False),
                        status=400, content_type="application/json; charset=utf-8")
    total_num = _num_currency(float(total_num))
    total_text = str(total_input).strip() if total_input else str(total_num)

    try:
        if order_id == "N/A":
            doc_ref = db.collection("orders").document()
            order_id = doc_ref.id
        else:
            doc_ref = db.collection("orders").document(str(order_id))

        order_doc: Dict[str, Any] = {
            "orderId": order_id,
            "customer": customer,
            "email": email,
            "phone": phone,
            "comment": comment,
            "currency": currency,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "status": "new",
            "total": total_num,
            "totalText": total_text,
            # --- ДОБАВЛЕНО ДЛЯ ПРАВИЛ: userId (совместимо с твоими правилами)
            "userId": user_id,
        }
        if fcmToken:
            order_doc["fcmToken"] = fcmToken
        if items:
            order_doc["items"] = items

        doc_ref.set(order_doc)
        print(f"💾 Order saved [id={order_id}] → {order_doc}", flush=True)
    except Exception as e:
        print("❌ Firestore save error:", e, flush=True)
        return Response(json.dumps({"ok": False, "error": "firestore save failed"}, ensure_ascii=False),
                        status=500, content_type="application/json; charset=utf-8")

    # Пуш только админу на этапе создания
    EXECUTOR.submit(lambda: send_push_to_admin(order_id, customer, phone, comment, total_text, currency))
    return Response(json.dumps({"ok": True, "orderId": order_id}, ensure_ascii=False),
                    content_type="application/json; charset=utf-8")

# === Заготовки текста для клиента ===
def _status_title_and_body(status: str) -> Dict[str, str]:
    s = status.lower().strip()
    if s == "progress":
        return {"title": "⚙️ Заказ в работе", "body": "Мы приняли ваш заказ, он уже в работе 🚀"}
    if s == "done":
        return {"title": "🎁 Заказ готов", "body": "Ура! Ваш заказ готов 🎉 Свяжитесь с продавцом."}
    if s == "canceled":
        return {"title": "💔 Заказ отменён", "body": "Очень жаль 😔 Ваш заказ был отменён."}
    return {"title": "ℹ️ Статус обновлён", "body": "Статус вашего заказа был обновлён."}

def _send_push_to_customer_tokens(tokens: List[str], data: Dict[str, str]) -> None:
    if not tokens:
        print("ℹ️ Нет токенов клиента для отправки.", flush=True)
        return
    for t in tokens:
        tok = to_str(t).strip()
        if not tok:
            continue
        try:
            msg = messaging.Message(token=tok, data={k: to_str(v) for k, v in data.items()})
            resp = messaging.send(msg)
            print(f"📤 FCM to user token={tok[:12]}… ok={resp}", flush=True)
        except Exception as e:
            print(f"❌ FCM send error for token={tok[:12]}…: {e}", flush=True)

def _collect_user_tokens_by_email(email: str) -> List[str]:
    if not email:
        return []
    try:
        q = db.collection("users").where("email", "==", email).limit(1).get()
        if not q:
            return []
        user_doc = q[0]
        tokens_snap = db.collection("users").document(user_doc.id)\
                        .collection("deviceTokens").stream()
        tokens: List[str] = []
        for d in tokens_snap:
            tok = (d.to_dict() or {}).get("token")
            if isinstance(tok, str) and tok.strip():
                tokens.append(tok.strip())
        # Уникализируем
        return list(dict.fromkeys(tokens))
    except Exception as e:
        print("❌ collect tokens by email error:", e, flush=True)
        return []

# === Роут /update-order-status — пуш клиенту ===
@app.post("/update-order-status")
def update_order_status():
    p = request.get_json(force=True, silent=True) or {}
    print("🔄 /update-order-status payload:", p, flush=True)

    order_id = first_nonempty(p, "orderId", "id")
    status = (first_nonempty(p, "status") or "").lower()
    notify_customer = (p.get("notifyCustomer", True) is True)

    if not order_id or status not in {"progress", "done", "canceled"}:
        return Response(json.dumps({"ok": False, "error": "orderId and valid status required"}, ensure_ascii=False),
                        status=400, content_type="application/json; charset=utf-8")

    try:
        doc_ref = db.collection("orders").document(str(order_id))
        snap = doc_ref.get()
        if not snap.exists:
            return Response(json.dumps({"ok": False, "error": "order not found"}, ensure_ascii=False),
                            status=404, content_type="application/json; charset=utf-8")

        update_fields: Dict[str, Any] = {"status": status}
        if status == "done":
            update_fields["doneAt"] = firestore.SERVER_TIMESTAMP
        if status == "canceled":
            update_fields["canceledAt"] = firestore.SERVER_TIMESTAMP
        if status == "progress":
            update_fields["progressAt"] = firestore.SERVER_TIMESTAMP

        doc_ref.set(update_fields, merge=True)
        print(f"📝 Order [{order_id}] status → {status}", flush=True)

        if notify_customer:
            order = snap.to_dict() or {}
            email = to_str(order.get("email"))
            fcmToken = to_str(order.get("fcmToken") or "").strip()
            total_text = to_str(order.get("totalText") or order.get("total") or "")
            currency = to_str(order.get("currency") or "TJS")

            tokens: List[str] = []
            if fcmToken:
                tokens.append(fcmToken)
            else:
                tokens = _collect_user_tokens_by_email(email)

            title_body = _status_title_and_body(status)
            data_payload = {
                "title": title_body["title"],
                "body": title_body["body"],
                "status": status,
                "orderId": str(order_id),
                "total": to_str(total_text),
                "currency": to_str(currency),
            }
            EXECUTOR.submit(lambda: _send_push_to_customer_tokens(tokens, data_payload))

        return Response(json.dumps({"ok": True, "orderId": order_id, "status": status}, ensure_ascii=False),
                        content_type="application/json; charset=utf-8")
    except Exception as e:
        print("❌ update status error:", e, flush=True)
        return Response(json.dumps({"ok": False, "error": "update failed"}, ensure_ascii=False),
                        status=500, content_type="application/json; charset=utf-8")

# === Служебные ===
@app.get("/health")
def health():
    return Response("OK", content_type="text/plain; charset=utf-8")

@app.get("/")
def root():
    return Response("OK", content_type="text/plain; charset=utf-8")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=False)

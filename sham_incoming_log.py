# -*- coding: utf-8 -*-
"""
sham_incoming_log.py — سجل تحويلات شام كاش الواردة (لزر «السجل» بلوحة الأدمن)
=============================================================================

يجلب التحويلات الواردة لحساب البوت من الـ API ويطابقها مع سجل الشحن الدائم
(recharge_transaction_ledger) ليحدد لكل عملية: هل تم شحنها لمستخدم أم لا.

اسم إجراء الـ API للواردات يُضبط بمتغير البيئة SHAM_INCOMING_ACTION (الافتراضي "logs").
إذا كان اسمه مختلفاً عند مزوّدك، تظهر رسالة الخطأ الحقيقية للأدمن داخل السجل.

الاستخدام:
    ok, items, error = load_incoming(force=False)
    items = apply_filter(items, "all" | "charged" | "free")
"""

import logging
import os
import time
from decimal import Decimal, InvalidOperation

from database import _db, _normalize_txid
from payment_api import SHAM_CASH_ADDRESS, PaymentAPIError, _get

log = logging.getLogger("sham_incoming_log")

INCOMING_ACTION = os.getenv("SHAM_INCOMING_ACTION", "logs")
PERIOD_DAYS = 7              # مدة السجل المطلوبة من الـ API
CACHE_SECONDS = 60           # لا نستعلم الـ API أكثر من مرة بالدقيقة أثناء التصفّح
PAGE_SIZE = 8

# ثلاثة مفاتيح محتملة لكل حقل (تختلف تسمياتها بين المزوّدين)
_TXID_KEYS = ("tran_id", "transaction_id", "txid", "tx_id", "tx", "id", "hash")
_NAME_KEYS = ("from_name", "sender_name", "sender", "from", "name", "account_name")
_TIME_KEYS = ("date", "datetime", "created_at", "time", "timestamp", "tran_date")

_cache = {"ts": 0.0, "items": None}


def _first(item: dict, keys):
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _fmt_amount(amount: Decimal, currency: str) -> str:
    if currency == "USD":
        return f"${amount:,.2f}"
    return f"{int(amount):,}" if amount == amount.to_integral_value() else f"{amount:,.2f}"


def _normalize_item(raw: dict):
    """يحوّل عنصر الـ API إلى شكل موحّد. يرجع None إذا لا يصلح (بلا رقم عملية)."""
    txid = _first(raw, _TXID_KEYS)
    if txid is None:
        return None
    try:
        amount = Decimal(str(raw.get("amount")))
    except (InvalidOperation, TypeError):
        return None
    currency = str(raw.get("currency") or "SYP").upper()
    return {
        "txid": str(txid).strip(),
        "norm": _normalize_txid(str(txid)),
        "amount": amount,
        "currency": currency,
        "amount_text": _fmt_amount(amount, currency),
        "name": str(_first(raw, _NAME_KEYS) or "").strip(),
        "time": str(_first(raw, _TIME_KEYS) or "").strip(),
        "charged": False,
        "user_id": None,
    }


def _mark_charged(items: list):
    """يضع charged=True (+ user_id) للعمليات الموجودة بالسجل الدائم بحالة approved."""
    if not items:
        return
    norms = list({it["norm"] for it in items})
    found = {}
    with _db() as conn:
        # SQLite يحدّ عدد المتغيّرات؛ ندفعات
        for i in range(0, len(norms), 500):
            chunk = norms[i:i + 500]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"""SELECT transaction_id_normalized, user_id
                    FROM recharge_transaction_ledger
                    WHERE status='approved' AND transaction_id_normalized IN ({marks})""",
                chunk,
            ).fetchall()
            for r in rows:
                found[r[0]] = int(r[1])
    for it in items:
        uid = found.get(it["norm"])
        if uid is not None:
            it["charged"] = True
            it["user_id"] = uid


def load_incoming(force: bool = False):
    """
    يرجع (ok, items, error). items مرتّبة من الأحدث للأقدم قدر الإمكان.
    الفشل لا يرمي استثناءً: يرجع (False, [], "سبب مقروء").
    """
    now = time.time()
    if not force and _cache["items"] is not None and now - _cache["ts"] < CACHE_SECONDS:
        items = _cache["items"]
        _mark_charged(items)  # حالة الشحن تتغير بسرعة؛ نحدّثها دائماً من قاعدتنا
        return True, items, ""

    if not SHAM_CASH_ADDRESS:
        return False, [], "SHAM_CASH_ADDRESS غير مضبوط."

    try:
        data = _get("shamcash", INCOMING_ACTION, {
            "account_address": SHAM_CASH_ADDRESS,
            "period": str(PERIOD_DAYS),
        })
    except PaymentAPIError as exc:
        return False, [], f"{exc} (الإجراء المستخدم: {INCOMING_ACTION})"
    except Exception as exc:
        log.exception("incoming log fetch failed")
        return False, [], f"تعذّر الاتصال: {exc}"

    payload = data.get("data") if isinstance(data, dict) else None
    raw_items = []
    if isinstance(payload, dict):
        raw_items = payload.get("items") or payload.get("transactions") or payload.get("logs") or []
    elif isinstance(payload, list):
        raw_items = payload

    items = []
    for raw in raw_items:
        if isinstance(raw, dict):
            norm = _normalize_item(raw)
            if norm:
                items.append(norm)

    # الأحدث أولاً إذا كان النص الزمني قابلاً للفرز (ISO-like)، وإلا نبقي ترتيب الـ API
    if items and all(it["time"] for it in items):
        items.sort(key=lambda x: x["time"], reverse=True)

    _mark_charged(items)
    _cache["ts"], _cache["items"] = now, items
    return True, items, ""


def apply_filter(items: list, mode: str) -> list:
    if mode == "charged":
        return [i for i in items if i["charged"]]
    if mode == "free":
        return [i for i in items if not i["charged"]]
    return items


# رموز الحالة — حسب طلبك: 🔴 مشحونة، 🟢 غير مشحونة
CHARGED_MARK = "🔴"
FREE_MARK = "🟢"
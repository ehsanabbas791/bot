"""
إدارة حسابات Syriatel Cash المربوطة بالبوت (للأدمن فقط):
  • قائمة الأكواد المعروضة للزبائن (قابلة للتعديل من لوحة الأدمن، تُحفظ بجدول settings)
  • جلب الرصيد من apisyria:        resource=syriatel & action=balance
  • التحويل اليدوي لحساب آخر:      resource=syriatel & action=transfer_cash   (POST)
  • سجل تدقيق لكل عملية (بدون رمز PIN أبداً)

قواعد الأمان:
  - رمز PIN لحساب Syriatel لا يُحفظ ولا يُسجَّل ولا يظهر بأي رسالة خطأ.
  - لا إعادة محاولة تلقائية للتحويل أبداً.
  - نتيجة التحويل: sent (نجح بتأكيد) | failed (رُفض قطعياً، لم يخرج مبلغ) | unknown (يلزم التحقق).
  - تحويل واحد في كل مرة (قفل).
"""
import logging
import re
import threading
from decimal import Decimal, InvalidOperation

import requests

from database import _db
from payment_api import API_KEY, BASE_URL, REQUEST_TIMEOUT

_log = logging.getLogger("syriatel_admin")

DEFAULT_CODES = ["17397943"]
_SETTING_KEY = "syriatel_codes"
TRANSFER_TIMEOUT = (10, 45)          # (اتصال، قراءة) بالثواني
_transfer_lock = threading.Lock()

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


# ------------------------------------------------------------------
# تنسيق وتحقق
# ------------------------------------------------------------------
def digits_only(value) -> str:
    return re.sub(r"\D", "", str(value or "").translate(_ARABIC_DIGITS))


def valid_code(code) -> bool:
    """كود كاش أو رقم موبايل: أرقام فقط (4–15 خانة)."""
    return bool(re.fullmatch(r"\d{4,15}", str(code or "")))


def valid_gsm(number) -> bool:
    """رقم موبايل سوري: 10 خانات يبدأ بـ 09."""
    return bool(re.fullmatch(r"09\d{8}", str(number or "")))


def api_ready() -> bool:
    return bool(API_KEY and BASE_URL)


def _headers():
    return {"X-Api-Key": API_KEY, "Accept": "application/json"}


def _error_text(payload, response_text, status) -> str:
    if isinstance(payload, dict):
        for key in ("error", "message", "msg", "detail", "reason"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:300]
    text = (response_text or "").strip()
    if text and not text.lstrip().startswith("<"):
        return text[:200]
    return f"HTTP {status}"


def _to_decimal(value):
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None


# ------------------------------------------------------------------
# الأكواد (settings)
# ------------------------------------------------------------------
def _dedupe(codes):
    seen, out = set(), []
    for c in codes:
        c = digits_only(c)
        if valid_code(c) and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def get_codes() -> list:
    try:
        with _db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (_SETTING_KEY,)).fetchone()
    except Exception:
        row = None

    if row and row[0]:
        codes = _dedupe(str(row[0]).split(","))
        if codes:
            return codes

    return _dedupe(DEFAULT_CODES)


def set_codes(codes) -> list:
    codes = _dedupe(codes)
    if not codes:
        raise ValueError("لازم يبقى كود واحد على الأقل.")
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (_SETTING_KEY, ",".join(codes)),
        )
    return codes


# ------------------------------------------------------------------
# سجل التدقيق
# ------------------------------------------------------------------
def _ensure_log(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS syriatel_admin_log (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               admin_id INTEGER NOT NULL,
               action TEXT NOT NULL,
               code TEXT, to_gsm TEXT, amount INTEGER,
               status TEXT NOT NULL DEFAULT '',
               detail TEXT NOT NULL DEFAULT '',
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )


def log_event(admin_id, action, code=None, to_gsm=None, amount=None, status="", detail="") -> int:
    try:
        with _db() as conn:
            _ensure_log(conn)
            cur = conn.execute(
                """INSERT INTO syriatel_admin_log (admin_id, action, code, to_gsm, amount, status, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (int(admin_id), action, code, to_gsm, amount, status, str(detail)[:500]),
            )
            return cur.lastrowid
    except Exception:
        _log.exception("audit log insert failed")
        return 0


def update_event(event_id, status, detail=""):
    if not event_id:
        return
    try:
        with _db() as conn:
            _ensure_log(conn)
            conn.execute(
                "UPDATE syriatel_admin_log SET status=?, detail=? WHERE id=?",
                (status, str(detail)[:500], int(event_id)),
            )
    except Exception:
        _log.exception("audit log update failed")


# ------------------------------------------------------------------
# apisyria
# ------------------------------------------------------------------
def fetch_balance(code) -> dict:
    """{"ok": True, "balance": Decimal, "gsm": str|None, "cash_code": str|None} أو {"ok": False, "error": str}"""
    if not api_ready():
        return {"ok": False, "error": "إعدادات API غير مكتملة."}

    try:
        response = requests.get(
            BASE_URL,
            params={"resource": "syriatel", "action": "balance", "gsm": code},
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        return {"ok": False, "error": "تعذّر الاتصال بـ apisyria."}

    try:
        payload = response.json()
    except ValueError:
        payload = None

    data = payload.get("data") if isinstance(payload, dict) else None

    if response.status_code == 200 and isinstance(payload, dict) and payload.get("success") and isinstance(data, dict):
        balance = _to_decimal(data.get("balance"))
        if balance is None:
            return {"ok": False, "error": "رد غير مفهوم (الرصيد)."}
        return {"ok": True, "balance": balance, "gsm": data.get("gsm"), "cash_code": data.get("cash_code")}

    return {"ok": False, "error": _error_text(payload, response.text, response.status_code)}


def recent_outgoing(code, limit=3) -> list:
    """آخر التحويلات الصادرة (للتحقق عند الحالة غير المؤكدة). يرجع [] عند أي فشل."""
    if not api_ready():
        return []
    try:
        response = requests.get(
            BASE_URL,
            params={"resource": "syriatel", "action": "outgoing_history", "gsm": code, "period": "7"},
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        items = response.json()["data"]["items"]
        return [dict(i) for i in items[:limit] if isinstance(i, dict)]
    except Exception:
        return []


def transfer_cash(code, to_gsm, amount, pin) -> dict:
    """
    ينفّذ تحويل Syriatel Cash. لا يعيد المحاولة أبداً.
    يرجع {"status": "sent"|"failed"|"unknown", "error": str, "fee": ..., "billcode": ..., "message": ...}
    """
    if not api_ready():
        return {"status": "failed", "error": "إعدادات API غير مكتملة."}

    if not _transfer_lock.acquire(blocking=False):
        return {"status": "failed", "error": "يوجد تحويل آخر قيد التنفيذ، انتظر انتهاءه."}

    try:
        try:
            response = requests.post(
                BASE_URL,
                params={"resource": "syriatel", "action": "transfer_cash"},
                headers=_headers(),
                data={"gsm": code, "to_gsm": to_gsm, "amount": str(int(amount)), "pin_code": pin},
                timeout=TRANSFER_TIMEOUT,
            )
        except requests.exceptions.ConnectTimeout:
            return {"status": "failed", "error": "تعذّر الاتصال بخادم API (لم يُرسَل أي طلب)."}
        except requests.RequestException as exc:
            _log.error("transfer_cash request error (%s): outcome unknown", type(exc).__name__)
            return {"status": "unknown", "error": "انقطع الاتصال أو تأخر الرد أثناء التنفيذ."}

        status = response.status_code
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if isinstance(payload, dict) and status == 200 and payload.get("success") is True:
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            return {
                "status": "sent",
                "error": "",
                "fee": data.get("fee"),
                "billcode": data.get("billcode"),
                "message": data.get("message"),
            }

        err = _error_text(payload, response.text, status)
        _log.warning("transfer_cash not successful: HTTP %s", status)

        if 400 <= status < 500:
            return {"status": "failed", "error": err}      # رُفض قطعياً (PIN خاطئ، رصيد، حساب...)

        return {"status": "unknown", "error": err}          # 5xx أو رد غير مفهوم: قد يكون نُفِّذ
    finally:
        _transfer_lock.release()

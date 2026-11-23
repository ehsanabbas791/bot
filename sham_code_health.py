# -*- coding: utf-8 -*-
"""
sham_code_health.py — إدارة كود (عنوان) شام كاش الذي يشحن عليه اللاعبون + فحص حالته
================================================================================

الكود المعروض للاعبين كان مكتوباً داخل الكود البرمجي؛ هنا يُخزَّن بجدول settings
(المفتاح sham_deposit_code) ويمكن تغييره من لوحة الأدمن.

«شغّال» (🟢) يعني اجتماع الشروط التالية:
  1) إعدادات الـ API مكتملة (API_KEY + SHAM_CASH_ADDRESS).
  2) الكود المعروض للاعبين = حساب الـ API (SHAM_CASH_ADDRESS)، أي البوت يستطيع التحقق من
     التحويلات الواردة له تلقائياً.
  3) الـ API يرد على هذا الحساب (استعلام الرصيد ينجح).
أي شرط ينقص → 🔴 مع سبب مقروء.

الاستخدام:
    get_code() / set_code(code)
    check()            # فحص فعلي (متزامن — استدعِه عبر asyncio.to_thread)
    get_cached()       # آخر نتيجة (بدون اتصال) أو None
    is_stale(status)   # هل تجاوزت مدة الصلاحية
"""

import logging
import re
import threading
import time
from decimal import Decimal

from database import _db
from payment_api import API_KEY, SHAM_CASH_ADDRESS, PaymentAPIError
from sham_transfer import clean_address, fetch_balances

log = logging.getLogger("sham_code_health")

SETTINGS_KEY = "sham_deposit_code"
DEFAULT_CODE = "97e172a587d6dd6daf23c47fc2c67cb4"   # الكود الذي كان مكتوباً بالكود البرمجي
CACHE_TTL_SECONDS = 300

_CODE_RE = re.compile(r"[A-Za-z0-9]{16,64}")
_lock = threading.Lock()
_cache = {"status": None}


# ============================================================
# الكود المعروض للاعبين
# ============================================================
def get_code() -> str:
    try:
        with _db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (SETTINGS_KEY,)).fetchone()
        if row and str(row[0]).strip():
            return str(row[0]).strip()
    except Exception:
        log.exception("failed to read deposit code; using default")
    return DEFAULT_CODE


def validate_code(raw: str) -> str:
    """ينظّف الكود الملصوق ويتحقق منه. يرمي ValueError برسالة مقروءة."""
    code = clean_address(raw)
    if not _CODE_RE.fullmatch(code):
        raise ValueError(
            "الكود غير صالح: لازم 16–64 حرف/رقم إنجليزي بدون رموز أو فراغات"
            f" (الطول المستلم {len(code)})."
        )
    return code


def set_code(raw: str) -> str:
    code = validate_code(raw)
    with _db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (SETTINGS_KEY, code))
    with _lock:
        _cache["status"] = None   # الحالة القديمة لم تعد صالحة
    return code


# ============================================================
# الفحص
# ============================================================
def _same(a: str, b: str) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def _fmt_balance(currency: str, value: Decimal) -> str:
    if currency == "SYP":
        return f"{int(value):,} SYP"
    return f"{value:,.2f} {currency}"


def check() -> dict:
    """فحص فعلي (يتصل بالـ API). لا يرمي استثناءً. يحدّث الكاش ويرجع النتيجة."""
    display = get_code()
    status = {
        "ok": False, "reason": "", "checked_at": time.time(),
        "display_code": display, "api_address": SHAM_CASH_ADDRESS or "",
        "match": _same(display, SHAM_CASH_ADDRESS or ""), "balances": [],
    }

    if not API_KEY or not SHAM_CASH_ADDRESS:
        status["reason"] = "إعدادات الـ API غير مكتملة (API_KEY أو SHAM_CASH_ADDRESS)."
    elif not status["match"]:
        status["reason"] = (
            "الكود المعروض للاعبين يختلف عن حساب الـ API (SHAM_CASH_ADDRESS) — "
            "البوت ما رح يقدر يتحقق من التحويلات الواردة له."
        )
    else:
        try:
            balances = fetch_balances()
            status["balances"] = [_fmt_balance(c, v) for c, v in sorted(balances.items())]
            status["ok"] = True
        except PaymentAPIError as exc:
            status["reason"] = f"الـ API ما رد على هذا الحساب: {exc}"
        except Exception as exc:
            log.exception("sham code check failed")
            status["reason"] = f"تعذّر الاتصال بالـ API: {exc}"

    with _lock:
        _cache["status"] = status
    return status


def get_cached():
    with _lock:
        return _cache["status"]


def is_stale(status) -> bool:
    return status is None or (time.time() - status["checked_at"]) > CACHE_TTL_SECONDS
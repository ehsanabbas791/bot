"""
sham_transfer.py
================
تحويل رصيد من حساب Sham Cash الخاص بالبوت إلى أي حساب Sham Cash، من لوحة الأدمن —
مع طبقات حماية لأنها عملية مالية لا يمكن التراجع عنها.

الحماية
-------
1) مغلقة افتراضياً: لا تعمل قبل أن يضبط الأدمن "رمز التحويل" (PIN) ثم يفعّلها صراحةً.
2) كل تحويل يتطلب إرسال رمز PIN (مخزَّن مشفّراً PBKDF2) — قفل 15 دقيقة بعد 3 محاولات خاطئة.
3) حدّان بالعملة: الحد للتحويل الواحد، والحد اليومي (بتوقيت سوريا). فحص الحد اليومي وحجز
   التحويل يتمّان بعملية ذرّية واحدة، فلا يمكن تجاوزه بطلبين متزامنين.
4) منع التكرار: كل تحويل له سجل ورقم مرجعي فريد؛ يُنفَّذ مرة واحدة فقط (pending → sending ذرّياً).
5) لا إعادة محاولة تلقائية أبداً. أي نتيجة غير مؤكدة (انقطاع/تأخر/خطأ خادم) تُسجَّل "unknown"
   وتُحتسب من الحد اليومي، ولا يُسمح بإعادة الإرسال قبل التحقق من سجل التحويلات الصادرة
   (البحث بالرقم المرجعي الذي نضيفه لملاحظة التحويل).
6) سجل تدقيق كامل لكل محاولة (من، إلى، كم، متى، النتيجة).

الاستدعاء من المهام الخلفية/الخيوط: كل الدوال هنا متزامنة (blocking) فاستدعِها عبر asyncio.to_thread.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

import requests

from database import _db
from payment_api import (
    API_KEY,
    BASE_URL,
    REQUEST_TIMEOUT,
    SHAM_CASH_ADDRESS,
    PaymentAPIError,
    _get,
)

_log = logging.getLogger("sham_transfer")

# =========================================================
# إعدادات
# =========================================================

SUPPORTED_CURRENCIES = ("SYP", "USD")

REQUEST_TTL_SECONDS = 5 * 60              # صلاحية معاينة التحويل قبل أن تنتهي
SENDING_STUCK_SECONDS = 120               # "sending" أقدم من هذا = انقطع التنفيذ → unknown
TRANSFER_TIMEOUT = max(float(REQUEST_TIMEOUT), 30.0)
NOTE_MAX = 180                            # حد API لملاحظة التحويل

PIN_MIN_DIGITS = 6
PIN_MAX_DIGITS = 12
PIN_PBKDF2_ITERATIONS = 200_000
PIN_MAX_FAILS = 3
PIN_LOCK_SECONDS = 15 * 60

# (الحد للتحويل الواحد، الحد اليومي) — الليرة هنا هي الليرة الجديدة (نفس وحدة شام كاش)
DEFAULT_LIMITS = {
    "SYP": (Decimal("50000"), Decimal("200000")),
    "USD": (Decimal("50"), Decimal("200")),
}
MAX_LIMIT = Decimal("1000000000")

SYRIA_UTC_OFFSET_HOURS = 3

ADDRESS_RE = re.compile(r"[A-Za-z0-9]{16,64}")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

_WEAK_PINS = {
    "123456", "1234567", "12345678", "123456789", "1234567890",
    "654321", "987654", "0123456", "000000", "121212", "112233",
}

STATUSES = ("pending", "sending", "sent", "failed", "unknown", "expired", "cancelled")
COUNTED_STATUSES = ("sending", "sent", "unknown")   # تُحتسب من الحد اليومي

# =========================================================
# الجداول
# =========================================================

with _db() as _conn:

    _conn.execute("""
    CREATE TABLE IF NOT EXISTS sham_transfers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ref TEXT NOT NULL UNIQUE,
        admin_id INTEGER NOT NULL,
        to_address TEXT NOT NULL,
        currency TEXT NOT NULL,
        amount TEXT NOT NULL,
        note TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        recipient_name TEXT,
        error TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        claimed_at TIMESTAMP,
        tran_id TEXT
    )
    """)

    # ترقية آمنة لجدول أُنشئ بنسخة أقدم (بدون هذين العمودين)
    _existing_columns = {r[1] for r in _conn.execute("PRAGMA table_info(sham_transfers)").fetchall()}

    for _column in ("claimed_at TIMESTAMP", "tran_id TEXT"):
        if _column.split()[0] not in _existing_columns:
            _conn.execute(f"ALTER TABLE sham_transfers ADD COLUMN {_column}")

    _conn.execute("""
    CREATE TABLE IF NOT EXISTS sham_transfer_pin_attempts (
        admin_id INTEGER PRIMARY KEY,
        fails INTEGER NOT NULL DEFAULT 0,
        locked_until REAL NOT NULL DEFAULT 0
    )
    """)


# =========================================================
# أدوات عامة
# =========================================================

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_setting(key: str) -> Optional[str]:

    with _db() as conn:

        row = conn.execute("""
            SELECT value
            FROM settings
            WHERE key=?
        """, (key,)).fetchone()

    return str(row[0]) if row else None


def _set_setting(key: str, value: str) -> None:

    with _db() as conn:

        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value)
            VALUES (?, ?)
        """, (key, value))


def _check_currency(currency: str) -> str:

    currency = str(currency or "").strip().upper()

    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("عملة غير مدعومة (المتاح: ليرة سورية أو دولار).")

    return currency


def format_amount(amount: Decimal, currency: str) -> str:
    """25000 → '25,000'  |  5 دولار → '5.00'"""
    amount = Decimal(amount)

    if currency == "SYP":
        return f"{int(amount):,}"

    return f"{amount:,.2f}"


def amount_api_string(amount: Decimal, currency: str) -> str:
    """الصيغة المرسلة لـ API: بدون أُسّ علمي وبدون أصفار زائدة (25000 / 5 / 5.5)."""
    amount = Decimal(amount)

    if currency == "SYP":
        return str(int(amount))

    text = format(amount.normalize(), "f")
    return text


def parse_amount(text: str, currency: str) -> Decimal:
    """
    يحوّل نص الأدمن إلى مبلغ. الليرة: عدد صحيح موجب. الدولار: حتى خانتين عشريتين.
    يقبل الأرقام العربية وفاصل الآلاف (25,000) والفاصلة العشرية للدولار.
    """
    currency = _check_currency(currency)

    cleaned = (
        str(text or "").strip()
        .translate(_ARABIC_DIGITS)
        .replace("٫", ".").replace("،", ",")
    )

    if re.fullmatch(r"\d{1,3}(,\d{3})+", cleaned):
        cleaned = cleaned.replace(",", "")
    elif re.fullmatch(r"\d+,\d{1,2}", cleaned):
        cleaned = cleaned.replace(",", ".")

    if not re.fullmatch(r"\d{1,12}(\.\d+)?", cleaned):
        raise ValueError("أرسل مبلغاً صحيحاً (أرقام فقط).")

    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError("أرسل مبلغاً صحيحاً (أرقام فقط).")

    if currency == "SYP":
        if amount != amount.to_integral_value():
            raise ValueError("مبلغ الليرة يجب أن يكون عدداً صحيحاً بدون كسور.")
    elif amount.as_tuple().exponent < -2:
        raise ValueError("مبلغ الدولار يقبل خانتين عشريتين كحد أقصى.")

    if amount <= 0:
        raise ValueError("المبلغ يجب أن يكون أكبر من صفر.")

    return amount


# محارف غير مرئية تدخل عند النسخ من التطبيقات (خصوصاً الواجهات العربية RTL):
# علامات الاتجاه، العرض الصفري، BOM، المسافة غير القابلة للكسر، الواصلة الناعمة...
_INVISIBLE_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\u00ad\u061c]")


def clean_address(address: str) -> str:
    """ينظّف عنواناً ملصوقاً: يزيل المحارف غير المرئية وكل الفراغات/الأسطر ويوحّد الأرقام."""
    text = unicodedata.normalize("NFKC", str(address or ""))
    text = text.translate(_ARABIC_DIGITS)
    text = _INVISIBLE_RE.sub("", text)
    return re.sub(r"\s+", "", text)


def validate_address(address: str) -> str:
    """عنوان حساب شام كاش للمستلم: أحرف/أرقام فقط، طول معقول، وليس حساب البوت نفسه."""
    address = clean_address(address)

    if not ADDRESS_RE.fullmatch(address):
        shown = len(address)
        hint = ""
        if shown < 16:
            hint = f" (الطول المستلم {shown} والمقبول 16–64)"
        elif shown > 64:
            hint = f" (الطول المستلم {shown} والمقبول 16–64)"
        else:
            hint = " (يحتوي رموزاً غير الحروف الإنجليزية والأرقام)"
        raise ValueError("عنوان الحساب غير صالح. أرسل عنوان شام كاش كاملاً (أحرف وأرقام فقط، بدون فراغات)." + hint)

    if SHAM_CASH_ADDRESS and address.lower() == SHAM_CASH_ADDRESS.lower():
        raise ValueError("لا يمكن التحويل إلى حساب البوت نفسه.")

    return address


def sanitize_note(note: str, max_length: int = 100) -> str:
    """ملاحظة الأدمن: سطر واحد، بدون محارف تحكّم."""
    note = re.sub(r"[\x00-\x1f\x7f]+", " ", str(note or "")).strip()
    note = re.sub(r"\s+", " ", note)
    return note[:max_length]


def compose_note(ref: str, admin_note: str = "") -> str:
    """ملاحظة التحويل = ملاحظة الأدمن + الرقم المرجعي (يبقى دائماً كاملاً ضمن حد 180)."""
    admin_note = sanitize_note(admin_note)

    if not admin_note:
        return f"Ref {ref}"

    room = NOTE_MAX - len(ref) - len(" | Ref ")
    return f"{admin_note[:max(room, 0)]} | Ref {ref}"


# =========================================================
# التفعيل والحدود
# =========================================================


def is_enabled() -> bool:
    return _get_setting("sham_transfer_enabled") == "1"


def set_enabled(enabled: bool) -> None:
    """التفعيل مشروط بوجود رمز PIN وإعدادات API صالحة؛ الإيقاف دائماً مسموح."""

    if enabled:
        if not has_pin():
            raise ValueError("اضبط رمز التحويل (PIN) أولاً.")
        if not API_KEY or not SHAM_CASH_ADDRESS:
            raise ValueError("إعدادات API غير مكتملة (API_SYRIA_KEY / SHAM_CASH_ADDRESS).")

    _set_setting("sham_transfer_enabled", "1" if enabled else "0")


def _decimal_setting(key: str) -> Optional[Decimal]:

    raw = _get_setting(key)

    if raw is None:
        return None

    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None

    return value if value.is_finite() and 0 < value <= MAX_LIMIT else None


def get_limits(currency: str) -> tuple[Decimal, Decimal]:
    """(الحد للتحويل الواحد، الحد اليومي)؛ القيمة التالفة/الناقصة تعود للافتراضي."""
    currency = _check_currency(currency)
    default_single, default_daily = DEFAULT_LIMITS[currency]

    single = _decimal_setting(f"sham_transfer_limit_single_{currency}") or default_single
    daily = _decimal_setting(f"sham_transfer_limit_daily_{currency}") or default_daily

    return single, max(daily, single)


def set_limits(currency: str, single, daily) -> None:

    currency = _check_currency(currency)

    try:
        single, daily = Decimal(str(single)), Decimal(str(daily))
    except InvalidOperation:
        raise ValueError("الحدود يجب أن تكون أرقاماً.")

    for value in (single, daily):
        if not value.is_finite() or value <= 0 or value > MAX_LIMIT:
            raise ValueError(f"كل حد يجب أن يكون بين 0 و {int(MAX_LIMIT):,} (أكبر من صفر).")

        if currency == "SYP" and value != value.to_integral_value():
            raise ValueError("حدود الليرة أعداد صحيحة.")

        if currency == "USD" and value.as_tuple().exponent < -2:
            raise ValueError("حدود الدولار تقبل خانتين عشريتين كحد أقصى.")

    if single > daily:
        raise ValueError("الحد للتحويل الواحد لا يمكن أن يتجاوز الحد اليومي.")

    _set_setting(f"sham_transfer_limit_single_{currency}", format(single.normalize(), "f"))
    _set_setting(f"sham_transfer_limit_daily_{currency}", format(daily.normalize(), "f"))


def _syria_day_window_utc(now_utc: Optional[datetime] = None) -> tuple[str, str]:
    """بداية ونهاية اليوم الحالي بتوقيت سوريا، مُحوَّلتين إلى UTC (صيغة SQLite)."""
    now_utc = now_utc or _utcnow()
    local = now_utc + timedelta(hours=SYRIA_UTC_OFFSET_HOURS)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_local - timedelta(hours=SYRIA_UTC_OFFSET_HOURS)
    return start_utc.strftime(_TS_FORMAT), (start_utc + timedelta(days=1)).strftime(_TS_FORMAT)


def _daily_total_conn(conn, currency: str, now_utc: Optional[datetime] = None) -> Decimal:

    start, end = _syria_day_window_utc(now_utc)

    rows = conn.execute(f"""
        SELECT amount
        FROM sham_transfers
        WHERE currency=?
        AND status IN ({",".join("?" * len(COUNTED_STATUSES))})
        AND claimed_at >= ? AND claimed_at < ?
    """, (currency, *COUNTED_STATUSES, start, end)).fetchall()

    return sum((Decimal(r[0]) for r in rows), Decimal(0))


def daily_total(currency: str, now_utc: Optional[datetime] = None) -> Decimal:
    """مجموع ما حُوِّل (أو قد يكون حُوِّل) اليوم بالعملة المحددة."""
    currency = _check_currency(currency)

    with _db() as conn:
        return _daily_total_conn(conn, currency, now_utc)


def _limit_error(currency: str, amount: Decimal, used_today: Decimal) -> Optional[str]:

    single, daily = get_limits(currency)

    if amount > single:
        return f"المبلغ يتجاوز الحد للتحويل الواحد ({format_amount(single, currency)} {currency})."

    if used_today + amount > daily:
        left = max(daily - used_today, Decimal(0))
        return (
            f"المبلغ يتجاوز الحد اليومي. المتبقي اليوم: {format_amount(left, currency)} {currency} "
            f"(من {format_amount(daily, currency)})."
        )

    return None


def check_limits(currency: str, amount: Decimal) -> None:
    """فحص مبدئي (للمعاينة). الفحص النهائي الملزِم يتم داخل claim_for_sending."""
    currency = _check_currency(currency)
    message = _limit_error(currency, Decimal(amount), daily_total(currency))

    if message:
        raise ValueError(message)


# =========================================================
# رمز التحويل (PIN)
# =========================================================


@dataclass(frozen=True)
class PinCheck:
    status: str                 # ok | wrong | locked | no_pin
    attempts_left: int = 0
    locked_seconds: int = 0


def _hash_pin(pin: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)


def has_pin() -> bool:
    return bool(_get_setting("sham_transfer_pin"))


def validate_pin_format(pin: str) -> str:

    pin = str(pin or "").strip().translate(_ARABIC_DIGITS)

    if not (pin.isascii() and pin.isdigit()) or not PIN_MIN_DIGITS <= len(pin) <= PIN_MAX_DIGITS:
        raise ValueError(f"الرمز يجب أن يكون من {PIN_MIN_DIGITS} إلى {PIN_MAX_DIGITS} رقماً (أرقام فقط).")

    if len(set(pin)) == 1 or pin in _WEAK_PINS:
        raise ValueError("الرمز سهل التخمين. اختر رمزاً أقوى (بدون تكرار أو تسلسل).")

    return pin


def verify_pin(admin_id: int, pin: str, now: Optional[float] = None) -> PinCheck:
    """يتحقق من الرمز مع قفل بعد PIN_MAX_FAILS محاولات خاطئة (لكل أدمن)."""

    now_ts = time.time() if now is None else now
    stored = _get_setting("sham_transfer_pin")

    if not stored:
        return PinCheck("no_pin")

    try:
        _, iterations, salt_hex, hash_hex = stored.split("$")
        expected = bytes.fromhex(hash_hex)
        candidate = _hash_pin(str(pin or "").strip().translate(_ARABIC_DIGITS), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        _log.error("stored transfer PIN is corrupt")
        return PinCheck("no_pin")

    matches = hmac.compare_digest(candidate, expected)

    with _db() as conn:

        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute("""
            SELECT fails, locked_until
            FROM sham_transfer_pin_attempts
            WHERE admin_id=?
        """, (int(admin_id),)).fetchone()

        fails, locked_until = (int(row[0]), float(row[1])) if row else (0, 0.0)

        if locked_until > now_ts:
            return PinCheck("locked", 0, int(locked_until - now_ts) + 1)

        if locked_until:                      # القفل انتهى → نبدأ العدّ من جديد
            fails = 0

        if matches:
            conn.execute("""
                INSERT OR REPLACE INTO sham_transfer_pin_attempts (admin_id, fails, locked_until)
                VALUES (?, 0, 0)
            """, (int(admin_id),))
            return PinCheck("ok")

        fails += 1

        if fails >= PIN_MAX_FAILS:
            conn.execute("""
                INSERT OR REPLACE INTO sham_transfer_pin_attempts (admin_id, fails, locked_until)
                VALUES (?, 0, ?)
            """, (int(admin_id), now_ts + PIN_LOCK_SECONDS))
            _log.warning("transfer PIN locked for admin %s", admin_id)
            return PinCheck("locked", 0, PIN_LOCK_SECONDS)

        conn.execute("""
            INSERT OR REPLACE INTO sham_transfer_pin_attempts (admin_id, fails, locked_until)
            VALUES (?, ?, 0)
        """, (int(admin_id), fails))

        return PinCheck("wrong", PIN_MAX_FAILS - fails)


def set_pin(admin_id: int, new_pin: str, current_pin: Optional[str] = None) -> PinCheck:
    """
    يضبط رمز التحويل. إن كان هناك رمز سابق فيلزم الرمز الحالي (ويخضع للقفل).
    يرمي ValueError إذا كان الرمز الجديد ضعيفاً/بصيغة خاطئة.
    """
    new_pin = validate_pin_format(new_pin)

    if has_pin():
        check = verify_pin(admin_id, current_pin or "")
        if check.status != "ok":
            return check

    salt = secrets.token_bytes(16)
    digest = _hash_pin(new_pin, salt, PIN_PBKDF2_ITERATIONS)

    _set_setting("sham_transfer_pin", f"pbkdf2_sha256${PIN_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}")

    return PinCheck("ok")


def reset_pin_and_disable() -> None:
    """للطوارئ فقط (نسيان الرمز): يتطلب وصولاً للخادم. يحذف الرمز ويوقف التحويلات."""

    with _db() as conn:

        conn.execute("DELETE FROM settings WHERE key='sham_transfer_pin'")
        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value)
            VALUES ('sham_transfer_enabled', '0')
        """)
        conn.execute("DELETE FROM sham_transfer_pin_attempts")


# =========================================================
# سجل التحويلات (حالة كل طلب)
# =========================================================


def _row_to_dict(row) -> dict:

    return {key: row[key] for key in row.keys()}


def get_request(request_id: int) -> Optional[dict]:

    with _db() as conn:

        row = conn.execute("SELECT * FROM sham_transfers WHERE id=?", (int(request_id),)).fetchone()

    return _row_to_dict(row) if row else None


def create_request(admin_id: int, to_address: str, currency: str, amount: Decimal, note: str = "") -> dict:
    """ينشئ طلب تحويل بحالة pending (معاينة). لا يحرّك أي مال."""

    currency = _check_currency(currency)
    to_address = validate_address(to_address)
    amount = Decimal(amount)

    if amount <= 0:
        raise ValueError("المبلغ يجب أن يكون أكبر من صفر.")

    amount_text = amount_api_string(amount, currency)
    note = sanitize_note(note)

    for _ in range(5):

        ref = "BT-" + secrets.token_hex(4).upper()

        try:
            with _db() as conn:

                cur = conn.execute("""
                    INSERT INTO sham_transfers (ref, admin_id, to_address, currency, amount, note)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (ref, int(admin_id), to_address, currency, amount_text, note))

                request_id = cur.lastrowid

            return get_request(request_id)

        except Exception as exc:                 # تعارض نادر على ref الفريد → نعيد التوليد
            if "UNIQUE" not in str(exc).upper():
                raise

    raise RuntimeError("تعذر توليد رقم مرجعي فريد")


def expire_old_pending() -> int:
    """يغلق المعاينات التي تجاوزت مهلتها."""

    with _db() as conn:

        cur = conn.execute(f"""
            UPDATE sham_transfers
            SET status='expired', updated_at=CURRENT_TIMESTAMP
            WHERE status='pending'
            AND created_at <= datetime('now', '-{REQUEST_TTL_SECONDS} seconds')
        """)

        return cur.rowcount


def cancel_request(request_id: int, admin_id: int) -> bool:

    with _db() as conn:

        cur = conn.execute("""
            UPDATE sham_transfers
            SET status='cancelled', updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND admin_id=? AND status='pending'
        """, (int(request_id), int(admin_id)))

        return cur.rowcount == 1


def claim_for_sending(request_id: int, admin_id: int) -> tuple[str, str]:
    """
    الحجز الذرّي قبل التنفيذ. بنفس المعاملة: يتأكد أن الطلب لمنشئه وما زال pending وضمن مهلته،
    ويفحص الحد للتحويل الواحد والحد اليومي (مع كل ما هو قيد التنفيذ)، ثم ينقله إلى sending.
    يرجع (النتيجة, رسالة): claimed | missing | not_pending | expired | limit
    """

    with _db() as conn:

        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute("SELECT * FROM sham_transfers WHERE id=?", (int(request_id),)).fetchone()

        if row is None or int(row["admin_id"]) != int(admin_id):
            return "missing", ""

        if row["status"] != "pending":
            return "not_pending", row["status"]

        created = datetime.strptime(row["created_at"], _TS_FORMAT)

        if (_utcnow() - created).total_seconds() > REQUEST_TTL_SECONDS:
            conn.execute("""
                UPDATE sham_transfers
                SET status='expired', updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (int(request_id),))
            return "expired", ""

        currency = row["currency"]
        amount = Decimal(row["amount"])

        message = _limit_error(currency, amount, _daily_total_conn(conn, currency))

        if message:
            return "limit", message

        conn.execute("""
            UPDATE sham_transfers
            SET status='sending', claimed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='pending'
        """, (int(request_id),))

        return "claimed", ""


def finish_request(
    request_id: int,
    status: str,
    recipient_name: Optional[str] = None,
    error: Optional[str] = None,
    tran_id: Optional[str] = None,
    *,
    allowed_from: Optional[tuple] = ("sending", "unknown"),
) -> bool:
    """
    ينقل طلباً إلى حالته النهائية. افتراضياً مسموح فقط من sending/unknown، فلا يمكن لتحديث
    متأخر أن يكتب فوق حالة نهائية (sent لا يعود failed بالغلط). ترجع True إذا تم التحديث.
    allowed_from=None يلغي القيد (للاختبارات فقط).
    """

    if status not in STATUSES:
        raise ValueError(f"حالة غير معروفة: {status}")

    guard, params = "", []

    if allowed_from is not None:
        guard = f" AND status IN ({','.join('?' * len(allowed_from))})"
        params = list(allowed_from)

    with _db() as conn:

        cur = conn.execute(f"""
            UPDATE sham_transfers
            SET status=?, recipient_name=COALESCE(?, recipient_name), error=?,
                tran_id=COALESCE(?, tran_id), updated_at=CURRENT_TIMESTAMP
            WHERE id=?{guard}
        """, (status, recipient_name, error, tran_id, int(request_id), *params))

        return cur.rowcount == 1


def reconcile_stuck(force: bool = False) -> list[int]:
    """
    أي طلب بقي "sending" لأن التنفيذ انقطع (إعادة تشغيل/انهيار) يصبح "unknown" ليُتحقَّق منه يدوياً.
    force=True عند إقلاع البوت: كل "sending" هو بالتعريف منقطع.
    """

    with _db() as conn:

        conn.execute("BEGIN IMMEDIATE")

        age_clause = "" if force else f"AND updated_at <= datetime('now', '-{SENDING_STUCK_SECONDS} seconds')"

        ids = [int(r[0]) for r in conn.execute(f"""
            SELECT id FROM sham_transfers
            WHERE status='sending' {age_clause}
        """).fetchall()]

        for request_id in ids:
            conn.execute("""
                UPDATE sham_transfers
                SET status='unknown',
                    error='انقطع التنفيذ قبل وصول النتيجة — تحقق من حساب شام كاش',
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (request_id,))

    return ids


def recent_transfers(limit: int = 10) -> list[dict]:

    with _db() as conn:

        rows = conn.execute("""
            SELECT * FROM sham_transfers
            WHERE status NOT IN ('pending', 'expired', 'cancelled')
            ORDER BY id DESC
            LIMIT ?
        """, (int(limit),)).fetchall()

    return [_row_to_dict(r) for r in rows]


def count_unknown() -> int:

    with _db() as conn:

        return int(conn.execute("SELECT COUNT(*) FROM sham_transfers WHERE status='unknown'").fetchone()[0])


def resolve_unknown(request_id: int, as_status: str, error: Optional[str] = None) -> bool:
    """يحسم تحويلاً غير مؤكد بعد التحقق: sent (وُجد) أو failed (تأكدنا أنه لم يحدث)."""

    if as_status not in ("sent", "failed"):
        raise ValueError("as_status must be sent|failed")

    with _db() as conn:

        cur = conn.execute("""
            UPDATE sham_transfers
            SET status=?, error=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='unknown'
        """, (as_status, error, int(request_id)))

        return cur.rowcount == 1


# =========================================================
# عميل الـ API (POST للتحويل — بدون أي إعادة محاولة)
# =========================================================


@dataclass(frozen=True)
class TransferResult:
    outcome: str                         # sent | failed | unknown
    message: str
    recipient_name: str = ""
    retry_after: Optional[int] = None


def _api_ready() -> bool:
    return bool(API_KEY and SHAM_CASH_ADDRESS)


def _pick_message(value) -> str:
    """يستخرج نصاً مقروءاً من قيمة قد تكون نصاً أو قاموساً أو قائمة."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("message", "error", "detail", "msg", "description", "reason"):
            found = _pick_message(value.get(key))
            if found:
                return found
        return ""
    if isinstance(value, (list, tuple)):
        parts = [_pick_message(v) for v in value[:3]]
        return " | ".join(p for p in parts if p)
    return ""


def _extract_api_error(payload: dict, response_text: str, status: int) -> str:
    """
    سبب الرفض كما يرسله الـ API. يقرأ عدة مفاتيح شائعة (error/message/msg/detail/errors/reason)،
    وإن لم يجد يعرض بداية الرد الخام (إلا لو كان صفحة HTML). هكذا يظهر السبب الحقيقي للأدمن
    بدل الجملة العامة «رُفض الطلب (HTTP …)».
    """
    for key in ("error", "message", "msg", "detail", "errors", "reason"):
        found = _pick_message(payload.get(key))
        if found:
            return found[:300]

    text = (response_text or "").strip()
    if text and not text.lstrip().startswith("<"):
        return text[:200]
    return ""


def execute_transfer(to_address: str, currency: str, amount: Decimal, note: str) -> TransferResult:
    """
    ينفّذ التحويل فعلياً. لا يعيد المحاولة أبداً.
      sent    : نجح بتأكيد من الـ API.
      failed  : رُفض بشكل قاطع (لم يخرج أي مبلغ).
      unknown : قد يكون نُفِّذ وقد لا يكون (انقطاع/تأخر/خطأ خادم/رد غير مفهوم) → يلزم التحقق.
    """

    if not _api_ready():
        return TransferResult("failed", "إعدادات API غير مكتملة.")

    data = {
        "account_address": SHAM_CASH_ADDRESS,
        "receive_key": to_address,
        "amount": amount_api_string(amount, currency),
        "currency": currency,
        "note": note[:NOTE_MAX],
    }

    try:
        response = requests.post(
            BASE_URL,
            params={"resource": "shamcash", "action": "transfer"},
            headers={
                "X-Api-Key": API_KEY,
                "Accept": "application/json",
            },
            data=data,
            timeout=TRANSFER_TIMEOUT,
        )

# =========================================================
# DEBUG مؤقت لمعرفة الرد الحقيقي من API
# =========================================================
        _log.warning("========== SHAM CASH TRANSFER DEBUG ==========")
        _log.warning("HTTP STATUS: %s", response.status_code)
        _log.warning("API RESPONSE: %s", response.text[:3000])
        _log.warning("==============================================")
    except requests.exceptions.ConnectTimeout:
        # لم تُقم الاتصال أصلاً → لم يُرسَل أي شيء
        return TransferResult("failed", "تعذر الاتصال بخادم API (لم يُرسَل أي طلب).")
    except requests.RequestException as exc:
        _log.error("transfer request error (%s): outcome unknown", type(exc).__name__)
        return TransferResult("unknown", "انقطع الاتصال أو تأخر الرد أثناء التنفيذ.")

    status = response.status_code

    try:
        payload = response.json()
    except ValueError:
        payload = None

    if not isinstance(payload, dict):
        payload = {}

    api_error = _extract_api_error(payload, response.text, status)

    if status == 429:
        retry_after = payload.get("retry_after")
        return TransferResult(
            "failed",
            api_error or "تم تجاوز حد الطلبات.",
            retry_after=int(retry_after) if str(retry_after).isdigit() else None,
        )

    if status >= 500 or status == 408:
        return TransferResult("unknown", api_error or f"خطأ من خادم API (HTTP {status}).")

    if status >= 400 or payload.get("success") is False:
        return TransferResult("failed", api_error or f"رُفض الطلب (HTTP {status}).")

    if payload.get("success") is not True:
        return TransferResult("unknown", "رد غير مفهوم من الخادم.")

    body = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    transfer = body.get("transfer") if isinstance(body.get("transfer"), dict) else {}
    recipient = body.get("recipient") if isinstance(body.get("recipient"), dict) else {}

    # تأكيد أن ما نُفِّذ يطابق ما طلبناه (إن أعاد الخادم هذه التفاصيل)
    try:
        if transfer.get("amount") is not None and Decimal(str(transfer["amount"])) != Decimal(amount):
            return TransferResult("unknown", "المبلغ في رد الخادم لا يطابق المطلوب — تحقق يدوياً.")

        if transfer.get("currency") and str(transfer["currency"]).upper() != currency:
            return TransferResult("unknown", "العملة في رد الخادم لا تطابق المطلوب — تحقق يدوياً.")
    except InvalidOperation:
        return TransferResult("unknown", "رد غير مفهوم من الخادم — تحقق يدوياً.")

    return TransferResult(
        "sent",
        str(body.get("message") or "تم التحويل بنجاح"),
        recipient_name=str(recipient.get("userName") or ""),
    )


def fetch_balances() -> dict:
    """أرصدة حساب البوت على شام كاش: {'SYP': Decimal, 'USD': Decimal, ...}. يرمي PaymentAPIError عند الفشل."""

    if not _api_ready():
        raise PaymentAPIError("إعدادات API غير مكتملة.")

    data = _get("shamcash", "balance", {"account_address": SHAM_CASH_ADDRESS})
    items = (data.get("data") or {}).get("balances") or []

    balances = {}

    for item in items:

        try:
            balances[str(item["currency"]).upper()] = Decimal(str(item["balance"]))
        except (KeyError, InvalidOperation):
            continue

    return balances


def find_outgoing_by_ref(ref: str, currency: str, amount: Decimal) -> Optional[dict]:
    """
    يبحث في سجل التحويلات الصادرة عن تحويلنا بالرقم المرجعي الموجود في الملاحظة،
    مع مطابقة العملة والمبلغ. يرمي PaymentAPIError عند فشل الاستعلام.
    """

    if not _api_ready():
        raise PaymentAPIError("إعدادات API غير مكتملة.")

    data = _get("shamcash", "outgoing_logs", {"account_address": SHAM_CASH_ADDRESS})
    items = (data.get("data") or {}).get("items") or []

    for item in items:

        if ref.lower() not in str(item.get("note") or "").lower():
            continue

        try:
            same_amount = Decimal(str(item.get("amount"))) == Decimal(amount)
        except InvalidOperation:
            continue

        if same_amount and str(item.get("currency") or "").upper() == currency:
            return item

    return None


# =========================================================
# التنفيذ الكامل لطلب (للاستدعاء من البوت عبر to_thread)
# =========================================================


def confirm_and_execute(request_id: int, admin_id: int) -> dict:
    """
    بعد التحقق من الـ PIN: يحجز الطلب ذرّياً (مع فحص الحدود)، ينفّذ التحويل، ويسجّل النتيجة.
    يرجع dict فيه status: sent | failed | unknown | missing | not_pending | expired | limit | disabled
    """

    expire_old_pending()

    if not is_enabled():
        return {"status": "disabled"}

    claim, detail = claim_for_sending(request_id, admin_id)

    if claim != "claimed":
        return {"status": claim, "message": detail, "request": get_request(request_id)}

    request = get_request(request_id)

    try:
        result = execute_transfer(
            request["to_address"],
            request["currency"],
            Decimal(request["amount"]),
            compose_note(request["ref"], request["note"] or ""),
        )
    except Exception as exc:                       # أي خطأ غير متوقع = نتيجة غير مؤكدة، لا نخمّن
        _log.exception("unexpected error during transfer %s", request["ref"])
        result = TransferResult("unknown", f"خطأ غير متوقع أثناء التنفيذ ({type(exc).__name__}).")

    if not finish_request(
        request_id,
        result.outcome,
        recipient_name=result.recipient_name or None,
        error=None if result.outcome == "sent" else result.message,
    ):
        _log.warning("transfer %s: could not record outcome %s (status changed meanwhile)", request["ref"], result.outcome)

    _log.info("transfer %s -> %s (%s %s)", request["ref"], result.outcome, request["amount"], request["currency"])

    return {"status": result.outcome, "result": result, "request": get_request(request_id)}


def verify_unknown(request_id: int) -> dict:
    """
    يتحقق من تحويل غير مؤكد عبر سجل الصادر. يرجع:
      {"status": "found", ...}      → وُجد وسُجِّل sent
      {"status": "not_found"}       → لم يظهر (قد يتأخر الظهور) — القرار للأدمن
      {"status": "error", ...}      → فشل الاستعلام
      {"status": "not_unknown"}     → الطلب ليس بحالة unknown
    """

    request = get_request(request_id)

    if request is None or request["status"] != "unknown":
        return {"status": "not_unknown", "request": request}

    try:
        item = find_outgoing_by_ref(request["ref"], request["currency"], Decimal(request["amount"]))
    except Exception as exc:
        return {"status": "error", "message": str(exc), "request": request}

    if item is None:
        return {"status": "not_found", "request": request}

    finish_request(
        request_id, "sent",
        recipient_name=str(item.get("to_name") or "") or None,
        error=None,
        tran_id=str(item.get("tran_id") or "") or None,
        allowed_from=("unknown",),
    )

    return {"status": "found", "item": item, "request": get_request(request_id)}


# =========================================================
# أدوات سطر الأوامر (طوارئ)
# =========================================================

if __name__ == "__main__":

    import sys

    if sys.argv[1:] == ["reset-pin"]:
        reset_pin_and_disable()
        print("✅ تم حذف رمز التحويل وإيقاف التحويلات. اضبط رمزاً جديداً من لوحة الأدمن ثم فعّلها.")
    else:
        print("الاستخدام:  python sham_transfer.py reset-pin")
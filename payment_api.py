"""
payment_api.py
==============
تكامل آمن مع API SYRIA للتحقق الآلي من عمليات:
- Syriatel Cash
- ShamCash

مهم:
1) لا تضع API Key أو أرقام الحسابات داخل هذا الملف.
2) ضعها في متغيرات البيئة (.env).
3) هذا الملف يتحقق من العملية فقط؛ إضافة الرصيد لقاعدة البيانات
   يجب أن تتم بعد نجاح verify_payment() وبطريقة تمنع تكرار نفس العملية.

متوافق مع توثيق API SYRIA الحالي:
https://apisyria.com/api/docs
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Union
from dotenv import load_dotenv
import requests

load_dotenv()
_log = logging.getLogger("payment_api")
BASE_URL = os.getenv("API_SYRIA_BASE_URL", "https://apisyria.com/api/v1").rstrip("/")
API_KEY = os.getenv("API_SYRIA_KEY", "").strip()

# Syriatel:
# يمكن وضع رقم الهاتف أو cash_code المرتبط بالحساب.
SYRIATEL_GSM = os.getenv("SYRIATEL_GSM", "").strip()

# ShamCash:
# عنوان حساب ShamCash.
SHAM_CASH_ADDRESS = os.getenv("SHAM_CASH_ADDRESS", "").strip()

# اختياري (حماية إضافية): اسم صاحب حساب ShamCash كما يظهر في التطبيق.
# إذا ضُبط، تُرفض أي عملية يكون "المرسِل" فيها هو اسمك (أي تحويل صادر منك
# كدفعات السحب) حتى لا يُقدَّم رقمها كإيداع.
SHAM_CASH_OWNER_NAME = os.getenv("SHAM_CASH_OWNER_NAME", "").strip()

# الأمان الافتراضي: بدون SHAM_CASH_OWNER_NAME نرفض عمليات ShamCash
# (لأننا لا نستطيع التفريق بين الإيداع الوارد ودفعات السحب الصادرة).
# ضع ALLOW_SHAMCASH_WITHOUT_OWNER_NAME=1 فقط إن كنت تقبل هذا الخطر عمداً.
ALLOW_SHAMCASH_WITHOUT_OWNER_NAME = (
    os.getenv("ALLOW_SHAMCASH_WITHOUT_OWNER_NAME", "0").strip() == "1"
)

REQUEST_TIMEOUT = float(os.getenv("PAYMENT_API_TIMEOUT", "15"))
MAX_RETRIES = int(os.getenv("PAYMENT_API_MAX_RETRIES", "2"))

# إذا كان النظام عندك يستخدم SYP فقط، اتركها SYP.
EXPECTED_CURRENCY = os.getenv("PAYMENT_CURRENCY", "SYP").upper()
# معامل تحويل العملة:
# 100 ليرة سورية قديمة = 1 ليرة سورية جديدة
CURRENCY_CONVERSION_RATE = int(
    os.getenv("CURRENCY_CONVERSION_RATE", "100")
)

# هل API يعيد المبالغ بالعملة القديمة؟
# True = نعم، سيتم تحويلها إلى العملة الجديدة داخل البوت.
API_AMOUNT_IS_OLD_CURRENCY = False


def api_amount_to_bot_amount(amount: int) -> int:
    """
    يحول مبلغ API من الليرة القديمة إلى الليرة الجديدة.
    مثال:
        5000 قديمة -> 50 جديدة
    """
    if not API_AMOUNT_IS_OLD_CURRENCY:
        return amount

    if CURRENCY_CONVERSION_RATE <= 0:
        raise PaymentAPIError(
            "CURRENCY_CONVERSION_RATE يجب أن يكون أكبر من صفر."
        )

    if amount % CURRENCY_CONVERSION_RATE != 0:
        raise PaymentAPIError(
            f"المبلغ {amount} لا يقبل التحويل بدقة "
            f"بمعدل 1/{CURRENCY_CONVERSION_RATE}."
        )

    return amount // CURRENCY_CONVERSION_RATE

class PaymentAPIError(Exception):
    """خطأ عام في API الدفع."""


class PaymentAPIAuthError(PaymentAPIError):
    """مشكلة في API Key أو الصلاحيات."""


class PaymentAPIRateLimitError(PaymentAPIError):
    """تم تجاوز Rate Limit."""


class PaymentAPINotFoundError(PaymentAPIError):
    """الحساب أو endpoint غير موجود."""


@dataclass(frozen=True)
class VerifiedPayment:
    method: str
    transaction_id: str
    # SYP: عدد صحيح (int) — USD/EUR: Decimal بدقة خانتين عشريتين
    amount: Union[int, Decimal]
    currency: str
    date: str
    sender: str = ""
    receiver: str = ""
    account: str = ""
    sender_account: str = ""
    raw: Optional[dict[str, Any]] = None


def _require_api_key() -> None:
    if not API_KEY:
        raise PaymentAPIAuthError(
            "API_SYRIA_KEY غير مضبوط. أضفه في متغيرات البيئة."
        )


def _get(
    resource: str,
    action: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """
    GET موحد مع معالجة أخطاء API SYRIA وإعادة المحاولة عند أخطاء الشبكة.
    """
    _require_api_key()

    query = {
        "resource": resource,
        "action": action,
        **params,
    }

    # نستخدم Header بدل وضع المفتاح في URL.
    headers = {
        "X-Api-Key": API_KEY,
        "Accept": "application/json",
    }

    last_error: Optional[Exception] = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.get(
                BASE_URL,
                params=query,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )

            # Rate limit
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                if retry_after and attempt < MAX_RETRIES:
                    try:
                        time.sleep(min(float(retry_after), 30.0))
                    except ValueError:
                        time.sleep(2.0)
                    continue

                raise PaymentAPIRateLimitError(
                    f"تم تجاوز الحد المسموح لطلبات API SYRIA. "
                    f"HTTP {response.status_code}"
                )

            if response.status_code == 401:
                raise PaymentAPIAuthError(
                    "API Key غير صالح أو غير مرسل بشكل صحيح."
                )

            if response.status_code == 403:
                raise PaymentAPIAuthError(
                    "ليس لديك صلاحية لاستخدام هذا الحساب/الخدمة."
                )

            if response.status_code == 404:
                raise PaymentAPINotFoundError(
                    "الحساب غير موجود."
                )

            response.raise_for_status()

            try:
                data = response.json()
            except ValueError as exc:
                raise PaymentAPIError(
                    "API SYRIA أعاد استجابة ليست JSON."
                ) from exc

            if not isinstance(data, dict):
                raise PaymentAPIError("صيغة استجابة API غير متوقعة.")

            if data.get("success") is False:
                raise PaymentAPIError(
                    str(data.get("error") or "فشل طلب API.")
                )

            return data

        except (PaymentAPIError, requests.HTTPError) as exc:
            last_error = exc

            # لا نكرر أخطاء API المنطقية.
            if isinstance(exc, PaymentAPIError):
                raise

            if attempt < MAX_RETRIES:
                time.sleep(1.0 * (attempt + 1))
                continue

        except requests.RequestException as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(1.0 * (attempt + 1))
                continue

    raise PaymentAPIError(
        f"تعذر الاتصال بـ API SYRIA: {last_error}"
    )


def _as_int_amount(value: Any) -> int:
    """
    يحول مبلغ الاستجابة إلى integer بدون الاعتماد على شكل واحد فقط.
    """
    try:
        # API قد يعيد "25000" أو 25000 أو 25000.0
        amount = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise PaymentAPIError(
            f"قيمة المبلغ غير صالحة في استجابة API: {value!r}"
        ) from exc

    if amount < 0:
        raise PaymentAPIError("API أعاد مبلغاً سالباً.")

    if not amount.is_integer():
        raise PaymentAPIError(
            f"المبلغ غير صحيح كنظام ليرة سورية: {value!r}"
        )

    return int(amount)


def _as_decimal_amount(value: Any) -> Decimal:
    """
    يحول مبلغ الاستجابة إلى Decimal (للعملات ذات الكسور مثل USD/EUR).
    يرفض القيم السالبة/الصفرية/غير الرقمية وأكثر من خانتين عشريتين.
    """
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise PaymentAPIError(
            f"قيمة المبلغ غير صالحة في استجابة API: {value!r}"
        ) from exc

    if not amount.is_finite():
        raise PaymentAPIError(
            f"قيمة المبلغ غير صالحة في استجابة API: {value!r}"
        )

    if amount <= 0:
        raise PaymentAPIError("API أعاد مبلغاً غير موجب.")

    quantized = amount.quantize(Decimal("0.01"))
    if quantized != amount:
        raise PaymentAPIError(
            f"المبلغ يحتوي أكثر من خانتين عشريتين: {value!r}"
        )

    return quantized


def _parse_amount_for_currency(value: Any, currency: str) -> Union[int, Decimal]:
    """SYP → int (كما كان)، غير ذلك → Decimal."""
    if currency == "SYP":
        return _as_int_amount(value)
    return _as_decimal_amount(value)


def _amounts_equal(actual: Union[int, Decimal], expected: Any) -> bool:
    try:
        return Decimal(str(actual)) == Decimal(str(expected))
    except (InvalidOperation, ValueError):
        return False


def _normalize_syriatel_transaction(
    data: dict[str, Any],
) -> Optional[VerifiedPayment]:
    payload = data.get("data") or {}

    if not payload.get("found"):
        return None

    tx = payload.get("transaction") or {}
    account = payload.get("account") or {}

    tx_id = str(tx.get("transaction_no") or "").strip()
    if not tx_id:
        return None

    return VerifiedPayment(
        method="Syriatel Cash",
        transaction_id=tx_id,
        amount=_as_int_amount(tx.get("amount")),
        currency=str(tx.get("currency") or EXPECTED_CURRENCY).upper(),
        date=str(tx.get("date") or ""),
        sender=str(tx.get("from") or ""),
        receiver=str(tx.get("to") or ""),
        account=str(
            account.get("gsm")
            or account.get("cash_code")
            or SYRIATEL_GSM
        ),
        sender_account=str(tx.get("from") or "").strip(),
        raw=payload,
    )


def _normalize_shamcash_transaction(
    data: dict[str, Any],
) -> Optional[VerifiedPayment]:
    payload = data.get("data") or {}

    if not payload.get("found"):
        return None

    tx = payload.get("transaction") or {}
    account = payload.get("account") or {}

    tx_id = str(tx.get("tran_id") or "").strip()
    if not tx_id:
        return None

    currency = str(tx.get("currency") or EXPECTED_CURRENCY).strip().upper()

    return VerifiedPayment(
        method="Sham Cash",
        transaction_id=tx_id,
        amount=_parse_amount_for_currency(tx.get("amount"), currency),
        currency=currency,
        date=str(tx.get("datetime") or ""),
        sender=str(tx.get("from_name") or ""),
        receiver=str(tx.get("to_name") or ""),
        account=str(
            tx.get("account")
            or account.get("account_address")
            or SHAM_CASH_ADDRESS
        ),
        raw=payload,
    )


def find_syriatel_transaction(
    transaction_id: str,
    *,
    period: str = "7",
) -> Optional[VerifiedPayment]:
    """
    البحث عن عملية Syriatel Cash برقم العملية.

    period حسب API:
      7 / 30 / all
    """
    transaction_id = str(transaction_id).strip()

    if not (transaction_id.isascii() and transaction_id.isdigit()) or not 3 <= len(transaction_id) <= 30:
        raise ValueError("رقم عملية Syriatel Cash غير صالح.")

    if not SYRIATEL_GSM:
        raise PaymentAPIError(
            "SYRIATEL_GSM غير مضبوط في متغيرات البيئة."
        )

    if period not in {"7", "30", "all"}:
        raise ValueError("period يجب أن يكون 7 أو 30 أو all.")

    data = _get(
        "syriatel",
        "find_tx",
        {
            "tx": transaction_id,
            "gsm": SYRIATEL_GSM,
            "period": period,
        },
    )

    return _normalize_syriatel_transaction(data)


def find_shamcash_transaction(
    transaction_id: str,
) -> Optional[VerifiedPayment]:
    """
    البحث عن عملية ShamCash برقم العملية.
    """
    transaction_id = str(transaction_id).strip()

    if not (transaction_id.isascii() and transaction_id.isdigit()) or not 3 <= len(transaction_id) <= 30:
        raise ValueError("رقم عملية ShamCash غير صالح.")

    if not SHAM_CASH_ADDRESS:
        raise PaymentAPIError(
            "SHAM_CASH_ADDRESS غير مضبوط في متغيرات البيئة."
        )

    data = _get(
        "shamcash",
        "find_tx",
        {
            "tx": transaction_id,
            "account_address": SHAM_CASH_ADDRESS,
        },
    )

    return _normalize_shamcash_transaction(data)


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _same_txid(a: Any, b: Any) -> bool:
    """مقارنة رقمي عمليتين بعد إزالة الأصفار البادئة والمسافات."""
    def norm(v: Any) -> str:
        s = "".join(str(v or "").split()).lower()
        if s.isascii() and s.isdigit():
            s = s.lstrip("0") or "0"
        return s

    return bool(norm(a)) and norm(a) == norm(b)


def _same_name(a: Any, b: Any) -> bool:
    norm = lambda v: " ".join(str(v or "").split()).casefold()
    return bool(norm(a)) and norm(a) == norm(b)


def _is_outgoing_from_our_account(method_normalized: str, payment: VerifiedPayment) -> bool:
    """
    هل العملية صادرة من حسابنا نحن (مثل دفعة سحب لاعب) وليست إيداعاً وارداً؟
    نرفضها حتى لا يُقدِّم اللاعب رقم عملية سحبه كأنها إيداع.

    - Syriatel: المرسِل (from) هو رقمنا/كود الكاش → صادرة.
    - ShamCash: المرسِل هو اسم صاحب الحساب (يتطلب SHAM_CASH_OWNER_NAME).
    """
    if method_normalized in {"syriatel cash", "syriatel", "syriatel_cash"}:
        sender = payment.sender_account
        if not sender:
            return False

        account = (payment.raw or {}).get("account") or {}
        ours = {SYRIATEL_GSM, str(account.get("gsm") or ""), str(account.get("cash_code") or "")}

        sender_digits = _digits(sender)
        for mine in ours:
            mine = mine.strip()
            if not mine:
                continue
            if sender.strip() == mine:
                return True
            mine_digits = _digits(mine)
            if len(sender_digits) >= 9 and len(mine_digits) >= 9 \
                    and sender_digits[-9:] == mine_digits[-9:]:
                return True
        return False

    if SHAM_CASH_OWNER_NAME:
        return _same_name(payment.sender, SHAM_CASH_OWNER_NAME)

    return False


def verify_payment(
    method: str,
    transaction_id: str,
    expected_amount: Optional[Union[int, Decimal, str]] = None,
    *,
    period: str = "7",
    expected_currency: Optional[str] = None,
) -> tuple[bool, str, Optional[VerifiedPayment]]:
    """
    التحقق الآمن من العملية قبل إضافة الرصيد.

    expected_amount:
      إذا أعطي، يجب أن يساوي المبلغ الفعلي الموجود في API.
      لاحقاً يمكننا إزالة هذا الإدخال وجعل API هو مصدر الحقيقة بالكامل.

    expected_currency:
      العملة المطلوبة للعملية (افتراضياً EXPECTED_CURRENCY = SYP).
      استخدم "USD" للتحقق من تحويلات ShamCash بالدولار.
      Syriatel Cash يدعم الليرة السورية فقط.
    """
    method_normalized = method.strip().lower()
    currency_required = (expected_currency or EXPECTED_CURRENCY).strip().upper()

    try:
        if method_normalized in {
            "sham cash",
            "shamcash",
            "sham_cash",
        }:
            if not SHAM_CASH_OWNER_NAME and not ALLOW_SHAMCASH_WITHOUT_OWNER_NAME:
                _log.error(
                    "ShamCash verification refused: SHAM_CASH_OWNER_NAME is not set."
                )
                return (
                    False,
                    "التحقق من ShamCash معطّل مؤقتاً لأسباب أمنية. "
                    "تواصل مع الإدارة.",
                    None,
                )

            payment = find_shamcash_transaction(transaction_id)

        elif method_normalized in {
            "syriatel cash",
            "syriatel",
            "syriatel_cash",
        }:
            if currency_required != "SYP":
                return (
                    False,
                    "Syriatel Cash يدعم الليرة السورية فقط.",
                    None,
                )

            payment = find_syriatel_transaction(
                transaction_id,
                period=period,
            )

        else:
            return False, "طريقة دفع غير مدعومة.", None

        if payment is None:
            return False, "لم يتم العثور على العملية.", None

        # رقم العملية المرجَع من API يجب أن يطابق ما طلبناه تماماً
        if not _same_txid(payment.transaction_id, transaction_id):
            _log.warning(
                "txid mismatch: requested=%r returned=%r",
                transaction_id, payment.transaction_id,
            )
            return False, "رقم العملية لا يطابق سجلات الدفع.", None

        # مبلغ صفر/سالب مرفوض دائماً
        try:
            if Decimal(str(payment.amount)) <= 0:
                return False, "مبلغ العملية غير صالح.", payment
        except (InvalidOperation, ValueError):
            return False, "مبلغ العملية غير صالح.", payment

        # Syriatel: إن لم نعرف المرسِل لا نستطيع التأكد أنها عملية واردة
        if (
            method_normalized in {"syriatel cash", "syriatel", "syriatel_cash"}
            and not payment.sender_account
        ):
            return False, "تعذر تحديد مرسل العملية.", payment

        if _is_outgoing_from_our_account(method_normalized, payment):
            return (
                False,
                "هذه العملية صادرة من حسابنا وليست تحويلاً وارداً إلينا.",
                payment,
            )

        if payment.currency != currency_required:
            return (
                False,
                f"عملة العملية {payment.currency} وليست {currency_required}.",
                payment,
            )

        if expected_amount is not None:
            if not _amounts_equal(payment.amount, expected_amount):
                return (
                    False,
                    (
                        "المبلغ غير مطابق. "
                        f"المطلوب: {expected_amount}، "
                        f"الموجود في العملية: {payment.amount}."
                    ),
                    payment,
                )

        return True, "تم التحقق من العملية بنجاح.", payment

    except ValueError as exc:
        return False, str(exc), None
    except PaymentAPIError as exc:
        return False, str(exc), None


def wait_for_payment(
    method: str,
    transaction_id: str,
    expected_amount: Optional[Union[int, Decimal, str]] = None,
    *,
    attempts: int = 3,
    interval_seconds: float = 3.0,
    period: str = "7",
    expected_currency: Optional[str] = None,
) -> tuple[bool, str, Optional[VerifiedPayment]]:
    """
    أحياناً العملية لا تظهر فوراً عند API.

    هذه الدالة تعيد المحاولة عدة مرات، ثم تعيد آخر نتيجة.
    """
    attempts = max(1, int(attempts))

    last_result: tuple[bool, str, Optional[VerifiedPayment]] = (
        False,
        "لم يتم العثور على العملية.",
        None,
    )

    for attempt in range(attempts):
        last_result = verify_payment(
            method,
            transaction_id,
            expected_amount,
            period=period,
            expected_currency=expected_currency,
        )

        if last_result[0]:
            return last_result

        if attempt < attempts - 1:
            time.sleep(max(0.0, float(interval_seconds)))

    return last_result


def security_warnings() -> list[str]:
    """
    تحذيرات أمنية غير مانعة للتشغيل (إعدادات تُضعف حماية التحقق من العمليات).
    """
    warnings: list[str] = []

    if not SHAM_CASH_OWNER_NAME:
        if ALLOW_SHAMCASH_WITHOUT_OWNER_NAME:
            warnings.append(
                "SHAM_CASH_OWNER_NAME غير مضبوط: حماية ShamCash من استخدام رقم عملية "
                "دفعة سحب صادرة من حسابنا كإيداع غير مفعّلة. "
                "اضبطه باسم صاحب حساب ShamCash كما يظهر في سجل العمليات."
            )
        else:
            warnings.append(
                "SHAM_CASH_OWNER_NAME غير مضبوط: تم تعطيل التحقق من ShamCash "
                "بالكامل حتى تضبطه باسم صاحب الحساب كما يظهر في سجل العمليات."
            )

    return warnings


for _warning in security_warnings():
    _log.warning(_warning)


def check_api_configuration() -> tuple[bool, list[str]]:
    """
    فحص الإعدادات قبل تشغيل البوت.
    """
    missing: list[str] = []

    if not API_KEY:
        missing.append("API_SYRIA_KEY")

    if not SYRIATEL_GSM:
        missing.append("SYRIATEL_GSM")

    if not SHAM_CASH_ADDRESS:
        missing.append("SHAM_CASH_ADDRESS")

    return (len(missing) == 0, missing)


if __name__ == "__main__":
    ok, missing = check_api_configuration()

    if not ok:
        print("❌ إعدادات ناقصة:")
        for item in missing:
            print(f"   - {item}")
        raise SystemExit(1)

    print("✅ إعدادات Payment API تبدو مكتملة.")

    for warning in security_warnings():
        print(f"⚠️ {warning}")
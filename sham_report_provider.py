"""
مزوّد سجل Sham Cash للتقرير اليومي — يفعّل قسم «🔎 مطابقة شام كاش».

يُسجَّل بـ:  daily_report.set_sham_history_provider(sham_report_provider.provider)

العقد (من daily_report.py): provider(start_utc, end_utc) ← قائمة قواميس:
    {"id", "direction": "in"|"out", "amount", "name", "account", "time"}
  • start_utc/end_utc و time: توقيت UTC بدون tzinfo.
  • amount: بالليرة السورية الجديدة.

المصدر: apisyria  (resource=shamcash)
    action=logs           ← الواردة   (tran_id, from_name, currency, amount, datetime, account, note)
    action=outgoing_logs  ← الصادرة   (tran_id, to_name,   currency, amount, datetime, account, note)

ملاحظات:
  - الدولار الوارد يُحوَّل لليرة بسعر الصرف الحالي (للإجماليات فقط؛ المطابقة تتم برقم العملية).
  - الصادر بالدولار والعملات الأخرى يُتجاهل (لا يقابل طلبات السحب بالليرة).
  - توقيت العمليات من apisyria يُفترض توقيت سوريا (UTC+3). غيّر SHAM_LOG_TZ_OFFSET_HOURS إن كان غير ذلك.
  - أي فشل بالـ API يُرمى كاستثناء ليظهر كتنبيه واضح بالتقرير بدل أرقام ناقصة بصمت.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import sham_transfer as sham_tx
from database import get_shamcash_usd_rate

SHAM_LOG_TZ_OFFSET_HOURS = 3

_log = logging.getLogger("sham_report")


def _parse_time(value):
    """وقت العملية من apisyria ← datetime بتوقيت UTC بدون tzinfo، أو None."""
    s = str(value or "").strip()
    if not s:
        return None

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s[:19], fmt) - timedelta(hours=SHAM_LOG_TZ_OFFSET_HOURS)
        except ValueError:
            continue

    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt - timedelta(hours=SHAM_LOG_TZ_OFFSET_HOURS)


def _to_row(item, direction, usd_rate):
    if not isinstance(item, dict):
        return None

    tran_id = str(item.get("tran_id") or "").strip()
    when = _parse_time(item.get("datetime"))
    if not tran_id or when is None:
        return None

    currency = str(item.get("currency") or "SYP").strip().upper()
    try:
        amount = Decimal(str(item.get("amount")).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None

    if currency == "USD":
        if direction == "out" or not usd_rate:
            return None
        amount = amount * Decimal(usd_rate)
    elif currency != "SYP":
        return None

    amount = int(amount) if amount == amount.to_integral_value() else float(amount)

    name_key = "from_name" if direction == "in" else "to_name"
    return {
        "id": tran_id,
        "direction": direction,
        "amount": amount,
        "name": str(item.get(name_key) or ""),
        "account": str(item.get("account") or ""),
        "time": when,
        "currency": currency,
    }


def _naive_utc(dt):
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def fetch_rows(start_utc, end_utc):
    """صفوف الواردة والصادرة ضمن [start_utc, end_utc). ترمي استثناءً عند فشل الـ API."""
    if not sham_tx._api_ready():
        raise RuntimeError("إعدادات API غير مكتملة.")

    start_utc, end_utc = _naive_utc(start_utc), _naive_utc(end_utc)

    try:
        usd_rate = int(get_shamcash_usd_rate())
    except Exception:
        usd_rate = 0

    rows = []
    for action, direction in (("logs", "in"), ("outgoing_logs", "out")):
        payload = sham_tx._get("shamcash", action, {"account_address": sham_tx.SHAM_CASH_ADDRESS})
        items = ((payload or {}).get("data") or {}).get("items") or []
        for item in items:
            row = _to_row(item, direction, usd_rate)
            if row is not None and start_utc <= row["time"] < end_utc:
                rows.append(row)

    return rows


async def provider(start_utc, end_utc):
    return await asyncio.to_thread(fetch_rows, start_utc, end_utc)
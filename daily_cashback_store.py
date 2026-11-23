"""
daily_cashback_store.py
=======================
طبقة البيانات + قواعد الاحتساب لنظام التعويض اليومي (Daily Cashback)،
ووضع التعويض الفعّال (أسبوعي / يومي / متوقف).

هذا الملف مستقل عن database.py عمداً (لا يعدّل عليه) ويستخدم نفس الاتصال
(database._db) ونفس جدول settings وجدول ichancy_transactions.

فكرة النظام
-----------
الرصيد في iChancy عند الساعة 12:00 ليلاً لا يمكن معرفته بعد وقوعه (الـ API يعطي
الرصيد الحالي فقط)، لذلك يأخذ البوت "لقطة" لرصيد كل لاعب عند منتصف الليل
(daily_cashback_snapshots). تعويض يوم D يُحسب من لقطتين:

    لقطة بداية اليوم  (boundary_day = D)      → رصيد 12:00 ليلاً بداية اليوم
    لقطة نهاية اليوم  (boundary_day = D + 1)  → رصيد 12:00 ليلاً نهاية اليوم

وحركات الشحن/السحب تُجمع بين توقيتَي اللقطتين الفعليَّين (وليس بين 00:00 و00:00
بالتقريب) حتى لا تُحتسب أي حركة مرتين أو تضيع بين يومين.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional

from database import _db

# =========================================================
# إعدادات
# =========================================================

CASHBACK_MODES = ("weekly", "daily", "off")
DEFAULT_CASHBACK_MODE = "weekly"          # الافتراضي = السلوك القديم (لا يتغيّر شيء قبل أن يختار الأدمن)

DEFAULT_DAILY_CASHBACK_PERCENT = 1.0      # "بالكامل" = 100% من خسارة اليوم السابق (يعدّلها الأدمن)

# «اختيار المستخدم» معطّل نهائياً: الأدمن فقط يفعّل نظاماً واحداً (يومي أو أسبوعي) ويسري على الجميع.
# (المفتاح محفوظ كسلوك قديم فقط، وليس له زر بلوحة الأدمن.)
DEFAULT_CASHBACK_USER_CHOICE = False

MAX_WEEKLY_DURATION_WEEKS = 520           # أقصى مدة للأسبوعي (10 سنوات) — 0 = بدون حد
MAX_DAILY_DURATION_DAYS = 3650            # أقصى مدة لليومي (10 سنوات) — 0 = بدون حد

# رصيد أقل من هذا (بالـ NSP) يُعتبر صفراً (يتجاوز غبار الأرقام العشرية)
ZERO_BALANCE_TOLERANCE_NSP = 1.0

EPOCH_TS = "1970-01-01 00:00:00"

# =========================================================
# إنشاء الجداول
# =========================================================

with _db() as _conn:

    _conn.execute("""
    CREATE TABLE IF NOT EXISTS daily_cashback_snapshots (
        user_id INTEGER NOT NULL,
        boundary_day TEXT NOT NULL,
        balance_nsp REAL NOT NULL,
        taken_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, boundary_day)
    )
    """)

    _conn.execute("""
    CREATE TABLE IF NOT EXISTS daily_cashback_snapshot_runs (
        boundary_day TEXT PRIMARY KEY,
        taken INTEGER NOT NULL DEFAULT 0,
        failed INTEGER NOT NULL DEFAULT 0,
        finished_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    _conn.execute("""
    CREATE TABLE IF NOT EXISTS daily_cashback_runs (
        target_day TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'running',
        attempts INTEGER NOT NULL DEFAULT 0,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        finished_at TIMESTAMP,
        summary TEXT
    )
    """)

    _conn.execute("""
    CREATE TABLE IF NOT EXISTS daily_cashback_payouts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        target_day TEXT NOT NULL,
        loss_amount INTEGER NOT NULL,
        cashback_amount INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (user_id, target_day)
    )
    """)

# =========================================================
# وضع التعويض (أسبوعي / يومي / متوقف)
# =========================================================


def get_cashback_mode() -> str:
    """الوضع الفعّال. أي قيمة غير معروفة تعود للافتراضي (الأسبوعي)."""

    with _db() as conn:

        row = conn.execute("""
            SELECT value
            FROM settings
            WHERE key='cashback_mode'
        """).fetchone()

    mode = str(row[0]).strip().lower() if row else DEFAULT_CASHBACK_MODE
    return mode if mode in CASHBACK_MODES else DEFAULT_CASHBACK_MODE


def set_cashback_mode(mode: str) -> None:
    """
    يغيّر وضع التعويض. عند الانتقال لوضع جديد:
      - أسبوعي/يومي → تُسجَّل لحظة التفعيل (بداية البرنامج) لحساب المدة.
      - متوقف → تُمسح لحظة البداية.
    الضغط على نفس الوضع الفعّال لا يغيّر شيئاً (لا يعيد تشغيل عدّاد المدة).
    """

    mode = str(mode).strip().lower()

    if mode not in CASHBACK_MODES:
        raise ValueError(f"وضع التعويض غير صالح: {mode!r}")

    with _db() as conn:

        row = conn.execute("""
            SELECT value
            FROM settings
            WHERE key='cashback_mode'
        """).fetchone()

        old_mode = str(row[0]).strip().lower() if row else DEFAULT_CASHBACK_MODE
        if old_mode not in CASHBACK_MODES:
            old_mode = DEFAULT_CASHBACK_MODE

        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value)
            VALUES ('cashback_mode', ?)
        """, (mode,))

        if mode != old_mode or row is None:

            if mode in ("weekly", "daily"):
                conn.execute("""
                    INSERT OR REPLACE INTO settings (key, value)
                    VALUES ('cashback_started_at', datetime('now'))
                """)
            else:
                conn.execute("""
                    DELETE FROM settings
                    WHERE key='cashback_started_at'
                """)


# =========================================================
# إعدادات: نسبة اليومي + مدة كل نظام + بداية البرنامج
# =========================================================


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


def is_cashback_user_choice_enabled() -> bool:
    """هل ميزة «المستخدم يختار يومي/أسبوعي» مفعّلة؟"""

    raw = _get_setting("cashback_user_choice")

    if raw is None:
        return DEFAULT_CASHBACK_USER_CHOICE

    return raw.strip() == "1"


def set_cashback_user_choice_enabled(enabled: bool) -> None:

    _set_setting("cashback_user_choice", "1" if enabled else "0")


def get_daily_cashback_percent() -> float:
    """نسبة التعويض اليومي (عشرية: 1.0 = 100%). أي قيمة تالفة ترجع للافتراضي."""

    raw = _get_setting("daily_cashback_percent")

    if raw is None:
        return DEFAULT_DAILY_CASHBACK_PERCENT

    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_DAILY_CASHBACK_PERCENT

    if not (0.0 <= value <= 1.0):          # يرفض NaN/inf/خارج النطاق
        return DEFAULT_DAILY_CASHBACK_PERCENT

    return value


def set_daily_cashback_percent(percent: float) -> None:

    percent = float(percent)

    if not (0.0 <= percent <= 1.0):
        raise ValueError("نسبة التعويض اليومي يجب أن تكون بين 0 و 1 (0%–100%).")

    _set_setting("daily_cashback_percent", repr(percent))


def _get_int_setting(key: str, maximum: int) -> int:

    raw = _get_setting(key)

    try:
        value = int(raw) if raw is not None else 0
    except ValueError:
        return 0

    return value if 0 <= value <= maximum else 0


def _set_int_setting(key: str, value: int, maximum: int, label: str) -> None:

    if isinstance(value, bool) or int(value) != value:
        raise ValueError(f"{label} يجب أن تكون عدداً صحيحاً.")

    value = int(value)

    if not 0 <= value <= maximum:
        raise ValueError(f"{label} يجب أن تكون بين 0 و {maximum} (0 = بدون حد).")

    _set_setting(key, str(value))


def get_weekly_duration_weeks() -> int:
    """مدة الاسترداد الأسبوعي بالأسابيع (0 = بدون حد)."""
    return _get_int_setting("cashback_weekly_weeks", MAX_WEEKLY_DURATION_WEEKS)


def set_weekly_duration_weeks(weeks: int) -> None:
    _set_int_setting("cashback_weekly_weeks", weeks, MAX_WEEKLY_DURATION_WEEKS, "مدة الأسبوعي (بالأسابيع)")


def get_daily_duration_days() -> int:
    """مدة التعويض اليومي بالأيام (0 = بدون حد)."""
    return _get_int_setting("cashback_daily_days", MAX_DAILY_DURATION_DAYS)


def set_daily_duration_days(days: int) -> None:
    _set_int_setting("cashback_daily_days", days, MAX_DAILY_DURATION_DAYS, "مدة اليومي (بالأيام)")


def get_cashback_started_at() -> Optional[str]:
    """لحظة تفعيل البرنامج الحالي (UTC، بنفس صيغة CURRENT_TIMESTAMP) أو None."""
    return _get_setting("cashback_started_at")


def set_cashback_started_at(timestamp: Optional[str]) -> None:
    """لاختبارات/تصحيح يدوي: None يمسح لحظة البداية."""

    if timestamp is None:
        with _db() as conn:
            conn.execute("DELETE FROM settings WHERE key='cashback_started_at'")
        return

    _set_setting("cashback_started_at", str(timestamp))


def ensure_cashback_started_at() -> str:
    """
    يرجع لحظة بداية البرنامج، وينشئها (الآن) إذا لم تكن مسجّلة —
    مثلاً نظام كان فعّالاً قبل إضافة ميزة المدة.
    """

    with _db() as conn:

        row = conn.execute("""
            SELECT value
            FROM settings
            WHERE key='cashback_started_at'
        """).fetchone()

        if row:
            return str(row[0])

        conn.execute("""
            INSERT INTO settings (key, value)
            VALUES ('cashback_started_at', datetime('now'))
        """)

        row = conn.execute("""
            SELECT value
            FROM settings
            WHERE key='cashback_started_at'
        """).fetchone()

    return str(row[0])


def weekly_program_end(started_at: Optional[str], weeks: int) -> Optional[str]:
    """
    لحظة انتهاء الأسبوعي (UTC) = البداية + weeks × 7 أيام،
    أو None إذا لا مدة محددة/لا بداية.
    """

    if not started_at or not weeks or weeks <= 0:
        return None

    try:
        start = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

    return (start + timedelta(weeks=int(weeks))).strftime("%Y-%m-%d %H:%M:%S")


# =========================================================
# قواعد الاحتساب (دالة نقية بدون قاعدة بيانات)
# =========================================================


def evaluate_daily_cashback(
    start_balance_nsp: float,
    end_balance_nsp: float,
    deposits_nsp: float,
    withdrawals_nsp: float,
) -> tuple[str, float]:
    """
    يطبّق شروط التعويض اليومي على يوم كامل ويرجع (الحالة, صافي الخسارة بالـ NSP).

    الشروط (بالترتيب):
      1) الرصيد عند 12:00 ليلاً (نهاية اليوم) يجب أن يساوي 0
         — وهذا يغطي شرط "الرصيد أكبر من إيداعات اليوم → لا تعويض".
      2) إذا كانت السحوبات أكبر من الإيداعات (رابح) → لا تعويض.
      3) صافي الخسارة = (رصيد بداية اليوم + الإيداعات) − (رصيد نهاية اليوم + السحوبات)
         ويجب أن يكون موجباً.

    الحالات: eligible | balance_not_zero | winner | no_loss
    """

    if end_balance_nsp >= ZERO_BALANCE_TOLERANCE_NSP:
        return "balance_not_zero", 0.0

    if withdrawals_nsp - deposits_nsp > 1e-9:
        return "winner", 0.0

    net_loss = (start_balance_nsp + deposits_nsp) - (end_balance_nsp + withdrawals_nsp)

    if net_loss <= 1e-9:
        return "no_loss", 0.0

    return "eligible", float(net_loss)


# =========================================================
# لقطات منتصف الليل
# =========================================================


def save_daily_snapshot(
    user_id: int,
    boundary_day: str,
    balance_nsp: float,
    taken_at: Optional[str] = None,
) -> bool:
    """
    يحفظ لقطة رصيد (أول لقطة تفوز — لا تُستبدل). يرجع True إذا حُفظت الآن.
    taken_at: لاختبارات فقط؛ افتراضياً توقيت الخادم (UTC) لحظة الحفظ، وهو نفس
    مصدر created_at في ichancy_transactions.
    """

    with _db() as conn:

        if taken_at is None:
            cur = conn.execute("""
                INSERT OR IGNORE INTO daily_cashback_snapshots
                    (user_id, boundary_day, balance_nsp)
                VALUES (?, ?, ?)
            """, (int(user_id), boundary_day, float(balance_nsp)))
        else:
            cur = conn.execute("""
                INSERT OR IGNORE INTO daily_cashback_snapshots
                    (user_id, boundary_day, balance_nsp, taken_at)
                VALUES (?, ?, ?, ?)
            """, (int(user_id), boundary_day, float(balance_nsp), taken_at))

        return cur.rowcount == 1


def get_daily_snapshot(user_id: int, boundary_day: str) -> Optional[dict]:

    with _db() as conn:

        row = conn.execute("""
            SELECT balance_nsp, taken_at
            FROM daily_cashback_snapshots
            WHERE user_id=? AND boundary_day=?
        """, (int(user_id), boundary_day)).fetchone()

    if not row:
        return None

    return {"balance_nsp": float(row[0]), "taken_at": str(row[1])}


def users_with_daily_snapshot(boundary_day: str) -> set:

    with _db() as conn:

        rows = conn.execute("""
            SELECT user_id
            FROM daily_cashback_snapshots
            WHERE boundary_day=?
        """, (boundary_day,)).fetchall()

    return {int(r[0]) for r in rows}


def count_daily_snapshots(boundary_day: str) -> int:

    with _db() as conn:

        row = conn.execute("""
            SELECT COUNT(*)
            FROM daily_cashback_snapshots
            WHERE boundary_day=?
        """, (boundary_day,)).fetchone()

    return int(row[0])


def is_snapshot_run_done(boundary_day: str) -> bool:

    with _db() as conn:

        row = conn.execute("""
            SELECT 1
            FROM daily_cashback_snapshot_runs
            WHERE boundary_day=?
        """, (boundary_day,)).fetchone()

    return row is not None


def mark_snapshot_run_done(boundary_day: str, taken: int, failed: int) -> None:

    with _db() as conn:

        conn.execute("""
            INSERT OR REPLACE INTO daily_cashback_snapshot_runs
                (boundary_day, taken, failed)
            VALUES (?, ?, ?)
        """, (boundary_day, int(taken), int(failed)))


# =========================================================
# حركات الشحن/السحب بين لقطتين
# =========================================================


def get_ichancy_movement_between(user_id: int, start_ts: str, end_ts: str) -> tuple[float, float]:
    """
    (مجموع الشحن, مجموع السحب) بالـ NSP ضمن (start_ts, end_ts]
    — بداية مفتوحة ونهاية مغلقة، حتى لا تُحتسب أي حركة في يومين متجاورين.
    """

    with _db() as conn:

        row = conn.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN direction='in'  THEN amount_nsp ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN direction='out' THEN amount_nsp ELSE 0 END), 0)
            FROM ichancy_transactions
            WHERE user_id=?
            AND created_at > ?
            AND created_at <= ?
        """, (int(user_id), start_ts, end_ts)).fetchone()

    return float(row[0]), float(row[1])


def has_ichancy_movement_at_or_before(user_id: int, ts: str) -> bool:

    with _db() as conn:

        row = conn.execute("""
            SELECT 1
            FROM ichancy_transactions
            WHERE user_id=? AND created_at <= ?
            LIMIT 1
        """, (int(user_id), ts)).fetchone()

    return row is not None


# =========================================================
# تنفيذ التعويض (مرة واحدة لكل يوم، ولكل لاعب)
# =========================================================


def is_daily_run_done(target_day: str) -> bool:

    with _db() as conn:

        row = conn.execute("""
            SELECT 1
            FROM daily_cashback_runs
            WHERE target_day=? AND status='done'
        """, (target_day,)).fetchone()

    return row is not None


def begin_daily_run(target_day: str) -> int:
    """يبدأ (أو يستأنف) تنفيذ يوم معيّن، ويرجع رقم المحاولة."""

    with _db() as conn:

        conn.execute("""
            INSERT OR IGNORE INTO daily_cashback_runs (target_day, status, attempts)
            VALUES (?, 'running', 0)
        """, (target_day,))

        conn.execute("""
            UPDATE daily_cashback_runs
            SET attempts = attempts + 1
            WHERE target_day=? AND status='running'
        """, (target_day,))

        row = conn.execute("""
            SELECT attempts
            FROM daily_cashback_runs
            WHERE target_day=?
        """, (target_day,)).fetchone()

    return int(row[0])


def finish_daily_run(target_day: str, summary: dict) -> None:

    with _db() as conn:

        conn.execute("""
            UPDATE daily_cashback_runs
            SET status='done',
                finished_at=CURRENT_TIMESTAMP,
                summary=?
            WHERE target_day=?
        """, (json.dumps(summary, ensure_ascii=False), target_day))


def record_daily_payout_and_credit(
    user_id: int,
    target_day: str,
    loss_amount: int,
    cashback_amount: int,
) -> bool:
    """
    يسجّل التعويض ويضيفه لرصيد اللاعب بعملية واحدة ذرّية.
    UNIQUE(user_id, target_day) يمنع الصرف مرتين لنفس اليوم حتى لو أُعيد التنفيذ
    أو انقطع البوت في منتصفه. ترجع True فقط إذا تم الصرف الآن.
    """

    with _db() as conn:

        cur = conn.execute("""
            INSERT OR IGNORE INTO daily_cashback_payouts
                (user_id, target_day, loss_amount, cashback_amount)
            VALUES (?, ?, ?, ?)
        """, (int(user_id), target_day, int(loss_amount), int(cashback_amount)))

        if cur.rowcount != 1:
            return False

        credit = conn.execute("""
            UPDATE users
            SET balance = balance + ?
            WHERE user_id=?
        """, (int(cashback_amount), int(user_id)))

        if credit.rowcount != 1:
            # لا نترك سجل صرف بدون إضافة فعلية للرصيد → تراجع كامل
            raise RuntimeError(f"المستخدم {user_id} غير موجود لإضافة التعويض")

        return True


def get_daily_payout_totals(target_day: str) -> tuple[int, int]:
    """(عدد المعوَّضين, إجمالي التعويض) ليوم معيّن من السجل الفعلي."""

    with _db() as conn:

        row = conn.execute("""
            SELECT COUNT(*), COALESCE(SUM(cashback_amount), 0)
            FROM daily_cashback_payouts
            WHERE target_day=?
        """, (target_day,)).fetchone()

    return int(row[0]), int(row[1])


def get_recent_daily_payouts(limit: int = 15) -> list[dict]:

    with _db() as conn:

        rows = conn.execute("""
            SELECT user_id, target_day, loss_amount, cashback_amount, created_at
            FROM daily_cashback_payouts
            ORDER BY id DESC
            LIMIT ?
        """, (int(limit),)).fetchall()

    return [
        {
            "user_id": r[0],
            "target_day": r[1],
            "loss_amount": r[2],
            "cashback_amount": r[3],
            "created_at": r[4],
        }
        for r in rows
    ]
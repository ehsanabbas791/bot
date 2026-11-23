# -*- coding: utf-8 -*-
"""
sham_payout.py — دفع طلبات السحب (Sham Cash) تلقائياً عبر الـ API
=================================================================

يربط طلب السحب بآلية «تحويل Sham Cash» الموجودة (sham_transfer) بدون أن يحتاج الأدمن لتطبيق شام كاش.

دورة الحياة (كل خطوة مال تتم داخل معاملة ذرّية، ولا يُعاد التحويل أبداً):

  1) preflight(wid)            فحوص بلا أي أثر (طريقة/عنوان/رصيد/حدود/تفعيل).
  2) begin_payout(...)         معاملة واحدة: الطلب pending→processing + خصم رصيد اللاعب + سجل payout «active».
  3) sham_transfer.confirm_and_execute(...)   الحجز الذرّي + التحويل الفعلي (مرة واحدة).
  4) reconcile_*               يقرأ حالة التحويل الحقيقية ويُنهي الدفعة:
        sent                      → paid     (الطلب approved)
        failed/expired/cancelled  → refunded (يُردّ الرصيد والطلب يعود pending ليُعاد أو يُرفض)
        sending/unknown           → تبقى active (الرصيد محجوز) حتى يُحسم التحويل

مبدأ الأمان: الرصيد يُخصم قبل التحويل ويُردّ فقط عند التأكد أن المال لم يخرج.
أي حالة غير مؤكدة تبقى محجوزة ولا تُرد ولا تُعاد حتى يحسمها التحقق من سجل الصادر أو الأدمن.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal

from database import _db, calc_withdraw_fee
import sham_transfer as st

log = logging.getLogger("sham_payout")

SHAM_METHOD = "Sham Cash"        # قيمة withdraw_requests.method لسحوبات شام كاش
CURRENCY = "SYP"
NOTE_PREFIX = "Withdraw"         # ملاحظة التحويل (تُضاف إليها Ref تلقائياً)
STALE_PENDING_GRACE_SECONDS = 60  # طلب تحويل pending لم يُحجز بعد انتهاء مهلته + هذه المدة = لم يخرج مال
_TS = "%Y-%m-%d %H:%M:%S"

# ============================================================
# الجدول + حالة الطلب الجديدة 'processing'
# ============================================================
with _db() as _conn:
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS withdraw_payouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            withdraw_id INTEGER NOT NULL,
            transfer_id INTEGER NOT NULL,
            admin_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            fee INTEGER NOT NULL,
            net INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'active',
            notified INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # دفعة active واحدة فقط لكل طلب (بعد الاسترداد يجوز إنشاء دفعة جديدة)
    _conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_withdraw_payouts_active
        ON withdraw_payouts(withdraw_id) WHERE state='active'
    """)
    _conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_withdraw_payouts_state
        ON withdraw_payouts(state, notified)
    """)


# ============================================================
# 1) الفحص المسبق (لا يغيّر شيئاً)
# ============================================================
def _fail(reason: str) -> dict:
    return {"ok": False, "reason": reason}


def _effective_fee(conn, row, override=None) -> float:
    """نسبة الحسم لهذا الطلب: صريحة (override) ← لقطة الطلب وقت إنشائه ← نسبة اللاعب ← العامة."""
    if override is not None:
        return float(override)
    if row["fee_percent"] is not None:
        return float(row["fee_percent"])
    user = conn.execute("SELECT withdraw_fee_percent FROM users WHERE user_id=?", (int(row["user_id"]),)).fetchone()
    if user and user[0] is not None:
        return float(user[0])
    g = conn.execute("SELECT value FROM settings WHERE key='withdraw_fee_percent'").fetchone()
    try:
        return float(g[0]) if g and g[0] is not None else 0.10
    except (TypeError, ValueError):
        return 0.10


def preflight(wid: int, fee_percent=None) -> dict:
    with _db() as conn:
        row = conn.execute(
            "SELECT id, user_id, amount, account, status, method, fee_percent FROM withdraw_requests WHERE id=?",
            (int(wid),),
        ).fetchone()
        if row is None:
            return _fail("طلب السحب غير موجود.")
        if row["status"] != "pending":
            return _fail(f"هذا الطلب لم يعد معلّقاً (الحالة: {row['status']}) — ربما عولج من أدمن آخر.")
        if (row["method"] or "") != SHAM_METHOD:
            return _fail("الدفع التلقائي متاح لسحوبات Sham Cash فقط.")

        user = conn.execute("SELECT balance FROM users WHERE user_id=?", (int(row["user_id"]),)).fetchone()
        balance = int(user["balance"] or 0) if user else 0
        fee_percent = _effective_fee(conn, row, fee_percent)

    amount = int(row["amount"])

    if not st.is_enabled():
        return _fail("تحويل Sham Cash متوقف — فعّله من: لوحة الأدمن ← تحويل Sham Cash.")
    if not st.has_pin():
        return _fail("رمز التحويل غير مضبوط.")
    if not st._api_ready():
        return _fail("إعدادات الـ API غير مكتملة.")

    try:
        address = st.validate_address(row["account"])
    except ValueError as exc:
        return _fail(f"حساب اللاعب لا يصلح للتحويل التلقائي: {exc}")

    if balance < amount:
        return _fail(f"رصيد اللاعب الحالي ({balance:,}) أقل من مبلغ السحب ({amount:,}).")

    fee = calc_withdraw_fee(amount, fee_percent)
    net = amount - fee
    if net <= 0:
        return _fail("المبلغ الصافي بعد الحسم غير صالح.")

    try:
        st.check_limits(CURRENCY, Decimal(net))
    except ValueError as exc:
        return _fail(str(exc))

    return {
        "ok": True, "wid": int(wid), "user_id": int(row["user_id"]), "amount": amount,
        "fee": fee, "net": net, "address": address, "balance": balance, "fee_percent": fee_percent,
    }


# ============================================================
# 2) بدء الدفعة (ذرّي): خصم + حجز الطلب + ربطه بطلب التحويل
# ============================================================
def begin_payout(wid: int, admin_id: int, transfer_id: int, fee_percent=None) -> dict:
    """يرجع {"status": "ok", "payout_id": N, ...} أو {"status": "not_pending"|"insufficient"|"no_user"|"busy"}."""
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            "SELECT id, user_id, amount, status, method, fee_percent FROM withdraw_requests WHERE id=?", (int(wid),)
        ).fetchone()
        if row is None or row["status"] != "pending" or (row["method"] or "") != SHAM_METHOD:
            return {"status": "not_pending"}

        amount = int(row["amount"])
        user_id = int(row["user_id"])
        fee = calc_withdraw_fee(amount, _effective_fee(conn, row, fee_percent))
        net = amount - fee

        user = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        if user is None:
            return {"status": "no_user"}
        if int(user["balance"] or 0) < amount:
            return {"status": "insufficient", "balance": int(user["balance"] or 0), "amount": amount}

        try:
            cur = conn.execute(
                """INSERT INTO withdraw_payouts
                   (withdraw_id, transfer_id, admin_id, user_id, amount, fee, net, state)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'active')""",
                (int(wid), int(transfer_id), int(admin_id), user_id, amount, fee, net),
            )
        except Exception as exc:               # فهرس «active واحدة لكل طلب»
            if "UNIQUE" in str(exc).upper():
                return {"status": "busy"}
            raise

        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, user_id))
        conn.execute(
            "UPDATE withdraw_requests SET status='processing' WHERE id=? AND status='pending'", (int(wid),)
        )
        return {
            "status": "ok", "payout_id": cur.lastrowid, "wid": int(wid), "user_id": user_id,
            "amount": amount, "fee": fee, "net": net,
        }


# ============================================================
# 4) إنهاء الدفعات حسب الحالة الحقيقية للتحويل (idempotent)
# ============================================================
def _transfer_row(conn, transfer_id):
    return conn.execute("SELECT * FROM sham_transfers WHERE id=?", (int(transfer_id),)).fetchone()


def reconcile_one(payout_id: int):
    """يُنهي دفعة واحدة إن كان تحويلها قد حُسم. يرجع dict الحدث أو None إن لم يتغير شيء."""
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")

        p = conn.execute(
            "SELECT * FROM withdraw_payouts WHERE id=? AND state='active'", (int(payout_id),)
        ).fetchone()
        if p is None:
            return None

        t = _transfer_row(conn, p["transfer_id"])
        if t is None:
            return None

        status = t["status"]

        # طلب تحويل لم يُحجز أبداً وانتهت مهلته → المال لم يخرج
        if status == "pending":
            created = datetime.strptime(t["created_at"], _TS)
            age = (datetime.now(timezone.utc).replace(tzinfo=None) - created).total_seconds()
            if age > st.REQUEST_TTL_SECONDS + STALE_PENDING_GRACE_SECONDS:
                conn.execute(
                    "UPDATE sham_transfers SET status='expired', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
                    (int(t["id"]),),
                )
                status = "expired"

        if status == "sent":
            conn.execute(
                "UPDATE withdraw_payouts SET state='paid', updated_at=CURRENT_TIMESTAMP WHERE id=? AND state='active'",
                (int(p["id"]),),
            )
            conn.execute(
                """UPDATE withdraw_requests SET status='approved', approved_at=CURRENT_TIMESTAMP
                   WHERE id=? AND status='processing'""",
                (int(p["withdraw_id"]),),
            )
            return {
                "event": "paid", "payout_id": int(p["id"]), "wid": int(p["withdraw_id"]), "user_id": int(p["user_id"]),
                "amount": int(p["amount"]), "fee": int(p["fee"]), "net": int(p["net"]),
                "ref": t["ref"], "recipient": t["recipient_name"] or "", "tran_id": t["tran_id"] or "",
            }

        if status in ("failed", "expired", "cancelled"):
            conn.execute(
                "UPDATE withdraw_payouts SET state='refunded', updated_at=CURRENT_TIMESTAMP WHERE id=? AND state='active'",
                (int(p["id"]),),
            )
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (int(p["amount"]), int(p["user_id"])))
            conn.execute(
                "UPDATE withdraw_requests SET status='pending' WHERE id=? AND status='processing'",
                (int(p["withdraw_id"]),),
            )
            return {
                "event": "refunded", "payout_id": int(p["id"]), "wid": int(p["withdraw_id"]), "user_id": int(p["user_id"]),
                "amount": int(p["amount"]), "fee": int(p["fee"]), "net": int(p["net"]),
                "ref": t["ref"], "reason": t["error"] or ("انتهت مهلة التحويل" if status == "expired" else "لم يتم التحويل"),
            }

        return None   # sending / unknown (أو pending ضمن مهلته): تبقى محجوزة


def reconcile_all() -> list:
    """يمرّ على كل الدفعات active وينهي ما حُسم. يرجع قائمة الأحداث."""
    with _db() as conn:
        ids = [int(r[0]) for r in conn.execute("SELECT id FROM withdraw_payouts WHERE state='active'").fetchall()]
    events = []
    for pid in ids:
        ev = reconcile_one(pid)
        if ev:
            events.append(ev)
    return events


def auto_verify_unknown() -> int:
    """يتحقق من سجل الصادر لكل دفعة تحويلها unknown (قد يظهر أنه تم فعلاً). يرجع عدد من وُجد."""
    with _db() as conn:
        tids = [int(r[0]) for r in conn.execute(
            """SELECT p.transfer_id FROM withdraw_payouts p
               JOIN sham_transfers t ON t.id = p.transfer_id
               WHERE p.state='active' AND t.status='unknown'"""
        ).fetchall()]
    found = 0
    for tid in tids:
        try:
            if st.verify_unknown(tid).get("status") == "found":
                found += 1
        except Exception:
            log.exception("auto verify failed for transfer %s", tid)
    return found


# ============================================================
# الإشعارات (مرة واحدة لكل دفعة منتهية)
# ============================================================
def pending_notifications() -> list:
    with _db() as conn:
        rows = conn.execute(
            """SELECT p.id, p.withdraw_id, p.user_id, p.amount, p.fee, p.net, p.state, p.admin_id,
                      t.ref, t.recipient_name, t.error
               FROM withdraw_payouts p JOIN sham_transfers t ON t.id = p.transfer_id
               WHERE p.state IN ('paid', 'refunded') AND p.notified=0
               ORDER BY p.id"""
        ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def mark_notified(payout_id: int) -> None:
    with _db() as conn:
        conn.execute("UPDATE withdraw_payouts SET notified=1 WHERE id=?", (int(payout_id),))


# ============================================================
# التنفيذ الكامل (يُستدعى بعد التحقق من الـ PIN، عبر asyncio.to_thread)
# ============================================================
def execute_withdraw_payout(wid: int, admin_id: int, fee_percent=None) -> dict:
    """
    يرجع {"status": ...}:
      blocked  → لم يحدث شيء (reason)
      paid     → تم التحويل وانتهت الدفعة
      refunded → لم يخرج مال وتم ردّ الرصيد (reason)
      unknown  → غير مؤكد: الرصيد محجوز، لا تعد المحاولة
    """
    pf = preflight(wid, fee_percent)
    if not pf["ok"]:
        return {"status": "blocked", "reason": pf["reason"]}

    try:
        req = st.create_request(admin_id, pf["address"], CURRENCY, Decimal(pf["net"]), f"{NOTE_PREFIX} #{wid}")
    except Exception as exc:
        return {"status": "blocked", "reason": f"تعذّر إنشاء طلب التحويل: {exc}"}

    began = begin_payout(wid, admin_id, req["id"], pf["fee_percent"])
    if began["status"] != "ok":
        st.cancel_request(req["id"], admin_id)
        reasons = {
            "not_pending": "الطلب عولج للتو من أدمن آخر.",
            "insufficient": f"رصيد اللاعب غير كافٍ ({began.get('balance', 0):,}).",
            "no_user": "اللاعب غير موجود.",
            "busy": "في دفعة جارية لهذا الطلب.",
        }
        return {"status": "blocked", "reason": reasons.get(began["status"], began["status"])}

    out = st.confirm_and_execute(req["id"], admin_id)

    # لم يُحجز/يُنفَّذ أصلاً (متوقف/حد/انتهت المعاينة...) → نلغي الطلب ليُردّ الرصيد
    if out["status"] in ("disabled", "limit", "expired", "missing"):
        st.cancel_request(req["id"], admin_id)
        extra = out.get("message") or ""
        with _db() as conn:
            conn.execute("UPDATE sham_transfers SET error=? WHERE id=? AND error IS NULL", (extra or out["status"], req["id"]))

    event = reconcile_one(began["payout_id"])

    base = {"wid": int(wid), "user_id": pf["user_id"], "amount": pf["amount"], "fee": pf["fee"], "net": pf["net"],
            "transfer_id": req["id"], "ref": req["ref"]}

    if event and event["event"] == "paid":
        return {"status": "paid", **base}
    if event and event["event"] == "refunded":
        return {"status": "refunded", "reason": event["reason"], **base}
    return {"status": "unknown", **base}
# -*- coding: utf-8 -*-
"""
daily_report.py — التقرير اليومي للأدمن («قناة التقارير»)
=========================================================

يولّد تقريراً HTML مفصّلاً (بطاقات + رسوم + جداول + إشارات مخاطر + مطابقة Sham Cash)
ويرسله كل يوم الساعة 01:00 بتوقيت دمشق إلى قناة التقارير (أو للأدمن إن لم تُضبط القناة).

نافذة التقرير: من 01:00 إلى 01:00 بتوقيت دمشق (UTC+3 ثابت).
كل الأوقات في قاعدة البيانات UTC (CURRENT_TIMESTAMP) وتُحوَّل هنا.

الاستخدام من البوت:
    daily_report.daily_report_loop(application, primary_targets, fallback_targets)
    daily_report.send_report(bot, targets, day)

معاينة محلية بدون تيليغرام:
    python daily_report.py 2026-10-06
"""

import asyncio
import html
import inspect
import logging
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

from database import _db, get_bonus_percent, _normalize_txid

log = logging.getLogger("daily_report")

# ============================================================
# الإعدادات
# ============================================================
SYRIA_UTC_OFFSET_HOURS = 3                 # سوريا UTC+3 ثابت (بدون توقيت صيفي)
SYRIA_TZ = timezone(timedelta(hours=SYRIA_UTC_OFFSET_HOURS))
REPORT_START_HOUR = 1                      # بداية النافذة ووقت الإرسال (دمشق)
WITHDRAW_FEE_PERCENT = 0.10                # نفس حسم السحب في البوت (للمطابقة)
RISK_WITHDRAW_RATIO = 0.8                  # أودع ثم سحب ≥ 80% من إيداعه بنفس النافذة = إشارة خطر
PENDING_WITHDRAW_ALERT_HOURS = 6           # تنبيه إذا في طلب سحب معلّق أقدم من هالمدة
TOP_DEPOSITORS = 10
AVG_DAYS = 8                               # «متوسط 8 يوم»
CHART_DAYS = 7
LOOP_POLL_SECONDS = 60
RETRY_AFTER_FAILURE_SECONDS = 300
SETTINGS_KEY_LAST_DAY = "daily_report_last_day"
CAPTION_LIMIT = 1024                       # حد تيليغرام لوصف الملف

_TS_FMT = "%Y-%m-%d %H:%M:%S"
_UTC_OFFSET = timedelta(hours=SYRIA_UTC_OFFSET_HOURS)

METHOD_ORDER = ["سيرياتيل كاش", "شام كاش", "USDT", "أكواد الهدية"]
_BONUS_KEY_BY_GROUP = {"سيرياتيل كاش": "syriatel", "شام كاش": "shamcash", "USDT": "usdt"}

# ============================================================
# مزوّد سجل Sham Cash (اختياري) — لمطابقة وارد/صادر الحساب
# ============================================================
# دالة (sync أو async) تأخذ (start_utc, end_utc) وترجع قائمة قواميس:
#   {"id": "...", "direction": "in"|"out", "amount": 12345,
#    "name": "...", "account": "...", "time": datetime|str}
# إذا لم تُسجَّل، يظهر قسم المطابقة في التقرير كـ «غير مفعّل».
_sham_history_provider = None


def set_sham_history_provider(fn):
    global _sham_history_provider
    _sham_history_provider = fn


# ============================================================
# أدوات عامة
# ============================================================
def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ts(dt: datetime) -> str:
    return dt.strftime(_TS_FMT)


def _parse_ts(value):
    if isinstance(value, datetime):
        return value
    try:
        return datetime.strptime(str(value)[:19], _TS_FMT)
    except Exception:
        return None


def _fmt(n) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except Exception:
        return "0"


def _pct_change(cur, prev):
    if not prev:
        return None
    return (cur - prev) / prev * 100.0


def _badge(cur, prev, invert=False) -> str:
    """شارة التغيير مقارنة بأمس. invert=True: الارتفاع سيّئ (مثل السحب) فيظهر أحمر."""
    pct = _pct_change(cur, prev)
    if pct is None:
        return ""
    if abs(pct) < 0.05:
        return '<span class="badge flat">■ 0.0%</span>'
    arrow = "▲" if pct > 0 else "▼"
    good = (pct > 0) != invert
    cls = "up" if good else "down"
    return f'<span class="badge {cls}">{arrow} {abs(pct):.1f}%</span>'


def day_window(day: date, end_cap_utc: datetime = None):
    """نافذة اليوم كاملة بتوقيت UTC: [start, end). end_cap يقصّها (للتقرير الجزئي)."""
    start_local = datetime(day.year, day.month, day.day, REPORT_START_HOUR)
    start = start_local - _UTC_OFFSET
    end = start + timedelta(days=1)
    if end_cap_utc is not None and end_cap_utc < end:
        end = end_cap_utc
    return start, end


def method_group(method) -> str:
    m = (method or "").strip()
    low = m.lower()
    if low.startswith("sham"):
        return "شام كاش"
    if low.startswith("syriatel"):
        return "سيرياتيل كاش"
    if low.startswith("usdt"):
        return "USDT"
    return m or "غير محدد"


def _withdraw_group(method, account) -> str:
    if (method or "").strip():
        return method_group(method)
    if str(account or "").strip().startswith("0x"):
        return "USDT"
    return "غير محدد"


def _order_key(name):
    return (METHOD_ORDER.index(name) if name in METHOD_ORDER else len(METHOD_ORDER), name)


# ============================================================
# الاستعلامات
# ============================================================
_AT = "COALESCE(r.approved_at, r.created_at)"


def _q_recharges(conn, s, e):
    rows = conn.execute(
        f"""
        SELECT r.id, r.user_id,
               COALESCE(l.amount, r.amount) AS credited,
               r.method, r.bonus, {_AT} AS at,
               u.ichancy_username
        FROM recharge_requests r
        LEFT JOIN recharge_transaction_ledger l ON l.recharge_id = r.id
        LEFT JOIN users u ON u.user_id = r.user_id
        WHERE r.status = 'approved' AND {_AT} >= ? AND {_AT} < ?
        """,
        (_ts(s), _ts(e)),
    ).fetchall()
    return [
        {"id": r[0], "user_id": int(r[1]), "credited": int(r[2] or 0), "method": r[3],
         "bonus": r[4], "at": r[5], "ichancy": r[6]}
        for r in rows
    ]


def _q_gifts(conn, s, e):
    rows = conn.execute(
        "SELECT user_id, amount, used_at FROM gift_code_uses WHERE used_at >= ? AND used_at < ?",
        (_ts(s), _ts(e)),
    ).fetchall()
    return [{"user_id": int(r[0]), "amount": int(r[1] or 0), "at": r[2]} for r in rows]


def _q_withdraws(conn, s, e):
    at = "COALESCE(approved_at, created_at)"
    rows = conn.execute(
        f"""
        SELECT user_id, amount, method, account, {at}
        FROM withdraw_requests
        WHERE status = 'approved' AND {at} >= ? AND {at} < ?
        """,
        (_ts(s), _ts(e)),
    ).fetchall()
    return [
        {"user_id": int(r[0]), "amount": int(r[1] or 0), "group": _withdraw_group(r[2], r[3]), "at": r[4]}
        for r in rows
    ]


def _q_scalar(conn, sql, params=()):
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row and row[0] is not None else 0
    except Exception as exc:  # جدول/عمود غير موجود في نسخة قديمة — لا نكسر التقرير
        log.warning("report query skipped (%s): %s", exc.__class__.__name__, exc)
        return 0


def _window_totals(conn, s, e):
    """إجمالي الشحن/السحب وعدد المودعين لنافذة (للمقارنة والرسم)."""
    a, b = _ts(s), _ts(e)
    charge = _q_scalar(
        conn,
        f"""SELECT COALESCE(SUM(COALESCE(l.amount, r.amount)), 0)
            FROM recharge_requests r
            LEFT JOIN recharge_transaction_ledger l ON l.recharge_id = r.id
            WHERE r.status='approved' AND {_AT} >= ? AND {_AT} < ?""",
        (a, b),
    )
    gifts = _q_scalar(conn, "SELECT COALESCE(SUM(amount),0) FROM gift_code_uses WHERE used_at >= ? AND used_at < ?", (a, b))
    wd_at = "COALESCE(approved_at, created_at)"
    withdraw = _q_scalar(
        conn,
        f"SELECT COALESCE(SUM(amount),0) FROM withdraw_requests WHERE status='approved' AND {wd_at} >= ? AND {wd_at} < ?",
        (a, b),
    )
    depositors = _q_scalar(
        conn,
        f"SELECT COUNT(DISTINCT r.user_id) FROM recharge_requests r WHERE r.status='approved' AND {_AT} >= ? AND {_AT} < ?",
        (a, b),
    )
    return {"charge": int(charge) + int(gifts), "withdraw": int(withdraw), "depositors": int(depositors)}


def _estimate_bonus(group, credited) -> int:
    key = _BONUS_KEY_BY_GROUP.get(group)
    if not key:
        return 0
    try:
        return int(int(credited) * float(get_bonus_percent(key)))
    except Exception:
        return 0


# ============================================================
# تجميع بيانات التقرير
# ============================================================
def collect_report_data(day: date, end_cap_utc: datetime = None) -> dict:
    s, e = day_window(day, end_cap_utc)
    partial = end_cap_utc is not None and end_cap_utc < day_window(day)[1]
    a, b = _ts(s), _ts(e)

    with _db() as conn:
        # ---------- الشحن ----------
        recharges = _q_recharges(conn, s, e)
        gifts = _q_gifts(conn, s, e)
        withdraws = _q_withdraws(conn, s, e)

        by_method = {}
        for r in recharges:
            g = method_group(r["method"])
            d = by_method.setdefault(g, {"count": 0, "amount": 0})
            d["count"] += 1
            d["amount"] += r["credited"]
        if gifts:
            by_method["أكواد الهدية"] = {"count": len(gifts), "amount": sum(g["amount"] for g in gifts)}

        wd_by_method = {}
        for w in withdraws:
            d = wd_by_method.setdefault(w["group"], {"count": 0, "amount": 0})
            d["count"] += 1
            d["amount"] += w["amount"]

        total_charge = sum(v["amount"] for v in by_method.values())
        total_withdraw = sum(w["amount"] for w in withdraws)

        # ---------- iChancy ----------
        ich_in = _q_scalar(conn, "SELECT COALESCE(SUM(amount_nsp),0) FROM ichancy_transactions WHERE direction='in' AND created_at >= ? AND created_at < ?", (a, b))
        ich_out = _q_scalar(conn, "SELECT COALESCE(SUM(amount_nsp),0) FROM ichancy_transactions WHERE direction='out' AND created_at >= ? AND created_at < ?", (a, b))

        # ---------- المستخدمون ----------
        dep_users = {r["user_id"] for r in recharges}
        wd_users = {w["user_id"] for w in withdraws}

        new_depositors = {
            int(x[0]) for x in conn.execute(
                f"""SELECT r.user_id FROM recharge_requests r WHERE r.status='approved'
                    GROUP BY r.user_id
                    HAVING MIN({_AT}) >= ? AND MIN({_AT}) < ?""",
                (a, b),
            ).fetchall()
        }
        new_users = {
            int(x[0]) for x in conn.execute(
                "SELECT user_id FROM users WHERE created_at >= ? AND created_at < ?", (a, b)
            ).fetchall()
        }
        converted = len(new_users & dep_users)
        balances_total = int(_q_scalar(conn, "SELECT COALESCE(SUM(balance),0) FROM users"))

        # ---------- أعلى المودعين ----------
        per_user = {}
        for r in recharges:
            d = per_user.setdefault(r["user_id"], {"amount": 0, "ops": 0, "ichancy": r["ichancy"]})
            d["amount"] += r["credited"]
            d["ops"] += 1
        top = sorted(per_user.items(), key=lambda kv: kv[1]["amount"], reverse=True)[:TOP_DEPOSITORS]
        top = [{"user_id": uid, **d} for uid, d in top]

        # ---------- ساعات الشحن (بتوقيت دمشق) ----------
        hourly = [0] * 24
        for r in recharges:
            t = _parse_ts(r["at"])
            if t:
                hourly[(t + _UTC_OFFSET).hour] += 1

        # ---------- تكلفة البونصات ----------
        est_used = False
        deposit_bonus = 0
        for r in recharges:
            if r["bonus"] is None:
                est_used = True
                deposit_bonus += _estimate_bonus(method_group(r["method"]), r["credited"])
            else:
                deposit_bonus += int(r["bonus"])

        weekly_cb = int(_q_scalar(conn, "SELECT COALESCE(SUM(cashback_amount),0) FROM cashback_payouts WHERE created_at >= ? AND created_at < ?", (a, b)))
        daily_cb = 0
        try:
            cols = {c[1] for c in conn.execute("PRAGMA table_info(daily_cashback_payouts)")}
            if "cashback_amount" in cols:
                daily_cb = int(_q_scalar(conn, "SELECT COALESCE(SUM(cashback_amount),0) FROM daily_cashback_payouts WHERE created_at >= ? AND created_at < ?", (a, b)))
        except Exception as exc:
            log.warning("daily cashback table skipped: %s", exc)

        welcome = int(_q_scalar(conn, "SELECT COALESCE(SUM(amount),0) FROM bonus_events WHERE kind='welcome' AND created_at >= ? AND created_at < ?", (a, b)))
        ref_reward = int(_q_scalar(conn, "SELECT COALESCE(SUM(reward),0) FROM referrals WHERE rewarded=1 AND activated_at >= ? AND activated_at < ?", (a, b)))
        ref_commission = int(_q_scalar(conn, "SELECT COALESCE(SUM(commission_amount),0) FROM referral_commissions WHERE paid=1 AND paid_at >= ? AND paid_at < ?", (a, b)))
        # نظام الإحالات حسب الحرق (هرمي/إفرادي): الدورات المصروفة خلال النافذة
        ref_commission += int(_q_scalar(
            conn,
            """SELECT COALESCE(SUM(b.amount),0) FROM ref2_beneficiaries b JOIN ref2_cycles c ON c.id = b.cycle_id
               WHERE c.status='paid' AND b.eligible=1 AND c.paid_at >= ? AND c.paid_at < ?""",
            (a, b),
        ))
        spin = int(_q_scalar(conn, "SELECT COALESCE(SUM(amount),0) FROM bonus_events WHERE kind='spin' AND created_at >= ? AND created_at < ?", (a, b)))
        gift_cost = sum(g["amount"] for g in gifts)

        bonus_rows = [
            ("بونص الإيداع", deposit_bonus),
            ("الكاش باك", weekly_cb + daily_cb),
            ("مكافآت الانضمام", welcome + ref_reward),
            ("بونص الإحالة الدوري", ref_commission),
            ("عجلة الحظ", spin),
            ("أكواد الهدية", gift_cost),
        ]
        bonus_total = sum(v for _, v in bonus_rows)

        # ---------- إشارات المخاطر ----------
        dep_amount = {uid: d["amount"] for uid, d in per_user.items()}
        wd_amount = {}
        for w in withdraws:
            wd_amount[w["user_id"]] = wd_amount.get(w["user_id"], 0) + w["amount"]
        risky = []
        for uid, dep in dep_amount.items():
            wd = wd_amount.get(uid, 0)
            if dep > 0 and wd > 0 and wd >= dep * RISK_WITHDRAW_RATIO:
                risky.append({"user_id": uid, "deposit": dep, "withdraw": wd, "ratio": wd / dep * 100})
        risky.sort(key=lambda x: x["ratio"], reverse=True)

        pending_old = conn.execute(
            """SELECT COUNT(*), MIN(created_at) FROM withdraw_requests
               WHERE status='pending' AND created_at <= ?""",
            (_ts(_utcnow() - timedelta(hours=PENDING_WITHDRAW_ALERT_HOURS)),),
        ).fetchone()
        pending_count = int(pending_old[0] or 0)
        pending_oldest = _parse_ts(pending_old[1]) if pending_old[1] else None

        # ---------- مقارنات: أمس + متوسط 8 أيام + رسم 7 أيام ----------
        prev_s, prev_e = day_window(day - timedelta(days=1))
        prev = _window_totals(conn, prev_s, prev_e)

        first_ts = conn.execute(
            f"SELECT MIN({_AT}) FROM recharge_requests r WHERE r.status='approved'"
        ).fetchone()[0]
        first_dt = _parse_ts(first_ts) if first_ts else None
        avg_charge = avg_withdraw = None
        if first_dt:
            vals = []
            for i in range(1, AVG_DAYS + 1):
                ws, we = day_window(day - timedelta(days=i))
                if we > first_dt:
                    vals.append(_window_totals(conn, ws, we))
            if vals:
                avg_charge = sum(v["charge"] for v in vals) / len(vals)
                avg_withdraw = sum(v["withdraw"] for v in vals) / len(vals)

        series = []
        for i in range(CHART_DAYS - 1, -1, -1):
            d = day - timedelta(days=i)
            if i == 0:
                series.append((d.strftime("%m-%d"), total_charge, total_withdraw))
            else:
                ws, we = day_window(d)
                t = _window_totals(conn, ws, we)
                series.append((d.strftime("%m-%d"), t["charge"], t["withdraw"]))

        # ---------- مدخلات مطابقة Sham Cash ----------
        known_ids = {
            x[0] for x in conn.execute(
                "SELECT transaction_id_normalized FROM recharge_transaction_ledger WHERE created_at >= ?",
                (_ts(s - timedelta(days=7)),),
            ).fetchall()
        }
        sham_wd_amounts = [w["amount"] for w in withdraws if w["group"] == "شام كاش"]

    return {
        "day": day, "start_utc": s, "end_utc": e, "partial": partial,
        "charge": total_charge, "withdraw": total_withdraw,
        "prev": prev, "avg_charge": avg_charge, "avg_withdraw": avg_withdraw,
        "ratio": (total_charge / total_withdraw) if total_withdraw else None,
        "ich_in": float(ich_in), "ich_out": float(ich_out), "net_burn": float(ich_in) - float(ich_out),
        "depositors": len(dep_users), "new_depositors": len(new_depositors & dep_users),
        "withdrawers": len(wd_users),
        "new_users": len(new_users), "converted": converted,
        "balances_total": balances_total,
        "by_method": by_method, "wd_by_method": wd_by_method,
        "top": top, "hourly": hourly, "series": series,
        "bonus_rows": bonus_rows, "bonus_total": bonus_total, "bonus_estimated": est_used,
        "risky": risky, "pending_count": pending_count, "pending_oldest": pending_oldest,
        "sham_inputs": {"known_ids": known_ids, "wd_amounts": sham_wd_amounts},
    }


# ============================================================
# مطابقة Sham Cash
# ============================================================
def reconcile_sham(rows, known_ids, wd_amounts, admin_out_amounts=()):
    """
    rows: سجل حساب Sham Cash عند المزوّد.
    - وارد لم يُشحن لأي مستخدم: رقم عمليته غير موجود في سجل الشحن الدائم.
    - صادر بلا طلب سحب: لا يقابله طلب سحب معتمد (بالمبلغ الكامل أو بعد حسم 10%)
      ولا تحويل أدمن منفَّذ.
    """
    incoming = [r for r in rows if r.get("direction") == "in"]
    outgoing = [r for r in rows if r.get("direction") == "out"]

    unmatched_in = [r for r in incoming if _normalize_txid(str(r.get("id", ""))) not in known_ids]

    pool = Counter()
    for amt in wd_amounts:
        pool[("wd", int(amt))] += 1
    for amt in admin_out_amounts:
        pool[("admin", int(amt))] += 1

    def consume(amount):
        amount = int(round(float(amount)))
        for key in ("wd", "admin"):
            net = amount  # المبلغ الصافي بعد الحسم
            if pool[(key, net)] > 0:
                pool[(key, net)] -= 1
                return True
        # طلب سحب بمبلغ كامل A صرف منه صافي A*(1-fee)
        for (kind, amt), cnt in list(pool.items()):
            if cnt > 0 and kind == "wd" and int(amt - int(amt * WITHDRAW_FEE_PERCENT)) == amount:
                pool[(kind, amt)] -= 1
                return True
        return False

    unmatched_out = [r for r in outgoing if not consume(r.get("amount", 0))]

    def total(lst):
        return sum(float(r.get("amount", 0) or 0) for r in lst)

    return {
        "in_count": len(incoming), "in_total": total(incoming),
        "out_count": len(outgoing), "out_total": total(outgoing),
        "unmatched_in": unmatched_in, "unmatched_out": unmatched_out,
    }


def _admin_out_amounts(start_utc, end_utc):
    """تحويلات الأدمن المنفَّذة من البوت نفسه (sham_transfer) — لاستثنائها من «صادر بلا طلب»."""
    try:
        import sham_transfer
        rows = sham_transfer.recent_transfers(200)
        ok_status = {"success", "completed", "executed", "done", "confirmed", "unknown"}
        out = []
        for r in rows:
            if str(r.get("status", "")).lower() in ok_status:
                out.append(int(float(r.get("amount", 0))))
        return out
    except Exception:
        return []


async def _sham_section(data):
    provider = _sham_history_provider
    if provider is None:
        return None
    try:
        res = provider(data["start_utc"], data["end_utc"])
        if inspect.isawaitable(res):
            res = await res
        rows = list(res or [])
    except Exception as exc:
        log.warning("sham history provider failed: %s", exc)
        return {"error": str(exc)}
    inp = data["sham_inputs"]
    return reconcile_sham(rows, inp["known_ids"], inp["wd_amounts"], _admin_out_amounts(data["start_utc"], data["end_utc"]))


# ============================================================
# أسماء المستخدمين (تيليغرام)
# ============================================================
async def _resolve_names(bot, ids):
    names = {}
    for uid in ids:
        name = None
        if bot is not None:
            try:
                chat = await asyncio.wait_for(bot.get_chat(uid), timeout=8)
                name = getattr(chat, "full_name", None) or getattr(chat, "title", None)
                if not name and getattr(chat, "username", None):
                    name = f"@{chat.username}"
            except Exception:
                name = None
        names[uid] = name
    return names


# ============================================================
# الرسوم (SVG)
# ============================================================
def _svg_hourly(counts) -> str:
    mx = max(counts) or 1
    out = ['<svg viewBox="0 0 760 200" class="chart" preserveAspectRatio="xMidYMid meet">']
    for h, c in enumerate(counts):
        x = 17.8 + h * 30.7
        hh = c / mx * 130.0
        y = 174.0 - hh
        out.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="19.0" height="{hh:.1f}" rx="3" fill="#3b82f6">'
            f"<title>{h:02d}: {c} عملية</title></rect>"
        )
        if c:
            out.append(f'<text x="{x + 9.5:.1f}" y="{y - 4:.1f}" text-anchor="middle" class="axis">{c}</text>')
        out.append(f'<text x="{x + 9.5:.1f}" y="192" text-anchor="middle" class="axis">{h:02d}</text>')
    out.append("</svg>")
    return "".join(out)


def _svg_series(series) -> str:
    mx = max([max(c, w) for _, c, w in series] + [1])
    n = len(series)
    step = 760.0 / n
    bw = 27.6
    pair = bw * 2 + 4
    out = ['<svg viewBox="0 0 760 230" class="chart" preserveAspectRatio="xMidYMid meet">']
    for i, (label, charge, withdraw) in enumerate(series):
        x0 = i * step + (step - pair) / 2
        hc = charge / mx * 182.0
        hw = withdraw / mx * 182.0
        out.append(
            f'<rect x="{x0:.1f}" y="{200 - hc:.1f}" width="{bw}" height="{hc:.1f}" rx="3" fill="#22c55e">'
            f"<title>{label} — شحن: {_fmt(charge)}</title></rect>"
        )
        out.append(
            f'<rect x="{x0 + bw + 4:.1f}" y="{200 - hw:.1f}" width="{bw}" height="{hw:.1f}" rx="3" fill="#ef4444">'
            f"<title>{label} — سحب: {_fmt(withdraw)}</title></rect>"
        )
        out.append(f'<text x="{x0 + pair / 2:.1f}" y="220" text-anchor="middle" class="axis">{label}</text>')
    out.append("</svg>")
    return "".join(out)


# ============================================================
# بناء HTML
# ============================================================
_CSS = """*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',Tahoma,'Noto Naskh Arabic',Arial,sans-serif;background:#0f172a;color:#e2e8f0;padding:24px;line-height:1.6}
.wrap{max-width:820px;margin:0 auto}
h1{font-size:22px;margin-bottom:4px}
h2{font-size:17px;margin:26px 0 10px;padding-bottom:6px;border-bottom:1px solid #1e293b;color:#93c5fd}
.sub{color:#94a3b8;font-size:13px;margin-bottom:18px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.card{background:#1e293b;border-radius:10px;padding:14px 16px;border:1px solid #334155}
.card .k{color:#94a3b8;font-size:12px;margin-bottom:6px}
.card .v{font-size:20px;font-weight:700;color:#f1f5f9}
.card .c{font-size:11px;color:#64748b;margin-top:6px}
.badge{display:inline-block;padding:1px 7px;border-radius:20px;font-size:11px;font-weight:700;margin-inline-start:6px}
.badge.up{background:#052e16;color:#4ade80}
.badge.down{background:#450a0a;color:#f87171}
.badge.flat{background:#1e293b;color:#94a3b8}
table{width:100%;border-collapse:collapse;background:#1e293b;border-radius:10px;overflow:hidden;font-size:13px}
th,td{padding:9px 12px;text-align:right;border-bottom:1px solid #334155}
th{background:#273449;color:#93c5fd;font-weight:600;font-size:12px}
tr:last-child td{border-bottom:none}
tr:hover td{background:#243044}
.pos{color:#4ade80;font-weight:700}
.neg{color:#f87171;font-weight:700}
.chart{width:100%;height:auto;background:#1e293b;border-radius:10px;padding:8px;border:1px solid #334155}
.axis{fill:#64748b;font-size:10px}
.legend{display:flex;gap:16px;margin-bottom:8px;font-size:12px;color:#94a3b8}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-inline-end:5px}
.empty{background:#1e293b;border:1px dashed #334155;border-radius:10px;padding:26px;text-align:center;color:#64748b;font-size:13px}
.alert{background:#422006;border:1px solid #854d0e;color:#fbbf24;border-radius:8px;padding:10px 14px;margin-top:10px;font-size:13px}
.foot{margin-top:30px;text-align:center;color:#475569;font-size:11px}
@media print{body{background:#fff;color:#000}.card,table,.chart{background:#f8fafc;border-color:#cbd5e1}}
"""

_MEDALS = ["🥇", "🥈", "🥉"]


def _card(k, v, c="", badge=""):
    return (
        f'<div class="card"><div class="k">{html.escape(k)}</div>'
        f'<div class="v">{v}{badge}</div><div class="c">{c}</div></div>'
    )


def _method_table(rows_dict) -> str:
    if not rows_dict:
        return '<div class="empty">لا توجد عمليات في هذه الفترة</div>'
    out = ["<table><tr><th>الطريقة</th><th>العدد</th><th>المبلغ</th></tr>"]
    for name in sorted(rows_dict, key=_order_key):
        d = rows_dict[name]
        out.append(f"<tr><td>{html.escape(name)}</td><td>{d['count']}</td><td>{_fmt(d['amount'])}</td></tr>")
    out.append("</table>")
    return "".join(out)


def _sham_html(sham) -> str:
    if sham is None:
        return (
            '<div class="empty">المطابقة غير مفعّلة بعد — تحتاج ربط دالة جلب سجل تحويلات حساب Sham Cash '
            "(<code>daily_report.set_sham_history_provider</code>).</div>"
        )
    if "error" in sham:
        return f'<div class="alert">⚠️ تعذّر جلب سجل Sham Cash: {html.escape(sham["error"])}</div>'

    out = []
    uo, ui = sham["unmatched_out"], sham["unmatched_in"]
    if uo:
        out.append(
            f'<div class="alert">🚨 يوجد {len(uo)} تحويل صادر بمجموع {_fmt(sum(float(r.get("amount", 0)) for r in uo))} '
            "لا يقابله طلب سحب — راجع فوراً.</div>"
        )
    if ui:
        out.append(
            f'<div class="alert">⚠️ يوجد {len(ui)} تحويل وارد بمجموع {_fmt(sum(float(r.get("amount", 0)) for r in ui))} '
            "لم يُشحن لأي مستخدم.</div>"
        )
    out.append(
        '<div class="cards" style="margin-bottom:12px">'
        + _card("وارد (عدد / مبلغ)", f'{sham["in_count"]} / {_fmt(sham["in_total"])}')
        + _card("صادر (عدد / مبلغ)", f'{sham["out_count"]} / {_fmt(sham["out_total"])}')
        + "</div>"
    )

    def tx_table(title, color, rows):
        head = f'<h3 style="font-size:14px;color:{color};margin:14px 0 6px">{title}</h3>'
        t = ["<table><tr><th>رقم العملية</th><th>الاسم</th><th>المبلغ</th><th>الحساب</th><th>الوقت</th></tr>"]
        if not rows:
            t.append('<tr><td colspan="5" style="text-align:center;color:#4ade80">لا شيء ✅</td></tr>')
        for r in rows:
            tm = _parse_ts(r.get("time"))
            tm_s = ((tm + _UTC_OFFSET).strftime("%m-%d %H:%M:%S")) if tm else html.escape(str(r.get("time", "")))
            t.append(
                f'<tr><td><code>{html.escape(str(r.get("id", "")))}</code></td>'
                f'<td>{html.escape(str(r.get("name", "") or ""))}</td><td>{_fmt(r.get("amount", 0))}</td>'
                f'<td><code>{html.escape(str(r.get("account", "") or ""))}</code></td><td>{tm_s}</td></tr>'
            )
        t.append("</table>")
        return head + "".join(t)

    out.append(tx_table("صادر بلا طلب سحب", "#f87171", uo))
    out.append(tx_table("وارد لم يُشحن لأي مستخدم", "#fbbf24", ui))
    return "".join(out)


def render_html(data: dict, names: dict, sham) -> str:
    day = data["day"]
    s_local = data["start_utc"] + _UTC_OFFSET
    e_local = data["end_utc"] + _UTC_OFFSET
    prev, avg_c, avg_w = data["prev"], data["avg_charge"], data["avg_withdraw"]

    def avg_txt(prev_v, avg_v):
        a = f"أمس: {_fmt(prev_v)}"
        if avg_v is not None:
            a += f" · متوسط {AVG_DAYS} يوم: {_fmt(avg_v)}"
        return a

    conv = (data["converted"] / data["new_users"] * 100) if data["new_users"] else 0.0
    ratio_txt = f"{data['ratio']:.2f}" if data["ratio"] is not None else "—"

    cards = "".join([
        _card("إجمالي الشحن", _fmt(data["charge"]), avg_txt(prev["charge"], avg_c), _badge(data["charge"], prev["charge"])),
        _card("إجمالي السحب", _fmt(data["withdraw"]), avg_txt(prev["withdraw"], avg_w), _badge(data["withdraw"], prev["withdraw"], invert=True)),
        _card("نسبة الشحن/السحب", ratio_txt, "أكبر من ١ يعني دخل أكثر من خرج"),
        _card("صافي الحرق", _fmt(data["net_burn"]), "شحن آيشانسي ناقص السحب منه — الربح الفعلي"),
        _card("آيشانسي: شحن / سحب", f"{_fmt(data['ich_in'])} / {_fmt(data['ich_out'])}", "إجمالي ما دخل وخرج من حسابات اللاعبين"),
        _card("عدد المودعين", str(data["depositors"]), f"منهم جدد: {data['new_depositors']}", _badge(data["depositors"], prev["depositors"])),
        _card("عدد الساحبين", str(data["withdrawers"])),
        _card("مستخدمون جدد", str(data["new_users"]), f"نسبة التحويل للإيداع: {conv:.1f}%"),
        _card("أرصدة اللاعبين", _fmt(data["balances_total"]), "الرصيد الحالي في البوت"),
    ])

    # أعلى المودعين
    top_rows = []
    for i, t in enumerate(data["top"]):
        medal = _MEDALS[i] if i < 3 else "▫️"
        nm = names.get(t["user_id"]) or t.get("ichancy") or "—"
        top_rows.append(
            f"<tr><td>{medal}</td><td>{html.escape(str(nm))}</td><td>{_fmt(t['amount'])}</td>"
            f"<td>{t['ops']}</td><td><code>{t['user_id']}</code></td></tr>"
        )
    top_html = (
        "<table><tr><th></th><th>الاسم</th><th>المبلغ</th><th>العمليات</th><th>الآيدي</th></tr>" + "".join(top_rows) + "</table>"
        if top_rows else '<div class="empty">لا يوجد مودعون في هذه الفترة</div>'
    )

    # البونصات
    est = "~" if data["bonus_estimated"] else ""
    ch = data["charge"] or 0
    brow = []
    for label, amount in data["bonus_rows"]:
        share = (amount / ch * 100) if ch else 0.0
        mark = est if label == "بونص الإيداع" else ""
        brow.append(f"<tr><td>{label}</td><td>{mark}{_fmt(amount)}</td><td>{share:.1f}%</td></tr>")
    tshare = (data["bonus_total"] / ch * 100) if ch else 0.0
    bonus_html = (
        "<table><tr><th>النوع</th><th>المبلغ</th><th>% من الشحن</th></tr>"
        + "".join(brow)
        + f"<tr><td><b>الإجمالي</b></td><td><b>{est}{_fmt(data['bonus_total'])}</b></td><td><b>{tshare:.1f}%</b></td></tr></table>"
    )
    if data["bonus_estimated"]:
        bonus_html += '<div class="sub" style="margin-top:8px">~ تقديري: بعض العمليات سُجّلت قبل تفعيل تتبّع البونص، قُدّر بونصها بالنسبة الحالية للطريقة.</div>'

    # المخاطر
    risk_parts = []
    if data["risky"]:
        risk_parts.append(
            '<div class="alert">⚠️ مستخدمون أودعوا ثم سحبوا معظم المبلغ في نفس اليوم — قد يكون استغلالاً للبونص أو نشاطاً مشبوهاً.</div>'
        )
        rr = "".join(
            f"<tr><td><code>{r['user_id']}</code></td><td>{_fmt(r['deposit'])}</td><td>{_fmt(r['withdraw'])}</td><td>{r['ratio']:.0f}%</td></tr>"
            for r in data["risky"][:15]
        )
        risk_parts.append(f'<table style="margin-top:10px"><tr><th>المستخدم</th><th>أودع</th><th>سحب</th><th>النسبة</th></tr>{rr}</table>')
    if data["pending_count"]:
        age = ""
        if data["pending_oldest"]:
            hrs = (_utcnow() - data["pending_oldest"]).total_seconds() / 3600
            age = f" (أقدمها منذ {hrs:.0f} ساعة)"
        risk_parts.append(f'<div class="alert">⏳ يوجد {data["pending_count"]} طلب سحب معلّق أكثر من {PENDING_WITHDRAW_ALERT_HOURS} ساعات{age}.</div>')
    if not risk_parts:
        risk_parts.append('<div class="empty" style="color:#4ade80">لا توجد إشارات مخاطر ✅</div>')

    sub = f"من {s_local:%Y-%m-%d %H:%M} إلى {e_local:%Y-%m-%d %H:%M} (بتوقيت دمشق)"
    if data["partial"]:
        sub += " — تقرير جزئي حتى اللحظة"
    now_local = datetime.now(timezone.utc).astimezone(SYRIA_TZ)

    return (
        '<!DOCTYPE html>\n<html lang="ar" dir="rtl"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>تقرير {day.isoformat()}</title><style>\n{_CSS}</style></head>\n"
        '<body><div class="wrap">\n'
        f"  <h1>📊 التقرير اليومي</h1>\n  <div class=\"sub\">{sub}</div>\n\n"
        f'  <div class="cards">{cards}</div>\n\n'
        f"  <h2>⏰ توزيع عمليات الشحن على ساعات اليوم</h2>\n  {_svg_hourly(data['hourly'])}\n\n"
        f"  <h2>📈 آخر {CHART_DAYS} أيام — شحن مقابل سحب</h2>\n"
        '  <div class="legend"><span><i style="background:#22c55e"></i>شحن</span>'
        '<span><i style="background:#ef4444"></i>سحب</span></div>'
        f"{_svg_series(data['series'])}\n\n"
        f"  <h2>💰 الشحن حسب الطريقة</h2>\n  {_method_table(data['by_method'])}\n\n"
        f"  <h2>💸 السحب حسب الطريقة</h2>\n  {_method_table(data['wd_by_method'])}\n\n"
        f"  <h2>🏅 أعلى {TOP_DEPOSITORS} مودعين</h2>\n  {top_html}\n\n"
        f"  <h2>🎁 تكلفة البونصات</h2>\n  {bonus_html}\n\n"
        f"  <h2>🚨 إشارات المخاطر</h2>\n  {''.join(risk_parts)}\n\n"
        f"  <h2>🔎 مطابقة شام كاش</h2>\n  {_sham_html(sham)}\n\n"
        f'  <div class="foot">أُنشئ في {now_local:%Y-%m-%d %H:%M} — بتوقيت دمشق</div>\n'
        "</div></body></html>\n"
    )


def render_summary(data: dict, sham) -> str:
    """ملخّص نصي قصير (HTML parse_mode) يرافق الملف كوصف."""
    prev = data["prev"]

    def arrow(cur, p, invert=False):
        pct = _pct_change(cur, p)
        if pct is None or abs(pct) < 0.05:
            return ""
        good = (pct > 0) != invert
        return f" {'🟢' if good else '🔴'}{'▲' if pct > 0 else '▼'}{abs(pct):.1f}%"

    alerts = len(data["risky"]) + (1 if data["pending_count"] else 0)
    if isinstance(sham, dict) and "error" not in sham:
        alerts += len(sham["unmatched_in"]) + len(sham["unmatched_out"])
    est = "~" if data["bonus_estimated"] else ""
    ch = data["charge"] or 0
    bshare = (data["bonus_total"] / ch * 100) if ch else 0.0
    title = "تقرير جزئي" if data["partial"] else "التقرير اليومي"

    lines = [
        f"📊 <b>{title} — {data['day'].isoformat()}</b>",
        "",
        f"💰 الشحن: <b>{_fmt(data['charge'])}</b>{arrow(data['charge'], prev['charge'])}",
        f"💸 السحب: <b>{_fmt(data['withdraw'])}</b>{arrow(data['withdraw'], prev['withdraw'], invert=True)}",
        f"🔥 صافي الحرق: <b>{_fmt(data['net_burn'])}</b>",
        f"⚖️ شحن/سحب: <b>{data['ratio']:.2f}</b>" if data["ratio"] is not None else "⚖️ شحن/سحب: —",
        f"👥 مودعون: {data['depositors']} (جدد {data['new_depositors']}) · ساحبون: {data['withdrawers']} · مستخدمون جدد: {data['new_users']}",
        f"🎁 البونصات: {est}{_fmt(data['bonus_total'])} ({bshare:.1f}% من الشحن)",
        f"🚨 تنبيهات: <b>{alerts}</b>" if alerts else "✅ لا توجد تنبيهات",
        "",
        "📎 التقرير الكامل بالملف المرفق — افتحه بالمتصفح.",
    ]
    return "\n".join(lines)


# ============================================================
# التوليد والإرسال
# ============================================================
async def generate_report(bot, day: date, end_cap_utc: datetime = None):
    data = await asyncio.to_thread(collect_report_data, day, end_cap_utc)
    names = await _resolve_names(bot, [t["user_id"] for t in data["top"]])
    sham = await _sham_section(data)
    return render_html(data, names, sham), render_summary(data, sham), f"report_{day.isoformat()}.html"


async def send_report(bot, targets, day: date, end_cap_utc: datetime = None) -> list:
    """يولّد التقرير ويرسله لكل chat في targets. يرجع قائمة الوجهات التي نجح الإرسال إليها."""
    doc, summary, fname = await generate_report(bot, day, end_cap_utc)
    ok = []
    for chat_id in targets:
        try:
            bio = BytesIO(doc.encode("utf-8"))
            bio.name = fname
            if len(summary) <= CAPTION_LIMIT:
                await bot.send_document(chat_id, document=bio, caption=summary, parse_mode="HTML")
            else:
                await bot.send_message(chat_id, summary, parse_mode="HTML")
                await bot.send_document(chat_id, document=bio)
            ok.append(chat_id)
        except Exception as exc:
            log.error("send report to %s failed: %s", chat_id, exc)
    return ok


# ============================================================
# الجدولة اليومية
# ============================================================
def _get_last_sent_day():
    try:
        with _db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (SETTINGS_KEY_LAST_DAY,)).fetchone()
            return row[0] if row else None
    except Exception:
        return None


def _set_last_sent_day(iso: str):
    with _db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (SETTINGS_KEY_LAST_DAY, iso))


async def daily_report_loop(application, primary_targets, fallback_targets):
    """
    كل دقيقة: إذا صارت الساعة ≥ 01:00 بتوقيت دمشق ولم يُرسل تقرير «الأمس» بعد، يرسله.
    primary_targets / fallback_targets: دوال بدون وسائط ترجع قائمة chat_ids.
    - يُرسل أولاً للقناة؛ إذا فشل الإرسال لها كلياً يُرسل للأدمن + ينبّههم.
    - آخر يوم مُرسل محفوظ في settings فلا يتكرر التقرير بعد إعادة التشغيل.
    """
    await asyncio.sleep(20)
    last_attempt = 0.0
    while True:
        try:
            local = datetime.now(timezone.utc).astimezone(SYRIA_TZ)
            if local.hour >= REPORT_START_HOUR:
                target_day = (local - timedelta(days=1)).date()
                if _get_last_sent_day() != target_day.isoformat() and time.time() - last_attempt >= RETRY_AFTER_FAILURE_SECONDS:
                    last_attempt = time.time()
                    bot = application.bot
                    prim = list(primary_targets())
                    ok = await send_report(bot, prim, target_day) if prim else []
                    if not ok:
                        fb = list(fallback_targets())
                        ok = await send_report(bot, fb, target_day)
                        if ok and prim:
                            for admin_id in fb:
                                try:
                                    await bot.send_message(
                                        admin_id,
                                        "⚠️ تعذّر الإرسال إلى «قناة التقارير» — تأكد أن البوت أدمن في القناة وأن المعرّف صحيح.",
                                    )
                                except Exception:
                                    pass
                    if ok:
                        _set_last_sent_day(target_day.isoformat())
                        log.info("daily report %s sent to %s", target_day, ok)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("daily report loop error: %s", exc)
        await asyncio.sleep(LOOP_POLL_SECONDS)


# ============================================================
# معاينة محلية: python daily_report.py 2026-10-06
# ============================================================
if __name__ == "__main__":
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else (datetime.now(SYRIA_TZ) - timedelta(days=1)).date()
    doc, summary, fname = asyncio.run(generate_report(None, d))
    with open(fname, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(summary)
    print(f"\n→ {fname}")
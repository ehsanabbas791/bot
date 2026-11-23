# -*- coding: utf-8 -*-
"""
referral_burn.py — نظام الإحالات حسب الحرق: كل لاعب يختار نظامه (هرمي / فردي) أول ما يستخدم البوت
===========================================================================================

الاختيار نهائي لكل لاعب (users.referral_plan) ويحدد كيف يربح هو من إحالاته:

  فردي (individual):  10% من حرق كل لاعب دخل مباشرة عبر رابطه (أي عدد). منفصل تماماً: لا مستويات، ولا يمرّر شيئاً لمن فوقه.
  هرمي (hierarchical): 9% من حرق من دخل عبر رابطه، 6% من حرق من دعاهم هؤلاء، 3% من المستوى الثالث —
                        بشرط أن تكون حلقات السلسلة أيضاً ضمن النظام الهرمي (الفردي يقطع السلسلة: «ما حدا الو علاقة بحدا»).

مثال: P1(هرمي) → P2(هرمي) → P3(هرمي) → P4:  حرق P4 ⇒ P3 9% ، P2 6% ، P1 3%.
       لو P2 اختار فردي: P2 يربح 10% من P3 فقط، ولا يربح P1 شيئاً من P3؛ لكن P1 يبقى يربح 9% من حرق P2 نفسه (إحالته المباشرة).

شروط الاستحقاق (لكل مستفيد، في كل دورة):
  1) عنده على الأقل MIN_ACTIVE_REFERRALS إحالة مباشرة نشطة (نشطة = حمّلت iChancy خلال الدورة، أي تشحن وتلعب).
  2) مجموع حرق نطاقه > مجموع سحبه خلال الدورة (النطاق: مستوى واحد للفردي، حتى 3 مستويات هرمية للهرمي).

التوزيع: كل CYCLE_DAYS يوماً من تاريخ آخر توزيع (حدود متصلة لا تنزلق). في نهاية كل دورة يُنشأ تقرير مفصّل للأدمن،
والصرف تلقائي أو بعد اعتماد الأدمن. الصرف ذرّي ولا يتكرر.

نقاط الضبط التي قد تحتاج تأكيداً من صاحب البوت (ثوابت معزولة):
  BURN_BASIS       "net"  = حرق اللاعب = max(0, شحن آيشانسي − سحب منه)    |  "gross" = إجمالي ما شحنه لآيشانسي
  WITHDRAW_SOURCE  "bot"  = سحوبات البوت المعتمدة                          |  "ichancy" = السحب من آيشانسي
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from database import _db

log = logging.getLogger("referral_burn")

# ============================================================
# الإعدادات
# ============================================================
PLANS = ("hierarchical", "individual")
PLAN_LABELS = {"hierarchical": "🌳 هرمي", "individual": "👤 فردي"}
HIER_PERCENTS = (0.09, 0.06, 0.03)
INDIVIDUAL_PERCENT = 0.10

CYCLE_DAYS = 10
MIN_ACTIVE_REFERRALS = 3
BURN_BASIS = "net"            # net | gross
WITHDRAW_SOURCE = "bot"       # bot | ichancy
MAX_NOTIFY_LINES = 20         # عدد أسطر التفاصيل في رسالة اللاعب
SNAPSHOT_TTL_SECONDS = 120    # كاش حساب الدورة الحالية لشاشة اللاعب

SYRIA_TZ = timezone(timedelta(hours=3))
_TS = "%Y-%m-%d %H:%M:%S"

KEY_ENABLED = "ref2_enabled"
KEY_START = "ref2_cycle_start"
KEY_AUTO = "ref2_auto_credit"


# ============================================================
# الجداول
# ============================================================
with _db() as _conn:
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS ref2_cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_at TEXT NOT NULL UNIQUE,
            end_at TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',     -- pending | paid | cancelled
            total_amount INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            paid_at TIMESTAMP
        )
    """)
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS ref2_beneficiaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            eligible INTEGER NOT NULL,
            reason TEXT,
            active_referrals INTEGER NOT NULL,
            total_burn INTEGER NOT NULL,
            total_withdraw INTEGER NOT NULL,
            potential INTEGER NOT NULL,                 -- ما كان سيستحقه لو كان مؤهلاً
            amount INTEGER NOT NULL,                    -- المستحق فعلياً (0 إن غير مؤهل)
            notified INTEGER NOT NULL DEFAULT 0,
            plan TEXT,
            UNIQUE(cycle_id, user_id)
        )
    """)
    if "plan" not in {r[1] for r in _conn.execute("PRAGMA table_info(ref2_beneficiaries)")}:
        _conn.execute("ALTER TABLE ref2_beneficiaries ADD COLUMN plan TEXT")
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS ref2_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id INTEGER NOT NULL,
            beneficiary_id INTEGER NOT NULL,
            level INTEGER NOT NULL,
            source_user_id INTEGER NOT NULL,
            burn INTEGER NOT NULL,
            withdraw INTEGER NOT NULL,
            percent REAL NOT NULL,
            amount INTEGER NOT NULL
        )
    """)
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_ref2_lines_cycle ON ref2_lines(cycle_id, beneficiary_id)")


# ============================================================
# أدوات
# ============================================================
def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt_ts(dt: datetime) -> str:
    return dt.strftime(_TS)


def _parse_ts(s: str) -> datetime:
    return datetime.strptime(s[:19], _TS)


def _get(key: str, default=None):
    with _db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row and row[0] is not None else default


def _set(key: str, value: str) -> None:
    with _db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))


def is_enabled() -> bool:
    """النظام الجديد مفعّل افتراضياً. إيقافه (مفتاح طوارئ) يعيد عمولة الشحن القديمة."""
    return _get(KEY_ENABLED, "1") != "0"


is_new_system_active = is_enabled


def set_enabled(enabled: bool) -> None:
    """عند إعادة التفعيل بعد إيقاف تبدأ دورة جديدة من الآن (لا احتساب لفترة التوقف)."""
    was = is_enabled()
    _set(KEY_ENABLED, "1" if enabled else "0")
    if enabled and not was:
        _set(KEY_START, _fmt_ts(_utcnow()))


def get_auto_credit() -> bool:
    return _get(KEY_AUTO, "0") == "1"


def set_auto_credit(enabled: bool) -> None:
    _set(KEY_AUTO, "1" if enabled else "0")


def get_cycle_start():
    raw = _get(KEY_START)
    return _parse_ts(raw) if raw else None


def plan_counts() -> dict:
    """عدد اللاعبين لكل نظام (للأدمن)."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT referral_plan, COUNT(*) FROM users WHERE agreed=1 GROUP BY referral_plan"
        ).fetchall()
    counts = {"hierarchical": 0, "individual": 0, "none": 0}
    for plan, n in rows:
        counts[plan if plan in PLANS else "none"] += int(n)
    return counts


# ============================================================
# تحميل البيانات وحساب الأدوار
# ============================================================
def _load_data(start: datetime, end: datetime) -> dict:
    a, b = _fmt_ts(start), _fmt_ts(end)
    with _db() as conn:
        parent = {int(r[0]): int(r[1]) for r in conn.execute("SELECT referred_user_id, referrer_id FROM referrals")}
        plans = {int(r[0]): r[1] for r in conn.execute("SELECT user_id, referral_plan FROM users WHERE referral_plan IS NOT NULL")}

        inn, out = {}, {}
        for uid, direction, total in conn.execute(
            """SELECT user_id, direction, COALESCE(SUM(amount_nsp), 0)
               FROM ichancy_transactions WHERE created_at >= ? AND created_at < ?
               GROUP BY user_id, direction""", (a, b)
        ):
            (inn if direction == "in" else out)[int(uid)] = int(total)

        wd_bot = {
            int(uid): int(total) for uid, total in conn.execute(
                """SELECT user_id, COALESCE(SUM(amount), 0) FROM withdraw_requests
                   WHERE status='approved' AND COALESCE(approved_at, created_at) >= ?
                     AND COALESCE(approved_at, created_at) < ?
                   GROUP BY user_id""", (a, b)
            )
        }

    children = {}
    for child, par in parent.items():
        if child != par:
            children.setdefault(par, []).append(child)

    return {"start": start, "end": end, "parent": parent, "children": children, "plans": plans,
            "inn": inn, "out": out, "wd_bot": wd_bot}


def _burn(d: dict, u: int) -> int:
    i, o = d["inn"].get(u, 0), d["out"].get(u, 0)
    return i if BURN_BASIS == "gross" else max(0, i - o)


def _withdrawn(d: dict, u: int) -> int:
    return d["out"].get(u, 0) if WITHDRAW_SOURCE == "ichancy" else d["wd_bot"].get(u, 0)


def _contributions(d: dict) -> dict:
    """
    من يربح من حرق كل لاعب: يصعد من اللاعب عبر محيليه (حتى 3 مستويات):
      فردي      → يربح 10% فقط إذا كان محيله المباشر، ثم تنقطع السلسلة.
      هرمي      → يربح 9/6/3% حسب المستوى، وتستمر السلسلة.
      بلا اختيار → لا يربح وتنقطع السلسلة.
    """
    parent, plans = d["parent"], d["plans"]
    contribs = {}
    for source in sorted(u for u in parent if _burn(d, u) > 0):
        node, seen = source, {source}
        for level in (1, 2, 3):
            anc = parent.get(node)
            if anc is None or anc in seen:
                break
            seen.add(anc)
            plan = plans.get(anc)
            if plan == "individual":
                if level == 1:
                    contribs.setdefault(anc, []).append((1, source, INDIVIDUAL_PERCENT))
                break
            if plan == "hierarchical":
                contribs.setdefault(anc, []).append((level, source, HIER_PERCENTS[level - 1]))
                node = anc
                continue
            break
    return contribs


def _scope_levels(d: dict, ben: int) -> list:
    """نطاق المستفيد: مستوى واحد للفردي؛ للهرمي حتى 3 مستويات عبر حلقات هرمية فقط."""
    children, plans = d["children"], d["plans"]
    l1 = list(children.get(ben, []))
    if plans.get(ben) != "hierarchical":
        return [l1]
    seen = {ben, *l1}
    levels, frontier = [l1], l1
    for _ in range(2):
        nxt = []
        for node in frontier:
            if plans.get(node) != "hierarchical":
                continue
            for ch in children.get(node, ()):
                if ch not in seen:
                    seen.add(ch)
                    nxt.append(ch)
        levels.append(nxt)
        frontier = nxt
    return levels


def _evaluate(d: dict, ben: int, spec: list) -> dict:
    levels = _scope_levels(d, ben)
    scope = [u for lvl in levels for u in lvl]
    directs = levels[0] if levels else []
    active = [c for c in directs if d["inn"].get(c, 0) > 0]

    total_burn = sum(_burn(d, u) for u in scope)
    total_withdraw = sum(_withdrawn(d, u) for u in scope)

    lines = [{
        "level": level, "source": source, "burn": _burn(d, source), "withdraw": _withdrawn(d, source),
        "percent": pct, "amount": int(_burn(d, source) * pct),
    } for level, source, pct in sorted(spec, key=lambda x: (x[0], x[1]))]
    potential = sum(l["amount"] for l in lines)

    reason = ""
    if len(active) < MIN_ACTIVE_REFERRALS:
        reason = f"عنده {len(active)} من {MIN_ACTIVE_REFERRALS} إحالات نشطة مطلوبة"
    elif total_burn <= total_withdraw:
        reason = f"مجموع الحرق ({total_burn:,}) ليس أعلى من مجموع السحب ({total_withdraw:,})"
    eligible = not reason

    return {
        "user_id": ben, "plan": d["plans"].get(ben), "eligible": eligible, "reason": reason,
        "active_referrals": len(active), "direct_referrals": len(directs),
        "total_burn": total_burn, "total_withdraw": total_withdraw,
        "potential": potential, "amount": potential if eligible else 0, "lines": lines,
    }


def compute_cycle(start: datetime, end: datetime, mode: str = "plans") -> dict:
    """يحسب نتيجة الدورة [start, end) بدون أي كتابة. نفس البنية يستخدمها التقرير والصرف."""
    d = _load_data(start, end)
    contribs = _contributions(d)
    beneficiaries = [_evaluate(d, ben, contribs[ben]) for ben in sorted(contribs)]
    return {
        "mode": "plans", "start": start, "end": end, "beneficiaries": beneficiaries,
        "total_amount": sum(x["amount"] for x in beneficiaries),
        "total_potential": sum(x["potential"] for x in beneficiaries),
    }


# كاش حساب «الدورة الحالية» لشاشة اللاعب (لا نعيد حساب الشبكة كلها عند كل ضغطة)
_snapshot = {"key": None, "ts": 0.0, "data": None, "contribs": None}


def player_status(user_id: int):
    """وضع اللاعب بالدورة الجارية (حتى اللحظة) أو None إن لم يكن النظام شغالاً/لم يختر نظاماً."""
    if not is_enabled():
        return None
    start = get_cycle_start()
    if start is None:
        return None
    now = _utcnow()
    key = _fmt_ts(start)
    if _snapshot["key"] != key or time.time() - _snapshot["ts"] > SNAPSHOT_TTL_SECONDS:
        d = _load_data(start, now)
        _snapshot.update(key=key, ts=time.time(), data=d, contribs=_contributions(d))
    d = _snapshot["data"]
    if d["plans"].get(int(user_id)) is None:
        return None
    status = _evaluate(d, int(user_id), _snapshot["contribs"].get(int(user_id), []))
    status["cycle_start"], status["cycle_end"] = start, start + timedelta(days=CYCLE_DAYS)
    return status


# ============================================================
# التخزين والصرف
# ============================================================
def store_cycle(result: dict) -> int:
    """يخزّن نتيجة دورة (idempotent على start_at). يرجع cycle_id."""
    s, e = _fmt_ts(result["start"]), _fmt_ts(result["end"])
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id FROM ref2_cycles WHERE start_at=?", (s,)).fetchone()
        if row:
            return int(row[0])
        cur = conn.execute(
            "INSERT INTO ref2_cycles (start_at, end_at, mode, total_amount) VALUES (?, ?, ?, ?)",
            (s, e, result.get("mode", "plans"), result["total_amount"]),
        )
        cid = cur.lastrowid
        for b in result["beneficiaries"]:
            conn.execute(
                """INSERT INTO ref2_beneficiaries
                   (cycle_id, user_id, eligible, reason, active_referrals, total_burn, total_withdraw, potential, amount, plan)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (cid, b["user_id"], int(b["eligible"]), b["reason"], b["active_referrals"],
                 b["total_burn"], b["total_withdraw"], b["potential"], b["amount"], b.get("plan")),
            )
            for l in b["lines"]:
                conn.execute(
                    """INSERT INTO ref2_lines (cycle_id, beneficiary_id, level, source_user_id, burn, withdraw, percent, amount)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (cid, b["user_id"], l["level"], l["source"], l["burn"], l["withdraw"], l["percent"], l["amount"]),
                )
        return cid


def load_cycle(cycle_id: int):
    """يحمّل دورة مخزّنة بنفس بنية compute_cycle (+ id وstatus)."""
    with _db() as conn:
        c = conn.execute("SELECT * FROM ref2_cycles WHERE id=?", (int(cycle_id),)).fetchone()
        if c is None:
            return None
        bens = conn.execute(
            "SELECT * FROM ref2_beneficiaries WHERE cycle_id=? ORDER BY amount DESC, potential DESC, user_id", (int(cycle_id),)
        ).fetchall()
        lines = conn.execute(
            "SELECT * FROM ref2_lines WHERE cycle_id=? ORDER BY beneficiary_id, level, source_user_id", (int(cycle_id),)
        ).fetchall()
    by_ben = {}
    for l in lines:
        by_ben.setdefault(int(l["beneficiary_id"]), []).append({
            "level": int(l["level"]), "source": int(l["source_user_id"]), "burn": int(l["burn"]),
            "withdraw": int(l["withdraw"]), "percent": float(l["percent"]), "amount": int(l["amount"]),
        })
    return {
        "id": int(c["id"]), "status": c["status"], "mode": c["mode"],
        "start": _parse_ts(c["start_at"]), "end": _parse_ts(c["end_at"]),
        "total_amount": int(c["total_amount"]), "paid_at": c["paid_at"],
        "beneficiaries": [{
            "user_id": int(b["user_id"]), "plan": b["plan"], "eligible": bool(b["eligible"]), "reason": b["reason"] or "",
            "active_referrals": int(b["active_referrals"]), "total_burn": int(b["total_burn"]),
            "total_withdraw": int(b["total_withdraw"]), "potential": int(b["potential"]), "amount": int(b["amount"]),
            "lines": by_ben.get(int(b["user_id"]), []),
        } for b in bens],
    }


def credit_cycle(cycle_id: int):
    """
    صرف دورة بانتظار الاعتماد (ذرّي، مرة واحدة). يرجع قائمة [(user_id, amount)] للمدفوعين،
    أو None إذا لم تعد الدورة pending (دُفعت/أُلغيت مسبقاً).
    """
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE ref2_cycles SET status='paid', paid_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
            (int(cycle_id),),
        )
        if cur.rowcount != 1:
            return None
        paid = []
        for r in conn.execute(
            "SELECT user_id, amount FROM ref2_beneficiaries WHERE cycle_id=? AND eligible=1 AND amount>0", (int(cycle_id),)
        ).fetchall():
            upd = conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (int(r["amount"]), int(r["user_id"])))
            if upd.rowcount == 1:
                paid.append((int(r["user_id"]), int(r["amount"])))
        return paid


def cancel_cycle(cycle_id: int) -> bool:
    with _db() as conn:
        cur = conn.execute("UPDATE ref2_cycles SET status='cancelled' WHERE id=? AND status='pending'", (int(cycle_id),))
        return cur.rowcount == 1


def list_cycles(limit: int = 8) -> list:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, start_at, end_at, mode, status, total_amount FROM ref2_cycles ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def pending_count() -> int:
    with _db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM ref2_cycles WHERE status='pending'").fetchone()[0])


def unnotified(cycle_id: int) -> list:
    """مستفيدو دورة مصروفة لم يُبلَّغوا بعد (مؤهلون وغير مؤهلين)."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT user_id FROM ref2_beneficiaries WHERE cycle_id=? AND notified=0", (int(cycle_id),)
        ).fetchall()
    return [int(r[0]) for r in rows]


def paid_cycles_with_unnotified() -> list:
    with _db() as conn:
        rows = conn.execute(
            """SELECT DISTINCT c.id FROM ref2_cycles c JOIN ref2_beneficiaries b ON b.cycle_id = c.id
               WHERE c.status='paid' AND b.notified=0 ORDER BY c.id"""
        ).fetchall()
    return [int(r[0]) for r in rows]


def mark_notified(cycle_id: int, user_id: int) -> None:
    with _db() as conn:
        conn.execute("UPDATE ref2_beneficiaries SET notified=1 WHERE cycle_id=? AND user_id=?", (int(cycle_id), int(user_id)))


# ============================================================
# إغلاق الدورات المستحقة (تستدعيه الحلقة)
# ============================================================
def close_due_cycles(now: datetime = None) -> list:
    """
    لكل دورة انتهت (start + CYCLE_DAYS <= now): يحسب ويخزّن نتيجتها ويقدّم بداية الدورة التالية = نهاية هذه.
    يرجع قائمة cycle_id الجديدة (بالترتيب). لا يصرف شيئاً — الصرف خطوة منفصلة.
    """
    if not is_enabled():
        return []
    now = now or _utcnow()
    start = get_cycle_start()
    if start is None:
        _set(KEY_START, _fmt_ts(now))
        return []

    created = []
    while start + timedelta(days=CYCLE_DAYS) <= now:
        end = start + timedelta(days=CYCLE_DAYS)
        created.append(store_cycle(compute_cycle(start, end)))
        start = end
        _set(KEY_START, _fmt_ts(start))
    return created


# ============================================================
# النصوص
# ============================================================
def plan_description(plan: str) -> str:
    if plan == "hierarchical":
        return "9% من حرق من يدخل عبر رابطك، و6% من حرق من يدعوهم هو، و3% من المستوى الثالث (ضمن السلسلة الهرمية)."
    return "10% من حرق كل شخص يدخل عبر رابطك (أي عدد)، نظام منفصل بلا مستويات."


def beneficiary_text(b: dict, cycle: dict, currency: str = "ل.س") -> str:
    s = (cycle["start"] + timedelta(hours=3)).strftime("%m-%d")
    e = (cycle["end"] + timedelta(hours=3)).strftime("%m-%d")
    plan = PLAN_LABELS.get(b.get("plan"), "")
    head = f"🌳 أرباح الإحالات {plan} — دورة {s} → {e}\n\n"
    if b["eligible"] and b["amount"] > 0:
        head += f"✅ أُضيف لرصيدك: {b['amount']:,} {currency}\n"
    elif b["eligible"]:
        head += "ℹ️ لا أرباح هالدورة (لا يوجد حرق ضمن نطاقك).\n"
    else:
        head += f"⚠️ ما انضافت أرباح هالدورة\nالسبب: {b['reason']}\n"
    head += f"📊 مجموع الحرق عبر رابطك: {b['total_burn']:,} {currency}\n👥 إحالاتك النشطة: {b['active_referrals']}\n"

    shown = [l for l in b["lines"] if l["burn"] > 0][:MAX_NOTIFY_LINES]
    if shown:
        head += "\nالتفاصيل:\n"
        for l in shown:
            head += f"• ID {l['source']} (مستوى {l['level']}): حرق {l['burn']:,} ← {l['amount']:,}\n"
        rest = len([x for x in b["lines"] if x["burn"] > 0]) - len(shown)
        if rest > 0:
            head += f"… و{rest} آخرين\n"
    return head


# ============================================================
# تقرير HTML للأدمن (نفس شكل التقرير اليومي)
# ============================================================
def _money(n) -> str:
    return f"{int(n):,}"


def render_report_html(cycle: dict, names: dict = None, title_note: str = "") -> str:
    """
    تقرير الدورة: لكل مستفيد → نظامه، المستحق ونسبته الفعلية، مجموع الحرق عبر رابطه،
    ومعرّفات المُحالين مع حرق كل معرّف خلال الدورة.
    """
    import html as _html
    from daily_report import _CSS

    names = names or {}
    esc = _html.escape
    s = (cycle["start"] + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M")
    e = (cycle["end"] + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M")
    bens = cycle["beneficiaries"]
    eligible_n = sum(1 for b in bens if b["eligible"])
    status_txt = {"pending": "بانتظار الاعتماد", "paid": "تم الصرف", "cancelled": "ملغاة"}.get(cycle.get("status", ""), "معاينة")

    def card(k, v, c=""):
        return f'<div class="card"><div class="k">{esc(k)}</div><div class="v">{v}</div><div class="c">{c}</div></div>'

    cards = "".join([
        card("المستفيدون", f"{len(bens)}", f"مؤهلون: {eligible_n}"),
        card("إجمالي المستحق", _money(cycle["total_amount"]), f"لو الجميع مؤهل: {_money(sum(b['potential'] for b in bens))}"),
        card("حالة الدورة", esc(status_txt)),
    ])

    sections = []
    for b in bens:
        uid = b["user_id"]
        nm = names.get(uid)
        who = f"{esc(nm)} · <code>{uid}</code>" if nm else f"<code>{uid}</code>"
        plan = esc(PLAN_LABELS.get(b.get("plan"), "—"))
        badge = '<span class="badge up">✅ مؤهل</span>' if b["eligible"] else '<span class="badge down">⛔ غير مؤهل</span>'
        eff = (b["amount"] / b["total_burn"] * 100) if b["total_burn"] else 0.0
        reason = "" if b["eligible"] else f'<div class="alert">{esc(b["reason"])}</div>'

        rows = [
            f"<tr><td>{l['level']}</td><td><code>{l['source']}</code></td><td>{_money(l['burn'])}</td>"
            f"<td>{l['percent'] * 100:.0f}%</td><td>{_money(l['amount'])}</td></tr>"
            for l in b["lines"]
        ]
        table = (
            "<table><tr><th>المستوى</th><th>ID المُحال</th><th>حرقه (الدورة)</th><th>النسبة</th><th>المبلغ</th></tr>"
            + "".join(rows) + "</table>"
        ) if rows else '<div class="empty">لا توجد تفاصيل</div>'

        sections.append(
            f"<h2>{who} · {plan} {badge}</h2>"
            f'<div class="cards" style="margin-bottom:10px">'
            + card("المستحق", _money(b["amount"]), f"النسبة الفعلية من حرق رابطه: {eff:.1f}%")
            + card("مجموع الحرق عبر رابطه", _money(b["total_burn"]), f"مجموع سحب نطاقه: {_money(b['total_withdraw'])}")
            + card("إحالات نشطة", f"{b['active_referrals']}", f"المطلوب: {MIN_ACTIVE_REFERRALS}")
            + "</div>" + reason + table
        )

    body = "".join(sections) if sections else '<div class="empty">لا يوجد حرق ضمن نطاق أي محيل في هذه الدورة</div>'
    now_local = datetime.now(timezone.utc).astimezone(SYRIA_TZ)
    note = f" — {esc(title_note)}" if title_note else ""
    return (
        '<!DOCTYPE html>\n<html lang="ar" dir="rtl"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>تقرير الإحالات {s[:10]}</title><style>\n{_CSS}</style></head>\n"
        '<body><div class="wrap">\n'
        f'  <h1>🌳 تقرير الإحالات (حسب الحرق){note}</h1>\n'
        f'  <div class="sub">الدورة: من {s} إلى {e} (بتوقيت دمشق) — لكل لاعب نظامه (هرمي/فردي)</div>\n'
        f'  <div class="cards">{cards}</div>\n{body}\n'
        f'  <div class="foot">أُنشئ في {now_local:%Y-%m-%d %H:%M} — بتوقيت دمشق</div>\n'
        "</div></body></html>\n"
    )


def admin_summary_text(cycle: dict, currency: str = "ل.س") -> str:
    bens = cycle["beneficiaries"]
    elig = [b for b in bens if b["eligible"] and b["amount"] > 0]
    s = (cycle["start"] + timedelta(hours=3)).strftime("%m-%d")
    e = (cycle["end"] + timedelta(hours=3)).strftime("%m-%d")
    hier = sum(1 for b in bens if b.get("plan") == "hierarchical")
    ind = sum(1 for b in bens if b.get("plan") == "individual")
    return (
        f"🌳 <b>تقرير الإحالات — دورة {s} → {e}</b>\n\n"
        f"👥 المستفيدون: {len(bens)} (هرمي {hier} · فردي {ind}) — مؤهلون {len(elig)}\n"
        f"💰 إجمالي المستحق: <b>{cycle['total_amount']:,}</b> {currency}\n"
        f"⛔ غير مؤهلين: {len(bens) - len(elig)}"
    )
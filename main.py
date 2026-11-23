from telegram import BotCommand
from telegram.error import RetryAfter, Forbidden, BadRequest, TelegramError
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ApplicationHandlerStop
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from database import (
    get_user, update_balance, create_recharge, create_withdraw,delete_user,
    update_ichancy_account, update_user_agreed, get_user_balance,get_welcome_bonus_config,
    get_user_recharges, get_user_withdraws,set_welcome_bonus_config,transaction_already_used,
    update_recharge_status, update_withdraw_status,apply_welcome_bonus,
    get_last_spin_date, update_last_spin_date,
    get_last_spin_at, update_last_spin_at,
    get_spin_cooldown_hours, set_spin_cooldown_hours,
    get_active_spin_prizes, get_all_spin_prizes,
    add_spin_prize, set_spin_prize_active, delete_spin_prize,
    update_spin_prize_weight,
    get_spin_prize, pick_random_spin_prize,
    get_all_users,
    get_usdt_rate, set_usdt_rate, approve_verified_recharge,set_referrer,
    get_referral_info,
    get_referrer,get_referral_users,
    activate_referral_after_recharge,
    get_referral_users,
    create_gift_code,
    redeem_gift_code,
    get_all_referrals,
    get_referral_commission_percent, set_referral_commission_percent,
    get_referral_period_days, set_referral_period_days,
    log_referral_commission, get_pending_commission_total,
    mark_referral_commissions_paid, init_referral_cycle_if_missing,
    get_referral_cycle_start, reset_referral_cycle,
    get_all_referrer_ids, get_recent_referral_payouts,
    get_bonus_percent, set_bonus_percent, get_all_bonus_percents,
    log_ichancy_transaction, get_ichancy_movement_since,
    get_cashback_snapshot, init_cashback_snapshot_if_missing, update_cashback_snapshot,
    record_cashback_payout, get_cashback_percent, set_cashback_percent,
    get_users_with_ichancy, get_recent_cashback_payouts,
    get_shamcash_usd_rate, set_shamcash_usd_rate,
    create_pending_recharge_once, get_pending_recharge,
    approve_pending_recharge, reject_pending_recharge,
    has_approved_recharge, try_claim_spin, SPIN_MIN_COOLDOWN_HOURS,
    SERVICE_SECTIONS, NOTICE_MAX_LENGTH, get_notice_state,
    get_active_service_notice, set_notice_text, set_notice_enabled,
    delete_notice, any_service_notice_active
)
from daily_cashback_store import (
    CASHBACK_MODES, EPOCH_TS, MAX_WEEKLY_DURATION_WEEKS, MAX_DAILY_DURATION_DAYS,
    get_cashback_mode, set_cashback_mode,
    get_daily_cashback_percent, set_daily_cashback_percent,
    get_weekly_duration_weeks, set_weekly_duration_weeks,
    get_daily_duration_days, set_daily_duration_days,
    get_cashback_started_at, ensure_cashback_started_at, weekly_program_end,
    save_daily_snapshot, get_daily_snapshot, users_with_daily_snapshot,
    count_daily_snapshots, is_snapshot_run_done, mark_snapshot_run_done,
    get_ichancy_movement_between, has_ichancy_movement_at_or_before,
    is_daily_run_done, begin_daily_run, finish_daily_run,
    record_daily_payout_and_credit, get_daily_payout_totals,
    get_recent_daily_payouts, evaluate_daily_cashback,
    is_cashback_user_choice_enabled, set_cashback_user_choice_enabled,
)
from database import (
    GIFT_MIN_AMOUNT_CEILING, admin_adjust_balance, get_manual_check, set_manual_check, get_user_recharge_overview, get_auto_withdraw, set_auto_withdraw,
    CASHBACK_CHOICES, get_user_cashback_info, get_user_cashback_choice,
    set_user_cashback_choice_once, admin_set_user_cashback_choice,
    count_cashback_choices, bulk_assign_unchosen_cashback,
    get_users_with_ichancy_by_choice,
    get_cashback_program_enabled, set_cashback_program_enabled,
    get_program_started_at, ensure_program_started_at,
    migrate_legacy_cashback_mode, apply_weekly_cashback,
    transfer_gift, get_gift_receive_status, get_gift_max_receives, get_gift_min_amount, set_gift_min_amount, is_gift_enabled, set_gift_enabled,
    set_gift_max_receives, count_gifts_received,
    grant_spin_coupon, get_daily_coupon_users, get_daily_coupon_percent,
    has_daily_coupon_for_day, has_active_daily_coupons, settle_daily_coupons,
    expire_old_daily_coupons, get_daily_payout_loss_since, SPIN_PERCENT_PRIZE_TYPES,
)
import os
import secrets
import string
import json
import asyncio
import time
import math
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone
import re
from decimal import Decimal, ROUND_DOWN
from typing import Optional
import sham_transfer as sham_tx
import daily_report
import sham_incoming_log
import sham_code_health
import sham_payout
import syriatel_admin as sy_admin
import sham_report_provider
import referral_burn
from database import log_bonus_event, user_exists, get_user_overview, zero_user_balance, approve_withdraw_manual, reject_withdraw, get_user_referral_plan, set_user_referral_plan_once, admin_set_user_referral_plan, get_withdraw_fee_percent, calc_withdraw_fee, set_global_withdraw_fee_percent, set_user_withdraw_fee_percent, get_global_withdraw_fee_percent, get_user_withdraw_fee_override, count_withdraw_fee_overrides, user_exists as _user_exists_fee
from html import escape as _esc_html
from ichancy_api2 import (
    init_api, register_player, get_player_by_username,
    deposit_to_player, withdraw_from_player, get_all_players,get_player_balance
)
from payment_api import verify_payment

# ============================
# الإعدادات الأساسية
# ============================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_IDS = [399342811,9080323424]  # ← حط IDs الأدمنية هون

# ============================
# 📊 قناة التقارير (التقرير اليومي 01:00 بتوقيت دمشق)
# ============================
# حط معرّف القناة (يبدأ بـ -100) هون أو بمتغير البيئة REPORTS_CHANNEL_ID.
# إذا تُرك 0 يُرسل التقرير للأدمن بالخاص.
try:
    REPORTS_CHANNEL_ID = int(os.getenv("REPORTS_CHANNEL_ID", "0") or 0)
except ValueError:
    REPORTS_CHANNEL_ID = 0


def _report_primary_targets():
    return [REPORTS_CHANNEL_ID] if REPORTS_CHANNEL_ID else []


def _report_fallback_targets():
    return list(ADMIN_IDS)
#
# دالة مساعدة لإرسال رسالة لجميع الأدمنية
# إذا True: عند عدم ضبط القنوات أو فشل الإرسال إليها يُرسل الإشعار للأدمن بالخاص بدل أن يضيع
# (مثلاً طلب سحب). ضعها False إذا بدك صفر رسائل خاصة نهائياً.
ADMIN_DM_FALLBACK = True


async def notify_admins(bot, text, reply_markup=None, channel="alerts"):
    """
    كل إشعارات الأدمن تذهب للقنوات (لا رسائل خاصة):
      channel="alerts"      → قناة التنبيهات (أمان، أخطاء، تنبيهات...)
      channel="deposits"    → قناة الشحن (شحن، تعويض، إحالات، هدايا، جوائز...)
      channel="withdrawals" → قناة سجل السحب (طلبات السحب ونتائج الدفع)
    إذا كانت القناة المطلوبة غير مضبوطة تُستخدم الأخرى. ترجع True إذا وصلت لقناة.
    """
    if channel == "deposits":
        order = (DEPOSITS_CHANNEL_ID, ALERTS_CHANNEL_ID, WITHDRAWALS_CHANNEL_ID)
    elif channel == "withdrawals":
        order = (WITHDRAWALS_CHANNEL_ID, ALERTS_CHANNEL_ID, DEPOSITS_CHANNEL_ID)
    else:
        order = (ALERTS_CHANNEL_ID, DEPOSITS_CHANNEL_ID, WITHDRAWALS_CHANNEL_ID)

    for chat_id in order:
        if not chat_id:
            continue
        try:
            await bot.send_message(chat_id, text, reply_markup=reply_markup)
            return True
        except Exception:
            logging.getLogger("alerts").exception("admin notification channel send failed")

    if ADMIN_DM_FALLBACK:
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, text, reply_markup=reply_markup)
            except Exception:
                pass

    return False


async def notify_deposits(bot, text, reply_markup=None):
    return await notify_admins(bot, text, reply_markup=reply_markup, channel="deposits")


async def notify_withdrawals(bot, text, reply_markup=None):
    return await notify_admins(bot, text, reply_markup=reply_markup, channel="withdrawals")

# ============================
# 🚨 قناة التنبيهات (سحب / شحن كبير)
# ============================
# حط معرّف القناة (يبدأ بـ -100) بمتغير البيئة ALERTS_CHANNEL_ID (والبوت لازم يكون أدمن بالقناة).
# إذا تُرك 0 أو فشل الإرسال للقناة، يوصل التنبيه للأدمن بالخاص (حتى لا يفوته أي تنبيه).
try:
    ALERTS_CHANNEL_ID = int(os.getenv("ALERTS_CHANNEL_ID", "0") or 0)
except ValueError:
    ALERTS_CHANNEL_ID = 0

# بالليرة السورية الجديدة: أي مبلغ أكبر من الحد يُنبَّه عنه
WITHDRAW_ALERT_THRESHOLD = 20000

# أعلى سعر صرف مقبول للدولار/USDT بالليرة الجديدة (يمنع إدخال رقم بحجم العملة القديمة مثل 13500 بالخطأ)
MAX_RATE_NEW_SYP = 1000

# الحد الأدنى للسحب حسب الطريقة (بالليرة السورية الجديدة). الطريقة غير المذكورة = بلا حد أدنى.
WITHDRAW_MIN_BY_METHOD = {
    "Sham Cash": 1000,
    "Syriatel Cash": 500,
}


# السحب التلقائي (Sham Cash): أي طلب مبلغه أكبر من هذا الحد يذهب لقناة السحب للمراجعة اليدوية.
# 0 = بلا حد. (الحدود والتفعيل داخل وحدة التحويل نفسها — لوحة الأدمن ← تحويل Sham Cash — تنطبق أيضاً.)
AUTO_WITHDRAW_MAX_AMOUNT = WITHDRAW_ALERT_THRESHOLD
AUTO_PAYOUT_ADMIN_ID = 0   # يظهر بالسجلات كـ«🤖 تلقائي»


def _withdraw_blocked_text(balance, method):
    """
    نص الرفض إذا كان رصيد المستخدم لا يكفي أصلاً للسحب بهذه الطريقة
    (أقل من الحد الأدنى للطريقة، أو صفر) — وإلا None.
    """
    minimum = WITHDRAW_MIN_BY_METHOD.get(method, 0)

    if balance >= max(minimum, 1):
        return None

    lines = ["❌ لا تملك رصيداً كافياً للسحب" + (f" عبر {method}" if method else "") + "."]
    if minimum:
        lines.append(f"📌 الحد الأدنى للسحب: {minimum:,} {CURRENCY_LABEL}")
    lines.append(f"💰 رصيدك الحالي: {balance:,} {CURRENCY_LABEL}")

    return "\n".join(lines)
DEPOSIT_ALERT_THRESHOLD = 20000


async def send_alert(bot, text):
    """
    يرسل التنبيه لقناة التنبيهات؛ إن لم تُضبط القناة أو فشل الإرسال إليها
    يُرسل للأدمن بالخاص. ترجع True إذا وصل للقناة. لا ترمي استثناءً أبداً.
    """
    if ALERTS_CHANNEL_ID:
        try:
            await bot.send_message(ALERTS_CHANNEL_ID, text)
            return True
        except Exception:
            logging.getLogger("alerts").exception("alert channel send failed")

    try:
        # قناة التنبيهات غير متاحة: نجرّب قناة الشحن ثم (اختيارياً) الخاص
        await notify_admins(bot, text, channel="deposits")
    except Exception:
        pass

    return False


def _alert_user_line(tg_user, user_id):
    name = f"@{tg_user.username}" if getattr(tg_user, "username", None) else (getattr(tg_user, "first_name", None) or "-")
    return f"👤 المستخدم: {name}\n🆔 المعرف: {user_id}"


# ============================
# 📥 قناة الشحن: سجل الشحن + مكافأة إنشاء الحساب + الإضافة/الخصم اليدوي
# ============================
# حط معرّف القناة (يبدأ بـ -100) بمتغير البيئة DEPOSITS_CHANNEL_ID (والبوت أدمن بالقناة).
# إذا تُرك 0 لا يُرسل شيء (والبوت يكمل شغله طبيعي).
try:
    DEPOSITS_CHANNEL_ID = int(os.getenv("DEPOSITS_CHANNEL_ID", "0") or 0)
except ValueError:
    DEPOSITS_CHANNEL_ID = 0


# ============================
# 📤 قناة سجل السحب: كل طلب سحب (مع أزرار الموافقة/الرفض) + نتائج الدفع
# ============================
# حط معرّفها (يبدأ بـ -100) بمتغير البيئة WITHDRAWALS_CHANNEL_ID (والبوت أدمن بالقناة).
try:
    WITHDRAWALS_CHANNEL_ID = int(os.getenv("WITHDRAWALS_CHANNEL_ID", "0") or 0)
except ValueError:
    WITHDRAWALS_CHANNEL_ID = 0


def _sham_account_lookup(address):
    """
    (اسم صاحب الحساب، رقم الحساب) من apisyria: resource=shamcash & action=lookup_account (قراءة فقط).
    يستخدم حساب البوت المرتبط كمصدر (account_address) والعنوان المُدخل كهدف (target_address).
    رقم الحساب يرجع مخفياً جزئياً من apisyria (مثل ************1234).
    يرجع (None, None) عند أي فشل (حد الطلبات، عنوان غير موجود، انقطاع...) فيظهر «غير متاح» ولا يتعطل السحب.
    """
    try:
        if not sham_tx._api_ready():
            return None, None

        payload = sham_tx._get(
            "shamcash", "lookup_account",
            {"account_address": sham_tx.SHAM_CASH_ADDRESS, "target_address": address},
        )
        info = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(info, dict):
            return None, None

        name = str(info.get("name") or "").strip() or None
        number = str(info.get("account_number") or "").strip() or None
        return name, number
    except Exception:
        logging.getLogger("withdraw_log").exception("sham account lookup failed")
        return None, None


def _withdraw_log_text(label, user_id, method, amount, fee_pct, fee, net, account,
                       ichancy_username, overview, acct_name=None, acct_code=None):
    lines = [f"📤 طلب سحب — {method}", "", f"🆔 ايدي الزبون: {user_id} ({label})",
             f"💰 مبلغ السحب (يُخصم من رصيده): {amount:,} {CURRENCY_LABEL}"]

    if fee > 0:
        lines.append(f"📉 عمولة السحب ({_fmt_pct(fee_pct)}): {fee:,} {CURRENCY_LABEL}")
    else:
        lines.append("📉 عمولة السحب: لا يوجد")

    lines.append(f"💵 الصافي المطلوب تحويله للزبون: {net:,} {CURRENCY_LABEL}")
    lines.append("")

    if method == "Sham Cash":
        lines.append(f"🏦 حساب شام: {account}")
        lines.append(f"👤 اسم الحساب: {acct_name or 'غير متاح'}")
        lines.append(f"🔑 رقم الحساب: {acct_code or 'غير متاح'}")
    elif method == "Syriatel Cash":
        lines.append(f"📱 رقم الزبون: {account}")
    else:
        lines.append(f"🏦 المحفظة: {account}")

    if method != "Syriatel Cash" and ichancy_username:
        lines.append(f"🎮 iChancy: {ichancy_username}")

    recent = overview.get("recent") or []
    lines.append("")
    lines.append("🧾 آخر 4 عمليات شحن:")
    if recent:
        for i, r in enumerate(recent, 1):
            day = str(r["at"] or "")[:10]
            lines.append(f"{i}) {r['amount']:,} {CURRENCY_LABEL} — {r['method']} — {day}")
    else:
        lines.append("لا توجد عمليات شحن سابقة")

    by_method = overview.get("by_method") or {}
    if method != "Syriatel Cash" and by_method:
        ranked = sorted(by_method.items(), key=lambda kv: kv[1]["total"], reverse=True)
        top_name, top = ranked[0]
        lines.append("")
        lines.append(
            f"📊 أكثر طريقة شحن: {top_name} — {top['count']} عملية / {top['total']:,} {CURRENCY_LABEL}"
        )
        for name, v in ranked[1:]:
            lines.append(f"   مقابل {name}: {v['count']} عملية / {v['total']:,} {CURRENCY_LABEL}")

    return "\n".join(lines)


async def send_deposit_log(bot, text):
    """يرسل سجلاً لقناة الشحن. لا ترمي استثناءً أبداً. ترجع True إذا وصل."""
    if not DEPOSITS_CHANNEL_ID:
        return False
    try:
        await bot.send_message(DEPOSITS_CHANNEL_ID, text)
        return True
    except Exception:
        logging.getLogger("deposit_log").exception("deposit channel send failed")
        return False


async def _tg_user_label(bot, user_id, tg_user=None):
    """@username أو الاسم (من رسالة المستخدم نفسها، أو من تيليغرام إذا لم تتوفر)."""
    try:
        if tg_user is None:
            tg_user = await bot.get_chat(user_id)
        if getattr(tg_user, "username", None):
            return f"@{tg_user.username}"
        return getattr(tg_user, "full_name", None) or getattr(tg_user, "first_name", None) or "-"
    except Exception:
        return "-"


# أكواد Syriatel Cash المعروضة للزبائن — أضف كوداً جديداً هنا مع الوقت (بين علامتي تنصيص)
SYRIATEL_CODES = ["17397943"]     # الافتراضي فقط؛ الأكواد الفعلية تُدار من لوحة الأدمن ← 📞 إدارة أكواد Syriatel
sy_admin.DEFAULT_CODES = list(SYRIATEL_CODES)


def _syriatel_codes():
    """الأكواد الحالية المعروضة للزبائن (من الإعدادات، وإلا الافتراضي)."""
    try:
        return sy_admin.get_codes()
    except Exception:
        return list(SYRIATEL_CODES)

# أقل مبلغ شحن بالليرة الجديدة عند التشييك اليدوي (نفس المعروض للزبون)
MANUAL_MIN_SYP = 200

# أقل مبلغ يمكن للزبون شحنه لحسابه في الأيشانسي من رصيد البوت (ل.س جديدة)
ICHANCY_CHARGE_MIN = 100


def _payment_code(payment, method):
    """
    الكود الذي استُلم عليه التحويل: من استجابة الـ API إن وُجد، وإلا الكود الوحيد المعروض.
    (إن وُجد أكثر من كود Syriatel ولم يُرجعه الـ API يُكتب «غير محدد».)
    """
    for attr in ("code", "gsm", "to_gsm", "to", "receiver", "account", "number", "destination"):
        val = getattr(payment, attr, None)
        if val:
            return str(val)

    if method == "Syriatel Cash":
        _codes = _syriatel_codes()
        return _codes[0] if len(_codes) == 1 else "غير محدد"

    return None


def _recharge_log_text(source, label, user_id, method, amount, bonus, total, txid,
                       admin_id=None, original=None, code=None):
    if method == "Syriatel Cash":
        who = f"شحن من المستخدم صاحب الايدي {user_id}"
    else:
        who = f"عملية شحن للزبون صاحب الايدي {user_id}"

    lines = [f"📥 سجل شحن — {method}", "", f"{who} ({label})"]

    if original is not None and original != amount:
        lines.append(f"💰 مقدار الشحن الأصلي: {original:,} {CURRENCY_LABEL}")
        lines.append(f"✏️ مقدار الشحن المعدّل: {amount:,} {CURRENCY_LABEL}")
    else:
        lines.append(f"💰 مقدار الشحن: {amount:,} {CURRENCY_LABEL}")

    if code:
        lines.append(f"📟 الكود المشحون عليه: {code}")

    lines.append(f"🔢 رقم العملية: {txid}")
    lines.append(f"🎁 قيمة البونص: {bonus:,} {CURRENCY_LABEL}")
    lines.append(f"💵 المجموع الواصل للزبون: {total:,} {CURRENCY_LABEL}")
    lines.append(f"⚙️ النوع: {source}" + (f" (الأدمن {admin_id})" if admin_id else ""))
    return "\n".join(lines)


def _in_deposits_channel(query):
    return (
        bool(DEPOSITS_CHANNEL_ID)
        and query.message is not None
        and query.message.chat_id == DEPOSITS_CHANNEL_ID
    )


async def _log_cashback_payouts(bot, kind, rows, header_extra=""):
    """
    يرسل لقناة الشحن قائمة بكل زبون وكم وصله من تعويض (يومي/أسبوعي) في هذه الجولة.
    rows: [(label, user_id, loss, cashback)]. تُجمَّع بأقل عدد رسائل ممكن (حد تيليغرام للقنوات
    ~20 رسالة بالدقيقة)، وتُقسَّم تلقائياً إذا طالت القائمة. لا ترمي استثناءً.
    """
    if not rows or not DEPOSITS_CHANNEL_ID:
        return

    try:
        title = "🌅 التعويض اليومي" if kind == "daily" else "📅 التعويض الأسبوعي"
        total = sum(r[3] for r in rows)
        head = (
            f"{title}{header_extra}\n"
            f"👥 عدد المعوَّضين: {len(rows)}\n"
            f"💰 إجمالي التعويض: {total:,} {CURRENCY_LABEL}\n"
        )
        lines = [
            f"• {label} | 🆔 {uid} | خسارة {loss:,} ← تعويض {cb:,} {CURRENCY_LABEL}"
            for label, uid, loss, cb in rows
        ]

        chunks, cur, size = [], [], 0
        for line in lines:
            if cur and size + len(line) > 3300:
                chunks.append(cur)
                cur, size = [], 0
            cur.append(line)
            size += len(line) + 1
        chunks.append(cur)

        for i, chunk in enumerate(chunks, 1):
            prefix = head if i == 1 else f"{title} (تابع {i}/{len(chunks)})\n"
            await send_deposit_log(bot, prefix + "\n" + "\n".join(chunk))
            if i < len(chunks):
                await asyncio.sleep(3)
    except Exception:
        logging.getLogger("deposit_log").exception("cashback payouts log failed")


def _manual_balance_log_text(mode, admin_id, label, user_id, amount, old, new):
    if mode == "add":
        head = "➕ إضافة رصيد يدوية"
        action = f"✅ تم شحن رصيد للزبون بقيمة {amount:,} {CURRENCY_LABEL}"
    elif mode == "sub":
        head = "➖ خصم رصيد يدوي"
        action = f"⛔ تم خصم {amount:,} {CURRENCY_LABEL} من رصيد الزبون"
    else:
        head = "🧹 تصفير رصيد (خصم يدوي)"
        action = f"⛔ تم تصفير رصيد الزبون (كان {amount:,} {CURRENCY_LABEL})"
    return (
        f"{head}\n\n"
        f"👮 الأدمن: {admin_id}\n"
        f"{action}\n"
        f"👤 الاسم: {label}\n"
        f"🆔 الايدي: {user_id}\n"
        f"💳 الرصيد: {old:,} ← {new:,} {CURRENCY_LABEL}"
    )

WITHDRAW_FEE_PERCENT   = 0.10   # (قديم) الافتراضي فقط — النسبة الفعلية من الإعدادات: لوحة الأدمن ← إدارة نسبة السحب

# ============================
# نسب بونص الشحن (قابلة للتعديل من لوحة الأدمن)
# ============================
# مفاتيح طرق الشحن كما تُخزَّن في قاعدة البيانات (settings)
BONUS_METHOD_LABELS = {
    "syriatel": "🟢 Syriatel Cash",
    "shamcash": "⚡ Sham Cash",
    "usdt": "💰 USDT",
}


def _bonus_key_for_method(method):
    """
    تحويل اسم طريقة الشحن (كما يُخزَّن في recharge_requests) إلى مفتاح
    موحّد يُستخدم لجلب/تعديل نسبة البونص من الإعدادات.
    """
    method = (method or "").strip()

    if method.startswith("Sham Cash"):
        return "shamcash"
    if method.startswith("USDT"):
        return "usdt"
    if method.startswith("Syriatel Cash"):
        return "syriatel"

    return None


# ============================
# الاسترداد الأسبوعي (Cashback)
# ============================
CASHBACK_INTERVAL_SECONDS = 7 * 24 * 60 * 60   # أسبوع
CASHBACK_INITIAL_DELAY_SECONDS = 60            # مهلة بسيطة بعد إقلاع البوت قبل أول فحص

# ============================
# عمولة الإحالة المستمرة (Referral Commission)
# ============================
REFERRAL_CHECK_INTERVAL_SECONDS = 6 * 60 * 60  # فحص كل 6 ساعات لمن استحق التوزيع
REFERRAL_CHECK_INITIAL_DELAY_SECONDS = 90

# ============================
# عجلة الحظ (Spin Wheel)
# ============================
SPIN_WEBAPP_BASE_URL = "https://ehsanabbas791.github.io/La_Casa/spin.html"


_spin_notice_tasks = set()


def _schedule_spin_notice(bot, user_id, text, delay=45):
    """
    رسالة تأكيد للكوبونات بعد delay ثانية من فتح العجلة (حتى لا تُحرق المفاجأة قبل اللف).
    """
    async def _send():
        await asyncio.sleep(delay)
        try:
            await bot.send_message(user_id, text)
        except Exception:
            pass

    task = asyncio.get_running_loop().create_task(_send())
    _spin_notice_tasks.add(task)
    task.add_done_callback(_spin_notice_tasks.discard)


def _build_spin_webapp_url(labels, eligible, land_index=None, remaining_text="", prize_type=None):
    """
    يبني رابط الـ WebApp للعجلة. النتيجة (land_index) والنوع (prize_type)
    تُحدَّد من السيرفر مسبقاً — الواجهة (spin.html) تعرضها فقط، ولا تقرر
    أي جائزة بنفسها. prize_type يُستخدم فقط لعرض رسالة دقيقة بعد اللف
    (كل نوع جائزة له رسالة مختلفة صحيحة).
    """
    payload = {
        "labels": labels,
        "eligible": bool(eligible),
        "land": land_index,
        "remaining": remaining_text,
        "prize_type": prize_type,
    }
    encoded = urllib.parse.quote(json.dumps(payload, ensure_ascii=False))
    return f"{SPIN_WEBAPP_BASE_URL}?data={encoded}"

# ============================
# إعدادات العملة
# ============================
CURRENCY_LABEL   = "ل.س"       # عملة البوت: ليرة سورية
ICHANCY_CURRENCY = "NPS"       # عملة الإيشانسي
LSP_PER_NPS      = 1           # 1 NSP = 1 ل.س (مبلغ البوت = مبلغ iChancy مباشرة)

def ls_to_nps(amount_ls: float) -> float:
    """تحويل ليرة سورية جديدة → NPS (1:1)"""
    return float(amount_ls)

def nps_to_ls(amount_nps: float) -> float:
    """تحويل NPS → ليرة سورية جديدة (1:1)"""
    return float(amount_nps)

# ============================
# شحن Sham Cash بالدولار
# ============================
# سعر الصرف (1$ = كم ل.س) يُخزَّن بقاعدة البيانات ويُعدَّل من لوحة الأدمن:
# get_shamcash_usd_rate() / set_shamcash_usd_rate()
SHAMCASH_USD_MIN = Decimal("2")   # أقل قيمة شحن بالدولار
USD_RATE_PIN_SECONDS = 30 * 60    # مدة تثبيت السعر المعروض للاعب؛ بعدها يُطبَّق السعر الحالي

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# هاش عملية USDT: حروف/أرقام/رموز بسيطة، بدون فراغات، طول معقول
_USDT_HASH_RE = re.compile(r"[A-Za-z0-9_\-.:]{8,128}")


def usd_to_bot_amount(usd: Decimal, rate: int) -> int:
    """يحوّل مبلغ الدولار إلى رصيد البوت (تقريب للأسفل لعدد صحيح)."""
    return int((Decimal(usd) * Decimal(int(rate))).to_integral_value(rounding=ROUND_DOWN))


# ============================
# رسائل الخدمة (حجب أقسام من لوحة الأدمن)
# ============================
_notice_log = logging.getLogger("service_notices")

# زر القائمة الرئيسية -> القسم
SERVICE_BUTTONS = {
    "📥 شحن رصيد في البوت": "recharge",
    "📤 سحب رصيد من البوت": "withdraw",
    "🔼 شحن حساب الأيشانسي": "transfer",
    "🔽 سحب من الأيشانسي": "transfer",
    "🧾  معلومات حسابي": "account",
    "🎰 اللفة المجانية": "spin",
}


def _pending_service_section(user_data) -> Optional[str]:
    """
    القسم الذي يكون المستخدم بمنتصف إدخال بياناته فيه (مبلغ/اسم...).
    ملاحظة: مرحلتا إرسال رقم العملية (payment_txid / usdt_txid) مستثنيتان عمداً،
    لأن المستخدم يكون قد حوّل المال فعلاً ويجب أن يُتحقَّق من عمليته.
    """
    if user_data.get("ichancy_charge") or user_data.get("ichancy_withdraw"):
        return "transfer"
    if user_data.get("withdraw_step"):
        return "withdraw"
    if user_data.get("ichancy_step") == "username":
        return "account"
    if user_data.get("recharge_step") == "usdt_amount":
        return "recharge"
    return None


async def service_blocked(send, user_id, section) -> bool:
    """
    إذا كان القسم محجوباً برسالة خدمة فعّالة: يرسل الرسالة للمستخدم ويرجع True.
    الأدمن غير خاضعين للحجب. عند أي خطأ بقراءة الإعدادات لا نحجب (نفضّل استمرار الخدمة).
    """
    if user_id in ADMIN_IDS:
        return False
    try:
        notice = get_active_service_notice(section)
    except Exception:
        _notice_log.exception("failed to read service notice for %s", section)
        return False
    if not notice:
        return False

    await send(
        f"🚧 تنبيه من الإدارة\n\n{notice}",
        reply_markup=main_menu(user_id)
    )
    return True


# ============================
# عجلة الحظ — حساب الوقت المتبقي
# ============================
def _spin_remaining_text(last_spin_at, cooldown_hours) -> Optional[str]:
    """
    يرجع None إذا كان المستخدم مؤهلاً للفة، وإلا نصاً بالوقت المتبقي (مثال: "5 ساعة و20 دقيقة").
    التوقيت مخزَّن UTC (SQLite CURRENT_TIMESTAMP).
    """
    if not last_spin_at:
        return None
    try:
        last_dt = datetime.strptime(last_spin_at, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None

    elapsed_hours = (datetime.utcnow() - last_dt).total_seconds() / 3600
    if elapsed_hours >= cooldown_hours:
        return None

    remaining = cooldown_hours - elapsed_hours
    rem_h = int(remaining)
    rem_m = int(round((remaining - rem_h) * 60))
    if rem_m == 60:
        rem_h, rem_m = rem_h + 1, 0

    if rem_h > 0:
        return f"{rem_h} ساعة" + (f" و{rem_m} دقيقة" if rem_m > 0 else "")
    return f"{max(rem_m, 1)} دقيقة"


CHANNEL_USERNAME = "@la_casa123"
CHANNEL_LINK = "https://t.me/la_casa123"

# القنوات المطلوب الاشتراك بها لاستخدام البوت (قناة البوت فقط حالياً)
REQUIRED_CHANNELS = [
    {"username": CHANNEL_USERNAME, "link": CHANNEL_LINK, "label": "قناة البوت"},
]

TEXT = """عند الضغط على زر موافقة فأنت توافق على الشروط القائمة ضمن البوت ويحق لك الإعتراض في حال مواجهة أي مشكلة خارجة عن شروط وقوانين البوت

يرجى قراءة هذه الشّروط قبل استخدام البوت لضمان تجربة آمنة وسلسة:

البوت مخصّص لإنشاء الحسابات، والسّحب، والتعبئة الفورية لحسابات موقع Ichancy.

1_منع الحسابات المتعدّدة:
إنشاء أكثر من حساب للشّخص الواحد مخالف للقوانين، وقد يؤدّي إلى حظر الحسابات المرتبطة وتجميد أرصدتها، وذلك بناءاً على سياسة اللّعب النظيف.

2_تبديل طرق الدفع غير مسموح:
لا يُسمح بشحن رصيد وسحبه بغرض التبديل بين وسائل الدفع المختلفة. في حال اكتشاف عملية كهذه، يتم سحب الرّصيد والتّحفظ عليه دون إشعار مسبق.

3_شروط أرباح الإحالات:
تُحتسب أرباح الإحالة فقط بعد تسجيل 3 إحالات نشطة أو أكثر (أي قاموا بالتعبئة الفعلية).

⛔️تنبيه:
أي محاولة للتّحايل أو مخالفة الشروط ستؤدي إلى إيقاف الحساب وتجميد الأرصدة.
"""

# ============================
# وظائف مساعدة
# ============================
def generate_password(length=10):
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))

def generate_gift_code(length=10):
    chars = string.ascii_uppercase + string.digits

    while True:
        code = "GIFT-" + "".join(
            secrets.choice(chars)
            for _ in range(length)
        )

        return code
    
async def is_subscribed_to_channel(bot, channel_username, user_id):
    try:
        member = await asyncio.wait_for(
            bot.get_chat_member(channel_username, user_id),
            timeout=5.0
        )
        return member.status in ["member", "administrator", "creator"]
    except asyncio.TimeoutError:
        return True   # في حال تأخر الاتصال نعامله كمشترك لتجنب التعليق
    except Exception:
        return False


async def get_unsubscribed_channels(bot, user_id):
    """
    ترجع قائمة بالقنوات (من REQUIRED_CHANNELS) التي لم يشترك بها
    المستخدم بعد.
    """
    missing = []
    for channel in REQUIRED_CHANNELS:
        subscribed = await is_subscribed_to_channel(bot, channel["username"], user_id)
        if not subscribed:
            missing.append(channel)
    return missing


async def is_user_subscribed(bot, user_id):
    """
    التحقق من اشتراك المستخدم بكل القنوات المطلوبة (قناة البوت + القناة الشخصية).
    """
    missing = await get_unsubscribed_channels(bot, user_id)
    return len(missing) == 0

# ============================
# لوحات المفاتيح
# ============================
def join_channel_keyboard(missing_channels=None):
    channels = missing_channels if missing_channels else REQUIRED_CHANNELS
    keyboard = [
        [InlineKeyboardButton(f"🔔 الانضمام إلى {ch['label']}", url=ch["link"])]
        for ch in channels
    ]
    keyboard.append([InlineKeyboardButton("✅ تحقّق من الاشتراك", callback_data="check_join")])
    return InlineKeyboardMarkup(keyboard)

def main_menu(user_id=None):
    buttons = [
        ["🧾  معلومات حسابي"],
        ["📥 شحن رصيد في البوت", "📤 سحب رصيد من البوت"],
        ["🔼 شحن حساب الأيشانسي", "🔽 سحب من الأيشانسي"],
        ["🎁 إهداء رصيد","🤝نظام الإحالات"],
        ["💎كود هدية"],
        ["🎰 اللفة المجانية","📈 العروض والبونصات الحالية"],
        ["📞 تواصل معنا"],
        ["📜 السجل", "☁️ الشروحات"],
        ["📱 ichancy apk"],
        ["📌 الشروط والأحكام"],
    ]
    if user_id in ADMIN_IDS:
        buttons.append(["🛠️ لوحة تحكم الأدمن"])

    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def cancel_keyboard():
    return ReplyKeyboardMarkup(
        [["❌ تراجع عن العملية"]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

# ======================================================
# ✅ التعديل: أضفنا زر "تعديل المبلغ" لرسالة الأدمن
# ======================================================
def admin_recharge_keyboard(user_id, amount, recharge_id=None):
    # رقم الطلب يربط الأزرار بطلب محدد (الأزرار القديمة بدونه تبقى تعمل)
    suffix = f"_{recharge_id}" if recharge_id is not None else ""
    keyboard = [
        [
            InlineKeyboardButton("✅ تأكيد الشحن", callback_data=f"approve_{user_id}_{amount}{suffix}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"reject_{user_id}_{amount}{suffix}")
        ],
        [
            InlineKeyboardButton("✏️ تعديل المبلغ", callback_data=f"edit_amount_{user_id}_{amount}{suffix}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def admin_withdraw_keyboard(user_id, amount, account, wid=None):
    """
    أزرار طلب السحب. الحساب داخل callback_data للعرض فقط، فنقصّه ليبقى ضمن حد تيليغرام (64 بايت).
    wid: رقم الطلب — إن وُجد (سحب Sham Cash) يظهر زر «💸 تحويل تلقائي عبر الـ API».
    """
    def _data(prefix):
        base = f"{prefix}_{user_id}_{amount}_"
        room = 64 - len(base.encode("utf-8"))
        acc = str(account)
        while acc and len(acc.encode("utf-8")) > room:
            acc = acc[:-1]
        return base + acc

    approve_label = "✅ تأكيد يدوي (دفعت من التطبيق)" if wid else "✅ تأكيد السحب"
    keyboard = [
        [
            InlineKeyboardButton(approve_label, callback_data=_data("wdapprove")),
            InlineKeyboardButton("❌ رفض", callback_data=_data("wdreject"))
        ]
    ]
    if wid:
        keyboard.append([InlineKeyboardButton("💸 تحويل تلقائي عبر الـ API", callback_data=f"wdpay_{wid}")])
    return InlineKeyboardMarkup(keyboard)

def faq_menu():
    keyboard = [
        [InlineKeyboardButton("❓ ما هو Ichancy؟", callback_data="faq_what")],
        [InlineKeyboardButton("📥 كيف أشحن رصيد؟", callback_data="faq_recharge")],
        [InlineKeyboardButton("📤 كيف أسحب رصيد؟", callback_data="faq_withdraw")],
        [InlineKeyboardButton("🎰 اللفة المجانية", callback_data="faq_spin")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="faq_back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def history_menu():
    keyboard = [
        [
            InlineKeyboardButton("📥 سجل الشحن", callback_data="history_recharge"),
            InlineKeyboardButton("📤 سجل السحب", callback_data="history_withdraw")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# ============================
# معالجات الأوامر الأساسية
# ============================
async def start(update, context):

    user_id = update.effective_user.id

    # =====================================================
    # Referral System
    # =====================================================

    referral_args = context.args

    if referral_args:

        referral_code = referral_args[0]

        if referral_code.startswith("ref_"):

            try:
                referrer_id = int(
                    referral_code.replace("ref_", "")
                )

                # منع إحالة المستخدم لنفسه
                if referrer_id != user_id:

                    # تسجيل الإحالة
                    set_referrer(
                        referred_user_id=user_id,
                        referrer_id=referrer_id
                    )

            except (ValueError, TypeError):
                pass

    # =====================================================
    # باقي كود start الحالي
    # =====================================================

    missing_channels = await get_unsubscribed_channels(context.bot, user_id)

    if missing_channels:

        await update.message.reply_text(
            "يجب الاشتراك بكل القنوات التالية أولاً لاستخدام البوت 🔔",
            reply_markup=join_channel_keyboard(missing_channels)
        )
        return

    user = get_user(user_id)

    if user.get("agreed") == 1:

        context.user_data["agreed"] = True

        await update.message.reply_text(
            "👋 أهلاً بك من جديد!",
            reply_markup=main_menu(user_id)
        )

        if _user_needs_cashback_choice(user_id):
            await send_cashback_choice_prompt(update.message)
        elif user_id not in ADMIN_IDS and get_user_referral_plan(user_id) is None:
            await send_referral_plan_prompt(update.message)

        return

    keyboard = [[
        InlineKeyboardButton(
            "✅ موافق",
            callback_data="agree"
        ),
        InlineKeyboardButton(
            "❌ غير موافق",
            callback_data="disagree"
        )
    ]]

    await update.message.reply_text(
        TEXT,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ============================
# اختيار نظام التعويض (مرة واحدة عند أول استخدام)
# ============================

def _user_needs_cashback_choice(user_id):
    """يُطلب من المستخدم اختيار نظامه فقط إذا فعّل الأدمن ميزة «اختيار المستخدم»."""
    return (
        user_id not in ADMIN_IDS
        and is_cashback_user_choice_enabled()
        and get_user_cashback_choice(user_id) is None
    )


def _cashback_program_users(program):
    """
    [(user_id, ichancy_username)] لمن يسري عليهم برنامج التعويض program ('weekly'/'daily'):
      - اختيار المستخدم مفعّل  → مشتركو هذا البرنامج فقط (من اختاروه).
      - اختيار المستخدم متوقف → كل المستخدمين (غير الأدمن) إن كان هذا البرنامج هو المفعّل وحده.
        إذا كان الاثنان مفعّلين معاً لا نصرف لأحد (حماية من صرف مزدوج).
    """
    if is_cashback_user_choice_enabled():
        return list(get_users_with_ichancy_by_choice(program))

    other = "daily" if program == "weekly" else "weekly"

    if get_cashback_program_enabled(program) and not get_cashback_program_enabled(other):
        return [
            (row[0], row[1])
            for row in get_users_with_ichancy()
            if row[0] not in ADMIN_IDS
        ]

    return []


def _cashback_choice_prompt():
    daily_pct = get_daily_cashback_percent() * 100
    weekly_pct = get_cashback_percent() * 100

    daily_off = "" if get_cashback_program_enabled("daily") else " (متوقف حالياً)"
    weekly_off = "" if get_cashback_program_enabled("weekly") else " (متوقف حالياً)"

    text = (
        "💸 اختر نظام التعويض (Cashback)\n\n"
        "⚠️ الاختيار نهائي ولا يمكن تغييره لاحقاً.\n\n"
        f"🌅 تعويض يومي{daily_off}\n"
        f"   {daily_pct:.1f}% من خسارة اليوم السابق، يُصرف الساعة {DAILY_CASHBACK_HOUR}:00 صباحاً "
        "(توقيت سوريا).\n"
        "   الشرط: رصيد حسابك في iChancy = 0 عند 12:00 ليلاً، والسحوبات ≤ الإيداعات.\n\n"
        f"📅 تعويض أسبوعي{weekly_off}\n"
        f"   {weekly_pct:.1f}% من صافي خسارتك خلال الأسبوع، يُصرف تلقائياً كل 7 أيام.\n\n"
        "اختر ما يناسبك:"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌅 تعويض يومي", callback_data="cb_choose_daily")],
        [InlineKeyboardButton("📅 تعويض أسبوعي", callback_data="cb_choose_weekly")],
    ])

    return text, keyboard


async def send_cashback_choice_prompt(message):
    text, keyboard = _cashback_choice_prompt()
    await message.reply_text(text, reply_markup=keyboard)


async def cashback_choice_callback(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data or ""

    if not is_cashback_user_choice_enabled():
        await _safe_edit_message(query, "ℹ️ اختيار نظام التعويض غير متاح حالياً.")
        if user_id not in ADMIN_IDS and get_user_referral_plan(user_id) is None:
            await send_referral_plan_prompt(query.message)
        return

    if data == "cb_cancel":
        if get_user_cashback_choice(user_id) is None:
            text, keyboard = _cashback_choice_prompt()
            await _safe_edit_message(query, text, reply_markup=keyboard)
        return

    m = re.fullmatch(r"cb_(choose|confirm)_(daily|weekly)", data)

    if not m:
        return

    step, choice = m.group(1), m.group(2)

    existing = get_user_cashback_choice(user_id)

    if existing is not None:
        await _safe_edit_message(
            query,
            f"ℹ️ تم اختيار التعويض {_CHOICE_LABELS[existing]} مسبقاً، ولا يمكن تغييره."
        )
        return

    if step == "choose":
        await _safe_edit_message(
            query,
            f"هل أنت متأكد من اختيار التعويض {_CHOICE_LABELS[choice]}؟\n\n"
            "⚠️ لا يمكنك تغيير اختيارك بعد التأكيد.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأكيد الاختيار", callback_data=f"cb_confirm_{choice}")],
                [InlineKeyboardButton("⬅️ رجوع", callback_data="cb_cancel")],
            ])
        )
        return

    result = set_user_cashback_choice_once(user_id, choice)

    if result["status"] == "already_chosen":
        await _safe_edit_message(
            query,
            f"ℹ️ تم اختيار التعويض {_CHOICE_LABELS[result['choice']]} مسبقاً، ولا يمكن تغييره."
        )
        return

    if not result["ok"]:
        await _safe_edit_message(query, "❌ تعذّر حفظ الاختيار، حاول مرة أخرى.")
        return

    if choice == "daily":
        detail = (
            f"يُصرف التعويض الساعة {DAILY_CASHBACK_HOUR}:00 صباحاً عن خسارة اليوم السابق "
            "(يبدأ احتسابه من منتصف الليل القادم)."
        )
    else:
        detail = "يُصرف التعويض تلقائياً كل 7 أيام من صافي خسارتك."

    await _safe_edit_message(
        query,
        f"✅ تم اختيار التعويض {_CHOICE_LABELS[choice]}.\n\n{detail}"
    )
    await query.message.reply_text("اختر من القائمة:", reply_markup=main_menu(user_id))

    if user_id not in ADMIN_IDS and get_user_referral_plan(user_id) is None:
        await send_referral_plan_prompt(query.message)


# ============================
# 🌳 اختيار نظام الإحالات (مرة واحدة عند أول استخدام) — هرمي أو فردي
# ============================
_RP_CODES = {"hier": "hierarchical", "ind": "individual"}
_RP_SHORT = {v: k for k, v in _RP_CODES.items()}


def _referral_plan_prompt():
    days = referral_burn.CYCLE_DAYS
    need = referral_burn.MIN_ACTIVE_REFERRALS
    text = (
        "🌳 اختر نظام الإحالات الخاص بك\n\n"
        "⚠️ الاختيار نهائي ولا يمكن تغييره لاحقاً.\n\n"
        "🌳 هرمي\n"
        "   • 9% من حرق من يدخل عبر رابطك مباشرة\n"
        "   • 6% من حرق من يدعوهم هو (المستوى الثاني)\n"
        "   • 3% من المستوى الثالث\n"
        "   (بشرط أن يكون هؤلاء أيضاً ضمن النظام الهرمي)\n\n"
        "👤 فردي (منفصل تماماً)\n"
        "   • 10% من حرق كل شخص يدخل عبر رابطك، بأي عدد\n"
        "   • بلا مستويات، ولا علاقة له بأحد غيرك\n\n"
        f"الشروط للنظامين: {need} إحالات نشطة على الأقل (يشحنون ويلعبون)، "
        "وأن يكون حرق شبكتك أعلى من سحبها.\n"
        f"تُحسب الأرباح وتُضاف لرصيدك كل {days} أيام من تاريخ آخر توزيع.\n\n"
        "اختر ما يناسبك:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌳 هرمي", callback_data="rp_choose_hier")],
        [InlineKeyboardButton("👤 فردي", callback_data="rp_choose_ind")],
    ])
    return text, keyboard


async def send_referral_plan_prompt(message):
    text, keyboard = _referral_plan_prompt()
    await message.reply_text(text, reply_markup=keyboard)


async def referral_plan_callback(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data or ""

    if data == "rp_cancel":
        if get_user_referral_plan(user_id) is None:
            text, keyboard = _referral_plan_prompt()
            await _safe_edit_message(query, text, reply_markup=keyboard)
        return

    m = re.fullmatch(r"rp_(choose|confirm)_(hier|ind)", data)
    if not m:
        return

    step, short = m.group(1), m.group(2)
    plan = _RP_CODES[short]
    label = referral_burn.PLAN_LABELS[plan]

    existing = get_user_referral_plan(user_id)
    if existing is not None:
        await _safe_edit_message(
            query, f"ℹ️ تم اختيار النظام {referral_burn.PLAN_LABELS[existing]} مسبقاً، ولا يمكن تغييره."
        )
        return

    if step == "choose":
        await _safe_edit_message(
            query,
            f"هل أنت متأكد من اختيار النظام {label}؟\n\n"
            f"{referral_burn.plan_description(plan)}\n\n"
            "⚠️ لا يمكنك تغيير اختيارك بعد التأكيد.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأكيد الاختيار", callback_data=f"rp_confirm_{short}")],
                [InlineKeyboardButton("⬅️ رجوع", callback_data="rp_cancel")],
            ])
        )
        return

    result = await asyncio.to_thread(set_user_referral_plan_once, user_id, plan)
    if result["status"] == "already_chosen":
        await _safe_edit_message(
            query, f"ℹ️ تم اختيار النظام {referral_burn.PLAN_LABELS[result['plan']]} مسبقاً، ولا يمكن تغييره."
        )
        return
    if not result["ok"]:
        await _safe_edit_message(query, "❌ تعذّر حفظ الاختيار، حاول مرة أخرى.")
        return

    await _safe_edit_message(
        query,
        f"✅ تم اختيار نظام الإحالات {label}.\n\n{referral_burn.plan_description(plan)}\n\n"
        "رابطك الخاص موجود في زر «🤝نظام الإحالات»."
    )
    await query.message.reply_text("اختر من القائمة:", reply_markup=main_menu(user_id))


async def show_balance(update, context):
    user_id = update.effective_user.id
    user = get_user(user_id)
    balance_ls = user['balance']
    balance_nps = ls_to_nps(balance_ls)
    return balance_ls

async def admin_command(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول لهذه الأوامر")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 قائمة المستخدمين", callback_data="admin_users_1")]
    ])
    await update.message.reply_text("🛠️ لوحة تحكم الأدمن", reply_markup=keyboard)

# ============================
# Callback Queries
# ============================
async def terms_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "agree":
        context.user_data["agreed"] = True
        update_user_agreed(user_id, True)
        await query.edit_message_text("✅ تمّت الموافقة على الشروط.")
        await query.message.reply_text("👋 أهلاً بك في Ichancy ⚡\nاختر من القائمة:", reply_markup=main_menu(user_id))

        if _user_needs_cashback_choice(user_id):
            await send_cashback_choice_prompt(query.message)
        elif user_id not in ADMIN_IDS and get_user_referral_plan(user_id) is None:
            await send_referral_plan_prompt(query.message)

    elif query.data == "disagree":
        await query.edit_message_text("❌ لا يمكنك استخدام البوت بدون الموافقة على الشروط.")

async def check_join_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    try:
        missing_channels = await asyncio.wait_for(
            get_unsubscribed_channels(context.bot, user_id),
            timeout=6.0
        )
    except asyncio.TimeoutError:
        await query.answer("⚠️ تأخر الاتصال، حاول مجدداً", show_alert=True)
        return

    if not missing_channels:
        await query.edit_message_text("✅ تم التحقق من اشتراكك بنجاح")
        await query.message.reply_text("اختر من القائمة:", reply_markup=main_menu(user_id))
    else:
        await query.answer("❌ لسا ما اشتركت بكل القنوات المطلوبة", show_alert=True)

async def recharge_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data in ("syriatel_cash", "crypto", "sham_cash", "sham_cash_usd"):
        if await service_blocked(query.message.reply_text, user_id, "recharge"):
            context.user_data.clear()
            return

    if query.data == "syriatel_cash":
        context.user_data["recharge_method"] = "Syriatel Cash"
        context.user_data["recharge_currency"] = "SYP"
        context.user_data["recharge_step"] = "payment_txid"
        text = (
            "🟢 شحن رصيد عبر Syriatel Cash\n\n"
            "ارسل الى احد الارقام التالية بطريقة *التحويل اليدوي*.\n\n"
            "اقل قيمة للشحن هي 200 ليرة سورية جديدة\n\n"
            + "\n".join(f"`{c}`" for c in _syriatel_codes()) + "\n\n\n"
            
            "📌 بعد إتمام التحويل أرسل *رقم العملية فقط*.\n"
            "🤖 سيتم التحقق من العملية تلقائياً.\n\n"
            
        )
        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=cancel_keyboard())

    elif query.data == "sham_cash":
        context.user_data["recharge_method"] = "Sham Cash"
        context.user_data["recharge_currency"] = "SYP"
        context.user_data["recharge_step"] = "payment_txid"
        text = (
            "عملائنا المحترمين …\n\n"
            " … أقل قيمة إيداع 200 ل.س جديدة …\n"
            "شكراً لثقتكم 💯\n\n"
            "⚡ ارسل الى العنوان …\n"
            "♦️ احسان عباس عباس ♦️\n\n"
            "من فضلك لا تقم بأخفاء هوية حساب شام كاش الذي تقوم بالشحن منه …\n"
            f"*1 ShamCash USD = {get_shamcash_usd_rate():,}*\n\n"
            f"`{sham_code_health.get_code()}`\n\n"
            "📌 بعد إتمام التحويل أرسل *رقم العملية فقط*.\n"
            "🤖 سيتم التحقق من العملية تلقائياً.\n\n"
        )
        try:
            with open("shamcash_qr.png", "rb") as photo:
                await query.message.reply_photo(photo=photo, caption=text, parse_mode="Markdown", reply_markup=cancel_keyboard())
        except Exception:
            await query.message.reply_text(text, parse_mode="Markdown", reply_markup=cancel_keyboard())

    elif query.data == "sham_cash_usd":
        usd_rate = get_shamcash_usd_rate()
        bonus_percent = get_bonus_percent("shamcash")
        min_ls = usd_to_bot_amount(SHAMCASH_USD_MIN, usd_rate)

        # نثبّت السعر لحظة عرض الشاشة حتى يُطبَّق نفس السعر المعروض للمستخدم
        context.user_data["recharge_method"] = "Sham Cash"
        context.user_data["recharge_currency"] = "USD"
        context.user_data["recharge_usd_rate"] = usd_rate
        context.user_data["recharge_usd_rate_at"] = time.time()
        context.user_data["recharge_step"] = "payment_txid"

        text = (
            "⚡ شحن رصيد عبر Sham Cash (دولار)\n\n"
            f"💱 سعر الصرف: 1 USD = {usd_rate:,} ل.س\n"
            f"❗️ أقل قيمة للشحن: {SHAMCASH_USD_MIN:g} دولار (= {min_ls:,} ل.س)\n"
            + (f"🎁 بونص {bonus_percent * 100:.0f}% على كل شحنة!\n" if bonus_percent > 0 else "")
            + "\n⚡ حوّل المبلغ بالدولار (USD) من داخل تطبيق شام كاش إلى العنوان:\n"
            "♦️ احسان عباس عباس ♦️\n"
            f"`{sham_code_health.get_code()}`\n\n"
            "⚠️ يجب أن يكون التحويل بعملة الدولار وليس بالليرة السورية.\n"
            "📌 بعد إتمام التحويل أرسل *رقم العملية فقط*.\n"
            "🤖 سيتم التحقق من العملية تلقائياً، ويُضاف الرصيد حسب المبلغ الفعلي المحوَّل."
        )
        try:
            with open("shamcash_qr.png", "rb") as photo:
                await query.message.reply_photo(photo=photo, caption=text, parse_mode="Markdown", reply_markup=cancel_keyboard())
        except Exception:
            await query.message.reply_text(text, parse_mode="Markdown", reply_markup=cancel_keyboard())

    elif query.data == "crypto":
        context.user_data["recharge_method"] = "USDT"
        context.user_data["recharge_step"] = "usdt_amount"
        USDT_WALLET = "0x022c4d1e65fa15af2a383981b38617fc5e7dcf7f"
        rate = get_usdt_rate()
        usdt_bonus_percent = get_bonus_percent("usdt")
        text = (
            "💰 شحن رصيد عبر USDT (ERC20/BEP20)\n\n"
            f"🎁 بونص {usdt_bonus_percent * 100:.0f}% على كل شحنة!\n\n"
            "📋 عنوان المحفظة (ERC20/BEP20):\n"
            f"`{USDT_WALLET}`\n\n"
            f"💱 سعر الصرف: 1 USDT = {rate:,} ل.س\n\n"
            "⚠️ تأكد من إرسال على شبكة ERC20 أو BEP20 فقط\n\n"
            "❗️ أدخل قيمة الشحن بالدولار (USDT):"
        )
        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=cancel_keyboard())

    elif query.data == "back_to_menu":
        await query.message.reply_text("👋 عدنا للقائمة الرئيسية:", reply_markup=main_menu(user_id))

async def withdraw_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data.startswith("wd_"):
        if await service_blocked(query.message.reply_text, user_id, "withdraw"):
            context.user_data.clear()
            return

        method_names = {
            "wd_syriatel": "Syriatel Cash",
            "wd_sham": "Sham Cash",
            "wd_usdt": "USDT"
        }
        _method = method_names.get(query.data, query.data)

        _u = get_user(user_id)
        _blocked = _withdraw_blocked_text(int(_u["balance"]) if _u else 0, _method)
        if _blocked:
            context.user_data.clear()
            await query.edit_message_text(_blocked)
            await query.message.reply_text("👋 عدنا للقائمة الرئيسية:", reply_markup=main_menu(user_id))
            return

        context.user_data["withdraw_method"] = _method
        context.user_data["withdraw_step"] = "amount"
        _notice, _example = _fee_notice(get_withdraw_fee_percent(user_id))
        _min = WITHDRAW_MIN_BY_METHOD.get(context.user_data["withdraw_method"], 0)
        _min_line = f"📌 الحد الأدنى للسحب: {_min:,} {CURRENCY_LABEL}\n\n" if _min else ""
        await query.edit_message_text(
            f"{_notice}\n\n"
            f"{_example}\n\n"
            f"{_min_line}"
            f"💰 أرسل مبلغ السحب بالليرة السورية:"
        )
        await query.message.reply_text("👇 أدخل المبلغ:", reply_markup=cancel_keyboard())

    elif query.data == "back_to_menu":
        await query.message.reply_text("👋 عدنا للقائمة الرئيسية:", reply_markup=main_menu(user_id))

_ALREADY_PROCESSED_TEXT = "⚠️ هذا الطلب تمت معالجته مسبقاً (تأكيد أو رفض) — لم يتم أي تغيير على الرصيد."


async def _safe_edit(query, text):
    """تعديل رسالة الأدمن دون أن يفشل باقي التنفيذ لو رفض تيليغرام التعديل."""
    try:
        await query.edit_message_text(text)
    except Exception:
        pass


# =====================================================
# 🔐 حد محاولات التحقق الفاشلة من رقم العملية
# أرقام العمليات ليست سرية، فبدونه يمكن تخمينها بالتتابع
# للعثور على تحويلات زبائن آخرين.
# =====================================================
_TXID_MAX_FAILS = 6
_TXID_FAIL_WINDOW = 15 * 60
_TXID_LOCK_SECONDS = 30 * 60
_txid_fail_state = {}


def _txid_locked_seconds(user_id):
    st = _txid_fail_state.get(user_id)
    if not st:
        return 0
    now = time.time()
    left = st.get("locked_until", 0) - now
    return int(left) if left > 0 else 0


def _txid_record_fail(user_id):
    now = time.time()
    st = _txid_fail_state.setdefault(user_id, {"fails": [], "locked_until": 0})
    st["fails"] = [t for t in st["fails"] if now - t < _TXID_FAIL_WINDOW]
    st["fails"].append(now)
    if len(st["fails"]) >= _TXID_MAX_FAILS:
        st["locked_until"] = now + _TXID_LOCK_SECONDS
        st["fails"] = []


def _txid_record_success(user_id):
    _txid_fail_state.pop(user_id, None)



async def admin_recharge_callback(update, context):
    query = update.callback_query
    await query.answer()

    # حماية: الأدمن فقط
    if query.from_user.id not in ADMIN_IDS:
        return

    # callback_data: approve|reject _ <user_id> _ <amount> [_ <recharge_id>]
    parts = query.data.split("_")
    action = parts[0]
    user_id = int(parts[1])
    amount = int(float(parts[2]))
    recharge_id = int(parts[3]) if len(parts) > 3 else None

    pending = get_pending_recharge(user_id, amount, recharge_id)

    if pending is None:
        await _safe_edit(query, _ALREADY_PROCESSED_TEXT)
        return

    if action == "approve":
        method = pending["method"]
        bonus_key = _bonus_key_for_method(method)
        bonus_percent = get_bonus_percent(bonus_key) if bonus_key else 0.0

        # ذرّية: الانتقال pending→approved وإضافة الرصيد في عملية واحدة، تنجح مرة واحدة فقط
        result = approve_pending_recharge(pending["id"], bonus_percent=bonus_percent)

        if not result["ok"]:
            await _safe_edit(query, _ALREADY_PROCESSED_TEXT)
            return

        amount = result["amount"]
        bonus = result["bonus"]
        final_amount = result["total"]

        # =====================================================
        # تفعيل الإحالة (حالة نشطة) + تسجيل عمولة الإحالة المستمرة
        # =====================================================
        activate_referral_after_recharge(referred_user_id=user_id, reward_amount=0)

        referrer_id = get_referrer(user_id)
        if referrer_id:
            commission_percent = get_referral_commission_percent()
            if commission_percent > 0:
                commission_amount = int(final_amount * commission_percent)
                if commission_amount > 0:
                    log_referral_commission(referrer_id, user_id, final_amount, commission_amount)

        bonus_text = f"\n🎁 بونص: +{bonus:,} {CURRENCY_LABEL}" if bonus > 0 else ""
        if result.get("coupon_percent"):
            bonus_text += f"\n🎟️ يشمل كوبون عجلة الحظ: +{result['coupon_percent']}%"

        in_ch = _in_deposits_channel(query)

        if in_ch:
            # داخل قناة الشحن: نُبقي تفاصيل الطلب ونضيف نتيجة الموافقة تحتها
            await _safe_edit(
                query,
                (query.message.text or "")
                + f"\n\n✅ تمت الموافقة (الأدمن {query.from_user.id})\n"
                f"💰 المبلغ: {amount:,} {CURRENCY_LABEL}{bonus_text}\n"
                f"💵 المجموع المضاف للزبون: {final_amount:,} {CURRENCY_LABEL}"
            )
        else:
            await _safe_edit(
                query,
                f"✅ تم شحن رصيد البوت\n\n"
                f"👤 المستخدم: {user_id}\n"
                f"💰 المبلغ الأصلي: {amount:,} {CURRENCY_LABEL}{bonus_text}\n"
                f"💵 المجموع المضاف: {final_amount:,} {CURRENCY_LABEL}"
            )

        bonus_line = f"🎁 بونص {method}: +{bonus:,} {CURRENCY_LABEL}\n" if bonus > 0 else ""
        if result.get("coupon_percent"):
            bonus_line += f"🎟️ يشمل كوبون عجلة الحظ: +{result['coupon_percent']}%\n"
        try:
            await context.bot.send_message(
                user_id,
                f"🎉 تم شحن رصيد البوت بنجاح!\n\n"
                f"💰 المبلغ الأصلي: {amount:,} {CURRENCY_LABEL}\n"
                f"{bonus_line}"
                f"💵 رصيدك داخل البوت: {final_amount:,} {CURRENCY_LABEL}\n"
                f"⚡ يمكنك الآن شحن حساب iChancy من رصيد البوت"
            )
        except Exception:
            pass

        try:
            _txid = pending["transaction_id"]
        except Exception:
            _txid = "-"
        if not in_ch:
            _dl = await _tg_user_label(context.bot, user_id)
            await send_deposit_log(
                context.bot,
                _recharge_log_text("موافقة أدمن", _dl, user_id, method, amount, bonus, final_amount, _txid,
                                   admin_id=query.from_user.id)
            )

        if amount > DEPOSIT_ALERT_THRESHOLD:
            await send_alert(
                context.bot,
                "🚨 تنبيه: عملية شحن كبيرة (موافقة أدمن)\n\n"
                f"🆔 المعرف: {user_id}\n"
                f"💳 الطريقة: {method}\n"
                f"💰 المبلغ: {amount:,} {CURRENCY_LABEL} (الحد: {DEPOSIT_ALERT_THRESHOLD:,})\n"
                f"🎁 البونص: {bonus:,} {CURRENCY_LABEL}\n"
                f"💵 المجموع المضاف: {final_amount:,} {CURRENCY_LABEL}\n"
                f"✅ وافق: {query.from_user.id}"
            )

    elif action == "reject":
        if not reject_pending_recharge(pending["id"]):
            await _safe_edit(query, _ALREADY_PROCESSED_TEXT)
            return

        if _in_deposits_channel(query):
            await _safe_edit(
                query,
                (query.message.text or "") + f"\n\n❌ تم الرفض (الأدمن {query.from_user.id})"
            )
        else:
            await _safe_edit(query, f"❌ تم رفض طلب الشحن للمستخدم {user_id}")

        try:
            _rtx = pending["transaction_id"]
        except Exception:
            _rtx = None
        try:
            await context.bot.send_message(
                user_id,
                "❌ تم رفض طلب الشحن الخاص بك\n"
                + (f"🔢 رقم العملية: {_rtx}\n" if _rtx else "")
                + "\nلم نتمكن من التأكد من وصول هذا التحويل (العملية غير صحيحة).\n"
                "إذا كنت متأكداً من التحويل، تواصل مع الدعم وأرسل صورة عن العملية."
            )
        except Exception:
            pass


async def _finish_manual_recharge(message, context, user_id, tg_user, code):
    """ينشئ طلب الشحن المعلّق (تشييك يدوي) ويرسله لقناة الشحن مع أزرار موافقة/رفض."""
    ud = context.user_data
    method = ud.get("recharge_method")
    txid = ud.get("manual_txid")
    ls_amount = ud.get("manual_ls_amount")
    shown = ud.get("manual_amount_text") or f"{ls_amount:,} {CURRENCY_LABEL}"

    if not (method and txid and ls_amount):
        ud.clear()
        await message.reply_text("❌ انتهت صلاحية الطلب. ابدأ عملية الشحن من جديد.", reply_markup=main_menu(user_id))
        return

    created = await asyncio.to_thread(create_pending_recharge_once, user_id, ls_amount, txid, method)

    if not created["ok"]:
        ud.clear()
        await message.reply_text(
            "❌ رقم العملية هذا مستخدم مسبقاً ولا يمكن استخدامه مرة ثانية.",
            reply_markup=main_menu(user_id)
        )
        return

    recharge_id = created["recharge_id"]
    bonus_key = _bonus_key_for_method(method)
    bonus_percent = get_bonus_percent(bonus_key) if bonus_key else 0.0
    bonus = int(ls_amount * bonus_percent)
    label = await _tg_user_label(context.bot, user_id, tg_user)

    lines = [
        "🧾 طلب شحن — بانتظار التشييك اليدوي",
        "",
        f"👤 المستخدم: {label}",
        f"🆔 الايدي: {user_id}",
        f"💳 الطريقة: {method}",
    ]
    if code:
        lines.append(f"📟 الكود المشحون عليه: {code}")
    lines += [
        f"💰 المبلغ: {shown}",
        f"🎁 البونص المتوقع: {bonus:,} {CURRENCY_LABEL}",
        f"🔢 رقم العملية: {txid}",
        "",
        "⚠️ تحقق من التطبيق ثم اختر:",
    ]
    admin_text = "\n".join(lines)
    kb = admin_recharge_keyboard(user_id, ls_amount, recharge_id)

    await notify_deposits(context.bot, admin_text, reply_markup=kb)

    ud.clear()
    await message.reply_text(
        "✅ تم استلام طلب الشحن!\n\n"
        f"💳 الطريقة: {method}\n"
        f"💰 المبلغ: {shown}\n"
        f"🔢 رقم العملية: {txid}\n\n"
        "⏳ بانتظار التحقق اليدوي من الإدارة، وسيصلك إشعار فور المراجعة.",
        reply_markup=main_menu(user_id)
    )


async def manual_check_code_callback(update, context):
    """اختيار الكود (Syriatel) عند وجود أكثر من كود، أثناء التشييك اليدوي."""
    query = update.callback_query
    await query.answer()

    if context.user_data.get("recharge_step") != "manual_code":
        await query.edit_message_text("⌛ انتهت صلاحية هذا الطلب. ابدأ عملية الشحن من جديد.")
        return

    idx = int(query.data.split("_", 1)[1])
    _codes = _syriatel_codes()
    if not 0 <= idx < len(_codes):
        return

    await query.edit_message_text(f"📟 الكود: {_codes[idx]}")
    await _finish_manual_recharge(
        query.message, context, query.from_user.id, query.from_user, _codes[idx]
    )


def _manual_check_menu_content():
    sy = get_manual_check("syriatel")
    sh = get_manual_check("shamcash")
    ch = "✅ مضبوطة" if DEPOSITS_CHANNEL_ID else "⚠️ غير مضبوطة (الطلبات ستصل للأدمن بالخاص)"
    text = (
        "🧾 التشييك اليدوي للشحن\n\n"
        "عند التفعيل لا يتم التحقق من العملية عبر الـ API: يرسل الزبون رقم العملية والمبلغ، "
        "فيصل الطلب لقناة الشحن مع أزرار موافقة / رفض.\n\n"
        f"🟢 Syriatel Cash: {'✅ مفعّل (يدوي)' if sy else '⛔ متوقف (تلقائي)'}\n"
        f"⚡ Sham Cash: {'✅ مفعّل (يدوي)' if sh else '⛔ متوقف (تلقائي)'}\n\n"
        f"📥 قناة الشحن: {ch}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(("⛔ إيقاف" if sy else "✅ تفعيل") + " التشييك اليدوي — Syriatel", callback_data="admin_mc_toggle_syriatel")],
        [InlineKeyboardButton(("⛔ إيقاف" if sh else "✅ تفعيل") + " التشييك اليدوي — Sham", callback_data="admin_mc_toggle_shamcash")],
        [InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel")],
    ])
    return text, kb


async def admin_manual_check_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    if query.data.startswith("admin_mc_toggle_"):
        key = query.data.rsplit("_", 1)[1]
        if key in ("syriatel", "shamcash"):
            new_state = not get_manual_check(key)
            set_manual_check(key, new_state)
            name = "Syriatel Cash" if key == "syriatel" else "Sham Cash"
            await send_deposit_log(
                context.bot,
                f"🧾 التشييك اليدوي لـ {name}: {'✅ تم التفعيل' if new_state else '⛔ تم الإيقاف'} "
                f"(الأدمن {query.from_user.id})"
            )

    text, kb = _manual_check_menu_content()
    await _safe_edit_message(query, text, reply_markup=kb)


async def admin_alert_test_callback(update, context):
    """زر لوحة الأدمن «اختبار قناة التنبيهات»: يرسل تنبيهاً تجريبياً ويبيّن هل وصل للقناة."""
    query = update.callback_query

    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return

    await query.answer("جاري إرسال التنبيه التجريبي…")

    delivered = await send_alert(context.bot, "🧪 تنبيه تجريبي — قناة التنبيهات تعمل ✅")

    if delivered:
        result = "✅ وصل التنبيه التجريبي لقناة التنبيهات."
    elif not ALERTS_CHANNEL_ID:
        result = "⚠️ قناة التنبيهات غير مضبوطة (ALERTS_CHANNEL_ID). أرسلت التجربة لك بالخاص."
    else:
        result = (
            "❌ تعذّر الإرسال للقناة. تأكد أن البوت أدمن فيها وأن المعرّف صحيح (يبدأ بـ -100). "
            "أرسلت التجربة لك بالخاص."
        )

    await query.message.reply_text(result)


async def admin_withdraw_log_test_callback(update, context):
    """زر لوحة الأدمن «اختبار قناة سجل السحب»."""
    query = update.callback_query

    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return

    await query.answer("جاري إرسال رسالة تجريبية…")

    if not WITHDRAWALS_CHANNEL_ID:
        result = "⚠️ قناة سجل السحب غير مضبوطة (WITHDRAWALS_CHANNEL_ID)."
    else:
        try:
            await context.bot.send_message(WITHDRAWALS_CHANNEL_ID, "🧪 رسالة تجريبية — قناة سجل السحب تعمل ✅")
            result = "✅ وصلت الرسالة التجريبية لقناة سجل السحب."
        except Exception:
            logging.getLogger("withdraw_log").exception("withdraw channel test failed")
            result = "❌ تعذّر الإرسال للقناة. تأكد أن البوت أدمن فيها وأن المعرّف صحيح (يبدأ بـ -100)."

    await query.message.reply_text(result)


async def admin_deposit_log_test_callback(update, context):
    """زر لوحة الأدمن «اختبار قناة الشحن»: يرسل رسالة تجريبية للقناة ويبيّن النتيجة."""
    query = update.callback_query

    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return

    await query.answer("جاري إرسال رسالة تجريبية…")

    if not DEPOSITS_CHANNEL_ID:
        result = "⚠️ قناة الشحن غير مضبوطة (DEPOSITS_CHANNEL_ID)."
    elif await send_deposit_log(context.bot, "🧪 رسالة تجريبية — قناة الشحن تعمل ✅"):
        result = "✅ وصلت الرسالة التجريبية لقناة الشحن."
    else:
        result = "❌ تعذّر الإرسال للقناة. تأكد أن البوت أدمن فيها وأن المعرّف صحيح (يبدأ بـ -100)."

    await query.message.reply_text(result)


async def handle_callback_query(update, context):

    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data in ("ichancy_charge_all", "ichancy_charge_custom",
                "ichancy_withdraw_all", "ichancy_withdraw_custom"):
        if await service_blocked(query.message.reply_text, user_id, "transfer"):
            context.user_data.clear()
            return

    # ==========================================
    # 💰 شحن كامل الرصيد
    # ==========================================
    if data == "ichancy_charge_all":

        user = get_user(user_id)

        if not user:
            await query.message.reply_text(
                "❌ لم يتم العثور على حسابك."
            )
            return

        balance = user["balance"]

        if balance <= 0:
            await query.message.reply_text(
                "❌ رصيدك في البوت غير كافي للشحن.",
                reply_markup=main_menu(user_id)
            )
            return

        if balance < ICHANCY_CHARGE_MIN:
            await query.message.reply_text(
                f"❌ لا يمكنك شحن حسابك في Ichancy بأقل من {ICHANCY_CHARGE_MIN:,} {CURRENCY_LABEL}.\n\n"
                f"💳 رصيدك الحالي: {balance:,} {CURRENCY_LABEL}",
                reply_markup=main_menu(user_id)
            )
            return

        ichancy_username = user.get("ichancy_username")

        if not ichancy_username:
            await query.message.reply_text(
                "❌ لا يوجد حساب Ichancy مربوط.\n"
                "أنشئ حساباً أولاً من القائمة.",
                reply_markup=main_menu(user_id)
            )
            return

        player = get_player_by_username(ichancy_username)

        if not player:
            await query.message.reply_text(
                "❌ فشل الوصول لحساب Ichancy، تواصل مع الدعم.",
                reply_markup=main_menu(user_id)
            )
            return

        await query.message.reply_text(
            f"⏳ جاري شحن كامل رصيدك...\n\n"
            f"💰 المبلغ: {balance:,} {CURRENCY_LABEL}"
        )

        # تحويل الليرة إلى NSP
        nps_amount = ls_to_nps(balance)

        # تنفيذ الشحن في Ichancy
        success = deposit_to_player(
            player.get("playerId"),
            nps_amount,
            comment="Full balance charge from bot wallet"
        )

        if success:

            # خصم الرصيد فقط بعد نجاح الشحن
            update_balance(user_id, -balance)
            log_ichancy_transaction(user_id, "in", nps_amount)

            await query.message.reply_text(
                f"✅ تم شحن كامل رصيدك بنجاح!\n\n"
                f"💰 المبلغ المشحون: {balance:,} {CURRENCY_LABEL}\n"
                f"💳 رصيد البوت المتبقي: 0 {CURRENCY_LABEL}",
                reply_markup=main_menu(user_id)
            )

        else:

            await query.message.reply_text(
                "❌ فشل شحن حسابك في Ichancy، حاول لاحقاً أو تواصل مع الدعم.\n"
                "لم يتم خصم أي رصيد من حسابك.",
                reply_markup=main_menu(user_id)
            )

        return
    


    # ==========================================
    # ✏️ إدخال مبلغ محدد
    # ==========================================
    elif data == "ichancy_charge_custom":

        user = get_user(user_id)

        if not user:
            await query.message.reply_text(
                "❌ لم يتم العثور على حسابك."
            )
            return

        context.user_data["ichancy_charge"] = True

        await query.message.reply_text(
            f"💰 أدخل المبلغ بالليرة السورية الذي تريد شحنه:\n\n"
            f"📌 الحد الأدنى للشحن: {ICHANCY_CHARGE_MIN:,} {CURRENCY_LABEL}\n"
            f"💳 رصيدك الحالي: "
            f"{user['balance']:,} {CURRENCY_LABEL}",
            reply_markup=cancel_keyboard()
        )

        return


    # ==========================================
    # ❌ إلغاء شحن Ichancy
    # ==========================================
    elif data == "cancel_ichancy_charge":

        context.user_data["ichancy_charge"] = None
        context.user_data["ichancy_charge_amount"] = None

        await query.message.reply_text(
            "❌ تم إلغاء عملية الشحن.",
            reply_markup=main_menu(user_id)
        )

        return
    # ==================================================
    # 📤 سحب كامل رصيد Ichancy إلى البوت
    # ==================================================
    elif data == "ichancy_withdraw_all":

        user = get_user(user_id)

        if not user:
            await query.message.reply_text(
                "❌ لم يتم العثور على حسابك."
            )
            return

        ichancy_username = user.get("ichancy_username")

        if not ichancy_username:
            await query.message.reply_text(
                "❌ لا يوجد حساب Ichancy مربوط.",
                reply_markup=main_menu(user_id)
            )
            return

        player = get_player_by_username(ichancy_username)

        if not player:
            await query.message.reply_text(
                "❌ فشل الوصول إلى حساب Ichancy.",
                reply_markup=main_menu(user_id)
            )
            return

        player_id = player.get("playerId")

        if not player_id:
            await query.message.reply_text(
                "❌ لم يتم العثور على Player ID للحساب.",
                reply_markup=main_menu(user_id)
            )
            return

        # جلب الرصيد الحقيقي من Ichancy
        current_balance_nps = get_player_balance(player_id)

        if current_balance_nps is None:
            await query.message.reply_text(
                "❌ تعذر معرفة رصيد حساب Ichancy.",
                reply_markup=main_menu(user_id)
            )
            return

        if current_balance_nps <= 0:
            await query.message.reply_text(
                "❌ لا يوجد رصيد متاح للسحب في حساب Ichancy.",
                reply_markup=main_menu(user_id)
            )
            return

        # تحويل NSP إلى الليرة السورية
        amount = nps_to_ls(current_balance_nps)

        if amount <= 0:
            await query.message.reply_text(
                "❌ الرصيد غير كافٍ للسحب.",
                reply_markup=main_menu(user_id)
            )
            return

        await query.message.reply_text(
            f"⏳ جاري سحب كامل رصيدك من Ichancy...\n\n"
            f"💰 المبلغ: {amount:,} {CURRENCY_LABEL}"
        )

        # تنفيذ السحب
        api_success = withdraw_from_player(
            player_id,
            current_balance_nps,
            comment="Full balance withdrawal to bot wallet"
        )

        if not api_success:
            await query.message.reply_text(
                "❌ فشل السحب.\n"
                "انتظر نصف ساعة لتتمكن من القيام بعملية سحب جديدة",
                reply_markup=main_menu(user_id)
            )
            return

        # إضافة الرصيد للبوت بعد نجاح API فقط
        update_balance(user_id, amount)
        log_ichancy_transaction(user_id, "out", current_balance_nps)

        updated_user = get_user(user_id)
        new_balance = updated_user.get("balance", 0)

        await query.message.reply_text(
            f"✅ تم سحب كامل رصيدك من Ichancy بنجاح!\n\n"
            f"💰 المبلغ المضاف لرصيد البوت: "
            f"{amount:,} {CURRENCY_LABEL}\n"
            f"💳 رصيدك الجديد في البوت: "
            f"{new_balance:,} {CURRENCY_LABEL}",
            reply_markup=main_menu(user_id)
        )

        return


    # ==================================================
    # ✏️ سحب مبلغ محدد
    # ==================================================
    elif data == "ichancy_withdraw_custom":

        user = get_user(user_id)

        if not user:
            await query.message.reply_text(
                "❌ لم يتم العثور على حسابك."
            )
            return

        context.user_data["ichancy_withdraw"] = True

        await query.message.reply_text(
            "💰 أدخل المبلغ بالليرة السورية الذي تريد "
            "سحبه من Ichancy إلى رصيد البوت:",
            reply_markup=cancel_keyboard()
        )

        return


    # ==================================================
    # ❌ إلغاء شحن Ichancy
    # ==================================================
    elif data == "cancel_ichancy_charge":

        context.user_data["ichancy_charge"] = None

        await query.message.reply_text(
            "❌ تم إلغاء عملية الشحن.",
            reply_markup=main_menu(user_id)
        )

        return


    # ==================================================
    # ❌ إلغاء سحب Ichancy
    # ==================================================
    elif data == "cancel_ichancy_withdraw":

        context.user_data["ichancy_withdraw"] = None

        await query.message.reply_text(
            "❌ تم إلغاء عملية السحب.",
            reply_markup=main_menu(user_id)
        )

        return
########################################################
# واجهة الأدمن الخيار الخاص بالبونص
async def admin_welcome_bonus_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.data == "admin_welcome_bonus":
        enabled, amount = get_welcome_bonus_config()
        status_str = "🟢 مفعل" if enabled else "🔴 معطل"

        text = (
            f"⚙️ **إعدادات البونص الترحيبي**\n\n"
            f"الحالة الحالية: {status_str}\n"
            f"قيمة البونص: {amount:,} {CURRENCY_LABEL}\n"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 تغيير الحالة (تفعيل/تعطيل)", callback_data="toggle_welcome_bonus")],
            [InlineKeyboardButton("✏️️ تعديل قيمة البونص", callback_data="set_welcome_bonus_amount")],
        ])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

    elif query.data == "toggle_welcome_bonus":
        enabled, amount = get_welcome_bonus_config()
        set_welcome_bonus_config(not enabled, amount)
        await admin_welcome_bonus_callback(update, context)

    elif query.data == "set_welcome_bonus_amount":
        context.user_data["admin_step"] = "set_welcome_bonus"
        await query.message.reply_text("✏️ أدخل القيمة الجديدة للبونص الترحيبي (بالأرقام):", reply_markup=cancel_keyboard())
# ======================================================
# ✅ التعديل: معالج زر "تعديل المبلغ"
# ======================================================
async def admin_edit_amount_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    # callback_data: edit_amount_{user_id}_{original_amount}[_{recharge_id}]
    parts = query.data.split("_")
    target_user_id = int(parts[2])
    original_amount = int(float(parts[3]))
    recharge_id = int(parts[4]) if len(parts) > 4 else None

    pending = get_pending_recharge(target_user_id, original_amount, recharge_id)
    if pending is None:
        await query.message.reply_text(_ALREADY_PROCESSED_TEXT)
        return

    # حفظ الحالة في user_data الخاص بالأدمن
    context.user_data["admin_edit_step"] = "new_amount"
    context.user_data["admin_edit_target_user"] = target_user_id
    context.user_data["admin_edit_original_amount"] = original_amount
    context.user_data["admin_edit_recharge_id"] = pending["id"]
    context.user_data["admin_edit_msg"] = (
        query.message.chat_id, query.message.message_id, query.message.text or ""
    )

    # نرسل للأدمن بالخاص (حتى لو ضغط الزر من داخل قناة الشحن)
    await context.bot.send_message(
        query.from_user.id,
        f"✏️ تعديل مبلغ الشحن\n\n"
        f"👤 المستخدم: {target_user_id}\n"
        f"💰 المبلغ الذي أرسله: {original_amount}\n\n"
        f"أرسل المبلغ الجديد الذي تريد إضافته للمستخدم:"
    )

async def admin_withdraw_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("wdapprove_"):
        parts = data.split("_", 3)

        user_id = int(parts[1])
        amount = int(parts[2])
        account = parts[3]

        # خصم + تأكيد بمعاملة واحدة: لا يمكن لضغطتين (أو لأدمنين) خصم المبلغ مرتين
        result = await asyncio.to_thread(approve_withdraw_manual, user_id, amount)
        status = result["status"]

        if status == "not_pending":
            await query.edit_message_text(
                "ℹ️ هذا الطلب عولج مسبقاً أو هو قيد الدفع حالياً — لم يُخصم أي مبلغ الآن."
            )
            return

        if status == "no_user":
            await query.edit_message_text("❌ المستخدم غير موجود.")
            return

        if status == "insufficient":
            await query.edit_message_text(
                f"❌ لا يمكن تأكيد السحب.\n\n"
                f"رصيد المستخدم الحالي: {result['balance']:,} {CURRENCY_LABEL}\n"
                f"مبلغ السحب: {amount:,} {CURRENCY_LABEL}"
            )

            await context.bot.send_message(
                user_id,
                "❌ تعذر تنفيذ طلب السحب لأن رصيدك الحالي غير كافٍ."
            )
            return

        await query.edit_message_text(
            f"✅ تم تأكيد السحب\n\n"
            f"👤 المستخدم: {user_id}\n"
            f"💸 المبلغ: {amount:,} {CURRENCY_LABEL}\n"
            f"🏦 إلى: {account}"
        )

        await context.bot.send_message(
            user_id,
            f"✅ تم تنفيذ طلب السحب بنجاح!\n"
            f"💸 المبلغ المحوّل: {amount:,} {CURRENCY_LABEL}"
        )

    elif data.startswith("wdreject_"):
        parts = data.split("_", 3)

        user_id = int(parts[1])
        amount = int(parts[2])

        # ==================================================
        # عند الرفض لا نلمس الرصيد أبداً
        # ==================================================
        rejected = await asyncio.to_thread(reject_withdraw, user_id, amount)

        if not rejected:
            await query.edit_message_text(
                "ℹ️ هذا الطلب عولج مسبقاً أو هو قيد الدفع حالياً — لا يمكن رفضه الآن."
            )
            return

        await query.edit_message_text(
            f"❌ تم رفض طلب السحب للمستخدم {user_id}\n"
            f"💰 لم يتم خصم أي مبلغ من رصيده"
        )

        await context.bot.send_message(
            user_id,
            "❌ تم رفض طلب السحب.\n\n"
            "💰 لم يتم خصم أي مبلغ من رصيدك.\n"
            "تواصل مع الدعم للمزيد."
        )


# ============================
# 💸 دفع طلب سحب Sham Cash تلقائياً عبر الـ API (بعد رمز التحويل)
# ============================
async def _post_withdrawal_request(bot, text, reply_markup):
    """
    ينشر طلب السحب بقناة السحب (ثم التنبيهات ثم الشحن) ويرجع كائن الرسالة لنعدّله لاحقاً.
    يرجع None إذا وصل للأدمن بالخاص (احتياطي) أو فشل كل شيء.
    """
    for chat_id in (WITHDRAWALS_CHANNEL_ID, ALERTS_CHANNEL_ID, DEPOSITS_CHANNEL_ID):
        if not chat_id:
            continue
        try:
            return await bot.send_message(chat_id, text, reply_markup=reply_markup)
        except Exception:
            logging.getLogger("withdraw_log").exception("withdrawal request channel send failed")

    if ADMIN_DM_FALLBACK:
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, text, reply_markup=reply_markup)
            except Exception:
                pass

    return None


async def _auto_withdraw_attempt(bot, wid, msg, base_text):
    """
    محاولة دفع تلقائية لطلب سحب Sham Cash (بدون أدمن/رمز). عند أي فشل يبقى الطلب معلّقاً بقناة
    السحب بأزرار الموافقة/الرفض ليعالجه الأدمن يدوياً. الحماية داخل الوحدة: تفعيل التحويل،
    حدود العملية واليوم، رصيد اللاعب، ومنع الدفع المزدوج.
    """
    note = None
    keep_buttons = True

    try:
        out = await asyncio.to_thread(sham_payout.execute_withdraw_payout, wid, AUTO_PAYOUT_ADMIN_ID)
        status = out["status"]

        if status == "paid":
            note = "✅ دُفع تلقائياً"
            keep_buttons = False
        elif status == "unknown":
            note = (
                "⚠️ حالة التحويل غير مؤكدة!\n"
                "⛔ لا تدفع يدوياً ولا تعد المحاولة قبل التحقق (الرصيد محجوز، والبوت يتحقق تلقائياً كل دقيقة)."
            )
            keep_buttons = False
            await send_alert(bot, f"⚠️ دفع سحب تلقائي بحالة غير مؤكدة — الطلب #{wid} · المرجع {out.get('ref')}")
        elif status == "refunded":
            note = (
                "↩️ فشل الدفع التلقائي — لم يخرج أي مبلغ ورُدّ رصيد اللاعب.\n"
                f"السبب: {out.get('reason') or '—'}\n"
                "👉 عالج الطلب يدوياً بالأزرار تحت."
            )
        else:  # blocked: لم يحدث شيء (مثلاً التحويل متوقف أو تجاوز الحد)
            note = (
                f"⚠️ لم يُنفَّذ الدفع التلقائي (لم يُخصم أي مبلغ): {out.get('reason') or '—'}\n"
                "👉 عالج الطلب يدوياً بالأزرار تحت."
            )
    except Exception:
        logging.getLogger("withdraw_log").exception("auto withdraw attempt failed")
        note = (
            "⚠️ خطأ غير متوقع بالدفع التلقائي. راجع «تحويل Sham Cash ← غير المؤكدة» قبل أي إجراء، "
            "ثم عالج الطلب يدوياً إن لزم."
        )

    if msg is not None:
        try:
            await msg.edit_text(
                base_text + "\n\n" + note,
                reply_markup=None if keep_buttons else InlineKeyboardMarkup([]),
            )
        except Exception:
            logging.getLogger("withdraw_log").exception("could not update the withdrawal request message")
            await notify_withdrawals(bot, f"طلب السحب #{wid}: {note}")
    else:
        await notify_withdrawals(bot, f"طلب السحب #{wid}: {note}")

    try:
        await _withdraw_payout_flush(bot)      # يرسل للزبون «تم التنفيذ» ويسجّل النتيجة بالقناة
    except Exception:
        logging.getLogger("withdraw_log").exception("payout flush failed")


def _auto_withdraw_menu_content():
    on = get_auto_withdraw()
    transfer_on = False
    pin_ok = False
    try:
        transfer_on = bool(sham_tx.is_enabled())
        pin_ok = bool(sham_tx.has_pin())
    except Exception:
        pass

    cap = f"{AUTO_WITHDRAW_MAX_AMOUNT:,} {CURRENCY_LABEL}" if AUTO_WITHDRAW_MAX_AMOUNT else "بلا حد"
    text = (
        "🤖 السحب التلقائي (Sham Cash)\n\n"
        "عند التفعيل: كل طلب سحب بشام كاش يحاول البوت دفعه فوراً بدون تدخل منك، "
        "وإذا فشل (أو تجاوز الحد) يبقى الطلب بقناة السحب بأزرار موافقة/رفض لتعالجه يدوياً.\n\n"
        f"الحالة: {'✅ مفعّل' if on else '⛔ متوقف'}\n"
        f"أعلى مبلغ للدفع التلقائي: {cap} (فوقه يدوي)\n\n"
        "المتطلبات (من لوحة الأدمن ← تحويل Sham Cash):\n"
        f"• تحويل Sham Cash: {'✅ مفعّل' if transfer_on else '⛔ متوقف — لن يعمل التلقائي'}\n"
        f"• رمز التحويل: {'✅ مضبوط' if pin_ok else '⛔ غير مضبوط — لن يعمل التلقائي'}\n"
        "• تنطبق حدود العملية والحد اليومي المضبوطة هناك."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⛔ إيقاف السحب التلقائي" if on else "✅ تفعيل السحب التلقائي",
                              callback_data="admin_autowd_toggle")],
        [InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel")],
    ])
    return text, kb


async def admin_auto_withdraw_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    if query.data == "admin_autowd_toggle":
        new_state = not await asyncio.to_thread(get_auto_withdraw)
        await asyncio.to_thread(set_auto_withdraw, new_state)
        await notify_withdrawals(
            context.bot,
            f"🤖 السحب التلقائي (Sham Cash): {'✅ تم التفعيل' if new_state else '⛔ تم الإيقاف'} "
            f"(الأدمن {query.from_user.id})"
        )

    text, kb = _auto_withdraw_menu_content()
    await _safe_edit_message(query, text, reply_markup=kb)


# ============================
# 📞 إدارة أكواد Syriatel Cash: الرصيد + تغيير الكود + تحويل يدوي (أدمن فقط)
# ============================
_SY_STATE_KEYS = ("admin_sy_step", "admin_sy_code", "admin_sy_new", "admin_sy_mode", "admin_sy_to", "admin_sy_amount")


def _sy_clear(context):
    for k in _SY_STATE_KEYS:
        context.user_data.pop(k, None)


def _sy_money(value):
    value = Decimal(value)
    return f"{int(value):,}" if value == value.to_integral_value() else f"{value:,.2f}"


async def _sy_menu_payload():
    codes = _syriatel_codes()
    results = await asyncio.gather(*[asyncio.to_thread(sy_admin.fetch_balance, c) for c in codes])

    lines = ["📞 إدارة أكواد Syriatel Cash", ""]
    for i, (code, res) in enumerate(zip(codes, results), 1):
        lines.append(f"{i}) الكود: {code}")
        if res["ok"]:
            lines.append(f"   💰 الرصيد: {_sy_money(res['balance'])} {CURRENCY_LABEL}")
            if res.get("gsm"):
                lines.append(f"   📱 الرقم: {res['gsm']}")
        else:
            lines.append(f"   ⚠️ تعذّر جلب الرصيد: {res['error']}")
    lines += ["", "الأكواد أعلاه هي اللي بتظهر للزبائن عند الشحن عبر Syriatel."]

    rows = [[
        InlineKeyboardButton("🔄 تحديث الأرصدة", callback_data="admin_sy_menu"),
        InlineKeyboardButton("➕ إضافة كود", callback_data="admin_sy_add"),
    ]]
    for code in codes:
        row = [
            InlineKeyboardButton(f"💸 تحويل من {code}", callback_data=f"admin_sy_tr_{code}"),
            InlineKeyboardButton("✏️ تغيير", callback_data=f"admin_sy_rep_{code}"),
        ]
        if len(codes) > 1:
            row.append(InlineKeyboardButton("🗑", callback_data=f"admin_sy_del_{code}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel")])

    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def admin_syriatel_callback(update, context):
    query = update.callback_query
    await query.answer()
    admin_id = query.from_user.id

    if admin_id not in ADMIN_IDS:
        return

    data = query.data

    if data == "admin_sy_menu":
        _sy_clear(context)
        text, kb = await _sy_menu_payload()
        await _safe_edit_message(query, text, reply_markup=kb)
        return

    # أي شيء يغيّر الأكواد أو يحوّل مالاً: فقط لمن يملك صلاحية التحويل
    if not shtx_user_allowed(admin_id):
        await query.message.reply_text("⛔ غير مصرّح لك بهالإجراء.")
        return

    codes = _syriatel_codes()

    if data == "admin_sy_add" or data.startswith("admin_sy_rep_"):
        mode = "add" if data == "admin_sy_add" else "rep"
        old = None if mode == "add" else data[len("admin_sy_rep_"):]
        if mode == "rep" and old not in codes:
            await query.message.reply_text("⚠️ هالكود مو موجود بالقائمة. حدّث الشاشة وجرّب من جديد.")
            return
        _sy_clear(context)
        context.user_data.update(admin_sy_step="code_await", admin_sy_mode=mode, admin_sy_code=old)
        head = "➕ إضافة كود Syriatel" if mode == "add" else f"✏️ تغيير الكود {old}"
        await query.message.reply_text(
            f"{head}\n\nابعت الكود الجديد (أرقام فقط). رح أتأكد إنو حساب مربوط بحسابك على apisyria قبل ما أعتمدو.",
            reply_markup=cancel_keyboard(),
        )
        return

    if data.startswith("admin_sy_del_"):
        code = data[len("admin_sy_del_"):]
        if code not in codes or len(codes) < 2:
            await query.message.reply_text("⚠️ ما فيني أحذف هالكود (لازم يبقى كود واحد على الأقل).")
            return
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ احذف", callback_data=f"admin_sy_delok_{code}"),
            InlineKeyboardButton("❌ إلغاء", callback_data="admin_sy_menu"),
        ]])
        await _safe_edit_message(query, f"🗑 حذف الكود {code}؟\nما رح يظهر للزبائن بعد هلق.", reply_markup=kb)
        return

    if data.startswith("admin_sy_delok_"):
        code = data[len("admin_sy_delok_"):]
        if code in codes and len(codes) > 1:
            sy_admin.set_codes([c for c in codes if c != code])
            sy_admin.log_event(admin_id, "delete_code", code, status="ok")
            await _shtx_notify(context.bot, f"📞 حذف كود Syriatel {code} (الأدمن {admin_id})")
        text, kb = await _sy_menu_payload()
        await _safe_edit_message(query, text, reply_markup=kb)
        return

    if data == "admin_sy_codeok":
        mode = context.user_data.get("admin_sy_mode")
        new = context.user_data.get("admin_sy_new")
        old = context.user_data.get("admin_sy_code")
        if context.user_data.get("admin_sy_step") != "code_confirm" or not new:
            await _safe_edit_message(query, "⌛ انتهت صلاحية هالطلب. ابدأ من جديد.")
            return
        _sy_clear(context)
        if mode == "rep" and old in codes:
            updated = [new if c == old else c for c in codes]
        else:
            updated = codes + [new]
        sy_admin.set_codes(updated)
        sy_admin.log_event(admin_id, "replace_code" if mode == "rep" else "add_code", new, status="ok",
                           detail=f"old={old}" if old else "")
        await _shtx_notify(
            context.bot,
            f"📞 {'تغيير' if mode == 'rep' else 'إضافة'} كود Syriatel: "
            f"{(old + ' ← ') if mode == 'rep' and old else ''}{new} (الأدمن {admin_id})"
        )
        text, kb = await _sy_menu_payload()
        await _safe_edit_message(query, "✅ تم الحفظ.\n\n" + text, reply_markup=kb)
        return

    if data.startswith("admin_sy_tr_"):
        code = data[len("admin_sy_tr_"):]
        if code not in codes:
            await query.message.reply_text("⚠️ هالكود مو موجود بالقائمة. حدّث الشاشة وجرّب من جديد.")
            return
        if not sy_admin.api_ready():
            await query.message.reply_text("⛔ إعدادات API غير مكتملة.")
            return
        _sy_clear(context)
        context.user_data.update(admin_sy_step="tr_to", admin_sy_code=code)
        await query.message.reply_text(
            f"💸 تحويل Syriatel Cash من الحساب {code}\n\nابعت رقم المستفيد (مثال: 0995123456):",
            reply_markup=cancel_keyboard(),
        )
        return

    if data == "admin_sy_trgo":
        if context.user_data.get("admin_sy_step") != "tr_confirm" or not context.user_data.get("admin_sy_amount"):
            await _safe_edit_message(query, "⌛ انتهت صلاحية هالطلب. ابدأ من جديد.")
            return
        context.user_data["admin_sy_step"] = "tr_pin"
        await query.message.reply_text(
            "🔐 ابعت رمز PIN الخاص بحساب Syriatel Cash لتأكيد التحويل.\n"
            "• يُستخدم مرة وحدة ولا يُحفظ أبداً، وتُحذف رسالتك فوراً.\n"
            "• التحويل فوري ولا يمكن التراجع عنه.",
            reply_markup=cancel_keyboard(),
        )
        return


async def _handle_syriatel_text(update, context, text):
    """خطوات الإدخال النصي لإدارة أكواد Syriatel. يرجع True دائماً (يستهلك الرسالة)."""
    step = context.user_data.get("admin_sy_step")
    admin_id = update.effective_user.id
    msg = update.message

    if not shtx_user_allowed(admin_id):
        _sy_clear(context)
        await msg.reply_text("⛔ غير مصرّح لك بهالإجراء.", reply_markup=main_menu(admin_id))
        return True

    if step == "code_await":
        new = sy_admin.digits_only(text)
        if not sy_admin.valid_code(new):
            await msg.reply_text("❌ ابعت الكود أرقام فقط (من 4 لـ 15 خانة):", reply_markup=cancel_keyboard())
            return True
        if new in _syriatel_codes():
            await msg.reply_text("ℹ️ هالكود موجود أصلاً بالقائمة. ابعت كود تاني أو اضغط تراجع.", reply_markup=cancel_keyboard())
            return True

        res = await asyncio.to_thread(sy_admin.fetch_balance, new)
        if not res["ok"]:
            await msg.reply_text(
                f"❌ ما قدرت أتأكد من هالكود: {res['error']}\n\n"
                "لازم يكون حساب Syriatel مربوط وفعّال بحسابك على apisyria. ابعت كود تاني أو اضغط تراجع.",
                reply_markup=cancel_keyboard(),
            )
            return True

        mode = context.user_data.get("admin_sy_mode")
        old = context.user_data.get("admin_sy_code")
        context.user_data.update(admin_sy_step="code_confirm", admin_sy_new=new)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأكيد", callback_data="admin_sy_codeok"),
            InlineKeyboardButton("❌ إلغاء", callback_data="admin_sy_menu"),
        ]])
        await msg.reply_text(
            f"{'✏️ استبدال ' + old + ' بـ ' if mode == 'rep' else '➕ إضافة '}{new}\n"
            f"💰 رصيد الحساب: {_sy_money(res['balance'])} {CURRENCY_LABEL}\n\n"
            "⚠️ الزبائن رح يشوفوا هالكود فوراً.\n"
            "التحقق التلقائي من الشحن بيعتمد على إعدادات payment_api؛ إذا ما بيدعم هالحساب فعّل "
            "«التشييك اليدوي» لـ Syriatel وجرّب شحنة صغيرة بعد التغيير.",
            reply_markup=kb,
        )
        return True

    if step == "tr_to":
        to = sy_admin.digits_only(text)
        if not sy_admin.valid_gsm(to):
            await msg.reply_text("❌ ابعت رقم موبايل سوري صحيح (10 أرقام يبدأ بـ 09):", reply_markup=cancel_keyboard())
            return True
        context.user_data.update(admin_sy_to=to, admin_sy_step="tr_amount")
        res = await asyncio.to_thread(sy_admin.fetch_balance, context.user_data.get("admin_sy_code"))
        hint = f"\n💰 الرصيد المتاح: {_sy_money(res['balance'])} {CURRENCY_LABEL}" if res["ok"] else ""
        await msg.reply_text(f"✅ المستفيد: {to}{hint}\n\nابعت المبلغ بالليرة (أرقام صحيحة فقط):", reply_markup=cancel_keyboard())
        return True

    if step == "tr_amount":
        cleaned = text.strip().translate(sy_admin._ARABIC_DIGITS).replace(",", "")
        if not cleaned.isdigit() or int(cleaned) <= 0:
            await msg.reply_text("❌ ابعت مبلغاً صحيحاً أكبر من صفر (أرقام فقط):", reply_markup=cancel_keyboard())
            return True
        amount = int(cleaned)
        code = context.user_data.get("admin_sy_code")
        to = context.user_data.get("admin_sy_to")

        res = await asyncio.to_thread(sy_admin.fetch_balance, code)
        warn = ""
        if res["ok"]:
            if Decimal(amount) > res["balance"]:
                await msg.reply_text(
                    f"❌ المبلغ أكبر من الرصيد المتاح ({_sy_money(res['balance'])} {CURRENCY_LABEL}). ابعت مبلغاً أقل:",
                    reply_markup=cancel_keyboard(),
                )
                return True
        else:
            warn = "\n⚠️ ما قدرت أتحقق من الرصيد، فقد يُرفض التحويل."

        context.user_data.update(admin_sy_amount=amount, admin_sy_step="tr_confirm")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ متابعة", callback_data="admin_sy_trgo"),
            InlineKeyboardButton("❌ إلغاء", callback_data="admin_sy_menu"),
        ]])
        await msg.reply_text(
            "⚠️ تأكيد التحويل\n\n"
            f"📤 من الحساب: {code}\n"
            f"📥 إلى: {to}\n"
            f"💰 المبلغ: {amount:,} {CURRENCY_LABEL}{warn}\n\n"
            "بعد المتابعة رح أطلب رمز PIN الحساب.",
            reply_markup=kb,
        )
        return True

    if step in ("tr_confirm", "code_confirm"):
        await msg.reply_text("اضغط «✅» أو «❌» بالرسالة اللي فوق، أو «تراجع».")
        return True

    if step == "tr_pin":
        pin = sy_admin.digits_only(text)
        try:
            await msg.delete()            # حذف رسالة الـ PIN فوراً
        except Exception:
            pass

        if not re.fullmatch(r"\d{3,8}", pin):
            await context.bot.send_message(admin_id, "❌ رمز PIN غير صالح (أرقام فقط). أرسله من جديد أو اضغط تراجع.",
                                           reply_markup=cancel_keyboard())
            return True

        code = context.user_data.get("admin_sy_code")
        to = context.user_data.get("admin_sy_to")
        amount = context.user_data.get("admin_sy_amount")
        _sy_clear(context)                # لا يمكن إعادة استخدام الحالة أو الـ PIN

        if not (code and to and amount):
            await context.bot.send_message(admin_id, "⌛ انتهت صلاحية الطلب. ابدأ من جديد.", reply_markup=main_menu(admin_id))
            return True

        event_id = sy_admin.log_event(admin_id, "transfer", code, to, amount, "started")
        await context.bot.send_message(admin_id, "⏳ جاري التحويل... لا تعد المحاولة.")

        out = await asyncio.to_thread(sy_admin.transfer_cash, code, to, amount, pin)
        pin = None
        status = out["status"]
        sy_admin.update_event(event_id, status, out.get("billcode") or out.get("error") or "")

        head = f"من {code} إلى {to} — {amount:,} {CURRENCY_LABEL}"
        if status == "sent":
            extra = ""
            if out.get("fee") not in (None, ""):
                extra += f"\n💸 العمولة: {out['fee']}"
            if out.get("billcode"):
                extra += f"\n🧾 رقم الفاتورة: {out['billcode']}"
            await context.bot.send_message(admin_id, f"✅ تم التحويل\n{head}{extra}", reply_markup=main_menu(admin_id))
            await _shtx_notify(context.bot, f"💸 تحويل يدوي Syriatel Cash (الأدمن {admin_id})\n{head}{extra}")
        elif status == "failed":
            await context.bot.send_message(
                admin_id, f"❌ لم يتم التحويل (لم يخرج أي مبلغ)\nالسبب: {out['error']}", reply_markup=main_menu(admin_id)
            )
            await _shtx_notify(context.bot, f"❌ فشل تحويل يدوي Syriatel (الأدمن {admin_id})\n{head}\nالسبب: {out['error']}")
        else:
            recent = await asyncio.to_thread(sy_admin.recent_outgoing, code, 3)
            lines = [
                f"• {i.get('date', '')} → {i.get('to', '')} : {i.get('amount', '')}" for i in recent
            ] or ["(تعذّر جلب آخر التحويلات)"]
            await context.bot.send_message(
                admin_id,
                "⚠️ حالة التحويل غير مؤكدة!\n"
                f"{head}\nالسبب: {out['error']}\n\n"
                "⛔ لا تعد المحاولة قبل التحقق من تطبيق Syriatel Cash. آخر التحويلات الصادرة:\n"
                + "\n".join(lines),
                reply_markup=main_menu(admin_id),
            )
            await send_alert(context.bot, f"⚠️ تحويل يدوي Syriatel بحالة غير مؤكدة (الأدمن {admin_id})\n{head}")
        return True

    _sy_clear(context)
    return True


class _DMTarget:
    """
    يجعل ردود الأدمن تذهب لمحادثته الخاصة مع البوت بدل مكان الزر. ضروري لأزرار طلبات السحب
    داخل القنوات: القنوات لا تقبل لوحات مفاتيح عادية (BadRequest: Inline keyboard expected)،
    ولا يمكن للأدمن كتابة رمز التحويل داخلها.
    """
    def __init__(self, bot, chat_id):
        self.bot = bot
        self.chat_id = chat_id

    async def reply_text(self, text, **kwargs):
        return await self.bot.send_message(self.chat_id, text, **kwargs)


async def _admin_withdraw_pay_flow(query, context, dm):
    """يرجع True إذا أُرسل طلب رمز التحويل للأدمن."""
    admin_id = query.from_user.id

    if not shtx_user_allowed(admin_id):
        await dm.reply_text("⛔ غير مصرّح لك بالتحويل التلقائي.")
        return False

    try:
        wid = int(query.data.split("_", 1)[1])
    except (ValueError, IndexError):
        return False

    pf = await asyncio.to_thread(sham_payout.preflight, wid)
    if not pf["ok"]:
        await dm.reply_text(
            "⛔ ما فيني أدفع تلقائياً:\n" + pf["reason"] +
            "\n\nإذا دفعت يدوياً من تطبيق شام كاش استخدم «✅ تأكيد يدوي»."
        )
        return False

    _shtx_ask_pin_state(context, {"type": "withdraw_pay", "wid": wid})
    await dm.reply_text(
        f"💸 دفع طلب سحب #{wid}\n\n"
        f"👤 اللاعب: {pf['user_id']}\n"
        f"💰 المبلغ: {pf['amount']:,} {CURRENCY_LABEL}\n"
        f"📉 الحسم: {pf['fee']:,} {CURRENCY_LABEL}\n"
        f"💵 سيُحوَّل: {pf['net']:,} {CURRENCY_LABEL}\n"
        f"📥 إلى:\n{pf['address']}"
    )
    await _shtx_prompt_pin(dm, "تأكيد الدفع — سيُنفَّذ التحويل فوراً ولا يمكن التراجع")
    return True


async def admin_withdraw_pay_callback(update, context):
    query = update.callback_query
    dm = _DMTarget(context.bot, query.from_user.id)

    try:
        sent = await _admin_withdraw_pay_flow(query, context, dm)
    except (Forbidden, BadRequest):
        logging.getLogger("withdraw_log").exception("could not DM the admin the payout prompt")
        await query.answer(
            "⚠️ ما قدرت أراسلك بالخاص: افتح محادثة البوت وأرسل /start ثم أعد المحاولة.",
            show_alert=True,
        )
        return

    if sent:
        await query.answer("📩 أرسلت لك تفاصيل الدفع وطلب رمز التحويل بالخاص")
    else:
        await query.answer()


async def _withdraw_pay_execute(bot, message, admin_id, wid):
    await message.reply_text("⏳ جاري دفع طلب السحب... لا تكرر العملية.", reply_markup=main_menu(admin_id))

    out = await asyncio.to_thread(sham_payout.execute_withdraw_payout, wid, admin_id)
    status = out["status"]

    if status == "blocked":
        await message.reply_text(
            f"⛔ لم يتم الدفع (لم يُخصم أي مبلغ): {out['reason']}", reply_markup=main_menu(admin_id)
        )
        return

    if status == "unknown":
        summary = f"🆔 الطلب #{wid} · 💵 {out['net']:,} {CURRENCY_LABEL} · 🔖 {out['ref']}"
        await message.reply_text(
            "⚠️ حالة التحويل غير مؤكدة!\n\n"
            "⛔ لا تدفع يدوياً ولا تعد المحاولة قبل التحقق، حتى لا يُدفع المبلغ مرتين.\n"
            "الرصيد محجوز والطلب قيد التحقق.\n\n"
            f"{summary}\n\n🔎 جاري التحقق من سجل التحويلات الصادرة...",
            reply_markup=main_menu(admin_id),
        )
        await _shtx_notify(
            bot,
            f"⚠️ دفع سحب بحالة غير مؤكدة\n👤 بواسطة: {admin_id}\n{summary}\n"
            "راجعه من: لوحة الأدمن ← تحويل Sham Cash ← غير المؤكدة",
            exclude=admin_id,
        )
        await asyncio.sleep(SHTX_AUTO_VERIFY_DELAY_SECONDS)
        verified = await asyncio.to_thread(sham_tx.verify_unknown, out["transfer_id"])
        if verified["status"] == "found":
            await message.reply_text("✅ تم التأكد من سجل التحويلات الصادرة: الدفع تمّ فعلاً.")
        else:
            await message.reply_text(
                "ℹ️ لم يظهر التحويل بعد في السجل (قد يتأخر). البوت يتحقق تلقائياً كل دقيقة، "
                "ولا تدفع يدوياً قبل التأكد من تطبيق شام كاش."
            )

    await _withdraw_payout_flush(bot)


async def _withdraw_payout_flush(bot):
    """ينهي الدفعات المحسومة (paid/refunded) ويرسل الإشعارات مرة واحدة لكل دفعة."""
    try:
        await asyncio.to_thread(sham_payout.reconcile_all)
        items = await asyncio.to_thread(sham_payout.pending_notifications)
    except Exception as exc:
        print(f"⚠️ تعذّر إنهاء دفعات السحب: {exc}")
        return

    for p in items:
        try:
            if p["state"] == "paid":
                try:
                    await bot.send_message(
                        p["user_id"],
                        "✅ تم تنفيذ طلب السحب بنجاح!\n\n"
                        f"💸 المبلغ المطلوب: {p['amount']:,} {CURRENCY_LABEL}\n"
                        f"📉 الحسم: {p['fee']:,} {CURRENCY_LABEL}\n"
                        f"💵 المحوّل إلى حسابك: {p['net']:,} {CURRENCY_LABEL}",
                    )
                except Exception:
                    pass
                text = (
                    f"✅ تم دفع طلب السحب #{p['withdraw_id']}\n"
                    f"👤 اللاعب: {p['user_id']}\n"
                    f"💵 المحوّل: {p['net']:,} {CURRENCY_LABEL} (الحسم {p['fee']:,})\n"
                    f"🔖 المرجع: {p['ref']}\n"
                    f"👤 المستلم: {p['recipient_name'] or '—'}\n"
                    f"🛠️ نفّذه: {'🤖 تلقائي' if not p['admin_id'] else p['admin_id']}"
                )
            else:
                text = (
                    f"↩️ تعذّر دفع طلب السحب #{p['withdraw_id']} — لم يخرج أي مبلغ ورُدّ رصيد اللاعب.\n"
                    f"السبب: {p['error'] or 'لم يتم التحويل'}\n"
                    "الطلب رجع معلّقاً: تقدر تعيد المحاولة من رسالة الطلب أو ترفضه."
                )
            await notify_withdrawals(bot, text)
        except Exception as exc:
            print(f"⚠️ إشعار دفعة السحب {p.get('id')}: {exc}")
        finally:
            await asyncio.to_thread(sham_payout.mark_notified, p["id"])


async def withdraw_payout_loop(application):
    """كل دقيقة: يتحقق من التحويلات غير المؤكدة ويُنهي الدفعات المحسومة (يغطي إعادة التشغيل والحسم اليدوي)."""
    await asyncio.sleep(30)
    while True:
        try:
            await asyncio.to_thread(sham_payout.auto_verify_unknown)
            await _withdraw_payout_flush(application.bot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"⚠️ حلقة دفعات السحب: {exc}")
        await asyncio.sleep(60)


def _gift_error_text(result):
    status = result.get("status")
    limit = result.get("limit")

    if status == "below_minimum":
        return f"❌ أقل مبلغ للإهداء هو {result.get('minimum', 0):,} {CURRENCY_LABEL}."
    if status == "disabled":
        return "⛔ إهداء الرصيد متوقف حالياً."
    if status == "target_not_found":
        return "❌ المستخدم غير موجود."
    if status == "self":
        return "❌ لا يمكنك إهداء نفسك."
    if status == "insufficient":
        return "❌ رصيدك غير كافي"
    if status == "invalid_amount":
        return "❌ المبلغ غير صالح"
    return "❌ تعذّر تنفيذ الإهداء، حاول لاحقاً."


async def gift_admin_callback(update, context):
    """
    أزرار قديمة (gift_accept / gift_reject): لم تعد تُرسَل من أي مكان.
    تُبقى للأدمن فقط وتمرّ عبر التحويل الذرّي نفسه (مع حد الاستلام)،
    ولا يُنفَّذ شيء من مستخدم عادي حتى لو وصل callback_data مفبرك.
    """
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    data = query.data or ""

    m = re.fullmatch(r"gift_accept_(\d+)_(\d+)_(\d+)", data)

    if m:
        sender_id, target_id, amount = (int(x) for x in m.groups())

        result = transfer_gift(sender_id, target_id, amount)

        if result["status"] != "ok":
            await query.edit_message_text(_gift_error_text(result))
            return

        await query.edit_message_text("✅ تم تنفيذ عملية الاهداء")

        for uid, text in (
            (sender_id, f"✅ تم اهداء {amount:,} {CURRENCY_LABEL} بنجاح"),
            (target_id, f"🎉 تم استلام {amount:,} {CURRENCY_LABEL} كهدية"),
        ):
            try:
                await context.bot.send_message(uid, text)
            except Exception:
                pass
        return

    m = re.fullmatch(r"gift_reject_(\d+)(?:_\d+)*", data)

    if m:
        await query.edit_message_text("❌ تم رفض طلب الإهداء")
        try:
            await context.bot.send_message(int(m.group(1)), "❌ تم رفض طلب الإهداء الخاص بك.")
        except Exception:
            pass


async def faq_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    text = None

    if data == "faq_what":
        text = "🎮 *ما هو موقع Ichancy؟*\n\nIchancy هو موقع ألعاب أونلاين\nيمكنك الشحن، اللعب، والسحب."

    elif data == "faq_recharge":
        text = (
            "📥 *كيف أشحن رصيد البوت؟*\n\n"
            "1️⃣ اختر شحن رصيد\n"
            "2️⃣ اختر طريقة الدفع\n"
            "3️⃣ أرسل المبلغ ورقم العملية\n"
            "4️⃣ انتظر تأكيد الكاشيرة\n\n"
            "💡 ثم اشحن حساب iChancy من رصيد البوت"
        )

    elif data == "faq_withdraw":
        text = (
            "📤 *كيف أسحب رصيدي؟*\n\n"
            "1️⃣ اختر سحب رصيد\n"
            "2️⃣ أدخل المبلغ\n"
            "3️⃣ أدخل الحساب\n"
            + _fee_notice(get_withdraw_fee_percent(query.from_user.id))[0]
        )

    elif data == "faq_spin":
        text = "🎰 *اللفة المجانية*\n\n🔒 متاحة فقط بعد شحن الرصيد\n⏱️ لفة واحدة كل 24 ساعة على الأقل\n🎉 قد تربح رصيد مجاني أو زيادة على الشحن أو تعويض يومي"

    elif data == "faq_back":
        await query.edit_message_text("📚 *الأسئلة الشائعة*", reply_markup=faq_menu(), parse_mode="Markdown")
        return

    if text:
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع للأسئلة", callback_data="faq_back")]])
        )

async def history_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "history_recharge":
        recharges = get_user_recharges(user_id)
        if not recharges:
            await query.edit_message_text("❌ لا يوجد عمليات شحن بعد")
            return
        text = "📥 آخر عمليات الشحن:\n\n"
        for r in recharges:
            amount, method, txid, date = r
            text += f"💰 {amount}\n💳 {method}\n🔢 {txid}\n📅 {date}\n\n"
        await query.edit_message_text(text)

    elif query.data == "history_withdraw":
        withdraws = get_user_withdraws(user_id)
        if not withdraws:
            await query.edit_message_text("❌ لا يوجد عمليات سحب بعد")
            return
        text = "📤 آخر عمليات السحب:\n\n"
        for w in withdraws:
            amount, account, date = w
            text += f"💸 {amount}\n🪙 {account}\n📅 {date}\n\n"
        await query.edit_message_text(text)

async def ichancy_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "back_to_menu":
        await query.message.reply_text("👋 عدنا للقائمة الرئيسية:", reply_markup=main_menu(user_id))


async def admin_set_rate_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    context.user_data["admin_set_rate"] = True
    rate = get_usdt_rate()
    await query.message.reply_text(
        f"💱 سعر الصرف الحالي: 1 USDT = {rate:,} ل.س\n\n"
        f"أرسل السعر الجديد (بالليرة السورية):\n"
        f"مثال: 147",
        reply_markup=cancel_keyboard()
    )


async def admin_set_usd_rate_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    context.user_data["admin_set_usd_rate"] = True
    rate = get_shamcash_usd_rate()
    await query.message.reply_text(
        f"💵 سعر دولار Sham Cash الحالي: 1$ = {rate:,} ل.س\n\n"
        f"أرسل السعر الجديد (رقم صحيح بالليرة):\n"
        f"مثال: 147",
        reply_markup=cancel_keyboard()
    )


def _notice_menu_view():
    rows = []
    for sec, title in SERVICE_SECTIONS.items():
        enabled, _ = get_notice_state(sec)
        rows.append([InlineKeyboardButton(
            f"{'🔴' if enabled else '⚪'} {title}",
            callback_data=f"admin_notice_sec_{sec}"
        )])
    text = (
        "📢 رسائل الخدمة\n\n"
        "عند تفعيل رسالة لقسم معيّن، يظهر النص للمستخدم عند دخوله هذا القسم "
        "ولا يستطيع المتابعة فيه حتى توقفها.\n\n"
        "🔴 مفعّلة (القسم محجوب)\n"
        "⚪ متوقفة\n\n"
        "الأدمن غير خاضعين للحجب."
    )
    return text, InlineKeyboardMarkup(rows)


def _notice_section_view(sec):
    enabled, text = get_notice_state(sec)
    title = SERVICE_SECTIONS[sec]

    status = "🔴 مفعّلة — القسم محجوب حالياً" if enabled else "⚪ متوقفة"
    note = "\n⚠️ هذه الرسالة تحجب كل الأقسام.\n" if sec == "general" else "\n"
    body = text.strip() if text.strip() else "— لا توجد رسالة —"

    msg = f"{title}\n{note}الحالة: {status}\n\nالرسالة:\n{body}"

    rows = [[InlineKeyboardButton("✏️ تعديل الرسالة", callback_data=f"admin_notice_edit_{sec}")]]
    if text.strip():
        rows.append([InlineKeyboardButton(
            "✅ إيقاف الحجب" if enabled else "🔴 تفعيل الحجب",
            callback_data=f"admin_notice_toggle_{sec}"
        )])
        rows.append([InlineKeyboardButton("🗑️ حذف الرسالة", callback_data=f"admin_notice_del_{sec}")])
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="admin_notice_menu")])

    return msg, InlineKeyboardMarkup(rows)


async def _notice_show(query, view):
    text, markup = view
    try:
        await query.edit_message_text(text, reply_markup=markup)
    except Exception:
        # مثلاً: "message is not modified" — نتجاهلها
        pass


async def admin_notice_callback(update, context):
    query = update.callback_query

    if query.from_user.id not in ADMIN_IDS:
        await query.answer()
        return

    parts = query.data.split("_")            # admin_notice_<action>[_<section>]
    action = parts[2] if len(parts) > 2 else "menu"
    sec = parts[3] if len(parts) > 3 else None

    if action != "menu" and sec not in SERVICE_SECTIONS:
        await query.answer("قسم غير معروف", show_alert=True)
        return

    if action == "menu":
        await query.answer()
        context.user_data.pop("admin_notice_edit", None)
        await _notice_show(query, _notice_menu_view())

    elif action == "sec":
        await query.answer()
        context.user_data.pop("admin_notice_edit", None)
        await _notice_show(query, _notice_section_view(sec))

    elif action == "edit":
        await query.answer()
        context.user_data["admin_notice_edit"] = sec
        await query.message.reply_text(
            f"✏️ {SERVICE_SECTIONS[sec]}\n\n"
            f"أرسل نص الرسالة الآن (حتى {NOTICE_MAX_LENGTH} حرف).\n"
            "بعد الحفظ فعّل الحجب من نفس الشاشة.",
            reply_markup=cancel_keyboard()
        )

    elif action == "toggle":
        enabled, _ = get_notice_state(sec)
        final = set_notice_enabled(sec, not enabled)
        if not enabled and not final:
            await query.answer("اكتب نص الرسالة أولاً", show_alert=True)
            return
        await query.answer("🔴 تم تفعيل الحجب" if final else "✅ تم إيقاف الحجب")
        await _notice_show(query, _notice_section_view(sec))

    elif action == "del":
        delete_notice(sec)
        await query.answer("🗑️ تم حذف الرسالة")
        await _notice_show(query, _notice_section_view(sec))

    else:
        await query.answer()


async def admin_bonus_menu_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    percents = get_all_bonus_percents()

    text = "🎁 نسب بونص الشحن الحالية:\n\n"
    for key, label in BONUS_METHOD_LABELS.items():
        text += f"{label}: {percents.get(key, 0.0) * 100:.1f}%\n"
    text += "\nاختر طريقة الشحن التي تريد تعديل نسبتها:"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✏️ {label}", callback_data=f"admin_set_bonus_{key}")]
        for key, label in BONUS_METHOD_LABELS.items()
    ] + [
        [InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel")]
    ])

    await query.edit_message_text(text, reply_markup=keyboard)


async def admin_set_bonus_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    key = query.data.replace("admin_set_bonus_", "")

    if key not in BONUS_METHOD_LABELS:
        return

    context.user_data["admin_set_bonus_key"] = key
    current = get_bonus_percent(key) * 100

    await query.message.reply_text(
        f"{BONUS_METHOD_LABELS[key]}\n"
        f"🎁 النسبة الحالية: {current:.1f}%\n\n"
        f"أرسل النسبة الجديدة (رقم فقط، مثال: 8 لتعني 8%):",
        reply_markup=cancel_keyboard()
    )


# ============================
# الاسترداد الأسبوعي (Cashback) — المنطق الأساسي
# ============================

async def process_weekly_cashback(bot, force=False):
    """
    جولة الأسبوعي — لا يُسمح بجولتين متزامنتين (الحلقة / الجولة الأخيرة / زر الأدمن).
    force=False: يُعالَج فقط من مضى على نقطته المرجعية 7 أيام (الحلقة التلقائية).
    force=True : يُعالَج كل مشتركي الأسبوعي الآن (زر الأدمن / الجولة الأخيرة).
    """
    async with _daily_lock("weekly"):
        return await _process_weekly_cashback_locked(bot, force)


async def _process_weekly_cashback_locked(bot, force=False):
    """
    استرداد أسبوعي لمشتركي «الأسبوعي» فقط (من اختاروه)، لكل مستخدم أسبوعه الخاص:
    يبدأ من نقطته المرجعية (أول فحص بعد اختياره) ويُصرف كل 7 أيام.

    صافي الخسارة = (الرصيد القديم في iChancy + كل ما تم شحنه للحساب
    خلال الفترة) - (الرصيد الحالي + كل ما تم سحبه خلال نفس الفترة).
    """

    if not get_cashback_program_enabled("weekly"):
        return {"processed": 0, "paid": 0, "total_cashback": 0, "skipped": True}

    percent = get_cashback_percent()

    if percent <= 0:
        return {"processed": 0, "paid": 0, "total_cashback": 0}

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    processed = 0
    paid = 0
    total_cashback = 0
    payout_rows = []

    for user_id, ichancy_username in _cashback_program_users("weekly"):

        try:
            snapshot = get_cashback_snapshot(user_id)

            if snapshot and not force:
                try:
                    age = (
                        datetime.utcnow()
                        - datetime.strptime(snapshot["last_run_at"], "%Y-%m-%d %H:%M:%S")
                    ).total_seconds()
                except (TypeError, ValueError):
                    age = 0

                if age < CASHBACK_INTERVAL_SECONDS:
                    continue

            player = get_player_by_username(ichancy_username)
            if not player:
                continue

            player_id = player.get("playerId")
            if not player_id:
                continue

            current_balance_nps = get_player_balance(player_id)
            if current_balance_nps is None:
                continue

            processed += 1

            # أول مرة فقط: تسجيل نقطة مرجعية بدون احتساب استرداد
            if snapshot is None:
                init_cashback_snapshot_if_missing(user_id, current_balance_nps)
                continue

            last_balance_nps = snapshot["last_balance_nps"]
            period_start = snapshot["last_run_at"]

            deposits_nps, withdrawals_nps = get_ichancy_movement_since(user_id, period_start)

            net_loss_nps = (last_balance_nps + deposits_nps) - (current_balance_nps + withdrawals_nps)

            loss_amount = 0
            cashback_amount = 0

            if net_loss_nps > 0:
                loss_amount = int(nps_to_ls(net_loss_nps))

                # ما عُوِّض مسبقاً بكوبون «تعويض يومي» من العجلة لا يُعوَّض مرة ثانية
                covered = get_daily_payout_loss_since(user_id, period_start)
                if covered > 0:
                    loss_amount = max(0, loss_amount - covered)

                cashback_amount = int(loss_amount * percent)

            # الصرف + سجل الدفع + تحديث النقطة المرجعية في معاملة واحدة
            applied = apply_weekly_cashback(
                user_id, loss_amount, cashback_amount,
                period_start, now_str, current_balance_nps
            )

            if applied and cashback_amount > 0:
                paid += 1
                total_cashback += cashback_amount
                payout_rows.append(
                    (await _tg_user_label(bot, user_id), user_id, loss_amount, cashback_amount)
                )

                try:
                    await bot.send_message(
                        user_id,
                        "🎁 تم صرف استردادك الأسبوعي (Cashback)!\n\n"
                        f"📉 صافي خسارتك هذا الأسبوع: {loss_amount:,} {CURRENCY_LABEL}\n"
                        f"💸 نسبة الاسترداد: {percent * 100:.1f}%\n"
                        f"💰 المبلغ المسترد: {cashback_amount:,} {CURRENCY_LABEL}\n\n"
                        f"💳 رصيدك الحالي: {get_user_balance(user_id):,} {CURRENCY_LABEL}"
                    )
                except Exception:
                    pass

        except Exception as e:
            print(f"⚠️ خطأ أثناء معالجة الاسترداد للمستخدم {user_id}: {e}")

    await _log_cashback_payouts(bot, "weekly", payout_rows, f" ({percent * 100:.1f}%)")

    return {"processed": processed, "paid": paid, "total_cashback": total_cashback}


WEEKLY_LOOP_POLL_SECONDS = 10 * 60   # فحص كل 10 دقائق: تثبيت نقاط البداية الجديدة + صرف من أتمّ 7 أيام


async def weekly_cashback_loop(application):
    """
    حلقة خلفية: كل 10 دقائق تفحص مشتركي الأسبوعي، ومن مضى على نقطته المرجعية
    7 أيام يُصرف له (أسبوع مستقل لكل مستخدم، ولا يتأثر بإعادة تشغيل البوت).
    """

    await asyncio.sleep(CASHBACK_INITIAL_DELAY_SECONDS)

    while True:
        try:
            result = await process_weekly_cashback(application.bot)
            if result["paid"] > 0:
                await notify_deposits(
                    application.bot,
                    "💸 تم تنفيذ صرف الاسترداد الأسبوعي\n\n"
                    f"👥 تم فحص: {result['processed']} مستخدم\n"
                    f"✅ تم صرف استرداد لـ: {result['paid']} مستخدم\n"
                    f"💰 إجمالي المسترد: {result['total_cashback']:,} {CURRENCY_LABEL}"
                )
        except Exception as e:
            print(f"⚠️ خطأ أثناء تنفيذ جولة الاسترداد الأسبوعي: {e}")

        await asyncio.sleep(WEEKLY_LOOP_POLL_SECONDS)


# ============================
# التعويض اليومي (Daily Cashback)
# ============================
# القواعد:
#  - الصرف الساعة 9:00 صباحاً (بتوقيت سوريا) عن خسارة اليوم السابق كاملاً.
#  - اليوم = من 12:00 ليلاً إلى 12:00 ليلاً (لقطتا رصيد عند منتصف الليل).
#  - شرط الاستحقاق: رصيد iChancy = 0 عند 12:00 ليلاً، والسحوبات ≤ الإيداعات.
#  - أي خسارة بعد 12:00 ليلاً تُحتسب مع اليوم التالي.
DAILY_CASHBACK_HOUR = 9                    # موعد الصرف (ساعة، بتوقيت سوريا)
DAILY_SNAPSHOT_GRACE_MINUTES = 15          # أقصى تأخير مسموح لأخذ لقطة منتصف الليل
DAILY_LOOP_POLL_SECONDS = 60               # دورة الفحص (كل خطوة idempotent)
DAILY_SNAPSHOT_USER_PAUSE = 0.05           # فسحة بين لاعب وآخر حتى لا يتجمّد البوت أثناء اللقطة
DAILY_RUN_MAX_ATTEMPTS = 5                 # أقصى عدد محاولات عند وجود أخطاء بالتنفيذ

# سوريا على UTC+3 بشكل دائم (بدون توقيت صيفي) منذ 2022. نثبّتها صراحةً بدل الاعتماد على
# قاعدة tzdata في الخادم، لأن النسخ القديمة منها تعطي إزاحة خاطئة شتاءً فيتحرك موعد الـ 9 صباحاً.
SYRIA_UTC_OFFSET_HOURS = 3
SYRIA_TZ = timezone(timedelta(hours=SYRIA_UTC_OFFSET_HOURS))

_daily_locks = {}


def _daily_lock(name):
    """قفل asyncio لكل حلقة أحداث (يمنع تنفيذين متزامنين: الحلقة + زر الأدمن)."""
    loop = asyncio.get_running_loop()
    entry = _daily_locks.get(name)
    if entry is None or entry[0] is not loop:
        entry = (loop, asyncio.Lock())
        _daily_locks[name] = entry
    return entry[1]


def syria_now():
    return datetime.now(SYRIA_TZ)


def _shift_day(day_str, days):
    return (datetime.strptime(day_str, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def _daily_pool(coupon_days):
    """
    لاعبو التعويض اليومي = مشتركو «اليومي» (إن كان البرنامج مفعّلاً)
    + أصحاب كوبون عجلة الحظ «تعويض يومي» لأيام coupon_days. [(user_id, ichancy_username)]
    """
    users = {}

    if get_cashback_program_enabled("daily"):
        for uid, uname in _cashback_program_users("daily"):
            users[uid] = uname

    for day in coupon_days:
        for uid, uname in get_daily_coupon_users(day):
            users.setdefault(uid, uname)

    return list(users.items())


def _daily_user_outcome(user_id, target_day):
    """
    يحسب حالة لاعب واحد ليوم target_day ويرجع (الحالة, صافي الخسارة NSP).
    الحالات: eligible | balance_not_zero | winner | no_loss | no_snapshot
    """
    start_boundary = target_day
    end_boundary = _shift_day(target_day, 1)

    end_snap = get_daily_snapshot(user_id, end_boundary)
    if not end_snap:
        return "no_snapshot", 0.0

    start_snap = get_daily_snapshot(user_id, start_boundary)

    if start_snap:
        start_balance = start_snap["balance_nps"]
        start_ts = start_snap["taken_at"]
    else:
        # لا لقطة بداية: مقبول فقط للحساب الجديد كلياً (لا أي حركة قبل هذا اليوم)
        # ورصيده عند البداية صفر بالتأكيد. غير ذلك نتجاوزه بدل التخمين.
        try:
            cutoff = (
                datetime.strptime(end_snap["taken_at"], "%Y-%m-%d %H:%M:%S") - timedelta(hours=24)
            ).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return "no_snapshot", 0.0

        if has_ichancy_movement_at_or_before(user_id, cutoff):
            return "no_snapshot", 0.0

        start_balance = 0.0
        start_ts = EPOCH_TS

    deposits, withdrawals = get_ichancy_movement_between(user_id, start_ts, end_snap["taken_at"])

    return evaluate_daily_cashback(start_balance, end_snap["balance_nps"], deposits, withdrawals)


async def take_daily_snapshots(boundary_day):
    """
    لقطة رصيد iChancy لكل لاعب لحظة منتصف الليل (boundary_day = تاريخ اليوم الجديد).
    تستأنف من حيث توقفت، ولا تستبدل لقطة موجودة.
    """
    async with _daily_lock("snapshot"):

        if is_snapshot_run_done(boundary_day):
            return {"status": "already_done", "taken": 0, "failed": 0}

        already = users_with_daily_snapshot(boundary_day)
        taken = 0
        failed = 0

        for user_id, ichancy_username in _daily_pool([boundary_day, _shift_day(boundary_day, -1)]):

            if user_id in already:
                continue

            try:
                player = get_player_by_username(ichancy_username)
                player_id = player.get("playerId") if player else None
                balance_nps = get_player_balance(player_id) if player_id else None

                if balance_nps is None:
                    failed += 1
                else:
                    if save_daily_snapshot(user_id, boundary_day, balance_nps):
                        taken += 1
            except Exception as e:
                failed += 1
                print(f"⚠️ خطأ أثناء لقطة منتصف الليل للمستخدم {user_id}: {e}")

            await asyncio.sleep(DAILY_SNAPSHOT_USER_PAUSE)

        # عند وجود إخفاقات نترك الجولة مفتوحة ليُعاد المحاولة ضمن مهلة السماح
        if failed == 0:
            mark_snapshot_run_done(boundary_day, taken, failed)

        return {"status": "ok", "taken": taken, "failed": failed}


async def process_daily_cashback(bot, target_day=None):
    """
    ينفّذ التعويض اليومي عن يوم target_day (الافتراضي: أمس بتوقيت سوريا).
    آمن للتكرار: لا يُصرف لأي لاعب مرتين لنفس اليوم.
    """
    async with _daily_lock("payout"):

        if target_day is None:
            target_day = _shift_day(syria_now().strftime("%Y-%m-%d"), -1)

        if is_daily_run_done(target_day):
            return {"status": "already_done", "target_day": target_day}

        # نسبة اليومي من إعدادات الأدمن؛ 0% = لا نبدأ ولا نحرق اليوم
        # (كوبونات عجلة الحظ ليوم target_day تُعوَّض حتى لو كان برنامج اليومي متوقفاً)
        program_on = get_cashback_program_enabled("daily")
        rate = get_daily_cashback_percent() if program_on else 0.0

        if rate <= 0 and not has_daily_coupon_for_day(target_day):
            return {"status": "percent_zero", "target_day": target_day}

        end_boundary = _shift_day(target_day, 1)

        # بدون أي لقطة نهاية يوم لا يمكن معرفة رصيد 12:00 ليلاً → لا نبدأ ولا نحرق اليوم
        if count_daily_snapshots(end_boundary) == 0:
            return {"status": "no_snapshots", "target_day": target_day}

        attempt = begin_daily_run(target_day)

        counts = {
            "processed": 0, "paid_now": 0, "already_paid": 0,
            "balance_not_zero": 0, "winner": 0, "no_loss": 0,
            "no_snapshot": 0, "errors": 0,
        }

        choice_users = (
            {uid for uid, _u in _cashback_program_users("daily")} if program_on else set()
        )

        payout_rows = []

        for user_id, _username in _daily_pool([target_day]):

            counts["processed"] += 1

            try:
                # النسبة الفعلية للاعب: نسبة اليومي (إن كان مشتركاً) أو كوبون العجلة — الأعلى منهما
                user_rate = max(
                    rate if user_id in choice_users else 0.0,
                    get_daily_coupon_percent(user_id, target_day) / 100.0,
                )

                if user_rate <= 0:
                    counts["no_loss"] += 1
                    continue

                status, loss_nps = _daily_user_outcome(user_id, target_day)

                if status != "eligible":
                    counts[status] += 1
                    continue

                loss_amount = int(nps_to_ls(loss_nps))
                cashback_amount = int(loss_amount * user_rate)

                if cashback_amount <= 0:
                    counts["no_loss"] += 1
                    continue

                if not record_daily_payout_and_credit(user_id, target_day, loss_amount, cashback_amount):
                    counts["already_paid"] += 1
                    continue

                counts["paid_now"] += 1
                payout_rows.append(
                    (await _tg_user_label(bot, user_id), user_id, loss_amount, cashback_amount)
                )

                try:
                    await bot.send_message(
                        user_id,
                        "🎁 تم صرف تعويضك اليومي!\n\n"
                        f"📅 عن خسارة يوم: {target_day}\n"
                        f"📉 خسارتك: {loss_amount:,} {CURRENCY_LABEL}\n"
                        f"💸 نسبة التعويض: {user_rate * 100:.1f}%\n"
                        f"💰 مبلغ التعويض: {cashback_amount:,} {CURRENCY_LABEL}\n\n"
                        f"💳 رصيدك الحالي: {get_user_balance(user_id):,} {CURRENCY_LABEL}"
                    )
                except Exception:
                    pass

            except Exception as e:
                counts["errors"] += 1
                print(f"⚠️ خطأ أثناء التعويض اليومي للمستخدم {user_id}: {e}")

        # سجل قناة الشحن: من تعوّض في هذه المحاولة (قبل أي إعادة محاولة حتى لا يضيع سجلهم)
        await _log_cashback_payouts(bot, "daily", payout_rows, f" — عن يوم {target_day}")

        # أخطاء مؤقتة (مثلاً قاعدة البيانات مشغولة): نترك اليوم مفتوحاً لمحاولة تالية
        if counts["errors"] and attempt < DAILY_RUN_MAX_ATTEMPTS:
            return {"status": "retry", "target_day": target_day, "attempt": attempt, **counts}

        paid_total, amount_total = get_daily_payout_totals(target_day)
        summary = {**counts, "paid_total": paid_total, "total_cashback": amount_total,
                   "attempts": attempt, "percent": rate}
        finish_daily_run(target_day, summary)
        settle_daily_coupons(target_day)       # كوبونات هذا اليوم صالحة ليوم واحد فقط

        return {"status": "completed", "target_day": target_day, **summary}


def _daily_summary_text(result):
    return (
        "🌅 تم تنفيذ التعويض اليومي\n\n"
        f"📅 عن خسارة يوم: {result['target_day']}\n"
        f"👥 تم فحص: {result['processed']} مستخدم\n"
        f"✅ تم تعويض: {result['paid_total']} مستخدم\n"
        f"💰 إجمالي التعويض: {result['total_cashback']:,} {CURRENCY_LABEL}\n"
        + (f"💸 نسبة التعويض: {result['percent'] * 100:.1f}%\n" if "percent" in result else "")
        + "\nغير مستحقين:\n"
        f"• رصيد ≠ 0 عند 12 ليلاً: {result['balance_not_zero']}\n"
        f"• رابحون (سحب > إيداع): {result['winner']}\n"
        f"• بلا خسارة: {result['no_loss']}\n"
        f"• بلا لقطة منتصف الليل: {result['no_snapshot']}"
        + (f"\n⚠️ أخطاء: {result['errors']}" if result.get("errors") else "")
    )


UTC_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def _ar_count(n, one, two, plural, many):
    """عدّ عربي صحيح: واحد / اثنان / 3-10 جمع / 11+ مفرد منصوب."""
    n = int(n)
    if n == 1:
        return one
    if n == 2:
        return two
    if 3 <= n <= 10:
        return f"{n} {plural}"
    return f"{n} {many}"


def ar_weeks(n):
    return _ar_count(n, "أسبوع واحد", "أسبوعان", "أسابيع", "أسبوعاً")


def ar_days(n):
    return _ar_count(n, "يوم واحد", "يومان", "أيام", "يوماً")


def _utc_to_syria_dt(ts_utc):
    return datetime.strptime(ts_utc, UTC_TS_FORMAT) + timedelta(hours=SYRIA_UTC_OFFSET_HOURS)


def _utc_to_syria_day(ts_utc):
    """يوم سوريا لتوقيت UTC مخزَّن؛ None إذا كانت القيمة تالفة (لا نُسقط القائمة/الجدولة بسببها)."""
    try:
        return _utc_to_syria_dt(ts_utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _program_status_text(mode):
    """وصف مدة النظام الفعّال (للقائمة ولرسائل التفعيل). نص فارغ إذا الوضع متوقف."""
    if mode == "weekly":
        weeks = get_weekly_duration_weeks()
        if weeks <= 0:
            return "⏳ المدة: بدون حد (يعمل حتى توقفه يدوياً)"

        started_at = get_program_started_at("weekly")
        if not started_at:
            return f"⏳ المدة: {ar_weeks(weeks)} (تبدأ من لحظة التفعيل)"

        end_at = weekly_program_end(started_at, weeks)
        if end_at is None:                       # لحظة بداية تالفة
            return f"⏳ المدة: {ar_weeks(weeks)}"

        remaining = datetime.strptime(end_at, UTC_TS_FORMAT) - datetime.utcnow()
        remaining_days = max(0, math.ceil(remaining.total_seconds() / 86400))
        remaining_text = ar_days(remaining_days) if remaining_days > 0 else "أقل من يوم"

        return (
            f"⏳ المدة: {ar_weeks(weeks)}\n"
            f"   بدأ: {_utc_to_syria_dt(started_at).strftime('%Y-%m-%d %H:%M')}\n"
            f"   ينتهي: {_utc_to_syria_dt(end_at).strftime('%Y-%m-%d %H:%M')} (المتبقي ≈ {remaining_text})\n"
            "   عند الانتهاء تُنفَّذ جولة أخيرة ثم يتوقف تلقائياً."
        )

    if mode == "daily":
        days = get_daily_duration_days()
        if days <= 0:
            return "⏳ المدة: بدون حد (يعمل حتى توقفه يدوياً)"

        started_at = get_program_started_at("daily")
        if not started_at:
            return f"⏳ المدة: {ar_days(days)} (تبدأ من لحظة التفعيل)"

        start_day = _utc_to_syria_day(started_at)
        if start_day is None:                    # لحظة بداية تالفة
            return f"⏳ المدة: {ar_days(days)}"

        last_target = _shift_day(start_day, days)

        return (
            f"⏳ المدة: {ar_days(days)} كاملة بعد يوم التفعيل\n"
            f"   بدأ: {start_day}\n"
            f"   آخر يوم يُعوَّض: {last_target} (يُصرف الساعة {DAILY_CASHBACK_HOUR}:00 صباح {_shift_day(last_target, 1)})\n"
            "   بعد صرف آخر يوم يتوقف تلقائياً."
        )

    return ""


async def _finish_cashback_program(bot, text, program):
    """نهاية مدة برنامج: إيقافه وحده (يمسح لحظة بدايته) وإبلاغ الأدمن."""
    set_cashback_program_enabled(program, False)
    await notify_deposits(bot, text)


async def daily_cashback_tick(bot, now=None):
    """
    خطوة فحص واحدة (تُستدعى كل دقيقة). كل ما فيها idempotent:
      1) أول 15 دقيقة بعد منتصف الليل → لقطة أرصدة منتصف الليل.
      2) من الساعة 9:00 صباحاً وحتى نهاية اليوم → صرف تعويض أمس (إن لم يُصرف).
      3) نهاية المدة المحددة (إن وُجدت) → إيقاف تلقائي وإبلاغ الأدمن.

    الأيام المشمولة بالتعويض = الأيام الكاملة بعد يوم التفعيل؛ يوم التفعيل نفسه جزئي فلا يُعوَّض.
    """
    actions = {}

    program_on = get_cashback_program_enabled("daily")

    # برنامج اليومي متوقف: نكمل فقط إن كانت هناك كوبونات عجلة حظ ما زالت تنتظر التعويض
    if not program_on and not has_active_daily_coupons():
        return actions

    now = now or syria_now()
    today = now.strftime("%Y-%m-%d")
    yesterday = _shift_day(today, -1)

    # كوبون يوم فاتت نافذة صرفه ينتهي بدل أن يبقى معلّقاً
    expire_old_daily_coupons(yesterday)

    days = get_daily_duration_days() if program_on else 0
    started_at = get_program_started_at("daily") if program_on else None

    if days > 0 and not started_at:
        started_at = ensure_program_started_at("daily")

    start_day = _utc_to_syria_day(started_at) if started_at else None
    last_target = _shift_day(start_day, days) if (days > 0 and start_day) else None

    # (1) لقطة منتصف الليل — لا حاجة لها بعد نهاية حدود آخر يوم معوَّض
    snapshot_needed = last_target is None or today <= _shift_day(last_target, 1)

    if (
        snapshot_needed
        and now.hour * 60 + now.minute < DAILY_SNAPSHOT_GRACE_MINUTES
        and not is_snapshot_run_done(today)
    ):
        actions["snapshot"] = await take_daily_snapshots(today)

    # (2) صرف تعويض أمس
    if now.hour >= DAILY_CASHBACK_HOUR:
        in_program = (
            (start_day is None or yesterday > start_day)
            and (last_target is None or yesterday <= last_target)
        )

        in_program = in_program or has_daily_coupon_for_day(yesterday)

        if in_program and not is_daily_run_done(yesterday):
            result = await process_daily_cashback(bot, yesterday)
            actions["payout"] = result

            if result.get("status") == "completed":
                await notify_deposits(bot, _daily_summary_text(result))

    # (3) نهاية المدة
    if last_target and (yesterday > last_target or is_daily_run_done(last_target)):
        text = (
            f"⏳ انتهت مدة التعويض اليومي ({ar_days(days)}) وتم إيقافه تلقائياً.\n"
            f"📅 آخر يوم معوَّض: {last_target}"
        )
        if not is_daily_run_done(last_target):
            text += f"\n⚠️ لم يُنفَّذ تعويض يوم {last_target} (البوت كان متوقفاً وقت الصرف)."

        await _finish_cashback_program(bot, text, "daily")
        actions["finished"] = True

    return actions


async def weekly_program_tick(bot, now_utc=None):
    """
    نهاية مدة الأسبوعي (إن حدّد الأدمن عدد أسابيع): عند بلوغ لحظة الانتهاء ننفّذ
    جولة أخيرة لكل المشتركين (حتى لا تضيع خسائر آخر الفترة) ثم نوقف الأسبوعي تلقائياً.
    """
    if not get_cashback_program_enabled("weekly"):
        return {}

    weeks = get_weekly_duration_weeks()

    if weeks <= 0:
        return {}

    end_at = weekly_program_end(ensure_program_started_at("weekly"), weeks)

    if end_at is None:                           # لحظة بداية تالفة: لا نخمّن
        return {}

    now_str = now_utc or datetime.utcnow().strftime(UTC_TS_FORMAT)

    if now_str < end_at:
        return {}

    result = await process_weekly_cashback(bot, force=True)

    await _finish_cashback_program(
        bot,
        f"⏳ انتهت مدة التعويض الأسبوعي ({ar_weeks(weeks)}) وتم إيقافه تلقائياً.\n\n"
        "الجولة الأخيرة:\n"
        f"👥 تم فحص: {result['processed']} مستخدم\n"
        f"✅ تم صرف استرداد لـ: {result['paid']} مستخدم\n"
        f"💰 إجمالي المسترد: {result['total_cashback']:,} {CURRENCY_LABEL}",
        "weekly",
    )

    return {"expired": True, "result": result}


async def cashback_scheduler_loop(application):
    """حلقة خلفية: كل دقيقة — لقطة منتصف الليل + صرف الـ 9 صباحاً (اليومي) + انتهاء المدد."""

    await asyncio.sleep(CASHBACK_INITIAL_DELAY_SECONDS)

    while True:
        for tick in (daily_cashback_tick, weekly_program_tick):
            try:
                await tick(application.bot)
            except Exception as e:
                print(f"⚠️ خطأ في جدولة التعويض ({tick.__name__}): {e}")

        await asyncio.sleep(DAILY_LOOP_POLL_SECONDS)


async def rebaseline_weekly_snapshots(skip_user_ids=()):
    """
    عند العودة للأسبوعي بعد اليومي/الإيقاف: نثبّت نقطة بداية جديدة لكل اللاعبين.
    بدونها سيُحسب الأسبوعي على كل المدة السابقة فيُعوَّض ما عُوِّض يومياً مرة ثانية.
    """
    for user_id, ichancy_username in _cashback_program_users("weekly"):
        if user_id in skip_user_ids:
            continue

        try:
            player = get_player_by_username(ichancy_username)
            player_id = player.get("playerId") if player else None
            balance_nps = get_player_balance(player_id) if player_id else None

            if balance_nps is not None:
                update_cashback_snapshot(user_id, balance_nps)
        except Exception as e:
            print(f"⚠️ خطأ أثناء تثبيت نقطة بداية الأسبوعي للمستخدم {user_id}: {e}")

        await asyncio.sleep(DAILY_SNAPSHOT_USER_PAUSE)


_CHOICE_LABELS = {"daily": "اليومي", "weekly": "الأسبوعي"}


def _cashback_menu_content():
    weekly_on = get_cashback_program_enabled("weekly")
    daily_on = get_cashback_program_enabled("daily")
    weekly_percent = get_cashback_percent()
    daily_percent = get_daily_cashback_percent()
    weeks = get_weekly_duration_weeks()
    days = get_daily_duration_days()
    counts = count_cashback_choices()
    choice_on = is_cashback_user_choice_enabled()

    if choice_on:
        choice_note = (
            "👥 اختيار المستخدم: ✅ مفعّل\n"
            "كل مستخدم يختار نظامه (يومي أو أسبوعي) عند أول استخدام للبوت "
            "ولا يستطيع تغييره لاحقاً. الأدمن فقط يعدّل.\n\n"
        )
        weekly_subs = str(counts["weekly"])
        daily_subs = str(counts["daily"])
        unchosen_line = f"❔ وافقوا على الشروط ولم يختاروا بعد: {counts['none']}\n\n"
    else:
        choice_note = (
            "ℹ️ يعمل نظام واحد فقط في كل مرة (يومي أو أسبوعي) ويُطبَّق على جميع المستخدمين. "
            "الأدمن يفعّله ويحدد نسبته ومدته.\n\n"
        )
        weekly_subs = "كل المستخدمين" if weekly_on else "—"
        daily_subs = "كل المستخدمين" if daily_on else "—"
        unchosen_line = ""

    weekly_duration = ar_weeks(weeks) if weeks > 0 else "بدون حد"
    daily_duration = ar_days(days) if days > 0 else "بدون حد"

    def state(on):
        return "✅ مفعّل" if on else "⛔ متوقف"

    text = (
        "💸 نظام التعويض (Cashback)\n\n"
        + choice_note
        + f"📅 الأسبوعي: {state(weekly_on)}\n"
        f"   • النسبة: {weekly_percent * 100:.1f}% من صافي الخسارة، تُصرف لكل مستخدم كل 7 أيام\n"
        f"   • المدة: {weekly_duration}\n"
        f"   • المشتركون: {weekly_subs}\n\n"
        f"🌅 اليومي: {state(daily_on)}\n"
        f"   • النسبة: {daily_percent * 100:.1f}% من خسارة اليوم السابق، الساعة {DAILY_CASHBACK_HOUR}:00 صباحاً (توقيت سوريا)\n"
        f"   • المدة: {daily_duration}\n"
        f"   • المشتركون: {daily_subs}\n\n"
        + unchosen_line
    )

    for program, on in (("weekly", weekly_on), ("daily", daily_on)):
        if on:
            status = _program_status_text(program)
            if status:
                text += f"{status}\n\n"

    if not choice_on and weekly_on and daily_on:
        text += "⚠️ الأسبوعي واليومي مفعّلان معاً — لن يُصرف شيء حتى توقف أحدهما.\n\n"

    if weekly_on and weekly_percent <= 0:
        text += "⚠️ نسبة الأسبوعي 0% — لن يصرف شيئاً حتى تعدّلها.\n\n"
    if daily_on and daily_percent <= 0:
        text += "⚠️ نسبة اليومي 0% — لن يصرف شيئاً حتى تعدّلها.\n\n"

    text += "اختر إجراء:"

    choice_rows = [
        [InlineKeyboardButton("👤 تغيير نظام مستخدم", callback_data="admin_cashback_user")],
        [
            InlineKeyboardButton("👥 تعيين غير المختارين: يومي", callback_data="admin_cashback_bulk_daily"),
            InlineKeyboardButton("👥 تعيين غير المختارين: أسبوعي", callback_data="admin_cashback_bulk_weekly"),
        ],
    ] if choice_on else []

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⛔ إيقاف الأسبوعي" if weekly_on else "✅ تفعيل الأسبوعي",
                callback_data="admin_cashback_toggle_weekly"),
            InlineKeyboardButton(
                "⛔ إيقاف اليومي" if daily_on else "✅ تفعيل اليومي",
                callback_data="admin_cashback_toggle_daily"),
        ],
        [
            InlineKeyboardButton("✏️ نسبة الأسبوعي", callback_data="admin_set_cashback_percent"),
            InlineKeyboardButton("✏️ نسبة اليومي", callback_data="admin_set_daily_percent"),
        ],
        [
            InlineKeyboardButton("⏳ مدة الأسبوعي", callback_data="admin_set_weekly_weeks"),
            InlineKeyboardButton("⏳ مدة اليومي", callback_data="admin_set_daily_days"),
        ],
        [
            InlineKeyboardButton("▶️ تنفيذ الأسبوعي الآن", callback_data="admin_run_cashback_now_weekly"),
            InlineKeyboardButton("▶️ تنفيذ اليومي الآن", callback_data="admin_run_cashback_now_daily"),
        ],
        *choice_rows,
        [
            InlineKeyboardButton("📊 سجل الأسبوعي", callback_data="admin_cashback_history"),
            InlineKeyboardButton("📊 سجل اليومي", callback_data="admin_cashback_history_daily"),
        ],
        [InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel")]
    ])

    return text, keyboard


async def _safe_edit_message(query, text, reply_markup=None):
    """تيليغرام يرفض تعديل رسالة بنفس محتواها ("Message is not modified") — نتجاهلها فقط."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except Exception as e:
        if "not modified" not in str(e).lower():
            raise


async def admin_cashback_menu_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    text, keyboard = _cashback_menu_content()
    await _safe_edit_message(query, text, reply_markup=keyboard)


async def admin_cashback_toggle_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    program = query.data.rsplit("_", 1)[-1]

    if program not in CASHBACK_CHOICES:
        return

    was_on = get_cashback_program_enabled(program)

    # بدون اختيار المستخدم يعمل نظام واحد فقط للجميع: تفعيل أحدهما يوقف الآخر
    other_stopped = None
    if not was_on and not is_cashback_user_choice_enabled():
        other = "daily" if program == "weekly" else "weekly"
        if get_cashback_program_enabled(other):
            set_cashback_program_enabled(other, False)
            other_stopped = other

    set_cashback_program_enabled(program, not was_on)

    if was_on:
        note = f"⛔ تم إيقاف التعويض {_CHOICE_LABELS[program]}."
    elif program == "weekly":
        context.application.create_task(rebaseline_weekly_snapshots())
        note = (
            "✅ تم تفعيل التعويض الأسبوعي.\n"
            "📌 جاري تثبيت نقطة بداية جديدة لمشتركيه، والحساب يبدأ من الآن."
        )
    else:
        note = (
            "✅ تم تفعيل التعويض اليومي.\n"
            "📌 تُؤخذ اللقطات من منتصف الليل القادم لمشتركيه، وأول صرف يكون الساعة "
            f"{DAILY_CASHBACK_HOUR}:00 صباحاً بعد ليلتين (يلزم لقطتان: بداية اليوم ونهايته)."
        )

    if other_stopped:
        note += (
            f"\n\n⛔ تم إيقاف التعويض {_CHOICE_LABELS[other_stopped]} تلقائياً "
            "(بدون اختيار المستخدم يعمل نظام واحد فقط لكل المستخدمين)."
        )

    if not was_on:
        status_text = _program_status_text(program)
        if status_text:
            note += "\n\n" + status_text

    await query.message.reply_text(note)

    text, keyboard = _cashback_menu_content()
    await _safe_edit_message(query, text, reply_markup=keyboard)


async def admin_cashback_user_callback(update, context):
    """يطلب رقم المستخدم لتغيير نظام تعويضه."""
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    context.user_data["admin_cashback_user_lookup"] = True

    await query.message.reply_text(
        "👤 أرسل رقم (Telegram ID) المستخدم لتغيير نظام تعويضه:",
        reply_markup=cancel_keyboard()
    )


async def _send_admin_cashback_user_card(message, target_id):
    info = get_user_cashback_info(target_id)

    back = [InlineKeyboardButton("⬅️ رجوع", callback_data="admin_cashback_menu")]

    if info is None:
        await message.reply_text(
            "❌ المستخدم غير موجود.",
            reply_markup=InlineKeyboardMarkup([back])
        )
        return

    choice = info["cashback_choice"]
    current = _CHOICE_LABELS.get(choice, "لم يختر بعد")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌅 تعيين يومي", callback_data=f"admin_cashback_setuser_daily_{target_id}"),
            InlineKeyboardButton("📅 تعيين أسبوعي", callback_data=f"admin_cashback_setuser_weekly_{target_id}"),
        ],
        [InlineKeyboardButton("🔄 مسح الاختيار (يُسأل من جديد)", callback_data=f"admin_cashback_setuser_clear_{target_id}")],
        back,
    ])

    await message.reply_text(
        f"👤 المستخدم: {target_id}\n"
        f"💸 نظام التعويض الحالي: {current}"
        + (f"\n🕒 تاريخ الاختيار: {info['cashback_chosen_at']}" if info.get("cashback_chosen_at") else ""),
        reply_markup=keyboard
    )


async def admin_cashback_setuser_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    m = re.fullmatch(r"admin_cashback_setuser_(daily|weekly|clear)_(\d+)", query.data or "")

    if not m:
        return

    action, target_id = m.group(1), int(m.group(2))
    new_choice = None if action == "clear" else action

    result = admin_set_user_cashback_choice(target_id, new_choice, f"admin:{query.from_user.id}")

    if not result["ok"]:
        await query.message.reply_text("❌ المستخدم غير موجود.")
        return

    try:
        if new_choice:
            await context.bot.send_message(
                target_id,
                f"ℹ️ قامت الإدارة بتعيين نظام التعويض الخاص بك إلى: {_CHOICE_LABELS[new_choice]}."
            )
        else:
            await context.bot.send_message(
                target_id,
                "ℹ️ طلبت منك الإدارة إعادة اختيار نظام التعويض. أرسل أي رسالة للبوت لتختار."
            )
    except Exception:
        pass

    await query.message.reply_text(
        f"✅ تم تحديث المستخدم {target_id}: "
        + (_CHOICE_LABELS[new_choice] if new_choice else "تم مسح الاختيار")
    )


async def admin_cashback_bulk_callback(update, context):
    """تعيين نظام لكل من لم يختر بعد (خطوتان: تأكيد ثم تنفيذ)."""
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    m = re.fullmatch(r"admin_cashback_(bulk|bulkgo)_(daily|weekly)", query.data or "")

    if not m:
        return

    step, choice = m.group(1), m.group(2)

    if step == "bulk":
        count = count_cashback_choices()["none"]

        if count == 0:
            await query.message.reply_text("ℹ️ لا يوجد مستخدمون بدون اختيار.")
            return

        await query.message.reply_text(
            f"⚠️ سيتم تعيين التعويض {_CHOICE_LABELS[choice]} لـ {count} مستخدم لم يختاروا بعد.\n"
            "لن يُسألوا مرة أخرى، ويمكنك تعديل أي منهم لاحقاً.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأكيد", callback_data=f"admin_cashback_bulkgo_{choice}")],
                [InlineKeyboardButton("⬅️ إلغاء", callback_data="admin_cashback_menu")],
            ])
        )
        return

    count = bulk_assign_unchosen_cashback(choice, f"admin:{query.from_user.id}")

    await query.message.reply_text(
        f"✅ تم تعيين التعويض {_CHOICE_LABELS[choice]} لـ {count} مستخدم."
    )


async def admin_set_cashback_percent_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    context.user_data["admin_set_cashback_percent"] = True
    current = get_cashback_percent() * 100

    await query.message.reply_text(
        f"💸 نسبة الاسترداد الأسبوعي الحالية: {current:.1f}%\n\n"
        f"أرسل النسبة الجديدة (رقم فقط، مثال: 10 لتعني 10%):\n"
        f"أرسل 0 لإيقاف الاسترداد الأسبوعي.",
        reply_markup=cancel_keyboard()
    )


def parse_admin_number(text):
    """رقم عشري من مدخل الأدمن: يقبل الأرقام العربية و% والفاصلة العشرية، ويرفض NaN/inf/السالب."""
    cleaned = (
        (text or "").strip()
        .translate(_ARABIC_DIGITS)
        .replace("٫", ".").replace("،", ".").replace(",", ".")
        .replace("%", "").strip()
    )
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", cleaned):
        return None
    return float(cleaned)


def parse_admin_int(text):
    """عدد صحيح غير سالب من مدخل الأدمن (يقبل الأرقام العربية)."""
    cleaned = (text or "").strip().translate(_ARABIC_DIGITS)
    if not (cleaned.isascii() and cleaned.isdigit()):
        return None
    return int(cleaned)


def _gift_admin_menu():
    enabled = is_gift_enabled()
    minimum = get_gift_min_amount()
    text = (
        "🎁 إدارة الإهداء\n\n"
        f"الحالة: {'🟢 مفعّل' if enabled else '🔴 متوقف'}\n"
        f"💰 الحد الأدنى لمبلغ الإهداء: {minimum:,} {CURRENCY_LABEL}"
        + (" (بلا حد أدنى)" if minimum == 0 else "") + "\n"
        "♾️ عدد الهدايا التي يستلمها المستخدم: غير محدود\n\n"
        "• أي إهداء أقل من الحد الأدنى يُرفض."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تعديل الحد الأدنى", callback_data="admin_gift_min_edit")],
        [InlineKeyboardButton("🔴 إيقاف الإهداء" if enabled else "🟢 تشغيل الإهداء", callback_data="admin_gift_toggle")],
        [InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel")],
    ])
    return text, kb


async def admin_gift_limit_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    data = query.data

    if data == "admin_gift_min_edit":
        context.user_data["admin_gift_limit_input"] = True
        await query.message.reply_text(
            f"✏️ الحد الأدنى الحالي لمبلغ الإهداء: {get_gift_min_amount():,} {CURRENCY_LABEL}\n\n"
            "أرسل الرقم الجديد (عدد صحيح بالليرة، من 0 إلى 1,000,000):\n"
            "• 0 = بدون حد أدنى.",
            reply_markup=cancel_keyboard()
        )
        return

    if data == "admin_gift_toggle":
        set_gift_enabled(not is_gift_enabled())

    context.user_data.pop("admin_gift_limit_input", None)
    text, kb = _gift_admin_menu()
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


async def admin_set_daily_percent_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    context.user_data["admin_set_daily_cashback_percent"] = True
    current = get_daily_cashback_percent() * 100

    await query.message.reply_text(
        f"🌅 نسبة التعويض اليومي الحالية: {current:.1f}%\n\n"
        f"أرسل النسبة الجديدة (رقم فقط، مثال: 100 للتعويض الكامل، أو 50 لنصف الخسارة):\n"
        f"أرسل 0 لإيقاف الصرف اليومي مؤقتاً.",
        reply_markup=cancel_keyboard()
    )


async def admin_set_weekly_weeks_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    context.user_data["admin_set_weekly_weeks"] = True
    weeks = get_weekly_duration_weeks()

    await query.message.reply_text(
        f"⏳ مدة التعويض الأسبوعي الحالية: {ar_weeks(weeks) if weeks > 0 else 'بدون حد'}\n\n"
        f"أرسل عدد الأسابيع (رقم صحيح، 0 = بدون حد):\n"
        f"• تُحسب المدة من لحظة تفعيل الأسبوعي.\n"
        f"• عند الانتهاء تُنفَّذ جولة أخيرة ثم يتوقف التعويض تلقائياً.",
        reply_markup=cancel_keyboard()
    )


async def admin_set_daily_days_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    context.user_data["admin_set_daily_days"] = True
    days = get_daily_duration_days()

    await query.message.reply_text(
        f"⏳ مدة التعويض اليومي الحالية: {ar_days(days) if days > 0 else 'بدون حد'}\n\n"
        f"أرسل عدد الأيام (رقم صحيح، 0 = بدون حد):\n"
        f"• تُحسب الأيام الكاملة بعد يوم التفعيل (يوم التفعيل نفسه لا يُحسب).\n"
        f"• بعد صرف آخر يوم يتوقف التعويض تلقائياً.",
        reply_markup=cancel_keyboard()
    )


async def admin_run_cashback_now_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    program = query.data.rsplit("_", 1)[-1]

    if program not in CASHBACK_CHOICES:
        return

    if not get_cashback_program_enabled(program):
        await query.message.reply_text(
            f"⛔ التعويض {_CHOICE_LABELS[program]} متوقف حالياً. فعّله أولاً."
        )
        return

    if program == "daily":
        await query.message.reply_text("⏳ جاري تنفيذ التعويض اليومي (عن خسارة أمس)...")

        result = await process_daily_cashback(context.bot)
        status = result.get("status")

        if status == "already_done":
            await query.message.reply_text(f"ℹ️ تعويض يوم {result['target_day']} نُفّذ مسبقاً.")
        elif status == "no_snapshots":
            await query.message.reply_text(
                f"⚠️ لا توجد لقطات منتصف الليل ليوم {result['target_day']}.\n"
                "النظام جديد، أو كان البوت متوقفاً وقت منتصف الليل."
            )
        elif status == "percent_zero":
            await query.message.reply_text(
                "⚠️ نسبة التعويض اليومي 0% — لم يُنفَّذ شيء. عدّل النسبة من زر ✏️ نسبة اليومي."
            )
        elif status == "retry":
            await query.message.reply_text(
                "⚠️ حصلت أخطاء مؤقتة أثناء التنفيذ، سيُعاد تلقائياً خلال دقيقة."
            )
        else:
            await query.message.reply_text(_daily_summary_text(result))
        return

    await query.message.reply_text("⏳ جاري تنفيذ جولة الاسترداد الأسبوعي يدوياً لكل المشتركين...")

    result = await process_weekly_cashback(context.bot, force=True)

    await query.message.reply_text(
        "✅ انتهى التنفيذ اليدوي للاسترداد الأسبوعي\n\n"
        f"👥 تم فحص: {result['processed']} مستخدم\n"
        f"✅ تم صرف استرداد لـ: {result['paid']} مستخدم\n"
        f"💰 إجمالي المسترد: {result['total_cashback']:,} {CURRENCY_LABEL}"
    )


async def admin_cashback_history_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    payouts = get_recent_cashback_payouts(limit=15)

    if not payouts:
        await query.edit_message_text(
            "📊 لا يوجد أي عمليات استرداد منفذة بعد.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ رجوع", callback_data="admin_cashback_menu")]
            ])
        )
        return

    text = "📊 آخر عمليات الاسترداد الأسبوعي:\n\n"
    for p in payouts:
        text += (
            f"🆔 {p['user_id']} | "
            f"📉 خسارة: {p['loss_amount']:,} | "
            f"💰 استرداد: {p['cashback_amount']:,} {CURRENCY_LABEL}\n"
        )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ رجوع", callback_data="admin_cashback_menu")]
    ])

    await query.edit_message_text(text, reply_markup=keyboard)


async def admin_cashback_history_daily_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    back = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ رجوع", callback_data="admin_cashback_menu")]
    ])

    payouts = get_recent_daily_payouts(limit=15)

    if not payouts:
        await query.edit_message_text("📊 لا يوجد أي عمليات تعويض يومي منفذة بعد.", reply_markup=back)
        return

    text = "📊 آخر عمليات التعويض اليومي:\n\n"
    for p in payouts:
        text += (
            f"🆔 {p['user_id']} | "
            f"📅 {p['target_day']} | "
            f"📉 {p['loss_amount']:,} | "
            f"💰 {p['cashback_amount']:,} {CURRENCY_LABEL}\n"
        )

    await query.edit_message_text(text, reply_markup=back)


# ============================
# عمولة الإحالة المستمرة (Referral Commission) — المنطق الأساسي
# ============================

async def process_referral_payouts(bot):
    """
    يفحص كل المُحيلين (referrers)، ولمن استحقت دورته (مرّ عدد أيام
    الدورة المحدد منذ آخر توزيع)، يصرف له كل العمولات المتراكمة
    غير المدفوعة دفعة واحدة، ثم يبدأ له دورة جديدة.
    """

    period_days = get_referral_period_days()

    if period_days <= 0:
        return {"checked": 0, "paid_count": 0, "total_paid": 0}

    now = datetime.utcnow()
    referrer_ids = get_all_referrer_ids()

    paid_count = 0
    total_paid = 0

    for referrer_id in referrer_ids:

        try:
            init_referral_cycle_if_missing(referrer_id)
            cycle_start_raw = get_referral_cycle_start(referrer_id)

            if not cycle_start_raw:
                continue

            cycle_start_dt = datetime.strptime(cycle_start_raw, "%Y-%m-%d %H:%M:%S")
            elapsed_days = (now - cycle_start_dt).total_seconds() / 86400

            if elapsed_days < period_days:
                continue

            pending = get_pending_commission_total(referrer_id)

            if pending > 0:
                update_balance(referrer_id, pending)
                mark_referral_commissions_paid(referrer_id)
                paid_count += 1
                total_paid += pending

                try:
                    await bot.send_message(
                        referrer_id,
                        "💰 تم توزيع أرباح الإحالة الخاصة بك!\n\n"
                        f"💵 المبلغ: {pending:,} {CURRENCY_LABEL}\n"
                        f"💳 رصيدك الحالي: {get_user_balance(referrer_id):,} {CURRENCY_LABEL}\n\n"
                        f"🔄 بدأت دورة أرباح جديدة مدتها {period_days} يوم/أيام."
                    )
                except Exception:
                    pass

            reset_referral_cycle(referrer_id)

        except Exception as e:
            print(f"⚠️ خطأ أثناء معالجة عمولة الإحالة للمستخدم {referrer_id}: {e}")

    return {"checked": len(referrer_ids), "paid_count": paid_count, "total_paid": total_paid}


async def referral_payout_loop(application):
    """
    حلقة خلفية تعمل طوال عمر البوت وتفحص دورياً أي المُحيلين استحقوا
    توزيع أرباح الإحالة، وتصرف لهم تلقائياً بدون أي تدخل من الأدمن.
    """

    await asyncio.sleep(REFERRAL_CHECK_INITIAL_DELAY_SECONDS)

    while True:
        try:
            result = await process_referral_payouts(application.bot)
            if result["paid_count"] > 0:
                await notify_deposits(
                    application.bot,
                    "💰 تم تنفيذ جولة توزيع أرباح الإحالة\n\n"
                    f"👥 تم فحص: {result['checked']} محيل\n"
                    f"✅ تم الصرف لـ: {result['paid_count']} محيل\n"
                    f"💵 إجمالي الموزّع: {result['total_paid']:,} {CURRENCY_LABEL}"
                )
        except Exception as e:
            print(f"⚠️ خطأ أثناء تنفيذ جولة توزيع أرباح الإحالة: {e}")

        await asyncio.sleep(REFERRAL_CHECK_INTERVAL_SECONDS)


async def admin_referral_menu_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    percent = get_referral_commission_percent()
    period_days = get_referral_period_days()

    text = (
        "🎯 نظام الإحالة (عمولة مستمرة)\n\n"
        f"📊 نسبة العمولة: {percent * 100:.1f}%\n"
        f"⏱️ مدة دورة الأرباح: {period_days} يوم/أيام\n"
        "📌 الأساس: نسبة من كل عمليات شحن المُحالين\n"
        "🤖 الصرف: تلقائي بالكامل عند اكتمال كل دورة\n\n"
        + ("⚠️ النسبة 0% حالياً، يعني النظام متوقف مؤقتاً.\n\n" if percent <= 0 else "")
        + "اختر إجراء:"
    )

    if referral_burn.is_new_system_active():
        text = "⚠️ النظام القديم متوقف حالياً — نظام الإحالات حسب الحرق مفعّل.\n\n" + text

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌳 نظام الإحالات حسب الحرق (تقارير وصرف)", callback_data="ref2:menu")],
        [InlineKeyboardButton("✏️ تعديل نسبة العمولة", callback_data="admin_set_referral_percent")],
        [InlineKeyboardButton("⏱️ تعديل مدة الدورة (أيام)", callback_data="admin_set_referral_period")],
        [InlineKeyboardButton("▶️ تنفيذ التوزيع الآن", callback_data="admin_run_referral_now")],
        [InlineKeyboardButton("📊 آخر العمليات", callback_data="admin_referral_history")],
        [InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel")]
    ])

    await query.edit_message_text(text, reply_markup=keyboard)


async def admin_set_referral_percent_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    context.user_data["admin_set_referral_percent"] = True
    current = get_referral_commission_percent() * 100

    await query.message.reply_text(
        f"🎯 نسبة عمولة الإحالة الحالية: {current:.1f}%\n\n"
        f"أرسل النسبة الجديدة (رقم فقط، مثال: 5 لتعني 5%):\n"
        f"أرسل 0 لإيقاف نظام العمولة.",
        reply_markup=cancel_keyboard()
    )


async def admin_set_referral_period_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    context.user_data["admin_set_referral_period"] = True
    current = get_referral_period_days()

    await query.message.reply_text(
        f"⏱️ مدة دورة الأرباح الحالية: {current} يوم/أيام\n\n"
        f"أرسل عدد الأيام الجديد (رقم صحيح، مثال: 10):",
        reply_markup=cancel_keyboard()
    )


async def admin_run_referral_now_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    await query.message.reply_text("⏳ جاري تنفيذ جولة توزيع أرباح الإحالة يدوياً...")

    result = await process_referral_payouts(context.bot)

    await query.message.reply_text(
        "✅ انتهى التنفيذ اليدوي لتوزيع أرباح الإحالة\n\n"
        f"👥 تم فحص: {result['checked']} محيل\n"
        f"✅ تم الصرف لـ: {result['paid_count']} محيل\n"
        f"💵 إجمالي الموزّع: {result['total_paid']:,} {CURRENCY_LABEL}"
    )


async def admin_referral_history_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    payouts = get_recent_referral_payouts(limit=15)

    if not payouts:
        await query.edit_message_text(
            "📊 لا يوجد أي عمليات توزيع أرباح إحالة منفذة بعد.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ رجوع", callback_data="admin_referral_menu")]
            ])
        )
        return

    text = "📊 آخر عمليات توزيع أرباح الإحالة:\n\n"
    for p in payouts:
        text += (
            f"🆔 {p['referrer_id']} | "
            f"💰 {p['total']:,} {CURRENCY_LABEL}\n"
        )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ رجوع", callback_data="admin_referral_menu")]
    ])

    await query.edit_message_text(text, reply_markup=keyboard)


# ============================
# إدارة عجلة الحظ (Spin Wheel Admin)
# ============================

async def _render_admin_spin_menu(query):
    cooldown_hours = get_spin_cooldown_hours()
    prizes = get_all_spin_prizes()

    text = (
        "🎡 إدارة عجلة الحظ\n\n"
        f"⏱️ مدة الانتظار بين اللفات: {cooldown_hours} ساعة\n\n"
        "🎁 الجوائز الحالية:\n\n"
    )

    keyboard_rows = []

    if not prizes:
        text += "لا توجد جوائز حالياً."
    else:
        for i, p in enumerate(prizes, 1):
            status_icon = "✅" if p["active"] else "⛔"
            if p["prize_type"] == "cash":
                extra = f" — {p['amount']:,} {CURRENCY_LABEL}"
            elif p["prize_type"] in SPIN_PERCENT_PRIZE_TYPES:
                extra = f" — {p['amount']}%"
            else:
                extra = ""
            text += f"{i}. {status_icon} {p['label']}{extra} (وزن: {p['weight']})\n"

            toggle_label = f"⛔ تعطيل #{i}" if p["active"] else f"✅ تفعيل #{i}"
            keyboard_rows.append([
                InlineKeyboardButton(toggle_label, callback_data=f"admin_spin_toggle_{p['id']}"),
                InlineKeyboardButton(f"⚖️ وزن #{i}", callback_data=f"admin_spin_editweight_{p['id']}"),
                InlineKeyboardButton(f"🗑️ حذف #{i}", callback_data=f"admin_spin_delete_{p['id']}")
            ])

    keyboard_rows.append([InlineKeyboardButton("➕ إضافة جائزة جديدة", callback_data="admin_spin_add")])
    keyboard_rows.append([InlineKeyboardButton("⏱️ تعديل مدة الانتظار", callback_data="admin_set_spin_cooldown")])
    keyboard_rows.append([InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard_rows))


async def admin_spin_menu_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    await _render_admin_spin_menu(query)


async def admin_spin_toggle_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    prize_id = int(query.data.replace("admin_spin_toggle_", ""))
    prize = get_spin_prize(prize_id)

    if prize:
        set_spin_prize_active(prize_id, not prize["active"])

    await _render_admin_spin_menu(query)


async def admin_spin_delete_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    prize_id = int(query.data.replace("admin_spin_delete_", ""))
    delete_spin_prize(prize_id)

    await _render_admin_spin_menu(query)


async def admin_spin_editweight_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    prize_id = int(query.data.replace("admin_spin_editweight_", ""))
    prize = get_spin_prize(prize_id)

    if not prize:
        await query.answer("❌ الجائزة غير موجودة", show_alert=True)
        return

    context.user_data["admin_spin_edit_weight_id"] = prize_id

    await query.message.reply_text(
        f"⚖️ تعديل وزن الجائزة: {prize['label']}\n\n"
        f"الوزن الحالي: {prize['weight']}\n\n"
        f"أرسل الوزن الجديد (رقم صحيح أكبر من صفر — كلما زاد الرقم زادت فرصة الربح بها):",
        reply_markup=cancel_keyboard()
    )


async def admin_spin_add_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    context.user_data["admin_spin_add_step"] = "label"

    await query.message.reply_text(
        "➕ إضافة جائزة جديدة لعجلة الحظ\n\n"
        "1️⃣ أرسل الآن نص الجائزة كما سيظهر على العجلة (مثال: 💰 50 ل.س):\n"
        "💡 يفضّل نص قصير (كلمة أو كلمتين) حتى يظهر مرتب على العجلة.",
        reply_markup=cancel_keyboard()
    )


async def admin_spin_type_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    prize_type = query.data.replace("admin_spin_type_", "")
    context.user_data["admin_spin_new_type"] = prize_type

    if prize_type in ("cash", "recharge_bonus", "daily_cashback"):
        context.user_data["admin_spin_add_step"] = "amount"
        await query.message.reply_text(
            "3️⃣ أرسل المبلغ (رقم صحيح، مثال: 50):" if prize_type == "cash"
            else "3️⃣ أرسل النسبة المئوية (رقم صحيح من 1 إلى 100، مثال: 10):",
            reply_markup=cancel_keyboard()
        )
    else:
        context.user_data["admin_spin_new_amount"] = 0
        context.user_data["admin_spin_add_step"] = "weight"
        await query.message.reply_text(
            "3️⃣ أرسل وزن الجائزة (رقم صحيح — كلما زاد الرقم زادت فرصة الربح بها، مثال: 10):",
            reply_markup=cancel_keyboard()
        )


async def admin_set_spin_cooldown_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    context.user_data["admin_set_spin_cooldown"] = True
    current = get_spin_cooldown_hours()

    await query.message.reply_text(
        f"⏱️ مدة الانتظار الحالية: {current} ساعة\n\n"
        f"أرسل عدد الساعات الجديد (رقم صحيح، الحد الأدنى {SPIN_MIN_COOLDOWN_HOURS} ساعة، مثال: 24):",
        reply_markup=cancel_keyboard()
    )

# ============================
# معالج الرسائل الرئيسي
# ============================
# ============================
# 📣 رسالة جماعية / ✉️ رسالة فردية (للأدمن)
# ============================
BROADCAST_MAX_LEN = 4000
_broadcast_tasks = set()      # نحتفظ بمرجع للمهام حتى لا يلغيها الـ garbage collector
_broadcast_running = False    # قفل بسيط: بث واحد فقط بنفس الوقت


async def admin_broadcast_callback(update, context):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return

    if _broadcast_running:
        await query.message.reply_text("⏳ في عملية بث شغالة حالياً، استنى تخلص.")
        return

    context.user_data.clear()
    context.user_data["admin_bc_step"] = "await_text"
    await query.message.reply_text(
        "📣 رسالة جماعية لكل اللاعبين\n\n"
        "اكتب الرسالة اللي بدك تبعتها لكل مستخدمي البوت.\n"
        "رح أعرضلك معاينة وتأكد قبل الإرسال.",
        reply_markup=cancel_keyboard()
    )


async def admin_dm_callback(update, context):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return

    context.user_data.clear()
    context.user_data["admin_dm_step"] = "await_id"
    await query.message.reply_text(
        "✉️ رسالة فردية للاعب\n\n"
        "ابعت ID المستخدم (أرقام فقط):",
        reply_markup=cancel_keyboard()
    )


async def admin_broadcast_confirm_callback(update, context):
    global _broadcast_running
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return

    admin_id = query.from_user.id

    if query.data == "admin_bc_cancel":
        context.user_data.pop("admin_bc_step", None)
        context.user_data.pop("admin_bc_text", None)
        await query.edit_message_text("❌ تم إلغاء الرسالة الجماعية.")
        return

    # admin_bc_confirm
    text = context.user_data.get("admin_bc_text")
    if not text or context.user_data.get("admin_bc_step") != "await_confirm":
        await query.edit_message_text("⚠️ انتهت صلاحية هالمعاينة، ابدأ من جديد.")
        return
    if _broadcast_running:
        await query.edit_message_text("⏳ في عملية بث شغالة حالياً.")
        return

    context.user_data.pop("admin_bc_step", None)
    context.user_data.pop("admin_bc_text", None)

    users = get_all_users()
    total = len(users)
    await query.edit_message_text(f"🚀 بدأ الإرسال لـ {total} مستخدم... رح يوصلك تقرير لما يخلص.")

    # نشغّل البث بالخلفية حتى ما يتجمّد البوت لباقي المستخدمين
    _broadcast_running = True
    task = asyncio.create_task(_run_broadcast(context.bot, admin_id, text, [u[0] for u in users]))
    _broadcast_tasks.add(task)
    task.add_done_callback(_broadcast_tasks.discard)


async def _run_broadcast(bot, admin_id, text, user_ids):
    global _broadcast_running
    sent = blocked = failed = 0
    try:
        for uid in user_ids:
            for attempt in range(3):
                try:
                    await bot.send_message(uid, text)
                    sent += 1
                    break
                except RetryAfter as e:
                    await asyncio.sleep(float(e.retry_after) + 1)
                    continue            # نعيد المحاولة بعد الانتظار
                except Forbidden:
                    blocked += 1        # المستخدم حاظر البوت
                    break
                except (BadRequest, TelegramError):
                    failed += 1
                    break
                except Exception:
                    failed += 1
                    break
            else:
                failed += 1             # استنفدنا المحاولات
            await asyncio.sleep(0.05)   # ~20 رسالة/ثانية (تحت حد تيليغرام)
    finally:
        _broadcast_running = False

    try:
        await bot.send_message(
            admin_id,
            "✅ انتهى الإرسال الجماعي\n\n"
            f"👥 الإجمالي: {len(user_ids)}\n"
            f"✅ نجح: {sent}\n"
            f"🚫 حاظرين البوت: {blocked}\n"
            f"❌ فشل: {failed}"
        )
    except Exception:
        pass


# ============================
# 📊 التقرير اليومي — من لوحة الأدمن
# ============================
def _report_days():
    """(آخر يوم تقرير مكتمل، اليوم الحالي) بتوقيت دمشق — نافذة اليوم تبدأ 01:00."""
    local = datetime.now(timezone.utc).astimezone(daily_report.SYRIA_TZ)
    current = (local if local.hour >= daily_report.REPORT_START_HOUR else local - timedelta(days=1)).date()
    return current - timedelta(days=1), current


async def _send_report_to_admin(bot, chat_id, day, partial):
    try:
        cap = datetime.now(timezone.utc).replace(tzinfo=None) if partial else None
        ok = await daily_report.send_report(bot, [chat_id], day, cap)
        if not ok:
            await bot.send_message(chat_id, "❌ فشل إرسال التقرير، شوف سجل البوت.")
    except Exception as exc:
        try:
            await bot.send_message(chat_id, f"❌ تعذّر توليد التقرير: {exc}")
        except Exception:
            pass


async def admin_report_callback(update, context):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return

    action = query.data
    last_done, current = _report_days()

    if action == "admin_report_menu":
        context.user_data.pop("admin_report_step", None)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 تقرير أمس", callback_data="admin_report_yesterday")],
            [InlineKeyboardButton("📡 تقرير اليوم حتى الآن", callback_data="admin_report_live")],
            [InlineKeyboardButton("📅 تقرير يوم محدد", callback_data="admin_report_date")],
            [InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel")],
        ])
        dest = "قناة التقارير" if REPORTS_CHANNEL_ID else "الأدمن بالخاص"
        await query.edit_message_text(
            "📊 التقرير اليومي\n\n"
            f"التقرير التلقائي يوصل كل يوم 01:00 بتوقيت دمشق إلى: {dest}.\n"
            "التقارير اليدوية تنبعت لك هون بالخاص:",
            reply_markup=kb,
        )
        return

    if action == "admin_report_date":
        context.user_data.clear()
        context.user_data["admin_report_step"] = "await_date"
        await query.message.reply_text(
            "📅 ابعت التاريخ بصيغة YYYY-MM-DD\nمثال: 2026-10-06",
            reply_markup=cancel_keyboard(),
        )
        return

    if action in ("admin_report_yesterday", "admin_report_live"):
        partial = action == "admin_report_live"
        day = current if partial else last_done
        await query.edit_message_text("⏳ جاري تجهيز التقرير...")
        context.application.create_task(_send_report_to_admin(context.bot, query.from_user.id, day, partial))


# ============================
# 👥 إدارة اللاعبين (الحصول على المعرف / تصفير الرصيد)
# ============================
def _forward_sender(message):
    """
    يستخرج مرسل رسالة محوّلة. يرجع (user_id, name, username, kind)
    kind: user | bot | hidden (مخفي بإعدادات الخصوصية) | chat (قناة/مجموعة) | none (ليست محوّلة).
    يدعم PTB v21 (forward_origin) و v20 (forward_from).
    """
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        otype = getattr(origin, "type", None)
        if otype == "user":
            u = origin.sender_user
            return u.id, u.full_name, u.username, ("bot" if u.is_bot else "user")
        if otype == "hidden_user":
            return None, getattr(origin, "sender_user_name", "") or "", None, "hidden"
        if otype in ("chat", "channel"):
            chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
            return None, getattr(chat, "title", "") or "", None, "chat"
        return None, "", None, "none"

    u = getattr(message, "forward_from", None)
    if u is not None:
        return u.id, u.full_name, u.username, ("bot" if u.is_bot else "user")
    if getattr(message, "forward_sender_name", None):
        return None, message.forward_sender_name, None, "hidden"
    chat = getattr(message, "forward_from_chat", None)
    if chat is not None:
        return None, getattr(chat, "title", "") or "", None, "chat"
    return None, "", None, "none"


async def _players_reply_forward_info(message, context, uid, name, username):
    """رد «الحصول على معرف اللاعب»: المعرّف + الاسم + حالته بالبوت."""
    info = await asyncio.to_thread(get_user_overview, uid)
    lines = [f"🆔 معرف اللاعب: <code>{uid}</code>"]
    if name:
        lines.append(f"👤 الاسم: {_esc_html(name)}")
    if username:
        lines.append(f"🔗 @{_esc_html(username)}")
    if info is None:
        lines.append("⚠️ غير مسجّل بالبوت")
    else:
        lines.append(f"💰 الرصيد: {info['balance']:,} {CURRENCY_LABEL}")
        if info["ichancy_username"]:
            lines.append(f"🎮 iChancy: {_esc_html(str(info['ichancy_username']))}")
    lines.append("\nابعت رسالة تانية من لاعب آخر، أو اضغط «تراجع» للخروج.")
    await message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=cancel_keyboard())


async def _players_zero_preview(message, context, target_id):
    """معاينة تصفير الرصيد + أزرار التأكيد. يرجع True إذا انتقلنا لخطوة التأكيد."""
    info = await asyncio.to_thread(get_user_overview, target_id)
    if info is None:
        await message.reply_text(
            f"⚠️ ما لقيت لاعب بالـ ID {target_id}. جرب ID تاني أو اضغط تراجع:",
            reply_markup=cancel_keyboard(),
        )
        return False

    if info["balance"] == 0:
        context.user_data.pop("admin_pl_step", None)
        await message.reply_text(
            f"ℹ️ رصيد اللاعب {target_id} هو 0 أصلاً — ما في شي لتصفيره.",
            reply_markup=main_menu(message.chat_id),
        )
        return False

    context.user_data["admin_pl_step"] = "zero_await_confirm"
    context.user_data["admin_pl_zero_target"] = target_id

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تأكيد التصفير", callback_data=f"admin_pl_zok_{target_id}"),
        InlineKeyboardButton("❌ إلغاء", callback_data="admin_pl_zno"),
    ]])
    ich = f"\n🎮 iChancy: {_esc_html(str(info['ichancy_username']))}" if info["ichancy_username"] else ""
    await message.reply_text(
        "⚠️ تصفير رصيد لاعب\n\n"
        f"🆔 المعرف: <code>{target_id}</code>\n"
        f"💰 الرصيد الحالي: {info['balance']:,} {CURRENCY_LABEL}{ich}\n\n"
        "رح يصير رصيده 0 ولا يمكن التراجع. متأكد؟",
        parse_mode="HTML",
        reply_markup=kb,
    )
    return True


async def _players_balance_target_preview(message, context, target_id, mode):
    """بعد اختيار اللاعب لإضافة/خصم رصيد يدوي: يعرض رصيده ويطلب المبلغ."""
    info = await asyncio.to_thread(get_user_overview, target_id)
    if info is None:
        await message.reply_text(
            f"⚠️ ما لقيت لاعب بالـ ID {target_id}. جرب ID تاني أو اضغط تراجع:",
            reply_markup=cancel_keyboard(),
        )
        return False

    context.user_data["admin_pl_step"] = f"{mode}_await_amount"
    context.user_data["admin_pl_bal_target"] = target_id

    ich = f"\n🎮 iChancy: {_esc_html(str(info['ichancy_username']))}" if info["ichancy_username"] else ""
    title = "➕ إضافة رصيد يدوي" if mode == "add" else "➖ خصم رصيد يدوي"
    limit = f"\n(الحد الأقصى للخصم: {info['balance']:,} {CURRENCY_LABEL})" if mode == "sub" else ""
    await message.reply_text(
        f"{title}\n\n"
        f"🆔 المعرف: <code>{target_id}</code>\n"
        f"💰 الرصيد الحالي: {info['balance']:,} {CURRENCY_LABEL}{ich}\n\n"
        f"ابعت المبلغ بالليرة (أرقام فقط):{limit}",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    return True


# ============================
# 📜 سجل شام كاش (التحويلات الواردة + حالة الشحن)
# ============================
_SHAMLOG_MODES = {"all": "الكل", "charged": "🔴 مشحونة", "free": "🟢 غير مشحونة"}


async def admin_shamlog_callback(update, context):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return

    try:
        _, mode, page_s, refresh = query.data.split(":")
        page = max(0, int(page_s))
    except ValueError:
        return
    if mode not in _SHAMLOG_MODES:
        mode = "all"

    back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel")]])

    ok, items, err = await asyncio.to_thread(sham_incoming_log.load_incoming, refresh == "1")
    if not ok:
        await query.edit_message_text(f"❌ تعذّر جلب سجل شام كاش:\n{err}", reply_markup=back_kb)
        return

    charged_n = sum(1 for i in items if i["charged"])
    free_n = len(items) - charged_n
    view = sham_incoming_log.apply_filter(items, mode)

    size = sham_incoming_log.PAGE_SIZE
    pages = max(1, (len(view) + size - 1) // size)
    page = min(page, pages - 1)
    chunk = view[page * size:(page + 1) * size]

    lines = [
        f"📜 <b>سجل شام كاش — الواردة</b> (آخر {sham_incoming_log.PERIOD_DAYS} أيام)",
        f"{sham_incoming_log.CHARGED_MARK} مشحونة: {charged_n}  ·  {sham_incoming_log.FREE_MARK} غير مشحونة: {free_n}  ·  الإجمالي: {len(items)}",
        f"{sham_incoming_log.CHARGED_MARK} = مشحونة لمستخدم   {sham_incoming_log.FREE_MARK} = غير مشحونة",
        "━━━━━━━━━━━━",
    ]
    if not chunk:
        lines.append("لا توجد عمليات بهذا التصنيف.")
    for it in chunk:
        mark = sham_incoming_log.CHARGED_MARK if it["charged"] else sham_incoming_log.FREE_MARK
        cur = CURRENCY_LABEL if it["currency"] == "SYP" else ""
        head = f"{mark} <b>{_esc_html(it['amount_text'])}</b> {cur}".rstrip()
        if it["time"]:
            head += f"  ·  {_esc_html(it['time'][:19])}"
        row2 = []
        if it["name"]:
            row2.append(f"👤 {_esc_html(it['name'])}")
        row2.append(f"<code>{_esc_html(it['txid'])}</code>")
        if it["charged"]:
            row2.append(f"➜ لاعب <code>{it['user_id']}</code>")
        lines.append(head + "\n   " + " · ".join(row2))
    lines.append(f"\nصفحة {page + 1}/{pages}")

    def cb(m, pg, rf=0):
        return f"shamlog:{m}:{pg}:{rf}"

    filter_row = [
        InlineKeyboardButton(("• " if m == mode else "") + label, callback_data=cb(m, 0))
        for m, label in _SHAMLOG_MODES.items()
    ]
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ السابق", callback_data=cb(mode, page - 1)))
    if page < pages - 1:
        nav_row.append(InlineKeyboardButton("التالي ▶️", callback_data=cb(mode, page + 1)))
    kb_rows = [filter_row]
    if nav_row:
        kb_rows.append(nav_row)
    kb_rows.append([
        InlineKeyboardButton("🔄 تحديث", callback_data=cb(mode, page, 1)),
        InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel"),
    ])

    try:
        await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb_rows))
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


# ============================
# ⚡ إدارة أكواد الشام كاش (الكود الذي يشحن عليه اللاعبون + حالته)
# ============================
async def _sham_code_mark():
    """🟢 شغّال / 🔴 غير شغّال / ⚪ غير معروف بعد. يعتمد على آخر فحص (يفحص فوراً إذا قديم، بمهلة 5 ثوانٍ)."""
    st = sham_code_health.get_cached()
    if sham_code_health.is_stale(st):
        try:
            st = await asyncio.wait_for(asyncio.to_thread(sham_code_health.check), timeout=5)
        except Exception:
            st = sham_code_health.get_cached()
    if st is None:
        return "⚪"
    return "🟢" if st["ok"] else "🔴"


def _shcode_menu_text(st) -> str:
    local = datetime.fromtimestamp(st["checked_at"], timezone(timedelta(hours=3)))
    lines = [
        "⚡ <b>إدارة أكواد الشام كاش</b>",
        "",
        "الحالة: " + ("🟢 <b>شغّال</b>" if st["ok"] else "🔴 <b>غير شغّال</b>"),
    ]
    if st["reason"]:
        lines.append(f"السبب: {_esc_html(st['reason'])}")
    lines += [
        "",
        "📋 الكود المعروض للاعبين:",
        f"<code>{_esc_html(st['display_code'])}</code>",
        "",
        "🔗 حساب التحقق (API):",
        f"<code>{_esc_html(st['api_address'] or '—')}</code> " + ("✅ مطابق" if st["match"] else "⚠️ مختلف"),
    ]
    if st["balances"]:
        lines.append("💰 رصيد الحساب: " + " · ".join(st["balances"]))
    lines.append(f"🕒 آخر فحص: {local:%H:%M:%S} (دمشق)")
    return "\n".join(lines)


async def _shcode_show_menu(query, st):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 فحص الآن", callback_data="shcode:check"),
         InlineKeyboardButton("✏️ تغيير الكود", callback_data="shcode:edit")],
        [InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel")],
    ])
    try:
        await query.edit_message_text(_shcode_menu_text(st), parse_mode="HTML", reply_markup=kb)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


async def admin_shcode_callback(update, context):
    query = update.callback_query
    await query.answer()
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    action = query.data.split(":", 1)[1]

    if action in ("menu", "check"):
        context.user_data.pop("admin_shcode_step", None)
        context.user_data.pop("admin_shcode_pending", None)
        # شاشة الإدارة تفحص فعلياً بكل فتح (الزر الرئيسي وحده يعتمد على الكاش لسرعة اللوحة)
        st = await asyncio.to_thread(sham_code_health.check)
        await _shcode_show_menu(query, st)
        return

    if action == "edit":
        context.user_data.clear()
        context.user_data["admin_shcode_step"] = "await_code"
        await query.message.reply_text(
            "✏️ تغيير كود شام كاش\n\n"
            "ابعت الكود الجديد (16–64 حرف/رقم إنجليزي).\n"
            "⚠️ لازم يكون هو نفسه حساب الـ API (SHAM_CASH_ADDRESS)، وإلا البوت ما رح يقدر يتحقق من التحويلات.",
            reply_markup=cancel_keyboard(),
        )
        return

    if action == "no":
        context.user_data.pop("admin_shcode_step", None)
        context.user_data.pop("admin_shcode_pending", None)
        await query.edit_message_text("❌ تم إلغاء تغيير الكود.")
        return

    if action == "ok":
        pending = context.user_data.get("admin_shcode_pending")
        if context.user_data.get("admin_shcode_step") != "await_confirm" or not pending:
            await query.edit_message_text("⌛ انتهت صلاحية هالطلب. ابدأ من جديد.")
            return
        context.user_data.pop("admin_shcode_step", None)
        context.user_data.pop("admin_shcode_pending", None)
        try:
            code = await asyncio.to_thread(sham_code_health.set_code, pending)
        except ValueError as exc:
            await query.edit_message_text(f"❌ {exc}")
            return
        st = await asyncio.to_thread(sham_code_health.check)
        await _shcode_show_menu(query, st)
        await _shtx_notify(
            context.bot,
            f"⚡ تغيير كود شام كاش المعروض للاعبين\n👤 بواسطة: {admin_id}\n🆕 {code}\nالحالة: {'🟢 شغّال' if st['ok'] else '🔴 غير شغّال'}",
            exclude=admin_id,
        )
        return


async def sham_code_monitor_loop(application):
    """فحص دوري كل 10 دقائق؛ ينبّه الأدمنية عند التحوّل إلى 🔴 (بعد فحصين متتاليين) أو العودة إلى 🟢."""
    await asyncio.sleep(45)
    failures = 0
    last_ok = None
    while True:
        try:
            st = await asyncio.to_thread(sham_code_health.check)
            if st["ok"]:
                if last_ok is False:
                    await notify_admins(application.bot, "🟢 كود شام كاش رجع شغّال.")
                failures, last_ok = 0, True
            else:
                failures += 1
                if failures >= 2 and last_ok is not False:
                    last_ok = False
                    await notify_admins(
                        application.bot,
                        f"🔴 كود شام كاش غير شغّال!\nالسبب: {st['reason']}\n"
                        "اللاعبين ممكن يحوّلوا ولا يتحقق البوت من تحويلاتهم — راجع: لوحة الأدمن ← إدارة أكواد الشام كاش.",
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"⚠️ فحص كود شام كاش: {exc}")
        await asyncio.sleep(600)


# ============================
# 🌳 نظام الإحالات حسب الحرق (هرمي / إفرادي) — دورات 10 أيام
# ============================
def _ref2_pending_markup(cid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ اعتماد وتوزيع", callback_data=f"ref2:pay:{cid}"),
        InlineKeyboardButton("❌ إلغاء", callback_data=f"ref2:cancel:{cid}"),
    ]])


async def _ref2_send_report(bot, chat_ids, cid=None, cycle=None, reply_markup=None, note=""):
    """يرسل ملف تقرير دورة (مخزّنة بـ cid، أو معاينة غير مخزّنة بـ cycle). يرجع قائمة الوجهات الناجحة."""
    from io import BytesIO

    if cycle is None:
        cycle = await asyncio.to_thread(referral_burn.load_cycle, cid)
    if cycle is None:
        return []

    names = await daily_report._resolve_names(bot, [b["user_id"] for b in cycle["beneficiaries"]][:25])
    doc = referral_burn.render_report_html(cycle, names, note)
    fname = f"referrals_{(cycle['start'] + timedelta(hours=3)):%Y-%m-%d}.html"
    caption = referral_burn.admin_summary_text(cycle, CURRENCY_LABEL)
    if note:
        caption += f"\n\n{_esc_html(note)}"

    ok = []
    for chat in chat_ids:
        try:
            bio = BytesIO(doc.encode("utf-8"))
            bio.name = fname
            await bot.send_document(chat, document=bio, caption=caption[:1024], parse_mode="HTML", reply_markup=reply_markup)
            ok.append(chat)
        except Exception as exc:
            print(f"⚠️ إرسال تقرير الإحالات إلى {chat}: {exc}")
    return ok


async def _ref2_notify_beneficiaries(bot, cid):
    """يبلّغ مستفيدي دورة مصروفة (مرة واحدة لكل مستفيد): الأرباح أو سبب عدم الاستحقاق."""
    cycle = await asyncio.to_thread(referral_burn.load_cycle, cid)
    if cycle is None or cycle["status"] != "paid":
        return
    by_id = {b["user_id"]: b for b in cycle["beneficiaries"]}
    for uid in await asyncio.to_thread(referral_burn.unnotified, cid):
        try:
            await bot.send_message(uid, referral_burn.beneficiary_text(by_id[uid], cycle, CURRENCY_LABEL))
        except Exception:
            pass
        await asyncio.to_thread(referral_burn.mark_notified, cid, uid)


async def _ref2_handle_closed_cycle(bot, cid):
    cycle = await asyncio.to_thread(referral_burn.load_cycle, cid)
    if cycle is None:
        return

    if referral_burn.get_auto_credit() or cycle["total_amount"] == 0:
        await asyncio.to_thread(referral_burn.credit_cycle, cid)
        await _ref2_notify_beneficiaries(bot, cid)
        note = "تم الصرف تلقائياً" if cycle["total_amount"] else "لا مستحقات في هذه الدورة"
        ok = await _ref2_send_report(bot, _report_primary_targets(), cid=cid, note=note) if _report_primary_targets() else []
        if not ok:
            await _ref2_send_report(bot, _report_fallback_targets(), cid=cid, note=note)
        return

    # بانتظار اعتماد الأدمن: أرشيف للقناة (إن وُجدت) + نسخة بأزرار الاعتماد لكل أدمن
    if _report_primary_targets():
        await _ref2_send_report(bot, _report_primary_targets(), cid=cid, note="بانتظار اعتماد الأدمن")
    await _ref2_send_report(
        bot, _report_fallback_targets(), cid=cid, reply_markup=_ref2_pending_markup(cid), note="بانتظار اعتمادك"
    )


async def referral_burn_loop(application):
    """كل 5 دقائق: يغلق الدورات المستحقة ويتعامل مع تقاريرها، ويكمل إبلاغ دورات مصروفة لم يكتمل إبلاغها."""
    await asyncio.sleep(60)
    while True:
        try:
            for cid in await asyncio.to_thread(referral_burn.close_due_cycles):
                await _ref2_handle_closed_cycle(application.bot, cid)
            for cid in await asyncio.to_thread(referral_burn.paid_cycles_with_unnotified):
                await _ref2_notify_beneficiaries(application.bot, cid)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"⚠️ حلقة نظام الإحالات حسب الحرق: {exc}")
        await asyncio.sleep(300)


async def _ref2_show_menu(query):
    enabled = referral_burn.is_enabled()
    auto = referral_burn.get_auto_credit()
    counts = await asyncio.to_thread(referral_burn.plan_counts)

    lines = [
        "🌳 نظام الإحالات (حسب الحرق)",
        "",
        "الحالة: " + ("🟢 مفعّل" if enabled else "🔴 متوقف (عمولة الشحن القديمة شغّالة)"),
        "كل لاعب يختار نظامه (هرمي / فردي) أول ما يستخدم البوت — الاختيار نهائي.",
        f"👥 اللاعبون: 🌳 هرمي {counts['hierarchical']} · 👤 فردي {counts['individual']} · لم يختاروا بعد {counts['none']}",
    ]

    if enabled:
        start = referral_burn.get_cycle_start()
        if start:
            end = start + timedelta(days=referral_burn.CYCLE_DAYS)
            left = max(0.0, (end - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() / 86400)
            lines.append(
                f"⏱️ الدورة الحالية: {(start + timedelta(hours=3)):%m-%d %H:%M} → {(end + timedelta(hours=3)):%m-%d %H:%M} "
                f"(دمشق) — باقي {left:.1f} يوم"
            )
        lines += [
            f"💳 الصرف: {'تلقائي' if auto else 'بعد اعتماد الأدمن'}",
            f"📋 تقارير بانتظار الاعتماد: {await asyncio.to_thread(referral_burn.pending_count)}",
        ]

    lines += [
        "",
        f"الشروط: {referral_burn.MIN_ACTIVE_REFERRALS} إحالات نشطة على الأقل + حرق النطاق أعلى من سحبه.",
        "الحرق = صافي (شحن آيشانسي − سحب منه) لكل لاعب خلال الدورة.",
    ]

    rows = []
    if enabled:
        rows.append([InlineKeyboardButton(f"💳 الصرف: {'تلقائي ✅' if auto else 'يدوي (باعتماد)'} — تبديل", callback_data="ref2:auto")])
        rows.append([
            InlineKeyboardButton("👁️ معاينة الدورة الحالية", callback_data="ref2:preview"),
            InlineKeyboardButton("📋 التقارير", callback_data="ref2:list"),
        ])
        rows.append([InlineKeyboardButton("🔴 إيقاف النظام (طوارئ)", callback_data="ref2:toggle")])
    else:
        rows.append([InlineKeyboardButton("🟢 تشغيل النظام", callback_data="ref2:toggle")])
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="admin_referral_menu")])

    try:
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


async def admin_ref2_callback(update, context):
    query = update.callback_query
    await query.answer()
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else "menu"
    arg = parts[2] if len(parts) > 2 else None

    if action == "menu":
        await _ref2_show_menu(query)
        return

    if action == "toggle":
        if referral_burn.is_enabled():
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ تأكيد الإيقاف", callback_data="ref2:toggleok"),
                InlineKeyboardButton("❌ إلغاء", callback_data="ref2:menu"),
            ]])
            await query.edit_message_text(
                "⚠️ إيقاف نظام الإحالات حسب الحرق؟\n\n"
                "• ترجع عمولة الشحن القديمة.\n"
                "• لا تُغلق دورات جديدة، والدورة الجارية غير المغلقة ما رح تنحسب.\n"
                "• عند إعادة التشغيل تبدأ دورة جديدة من لحظتها.",
                reply_markup=kb,
            )
        else:
            await asyncio.to_thread(referral_burn.set_enabled, True)
            await _shtx_notify(context.bot, f"🌳 تم تشغيل نظام الإحالات حسب الحرق\n👤 بواسطة: {admin_id}", exclude=admin_id)
            await _ref2_show_menu(query)
        return

    if action == "toggleok":
        await asyncio.to_thread(referral_burn.set_enabled, False)
        await _shtx_notify(context.bot, f"🌳 تم إيقاف نظام الإحالات حسب الحرق\n👤 بواسطة: {admin_id}", exclude=admin_id)
        await _ref2_show_menu(query)
        return

    if action == "auto":
        referral_burn.set_auto_credit(not referral_burn.get_auto_credit())
        await _ref2_show_menu(query)
        return

    if action == "preview":
        start = referral_burn.get_cycle_start()
        if not referral_burn.is_enabled() or start is None:
            await query.message.reply_text("النظام غير مفعّل أو لم تبدأ دورته بعد.")
            return
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cycle = await asyncio.to_thread(referral_burn.compute_cycle, start, now)
        await _ref2_send_report(context.bot, [admin_id], cycle=cycle, note="معاينة الدورة الحالية (غير نهائية)")
        return

    if action == "list":
        cycles = await asyncio.to_thread(referral_burn.list_cycles, 8)
        icons = {"pending": "⏳", "paid": "✅", "cancelled": "🚫"}
        rows = [[InlineKeyboardButton(
            f"{icons.get(c['status'], '•')} {(referral_burn._parse_ts(c['start_at']) + timedelta(hours=3)):%m-%d} → "
            f"{(referral_burn._parse_ts(c['end_at']) + timedelta(hours=3)):%m-%d} · {c['total_amount']:,}",
            callback_data=f"ref2:view:{c['id']}",
        )] for c in cycles]
        rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="ref2:menu")])
        await query.edit_message_text(
            "📋 تقارير الإحالات (الأحدث أولاً)\n⏳ بانتظار الاعتماد · ✅ مصروفة · 🚫 ملغاة" if cycles
            else "لا توجد تقارير بعد — أول دورة تُغلق بعد انتهاء مدتها.",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if action == "view" and arg and arg.isdigit():
        cycle = await asyncio.to_thread(referral_burn.load_cycle, int(arg))
        if cycle is None:
            await query.message.reply_text("التقرير غير موجود.")
            return
        markup = _ref2_pending_markup(cycle["id"]) if cycle["status"] == "pending" else None
        await _ref2_send_report(context.bot, [admin_id], cycle=cycle, reply_markup=markup)
        return

    if action in ("pay", "cancel") and arg and arg.isdigit():
        cid = int(arg)
        if action == "pay":
            paid = await asyncio.to_thread(referral_burn.credit_cycle, cid)
            if paid is None:
                await query.message.reply_text("ℹ️ هذا التقرير ما عاد بانتظار الاعتماد (عولج مسبقاً).")
            else:
                total = sum(a for _, a in paid)
                await query.message.reply_text(f"✅ تم الصرف: {len(paid)} مستفيد — {total:,} {CURRENCY_LABEL}")
                await _ref2_notify_beneficiaries(context.bot, cid)
                await _shtx_notify(
                    context.bot,
                    f"🌳 اعتماد توزيع الإحالات (دورة #{cid})\n👤 بواسطة: {admin_id}\n💰 {total:,} {CURRENCY_LABEL} لـ {len(paid)} مستفيد",
                    exclude=admin_id,
                )
        else:
            done = await asyncio.to_thread(referral_burn.cancel_cycle, cid)
            await query.message.reply_text("🚫 تم إلغاء التقرير (لن يُصرف)." if done else "ℹ️ هذا التقرير ما عاد بانتظار الاعتماد.")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return


async def _players_rp_preview(message, context, target_id):
    """عرض نظام إحالات لاعب (الاختيار نهائي للاعب؛ هذا تصحيح من الأدمن فقط) مع أزرار التعديل."""
    info = await asyncio.to_thread(get_user_overview, target_id)
    if info is None:
        await message.reply_text(
            f"⚠️ ما لقيت لاعب بالـ ID {target_id}. جرب ID تاني أو اضغط تراجع:", reply_markup=cancel_keyboard()
        )
        return False

    context.user_data.pop("admin_pl_step", None)
    current = get_user_referral_plan(target_id)
    shown = referral_burn.PLAN_LABELS.get(current, "لم يختر بعد")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌳 هرمي", callback_data=f"admin_pl_rps_h_{target_id}"),
         InlineKeyboardButton("👤 فردي", callback_data=f"admin_pl_rps_i_{target_id}")],
        [InlineKeyboardButton("🧹 مسح الاختيار (يُسأل من جديد)", callback_data=f"admin_pl_rps_c_{target_id}")],
    ])
    await message.reply_text(
        f"🌳 نظام إحالات اللاعب <code>{target_id}</code>\n"
        f"النظام الحالي: {shown}\n\n"
        "⚠️ تغيير النظام يسري على احتساب الدورة الجارية كلها. يُسجَّل بسجل التدقيق.",
        parse_mode="HTML",
        reply_markup=kb,
        )
    return True


# ============================
# 💸 إدارة نسبة السحب (عامة + لكل لاعب)
# ============================
def _parse_fee_percent(text):
    """'10' / '7.5' / '7,5' / '٧٫٥' / '10%' → كسر (0.10) أو None إن غير صالح (0–50%)."""
    t = str(text or "").strip().replace("%", "").replace(",", ".").replace("٫", ".")
    t = t.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    try:
        value = float(t)
    except ValueError:
        return None
    if value != value or value < 0 or value > 50:
        return None
    return round(value / 100, 4)


async def _wdfee_show_menu(query):
    glob = await asyncio.to_thread(get_global_withdraw_fee_percent)
    overrides = await asyncio.to_thread(count_withdraw_fee_overrides)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تغيير النسبة العامة", callback_data="wdfee:global")],
        [InlineKeyboardButton("👤 نسبة سحب لاعب معين", callback_data="wdfee:user")],
        [InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel")],
    ])
    text = (
        "💸 إدارة نسبة السحب\n\n"
        f"📉 النسبة العامة (حسم السحب): {_fmt_pct(glob)}\n"
        f"👤 لاعبون لهم نسبة خاصة: {overrides}\n\n"
        "• النسبة الخاصة تتغلب على العامة لذاك اللاعب.\n"
        "• التغيير يسري على طلبات السحب الجديدة فقط؛ الطلبات المعلّقة تحتفظ بنسبتها وقت الطلب."
    )
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


async def _wdfee_user_preview(message, context, target_id):
    """بعد تحديد اللاعب: يعرض نسبته الحالية وأزرار التعديل."""
    if not await asyncio.to_thread(_user_exists_fee, target_id):
        await message.reply_text(
            f"⚠️ ما لقيت لاعب بالـ ID {target_id}. جرب ID تاني أو اضغط تراجع:", reply_markup=cancel_keyboard()
        )
        return False

    context.user_data["admin_wdfee_step"] = "user_pick"
    context.user_data["admin_wdfee_target"] = target_id
    override = await asyncio.to_thread(get_user_withdraw_fee_override, target_id)
    glob = await asyncio.to_thread(get_global_withdraw_fee_percent)
    current = f"{_fmt_pct(override)} (خاصة)" if override is not None else f"{_fmt_pct(glob)} (تتبع العامة)"
    rows = [[InlineKeyboardButton("✏️ تعيين نسبة خاصة", callback_data="wdfee:uset")]]
    if override is not None:
        rows.append([InlineKeyboardButton("🧹 إزالة النسبة الخاصة (تتبع العامة)", callback_data="wdfee:uclear")])
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data="wdfee:menu")])
    await message.reply_text(
        f"👤 نسبة سحب اللاعب {target_id}\n\nالنسبة الحالية: {current}",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return True


async def admin_wdfee_callback(update, context):
    query = update.callback_query
    await query.answer()
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    action = query.data.split(":", 1)[1]

    if action == "menu":
        for k in ("admin_wdfee_step", "admin_wdfee_value", "admin_wdfee_target"):
            context.user_data.pop(k, None)
        await _wdfee_show_menu(query)
        return

    if action == "global":
        context.user_data.clear()
        context.user_data["admin_wdfee_step"] = "await_global"
        await query.message.reply_text(
            "✏️ النسبة العامة لحسم السحب\n\nابعت النسبة المئوية (من 0 إلى 50). مثال: 10 أو 7.5\n"
            "0 = بدون رسوم.",
            reply_markup=cancel_keyboard(),
        )
        return

    if action == "user":
        context.user_data.clear()
        context.user_data["admin_wdfee_step"] = "await_user"
        await query.message.reply_text(
            "👤 نسبة سحب لاعب معين\n\nابعت ID اللاعب (أرقام فقط) أو حوّلّي رسالة منه:",
            reply_markup=cancel_keyboard(),
        )
        return

    if action == "uset":
        if not context.user_data.get("admin_wdfee_target"):
            await query.edit_message_text("⌛ انتهت صلاحية هالطلب. ابدأ من جديد.")
            return
        context.user_data["admin_wdfee_step"] = "await_user_pct"
        await query.message.reply_text(
            "✏️ ابعت النسبة الخاصة لهذا اللاعب (من 0 إلى 50). مثال: 5 أو 7.5\n0 = بدون رسوم.",
            reply_markup=cancel_keyboard(),
        )
        return

    if action == "uclear":
        target = context.user_data.get("admin_wdfee_target")
        if not target:
            await query.edit_message_text("⌛ انتهت صلاحية هالطلب. ابدأ من جديد.")
            return
        result = await asyncio.to_thread(set_user_withdraw_fee_percent, target, None, admin_id)
        for k in ("admin_wdfee_step", "admin_wdfee_value", "admin_wdfee_target"):
            context.user_data.pop(k, None)
        if not result["ok"]:
            await query.edit_message_text("⚠️ تعذّر التحديث (اللاعب غير موجود؟).")
            return
        glob = await asyncio.to_thread(get_global_withdraw_fee_percent)
        await query.edit_message_text(f"✅ أُزيلت النسبة الخاصة للاعب {target} — صار يتبع العامة ({_fmt_pct(glob)}).")
        await _shtx_notify(context.bot, f"💸 إزالة نسبة سحب خاصة\n👤 بواسطة: {admin_id}\n🆔 اللاعب: {target}", exclude=admin_id)
        return

    if action in ("gok", "uok"):
        step = context.user_data.get("admin_wdfee_step")
        value = context.user_data.get("admin_wdfee_value")
        target = context.user_data.get("admin_wdfee_target")
        if (value is None or step != "await_confirm_" + ("global" if action == "gok" else "user")
                or (action == "uok" and not target)):
            await query.edit_message_text("⌛ انتهت صلاحية هالطلب. ابدأ من جديد.")
            return
        for k in ("admin_wdfee_step", "admin_wdfee_value", "admin_wdfee_target"):
            context.user_data.pop(k, None)

        if action == "gok":
            result = await asyncio.to_thread(set_global_withdraw_fee_percent, value, admin_id)
            if not result["ok"]:
                await query.edit_message_text("❌ نسبة غير صالحة.")
                return
            await query.edit_message_text(f"✅ صارت النسبة العامة لحسم السحب {_fmt_pct(result['new'])} (كانت {_fmt_pct(result['old'])}).")
            await _shtx_notify(
                context.bot,
                f"💸 تغيير نسبة السحب العامة: {_fmt_pct(result['old'])} ← {_fmt_pct(result['new'])}\n👤 بواسطة: {admin_id}",
                exclude=admin_id,
            )
        else:
            result = await asyncio.to_thread(set_user_withdraw_fee_percent, target, value, admin_id)
            if not result["ok"]:
                await query.edit_message_text("⚠️ تعذّر التحديث (نسبة غير صالحة أو لاعب غير موجود).")
                return
            old = _fmt_pct(result["old"]) if result["old"] is not None else "تتبع العامة"
            await query.edit_message_text(f"✅ نسبة سحب اللاعب {target}: {_fmt_pct(result['new'])} (كانت {old}).")
            await _shtx_notify(
                context.bot,
                f"💸 نسبة سحب خاصة للاعب {target}: {old} ← {_fmt_pct(result['new'])}\n👤 بواسطة: {admin_id}",
                exclude=admin_id,
            )
        return


async def admin_players_callback(update, context):
    query = update.callback_query
    await query.answer()
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    action = query.data

    if action == "admin_pl_menu":
        context.user_data.pop("admin_pl_step", None)
        context.user_data.pop("admin_pl_zero_target", None)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🆔 الحصول على معرف اللاعب", callback_data="admin_pl_getid")],
            [InlineKeyboardButton("➕ إضافة رصيد يدوي", callback_data="admin_pl_add"),
             InlineKeyboardButton("➖ خصم رصيد يدوي", callback_data="admin_pl_sub")],
            [InlineKeyboardButton("🧹 تصفير رصيد اللاعب", callback_data="admin_pl_zero")],
            [InlineKeyboardButton("🌳 نظام إحالات لاعب", callback_data="admin_pl_rp")],
            [InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel")],
        ])
        await query.edit_message_text("👥 إدارة اللاعبين\n\nاختر العملية:", reply_markup=kb)
        return

    if action == "admin_pl_getid":
        context.user_data.clear()
        context.user_data["admin_pl_step"] = "await_forward"
        await query.message.reply_text(
            "🆔 الحصول على معرف اللاعب\n\n"
            "حوّلّي (Forward) أي رسالة من اللاعب وبعطيك الـ ID تبعه.\n"
            "ملاحظة: إذا اللاعب مخفي حسابه بإعدادات الخصوصية ما بيظهر الـ ID.",
            reply_markup=cancel_keyboard(),
        )
        return

    if action == "admin_pl_zero":
        context.user_data.clear()
        context.user_data["admin_pl_step"] = "zero_await_target"
        await query.message.reply_text(
            "🧹 تصفير رصيد اللاعب\n\n"
            "ابعت ID اللاعب (أرقام فقط) أو حوّلّي رسالة منه:",
            reply_markup=cancel_keyboard(),
        )
        return

    if action == "admin_pl_rp":
        context.user_data.clear()
        context.user_data["admin_pl_step"] = "rp_await_target"
        await query.message.reply_text(
            "🌳 نظام إحالات لاعب\n\nابعت ID اللاعب (أرقام فقط) أو حوّلّي رسالة منه:",
            reply_markup=cancel_keyboard(),
        )
        return

    if action.startswith("admin_pl_rps_"):
        try:
            _, _, _, code, uid_s = action.split("_", 4)
            target = int(uid_s)
        except ValueError:
            return
        plan = {"h": "hierarchical", "i": "individual", "c": None}.get(code, "bad")
        if plan == "bad":
            return
        result = await asyncio.to_thread(admin_set_user_referral_plan, target, plan, admin_id)
        if result["status"] == "not_found":
            await query.edit_message_text(f"⚠️ ما لقيت اللاعب {target}.")
            return
        old = referral_burn.PLAN_LABELS.get(result["old"], "لم يختر")
        new = referral_burn.PLAN_LABELS.get(plan, "مسح (يُسأل من جديد)")
        await query.edit_message_text(f"✅ تم تحديث نظام إحالات اللاعب {target}\nمن: {old}\nإلى: {new}")
        await _shtx_notify(
            context.bot,
            f"🌳 تعديل نظام إحالات لاعب\n👤 بواسطة: {admin_id}\n🆔 اللاعب: {target}\nمن: {old} ← إلى: {new}",
            exclude=admin_id,
        )
        return

    if action in ("admin_pl_add", "admin_pl_sub"):
        mode = "add" if action == "admin_pl_add" else "sub"
        context.user_data.clear()
        context.user_data["admin_pl_step"] = f"{mode}_await_target"
        title = "➕ إضافة رصيد يدوي" if mode == "add" else "➖ خصم رصيد يدوي"
        await query.message.reply_text(
            f"{title}\n\nابعت ID اللاعب (أرقام فقط) أو حوّلّي رسالة منه:",
            reply_markup=cancel_keyboard(),
        )
        return

    if action == "admin_pl_bno":
        for k in ("admin_pl_step", "admin_pl_bal_target", "admin_pl_bal_amount"):
            context.user_data.pop(k, None)
        await query.edit_message_text("❌ تم الإلغاء.")
        return

    if action in ("admin_pl_bok_add", "admin_pl_bok_sub"):
        mode = "add" if action.endswith("_add") else "sub"
        target = context.user_data.get("admin_pl_bal_target")
        amount = context.user_data.get("admin_pl_bal_amount")

        if context.user_data.get("admin_pl_step") != f"{mode}_await_confirm" or not target or not amount:
            await query.edit_message_text("⌛ انتهت صلاحية هالطلب. ابدأ من جديد.")
            return

        for k in ("admin_pl_step", "admin_pl_bal_target", "admin_pl_bal_amount"):
            context.user_data.pop(k, None)

        result = await asyncio.to_thread(admin_adjust_balance, target, admin_id, amount, mode)

        if result["status"] == "not_found":
            await query.edit_message_text(f"⚠️ ما لقيت اللاعب {target}.")
            return
        if result["status"] == "insufficient":
            await query.edit_message_text(
                f"❌ رصيد اللاعب صار {result['balance']:,} {CURRENCY_LABEL} — ما بيكفي للخصم. لم يتم أي تغيير."
            )
            return
        if result["status"] != "ok":
            await query.edit_message_text("❌ تعذّر تنفيذ العملية.")
            return

        old, new = result["old"], result["new"]
        verb = "إضافة" if mode == "add" else "خصم"
        await query.edit_message_text(
            f"✅ تم {verb} {amount:,} {CURRENCY_LABEL} للاعب {target}\n"
            f"💳 الرصيد: {old:,} ← {new:,} {CURRENCY_LABEL}"
        )

        label = await _tg_user_label(context.bot, target)
        log_text = _manual_balance_log_text(mode, admin_id, label, target, amount, old, new)
        await send_deposit_log(context.bot, log_text)
        return

    if action == "admin_pl_zno":
        context.user_data.pop("admin_pl_step", None)
        context.user_data.pop("admin_pl_zero_target", None)
        await query.edit_message_text("❌ تم إلغاء التصفير.")
        return

    if action.startswith("admin_pl_zok_"):
        try:
            target = int(action.rsplit("_", 1)[1])
        except ValueError:
            return

        if (context.user_data.get("admin_pl_step") != "zero_await_confirm"
                or context.user_data.get("admin_pl_zero_target") != target):
            await query.edit_message_text("⌛ انتهت صلاحية هالطلب. ابدأ من جديد.")
            return

        context.user_data.pop("admin_pl_step", None)
        context.user_data.pop("admin_pl_zero_target", None)

        result = await asyncio.to_thread(zero_user_balance, target, admin_id)
        if result["status"] != "zeroed":
            await query.edit_message_text(f"⚠️ ما لقيت اللاعب {target}.")
            return

        old = result["old_balance"]
        await query.edit_message_text(
            f"✅ تم تصفير رصيد اللاعب {target}\n💰 الرصيد السابق: {old:,} {CURRENCY_LABEL}"
        )
        label = await _tg_user_label(context.bot, target)
        await send_deposit_log(
            context.bot,
            _manual_balance_log_text("zero", admin_id, label, target, old, old, 0)
        )
        return


async def admin_players_forward_handler(update, context):
    """
    معالج الرسائل المحوّلة للأدمن (group=-1 قبل باقي المعالجات).
    لا يتدخل إلا إذا كان الأدمن داخل خطوة من «إدارة اللاعبين» والرسالة فعلاً محوّلة.
    """
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or user.id not in ADMIN_IDS:
        return

    step = context.user_data.get("admin_pl_step")
    wdfee_step = context.user_data.get("admin_wdfee_step")
    if step not in ("await_forward", "zero_await_target", "rp_await_target",
                    "add_await_target", "sub_await_target") and wdfee_step != "await_user":
        return

    uid, name, username, kind = _forward_sender(message)
    if kind == "none":
        return  # رسالة عادية → تكمل للمعالجات الطبيعية (مثل إدخال ID نصي)

    if kind == "hidden":
        await message.reply_text(
            f"⚠️ اللاعب «{name}» مخفي حسابه عند التحويل (إعدادات الخصوصية) — تيليغرام ما بيعطيني الـ ID.\n"
            "جرب تلاقيه من قائمة «مستخدمو البوت» أو اطلب منه يبعتلك الـ ID.",
            reply_markup=cancel_keyboard(),
        )
    elif kind == "chat":
        await message.reply_text("⚠️ هاي رسالة محوّلة من قناة/مجموعة مو من لاعب. حوّلّي رسالة من اللاعب نفسه.", reply_markup=cancel_keyboard())
    elif kind == "bot":
        await message.reply_text("⚠️ هذا بوت مو لاعب. حوّلّي رسالة من لاعب.", reply_markup=cancel_keyboard())
    elif step == "await_forward":
        await _players_reply_forward_info(message, context, uid, name, username)
    elif wdfee_step == "await_user":
        await _wdfee_user_preview(message, context, uid)
    elif step == "zero_await_target":
        await _players_zero_preview(message, context, uid)
    elif step in ("add_await_target", "sub_await_target"):
        await _players_balance_target_preview(message, context, uid, step.split("_")[0])
    else:  # rp_await_target
        await _players_rp_preview(message, context, uid)

    raise ApplicationHandlerStop


async def handle_admin_message_flows(update, context, text):
    """
    يعالج خطوات الرسالة الجماعية والفردية.
    يرجع True إذا استهلك الرسالة.
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return False

    # ---------- 📞 إدارة أكواد Syriatel ----------
    if context.user_data.get("admin_sy_step"):
        return await _handle_syriatel_text(update, context, text)

    # ---------- إدارة نسبة السحب ----------
    wf_step = context.user_data.get("admin_wdfee_step")
    if wf_step in ("await_global", "await_user_pct"):
        value = _parse_fee_percent(text)
        if value is None:
            await update.message.reply_text("❌ ابعت رقم من 0 إلى 50 (مثال: 10 أو 7.5):", reply_markup=cancel_keyboard())
            return True
        context.user_data["admin_wdfee_value"] = value
        if wf_step == "await_global":
            old = get_global_withdraw_fee_percent()
            context.user_data["admin_wdfee_step"] = "await_confirm_global"
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ تأكيد", callback_data="wdfee:gok"),
                InlineKeyboardButton("❌ إلغاء", callback_data="wdfee:menu"),
            ]])
            await update.message.reply_text(
                f"📉 النسبة العامة لحسم السحب: {_fmt_pct(old)} ← {_fmt_pct(value)}\n\n"
                "تسري على طلبات السحب الجديدة لكل اللاعبين (عدا من لهم نسبة خاصة).",
                reply_markup=kb,
            )
        else:
            target = context.user_data.get("admin_wdfee_target")
            context.user_data["admin_wdfee_step"] = "await_confirm_user"
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ تأكيد", callback_data="wdfee:uok"),
                InlineKeyboardButton("❌ إلغاء", callback_data="wdfee:menu"),
            ]])
            await update.message.reply_text(
                f"👤 اللاعب {target}: نسبة السحب الخاصة ← {_fmt_pct(value)}", reply_markup=kb
            )
        return True
    if wf_step == "await_user":
        target_id = parse_admin_int(text)
        if not target_id:
            await update.message.reply_text("❌ ابعت ID صحيح (أرقام فقط) أو حوّلّي رسالة من اللاعب:", reply_markup=cancel_keyboard())
            return True
        await _wdfee_user_preview(update.message, context, target_id)
        return True
    if wf_step in ("user_pick", "await_confirm_global", "await_confirm_user"):
        await update.message.reply_text("اضغط الأزرار بالرسالة اللي فوق، أو «تراجع».")
        return True

    # ---------- إدارة أكواد الشام كاش ----------
    shcode_step = context.user_data.get("admin_shcode_step")
    if shcode_step == "await_code":
        try:
            code = sham_code_health.validate_code(text)
        except ValueError as exc:
            await update.message.reply_text(f"❌ {exc}\nجرب مرة تانية أو اضغط تراجع:", reply_markup=cancel_keyboard())
            return True

        context.user_data["admin_shcode_pending"] = code
        context.user_data["admin_shcode_step"] = "await_confirm"
        same = code.lower() == (sham_code_health.SHAM_CASH_ADDRESS or "").lower()
        warn = "" if same else (
            "\n\n⚠️ هذا الكود مختلف عن حساب الـ API (SHAM_CASH_ADDRESS): الحالة رح تظهر 🔴 لحد ما تغيّر "
            "SHAM_CASH_ADDRESS بمتغيرات البيئة وتعيد تشغيل البوت."
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأكيد", callback_data="shcode:ok"),
            InlineKeyboardButton("❌ إلغاء", callback_data="shcode:no"),
        ]])
        await update.message.reply_text(
            f"🆕 الكود الجديد:\n<code>{_esc_html(code)}</code>\n\n"
            f"الحالي: <code>{_esc_html(sham_code_health.get_code())}</code>{warn}\n\n"
            "🖼️ تذكير: صورة QR (shamcash_qr.png) لازم تتبدّل يدوياً لتطابق الكود الجديد.",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return True
    if shcode_step == "await_confirm":
        await update.message.reply_text("اضغط «✅ تأكيد» أو «❌ إلغاء» بالرسالة اللي فوق، أو «تراجع».")
        return True

    # ---------- إدارة اللاعبين ----------
    pl_step = context.user_data.get("admin_pl_step")
    if pl_step == "await_forward":
        await update.message.reply_text(
            "↪️ حوّلّي (Forward) رسالة من اللاعب — الـ ID ما بينكتب يدوياً هون.",
            reply_markup=cancel_keyboard(),
        )
        return True
    if pl_step == "zero_await_target":
        target_id = parse_admin_int(text)
        if not target_id:
            await update.message.reply_text("❌ ابعت ID صحيح (أرقام فقط) أو حوّلّي رسالة من اللاعب:", reply_markup=cancel_keyboard())
            return True
        await _players_zero_preview(update.message, context, target_id)
        return True
    if pl_step == "rp_await_target":
        target_id = parse_admin_int(text)
        if not target_id:
            await update.message.reply_text("❌ ابعت ID صحيح (أرقام فقط) أو حوّلّي رسالة من اللاعب:", reply_markup=cancel_keyboard())
            return True
        await _players_rp_preview(update.message, context, target_id)
        return True
    if pl_step == "zero_await_confirm":
        await update.message.reply_text("اضغط «✅ تأكيد التصفير» أو «❌ إلغاء» بالرسالة اللي فوق، أو «تراجع».")
        return True
    if pl_step in ("add_await_target", "sub_await_target"):
        target_id = parse_admin_int(text)
        if not target_id:
            await update.message.reply_text("❌ ابعت ID صحيح (أرقام فقط) أو حوّلّي رسالة من اللاعب:", reply_markup=cancel_keyboard())
            return True
        await _players_balance_target_preview(update.message, context, target_id, pl_step.split("_")[0])
        return True
    if pl_step in ("add_await_amount", "sub_await_amount"):
        mode = pl_step.split("_")[0]
        amount = parse_admin_int(text)
        target = context.user_data.get("admin_pl_bal_target")
        if not amount or amount <= 0:
            await update.message.reply_text("❌ ابعت مبلغاً صحيحاً أكبر من صفر (أرقام فقط):", reply_markup=cancel_keyboard())
            return True
        info = await asyncio.to_thread(get_user_overview, target)
        if info is None:
            context.user_data.pop("admin_pl_step", None)
            await update.message.reply_text(f"⚠️ ما لقيت اللاعب {target}.", reply_markup=main_menu(user_id))
            return True
        if mode == "sub" and amount > info["balance"]:
            await update.message.reply_text(
                f"❌ المبلغ أكبر من رصيد اللاعب ({info['balance']:,} {CURRENCY_LABEL}). ابعت مبلغاً أقل أو يساويه:",
                reply_markup=cancel_keyboard(),
            )
            return True

        context.user_data["admin_pl_bal_amount"] = amount
        context.user_data["admin_pl_step"] = f"{mode}_await_confirm"
        new_balance = info["balance"] + amount if mode == "add" else info["balance"] - amount
        verb = "إضافة" if mode == "add" else "خصم"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأكيد", callback_data=f"admin_pl_bok_{mode}"),
            InlineKeyboardButton("❌ إلغاء", callback_data="admin_pl_bno"),
        ]])
        await update.message.reply_text(
            f"⚠️ تأكيد {verb} رصيد يدوي\n\n"
            f"🆔 اللاعب: {target}\n"
            f"💰 المبلغ: {amount:,} {CURRENCY_LABEL}\n"
            f"💳 الرصيد: {info['balance']:,} ← {new_balance:,} {CURRENCY_LABEL}",
            reply_markup=kb,
        )
        return True
    if pl_step in ("add_await_confirm", "sub_await_confirm"):
        await update.message.reply_text("اضغط «✅ تأكيد» أو «❌ إلغاء» بالرسالة اللي فوق، أو «تراجع».")
        return True

    # ---------- تقرير يوم محدد: استلام التاريخ ----------
    if context.user_data.get("admin_report_step") == "await_date":
        try:
            day = datetime.strptime(text.strip(), "%Y-%m-%d").date()
        except ValueError:
            await update.message.reply_text("❌ الصيغة غلط. مثال صحيح: 2026-10-06")
            return True

        last_done, current = _report_days()
        if day > current:
            await update.message.reply_text("❌ هذا التاريخ بالمستقبل.")
            return True

        context.user_data.pop("admin_report_step", None)
        await update.message.reply_text("⏳ جاري تجهيز التقرير...", reply_markup=main_menu(user_id))
        context.application.create_task(_send_report_to_admin(context.bot, user_id, day, day == current))
        return True

    # ---------- رسالة جماعية: استلام النص ----------
    if context.user_data.get("admin_bc_step") == "await_text":
        if len(text) > BROADCAST_MAX_LEN:
            await update.message.reply_text(
                f"❌ الرسالة طويلة ({len(text)} حرف). الحد الأقصى {BROADCAST_MAX_LEN}."
            )
            return True

        context.user_data["admin_bc_text"] = text
        context.user_data["admin_bc_step"] = "await_confirm"
        total = len(get_all_users())

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأكيد الإرسال", callback_data="admin_bc_confirm"),
            InlineKeyboardButton("❌ إلغاء", callback_data="admin_bc_cancel"),
        ]])
        await update.message.reply_text(
            f"👁️ معاينة الرسالة — رح تنبعت لـ {total} مستخدم:\n"
            "─────────────\n"
            f"{text}\n"
            "─────────────",
            reply_markup=keyboard
        )
        return True

    # ---------- رسالة فردية: استلام الـ ID ----------
    if context.user_data.get("admin_dm_step") == "await_id":
        target_id = parse_admin_int(text)
        if not target_id:
            await update.message.reply_text("❌ ابعت ID صحيح (أرقام فقط):")
            return True

        if not user_exists(target_id):
            await update.message.reply_text(
                f"⚠️ ما لقيت مستخدم بالـ ID {target_id}. جرب ID تاني أو اضغط تراجع:"
            )
            return True

        context.user_data["admin_dm_target"] = target_id
        context.user_data["admin_dm_step"] = "await_text"
        await update.message.reply_text(
            f"✅ المستخدم موجود: {target_id}\n\nهلأ اكتب الرسالة اللي بدك تبعتله:",
            reply_markup=cancel_keyboard()
        )
        return True

    # ---------- رسالة فردية: استلام النص وإرساله ----------
    if context.user_data.get("admin_dm_step") == "await_text":
        if len(text) > BROADCAST_MAX_LEN:
            await update.message.reply_text(
                f"❌ الرسالة طويلة ({len(text)} حرف). الحد الأقصى {BROADCAST_MAX_LEN}."
            )
            return True

        target_id = context.user_data.get("admin_dm_target")
        context.user_data.clear()

        try:
            await context.bot.send_message(target_id, text)
            result = f"✅ انبعتت الرسالة للمستخدم {target_id}"
        except Forbidden:
            result = f"🚫 المستخدم {target_id} حاظر البوت، ما قدرت أبعتله."
        except Exception as e:
            result = f"❌ فشل الإرسال للمستخدم {target_id}: {e}"

        await update.message.reply_text(result, reply_markup=main_menu(user_id))
        return True

    return False


async def admin_users_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    page = int(query.data.split("_")[-1])
    page_size = 10

    users = get_all_users()
    total = len(users)

    if total == 0:
        await query.edit_message_text("❌ لا يوجد مستخدمين بعد")
        return

    total_balance = sum(u[1] for u in users)
    start = (page - 1) * page_size
    end = start + page_size
    page_users = users[start:end]
    total_pages = (total + page_size - 1) // page_size

    text = f"👥 مستخدمو البوت — صفحة {page}/{total_pages}\n"
    text += f"📊 الإجمالي: {total} مستخدم | 💰 مجموع الأرصدة: {total_balance:,} {CURRENCY_LABEL}\n"
    text += "─" * 30 + "\n\n"

    for i, (uid, balance, ichancy_username) in enumerate(page_users, start=start + 1):
        ichancy = ichancy_username or "❌ لا يوجد"
        text += f"{i}. 🆔 `{uid}`\n"
        text += f"   💰 الرصيد: {balance:,} {CURRENCY_LABEL}\n"
        text += f"   🎮 iChancy: {ichancy}\n\n"

    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("◀️ السابق", callback_data=f"admin_users_{page - 1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("التالي ▶️", callback_data=f"admin_users_{page + 1}"))

    keyboard = []
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("🔄 تحديث", callback_data=f"admin_users_{page}")])
    keyboard.append([InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel")])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
#-------------------------------
async def admin_delete_user_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    context.user_data["admin_delete_user"] = True
    await query.message.reply_text(
        "🗑️ **حذف حساب مستخدم**\n\n"
        "أرسل الآن **معرّف المستخدم (ID)** الذي تريد حذفه من البوت نهائياً:",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard()
    )

async def ichancy_players_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    page_size = 10
    offset = int(query.data.split("_")[-1])

    await query.edit_message_text("⏳ جاري جلب البيانات من iChancy...")

    players = get_all_players(start=offset, limit=page_size)

    if not players:
        await query.edit_message_text("❌ فشل الاتصال بـ iChancy API")
        return

    if len(players) == 0 and offset == 0:
        await query.edit_message_text("❌ لا يوجد لاعبون في الكاشيرة بعد")
        return

    text = f"🎮 لاعبو iChancy\n"
    text += f"📋 يعرض {offset + 1} – {offset + len(players)}\n"
    text += "─" * 30 + "\n\n"

    for i, player in enumerate(players, start=offset + 1):
        login   = player.get("login", "—")
        balance = player.get("balance", 0)
        text += f"{i}. 🎮 `{login}`\n"
        text += f"   💰 {balance} NPS\n\n"

    nav_buttons = []
    if offset > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ السابق", callback_data=f"ichancy_players_{offset - page_size}"))
    if len(players) == page_size:
        nav_buttons.append(InlineKeyboardButton("التالي ▶️", callback_data=f"ichancy_players_{offset + page_size}"))

    keyboard = []
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("🔄 تحديث", callback_data=f"ichancy_players_{offset}")])
    keyboard.append([InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel")])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


def _admin_panel_keyboard(rate, usd_rate, notice_flag="", sham_mark="⚪"):
    """لوحة تحكم الأدمن — زرّان بكل صف (مشتركة بين زر اللوحة وزر الرجوع إليها)."""
    B = InlineKeyboardButton
    return InlineKeyboardMarkup([
        [B("👥 مستخدمو البوت", callback_data="admin_users_1"),
         B("🎮 لاعبو iChancy", callback_data="ichancy_players_0")],
        [B(f"💱 USDT = {rate:,}", callback_data="admin_set_rate"),
         B(f"💵 Sham $ = {usd_rate:,}", callback_data="admin_set_usd_rate")],
        [B("🎁 البونص الترحيبي", callback_data="admin_welcome_bonus"),
         B(f"📢 رسائل الخدمة{notice_flag}", callback_data="admin_notice_menu")],
        [B("💸 تحويل Sham Cash", callback_data="shtx_menu"),
         B("📜 السجل", callback_data="shamlog:all:0:1")],
        [B(f"{sham_mark} إدارة أكواد الشام كاش", callback_data="shcode:menu")],
        [B("🎁 نسب بونص الشحن", callback_data="admin_bonus_menu"),
         B("💸 التعويض", callback_data="admin_cashback_menu")],
        [B("🎯 عمولة الإحالة", callback_data="admin_referral_menu"),
         B("🎡 عجلة الحظ", callback_data="admin_spin_menu")],
        [B("💎 إنشاء كود هدية", callback_data="admin_create_gift"),
         B("🎁 إدارة الإهداء", callback_data="admin_gift_limit")],
        [B("🗑️ حذف حساب", callback_data="admin_delete_user"),
         B("👥 إدارة اللاعبين", callback_data="admin_pl_menu")],
        [B("📣 رسالة جماعية", callback_data="admin_broadcast"),
         B("✉️ رسالة فردية", callback_data="admin_dm")],
        [B("📊 التقرير اليومي", callback_data="admin_report_menu"),
         B("💸 إدارة نسبة السحب", callback_data="wdfee:menu")],
        [B("🤖 السحب التلقائي (Sham)", callback_data="admin_autowd_menu"),
         B("🧾 التشييك اليدوي للشحن", callback_data="admin_mc_menu")],
        [B("📞 إدارة أكواد Syriatel", callback_data="admin_sy_menu")],
        [B(f"{'✅' if WITHDRAWALS_CHANNEL_ID else '⚪'} اختبار قناة سجل السحب", callback_data="admin_withdraw_log_test")],
        [B(f"{'✅' if ALERTS_CHANNEL_ID else '⚪'} اختبار قناة التنبيهات", callback_data="admin_alert_test"),
         B(f"{'✅' if DEPOSITS_CHANNEL_ID else '⚪'} اختبار قناة الشحن", callback_data="admin_deposit_log_test")],
    ])


async def admin_panel_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    rate = get_usdt_rate()
    usd_rate = get_shamcash_usd_rate()
    notice_flag = " 🔴" if any_service_notice_active() else ""

    sham_mark = await _sham_code_mark()
    keyboard = _admin_panel_keyboard(rate, usd_rate, notice_flag, sham_mark)

    await query.edit_message_text(
        "🛠️ لوحة تحكم الأدمن",
        reply_markup=keyboard
    )


# ============================
# 💸 تحويل Sham Cash من حساب البوت (للأدمن) — الواجهة
# المنطق والحماية الأساسية كلها في sham_transfer.py؛ هنا فقط التفاعل مع تيليغرام.
# ============================
SHTX_PIN_WINDOW_SECONDS = 120        # مهلة إرسال الرمز بعد طلبه
SHTX_AUTO_VERIFY_DELAY_SECONDS = 5   # انتظار قبل أول تحقق تلقائي من تحويل غير مؤكد
SHTX_NOTE_MAX = 100                  # أقصى طول لملاحظة الأدمن

_SHTX_CUR_LABEL = {"SYP": "ليرة سورية (SYP)", "USD": "دولار (USD)"}
_shtx_log = logging.getLogger("sham_transfer_ui")


def _shtx_allowed_ids():
    """
    من يحق لهم استخدام التحويل. افتراضياً كل الأدمنية؛ ويمكن حصرها بمتغير البيئة
    SHAM_TRANSFER_ADMIN_IDS (أرقام مفصولة بفواصل) — والتقاطع مع ADMIN_IDS دائماً.
    """
    raw = os.getenv("SHAM_TRANSFER_ADMIN_IDS", "").strip()
    wanted = {int(x) for x in re.split(r"[,\s]+", raw) if x.isdigit()}
    return (wanted & set(ADMIN_IDS)) if wanted else set(ADMIN_IDS)


def shtx_user_allowed(user_id):
    return user_id in _shtx_allowed_ids()


def _shtx_money(amount, currency):
    return f"{sham_tx.format_amount(amount, currency)} {currency}"


def _shtx_short(address):
    address = address or ""
    return address if len(address) <= 14 else f"{address[:7]}…{address[-7:]}"


def _shtx_normalize_pin(text):
    """الرمز بصيغته الموحّدة (أرقام لاتينية) أو None إذا غير صالح — بدون رفع استثناء."""
    try:
        return sham_tx.validate_pin_format(text)
    except ValueError:
        return None


async def _shtx_notify(bot, text, exclude=None):
    """يسجّل أي حركة مالية/أمنية بقناة التنبيهات (بدل مراسلة كل أدمن بالخاص)."""
    await notify_admins(bot, text)


async def _shtx_delete_message(update):
    """حذف رسالة حساسة (الرمز) من المحادثة قدر الإمكان."""
    try:
        await update.message.delete()
    except Exception:
        pass


def _shtx_actor(update):
    user = update.effective_user
    return f"@{user.username}" if user.username else (user.first_name or str(user.id))


async def _shtx_menu_view():
    enabled = sham_tx.is_enabled()
    pin_set = sham_tx.has_pin()
    unknown = sham_tx.count_unknown()

    try:
        balances = await asyncio.to_thread(sham_tx.fetch_balances)
        balance_line = " | ".join(
            f"{c}: {sham_tx.format_amount(balances.get(c, Decimal(0)), c)}" for c in sham_tx.SUPPORTED_CURRENCIES
        )
    except Exception:
        balance_line = "تعذر جلب الرصيد حالياً"

    limit_lines = []
    for currency in sham_tx.SUPPORTED_CURRENCIES:
        single, daily = sham_tx.get_limits(currency)
        used = sham_tx.daily_total(currency)
        limit_lines.append(
            f"• {currency}: للعملية {sham_tx.format_amount(single, currency)} | "
            f"اليوم {sham_tx.format_amount(used, currency)} / {sham_tx.format_amount(daily, currency)}"
        )

    text = (
        "💸 تحويل Sham Cash من حساب البوت\n\n"
        f"🔘 الحالة: {'🟢 مفعّلة' if enabled else '⚪ متوقفة'}\n"
        f"🔐 رمز التحويل: {'✅ مضبوط' if pin_set else '❌ غير مضبوط (اضبطه أولاً)'}\n"
        f"💰 رصيد الحساب: {balance_line}\n\n"
        "📊 الحدود (حد العملية الواحدة | المصروف اليوم / الحد اليومي):\n"
        + "\n".join(limit_lines)
        + (f"\n\n⚠️ يوجد {unknown} تحويل غير مؤكد — راجعه قبل أي تحويل جديد." if unknown else "")
        + "\n\n⚠️ التحويل لا يمكن التراجع عنه. كل تحويل يتطلب رمز التحويل."
    )

    keyboard = [
        [InlineKeyboardButton("➕ تحويل جديد", callback_data="shtx_new")],
        [
            InlineKeyboardButton("🕘 آخر التحويلات", callback_data="shtx_hist"),
            InlineKeyboardButton(f"⚠️ غير المؤكدة ({unknown})" if unknown else "⚠️ غير المؤكدة", callback_data="shtx_unk"),
        ],
        [
            InlineKeyboardButton("⚙️ الحدود", callback_data="shtx_lim"),
            InlineKeyboardButton("🔐 رمز التحويل", callback_data="shtx_pin"),
        ],
        [
            InlineKeyboardButton("⛔ إيقاف فوري" if enabled else "🟢 تفعيل", callback_data="shtx_off" if enabled else "shtx_on"),
        ],
        [InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="admin_panel")],
    ]

    return text, InlineKeyboardMarkup(keyboard)


def _shtx_back_markup():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="shtx_menu")]])


def _shtx_ask_pin_state(context, purpose):
    context.user_data["shtx"] = {
        "step": "pin",
        "purpose": purpose,
        "deadline": time.time() + SHTX_PIN_WINDOW_SECONDS,
    }


async def _shtx_prompt_pin(message, why):
    await message.reply_text(
        f"🔐 {why}\n\nأرسل رمز التحويل الآن (تُحذف رسالتك تلقائياً).\n"
        f"⏳ المهلة: {SHTX_PIN_WINDOW_SECONDS // 60} دقيقتان.",
        reply_markup=cancel_keyboard()
    )


def _shtx_preview_text(req):
    currency = req["currency"]
    amount = Decimal(req["amount"])
    _single, daily = sham_tx.get_limits(currency)
    left_after = max(daily - sham_tx.daily_total(currency) - amount, Decimal(0))

    return (
        "🧾 مراجعة التحويل — راجع بدقة، لا يمكن التراجع بعد التنفيذ\n\n"
        "📤 من: حساب البوت\n"
        f"📥 إلى:\n{req['to_address']}\n"
        f"💰 المبلغ: {_shtx_money(amount, currency)}\n"
        f"📝 الملاحظة: {req['note'] or '—'}\n"
        f"🔖 المرجع: {req['ref']} (يُضاف تلقائياً لملاحظة التحويل)\n"
        f"📊 المتبقي من الحد اليومي بعد هذه العملية: {_shtx_money(left_after, currency)}\n\n"
        f"⏳ المعاينة صالحة {sham_tx.REQUEST_TTL_SECONDS // 60} دقائق."
    )


async def _shtx_show_preview(message, user_id, context, flow, note):
    try:
        req = sham_tx.create_request(user_id, flow["addr"], flow["cur"], Decimal(flow["amount"]), note)
    except ValueError as exc:
        context.user_data.pop("shtx", None)
        await message.reply_text(f"❌ {exc}", reply_markup=main_menu(user_id))
        return

    context.user_data["shtx"] = {"step": "preview", "request_id": req["id"]}

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأكيد (سيُطلب الرمز)", callback_data=f"shtx_ok_{req['id']}"),
            InlineKeyboardButton("❌ إلغاء", callback_data=f"shtx_no_{req['id']}"),
        ]
    ])

    await message.reply_text(_shtx_preview_text(req), reply_markup=keyboard)


async def _shtx_execute_send(bot, message, user_id, actor, request_id):
    await message.reply_text("⏳ جاري تنفيذ التحويل... لا تكرر العملية.", reply_markup=main_menu(user_id))

    out = await asyncio.to_thread(sham_tx.confirm_and_execute, request_id, user_id)
    status = out["status"]
    req = out.get("request") or {}
    summary = (
        f"💰 {_shtx_money(Decimal(req['amount']), req['currency'])}\n"
        f"📥 {req['to_address']}\n"
        f"🔖 {req['ref']}"
    ) if req else ""

    if status == "sent":
        result = out["result"]
        recipient = f"\n👤 المستلم: {result.recipient_name}" if result.recipient_name else ""
        await message.reply_text(
            f"✅ تم التحويل بنجاح\n\n{summary}{recipient}", reply_markup=main_menu(user_id)
        )
        await _shtx_notify(bot, f"💸 تحويل Sham Cash نُفِّذ\n👤 بواسطة: {actor} ({user_id})\n\n{summary}{recipient}", exclude=user_id)

    elif status == "failed":
        reason = out["result"].message
        await message.reply_text(
            f"❌ لم يتم التحويل — لم يخرج أي مبلغ.\n\n{summary}\n\nالسبب: {reason}", reply_markup=main_menu(user_id)
        )
        await _shtx_notify(bot, f"❌ محاولة تحويل Sham Cash فشلت (لم يخرج مبلغ)\n👤 {actor} ({user_id})\n\n{summary}\nالسبب: {reason}", exclude=user_id)

    elif status == "unknown":
        await message.reply_text(
            "⚠️ حالة التحويل غير مؤكدة!\n\n"
            "⛔ لا تعد المحاولة قبل التحقق، حتى لا يُحوَّل المبلغ مرتين.\n\n"
            f"{summary}\n\n🔎 جاري التحقق من سجل التحويلات الصادرة...",
            reply_markup=main_menu(user_id)
        )
        await _shtx_notify(bot, f"⚠️ تحويل Sham Cash بحالة غير مؤكدة\n👤 {actor} ({user_id})\n\n{summary}\nراجعه من: لوحة الأدمن ← تحويل Sham Cash ← غير المؤكدة", exclude=user_id)

        await asyncio.sleep(SHTX_AUTO_VERIFY_DELAY_SECONDS)
        verified = await asyncio.to_thread(sham_tx.verify_unknown, request_id)

        if verified["status"] == "found":
            await message.reply_text("✅ تم التأكد من سجل التحويلات الصادرة: التحويل تمّ فعلاً.")
        else:
            await message.reply_text(
                "ℹ️ لم يظهر التحويل بعد في السجل (قد يتأخر ظهوره).\n"
                "تحقق لاحقاً من: غير المؤكدة. لا تحوّل مجدداً قبل التأكد."
            )

    else:
        explanations = {
            "disabled": "الميزة متوقفة حالياً.",
            "expired": f"انتهت صلاحية المعاينة ({sham_tx.REQUEST_TTL_SECONDS // 60} دقائق). ابدأ تحويلاً جديداً.",
            "not_pending": "هذا الطلب نُفِّذ أو أُلغي مسبقاً — لم يتم أي تحويل جديد.",
            "missing": "الطلب غير موجود.",
            "limit": out.get("message") or "تجاوز الحد المسموح.",
        }
        await message.reply_text(f"⛔ لم يتم التحويل: {explanations.get(status, status)}", reply_markup=main_menu(user_id))


async def _shtx_after_pin(update, context, purpose):
    """ينفّذ الإجراء المحمي بعد نجاح التحقق من الرمز."""
    user_id = update.effective_user.id
    actor = _shtx_actor(update)
    message = update.message
    kind = purpose["type"]

    if kind == "send":
        await _shtx_execute_send(context.bot, message, user_id, actor, purpose["request_id"])

    elif kind == "withdraw_pay":
        await _withdraw_pay_execute(context.bot, message, user_id, purpose["wid"])

    elif kind == "enable":
        try:
            sham_tx.set_enabled(True)
        except ValueError as exc:
            await message.reply_text(f"❌ {exc}", reply_markup=main_menu(user_id))
            return
        await message.reply_text("🟢 تم تفعيل التحويل.", reply_markup=main_menu(user_id))
        await _shtx_notify(context.bot, f"🟢 تم تفعيل تحويل Sham Cash\n👤 {actor} ({user_id})", exclude=user_id)

    elif kind == "limits":
        currency = purpose["cur"]
        try:
            sham_tx.set_limits(currency, Decimal(purpose["single"]), Decimal(purpose["daily"]))
        except ValueError as exc:
            await message.reply_text(f"❌ {exc}", reply_markup=main_menu(user_id))
            return
        text = (
            f"⚙️ تم تعديل حدود {currency}: للعملية {_shtx_money(Decimal(purpose['single']), currency)} | "
            f"يومياً {_shtx_money(Decimal(purpose['daily']), currency)}"
        )
        await message.reply_text("✅ " + text, reply_markup=main_menu(user_id))
        await _shtx_notify(context.bot, f"{text}\n👤 {actor} ({user_id})", exclude=user_id)

    elif kind == "resolve":
        as_status = purpose["as_status"]
        ok = sham_tx.resolve_unknown(purpose["id"], as_status, error=f"حُسم يدوياً بواسطة {user_id}")
        label = "تم فعلاً ✅" if as_status == "sent" else "لم يتم ❌ (حُرِّر الحد اليومي)"
        if not ok:
            await message.reply_text("ℹ️ هذا الطلب لم يعد بحالة غير مؤكدة.", reply_markup=main_menu(user_id))
            return
        await message.reply_text(f"✅ تم حسم التحويل #{purpose['id']}: {label}", reply_markup=main_menu(user_id))
        await _shtx_notify(context.bot, f"🛠️ حُسم تحويل غير مؤكد #{purpose['id']}: {label}\n👤 {actor} ({user_id})", exclude=user_id)
        await _withdraw_payout_flush(context.bot)


async def _shtx_handle_pin_text(update, context, flow, text):
    user_id = update.effective_user.id
    actor = _shtx_actor(update)

    check = await asyncio.to_thread(sham_tx.verify_pin, user_id, text)

    if check.status == "ok":
        context.user_data.pop("shtx", None)
        await _shtx_after_pin(update, context, flow["purpose"])
        return

    if check.status == "locked":
        context.user_data.pop("shtx", None)
        minutes = max(1, math.ceil(check.locked_seconds / 60))
        await update.message.reply_text(
            f"🔒 الرمز مقفل لمدة {minutes} دقيقة بسبب محاولات خاطئة.", reply_markup=main_menu(user_id)
        )
        await _shtx_notify(context.bot, f"🔒 قُفل رمز تحويل Sham Cash بعد محاولات خاطئة\n👤 {actor} ({user_id})", exclude=user_id)
        return

    if check.status == "no_pin":
        context.user_data.pop("shtx", None)
        await update.message.reply_text("❌ لم يُضبط رمز التحويل بعد.", reply_markup=main_menu(user_id))
        return

    await update.message.reply_text(
        f"❌ الرمز غير صحيح. المحاولات المتبقية: {check.attempts_left}", reply_markup=cancel_keyboard()
    )
    await _shtx_notify(context.bot, f"⚠️ محاولة رمز تحويل خاطئة\n👤 {actor} ({user_id}) — المتبقي {check.attempts_left}", exclude=user_id)


async def shtx_handle_text(update, context, text):
    """يعالج نص الأدمن أثناء أي خطوة من خطوات التحويل. يرجع True إذا استهلك الرسالة."""
    flow = context.user_data.get("shtx")

    if not flow:
        return False

    user_id = update.effective_user.id

    if not shtx_user_allowed(user_id):
        context.user_data.pop("shtx", None)
        return False

    step = flow.get("step")
    message = update.message

    # ---- خطوات الرمز: نحذف رسالة الأدمن فوراً (تحوي الرمز)
    if step in ("pin", "setpin_old", "setpin_new", "setpin_confirm"):
        await _shtx_delete_message(update)

        if time.time() > flow.get("deadline", 0):
            context.user_data.pop("shtx", None)
            await message.reply_text("⌛ انتهت المهلة. أعد العملية من البداية.", reply_markup=main_menu(user_id))
            return True

    if step == "pin":
        await _shtx_handle_pin_text(update, context, flow, text)
        return True

    if step == "addr":
        try:
            flow["addr"] = sham_tx.validate_address(text)
        except ValueError as exc:
            await message.reply_text(f"❌ {exc}", reply_markup=cancel_keyboard())
            return True

        flow["step"] = "cur"
        buttons = [[InlineKeyboardButton(_SHTX_CUR_LABEL[c], callback_data=f"shtx_cur_{c}") for c in sham_tx.SUPPORTED_CURRENCIES]]
        await message.reply_text(f"📥 المستلم:\n{flow['addr']}\n\nاختر العملة:", reply_markup=InlineKeyboardMarkup(buttons))
        return True

    if step == "cur":
        await message.reply_text("اختر العملة من الأزرار أعلاه.", reply_markup=cancel_keyboard())
        return True

    if step == "amount":
        currency = flow["cur"]
        try:
            amount = sham_tx.parse_amount(text, currency)
            sham_tx.check_limits(currency, amount)
        except ValueError as exc:
            await message.reply_text(f"❌ {exc}", reply_markup=cancel_keyboard())
            return True

        try:
            balances = await asyncio.to_thread(sham_tx.fetch_balances)
        except Exception:
            balances = None

        if balances is not None and amount > balances.get(currency, Decimal(0)):
            await message.reply_text(
                f"❌ رصيد حساب البوت غير كافٍ: المتاح {_shtx_money(balances.get(currency, Decimal(0)), currency)}.",
                reply_markup=cancel_keyboard()
            )
            return True

        flow["amount"] = str(amount)
        flow["step"] = "note"
        await message.reply_text(
            f"💰 المبلغ: {_shtx_money(amount, currency)}\n\n"
            f"📝 أرسل ملاحظة تظهر للمستلم (حتى {SHTX_NOTE_MAX} حرفاً) أو اضغط تخطي:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ تخطي", callback_data="shtx_skip")]])
        )
        return True

    if step == "note":
        if len(sham_tx.sanitize_note(text, max_length=10 ** 6)) > SHTX_NOTE_MAX:
            await message.reply_text(f"❌ الملاحظة أطول من {SHTX_NOTE_MAX} حرفاً.", reply_markup=cancel_keyboard())
            return True

        await _shtx_show_preview(message, user_id, context, flow, text)
        return True

    if step == "preview":
        await message.reply_text("استخدم أزرار المعاينة (تأكيد / إلغاء) أعلاه.", reply_markup=cancel_keyboard())
        return True

    # ---- ضبط/تغيير الرمز
    if step == "setpin_old":
        check = await asyncio.to_thread(sham_tx.verify_pin, user_id, text)

        if check.status == "ok":
            flow.update(step="setpin_new", old_pin=text, deadline=time.time() + SHTX_PIN_WINDOW_SECONDS)
            await message.reply_text(
                f"🔐 أرسل الرمز الجديد ({sham_tx.PIN_MIN_DIGITS}–{sham_tx.PIN_MAX_DIGITS} رقماً):", reply_markup=cancel_keyboard()
            )
        elif check.status == "locked":
            context.user_data.pop("shtx", None)
            await message.reply_text("🔒 الرمز مقفل مؤقتاً بسبب محاولات خاطئة.", reply_markup=main_menu(user_id))
            await _shtx_notify(context.bot, f"🔒 قُفل رمز تحويل Sham Cash\n👤 {_shtx_actor(update)} ({user_id})", exclude=user_id)
        else:
            await message.reply_text(f"❌ الرمز الحالي غير صحيح. المتبقي: {check.attempts_left}", reply_markup=cancel_keyboard())
            await _shtx_notify(context.bot, f"⚠️ محاولة خاطئة لتغيير رمز التحويل\n👤 {_shtx_actor(update)} ({user_id})", exclude=user_id)
        return True

    if step == "setpin_new":
        try:
            flow["new_pin"] = sham_tx.validate_pin_format(text)
        except ValueError as exc:
            await message.reply_text(f"❌ {exc}", reply_markup=cancel_keyboard())
            return True

        flow["step"] = "setpin_confirm"
        await message.reply_text("🔐 أعد إرسال الرمز الجديد للتأكيد:", reply_markup=cancel_keyboard())
        return True

    if step == "setpin_confirm":
        if _shtx_normalize_pin(text) != flow.get("new_pin"):
            context.user_data.pop("shtx", None)
            await message.reply_text("❌ الرمزان غير متطابقين. أعد العملية.", reply_markup=main_menu(user_id))
            return True

        result = await asyncio.to_thread(sham_tx.set_pin, user_id, flow["new_pin"], flow.get("old_pin"))
        context.user_data.pop("shtx", None)

        if result.status != "ok":
            await message.reply_text("❌ تعذر تغيير الرمز (الرمز الحالي غير صحيح أو مقفل).", reply_markup=main_menu(user_id))
            return True

        await message.reply_text("✅ تم ضبط رمز التحويل.", reply_markup=main_menu(user_id))
        await _shtx_notify(context.bot, f"🔐 تم ضبط/تغيير رمز تحويل Sham Cash\n👤 {_shtx_actor(update)} ({user_id})", exclude=user_id)
        return True

    # ---- تعديل الحدود
    if step in ("limit_single", "limit_daily"):
        currency = flow["cur"]
        try:
            value = sham_tx.parse_amount(text, currency)
        except ValueError as exc:
            await message.reply_text(f"❌ {exc}", reply_markup=cancel_keyboard())
            return True

        if step == "limit_single":
            flow.update(single=str(value), step="limit_daily")
            await message.reply_text(
                f"✅ حد العملية الواحدة: {_shtx_money(value, currency)}\n\nأرسل الحد اليومي ({currency}):",
                reply_markup=cancel_keyboard()
            )
            return True

        if Decimal(flow["single"]) > value:
            await message.reply_text("❌ الحد اليومي لا يمكن أن يكون أقل من حد العملية الواحدة.", reply_markup=cancel_keyboard())
            return True

        _shtx_ask_pin_state(context, {"type": "limits", "cur": currency, "single": flow["single"], "daily": str(value)})
        await _shtx_prompt_pin(message, f"تأكيد تعديل حدود {currency}")
        return True

    return False


async def shtx_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id

    if not shtx_user_allowed(user_id):
        await query.answer("غير مصرّح", show_alert=True)
        return

    await query.answer()

    data = query.data
    message = query.message
    flow = context.user_data.get("shtx") or {}

    if data == "shtx_menu":
        context.user_data.pop("shtx", None)
        text, markup = await _shtx_menu_view()
        await _safe_edit_message(query, text, reply_markup=markup)
        return

    if data == "shtx_new":
        if not sham_tx.is_enabled():
            await message.reply_text("⛔ الميزة متوقفة. اضبط الرمز ثم فعّلها من القائمة.")
            return
        if not sham_tx.has_pin():
            await message.reply_text("❌ اضبط رمز التحويل أولاً.")
            return

        await asyncio.to_thread(sham_tx.expire_old_pending)
        context.user_data["shtx"] = {"step": "addr"}
        await message.reply_text(
            "📥 أرسل عنوان حساب Sham Cash للمستلم (كاملاً كما هو، بدون فراغات):", reply_markup=cancel_keyboard()
        )
        return

    if data.startswith("shtx_cur_"):
        currency = data.rsplit("_", 1)[-1]
        if flow.get("step") != "cur" or currency not in sham_tx.SUPPORTED_CURRENCIES:
            await message.reply_text("⌛ هذه الخطوة لم تعد فعّالة. ابدأ من جديد.")
            return

        flow.update(cur=currency, step="amount")
        single, daily = sham_tx.get_limits(currency)
        remaining = max(daily - sham_tx.daily_total(currency), Decimal(0))
        await message.reply_text(
            f"💰 أرسل المبلغ بـ {_SHTX_CUR_LABEL[currency]}:\n\n"
            f"• الحد للعملية: {_shtx_money(single, currency)}\n"
            f"• المتبقي اليوم: {_shtx_money(remaining, currency)}",
            reply_markup=cancel_keyboard()
        )
        return

    if data == "shtx_skip":
        if flow.get("step") != "note":
            await message.reply_text("⌛ هذه الخطوة لم تعد فعّالة. ابدأ من جديد.")
            return
        await _shtx_show_preview(message, user_id, context, flow, "")
        return

    if data.startswith("shtx_ok_") or data.startswith("shtx_no_"):
        try:
            request_id = int(data.rsplit("_", 1)[-1])
        except ValueError:
            return

        if data.startswith("shtx_no_"):
            cancelled = await asyncio.to_thread(sham_tx.cancel_request, request_id, user_id)
            context.user_data.pop("shtx", None)
            await message.reply_text("❌ تم إلغاء التحويل." if cancelled else "ℹ️ هذا الطلب لم يعد معلقاً.", reply_markup=main_menu(user_id))
            return

        req = sham_tx.get_request(request_id)
        if not req or req["admin_id"] != user_id or req["status"] != "pending":
            await message.reply_text("ℹ️ هذا الطلب نُفِّذ أو أُلغي أو انتهت صلاحيته. لم يتم أي تحويل جديد.")
            return

        _shtx_ask_pin_state(context, {"type": "send", "request_id": request_id})
        await _shtx_prompt_pin(message, "تأكيد التحويل")
        return

    if data == "shtx_hist":
        rows = await asyncio.to_thread(sham_tx.recent_transfers, 10)
        icons = {"sent": "✅", "failed": "❌", "unknown": "⚠️", "sending": "⏳"}

        if not rows:
            body = "لا توجد تحويلات بعد."
        else:
            body = "\n".join(
                f"{icons.get(r['status'], '•')} #{r['id']} | {_shtx_money(Decimal(r['amount']), r['currency'])} | "
                f"{_shtx_short(r['to_address'])} | {r['ref']}"
                for r in rows
            )

        await _safe_edit_message(query, f"🕘 آخر التحويلات\n\n{body}", reply_markup=_shtx_back_markup())
        return

    if data == "shtx_unk":
        rows = [r for r in await asyncio.to_thread(sham_tx.recent_transfers, 50) if r["status"] == "unknown"]

        if not rows:
            await _safe_edit_message(query, "✅ لا توجد تحويلات غير مؤكدة.", reply_markup=_shtx_back_markup())
            return

        keyboard = []
        lines = []
        for r in rows[:8]:
            lines.append(f"⚠️ #{r['id']} | {_shtx_money(Decimal(r['amount']), r['currency'])} | {_shtx_short(r['to_address'])} | {r['ref']}")
            keyboard.append([
                InlineKeyboardButton(f"🔎 تحقق #{r['id']}", callback_data=f"shtx_chk_{r['id']}"),
                InlineKeyboardButton("✅ تم", callback_data=f"shtx_sent_{r['id']}"),
                InlineKeyboardButton("❌ لم يتم", callback_data=f"shtx_fail_{r['id']}"),
            ])
        keyboard.append([InlineKeyboardButton("⬅️ رجوع", callback_data="shtx_menu")])

        await _safe_edit_message(
            query,
            "⚠️ تحويلات غير مؤكدة\n\n" + "\n".join(lines) +
            "\n\n🔎 تحقق: يبحث في سجل الصادر عن المرجع.\n"
            "✅ تم / ❌ لم يتم: حسم يدوي بعد تأكدك من تطبيق شام كاش (يتطلب الرمز).",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data.startswith("shtx_chk_"):
        request_id = int(data.rsplit("_", 1)[-1])
        verified = await asyncio.to_thread(sham_tx.verify_unknown, request_id)

        answers = {
            "found": "✅ وُجد في سجل الصادر — تم التحويل فعلاً وسُجِّل.",
            "not_found": "ℹ️ لم يظهر في سجل الصادر بعد. قد يتأخر ظهوره؛ أعد التحقق لاحقاً أو افحص التطبيق.",
            "error": "⚠️ تعذر الاستعلام عن السجل الآن. حاول لاحقاً.",
            "not_unknown": "ℹ️ هذا الطلب لم يعد بحالة غير مؤكدة.",
        }
        await message.reply_text(answers.get(verified["status"], verified["status"]))
        if verified["status"] == "found":
            await _withdraw_payout_flush(context.bot)
        return

    if data.startswith("shtx_sent_") or data.startswith("shtx_fail_"):
        request_id = int(data.rsplit("_", 1)[-1])
        as_status = "sent" if data.startswith("shtx_sent_") else "failed"
        req = sham_tx.get_request(request_id)

        if not req or req["status"] != "unknown":
            await message.reply_text("ℹ️ هذا الطلب لم يعد بحالة غير مؤكدة.")
            return

        warning = (
            "اختر هذا فقط إذا تأكدت من تطبيق شام كاش أن المبلغ لم يخرج. سيُحرَّر الحد اليومي ويصير ممكناً إعادة التحويل."
            if as_status == "failed"
            else "اختر هذا فقط إذا رأيت التحويل فعلاً في تطبيق شام كاش."
        )
        _shtx_ask_pin_state(context, {"type": "resolve", "id": request_id, "as_status": as_status})
        await _shtx_prompt_pin(message, f"حسم التحويل #{request_id} ({'تم' if as_status == 'sent' else 'لم يتم'})\n⚠️ {warning}")
        return

    if data == "shtx_lim":
        lines = []
        keyboard = []
        for currency in sham_tx.SUPPORTED_CURRENCIES:
            single, daily = sham_tx.get_limits(currency)
            lines.append(f"• {currency}: للعملية {_shtx_money(single, currency)} | يومياً {_shtx_money(daily, currency)}")
            keyboard.append([InlineKeyboardButton(f"✏️ حدود {currency}", callback_data=f"shtx_limedit_{currency}")])
        keyboard.append([InlineKeyboardButton("⬅️ رجوع", callback_data="shtx_menu")])

        await _safe_edit_message(
            query,
            "⚙️ حدود التحويل\n\n" + "\n".join(lines) + "\n\nتعديل أي حد يتطلب رمز التحويل.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data.startswith("shtx_limedit_"):
        currency = data.rsplit("_", 1)[-1]
        if currency not in sham_tx.SUPPORTED_CURRENCIES:
            return
        context.user_data["shtx"] = {"step": "limit_single", "cur": currency}
        await message.reply_text(f"✏️ أرسل الحد الأقصى للعملية الواحدة ({currency}):", reply_markup=cancel_keyboard())
        return

    if data == "shtx_pin":
        if sham_tx.has_pin():
            context.user_data["shtx"] = {"step": "setpin_old", "deadline": time.time() + SHTX_PIN_WINDOW_SECONDS}
            await message.reply_text("🔐 أرسل رمز التحويل الحالي (تُحذف رسالتك):", reply_markup=cancel_keyboard())
        else:
            context.user_data["shtx"] = {"step": "setpin_new", "deadline": time.time() + SHTX_PIN_WINDOW_SECONDS}
            await message.reply_text(
                f"🔐 أرسل رمزاً جديداً ({sham_tx.PIN_MIN_DIGITS}–{sham_tx.PIN_MAX_DIGITS} رقماً، غير متسلسل ولا متكرر). تُحذف رسالتك:",
                reply_markup=cancel_keyboard()
            )
        return

    if data == "shtx_on":
        if not sham_tx.has_pin():
            await message.reply_text("❌ اضبط رمز التحويل أولاً.")
            return
        _shtx_ask_pin_state(context, {"type": "enable"})
        await _shtx_prompt_pin(message, "تأكيد تفعيل التحويل")
        return

    if data == "shtx_off":
        sham_tx.set_enabled(False)
        await _shtx_notify(
            context.bot,
            f"⛔ تم إيقاف تحويل Sham Cash\n👤 {query.from_user.first_name} ({user_id})",
            exclude=user_id,
        )
        text, markup = await _shtx_menu_view()
        await _safe_edit_message(query, text, reply_markup=markup)
        return


# ============================
# 📈 شاشة "العروض والبونصات الحالية" (للزبون) — كلها من إعدادات الأدمن الحالية
# ============================

# شروط التعويض اليومي كما تظهر للزبون
DAILY_CASHBACK_RULES = (
    "يتم احتساب الخسارة ضمن يوم كامل: من 12:00 ليلاً إلى 12:00 ليلاً (24 ساعة كاملة)",
    "التعويض يكون فقط عن اليوم السابق",
    "يجب أن يكون رصيدك في الحساب = 0 عند الساعة 12:00 ليلاً",
    "إذا كان لديك رصيد عند الساعة 12:00 ليلاً وكان أكبر من مجموع إيداعاتك خلال اليوم ← لا تستحق التعويض ❌",
    "إذا كان مجموع السحوبات أكبر من الإيداعات (يعني أنت رابح ❤️) لا تحصل على التعويض ❌",
    "أي خسارة تحدث بعد الساعة 12:00 ليلاً لا تُحتسب ضمن تعويض اليوم (تُحتسب مع خسارة اليوم التالي) ❌",
)


def _fee_notice(fee_pct):
    """سطر التنبيه عن رسوم السحب + مثال حسب النسبة الفعلية للاعب."""
    if fee_pct <= 0:
        return "✅ لا توجد رسوم على السحب", f"مثال: إذا أدخلت 1,000 {CURRENCY_LABEL} → ستستلم 1,000 {CURRENCY_LABEL}"
    net = 1000 - calc_withdraw_fee(1000, fee_pct)
    return (
        f"⚠️ تنبيه: يتم حسم {_fmt_pct(fee_pct)} من المبلغ كرسوم سحب",
        f"مثال: إذا أدخلت 1,000 {CURRENCY_LABEL} → ستستلم {net:,} {CURRENCY_LABEL}",
    )


def _fmt_pct(fraction):
    """0.05 → '5%'  |  0.125 → '12.5%'"""
    value = float(fraction) * 100
    return f"{value:.0f}%" if abs(value - round(value)) < 1e-9 else f"{value:.1f}%"


def _ar_every_days(n):
    """'كل يوم' / 'كل يومين' / 'كل 10 أيام' / 'كل 15 يوماً'"""
    return "كل " + {1: "يوم", 2: "يومين"}.get(int(n), ar_days(n))


def _offer_end_line(mode):
    """سطر مدة العرض للزبون (فارغ إذا لا توجد مدة محددة)."""
    started_at = get_program_started_at(mode) if mode in CASHBACK_CHOICES else None

    if mode == "weekly":
        weeks = get_weekly_duration_weeks()
        end_at = weekly_program_end(started_at, weeks) if weeks > 0 else None
        end_day = _utc_to_syria_day(end_at) if end_at else None

        if not end_day:
            return ""

        remaining = datetime.strptime(end_at, UTC_TS_FORMAT) - datetime.utcnow()
        days_left = max(0, math.ceil(remaining.total_seconds() / 86400))
        left = ar_days(days_left) if days_left > 0 else "أقل من يوم"

        return f"⏳ العرض ساري حتى {end_day} (المتبقي ≈ {left})"

    if mode == "daily":
        days = get_daily_duration_days()
        start_day = _utc_to_syria_day(started_at) if started_at else None

        if days <= 0 or not start_day:
            return ""

        return f"⏳ العرض ساري لغاية تعويض يوم {_shift_day(start_day, days)}"

    return ""


def _offers_bonus_section():
    percents = get_all_bonus_percents()

    labels = (
        ("syriatel", "🟢 Syriatel Cash"),
        ("shamcash", "⚡ Sham Cash (ليرة ودولار)"),
        ("usdt", "💰 USDT"),
    )

    lines = [
        f"{label}: <b>+{_fmt_pct(percents.get(key, 0.0))}</b>"
        for key, label in labels
        if percents.get(key, 0.0) > 0
    ]

    if not lines:
        return "🎁 <b>بونص الشحن</b>\nلا يوجد بونص على الشحن حالياً."

    return (
        "🎁 <b>بونص الشحن</b>\n"
        + "\n".join(lines)
        + "\nيُضاف البونص تلقائياً على كل عملية شحن."
    )


def _offers_rates_section():
    return (
        "💱 <b>أسعار الصرف الحالية</b>\n"
        f"• 1 USDT = {get_usdt_rate():,} {CURRENCY_LABEL}\n"
        f"• 1$ (Sham Cash) = {get_shamcash_usd_rate():,} {CURRENCY_LABEL}"
    )


def _offers_cashback_section():
    blocks = []

    if get_cashback_program_enabled("weekly") and get_cashback_percent() > 0:
        lines = [
            f"📅 <b>الاسترداد الأسبوعي: {_fmt_pct(get_cashback_percent())}</b>",
            "• يُحسب من صافي خسارتك في حسابك على iChancy.",
            "• يُضاف تلقائياً إلى رصيدك داخل البوت كل 7 أيام.",
        ]
        end_line = _offer_end_line("weekly")
        if end_line:
            lines.append(end_line)

        blocks.append("\n".join(lines))

    if get_cashback_program_enabled("daily") and get_daily_cashback_percent() > 0:
        percent = get_daily_cashback_percent()
        how = (
            "يتم تعويض خسارة اليوم السابق <b>بالكامل</b>"
            if percent >= 0.9999
            else f"يتم تعويض <b>{_fmt_pct(percent)}</b> من خسارة اليوم السابق"
        )

        lines = [
            "🌅 <b>التعويض اليومي</b>\n",
            f"⏰ <b>موعد التعويض:</b> الساعة {DAILY_CASHBACK_HOUR}:00 صباحاً (بتوقيت سوريا)\n",
            "📌 <b>كيف يتم التعويض؟</b>",
            how + "، ويُضاف مباشرة إلى رصيدك داخل البوت.",
            f"(مثال: تعويض يوم الخميس الساعة {DAILY_CASHBACK_HOUR} صباحاً يكون عن خسارة يوم الأربعاء)\n",
            "📋 <b>الشروط:</b>",
        ]
        lines += [f"• {rule}" for rule in DAILY_CASHBACK_RULES]

        end_line = _offer_end_line("daily")
        if end_line:
            lines += ["", end_line]

        blocks.append("\n".join(lines))

    if not blocks:
        return "💸 <b>التعويض (Cashback)</b>\nلا يوجد نظام تعويض مفعّل حالياً."

    intro = (
        "يختار كل مستخدم نظامه (يومي أو أسبوعي) عند أول استخدام ولا يمكن تغييره لاحقاً."
        if is_cashback_user_choice_enabled()
        else "النظام المفعّل يُطبَّق تلقائياً على جميع المستخدمين."
    )

    return (
        "💸 <b>التعويض (Cashback)</b>\n"
        + intro + "\n\n"
        + "\n\n".join(blocks)
    )


def _offers_referral_section():
    if not referral_burn.is_enabled():
        return _offers_referral_section_legacy()
    days = referral_burn.CYCLE_DAYS
    need = referral_burn.MIN_ACTIVE_REFERRALS
    return (
        "🎯 <b>نظام الإحالات (حسب الحرق)</b>\n"
        "• تختار نظامك مرة واحدة: 🌳 <b>هرمي</b> (9% / 6% / 3% عبر 3 مستويات) أو 👤 <b>فردي</b> (10% من كل مُحال مباشر).\n"
        f"• الشرط: {need} إحالات نشطة على الأقل (يشحنون ويلعبون)، وأن يكون حرق شبكتك أعلى من سحبها.\n"
        f"• تُحسب الأرباح وتُضاف لرصيدك تلقائياً كل {days} أيام من تاريخ آخر توزيع.\n"
        "• رابطك الخاص في زر «🤝نظام الإحالات»."
    )


def _offers_referral_section_legacy():
    percent = get_referral_commission_percent()
    period_days = get_referral_period_days()

    if percent <= 0 or period_days <= 0:
        return ""

    return (
        "🎯 <b>عمولة الإحالة</b>\n"
        f"• تربح <b>{_fmt_pct(percent)}</b> من كل عمليات شحن الأشخاص الذين يسجّلون عبر رابطك.\n"
        f"• تُصرف أرباحك تلقائياً {_ar_every_days(period_days)}.\n"
        "• تُحتسب الأرباح بعد تسجيل 3 إحالات نشطة أو أكثر (حسب الشروط والأحكام).\n"
        "• رابطك الخاص في زر «🤝نظام الإحالات»."
    )


def _offers_spin_section():
    return (
        "🎰 <b>اللفة المجانية</b>\n"
        f"• بعد أول شحن لرصيدك، لك لفة مجانية كل {get_spin_cooldown_hours()} ساعة على الأقل.\n"
        "• قد تربح رصيداً مجانياً، أو زيادة على شحنتك القادمة، أو تعويضاً يومياً، أو لفة إضافية."
    )


def _offers_admin_cashback_hint():
    """للأدمن فقط: يبيّن لماذا قد يظهر «لا يوجد تعويض» رغم أن برنامجاً مفعّل (نسبته 0%)."""
    hints = []

    if get_cashback_program_enabled("weekly") and get_cashback_percent() <= 0:
        hints.append("• الأسبوعي مفعّل لكن نسبته 0% — حدّدها من لوحة التعويض ← ✏️ نسبة الأسبوعي.")

    if get_cashback_program_enabled("daily") and get_daily_cashback_percent() <= 0:
        hints.append("• اليومي مفعّل لكن نسبته 0% — حدّدها من لوحة التعويض.")

    if get_cashback_program_enabled("weekly") and get_cashback_program_enabled("daily"):
        hints.append("• الأسبوعي واليومي مفعّلان معاً — لن يُصرف شيء حتى توقف أحدهما.")

    if not hints:
        return ""

    return "🛠 <b>تنبيه للأدمن فقط (لا يظهر للزبائن):</b>\n" + "\n".join(hints)


def build_offers_text(viewer_id=None):
    """
    نص شاشة العروض الكاملة للزبون (HTML). كل قسم يُبنى مستقلاً: لو فشل قسم لأي سبب
    نتخطاه ونعرض الباقي بدل أن يفشل الزر كله.
    """
    sections = []

    for builder in (
        _offers_bonus_section,
        _offers_rates_section,
        _offers_cashback_section,
        _offers_referral_section,
        _offers_spin_section,
    ):
        try:
            block = builder()
        except Exception:
            logging.getLogger("offers").exception("offers section failed: %s", builder.__name__)
            continue

        if block:
            sections.append(block)

    text = (
        "📈 <b>العروض والبونصات الحالية</b>\n\n"
        + "\n\n".join(sections)
        + "\n\n📌 <b>ملاحظة:</b>\nالبونص والتعويض يُضافان وفق شروط كل نظام وحسب الإعدادات الحالية."
    )

    if viewer_id in ADMIN_IDS:
        try:
            hint = _offers_admin_cashback_hint()
        except Exception:
            logging.getLogger("offers").exception("admin cashback hint failed")
            hint = ""

        if hint:
            text += "\n\n" + hint

    return text


async def menu_handler(update, context):
    user_id = update.effective_user.id
    text = update.message.text
    

    # ============================
    # ✅ زر التراجع — يعمل في أي مرحلة
    # ============================
    if text == "❌ تراجع عن العملية":
        context.user_data.clear()
        await update.message.reply_text(
            "↩️ تم إلغاء العملية والرجوع للقائمة الرئيسية",
            reply_markup=main_menu(user_id)
        )
        return

    # ======================================================
    # 🛡️ شبكة أمان عامة: إذا كان في عملية معلّقة (البوت بانتظار
    # رقم أو نص معيّن من الأدمن/المستخدم) وضغط بدل هيك على أي زر
    # حقيقي من القائمة، نعتبرها إلغاء ضمني للعملية المعلّقة ونكمل
    # تنفيذ الزر الجديد عادي — بدل ما يضل البوت طالب نفس القيمة
    # إلى الأبد.
    # ======================================================
    KNOWN_MENU_TEXTS = {
        "🧾  معلومات حسابي",
        "📥 شحن رصيد في البوت", "📤 سحب رصيد من البوت",
        "🔼 شحن حساب الأيشانسي", "🔽 سحب من الأيشانسي",
        "🎁 إهداء رصيد", "🤝نظام الإحالات",
        "💎كود هدية", "🎰 اللفة المجانية",
        "📞 تواصل معنا", "📈 العروض والبونصات الحالية",
        "📜 السجل", "☁️ الشروحات",
        "📱 ichancy apk", "📌 الشروط والأحكام",
        "🛠️ لوحة تحكم الأدمن",
    }

    if context.user_data and text in KNOWN_MENU_TEXTS:
        context.user_data.clear()

    # ======================================================
    # 🚧 رسائل الخدمة: حجب القسم إذا فعّل الأدمن رسالة له
    # (يجب أن يسبق كل معالجات الأقسام؛ الأدمن غير خاضعين للحجب)
    # ======================================================
    gated_section = SERVICE_BUTTONS.get(text) or _pending_service_section(context.user_data)
    if gated_section and await service_blocked(update.message.reply_text, user_id, gated_section):
        context.user_data.clear()
        return

    # ======================================================
    # 💸 خطوات تحويل Sham Cash (إدخال عنوان/مبلغ/ملاحظة/رمز) — للأدمن فقط
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("shtx"):
        if await shtx_handle_text(update, context, text):
            return

    # ======================================================
    # 📣✉️ الرسالة الجماعية / الفردية — للأدمن فقط
    # ======================================================
    if user_id in ADMIN_IDS and (context.user_data.get("admin_bc_step") or context.user_data.get("admin_dm_step") or context.user_data.get("admin_report_step") or context.user_data.get("admin_pl_step") or context.user_data.get("admin_shcode_step") or context.user_data.get("admin_wdfee_step") or context.user_data.get("admin_sy_step")):
        if await handle_admin_message_flows(update, context, text):
            return

    # ======================================================
    # ✅ التعديل: استقبال المبلغ الجديد من الأدمن
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_set_rate"):

        if not text.isdigit():
            await update.message.reply_text(
                "❌ السعر يجب أن يكون رقماً فقط.\n\n"
                "مثال:\n"
                "147"
            )
            return

        new_rate = int(text)

        if not 1 <= new_rate <= MAX_RATE_NEW_SYP:
            await update.message.reply_text(
                f"❌ سعر الصرف يجب أن يكون بين 1 و {MAX_RATE_NEW_SYP:,} (بالليرة السورية الجديدة)."
            )
            return

        # حفظ السعر الجديد في قاعدة البيانات
        set_usdt_rate(new_rate)

        # حذف حالة تعديل السعر
        context.user_data.pop("admin_set_rate", None)

        await update.message.reply_text(
            f"✅ تم تحديث سعر الصرف بنجاح!\n\n"
            f"💱 السعر الجديد:\n"
            f"1 USDT = {new_rate:,} ل.س",
            reply_markup=main_menu(user_id)
        )

        return

    # ======================================================
    # 📢 حفظ نص رسالة خدمة جديدة
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_notice_edit"):

        notice_section = context.user_data["admin_notice_edit"]

        try:
            set_notice_text(notice_section, text)
        except ValueError as exc:
            await update.message.reply_text(f"❌ {exc}")
            return

        context.user_data.pop("admin_notice_edit", None)

        await update.message.reply_text("✅ تم حفظ الرسالة.", reply_markup=main_menu(user_id))
        view_text, view_markup = _notice_section_view(notice_section)
        await update.message.reply_text(view_text, reply_markup=view_markup)
        return

    # ======================================================
    # 💵 تعديل سعر دولار Sham Cash
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_set_usd_rate"):

        cleaned = text.strip().translate(_ARABIC_DIGITS).replace(",", "").replace("،", "")

        if not cleaned.isdigit():
            await update.message.reply_text(
                "❌ السعر يجب أن يكون رقماً صحيحاً فقط.\n\n"
                "مثال:\n"
                "147"
            )
            return

        new_usd_rate = int(cleaned)

        if not 1 <= new_usd_rate <= MAX_RATE_NEW_SYP:
            await update.message.reply_text(
                f"❌ سعر الصرف يجب أن يكون بين 1 و {MAX_RATE_NEW_SYP:,} (بالليرة السورية الجديدة)."
            )
            return

        set_shamcash_usd_rate(new_usd_rate)
        context.user_data.pop("admin_set_usd_rate", None)

        await update.message.reply_text(
            f"✅ تم تحديث سعر دولار Sham Cash بنجاح!\n\n"
            f"💵 السعر الجديد:\n"
            f"1$ = {new_usd_rate:,} ل.س",
            reply_markup=main_menu(user_id)
        )

        return
    

# حذف حساب مستخدم عن طريق الـ ID
    if context.user_data.get("admin_delete_user"):
            target_id = parse_admin_int(text)
            if not target_id:
                await update.message.reply_text("❌ يرجى إدخال ID صحيح (أرقام فقط):")
                return

            # التأكد من وجود المستخدم أولاً
            target_user = get_user(target_id)
            if not target_user:
                await update.message.reply_text(
                    f"⚠️ لم يتم العثور على مستخدم بالمعرّف `{target_id}` في البوت.",
                    parse_mode="Markdown"
                )
                context.user_data.clear()
                return

            # تنفيذ الحذف باستخدام الدالة المعرفة بقاعدة البيانات
            try:
                delete_user(target_id)
                context.user_data.clear()

                await update.message.reply_text(
                    f"✅ تم حذف الحساب صاحب المعرّف `{target_id}` وجميع سجلاته المرتبطة بنجاح من قاعدة البيانات.",
                    parse_mode="Markdown",
                    reply_markup=main_menu(user_id)
                )

            except Exception as e:
                context.user_data.clear()
                await update.message.reply_text(
                    f"❌ حدث خطأ أثناء تنفيذ الحذف: `{e}`",
                    parse_mode="Markdown",
                    reply_markup=main_menu(user_id)
                )

            return
    if context.user_data.get("admin_step") == "set_welcome_bonus":
        if not text.isdigit():
            await update.message.reply_text("❌ يرجى إدخال رقم صحيح:")
            return

        new_amount = int(text)
        enabled, _ = get_welcome_bonus_config()
        set_welcome_bonus_config(enabled, new_amount)
        context.user_data.pop("admin_step", None)

        await update.message.reply_text(f"✅ تم تغيير قيمة البونص الترحيبي إلى: {new_amount:,} {CURRENCY_LABEL}")
        return
    # ======================================================
    # 🎁 تعديل نسبة بونص إحدى طرق الشحن
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_set_bonus_key"):

        bonus_key = context.user_data["admin_set_bonus_key"]

        try:
            new_percent = float(text.replace("%", "").strip())
        except ValueError:
            await update.message.reply_text(
                "❌ أرسل رقماً فقط (مثال: 8 لتعني 8%)."
            )
            return

        if new_percent < 0 or new_percent > 100:
            await update.message.reply_text(
                "❌ النسبة يجب أن تكون بين 0 و 100."
            )
            return

        set_bonus_percent(bonus_key, new_percent / 100)
        context.user_data.pop("admin_set_bonus_key", None)

        await update.message.reply_text(
            f"✅ تم تحديث نسبة البونص بنجاح!\n\n"
            f"{BONUS_METHOD_LABELS.get(bonus_key, bonus_key)}\n"
            f"🎁 النسبة الجديدة: {new_percent:.1f}%",
            reply_markup=main_menu(user_id)
        )

        return
    


    # ======================================================
    # 🎁 تعديل الحد الأدنى لمبلغ الإهداء
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_gift_limit_input"):

        value = parse_admin_int(text)

        if value is None or value > GIFT_MIN_AMOUNT_CEILING:
            await update.message.reply_text(f"❌ أرسل عدداً صحيحاً بين 0 و {GIFT_MIN_AMOUNT_CEILING:,}.")
            return

        set_gift_min_amount(value)
        context.user_data.pop("admin_gift_limit_input", None)

        await update.message.reply_text(
            "✅ ألغيت الحد الأدنى: يمكن إهداء أي مبلغ." if value == 0
            else f"✅ أصبح الحد الأدنى لمبلغ الإهداء: {value:,} {CURRENCY_LABEL}.",
            reply_markup=main_menu(user_id)
        )

        return

    # ======================================================
    # 👤 تغيير نظام تعويض مستخدم (بحث برقم المستخدم)
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_cashback_user_lookup"):

        target_id = parse_admin_int(text)

        if target_id is None or target_id <= 0:
            await update.message.reply_text("❌ أرسل رقم المستخدم (أرقام فقط).")
            return

        context.user_data.pop("admin_cashback_user_lookup", None)
        await _send_admin_cashback_user_card(update.message, target_id)

        return

    # ======================================================
    # 💸 تعديل نسبة الاسترداد الأسبوعي (Cashback)
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_set_cashback_percent"):

        new_percent = parse_admin_number(text)

        if new_percent is None:
            await update.message.reply_text(
                "❌ أرسل رقماً فقط (مثال: 10 لتعني 10%)."
            )
            return

        if new_percent < 0 or new_percent > 100:
            await update.message.reply_text(
                "❌ النسبة يجب أن تكون بين 0 و 100."
            )
            return

        set_cashback_percent(new_percent / 100)
        context.user_data.pop("admin_set_cashback_percent", None)

        status = "✅ تم تفعيل الاسترداد الأسبوعي" if new_percent > 0 else "⏸️ تم إيقاف الاسترداد الأسبوعي"

        await update.message.reply_text(
            f"{status}\n\n"
            f"💸 النسبة الجديدة: {new_percent:.1f}%",
            reply_markup=main_menu(user_id)
        )

        return

    # ======================================================
    # 🌅 تعديل نسبة التعويض اليومي
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_set_daily_cashback_percent"):

        new_percent = parse_admin_number(text)

        if new_percent is None:
            await update.message.reply_text(
                "❌ أرسل رقماً فقط (مثال: 100 لتعني 100%)."
            )
            return

        if new_percent > 100:
            await update.message.reply_text(
                "❌ النسبة يجب أن تكون بين 0 و 100."
            )
            return

        set_daily_cashback_percent(new_percent / 100)
        context.user_data.pop("admin_set_daily_cashback_percent", None)

        status = "✅ تم ضبط نسبة التعويض اليومي" if new_percent > 0 else "⏸️ تم إيقاف الصرف اليومي (النسبة 0%)"

        await update.message.reply_text(
            f"{status}\n\n"
            f"🌅 النسبة الجديدة: {new_percent:.1f}%",
            reply_markup=main_menu(user_id)
        )

        return

    # ======================================================
    # ⏳ مدة التعويض الأسبوعي (بالأسابيع)
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_set_weekly_weeks"):

        weeks = parse_admin_int(text)

        if weeks is None or weeks > MAX_WEEKLY_DURATION_WEEKS:
            await update.message.reply_text(
                f"❌ أرسل عدداً صحيحاً من الأسابيع بين 0 و {MAX_WEEKLY_DURATION_WEEKS} (0 = بدون حد)."
            )
            return

        if weeks > 0 and get_cashback_program_enabled("weekly"):
            started_at = ensure_program_started_at("weekly")
            end_at = weekly_program_end(started_at, weeks)

            if end_at is not None and end_at <= datetime.utcnow().strftime(UTC_TS_FORMAT):
                elapsed = (datetime.utcnow() - datetime.strptime(started_at, UTC_TS_FORMAT)).days
                await update.message.reply_text(
                    f"❌ التعويض الأسبوعي شغّال منذ {ar_days(elapsed)}، و{ar_weeks(weeks)} أقصر من المدة المنقضية.\n"
                    "اختر مدة أكبر، أو 0 لإلغاء الحد."
                )
                return

        set_weekly_duration_weeks(weeks)
        context.user_data.pop("admin_set_weekly_weeks", None)

        summary = ar_weeks(weeks) if weeks > 0 else "بدون حد"
        status = _program_status_text("weekly") if get_cashback_program_enabled("weekly") else ""

        await update.message.reply_text(
            f"✅ تم ضبط مدة التعويض الأسبوعي: {summary}"
            + (f"\n\n{status}" if status else "")
            + ("\n\nℹ️ ستُطبَّق المدة عند تفعيل الأسبوعي." if not get_cashback_program_enabled("weekly") else ""),
            reply_markup=main_menu(user_id)
        )

        return

    # ======================================================
    # ⏳ مدة التعويض اليومي (بالأيام)
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_set_daily_days"):

        days = parse_admin_int(text)

        if days is None or days > MAX_DAILY_DURATION_DAYS:
            await update.message.reply_text(
                f"❌ أرسل عدداً صحيحاً من الأيام بين 0 و {MAX_DAILY_DURATION_DAYS} (0 = بدون حد)."
            )
            return

        if days > 0 and get_cashback_program_enabled("daily"):
            start_day = _utc_to_syria_day(ensure_program_started_at("daily"))
            last_target = _shift_day(start_day, days) if start_day else None
            yesterday = _shift_day(syria_now().strftime("%Y-%m-%d"), -1)

            if last_target is not None and last_target < yesterday:
                await update.message.reply_text(
                    f"❌ التعويض اليومي بدأ بتاريخ {start_day}، و{ar_days(days)} أقصر من المدة المنقضية.\n"
                    "اختر مدة أكبر، أو 0 لإلغاء الحد."
                )
                return

        set_daily_duration_days(days)
        context.user_data.pop("admin_set_daily_days", None)

        summary = ar_days(days) if days > 0 else "بدون حد"
        status = _program_status_text("daily") if get_cashback_program_enabled("daily") else ""

        await update.message.reply_text(
            f"✅ تم ضبط مدة التعويض اليومي: {summary}"
            + (f"\n\n{status}" if status else "")
            + ("\n\nℹ️ ستُطبَّق المدة عند تفعيل اليومي." if not get_cashback_program_enabled("daily") else ""),
            reply_markup=main_menu(user_id)
        )

        return

    # ======================================================
    # 🎯 تعديل نسبة عمولة الإحالة
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_set_referral_percent"):

        try:
            new_percent = float(text.replace("%", "").strip())
        except ValueError:
            await update.message.reply_text(
                "❌ أرسل رقماً فقط (مثال: 5 لتعني 5%)."
            )
            return

        if new_percent < 0 or new_percent > 100:
            await update.message.reply_text(
                "❌ النسبة يجب أن تكون بين 0 و 100."
            )
            return

        set_referral_commission_percent(new_percent / 100)
        context.user_data.pop("admin_set_referral_percent", None)

        status = "✅ تم تفعيل عمولة الإحالة" if new_percent > 0 else "⏸️ تم إيقاف عمولة الإحالة"

        await update.message.reply_text(
            f"{status}\n\n"
            f"🎯 النسبة الجديدة: {new_percent:.1f}%",
            reply_markup=main_menu(user_id)
        )

        return

    # ======================================================
    # ⏱️ تعديل مدة دورة أرباح الإحالة
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_set_referral_period"):

        if not text.strip().isdigit():
            await update.message.reply_text(
                "❌ أرسل رقماً صحيحاً فقط (عدد الأيام، مثال: 10)."
            )
            return

        new_days = int(text.strip())

        if new_days <= 0 or new_days > 365:
            await update.message.reply_text(
                "❌ عدد الأيام يجب أن يكون بين 1 و 365."
            )
            return

        set_referral_period_days(new_days)
        context.user_data.pop("admin_set_referral_period", None)

        await update.message.reply_text(
            f"✅ تم تحديث مدة دورة الأرباح بنجاح!\n\n"
            f"⏱️ المدة الجديدة: {new_days} يوم/أيام",
            reply_markup=main_menu(user_id)
        )

        return

    # ======================================================
    # ⏱️ تعديل مدة الانتظار بين لفات عجلة الحظ
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_set_spin_cooldown"):

        if not text.strip().isdigit():
            await update.message.reply_text(
                "❌ أرسل رقماً صحيحاً فقط (عدد الساعات، مثال: 24)."
            )
            return

        hours = int(text.strip())

        if hours < SPIN_MIN_COOLDOWN_HOURS or hours > 720:
            await update.message.reply_text(
                f"❌ عدد الساعات يجب أن يكون بين {SPIN_MIN_COOLDOWN_HOURS} و 720."
            )
            return

        set_spin_cooldown_hours(hours)
        context.user_data.pop("admin_set_spin_cooldown", None)

        await update.message.reply_text(
            f"✅ تم تحديث مدة الانتظار بنجاح!\n\n"
            f"⏱️ المدة الجديدة: {hours} ساعة",
            reply_markup=main_menu(user_id)
        )

        return

    # ======================================================
    # ➕ إضافة جائزة جديدة لعجلة الحظ — المرحلة 1: النص
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_spin_add_step") == "label":

        label = text.strip()

        if not label:
            await update.message.reply_text("❌ الرجاء إرسال نص صحيح.")
            return

        context.user_data["admin_spin_new_label"] = label
        context.user_data["admin_spin_add_step"] = "type"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 مبلغ نقدي", callback_data="admin_spin_type_cash")],
            [InlineKeyboardButton("📈 زيادة على الشحن (%)", callback_data="admin_spin_type_recharge_bonus")],
            [InlineKeyboardButton("🌅 تعويض يومي (%)", callback_data="admin_spin_type_daily_cashback")],
            [InlineKeyboardButton("🔄 لفة إضافية مجانية", callback_data="admin_spin_type_respin")],
            [InlineKeyboardButton("😔 بلا جائزة (حظ أوفر)", callback_data="admin_spin_type_none")],
        ])

        await update.message.reply_text("2️⃣ اختر نوع الجائزة:", reply_markup=keyboard)
        return

    # ======================================================
    # ➕ إضافة جائزة جديدة لعجلة الحظ — المرحلة 2: المبلغ (نقدي فقط)
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_spin_add_step") == "amount":

        if not text.strip().isdigit():
            await update.message.reply_text("❌ أرسل رقماً صحيحاً فقط.")
            return

        new_amount = int(text.strip())

        if context.user_data.get("admin_spin_new_type") in SPIN_PERCENT_PRIZE_TYPES and not (1 <= new_amount <= 100):
            await update.message.reply_text("❌ النسبة يجب أن تكون بين 1 و 100.")
            return

        context.user_data["admin_spin_new_amount"] = new_amount
        context.user_data["admin_spin_add_step"] = "weight"

        await update.message.reply_text(
            "4️⃣ أرسل وزن الجائزة (رقم صحيح — كلما زاد الرقم زادت فرصة الربح بها، مثال: 10):",
            reply_markup=cancel_keyboard()
        )
        return

    # ======================================================
    # ➕ إضافة جائزة جديدة لعجلة الحظ — المرحلة 3: الوزن (الحفظ)
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_spin_add_step") == "weight":

        if not text.strip().isdigit() or int(text.strip()) <= 0:
            await update.message.reply_text("❌ أرسل رقماً صحيحاً أكبر من صفر.")
            return

        weight = int(text.strip())
        label = context.user_data.get("admin_spin_new_label", "جائزة")
        prize_type = context.user_data.get("admin_spin_new_type", "none")
        amount = context.user_data.get("admin_spin_new_amount", 0)

        add_spin_prize(label, prize_type, amount, weight)

        context.user_data.pop("admin_spin_add_step", None)
        context.user_data.pop("admin_spin_new_label", None)
        context.user_data.pop("admin_spin_new_type", None)
        context.user_data.pop("admin_spin_new_amount", None)

        await update.message.reply_text(
            f"✅ تمت إضافة الجائزة بنجاح!\n\n🎁 {label}\n⚖️ الوزن: {weight}",
            reply_markup=main_menu(user_id)
        )
        return

    # ======================================================
    # ⚖️ تعديل وزن جائزة موجودة على عجلة الحظ
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_spin_edit_weight_id"):

        if not text.strip().isdigit() or int(text.strip()) <= 0:
            await update.message.reply_text("❌ أرسل رقماً صحيحاً أكبر من صفر.")
            return

        prize_id = context.user_data["admin_spin_edit_weight_id"]
        new_weight = int(text.strip())

        update_spin_prize_weight(prize_id, new_weight)
        context.user_data.pop("admin_spin_edit_weight_id", None)

        await update.message.reply_text(
            f"✅ تم تحديث الوزن بنجاح!\n\n⚖️ الوزن الجديد: {new_weight}",
            reply_markup=main_menu(user_id)
        )
        return

# ======================================================
# 🎁 إنشاء أكواد هدايا - المرحلة الأولى: قيمة الهدية
# ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("gift_admin_step") == "amount":

        if not text.isdigit():
            await update.message.reply_text(
                "❌ قيمة الهدية يجب أن تكون رقماً فقط.\n\n"
                "مثال:\n"
                "5000"
            )
            return

        amount = int(text)

        if amount <= 0:
            await update.message.reply_text(
                "❌ قيمة الهدية يجب أن تكون أكبر من صفر."
            )
            return

    # حفظ قيمة الهدية
        context.user_data["gift_amount"] = amount

    # الانتقال لعدد الأكواد
        context.user_data["gift_admin_step"] = "count"

        await update.message.reply_text(
            f"🎁 قيمة كل كود: {amount:,} {CURRENCY_LABEL}\n\n"
            "🔢 الآن أرسل عدد الأكواد التي تريد إنشاءها:\n\n"
            "مثال:\n"
            "1 = كود واحد\n"
            "10 = عشرة أكواد\n"
            "20 = عشرون كوداً\n"
            "100 = مئة كود"
        )

        return


# ======================================================
# 🎁 إنشاء أكواد هدايا - المرحلة الثانية: عدد الأكواد
# ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("gift_admin_step") == "count":

        if not text.isdigit():
            await update.message.reply_text(
                "❌ عدد الأكواد يجب أن يكون رقماً فقط."
            )
            return

        number_of_codes = int(text)

        if number_of_codes <= 0:
            await update.message.reply_text(
                "❌ عدد الأكواد يجب أن يكون أكبر من صفر."
            )
            return

    # حماية من إنشاء عدد ضخم بالخطأ
        if number_of_codes > 1000:
            await update.message.reply_text(
                "❌ الحد الأقصى لإنشاء الأكواد دفعة واحدة هو 1000 كود."
            )
            return

        context.user_data["gift_number_of_codes"] = number_of_codes
        context.user_data["gift_admin_step"] = "uses"

        amount = context.user_data.get("gift_amount")

        await update.message.reply_text(
            f"🎁 قيمة كل كود: {amount:,} {CURRENCY_LABEL}\n"
            f"🔢 عدد الأكواد: {number_of_codes}\n\n"
            "🔄 الآن أرسل عدد مرات استخدام كل كود:\n\n"
            "مثال:\n"
            "1 = كل كود يستخدم مرة واحدة\n"
            "5 = كل كود يستخدمه 5 أشخاص\n"
            "10 = كل كود يستخدمه 10 أشخاص"
        )

        return


# ======================================================
# 🎁 إنشاء أكواد هدايا - المرحلة الثالثة: عدد الاستخدامات
# ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("gift_admin_step") == "uses":

        if not text.isdigit():
            await update.message.reply_text(
                "❌ عدد الاستخدامات يجب أن يكون رقماً فقط."
            )
            return

        max_uses = int(text)

        if max_uses <= 0:
            await update.message.reply_text(
                "❌ عدد الاستخدامات يجب أن يكون أكبر من صفر."
            )
            return

        amount = context.user_data.get("gift_amount")
        number_of_codes = context.user_data.get("gift_number_of_codes")

    # التأكد من وجود البيانات المطلوبة
        if not amount or not number_of_codes:
            context.user_data.pop("gift_admin_step", None)
            context.user_data.pop("gift_amount", None)
            context.user_data.pop("gift_number_of_codes", None)

            await update.message.reply_text(
                "❌ حدث خطأ في بيانات إنشاء الأكواد.\n"
                "يرجى بدء العملية من جديد.",
                reply_markup=main_menu(user_id)
            )
            return

    # ==================================================
    # إنشاء الأكواد
    # ==================================================

        generated_codes = []

        for _ in range(number_of_codes):

        # نحاول إنشاء كود فريد
            created = False

            for attempt in range(20):

                code = generate_gift_code()

                created = create_gift_code(
                    code=code,
                    amount=amount,
                    max_uses=max_uses,
                    created_by=user_id
                )

                if created:
                    generated_codes.append(code)
                    break

        # إذا فشل إنشاء هذا الكود بعد عدة محاولات
            if not created:
                break

    # ==================================================
    # تنظيف بيانات العملية
    # ==================================================

        context.user_data.pop("gift_admin_step", None)
        context.user_data.pop("gift_amount", None)
        context.user_data.pop("gift_number_of_codes", None)

    # ==================================================
    # التحقق من نجاح العملية
    # ==================================================

        if len(generated_codes) != number_of_codes:

            await update.message.reply_text(
                "⚠️ تم إنشاء الأكواد جزئياً.\n\n"
                f"المطلوب: {number_of_codes}\n"
                f"تم إنشاء: {len(generated_codes)}\n\n"
                "يرجى مراجعة الأكواد التي تم إنشاؤها من لوحة الإدارة.",
                reply_markup=main_menu(user_id)
            )

            return

    # ==================================================
    # تجهيز قائمة الأكواد
    # ==================================================

        codes_text = "\n".join(
            f"{index}. `{code}`"
            for index, code in enumerate(generated_codes, start=1)
        )

    # ==================================================
    # إرسال النتيجة
    # ==================================================

        await update.message.reply_text(
            "🎉 تم إنشاء أكواد الهدايا بنجاح!\n\n"

            f"💰 قيمة كل كود: {amount:,} {CURRENCY_LABEL}\n"
            f"🔢 عدد الأكواد: {number_of_codes}\n"
            f"👥 استخدامات كل كود: {max_uses}\n\n"

            "🎁 الأكواد:\n\n"

            f"{codes_text}\n\n"

            "📌 يمكنك إرسال الأكواد للزبائن.",
            parse_mode="Markdown",
            reply_markup=main_menu(user_id)
        )

        return
        # ======================================================
    # تعديل مبلغ الشحن من الأدمن
    # ======================================================
    if user_id in ADMIN_IDS and context.user_data.get("admin_edit_step") == "new_amount":

        if not text.isdigit():
            await update.message.reply_text(
                "❌ أرسل رقم فقط، حاول مجدداً:"
            )
            return

        new_amount = int(text)

        if new_amount <= 0:
            await update.message.reply_text(
                "❌ المبلغ يجب أن يكون أكبر من صفر."
            )
            return

        # جلب بيانات عملية التعديل
        target_user_id = context.user_data.get("admin_edit_target_user")
        original_amount = context.user_data.get("admin_edit_original_amount")

        # حماية من فقدان البيانات
        if target_user_id is None or original_amount is None:
            context.user_data.pop("admin_edit_step", None)
            context.user_data.pop("admin_edit_target_user", None)
            context.user_data.pop("admin_edit_original_amount", None)

            await update.message.reply_text(
                "❌ حدث خطأ: لم يتم العثور على بيانات عملية الشحن.\n"
                "يرجى إعادة العملية من البداية.",
                reply_markup=main_menu(user_id)
            )
            return

        target_user_id = int(target_user_id)
        original_amount = int(original_amount)
        edit_recharge_id = context.user_data.get("admin_edit_recharge_id")

        def _clear_edit_state():
            context.user_data.pop("admin_edit_step", None)
            context.user_data.pop("admin_edit_target_user", None)
            context.user_data.pop("admin_edit_original_amount", None)
            context.user_data.pop("admin_edit_recharge_id", None)

        # ==========================================
        # الطلب لازم يكون لسا معلّق (حماية من التكرار: أدمن ثاني/ضغطة سابقة)
        # ==========================================
        pending = get_pending_recharge(target_user_id, original_amount, edit_recharge_id)
        if pending is None:
            _clear_edit_state()
            await update.message.reply_text(
                _ALREADY_PROCESSED_TEXT,
                reply_markup=main_menu(user_id)
            )
            return

        # ==========================================
        # طريقة الدفع وحساب البونص (من الطلب نفسه)
        # ==========================================
        method = pending["method"]
        bonus_key = _bonus_key_for_method(method)
        bonus_percent = get_bonus_percent(bonus_key) if bonus_key else 0.0

        # ==========================================
        # اعتماد الطلب + إضافة الرصيد (ذرّي، مرة واحدة فقط)
        # ==========================================
        result = approve_pending_recharge(
            pending["id"],
            credit_amount=new_amount,
            bonus_percent=bonus_percent
        )

        if not result["ok"]:
            _clear_edit_state()
            await update.message.reply_text(
                _ALREADY_PROCESSED_TEXT,
                reply_markup=main_menu(user_id)
            )
            return

        bonus = result["bonus"]
        final_amount = result["total"]

        try:
            _txid = pending["transaction_id"]
        except Exception:
            _txid = "-"
        _dl = await _tg_user_label(context.bot, target_user_id)
        _src = context.user_data.get("admin_edit_msg")
        _done_in_channel = False
        if _src and DEPOSITS_CHANNEL_ID and _src[0] == DEPOSITS_CHANNEL_ID:
            try:
                await context.bot.edit_message_text(
                    chat_id=_src[0], message_id=_src[1],
                    text=_src[2]
                    + f"\n\n✅ تمت الموافقة بمبلغ معدّل (الأدمن {update.effective_user.id})\n"
                    f"💰 المبلغ الأصلي: {original_amount:,} {CURRENCY_LABEL}\n"
                    f"✏️ المبلغ المعدّل: {new_amount:,} {CURRENCY_LABEL}\n"
                    f"🎁 البونص: {bonus:,} {CURRENCY_LABEL}\n"
                    f"💵 المجموع المضاف للزبون: {final_amount:,} {CURRENCY_LABEL}"
                )
                _done_in_channel = True
            except Exception:
                logging.getLogger("deposit_log").exception("could not edit channel message")
        if not _done_in_channel:
            await send_deposit_log(
                context.bot,
                _recharge_log_text("موافقة أدمن بمبلغ معدّل", _dl, target_user_id, method, new_amount,
                                   bonus, final_amount, _txid, admin_id=update.effective_user.id,
                                   original=original_amount)
            )

        # ==========================================
        # تفعيل الإحالة (حالة نشطة) + تسجيل عمولة الإحالة المستمرة
        # ==========================================
        activate_referral_after_recharge(
            referred_user_id=target_user_id,
            reward_amount=0
        )

        referrer_id = get_referrer(target_user_id)
        if referrer_id:
            commission_percent = get_referral_commission_percent()
            if commission_percent > 0:
                commission_amount = int(final_amount * commission_percent)
                if commission_amount > 0:
                    log_referral_commission(referrer_id, target_user_id, final_amount, commission_amount)

        # ==========================================
        # تجهيز رسالة البونص
        # ==========================================
        bonus_text = (
            f"\n🎁 بونص: +{bonus:,} {CURRENCY_LABEL}"
            if bonus > 0
            else ""
        )
        if result.get("coupon_percent"):
            bonus_text += f"\n🎟️ يشمل كوبون عجلة الحظ: +{result['coupon_percent']}%"

        # ==========================================
        # رسالة للأدمن
        # ==========================================
        await update.message.reply_text(
            f"✅ تم شحن الرصيد بالمبلغ المعدّل\n\n"
            f"👤 المستخدم: {target_user_id}\n"
            f"💰 المبلغ الأصلي: "
            f"{original_amount:,} {CURRENCY_LABEL}\n"
            f"✏️ المبلغ المعدّل: "
            f"{new_amount:,} {CURRENCY_LABEL}"
            f"{bonus_text}\n"
            f"💵 المجموع المضاف للمستخدم: "
            f"{final_amount:,} {CURRENCY_LABEL}",
            reply_markup=main_menu(user_id)
        )

        # ==========================================
        # رسالة للمستخدم
        # ==========================================
        bonus_line = (
            f"🎁 بونص: +{bonus:,} {CURRENCY_LABEL}\n"
            if bonus > 0
            else ""
        )

        try:
            await context.bot.send_message(
                target_user_id,
                f"🎉 تم شحن رصيد البوت بنجاح!\n\n"
                f"💰 المبلغ المضاف: "
                f"{new_amount:,} {CURRENCY_LABEL}\n"
                f"{bonus_line}"
                f"💵 الإجمالي المضاف لرصيدك: "
                f"{final_amount:,} {CURRENCY_LABEL}\n\n"
                f"⚡ يمكنك الآن شحن حساب iChancy من رصيد البوت"
            )
        except Exception:
            pass

        # ==========================================
        # تنظيف حالة التعديل بعد انتهاء العملية
        # ==========================================
        _clear_edit_state()

        return

    # ============================
    # زر لوحة تحكم الأدمن
    # ============================
    if text == "🛠️ لوحة تحكم الأدمن":
        if user_id not in ADMIN_IDS:
            return

        rate = get_usdt_rate()
        usd_rate = get_shamcash_usd_rate()
        notice_flag = " 🔴" if any_service_notice_active() else ""

        sham_mark = await _sham_code_mark()
        keyboard = _admin_panel_keyboard(rate, usd_rate, notice_flag, sham_mark)

        await update.message.reply_text(
            "🛠️ لوحة تحكم الأدمن",
            reply_markup=keyboard
        )

        return
    # ============================
    # شحن iChancy من رصيد البوت
    # ============================
    if text == "🔼 شحن حساب الأيشانسي":

        context.user_data["ichancy_charge"] = True

        user = get_user(user_id)
        balance = user["balance"]

        keyboard = [
           [
               InlineKeyboardButton(
                    "💰 شحن كامل الرصيد",
                    callback_data="ichancy_charge_all"
                )
            ],
            [
                InlineKeyboardButton(
                    "✏️ إدخال مبلغ محدد",
                    callback_data="ichancy_charge_custom"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ إلغاء",
                    callback_data="cancel_ichancy_charge"
                )
            ]
        ]

        await update.message.reply_text(
            f"💰 اختر طريقة الشحن:\n\n"
            f"رصيدك الحالي: {balance:,} {CURRENCY_LABEL}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

    elif context.user_data.get("ichancy_charge"):
        if not text.isdigit():
            await update.message.reply_text("❌ أرسل رقم فقط", reply_markup=cancel_keyboard())
            return

        amount = int(text)
        context.user_data["ichancy_charge"] = None
        user = get_user(user_id)

        if amount < ICHANCY_CHARGE_MIN:
            await update.message.reply_text(
                f"❌ أقل مبلغ للشحن هو {ICHANCY_CHARGE_MIN:,} {CURRENCY_LABEL}",
                reply_markup=main_menu(user_id)
            )
            return

        if amount > user["balance"]:
            await update.message.reply_text("❌ رصيدك داخل البوت غير كافي", reply_markup=main_menu(user_id))
            return

        ichancy_username = user.get("ichancy_username")
        if not ichancy_username:
            await update.message.reply_text("❌ لا يوجد حساب Ichancy مربوط\nأنشئ حساباً أولاً من القائمة", reply_markup=main_menu(user_id))
            return

        player = get_player_by_username(ichancy_username)
        if not player:
            await update.message.reply_text("❌ فشل الوصول لحساب Ichancy، تواصل مع الدعم", reply_markup=main_menu(user_id))
            return

        await update.message.reply_text("⏳ جاري شحن حسابك...", reply_markup=main_menu(user_id))

        nps_amount = ls_to_nps(amount)
        success = deposit_to_player(player.get("playerId"), nps_amount, comment="Charge from bot wallet")

        if success:
            update_balance(user_id, -amount)
            log_ichancy_transaction(user_id, "in", nps_amount)
            await update.message.reply_text(
                f"✅ تم شحن حسابك في Ichancy بنجاح!\n"
                f"💰 المبلغ المشحون: {amount:,} {CURRENCY_LABEL}\n"
                f"💳 رصيد البوت المتبقي: {user['balance'] - amount:,} {CURRENCY_LABEL}"
            )
        else:
            await update.message.reply_text("❌ فشل شحن حسابك في Ichancy، حاول لاحقاً أو تواصل مع الدعم.\nلم يتم خصم أي رصيد من حسابك.", reply_markup=main_menu(user_id))
        return

    # ============================
    # سحب من iChancy إلى رصيد البوت
    # ============================
    if text == "🔽 سحب من الأيشانسي":

        context.user_data["ichancy_withdraw"] = True

        keyboard = [
            [
                InlineKeyboardButton(
                    "📤 سحب كامل الرصيد",
                    callback_data="ichancy_withdraw_all"
                )
            ],
            [
                InlineKeyboardButton(
                    "✏️ إدخال مبلغ محدد",
                    callback_data="ichancy_withdraw_custom"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ إلغاء",
                    callback_data="cancel_ichancy_withdraw"
                )
            ]
        ]

        await update.message.reply_text( 
                "📤 اختر طريقة السحب:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        return


    elif context.user_data.get("ichancy_withdraw"):

        if not text.isdigit():
            await update.message.reply_text(
                "❌ أرسل رقم فقط",
                reply_markup=cancel_keyboard()
            )
            return

        amount = int(text)

        if amount <= 0:
            await update.message.reply_text(
                "❌ يجب أن يكون المبلغ أكبر من صفر",
                reply_markup=cancel_keyboard()
            )
            return

        context.user_data["ichancy_withdraw"] = None

        user = get_user(user_id)
        ichancy_username = user.get("ichancy_username")

        if not ichancy_username:
            await update.message.reply_text(
                "❌ لا يوجد حساب Ichancy مربوط",
                reply_markup=main_menu(user_id)
            )
            return

        player = get_player_by_username(ichancy_username)

        if not player:
            await update.message.reply_text(
                "❌ فشل الوصول لحساب Ichancy",
                reply_markup=main_menu(user_id)
            )
            return

        player_id = player.get("playerId")

    # التحقق من رصيد Ichancy قبل تنفيذ السحب
        current_balance_nps = get_player_balance(player_id)

        if current_balance_nps is None:
            await update.message.reply_text(
                "❌ تعذر معرفة رصيد حساب Ichancy.",
                reply_markup=main_menu(user_id)
            )
            return

        requested_nps = ls_to_nps(amount)

        if requested_nps > current_balance_nps:
            await update.message.reply_text(
                f"❌ رصيدك في Ichancy غير كافٍ.\n\n"
                f"💰 رصيدك الحالي: {current_balance_nps} NPS",
                reply_markup=main_menu(user_id)
            )
            return

        await update.message.reply_text(
            "⏳ جاري السحب من Ichancy...",
            reply_markup=main_menu(user_id)
        )

        api_success = withdraw_from_player(
            player_id,
            requested_nps,
            comment="Withdraw to bot wallet"
        )

        if not api_success:
            await update.message.reply_text(
                "❌ فشل السحب، تأكد من رصيدك على iChancy.",
                reply_markup=main_menu(user_id)
            )
            return

    # إضافة الرصيد للبوت فقط بعد نجاح API
        update_balance(user_id, amount)
        log_ichancy_transaction(user_id, "out", requested_nps)

    # جلب الرصيد الجديد من قاعدة البيانات
        updated_user = get_user(user_id)

        await update.message.reply_text(
            f"✅ تم السحب من iChancy بنجاح!\n\n"
            f"💰 المسحوب والمضاف لرصيد البوت: "
            f"{amount:,} {CURRENCY_LABEL}\n"
            f"💳 رصيدك الجديد: "
            f"{updated_user['balance']:,} {CURRENCY_LABEL}",
            reply_markup=main_menu(user_id)
        )

        return

    # ============================
    # بيانات واردة من واجهة العجلة (WebApp)
    # ============================
    # ملاحظة أمان: لا نثق بأي نتيجة/جائزة تُرسل من الواجهة (WebApp)،
    # لأن الطرف الأمامي (متصفح المستخدم) غير موثوق ويمكن التلاعب به.
    # القرار الفعلي للجائزة يُتخذ ويُطبَّق بالكامل من السيرفر لحظة
    # الضغط على زر "🆓 اللفة المجانية" (قبل حتى فتح الواجهة). لذلك
    # نتجاهل أي بيانات واردة من الواجهة هنا بأمان.
    if update.message.web_app_data:
        return

    # التحقق من الموافقة على الشروط
    user = get_user(user_id)
    if user.get("agreed") != 1:
        await update.message.reply_text("⚠️ يجب الموافقة على الشروط أولًا /start")
        return

    # اختيار نظام التعويض إلزامي مرة واحدة قبل استخدام البوت (الأدمن مستثنى) — فقط إذا فعّل الأدمن الميزة
    if _user_needs_cashback_choice(user_id):
        await send_cashback_choice_prompt(update.message)
        return

    # اختيار نظام الإحالات (هرمي/فردي) إلزامي مرة واحدة أيضاً (الأدمن مستثنى)
    if user_id not in ADMIN_IDS and get_user_referral_plan(user_id) is None:
        await send_referral_plan_prompt(update.message)
        return

    # ============================
    # إنشاء حساب Ichancy
    # ============================
    if context.user_data.get("ichancy_step") == "username":
        base_username = text.strip()

        if not base_username.endswith("@lacassa-bot1"):
            ichancy_username = f"{base_username}@lacassa-bot1"
        else:
            ichancy_username = base_username

        context.user_data["ichancy_step"] = None

        await update.message.reply_text("⏳ جاري إنشاء حسابك في iChancy...", reply_markup=main_menu(user_id))

        password = generate_password()
        success = register_player(ichancy_username, password)

        if success:
            update_ichancy_account(user_id, ichancy_username, password)

            # 1. تطبيق البونص الترحيبي عند النجاح في إنشاء الحساب
            bonus = apply_welcome_bonus(user_id)

            if bonus > 0:
                _wl = await _tg_user_label(context.bot, user_id, update.effective_user)
                await send_deposit_log(
                    context.bot,
                    "🎁 مكافأة إنشاء حساب\n\n"
                    f"انضاف رصيد مقداره {bonus:,} {CURRENCY_LABEL} للزبون صاحب الايدي {user_id} ({_wl})\n"
                    f"🎮 حساب iChancy: {ichancy_username}"
                )

            # 2. جلب الرصيد المحدث للمستخدم من قاعدة البيانات
            current_balance = get_user_balance(user_id)  # استخدم الدالة المعتمدة لديك لجلب الرصيد

            # 3. صياغة الرسالة الشاملة لمعلومات الحساب والبونص
            msg = (
                f"🎉 **تم إنشاء حساب iChancy بنجاح!**\n\n"
                f"👤 **Username:** `{ichancy_username}`\n"
                f"🔑 **Password:** `{password}`\n\n"
            )

            if bonus > 0:
                msg += f"🎁 **هدية ترحيبية:** تم إضافة **{bonus:,}** {CURRENCY_LABEL} إلى رصيدك!\n"

            msg += (
                f"💰 **تم منحك بونص ترحيبي يقيمة :** {current_balance:,} {CURRENCY_LABEL}\n\n"
                f"⚠️ احتفظ بالمعلومات جيدًا.\n"
                f"🌐 الموقع: https://www.ichancy100.com\n"
                f"🌐 الموقع: https://www.ichancy200.com"
            )

            await update.message.reply_text(
                msg,
                parse_mode="Markdown",
                reply_markup=main_menu(user_id)
            )

            user_info = update.effective_user
            user_display = f"@{user_info.username}" if user_info.username else user_info.first_name
            await notify_admins(
                context.bot,
                f"🧾 حساب Ichancy جديد تم إنشاؤه تلقائياً\n"
                f"👤 {user_display} | 🆔 {user_id}\n"
                f"🔑 {ichancy_username}\n"
                f"🎁 البونص الممنوح: {bonus:,} {CURRENCY_LABEL}"
            )
        else:
            await update.message.reply_text(
                f"❌ فشل إنشاء الحساب\n\n"
                f"السبب المحتمل: اسم المستخدم ({ichancy_username}) مكرر\n"
                f"جرب اسم مستخدم مختلف"
            )
        return

    # ============================
    # شحن USDT — المرحلة 1: المبلغ بالدولار
    # ============================
    if context.user_data.get("recharge_step") == "usdt_amount":
        try:
            usdt_amount = float(text.replace(",", "."))
            if usdt_amount <= 0:
                raise ValueError
        except:
            await update.message.reply_text("❌ أرسل مبلغاً صحيحاً بالدولار (مثال: 10 أو 10.5)", reply_markup=cancel_keyboard())
            return
        rate = get_usdt_rate()
        ls_amount = int(usdt_amount * rate)
        usdt_bonus_percent = get_bonus_percent("usdt")
        bonus = int(ls_amount * usdt_bonus_percent)
        total_ls = ls_amount + bonus
        context.user_data["recharge_usdt_amount"] = usdt_amount
        context.user_data["recharge_ls_amount"] = ls_amount
        context.user_data["recharge_step"] = "usdt_txid"
        await update.message.reply_text(
            f"✅ المبلغ: {usdt_amount} USDT\n"
            f"💱 سعر الصرف: 1 USDT = {rate:,} ل.س\n"
            f"💰 يعادل: {ls_amount:,} ل.س\n"
            f"🎁 بونص {usdt_bonus_percent * 100:.0f}%: +{bonus:,} ل.س\n"
            f"💵 الإجمالي الذي سيُضاف: {total_ls:,} ل.س\n\n"
            "📌 الآن أرسل هاش (Hash) عملية التحويل من محفظتك:",
            reply_markup=cancel_keyboard()
        )
        return

    # ============================
    # شحن USDT — المرحلة 2: هاش العملية
    # ============================
    if context.user_data.get("recharge_step") == "usdt_txid":
        txid = text.strip()

        # شكل الهاش: بدون فراغات وبطول معقول (يقبل TRC20/ERC20/BEP20 ومعرّفات التحويل الداخلي)
        if not _USDT_HASH_RE.fullmatch(txid):
            await update.message.reply_text(
                "❌ الهاش غير صالح. أرسل هاش العملية فقط (بدون فراغات أو نص إضافي).",
                reply_markup=cancel_keyboard()
            )
            return

        usdt_amount = context.user_data.get("recharge_usdt_amount", 0)
        ls_amount = context.user_data.get("recharge_ls_amount", 0)
        method = context.user_data.get("recharge_method", "USDT")
        rate = get_usdt_rate()
        context.user_data["recharge_step"] = None

        usdt_bonus_percent = get_bonus_percent("usdt")
        bonus = int(ls_amount * usdt_bonus_percent)
        total_ls = ls_amount + bonus

        # نحفظ المبلغ بالليرة في قاعدة البيانات — مرفوض إذا الهاش مستخدم من قبل (بأي كتابة)
        created = create_pending_recharge_once(user_id, ls_amount, txid, method)

        if not created["ok"]:
            context.user_data.clear()
            await update.message.reply_text(
                "❌ هذا الهاش مستخدم مسبقاً ولا يمكن استخدامه مرة ثانية.",
                reply_markup=main_menu(user_id)
            )
            return

        recharge_id = created["recharge_id"]

        await update.message.reply_text(
            f"✅ تم استلام طلب الشحن!\n\n"
            f"💵 المبلغ المرسل: {usdt_amount} USDT\n"
            f"💱 سعر الصرف: 1 USDT = {rate:,} ل.س\n"
            f"💰 يعادل: {ls_amount:,} ل.س\n"
            f"🎁 بونص {usdt_bonus_percent * 100:.0f}%: +{bonus:,} ل.س\n"
            f"💵 الإجمالي الذي سيُضاف: {total_ls:,} ل.س\n"
            f"🔢 هاش العملية: {txid}\n\n"
            f"⏳ بانتظار تأكيد الكاشيرة...",
            reply_markup=main_menu(user_id)
        )

        user_info = update.effective_user
        user = get_user(user_id)
        await notify_deposits(context.bot,
            f"📥 طلب شحن USDT\n\n"
            f"👤 الاسم: {user_info.first_name}\n"
            f"📛 اليوزر: @{user_info.username}\n"
            f"🆔 Telegram ID: {user_id}\n\n"
            f"💳 الطريقة: {method}\n"
            f"💵 المبلغ: {usdt_amount} USDT\n"
            f"💱 سعر الصرف: 1 USDT = {rate:,} ل.س\n"
            f"💰 يعادل: {ls_amount:,} ل.س\n"
            f"🎁 البونص ({usdt_bonus_percent * 100:.0f}%): +{bonus:,} ل.س\n"
            f"💵 الإجمالي بالليرة: {total_ls:,} ل.س\n"
            f"🔢 هاش التحويل: `{txid}`\n\n"
            f"🎮 حساب Ichancy: {user.get('ichancy_username')}\n\n"
            f"⚠️ تحقق من الهاش ثم اضغط تأكيد",
            reply_markup=admin_recharge_keyboard(user_id, ls_amount, recharge_id)
        )
        return

    # ============================
    # Syriatel Cash / Sham Cash — تحقق آلي من العملية
    # ============================
    if context.user_data.get("recharge_step") == "manual_amount":
        method = context.user_data.get("recharge_method")
        is_usd = context.user_data.get("recharge_currency") == "USD"
        raw = text.strip().translate(_ARABIC_DIGITS).replace(",", "")

        if is_usd:
            try:
                usd = Decimal(raw)
            except Exception:
                usd = None

            if usd is None or not usd.is_finite() or usd < SHAMCASH_USD_MIN or usd > 100000:
                await update.message.reply_text(
                    f"❌ أرسل مبلغاً صحيحاً بالدولار (أقل مبلغ {SHAMCASH_USD_MIN:g}):",
                    reply_markup=cancel_keyboard()
                )
                return

            pinned_rate = context.user_data.get("recharge_usd_rate")
            pinned_age = time.time() - context.user_data.get("recharge_usd_rate_at", 0)
            rate = pinned_rate if (pinned_rate and 0 <= pinned_age <= USD_RATE_PIN_SECONDS) else get_shamcash_usd_rate()
            ls_amount = usd_to_bot_amount(usd, rate)
            shown = f"{usd:g} USD (≈ {ls_amount:,} {CURRENCY_LABEL} — 1 USD = {rate:,})"
        else:
            if not raw.isdigit() or int(raw) < MANUAL_MIN_SYP:
                await update.message.reply_text(
                    f"❌ أرسل مبلغاً صحيحاً بالأرقام (أقل مبلغ {MANUAL_MIN_SYP} {CURRENCY_LABEL}):",
                    reply_markup=cancel_keyboard()
                )
                return
            ls_amount = int(raw)
            shown = f"{ls_amount:,} {CURRENCY_LABEL}"

        context.user_data["manual_ls_amount"] = ls_amount
        context.user_data["manual_amount_text"] = shown

        if method == "Syriatel Cash":
            _codes = _syriatel_codes()
            if len(_codes) > 1:
                context.user_data["recharge_step"] = "manual_code"
                kb = InlineKeyboardMarkup(
                    [[InlineKeyboardButton(c, callback_data=f"mccode_{i}")] for i, c in enumerate(_codes)]
                )
                await update.message.reply_text("📟 اختر الكود الذي حوّلت عليه:", reply_markup=kb)
                return
            code = _codes[0]
        else:
            code = sham_code_health.get_code()

        await _finish_manual_recharge(update.message, context, user_id, update.effective_user, code)
        return

    if context.user_data.get("recharge_step") == "payment_txid":

    # =====================================================
    # الحصول على رقم العملية
    # =====================================================

        txid = text.strip().translate(_ARABIC_DIGITS)

    # =====================================================
    # التحقق من صيغة رقم العملية
    # =====================================================

        if not (txid.isascii() and txid.isdigit()) or not 3 <= len(txid) <= 30:

            await update.message.reply_text(
                "❌ رقم العملية غير صالح.\n\n"
                "أرسل رقم العملية فقط.",
                reply_markup=cancel_keyboard()
            )

            return

    # =====================================================
    # معرفة طريقة الدفع
    # =====================================================

        method = context.user_data.get("recharge_method")

        if method not in ("Syriatel Cash", "Sham Cash"):

            context.user_data.clear()

            await update.message.reply_text(
                "❌ طريقة الدفع غير معروفة.\n"
                "ابدأ عملية الشحن من جديد.",
                reply_markup=main_menu(user_id)
            )

            return

    # =====================================================
    # 🔐 حظر مؤقت بعد محاولات فاشلة متكررة
    # =====================================================

        _lock_left = _txid_locked_seconds(user_id)

        if _lock_left > 0:

            await update.message.reply_text(
                "⛔ محاولات فاشلة كثيرة.\n"
                f"حاول مرة ثانية بعد {max(1, _lock_left // 60)} دقيقة "
                "أو تواصل مع الإدارة.",
                reply_markup=main_menu(user_id)
            )

            return

    # =====================================================
    # 🔐 فحص العملية قبل الاتصال بالـ API
    #
    # هذا يمنع إعادة استخدام الرقم حتى لو:
    # - نفس المستخدم
    # - مستخدم آخر
    # - طريقة دفع أخرى
    # =====================================================

        existing_transaction = transaction_already_used(txid)

        if existing_transaction:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ رقم العملية هذا مستخدم مسبقاً.\n\n"
                "لا يمكن استخدام نفس عملية الدفع "
                "لشحن الرصيد أكثر من مرة.",
                reply_markup=main_menu(user_id)
            )

            return

    # =====================================================
    # معرفة العملة
    # =====================================================

        is_usd = (
            context.user_data.get("recharge_currency") == "USD"
        )

    # =====================================================
    # 🧾 التشييك اليدوي (إذا فعّله الأدمن لهذه الطريقة): لا اتصال بالـ API
    # =====================================================

        _mk = _bonus_key_for_method(method)
        if _mk in ("syriatel", "shamcash") and await asyncio.to_thread(get_manual_check, _mk):
            context.user_data["manual_txid"] = txid
            context.user_data["recharge_step"] = "manual_amount"

            if is_usd:
                ask = "💵 أرسل المبلغ المحوَّل بالدولار (مثال: 10 أو 12.5):"
            else:
                ask = f"💰 أرسل المبلغ المحوَّل بالليرة السورية الجديدة (أرقام فقط، أقل مبلغ {MANUAL_MIN_SYP}):"

            await update.message.reply_text(
                "✅ تم استلام رقم العملية.\n\n"
                f"{ask}\n\n"
                "ℹ️ التحقق من هذه العملية يتم يدوياً من الإدارة.",
                reply_markup=cancel_keyboard()
            )
            return

    # =====================================================
    # رسالة الانتظار
    # =====================================================

        await update.message.reply_text(
            "🔎 جاري التحقق من العملية تلقائياً...\n"
            "⏳ يرجى الانتظار قليلاً.",
            reply_markup=cancel_keyboard()
        )

    # =====================================================
    # الاتصال بـ API
    # =====================================================

        verified, message, payment = await asyncio.to_thread(
            verify_payment,
            method,
            txid,
            None,
            period="7",
            expected_currency="USD" if is_usd else None,
        )

    # =====================================================
    # فشل التحقق
    # =====================================================

        if not verified or payment is None:

            _txid_record_fail(user_id)

            await update.message.reply_text(
                f"❌ لم يتم قبول العملية.\n\n"
                f"{message}\n\n"
                "تأكد من رقم العملية والتحويل إلى حسابنا "
                "ثم حاول مرة أخرى.",
                reply_markup=cancel_keyboard()
            )

            return

    # =====================================================
    # تحديد البونص
    # =====================================================

        bonus_key = _bonus_key_for_method(method)

        bonus_percent = (
            get_bonus_percent(bonus_key)
            if bonus_key
            else 0.0
        )

    # =====================================================
    # حساب المبلغ
    # =====================================================

        if is_usd:

        # -----------------------------------------------
        # الحد الأدنى
        # -----------------------------------------------

            if payment.amount < SHAMCASH_USD_MIN:

                await update.message.reply_text(
                    f"❌ أقل قيمة للشحن بالدولار هي "
                    f"{SHAMCASH_USD_MIN:g} دولار.\n\n"
                    f"المبلغ المحوّل: "
                    f"{payment.amount:.2f} دولار.\n\n"
                    "تواصل مع الإدارة بخصوص هذه العملية.",
                    reply_markup=cancel_keyboard()
                )

                return

        # -----------------------------------------------
        # سعر الصرف المثبت
        # -----------------------------------------------

            pinned_rate = context.user_data.get(
                "recharge_usd_rate"
            )

            pinned_age = (
                time.time()
                - context.user_data.get(
                    "recharge_usd_rate_at",
                    0
                )
            )

            if pinned_rate and 0 <= pinned_age <= USD_RATE_PIN_SECONDS:

                usd_rate = pinned_rate

            else:

                usd_rate = get_shamcash_usd_rate()

        # -----------------------------------------------
        # تحويل الدولار إلى ليرة
        # -----------------------------------------------

            credit_ls = usd_to_bot_amount(
                payment.amount,
                usd_rate
            )

        else:

        # Syriatel/Sham Cash بالليرة: الـ API يرجّع المبلغ بالليرة السورية الجديدة مباشرة، فلا ضرب
        # (كان الضرب بـ 100 من زمن العملة القديمة ويضخّم الرصيد 100 ضعف).
            credit_ls = int(Decimal(str(payment.amount)))

    # =====================================================
    # 🔐 الاعتماد النهائي
    #
    # انتبه:
    # نستخدم txid الذي أدخله المستخدم،
    # وليس payment.transaction_id
    #
    # لأن رقم العملية الذي حجزناه وفحصناه
    # يجب أن يكون نفسه الذي نسجله.
    # =====================================================

        result = approve_verified_recharge(
            user_id=user_id,
            amount=credit_ls,

        # ❗ مهم جداً: نسجّل رقم العملية القادم من API (المرجع الموثوق).
        # verify_payment تأكد أنه يطابق ما أدخله المستخدم بعد التوحيد.
            txid=str(payment.transaction_id or txid).strip(),

            method=method,
            bonus_percent=bonus_percent,
        )

    # =====================================================
    # العملية مستخدمة مسبقاً
    # =====================================================

        if result["status"] == "already_used":

            context.user_data.clear()

            await update.message.reply_text(
                "❌ هذه العملية مستخدمة مسبقاً "
                "ولا يمكن استخدامها مرة ثانية.",
                reply_markup=main_menu(user_id)
            )

            return

    # =====================================================
    # خطأ أثناء التسجيل
    # =====================================================

        if not result["ok"]:

            await update.message.reply_text(
                "❌ حدث خطأ أثناء تسجيل العملية.\n\n"
                "لم يتم إضافة الرصيد.",
                reply_markup=cancel_keyboard()
            )

            return

    # =====================================================
    # العملية نجحت
    # =====================================================

        bonus = result["bonus"]
        total = result["total"]

        _txid_record_success(user_id)

    # =====================================================
    # تفعيل الإحالة
    # =====================================================

        activation = activate_referral_after_recharge(
            referred_user_id=user_id,
            reward_amount=0
        )

        if activation.get("status") == "activated_no_reward":

            ref_id = activation["referrer_id"]

            try:

                await context.bot.send_message(
                    ref_id,
                    "🎉 تمت تفعيل إحالة جديدة بنجاح."
                )

            except Exception:
                pass

    # =====================================================
    # تسجيل عمولة الإحالة
    # =====================================================

        referrer_id = get_referrer(user_id)

        if referrer_id:

            commission_percent = (
                get_referral_commission_percent()
            )

            if commission_percent > 0:

                commission_amount = int(
                    total * commission_percent
                )

                if commission_amount > 0:

                    log_referral_commission(
                        referrer_id,
                        user_id,
                        total,
                        commission_amount
                    )

    # =====================================================
    # تنظيف حالة الشحن
    # =====================================================

        context.user_data.clear()

    # =====================================================
    # رسالة البونص
    # =====================================================

        bonus_text = (
            f"\n🎁 البونص: +{bonus:,} {CURRENCY_LABEL}"
            if bonus > 0
            else ""
        )
        if result.get("coupon_percent"):
            bonus_text += f"\n🎟️ يشمل كوبون عجلة الحظ: +{result['coupon_percent']}%"

    # =====================================================
    # رسالة للمستخدم
    # =====================================================

        await update.message.reply_text(
            "🎉 تم شحن رصيدك بنجاح!\n\n"
            f"💰 المبلغ الأساسي: "
            f"{credit_ls:,} {CURRENCY_LABEL}\n"
            f"{bonus_text}\n"
            f"💵 المجموع المضاف: "
            f"{total:,} {CURRENCY_LABEL}\n\n"
            f"🔢 رقم العملية: `{txid}`",
            parse_mode="Markdown",
            reply_markup=main_menu(user_id)
        )

        _dl = await _tg_user_label(context.bot, user_id, update.effective_user)
        await send_deposit_log(
            context.bot,
            _recharge_log_text("تلقائي", _dl, user_id, method, credit_ls, bonus, total, txid,
                               code=_payment_code(payment, method))
        )

        if credit_ls > DEPOSIT_ALERT_THRESHOLD:
            await send_alert(
                context.bot,
                "🚨 تنبيه: عملية شحن كبيرة (تلقائي)\n\n"
                f"{_alert_user_line(update.effective_user, user_id)}\n"
                f"💳 الطريقة: {method}\n"
                f"💰 المبلغ: {credit_ls:,} {CURRENCY_LABEL} (الحد: {DEPOSIT_ALERT_THRESHOLD:,})\n"
                f"🎁 البونص: {bonus:,} {CURRENCY_LABEL}\n"
                f"💵 المجموع المضاف: {total:,} {CURRENCY_LABEL}\n"
                f"🔢 رقم العملية: {txid}"
            )

        return

    # ============================
    # السحب — حسم بنسبة قابلة للتعديل (لوحة الأدمن ← إدارة نسبة السحب)
    # ============================
    if context.user_data.get("withdraw_step") == "amount":
        if not text.isdigit():
            await update.message.reply_text("❌ أرسل رقم فقط", reply_markup=cancel_keyboard())
            return
        method = context.user_data.get("withdraw_method", "")
        min_withdraw = WITHDRAW_MIN_BY_METHOD.get(method, 0)

        _wd_user = get_user(user_id)
        _wd_balance = int(_wd_user["balance"]) if _wd_user else 0

        # 1) الرصيد لا يكفي أصلاً للسحب بهذه الطريقة → إلغاء العملية (لا معنى لطلب مبلغ أكبر)
        _blocked = _withdraw_blocked_text(_wd_balance, method)
        if _blocked:
            context.user_data.clear()
            await update.message.reply_text(
                _blocked + "\n\nتم إلغاء العملية.",
                reply_markup=main_menu(user_id)
            )
            return

        # 2) المبلغ أكبر من الرصيد
        if int(text) > _wd_balance:
            await update.message.reply_text(
                f"❌ لا تملك رصيداً كافياً لهذا المبلغ.\n"
                f"💰 رصيدك الحالي: {_wd_balance:,} {CURRENCY_LABEL}\n\n"
                "أرسل مبلغاً أقل أو يساويه:",
                reply_markup=cancel_keyboard()
            )
            return

        # 3) المبلغ أقل من الحد الأدنى (والرصيد يكفي، فيستطيع تصحيحه)
        if int(text) < min_withdraw:
            await update.message.reply_text(
                f"❌ الحد الأدنى للسحب عبر {method} هو {min_withdraw:,} {CURRENCY_LABEL}.\n"
                "أرسل مبلغاً أكبر أو يساويه:",
                reply_markup=cancel_keyboard()
            )
            return
        context.user_data["withdraw_amount"] = int(text)
        context.user_data["withdraw_step"] = "account"
        if method == "USDT":
            await update.message.reply_text(
                "📌 أرسل عنوان محفظة USDT الخاصة بك (شبكة ERC20/BEP20):\n\n"
                "⚠️ تأكد من صحة العنوان — أي خطأ يعني ضياع المبلغ",
                reply_markup=cancel_keyboard()
            )
        elif method == "Sham Cash":
            await update.message.reply_text(
                "📌 أرسل عنوان حساب شام كاش الخاص بك (الكود الطويل الظاهر في التطبيق):\n\n"
                "⚠️ تأكد من صحته — الدفع يتم لهذا العنوان",
                reply_markup=cancel_keyboard()
            )
        else:
            await update.message.reply_text(
                "📌 أرسل الحساب أو المحفظة التي تريد السحب إليها:",
                reply_markup=cancel_keyboard()
            )
        return

    if context.user_data.get("withdraw_step") == "account":
        account = text.strip()
        amount = context.user_data["withdraw_amount"]
        method = context.user_data.get("withdraw_method", "غير محدد")
        context.user_data["withdraw_step"] = None

        user = get_user(user_id)

        if amount > user["balance"]:
            await update.message.reply_text("❌ الرصيد غير كافي", reply_markup=main_menu(user_id))
            return

        fee_pct = get_withdraw_fee_percent(user_id)
        fee = calc_withdraw_fee(amount, fee_pct)
        net_amount = amount - fee

        # التحقق من صحة عنوان USDT (ERC20/BEP20 يبدأ بـ 0x)
        if method == "USDT" and not account.startswith("0x"):
            await update.message.reply_text(
                "❌ عنوان محفظة USDT غير صحيح\n"
                "تأكد أن العنوان على شبكة ERC20/BEP20 (يبدأ بـ 0x)",
                reply_markup=cancel_keyboard()
            )
            context.user_data["withdraw_step"] = "account"
            return

       # update_balance(user_id, -amount)
        wid = create_withdraw(user_id, amount, account, method, fee_percent=fee_pct)
        update_withdraw_status(user_id, amount, "pending")

        if method == "USDT":
            user_msg = (
                f"✅ تم إرسال طلب السحب بـ USDT\n\n"
                f"💸 المبلغ المطلوب: {amount:,} {CURRENCY_LABEL}\n"
                f"📉 حسم {_fmt_pct(fee_pct)}: -{fee:,} {CURRENCY_LABEL}\n"
                f"💵 المبلغ الذي ستستلمه: {net_amount:,} {CURRENCY_LABEL}\n"
                f"🏦 محفظة USDT (ERC20/BEP20): `{account}`\n\n"
                f"⏳ بانتظار موافقة الأدمن"
            )
            admin_msg = (
                f"📤 طلب سحب USDT\n\n"
                f"👤 الاسم: @{update.effective_user.username or update.effective_user.first_name}\n"
                f"🆔 المعرف: {user_id}\n"
                f"💳 الطريقة: {method}\n"
                f"💰 المبلغ الكامل: {amount:,} {CURRENCY_LABEL}\n"
                f"📉 الحسم ({_fmt_pct(fee_pct)}): {fee:,} {CURRENCY_LABEL}\n"
                f"💵 يصله: {net_amount:,} {CURRENCY_LABEL}\n"
                f"🏦 عنوان المحفظة (ERC20/BEP20):\n`{account}`\n"
                f"🎮 iChancy: {user.get('ichancy_username')}"
            )
        else:
            user_msg = (
                f"✅ تم إرسال طلب السحب\n\n"
                f"💸 المبلغ المطلوب: {amount:,} {CURRENCY_LABEL}\n"
                f"📉 حسم {_fmt_pct(fee_pct)}: -{fee:,} {CURRENCY_LABEL}\n"
                f"💵 المبلغ الذي ستستلمه: {net_amount:,} {CURRENCY_LABEL}\n"
                f"🏦 إلى: {account}\n\n"
                f"⏳ بانتظار موافقة الأدمن"
            )
            user_display = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name
            admin_msg = (
                f"📤 طلب سحب جديد\n\n"
                f"👤 الاسم: {user_display}\n"
                f"🆔 المعرف: {user_id}\n"
                f"💳 الطريقة: {method}\n"
                f"💰 المبلغ الكامل: {amount:,} {CURRENCY_LABEL}\n"
                f"📉 الحسم ({_fmt_pct(fee_pct)}): {fee:,} {CURRENCY_LABEL}\n"
                f"💵 يصله: {net_amount:,} {CURRENCY_LABEL}\n"
                f"🪙 الحساب: {account}\n"
                f"🎮 iChancy: {user.get('ichancy_username')}"
            )

        await update.message.reply_text(user_msg, parse_mode="Markdown", reply_markup=main_menu(user_id))

        # نص سجل السحب (ايدي، مبلغ، عمولة، صافي، حساب، آخر 4 شحنات...). عند أي خطأ نرجع للنص القديم
        # حتى لا يضيع طلب السحب أبداً.
        try:
            overview = await asyncio.to_thread(get_user_recharge_overview, user_id, 4)
            acct_name = acct_code = None
            if method == "Sham Cash":
                try:
                    acct_name, acct_code = await asyncio.wait_for(
                        asyncio.to_thread(_sham_account_lookup, account), timeout=10
                    )
                except Exception:
                    acct_name = acct_code = None
            _lbl = await _tg_user_label(context.bot, user_id, update.effective_user)
            admin_msg = _withdraw_log_text(
                _lbl, user_id, method, amount, fee_pct, fee, net_amount, account,
                user.get("ichancy_username"), overview, acct_name, acct_code,
            )
        except Exception:
            logging.getLogger("withdraw_log").exception("withdraw log text failed; using basic text")

        _wd_msg = await _post_withdrawal_request(
            context.bot, admin_msg,
            admin_withdraw_keyboard(user_id, amount, account, wid=wid if method == "Sham Cash" else None),
        )

        # السحب التلقائي أولاً (Sham Cash)؛ عند الفشل يبقى الطلب بالقناة للمعالجة اليدوية
        if (
            method == "Sham Cash" and wid
            and (AUTO_WITHDRAW_MAX_AMOUNT == 0 or amount <= AUTO_WITHDRAW_MAX_AMOUNT)
            and await asyncio.to_thread(get_auto_withdraw)
        ):
            context.application.create_task(_auto_withdraw_attempt(context.bot, wid, _wd_msg, admin_msg))

        if amount > WITHDRAW_ALERT_THRESHOLD:
            await send_alert(
                context.bot,
                "🚨 تنبيه: طلب سحب كبير\n\n"
                f"{_alert_user_line(update.effective_user, user_id)}\n"
                f"💳 الطريقة: {method}\n"
                f"💰 المبلغ: {amount:,} {CURRENCY_LABEL} (الحد: {WITHDRAW_ALERT_THRESHOLD:,})\n"
                f"📉 الحسم ({_fmt_pct(fee_pct)}): {fee:,} {CURRENCY_LABEL}\n"
                f"💵 يصله: {net_amount:,} {CURRENCY_LABEL}\n"
                f"🎮 iChancy: {user.get('ichancy_username')}\n\n"
                "⏳ بانتظار موافقة الأدمن"
            )
        return

    # ============================
    # إهداء رصيد
    # ============================
    if text == "🎁 إهداء رصيد":
        context.user_data["state"] = "gift_user_id"
        await update.message.reply_text(
            f"🎁 عملية إهداء رصيد\n\n"
            f"ارسل معرف التلغرام للشخص المراد اهداء الرصيد اليه\n\n"
            f"🔹 معرفك هو: {user_id}\n"
            f"💰 الحد الأدنى للإهداء: {get_gift_min_amount():,} {CURRENCY_LABEL}\n\n"
            f"ارسل الآن معرف تلغرام المستخدم 👇",
            reply_markup=cancel_keyboard()
        )
        return

    elif context.user_data.get("state") == "gift_user_id":
        try:
            target_id = int(text)
        except:
            await update.message.reply_text("❌ الرجاء ارسال رقم معرف صحيح", reply_markup=cancel_keyboard())
            return

        if target_id <= 0:
            await update.message.reply_text("❌ الرجاء ارسال رقم معرف صحيح", reply_markup=cancel_keyboard())
            return

        if target_id == user_id:
            await update.message.reply_text("❌ لا يمكنك إهداء نفسك", reply_markup=cancel_keyboard())
            return

        gift_check = get_gift_receive_status(target_id)

        if gift_check["status"] != "ok":
            context.user_data.clear()
            await update.message.reply_text(
                _gift_error_text(gift_check),
                reply_markup=main_menu(user_id)
            )
            return

        context.user_data["gift_target"] = target_id
        context.user_data["state"] = "gift_amount"
        await update.message.reply_text(
            f"💰 ارسل المبلغ الذي تريد اهداؤه (الحد الأدنى {get_gift_min_amount():,} {CURRENCY_LABEL}):",
            reply_markup=cancel_keyboard()
        )
        return

    elif context.user_data.get("state") == "gift_amount":
        try:
            amount = int(text)
        except:
            await update.message.reply_text("❌ ارسل رقم صحيح", reply_markup=cancel_keyboard())
            return

        target_id = context.user_data.get("gift_target")

        if target_id is None:
            context.user_data.clear()
            await update.message.reply_text("❌ انتهت العملية، ابدأ من جديد.", reply_markup=main_menu(user_id))
            return

        # خصم + إضافة + تسجيل الهدية + حد الاستلام: كلها بمعاملة ذرّية واحدة
        result = transfer_gift(user_id, target_id, amount)

        if result["status"] in ("invalid_amount", "below_minimum", "insufficient"):
            await update.message.reply_text(_gift_error_text(result), reply_markup=cancel_keyboard())
            return

        if result["status"] != "ok":
            context.user_data.clear()
            await update.message.reply_text(_gift_error_text(result), reply_markup=main_menu(user_id))
            return

        await update.message.reply_text(f"✅ تم إهداء {amount:,} {CURRENCY_LABEL} بنجاح 🎁", reply_markup=main_menu(user_id))

        try:
            await context.bot.send_message(target_id, f"🎉 استلمت {amount:,} {CURRENCY_LABEL} كهدية!")
        except Exception:
            pass

        user_info = update.effective_user
        user_display = f"@{user_info.username}" if user_info.username else user_info.first_name
        await notify_deposits(
            context.bot,
            f"🎁 إهداء\n👤 من: {user_display} ({user_id})\n🎯 إلى: {target_id} | 💰 {amount:,} {CURRENCY_LABEL}\n"
            f"📥 الهدية رقم {result['received']} للمستلم"
        )
        context.user_data.clear()
        return
    # ============================
    # 📈 العروض والبونصات الحالية
    # ============================
    if text == "📈 العروض والبونصات الحالية":
        await update.message.reply_text(
            build_offers_text(user_id),
            parse_mode="HTML",
            reply_markup=main_menu(user_id)
        )
        return


    # ============================
    # 🎁 تفعيل كود هدية
    # ============================
    if text == "💎كود هدية":
        context.user_data["state"] = "redeem_gift_code"
        await update.message.reply_text(
            "💎تفعيل كود هدية\n\n"
            "أرسل الآن كود الهدية الذي تملكه 👇",
            reply_markup=cancel_keyboard()
        )
        return

    elif context.user_data.get("state") == "redeem_gift_code":

        code = text.strip()
        result = redeem_gift_code(user_id, code)

        if result["ok"]:
            context.user_data.clear()
            new_balance = get_user_balance(user_id)

            await update.message.reply_text(
                f"✅ تم تفعيل كود الهدية بنجاح! 🎁\n\n"
                f"🎟️ الكود: {result['code']}\n"
                f"💰 المبلغ المضاف: {result['amount']:,} {CURRENCY_LABEL}\n\n"
                f"💳 رصيدك الحالي: {new_balance:,} {CURRENCY_LABEL}",
                reply_markup=main_menu(user_id)
            )

            user_info = update.effective_user
            user_display = f"@{user_info.username}" if user_info.username else user_info.first_name
            await notify_deposits(
                context.bot,
                f"💎تم استخدام كود هدية\n"
                f"👤 {user_display} ({user_id})\n"
                f"🎟️ الكود: {result['code']}\n"
                f"💰 المبلغ: {result['amount']:,} {CURRENCY_LABEL}\n"
                f"📊 الاستخدامات: {result['used_count']}/{result['max_uses']}"
            )
            return

        status = result.get("status")

        error_messages = {
            "invalid_code": "❌ الرجاء إرسال كود صحيح.",
            "not_found": "❌ هذا الكود غير موجود، تأكد من كتابته وأعد المحاولة.",
            "inactive": "❌ هذا الكود غير مُفعّل حالياً.",
            "expired": "⌛ انتهت صلاحية هذا الكود.",
            "limit_reached": "❌ تم استنفاد عدد مرات استخدام هذا الكود بالكامل.",
            "already_used": "❌ لقد استخدمت هذا الكود من قبل، لا يمكن استخدامه مرة أخرى.",
        }

        await update.message.reply_text(
            error_messages.get(status, "❌ حدث خطأ أثناء تفعيل الكود، حاول مجدداً.")
            + "\n\nيمكنك إرسال كود آخر، أو الضغط على تراجع:",
            reply_markup=cancel_keyboard()
        )
        return

    # ============================
    # رسالة للأدمن
    # ============================
    if text == "📞 تواصل معنا":
        context.user_data["state"] = "contact_admin"
        await update.message.reply_text(" أرسل استفسارك الآن سيتم الرد من قبل الأدمن ✉️", reply_markup=cancel_keyboard())
        return

    elif context.user_data.get("state") == "contact_admin":
        user_info = update.effective_user
        user_display = f"@{user_info.username}" if user_info.username else user_info.first_name
        await notify_admins(context.bot, f"📩 رسالة من مستخدم\n👤 {user_display}\n🆔 {user_id}\n\n📝 {text}")
        await update.message.reply_text("✅ تم إرسال رسالتك للإدمن", reply_markup=main_menu(user_id))
        context.user_data.clear()
        return

    # ============================
    # القوائم الرئيسية
    # ============================
    if text == "🤝نظام الإحالات":
        await referral_menu(update, context)
        return
    
    if text == "📥 شحن رصيد في البوت":
        bonus_percents = get_all_bonus_percents()

        def _bonus_suffix(key):
            pct = bonus_percents.get(key, 0.0)
            return f" (+{pct * 100:.0f}%)" if pct > 0 else ""

        keyboard = [
            [InlineKeyboardButton(f"🟢 Syriatel Cash{_bonus_suffix('syriatel')}", callback_data="syriatel_cash")],
            [InlineKeyboardButton(f"💰 عملات ومحافظ رقمية (USDT){_bonus_suffix('usdt')}", callback_data="crypto")],
            [InlineKeyboardButton(f"⚡ Sham Cash (ل.س){_bonus_suffix('shamcash')}", callback_data="sham_cash")],
            [InlineKeyboardButton(f"⚡ Sham Cash (دولار $){_bonus_suffix('shamcash')}", callback_data="sham_cash_usd")],
            [InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="back_to_menu")]
        ]
        await update.message.reply_text("اختر أحد طرق الشحن:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if text == "📤 سحب رصيد من البوت":
        _u = get_user(user_id)
        _blocked = _withdraw_blocked_text(int(_u["balance"]) if _u else 0, "")
        if _blocked:
            await update.message.reply_text(_blocked, reply_markup=main_menu(user_id))
            return

        _notice, _example = _fee_notice(get_withdraw_fee_percent(user_id))
        keyboard = [
            [InlineKeyboardButton("🟢 Syriatel Cash", callback_data="wd_syriatel"),
             InlineKeyboardButton("⚡ Sham Cash", callback_data="wd_sham")],
            [InlineKeyboardButton("💰 USDT", callback_data="wd_usdt")],
            [InlineKeyboardButton("⬅️ القائمة الرئيسية", callback_data="back_to_menu")]
        ]
        await update.message.reply_text(
            f"{_notice}\n"
            f"{_example}\n\n"
            f"اختر طريقة السحب:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # ============================
    # 🧾 حساب Ichancy
    # ============================
    if text == "🧾  معلومات حسابي":

        user = get_user(user_id)

    # ==========================================
    # المستخدم لديه حساب Ichancy بالفعل
    # ==========================================
        if user["ichancy_username"] is not None:
 
            ichancy_username = user["ichancy_username"]
            ichancy_password = user["ichancy_password"]

        # جلب الحساب من Ichancy لمعرفة الرصيد الحقيقي
            player = get_player_by_username(ichancy_username)

            if player:
                player_id = player.get("playerId")

                if player_id:
                    current_balance = get_player_balance(player_id)

                    if current_balance is not None:
                        balance_text = f"{current_balance:,.2f}"
                    else:
                        balance_text = "تعذر جلب الرصيد"
                else:
                    balance_text = "تعذر جلب الرصيد"
            else:
                balance_text = "تعذر الوصول للحساب"

        # ==========================================
        # أزرار Inline
        # ==========================================
            keyboard = [
                [
                    InlineKeyboardButton(
                        "🔄 تحديث الرصيد",
                        callback_data="refresh_ichancy_account"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🌐 فتح Ichancy",
                        url="https://www.ichancy100.com"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ رجوع",
                        callback_data="back_to_menu"
                    )
                ]
            ]

            balance = await show_balance(update, context)
            
            await update.message.reply_text(
                f"🧾 *معلومات حسابك في Ichancy*\n\n"
                f"🆔 *User ID:*\n"
                f"`{user_id}`\n\n"
                f"👤 *Username:*\n"
                f"`{ichancy_username}`\n\n"
                f"🔑 *Password:*\n"
                f"`{ichancy_password}`\n\n"
                f"💰 * الرصيد داخل الموقع:*\n"
                f"`{balance_text}`\n\n"
                f"💰 *الرصيد داخل البوت:*\n"
                f"`{balance}`\n\n"
                f"⚠️ احتفظ بمعلومات تسجيل الدخول ولا تشاركها مع أي شخص.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            return

    # ==========================================
    # المستخدم لا يملك حساب Ichancy
    # ==========================================
        context.user_data["ichancy_step"] = "username"

        await update.message.reply_text(
            "🧾 حساب Ichancy\n\n"
            "✏️ أرسل اسم المستخدم المطلوب فقط\n"
            "(بدون @lacassa-bot1 — سيضاف تلقائياً):",
            reply_markup=cancel_keyboard()
        )

        return
    

    if text == "🎰 اللفة المجانية":
        user = get_user(user_id)

        # الشرط: لاعب شاحن فعلاً (عملية شحن مؤكَّدة) — وليس مجرد رصيد موجود
        if not has_approved_recharge(user_id):
            await update.message.reply_text("❌ يجب شحن رصيدك أولاً للحصول على اللفة المجانية 🔒")
            return

        prizes = get_active_spin_prizes()

        if not prizes:
            await update.message.reply_text("⚠️ عجلة الحظ غير مهيأة حالياً، حاول لاحقاً.")
            return

        labels = [p["label"] for p in prizes]

        # ==========================================================
        # فحص الأهلية: مرور مدة الانتظار (24 ساعة كحد أدنى) على آخر لفة
        # ==========================================================
        cooldown_hours = get_spin_cooldown_hours()
        remaining_text = _spin_remaining_text(get_last_spin_at(user_id), cooldown_hours)
        eligible = remaining_text is None

        land_index = None
        chosen_prize_type = None

        # ==========================================================
        # القرار من السيرفر فقط (آمن 100%) — يُطبَّق فوراً هون قبل
        # حتى ما تنفتح واجهة العجلة. الواجهة تعرض النتيجة فقط.
        # ==========================================================
        if eligible:

            chosen = pick_random_spin_prize()

            if chosen:

                # حجز اللفة بشكل ذرّي: يمنع اللف مرتين عند الضغط المتكرر/المتزامن
                if not try_claim_spin(user_id, cooldown_hours):
                    eligible = False
                    remaining_text = (
                        _spin_remaining_text(get_last_spin_at(user_id), cooldown_hours)
                        or f"{cooldown_hours} ساعة"
                    )

                else:
                    for i, p in enumerate(prizes):
                        if p["id"] == chosen["id"]:
                            land_index = i
                            break

                    prize_type = chosen["prize_type"]
                    chosen_prize_type = prize_type
                    amount = chosen["amount"]
                    label = chosen["label"]

                    if prize_type == "cash" and amount > 0:
                        # رصيد البوت فقط (مثل كل المكافآت الأخرى). كان الكود السابق يضيف المبلغ
                        # لرصيد البوت وأيضاً لحساب iChancy (ضِعف الجائزة)، فأُزيل إيداع iChancy.
                        update_balance(user_id, amount)
                        log_bonus_event(user_id, "spin", amount)

                        # ملاحظة: لا نرسل نص النتيجة بالشات هون عمداً — النتيجة
                        # تنكشف فقط جوا واجهة العجلة بعد ما يلف المستخدم، حتى ما
                        # تنحرق المفاجأة قبل فتح الصفحة.

                        user_info = update.effective_user
                        user_display = f"@{user_info.username}" if user_info.username else user_info.first_name
                        await notify_deposits(context.bot, f"🎡 جائزة عجلة\n👤 {user_display} | 🆔 {user_id}\n💰 {amount:,} {CURRENCY_LABEL} ({label})")

                    elif prize_type in SPIN_PERCENT_PRIZE_TYPES and amount > 0:
                        user_info = update.effective_user
                        user_display = f"@{user_info.username}" if user_info.username else user_info.first_name

                        granted = False
                        notice = ""

                        try:
                            if prize_type == "recharge_bonus":
                                grant_spin_coupon(user_id, "recharge_bonus", amount)
                                notice = (
                                    f"🎁 مبروك! ربحت زيادة {amount}% على شحنتك القادمة 📈\n\n"
                                    f"تُضاف الزيادة تلقائياً على أول شحن معتمد لك."
                                )
                            else:
                                coupon_day = _shift_day(syria_now().strftime("%Y-%m-%d"), 1)
                                grant_spin_coupon(user_id, "daily_cashback", amount, applies_day=coupon_day)
                                notice = (
                                    f"🎁 مبروك! ربحت تعويضاً يومياً {amount}% 🌅\n\n"
                                    f"يشمل خسارة يوم {coupon_day} (اليوم الكامل القادم)، ويُصرف الساعة "
                                    f"{DAILY_CASHBACK_HOUR}:00 صباح اليوم التالي بشرط أن يكون رصيدك في iChancy = 0 "
                                    f"عند 12:00 ليلاً وسحوبك ≤ إيداعاتك."
                                )
                            granted = True
                        except Exception as exc:
                            update_last_spin_at(user_id, reset=True)   # لا يضيع دوره إن فشل منح الكوبون
                            print(f"⚠️ فشل منح كوبون العجلة للمستخدم {user_id}: {exc}")

                        if granted:
                            try:
                                # تأكيد بالشات بعد وقت كافٍ لانكشاف النتيجة داخل العجلة
                                _schedule_spin_notice(context.bot, user_id, notice)
                                await notify_deposits(context.bot, f"🎡 جائزة عجلة\n👤 {user_display} | 🆔 {user_id}\n🎟️ {label}")
                            except Exception as exc:
                                print(f"⚠️ تعذر إرسال إشعار كوبون العجلة: {exc}")

                    elif prize_type == "respin":
                        # لفة إضافية: نصفّر التوقيت ليصير مؤهلاً فوراً
                        update_last_spin_at(user_id, reset=True)

                    elif prize_type == "custom_message":
                        user_info = update.effective_user
                        user_display = f"@{user_info.username}" if user_info.username else user_info.first_name
                        await notify_admins(context.bot, f"🎁 جائزة عجلة تحتاج معالجة يدوية\n👤 {user_display} | 🆔 {user_id}\n🎁 {label}")

        spin_url = _build_spin_webapp_url(
            labels=labels,
            eligible=eligible,
            land_index=land_index,
            remaining_text=remaining_text,
            prize_type=chosen_prize_type,
        )

        keyboard = [[InlineKeyboardButton("🎡 فتح عجلة الحظ", web_app={"url": spin_url})]]

        if eligible:
            caption = "🎁 اضغط بالأسفل لمشاهدة نتيجتك على العجلة 🎡"
        else:
            caption = (
                f"❌ ما فيك تلف العجلة هلق.\n"
                f"⏰ اللفة القادمة متاحة خلال {remaining_text}\n\n"
                f"🎡 بإمكانك تجربة العجلة بلفة تجريبية بالأسفل (بدون جائزة حقيقية)"
            )

        await update.message.reply_text(caption, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if text == "☁️ الشروحات":
        await update.message.reply_text("📚 *الأسئلة الشائعة*", reply_markup=faq_menu(), parse_mode="Markdown")
        return

    if text == "📌 الشروط والأحكام":
        await update.message.reply_text(TEXT)
        return

   

    if text == "📱 ichancy apk":
        keyboard = [[InlineKeyboardButton("⬇️ تحميل التطبيق", url="https://android.betcoapps.com/novichok/ichancy_com/ichancy_com.apk")]]
        await update.message.reply_text("اضغط على الزر لتحميل التطبيق:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if text == "📜 السجل":
        await update.message.reply_text("📜 اختر نوع السجل:", reply_markup=history_menu())
        return
#--------------------------------------------------
#--------------------------------------------------    
async def refresh_ichancy_account_callback(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if await service_blocked(query.message.reply_text, user_id, "account"):
        return

    user = get_user(user_id)

    if not user:
        await query.answer("❌ لم يتم العثور على حسابك.", show_alert=True)
        return

    ichancy_username = user.get("ichancy_username")
    ichancy_password = user.get("ichancy_password")

    if not ichancy_username:
        await query.answer("❌ لا يوجد حساب Ichancy مربوط.", show_alert=True)
        return

    # جلب الحساب من Ichancy
    player = get_player_by_username(ichancy_username)

    if not player:
        await query.answer(
            "❌ تعذر الوصول إلى حساب Ichancy.",
            show_alert=True
        )
        return

    player_id = player.get("playerId")

    if not player_id:
        await query.answer(
            "❌ لم يتم العثور على Player ID.",
            show_alert=True
        )
        return

    # جلب الرصيد الحقيقي
    current_balance = get_player_balance(player_id)

    if current_balance is None:
        await query.answer(
            "❌ تعذر جلب الرصيد حالياً.",
            show_alert=True
        )
        return

    balance_text = f"{current_balance:,.2f}"

    keyboard = [
        [
            InlineKeyboardButton(
                "🔄 تحديث الرصيد",
                callback_data="refresh_ichancy_account"
            )
        ],
        [
            InlineKeyboardButton(
                "🌐 فتح Ichancy",
                url="https://www.ichancy100.com"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ رجوع",
                callback_data="back_to_menu"
            )
        ]
    ]

    await query.edit_message_text(
        f"🧾 *معلومات حسابك في Ichancy*\n\n"
        f"👤 *Username:*\n"
        f"`{ichancy_username}`\n\n"
        f"🔑 *Password:*\n"
        f"`{ichancy_password}`\n\n"
        f"💰 *الرصيد الحالي:*\n"
        f"`{balance_text}` NPS\n\n"
        f"🔄 تم تحديث الرصيد الآن.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def _referral_menu_burn(update, context):
    """شاشة الإحالات بنظام الحرق: نظامه، رابطه، إحالاته النشطة وأرباحه المتوقعة بالدورة الجارية."""
    user_id = update.effective_user.id
    plan = get_user_referral_plan(user_id)

    if plan is None:
        await send_referral_plan_prompt(update.message)
        return

    total = len(get_referral_users(user_id))
    st = await asyncio.to_thread(referral_burn.player_status, user_id)

    lines = [
        f"🎯 <b>نظام الإحالات — {referral_burn.PLAN_LABELS[plan]}</b>",
        referral_burn.plan_description(plan),
        "",
        "🔗 رابط الاحالة الخاص بك:",
        f"<code>https://t.me/{context.bot.username}?start=ref_{user_id}</code>",
        "",
        f"👤 إجمالي الإحالات: <b>{total}</b>",
    ]
    if st:
        end_local = st["cycle_end"]
        left = max(0.0, (end_local - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() / 86400)
        lines += [
            f"✅ إحالات نشطة هالدورة: <b>{st['active_referrals']}</b> من {referral_burn.MIN_ACTIVE_REFERRALS}",
            f"📊 حرق نطاقك هالدورة: <b>{st['total_burn']:,} {CURRENCY_LABEL}</b>",
            f"💰 أرباحك المتوقعة: <b>{st['potential']:,} {CURRENCY_LABEL}</b>",
            ("✅ الشروط متحققة حالياً" if st["eligible"] else f"⚠️ الشروط غير متحققة بعد: {st['reason']}"),
            "",
            f"⏳ المتبقي على التوزيع: <b>{left:.1f} يوم</b> (كل {referral_burn.CYCLE_DAYS} أيام)",
        ]
    lines += ["", "ℹ️ الأرباح تُضاف تلقائياً عند التوزيع إذا تحققت الشروط وقت الإغلاق."]

    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=main_menu(user_id))


async def referral_menu(update, context):
    user_id = update.effective_user.id

    if referral_burn.is_enabled():
        await _referral_menu_burn(update, context)
        return

    referrals = get_referral_users(user_id)

    active_count = sum(
        1 for r in referrals
        if r["status"] == "active"
    )

    total_count = len(referrals)

    # ضمان وجود دورة أرباح لهذا المستخدم (حتى لو لسا ما عنده إحالات)
    init_referral_cycle_if_missing(user_id)
    cycle_start_raw = get_referral_cycle_start(user_id)
    period_days = get_referral_period_days()

    remaining_days = period_days
    if cycle_start_raw:
        cycle_start_dt = datetime.strptime(cycle_start_raw, "%Y-%m-%d %H:%M:%S")
        elapsed_days = (datetime.utcnow() - cycle_start_dt).total_seconds() / 86400
        remaining_days = max(0, math.ceil(period_days - elapsed_days))

    pending_commission = get_pending_commission_total(user_id)

    message = (
        "🎯 <b>كن وكيلاً معنا بأبسط طريقة</b>\n"
        "احصل على نسبة ثابتة لكل عمليات الشحن والتعبئة القادمة "
        "عن طريق رابط احالتك ضمن البوت\n\n"
        "🔗 رابط الاحالة الخاص بك:\n"
        f"<code>https://t.me/{context.bot.username}?start=ref_{user_id}</code>\n\n"
        f"👤 إجمالي الإحالات: <b>{total_count}</b>\n"
        f"✅ الإحالات النشطة: <b>{active_count}</b>\n\n"
        f"💰 أرباحك المتراكمة هذه الدورة: <b>{pending_commission:,} {CURRENCY_LABEL}</b>\n\n"
        f"⏱️ مدة حساب الارباح: <b>{period_days} يوم/أيام</b>\n"
        f"⏳ المدة المتبقية على توزيع الارباح: <b>{remaining_days} يوم/أيام</b>"
    )

    await update.message.reply_text(
        message,
        parse_mode="HTML",
        reply_markup=main_menu(user_id)
    )


# ============================
# 👨‍💻 أمر التواصل مع المطور
# ============================

async def developer_command(update, context):

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👨‍💻 التواصل مع المطور",
                url="https://t.me/Ehsan_abbas"
            )
        ]
    ])

    await update.message.reply_text(
        "👨‍💻 *مطور البوت*\n\n"
        "إذا كان لديك استفسار أو مشكلة أو تريد التواصل "
        "مع مطور البوت، اضغط على الزر بالأسفل 👇",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
#---------------------------------------
async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "بدء البوت"),
        BotCommand("developer", "التواصل مع المطور"),
    ])
    # تحويل Sham Cash انقطع بالبوت أثناء الإرسال: نعلّمه "غير مؤكد" (لا نعيد إرساله أبداً)
    try:
        stuck = await asyncio.to_thread(sham_tx.reconcile_stuck, True)
        if stuck:
            await notify_admins(
                application.bot,
                f"⚠️ {len(stuck)} تحويل Sham Cash انقطع أثناء التنفيذ (إعادة تشغيل البوت).\n"
                "راجعه من: لوحة الأدمن ← تحويل Sham Cash ← غير المؤكدة، وتأكد من تطبيق شام كاش."
            )
    except Exception as exc:
        print(f"⚠️ تعذر فحص تحويلات Sham Cash المعلّقة: {exc}")

    # تحويل الوضع العام القديم (أسبوعي/يومي) إلى مفاتيح التفعيل الجديدة (مرة واحدة)
    try:
        migrate_legacy_cashback_mode(get_cashback_mode(), get_cashback_started_at())
    except Exception as exc:
        print(f"⚠️ تعذر ترحيل وضع التعويض القديم: {exc}")

    # تشغيل حلقة الاسترداد الأسبوعي التلقائية بالخلفية
    application.create_task(weekly_cashback_loop(application))
    application.create_task(cashback_scheduler_loop(application))
    application.create_task(referral_payout_loop(application))
    # مطابقة شام كاش بالتقرير اليومي: ربط مزوّد سجل تحويلات الحساب (apisyria logs + outgoing_logs)
    daily_report.set_sham_history_provider(sham_report_provider.provider)
    application.create_task(
        daily_report.daily_report_loop(application, _report_primary_targets, _report_fallback_targets)
    )
    application.create_task(sham_code_monitor_loop(application))
    application.create_task(withdraw_payout_loop(application))
    application.create_task(referral_burn_loop(application))
#----------------------------
async def admin_create_gift_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    # بداية إنشاء الكود
    context.user_data["gift_admin_step"] = "amount"

    await query.message.reply_text(
        "💎إنشاء كود هدية\n\n"
        "💰 أرسل قيمة الهدية بالليرة السورية:\n\n"
        "مثال:\n"
        "50000",
        reply_markup=cancel_keyboard()
    )

# ============================
# تشغيل البوت
# ============================
if __name__ == "__main__":
    init_api()

    application = (
    ApplicationBuilder()
    .token(TOKEN)
    .post_init(post_init)
    .build()
)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(admin_alert_test_callback, pattern="^admin_alert_test$"))
    application.add_handler(CallbackQueryHandler(admin_manual_check_callback, pattern="^admin_mc_"))
    application.add_handler(CallbackQueryHandler(admin_auto_withdraw_callback, pattern="^admin_autowd_"))
    application.add_handler(CallbackQueryHandler(admin_syriatel_callback, pattern="^admin_sy_"))
    application.add_handler(CallbackQueryHandler(manual_check_code_callback, pattern="^mccode_[0-9]+$"))
    application.add_handler(CallbackQueryHandler(admin_deposit_log_test_callback, pattern="^admin_deposit_log_test$"))
    application.add_handler(CallbackQueryHandler(admin_withdraw_log_test_callback, pattern="^admin_withdraw_log_test$"))
    application.add_handler(CommandHandler("developer", developer_command))


    application.add_handler(CallbackQueryHandler(admin_users_callback,      pattern="^admin_users_"))
    application.add_handler(CallbackQueryHandler(ichancy_players_callback,   pattern="^ichancy_players_"))
    application.add_handler(CallbackQueryHandler(admin_panel_callback,       pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(admin_broadcast_callback,   pattern="^admin_broadcast$"))
    application.add_handler(CallbackQueryHandler(admin_dm_callback,          pattern="^admin_dm$"))
    application.add_handler(CallbackQueryHandler(admin_broadcast_confirm_callback, pattern="^admin_bc_(confirm|cancel)$"))
    application.add_handler(CallbackQueryHandler(admin_report_callback, pattern="^admin_report_"))
    application.add_handler(CallbackQueryHandler(admin_players_callback, pattern="^admin_pl_"))
    application.add_handler(CallbackQueryHandler(admin_shamlog_callback, pattern="^shamlog:"))
    application.add_handler(CallbackQueryHandler(admin_shcode_callback, pattern="^shcode:"))
    # group=-1: يلتقط الرسائل المحوّلة للأدمن قبل باقي المعالجات (ولا يتدخل خارج خطوات «إدارة اللاعبين»)
    application.add_handler(MessageHandler(filters.ALL, admin_players_forward_handler), group=-1)
    application.add_handler(
    CallbackQueryHandler(
        admin_create_gift_callback,
        pattern="^admin_create_gift$"
    )
    )
    application.add_handler(CallbackQueryHandler(admin_welcome_bonus_callback, pattern="^(admin_welcome_bonus|toggle_welcome_bonus|set_welcome_bonus_amount)$"))
    application.add_handler(CallbackQueryHandler(admin_delete_user_callback, pattern="^admin_delete_user$"))
    application.add_handler(CallbackQueryHandler(admin_set_rate_callback,      pattern="^admin_set_rate$"))
    application.add_handler(CallbackQueryHandler(admin_set_usd_rate_callback,  pattern="^admin_set_usd_rate$"))
    application.add_handler(CallbackQueryHandler(admin_notice_callback,        pattern="^admin_notice_"))
    application.add_handler(CallbackQueryHandler(admin_bonus_menu_callback,    pattern="^admin_bonus_menu$"))
    application.add_handler(CallbackQueryHandler(admin_set_bonus_callback,     pattern="^admin_set_bonus_"))
    application.add_handler(CallbackQueryHandler(admin_cashback_menu_callback, pattern="^admin_cashback_menu$"))
    application.add_handler(CallbackQueryHandler(admin_set_cashback_percent_callback, pattern="^admin_set_cashback_percent$"))
    application.add_handler(CallbackQueryHandler(admin_gift_limit_callback,          pattern="^admin_gift_(limit|min_edit|toggle)$"))
    application.add_handler(CallbackQueryHandler(referral_plan_callback, pattern="^rp_(choose_(hier|ind)|confirm_(hier|ind)|cancel)$"))
    application.add_handler(CallbackQueryHandler(cashback_choice_callback,            pattern="^cb_(choose_(daily|weekly)|confirm_(daily|weekly)|cancel)$"))
    application.add_handler(CallbackQueryHandler(admin_run_cashback_now_callback,     pattern="^admin_run_cashback_now_(weekly|daily)$"))
    application.add_handler(CallbackQueryHandler(admin_cashback_user_callback,        pattern="^admin_cashback_user$"))
    application.add_handler(CallbackQueryHandler(admin_cashback_setuser_callback,     pattern="^admin_cashback_setuser_(daily|weekly|clear)_[0-9]+$"))
    application.add_handler(CallbackQueryHandler(admin_cashback_bulk_callback,        pattern="^admin_cashback_(bulk|bulkgo)_(daily|weekly)$"))
    # أزرار قديمة من رسائل سابقة: نعيد عرض القائمة الجديدة فقط
    application.add_handler(CallbackQueryHandler(admin_cashback_menu_callback,        pattern="^admin_run_cashback_now$"))
    application.add_handler(CallbackQueryHandler(admin_cashback_history_callback,     pattern="^admin_cashback_history$"))
    application.add_handler(CallbackQueryHandler(admin_cashback_history_daily_callback, pattern="^admin_cashback_history_daily$"))
    application.add_handler(CallbackQueryHandler(admin_cashback_toggle_callback,      pattern="^admin_cashback_toggle_(weekly|daily)$"))
    application.add_handler(CallbackQueryHandler(admin_cashback_menu_callback,        pattern="^admin_cashback_mode_(weekly|daily|off)$"))
    application.add_handler(CallbackQueryHandler(admin_set_daily_percent_callback,    pattern="^admin_set_daily_percent$"))
    application.add_handler(CallbackQueryHandler(admin_set_weekly_weeks_callback,     pattern="^admin_set_weekly_weeks$"))
    application.add_handler(CallbackQueryHandler(admin_set_daily_days_callback,       pattern="^admin_set_daily_days$"))
    application.add_handler(CallbackQueryHandler(shtx_callback,                       pattern="^shtx_"))
    application.add_handler(CallbackQueryHandler(admin_referral_menu_callback,        pattern="^admin_referral_menu$"))
    application.add_handler(CallbackQueryHandler(admin_set_referral_percent_callback, pattern="^admin_set_referral_percent$"))
    application.add_handler(CallbackQueryHandler(admin_set_referral_period_callback,  pattern="^admin_set_referral_period$"))
    application.add_handler(CallbackQueryHandler(admin_run_referral_now_callback,     pattern="^admin_run_referral_now$"))
    application.add_handler(CallbackQueryHandler(admin_referral_history_callback,     pattern="^admin_referral_history$"))
    application.add_handler(CallbackQueryHandler(admin_spin_menu_callback,        pattern="^admin_spin_menu$"))
    application.add_handler(CallbackQueryHandler(admin_spin_toggle_callback,      pattern="^admin_spin_toggle_"))
    application.add_handler(CallbackQueryHandler(admin_spin_delete_callback,      pattern="^admin_spin_delete_"))
    application.add_handler(CallbackQueryHandler(admin_spin_editweight_callback,  pattern="^admin_spin_editweight_"))
    application.add_handler(CallbackQueryHandler(admin_spin_add_callback,         pattern="^admin_spin_add$"))
    application.add_handler(CallbackQueryHandler(admin_spin_type_callback,        pattern="^admin_spin_type_"))
    application.add_handler(CallbackQueryHandler(admin_set_spin_cooldown_callback, pattern="^admin_set_spin_cooldown$"))
    application.add_handler(CallbackQueryHandler(terms_callback,             pattern="^(agree|disagree)$"))
    application.add_handler(CallbackQueryHandler(check_join_callback,        pattern="^check_join$"))
    application.add_handler(CallbackQueryHandler(faq_callback,               pattern="^faq_"))
    application.add_handler(CallbackQueryHandler(gift_admin_callback,        pattern="^gift_"))
    application.add_handler(CallbackQueryHandler(recharge_callback,          pattern="^(syriatel_cash|crypto|sham_cash|sham_cash_usd|back_to_menu)$"))
    application.add_handler(CallbackQueryHandler(withdraw_callback,          pattern="^wd_"))
    # ✅ التعديل: إضافة handler لزر تعديل المبلغ
    application.add_handler(CallbackQueryHandler(admin_edit_amount_callback, pattern="^edit_amount_"))
    application.add_handler(CallbackQueryHandler(admin_recharge_callback,    pattern="^(approve_|reject_)[0-9]+_[0-9]+(_[0-9]+)?$"))
    application.add_handler(CallbackQueryHandler(admin_withdraw_callback,    pattern="^(wdapprove_|wdreject_).*"))
    application.add_handler(CallbackQueryHandler(admin_withdraw_pay_callback, pattern="^wdpay_"))
    application.add_handler(CallbackQueryHandler(admin_ref2_callback, pattern="^ref2:"))
    application.add_handler(CallbackQueryHandler(admin_wdfee_callback, pattern="^wdfee:"))
    application.add_handler(CallbackQueryHandler(ichancy_callback,           pattern="^back_to_menu$"))
    application.add_handler(CallbackQueryHandler(history_callback,           pattern="^history_"))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, menu_handler))
    application.add_handler(
    CallbackQueryHandler(handle_callback_query)
    )
    application.add_handler(
    CallbackQueryHandler(
        refresh_ichancy_account_callback,
        pattern="^refresh_ichancy_account$"
    ))
    application.add_handler(CommandHandler("developer", developer_command))
    

    print("✅ البوت يعمل الآن...")
    application.run_polling()

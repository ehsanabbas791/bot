import os
import sqlite3
import random
import unicodedata
from contextlib import contextmanager
from typing import Optional

DB_PATH = os.getenv("BOT_DB_PATH", "new_databas_one.db")

# =========================================================
# توحيد رقم العملية (يُستخدم في Python وفي SQL معاً)
# =========================================================

_ARABIC_TO_ASCII_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)
_INVISIBLE_CHARS = {
    "\u200b", "\u200c", "\u200d", "\u200e", "\u200f",
    "\u202a", "\u202b", "\u202c", "\u2060", "\ufeff",
}


def _normalize_txid(txid) -> str:
    """
    توحيد رقم/هاش العملية قبل المقارنة.

    أمثلة:
        " 123456 "      -> "123456"
        "0000123456"    -> "123456"   (الأصفار البادئة لا تغيّر العملية)
        "١٢٣٤٥٦"        -> "123456"   (أرقام عربية)
        "0xABCDEF"      -> "abcdef"
        "ABCDEF"        -> "abcdef"

    الهدف: منع استخدام نفس العملية بأكثر من صيغة.
    """
    value = unicodedata.normalize("NFKC", str(txid or ""))
    value = value.translate(_ARABIC_TO_ASCII_DIGITS)
    value = "".join(
        ch for ch in value
        if not ch.isspace() and ch not in _INVISIBLE_CHARS
    ).lower()

    if value.startswith("0x"):
        value = value[2:]

    if value.isascii() and value.isdigit():
        value = value.lstrip("0") or "0"

    return value


normalize_txid = _normalize_txid  # اسم عام للاستيراد من الملفات الأخرى


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=20000")
    conn.create_function("norm_txid", 1, _normalize_txid)
    return conn


@contextmanager
def _db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# =========================================================
# إنشاء / تحديث قاعدة البيانات
# =========================================================

with _db() as conn:

    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        balance INTEGER DEFAULT 0,
        spin_used BOOLEAN DEFAULT 0,
        ichancy_username TEXT DEFAULT NULL,
        ichancy_password TEXT DEFAULT NULL,
        agreed INTEGER DEFAULT 0,
        last_spin_date TEXT DEFAULT NULL
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS recharge_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        transaction_id TEXT NOT NULL,
        method TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        approved_at TIMESTAMP DEFAULT NULL
    )
    """)
# =========================================================
# سجل دائم لكل أرقام عمليات الشحن المستخدمة
# =========================================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recharge_transaction_ledger (
            transaction_id_normalized TEXT PRIMARY KEY,
            transaction_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            method TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            recharge_id INTEGER DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_at TIMESTAMP DEFAULT NULL
        )
    """)

    # السجل الدائم لا يمكن حذف أي صف منه (حماية ضد إعادة فتح عملية قديمة)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_ledger_no_delete
        BEFORE DELETE ON recharge_transaction_ledger
        BEGIN
            SELECT RAISE(ABORT, 'recharge ledger rows are permanent');
        END
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS withdraw_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        account TEXT,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    conn.execute("""
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('usdt_rate', '147')
    """)

    conn.execute("""
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('shamcash_usd_rate', '147')
    """)

    # =====================================================
    # جداول الاسترداد الأسبوعي (Cashback)
    # =====================================================

    conn.execute("""
    CREATE TABLE IF NOT EXISTS ichancy_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        direction TEXT NOT NULL,
        amount_nsp REAL NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_ichancy_tx_user
    ON ichancy_transactions(user_id, created_at)
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS cashback_snapshots (
        user_id INTEGER PRIMARY KEY,
        last_balance_nsp REAL DEFAULT 0,
        last_run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS cashback_payouts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        loss_amount INTEGER NOT NULL,
        cashback_amount INTEGER NOT NULL,
        period_start TIMESTAMP,
        period_end TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # =====================================================
    # جداول عمولة الإحالة المستمرة (Referral Commission)
    # =====================================================

    conn.execute("""
    CREATE TABLE IF NOT EXISTS referral_commissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER NOT NULL,
        referred_user_id INTEGER NOT NULL,
        recharge_amount INTEGER NOT NULL,
        commission_amount INTEGER NOT NULL,
        paid INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        paid_at TIMESTAMP DEFAULT NULL
    )
    """)

    conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_referral_commissions_referrer
    ON referral_commissions(referrer_id, paid)
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS referral_payout_cycles (
        referrer_id INTEGER PRIMARY KEY,
        cycle_start_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # =====================================================
    # جدول الإحالات
    # =====================================================

    conn.execute("""
    CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        referrer_id INTEGER NOT NULL,

        referred_user_id INTEGER NOT NULL UNIQUE,

        status TEXT DEFAULT 'pending',

        reward INTEGER DEFAULT 0,

        rewarded INTEGER DEFAULT 0,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        activated_at TIMESTAMP DEFAULT NULL,

        UNIQUE(referrer_id, referred_user_id)
    )
    """)
########################################
########################################
    conn.execute("""
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('welcome_bonus_amount', '50')
    """)

    conn.execute("""
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('welcome_bonus_enabled', '1')
    """)

        # التحقق من وجود العمود وإضافته إن لم يكن موجوداً
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}

    if "received_welcome_bonus" not in user_cols:
        conn.execute("""
            ALTER TABLE users
             ADD COLUMN received_welcome_bonus INTEGER DEFAULT 0
        """)

    # =====================================================
    # تحديث recharge_requests القديمة
    # =====================================================

    cols = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(recharge_requests)"
        )
    }

    if "approved_at" not in cols:
        conn.execute("""
            ALTER TABLE recharge_requests
            ADD COLUMN approved_at TIMESTAMP DEFAULT NULL
        """)

    # =====================================================
    # تحديث users: عمود توقيت آخر لفة (لعد تنازلي حقيقي بالساعات)
    # =====================================================

    user_cols = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(users)"
        )
    }

    if "last_spin_at" not in user_cols:
        conn.execute("""
            ALTER TABLE users
            ADD COLUMN last_spin_at TIMESTAMP DEFAULT NULL
        """)

    # =====================================================
    # جدول جوائز عجلة الحظ (قابل للتعديل من لوحة الأدمن)
    # =====================================================

    conn.execute("""
    CREATE TABLE IF NOT EXISTS spin_prizes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        label TEXT NOT NULL,
        prize_type TEXT NOT NULL,
        amount INTEGER DEFAULT 0,
        weight INTEGER DEFAULT 1,
        active INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 0
    )
    """)

    existing_prizes = conn.execute("""
        SELECT COUNT(*) FROM spin_prizes
    """).fetchone()[0]

    if existing_prizes == 0:
        default_prizes = [
            ("🔄 لفة إضافية", "respin", 0, 15, 1),
            ("💰 10 ل.س", "cash", 10, 25, 2),
            ("💵 100 ل.س", "cash", 100, 8, 3),
            ("💎 2000 ل.س", "cash", 2000, 1, 4),
            ("😔 حظ أوفر", "none", 0, 40, 5),
            ("🎁 30 ل.س", "cash", 30, 5, 6),
        ]
        conn.executemany("""
            INSERT INTO spin_prizes (label, prize_type, amount, weight, sort_order)
            VALUES (?, ?, ?, ?, ?)
        """, default_prizes)

    # =====================================================
    # ترقية لمرة واحدة: تقصير نصوص الجوائز الافتراضية الطويلة
    # (فقط إذا لم يعدّلها الأدمن بعد) + استبدال "جائزة مميزة" بـ 3,000 ل.س
    # =====================================================

    already_migrated = conn.execute("""
        SELECT value FROM settings WHERE key='spin_prizes_migrated_v2'
    """).fetchone()

    if not already_migrated:

        conn.execute("""
            UPDATE spin_prizes
            SET label='🔄 لفة إضافية'
            WHERE label='🔄 لفة إضافية مجانية'
        """)

        conn.execute("""
            UPDATE spin_prizes
            SET label='😔 حظ أوفر'
            WHERE label='😔 حظ أوفر - جرب مرة تانية'
        """)

        conn.execute("""
            UPDATE spin_prizes
            SET label='🎁 30 ل.س', prize_type='cash', amount=30
            WHERE label='🎁 جائزة مميزة - تواصل مع الإدارة'
            AND prize_type='custom_message'
        """)

        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value)
            VALUES ('spin_prizes_migrated_v2', '1')
        """)

    # =====================================================
    # معالجة عمليات الشحن المكررة القديمة
    # =====================================================

    duplicates = conn.execute("""
        SELECT method, transaction_id, MIN(id) AS keep_id
        FROM recharge_requests
        WHERE transaction_id IS NOT NULL
        AND transaction_id != ''
        GROUP BY method, transaction_id
        HAVING COUNT(*) > 1
    """).fetchall()

    for method, transaction_id, keep_id in duplicates:

        conn.execute("""
            UPDATE recharge_requests
            SET status='duplicate'
            WHERE method=?
            AND transaction_id=?
            AND id<>?
        """, (method, transaction_id, keep_id))

    # =====================================================
    # منع تكرار نفس العملية
    # =====================================================

    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_recharge_method_txid
        ON recharge_requests(method, transaction_id)
    """)


# =========================================================
# USERS
# =========================================================

def get_user(user_id):

    with _db() as conn:

        row = conn.execute("""
            SELECT balance,
                   ichancy_username,
                   ichancy_password,
                   agreed
            FROM users
            WHERE user_id=?
        """, (user_id,)).fetchone()

        if row is None:

            conn.execute("""
                INSERT INTO users (user_id)
                VALUES (?)
            """, (user_id,))

            return {
                "balance": 0,
                "ichancy_username": None,
                "ichancy_password": None,
                "agreed": 0
            }

        return dict(row)


def get_user_recharges(user_id):

    with _db() as conn:

        rows = conn.execute("""
            SELECT amount,
                   method,
                   transaction_id,
                   created_at
            FROM recharge_requests
            WHERE user_id=?
            ORDER BY created_at DESC
        """, (user_id,)).fetchall()

        return [tuple(r) for r in rows]


def get_user_withdraws(user_id):

    with _db() as conn:

        rows = conn.execute("""
            SELECT amount,
                   account,
                   created_at
            FROM withdraw_requests
            WHERE user_id=?
            ORDER BY created_at DESC
        """, (user_id,)).fetchall()

        return [tuple(r) for r in rows]


def update_recharge_status(user_id, amount, status):

    with _db() as conn:

        conn.execute("""
            UPDATE recharge_requests
            SET status=?
            WHERE user_id=?
            AND amount=?
            AND status='pending'
        """, (status, user_id, amount))


def approve_withdraw_manual(user_id, amount):
    """
    اعتماد يدوي ذرّي لأقدم طلب سحب معلّق (user_id, amount): يتحقق من الحالة والرصيد، يخصم،
    ويضع approved في معاملة واحدة — فلا يمكن لضغطتين (أو لأدمنين) خصم المبلغ مرتين.
    يرجع {"status": "ok"|"not_pending"|"no_user"|"insufficient", ...}
    """
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT id FROM withdraw_requests
               WHERE user_id=? AND amount=? AND status='pending'
               ORDER BY id LIMIT 1""",
            (int(user_id), int(amount)),
        ).fetchone()
        if row is None:
            return {"status": "not_pending"}

        user = conn.execute("SELECT balance FROM users WHERE user_id=?", (int(user_id),)).fetchone()
        if user is None:
            return {"status": "no_user"}
        balance = int(user[0] or 0)
        if balance < int(amount):
            return {"status": "insufficient", "balance": balance}

        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (int(amount), int(user_id)))
        conn.execute(
            "UPDATE withdraw_requests SET status='approved', approved_at=CURRENT_TIMESTAMP WHERE id=?",
            (int(row[0]),),
        )
        return {"status": "ok", "id": int(row[0])}


def reject_withdraw(user_id, amount) -> bool:
    """رفض أقدم طلب سحب معلّق (user_id, amount). يرجع False إذا لم يكن هناك طلب معلّق (عولج مسبقاً)."""
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT id FROM withdraw_requests
               WHERE user_id=? AND amount=? AND status='pending'
               ORDER BY id LIMIT 1""",
            (int(user_id), int(amount)),
        ).fetchone()
        if row is None:
            return False
        conn.execute("UPDATE withdraw_requests SET status='rejected' WHERE id=?", (int(row[0]),))
        return True


def update_withdraw_status(user_id, amount, status):

    with _db() as conn:

        conn.execute("""
            UPDATE withdraw_requests
            SET status=?,
                approved_at = CASE WHEN ?='approved'
                                   THEN CURRENT_TIMESTAMP
                                   ELSE approved_at END
            WHERE user_id=?
            AND amount=?
            AND status='pending'
        """, (status, status, user_id, amount))


def update_ichancy_account(user_id, username, password):

    with _db() as conn:

        conn.execute("""
            UPDATE users
            SET ichancy_username=?,
                ichancy_password=?
            WHERE user_id=?
        """, (username, password, user_id))


def get_user_balance(user_id):

    with _db() as conn:

        row = conn.execute("""
            SELECT balance
            FROM users
            WHERE user_id=?
        """, (user_id,)).fetchone()

        if row is None:

            conn.execute("""
                INSERT INTO users (user_id)
                VALUES (?)
            """, (user_id,))

            return 0

        return int(row[0])


def update_balance(user_id, amount):

    with _db() as conn:

        conn.execute("""
            UPDATE users
            SET balance=balance+?
            WHERE user_id=?
        """, (int(amount), user_id))


def update_user_agreed(user_id, agreed: bool):

    with _db() as conn:

        conn.execute("""
            UPDATE users
            SET agreed=?
            WHERE user_id=?
        """, (1 if agreed else 0, user_id))

#---------------------------------------------------------
# DELETE user from database
#---------------------------------------------------------
def delete_user(user_id: int):
    """
    حذف المستخدم بشكل كامل من قاعدة البيانات وبكافة سجلاته المرتبطة.
    """
    with _db() as conn:
        conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM recharge_requests WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM withdraw_requests WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM ichancy_transactions WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM cashback_snapshots WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM cashback_payouts WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM gift_code_uses WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM referrals WHERE referrer_id=? OR referred_user_id=?", (user_id, user_id))
        conn.execute("DELETE FROM referral_commissions WHERE referrer_id=? OR referred_user_id=?", (user_id, user_id))
        conn.execute("DELETE FROM referral_payout_cycles WHERE referrer_id=?", (user_id,))
# =========================================================
# RECHARGE
# =========================================================

def create_recharge(user_id, amount, txid, method):

    with _db() as conn:

        cur = conn.execute("""
            INSERT INTO recharge_requests
                (user_id, amount, transaction_id, method)
            VALUES (?, ?, ?, ?)
        """, (
            user_id,
            int(amount),
            str(txid).strip(),
            method
        ))

        return cur.lastrowid




def has_approved_recharge(user_id) -> bool:
    """
    هل لدى المستخدم عملية شحن مؤكَّدة واحدة على الأقل؟
    (Syriatel / Sham تُسجَّل مؤكَّدة بعد التحقق الآلي،
     وUSDT تصبح مؤكَّدة بعد موافقة الأدمن — الطلبات المعلّقة لا تُحتسب.)
    """

    with _db() as conn:

        row = conn.execute("""
            SELECT 1
            FROM recharge_requests
            WHERE user_id=?
            AND status='approved'
            LIMIT 1
        """, (int(user_id),)).fetchone()

        return row is not None


# =========================================================
# APPROVE RECHARGE + REFERRAL
# =========================================================

def approve_verified_recharge(
    user_id: int,
    amount: int,
    txid: str,
    method: str,
    bonus_percent: float = 0.0,
):
    """
    اعتماد عملية شحن تم التحقق منها وإضافة الرصيد.

    الحماية هنا ذرّية Atomic:

    1. توحيد رقم العملية.
    2. محاولة حجزه في ledger.
    3. إذا كان موجوداً مسبقاً -> رفض.
    4. إذا لم يكن موجوداً -> تسجيل العملية.
    5. إضافة الرصيد.
    6. إذا حدث أي خطأ -> يتم rollback لكل شيء.

    وبالتالي لا يمكن إضافة نفس العملية مرتين.
    """

    txid = str(txid or "").strip()
    normalized = _normalize_txid(txid)
    method = str(method or "").strip()
    amount = int(amount)

    # =====================================================
    # التحقق من المدخلات
    # =====================================================

    if amount <= 0 or not txid or not normalized or not method:
        return {
            "ok": False,
            "status": "error",
            "message": "بيانات الشحن غير صالحة."
        }

    bonus = int(amount * float(bonus_percent))
    total = amount + bonus

    with _db() as conn:

        # قفل الكتابة من البداية: الفحص والحجز وإضافة الرصيد كلها
        # داخل معاملة واحدة، فلا يمكن لطلبين متزامنين تجاوز الفحص.
        conn.execute("BEGIN IMMEDIATE")

        # =================================================
        # 1) التأكد من عدم وجود العملية في السجل الدائم
        # =================================================

        existing = conn.execute("""
            SELECT
                transaction_id_normalized,
                transaction_id,
                user_id,
                amount,
                method,
                status,
                recharge_id,
                created_at,
                approved_at
            FROM recharge_transaction_ledger
            WHERE transaction_id_normalized=?
            LIMIT 1
        """, (normalized,)).fetchone()

        if existing:

            return {
                "ok": False,
                "status": "already_used",
                "message": "هذه العملية مستخدمة مسبقاً.",
                "existing": dict(existing)
            }

        # =================================================
        # 2) حماية إضافية من السجلات القديمة
        # =================================================

        existing = conn.execute("""
            SELECT
                id,
                user_id,
                amount,
                method,
                status,
                transaction_id
            FROM recharge_requests
            WHERE transaction_id IS NOT NULL
              AND norm_txid(transaction_id) = ?
            LIMIT 1
        """, (normalized,)).fetchone()

        if existing:

            return {
                "ok": False,
                "status": "already_used",
                "message": "هذه العملية مستخدمة مسبقاً.",
                "existing": dict(existing)
            }

        # =================================================
        # 3) إنشاء المستخدم إذا لم يكن موجوداً
        # =================================================

        conn.execute("""
            INSERT OR IGNORE INTO users (user_id)
            VALUES (?)
        """, (int(user_id),))

        # =================================================
        # 4) حجز رقم العملية
        #
        # هذه الخطوة هي القفل الحقيقي.
        # =================================================

        try:

            conn.execute("""
                INSERT INTO recharge_transaction_ledger
                (
                    transaction_id_normalized,
                    transaction_id,
                    user_id,
                    amount,
                    method,
                    status
                )
                VALUES (?, ?, ?, ?, ?, 'approved')
            """, (
                normalized,
                txid,
                int(user_id),
                amount,
                method
            ))

        except sqlite3.IntegrityError:

            existing = conn.execute("""
                SELECT
                    transaction_id_normalized,
                    transaction_id,
                    user_id,
                    amount,
                    method,
                    status,
                    recharge_id,
                    created_at,
                    approved_at
                FROM recharge_transaction_ledger
                WHERE transaction_id_normalized=?
                LIMIT 1
            """, (normalized,)).fetchone()

            return {
                "ok": False,
                "status": "already_used",
                "message": "هذه العملية مستخدمة مسبقاً.",
                "existing": dict(existing) if existing else None
            }

        # =================================================
        # 5) تسجيل عملية الشحن كـ approved
        # =================================================

        try:

            cur = conn.execute("""
                INSERT INTO recharge_requests
                (
                    user_id,
                    amount,
                    transaction_id,
                    method,
                    status,
                    approved_at
                )
                VALUES (?, ?, ?, ?, 'approved', CURRENT_TIMESTAMP)
            """, (
                int(user_id),
                amount,
                txid,
                method
            ))

        except sqlite3.IntegrityError:

            # return داخل with يعمل commit، لذلك نلغي المعاملة صراحةً
            # حتى لا يبقى حجز العملية في ledger بدون رصيد.
            conn.rollback()

            return {
                "ok": False,
                "status": "error",
                "message": "تعذر تسجيل عملية الشحن."
            }

        recharge_id = cur.lastrowid

        # =================================================
        # 6) ربط العملية بالسجل الدائم
        # =================================================

        conn.execute("""
            UPDATE recharge_transaction_ledger
            SET
                recharge_id=?,
                approved_at=CURRENT_TIMESTAMP
            WHERE transaction_id_normalized=?
        """, (
            recharge_id,
            normalized
        ))

        # =================================================
        # 6.5) كوبون عجلة الحظ «زيادة على الشحن» (إن وُجد) — ذرّي مع الشحن
        # =================================================

        coupon = _consume_recharge_coupon(conn, int(user_id), amount, recharge_id)

        if coupon:
            bonus += coupon["bonus"]
            total += coupon["bonus"]

        # تسجيل البونص الفعلي لهذه العملية (للتقرير اليومي)
        conn.execute(
            "UPDATE recharge_requests SET bonus=? WHERE id=?",
            (int(bonus), recharge_id)
        )

        # =================================================
        # 7) إضافة الرصيد
        # =================================================

        conn.execute("""
            UPDATE users
            SET balance = balance + ?
            WHERE user_id=?
        """, (
            total,
            int(user_id)
        ))

        # =================================================
        # 8) النتيجة
        # =================================================

        return {
            "ok": True,
            "status": "approved",
            "recharge_id": recharge_id,
            "amount": amount,
            "bonus": bonus,
            "total": total,
            "coupon_percent": coupon["percent"] if coupon else 0,
            "coupon_bonus": coupon["bonus"] if coupon else 0,
        }

# =========================================================
# PENDING RECHARGES (USDT — موافقة الأدمن) — ذرّية وآمنة من التكرار
# =========================================================

def _migrate_ledger_normalization():
    """
    إعادة توحيد مفاتيح السجل الدائم القديمة (مثلاً "00123" -> "123")
    حتى تعمل الحماية الجديدة على العمليات القديمة أيضاً.
    """
    with _db() as conn:
        rows = conn.execute("""
            SELECT transaction_id_normalized, transaction_id
            FROM recharge_transaction_ledger
        """).fetchall()

        for row in rows:
            old_key = row["transaction_id_normalized"]
            new_key = _normalize_txid(row["transaction_id"])

            if not new_key or new_key == old_key:
                continue

            taken = conn.execute("""
                SELECT 1 FROM recharge_transaction_ledger
                WHERE transaction_id_normalized=?
            """, (new_key,)).fetchone()

            if taken:
                continue

            conn.execute("""
                UPDATE recharge_transaction_ledger
                SET transaction_id_normalized=?
                WHERE transaction_id_normalized=?
            """, (new_key, old_key))


_migrate_ledger_normalization()

def transaction_already_used(txid, method=None):
    """
    التحقق مما إذا كان رقم العملية مستخدماً سابقاً.

    ملاحظة:
    method موجود فقط للتوافق مع الكود القديم،
    لكن التحقق الحقيقي أصبح عالمياً بين جميع طرق الدفع.

    يعني:
        Syriatel + 12345
        Sham Cash + 12345

    تعتبر نفس العملية.
    """

    normalized = _normalize_txid(txid)

    if not normalized:
        return None

    with _db() as conn:

        # ==========================================
        # 1) البحث في السجل الدائم
        # ==========================================
        row = conn.execute("""
            SELECT
                transaction_id_normalized,
                transaction_id,
                user_id,
                amount,
                method,
                status,
                recharge_id,
                created_at,
                approved_at
            FROM recharge_transaction_ledger
            WHERE transaction_id_normalized=?
            LIMIT 1
        """, (normalized,)).fetchone()

        if row:
            return dict(row)

        # ==========================================
        # 2) البحث في السجل القديم
        #    لحماية العمليات الموجودة قبل التعديل
        # ==========================================
        row = conn.execute("""
            SELECT
                id,
                user_id,
                amount,
                method,
                status,
                transaction_id
            FROM recharge_requests
            WHERE transaction_id IS NOT NULL
              AND norm_txid(transaction_id) = ?
            LIMIT 1
        """, (normalized,)).fetchone()

        if row:
            return dict(row)

        return None
    
def create_pending_recharge_once(user_id, amount, txid, method) -> dict:
    """
    إنشاء طلب شحن pending مع حجز رقم العملية بشكل دائم.

    بمجرد تسجيل العملية هنا:
    لا يمكن استخدام نفس العملية مرة ثانية،
    حتى لو:
    - حذف المستخدم حسابه
    - غيّر طريقة الدفع
    - أعاد إرسال العملية
    """

    txid = str(txid or "").strip()
    normalized = _normalize_txid(txid)
    method = str(method or "").strip()

    if not txid or not normalized or not method:
        return {
            "ok": False,
            "status": "invalid"
        }

    amount = int(amount)

    if amount <= 0:
        return {
            "ok": False,
            "status": "invalid_amount"
        }

    with _db() as conn:

        # قفل الكتابة من البداية: الفحص والحجز وإضافة الرصيد كلها
        # داخل معاملة واحدة، فلا يمكن لطلبين متزامنين تجاوز الفحص.
        conn.execute("BEGIN IMMEDIATE")

        # =================================================
        # 1) فحص السجل الدائم
        # =================================================
        existing = conn.execute("""
            SELECT
                transaction_id_normalized,
                transaction_id,
                user_id,
                amount,
                method,
                status,
                recharge_id,
                created_at,
                approved_at
            FROM recharge_transaction_ledger
            WHERE transaction_id_normalized=?
            LIMIT 1
        """, (normalized,)).fetchone()

        if existing:
            return {
                "ok": False,
                "status": "duplicate",
                "existing": dict(existing)
            }

        # =================================================
        # 2) فحص recharge_requests القديمة
        # =================================================
        existing = conn.execute("""
            SELECT
                id,
                user_id,
                amount,
                method,
                status,
                transaction_id
            FROM recharge_requests
            WHERE transaction_id IS NOT NULL
              AND norm_txid(transaction_id) = ?
            LIMIT 1
        """, (normalized,)).fetchone()

        if existing:
            return {
                "ok": False,
                "status": "duplicate",
                "existing": dict(existing)
            }

        # =================================================
        # 3) حجز رقم العملية بشكل دائم
        # =================================================
        try:

            conn.execute("""
                INSERT INTO recharge_transaction_ledger
                (
                    transaction_id_normalized,
                    transaction_id,
                    user_id,
                    amount,
                    method,
                    status
                )
                VALUES (?, ?, ?, ?, ?, 'pending')
            """, (
                normalized,
                txid,
                int(user_id),
                amount,
                method
            ))

        except sqlite3.IntegrityError:

            existing = conn.execute("""
                SELECT
                    transaction_id_normalized,
                    transaction_id,
                    user_id,
                    amount,
                    method,
                    status,
                    recharge_id,
                    created_at,
                    approved_at
                FROM recharge_transaction_ledger
                WHERE transaction_id_normalized=?
                LIMIT 1
            """, (normalized,)).fetchone()

            return {
                "ok": False,
                "status": "duplicate",
                "existing": dict(existing) if existing else None
            }

        # =================================================
        # 4) إنشاء طلب الشحن
        # =================================================
        try:

            cur = conn.execute("""
                INSERT INTO recharge_requests
                (
                    user_id,
                    amount,
                    transaction_id,
                    method,
                    status
                )
                VALUES (?, ?, ?, ?, 'pending')
            """, (
                int(user_id),
                amount,
                txid,
                method
            ))

        except sqlite3.IntegrityError:

            # return داخل with يعمل commit، لذلك نلغي المعاملة صراحةً
            # حتى لا يبقى الحجز في ledger.
            conn.rollback()

            return {
                "ok": False,
                "status": "duplicate",
                "existing": None
            }

        recharge_id = cur.lastrowid

        # =================================================
        # 5) ربط سجل العملية بطلب الشحن
        # =================================================
        conn.execute("""
            UPDATE recharge_transaction_ledger
            SET recharge_id=?
            WHERE transaction_id_normalized=?
        """, (
            recharge_id,
            normalized
        ))

        return {
            "ok": True,
            "status": "created",
            "recharge_id": recharge_id
        }

def get_pending_recharge(user_id, amount, recharge_id=None):
    """
    يرجع طلب الشحن المعلّق (dict) أو None.
    مع recharge_id: الطلب المحدد فقط (وبشرط أن يكون لنفس المستخدم).
    بدونه (أزرار قديمة): أقدم طلب معلّق لنفس المستخدم والمبلغ.
    """
    with _db() as conn:

        if recharge_id is not None:
            row = conn.execute("""
                SELECT id, user_id, amount, method, transaction_id, status
                FROM recharge_requests
                WHERE id=? AND user_id=? AND status='pending'
            """, (int(recharge_id), int(user_id))).fetchone()
        else:
            row = conn.execute("""
                SELECT id, user_id, amount, method, transaction_id, status
                FROM recharge_requests
                WHERE user_id=? AND amount=? AND status='pending'
                ORDER BY id ASC
                LIMIT 1
            """, (int(user_id), int(amount))).fetchone()

        return dict(row) if row else None


def approve_pending_recharge(
    recharge_id,
    credit_amount=None,
    bonus_percent=0.0
) -> dict:

    with _db() as conn:

        # قفل الكتابة من البداية: الفحص والحجز وإضافة الرصيد كلها
        # داخل معاملة واحدة، فلا يمكن لطلبين متزامنين تجاوز الفحص.
        conn.execute("BEGIN IMMEDIATE")

        # =================================================
        # جلب الطلب pending فقط
        # =================================================

        row = conn.execute("""
            SELECT
                id,
                user_id,
                amount,
                method,
                transaction_id
            FROM recharge_requests
            WHERE id=?
              AND status='pending'
        """, (
            int(recharge_id),
        )).fetchone()

        if not row:

            return {
                "ok": False,
                "status": "not_pending"
            }

        amount = (
            int(credit_amount)
            if credit_amount is not None
            else int(row["amount"])
        )

        if amount <= 0:

            return {
                "ok": False,
                "status": "invalid_amount"
            }

        bonus = int(
            amount * float(bonus_percent)
        )

        total = amount + bonus

        normalized = _normalize_txid(
            row["transaction_id"]
        )

        # =================================================
        # التأكد أن العملية مرتبطة بالسجل الدائم
        # =================================================

        ledger = conn.execute("""
            SELECT
                transaction_id_normalized,
                user_id,
                status
            FROM recharge_transaction_ledger
            WHERE transaction_id_normalized=?
            LIMIT 1
        """, (
            normalized,
        )).fetchone()

        if not ledger:

            # حماية إضافية:
            # لا نعتمد طلب USDT لا يوجد له سجل دائم.

            return {
                "ok": False,
                "status": "transaction_not_reserved"
            }

        # =================================================
        # اعتماد الطلب مرة واحدة فقط
        # =================================================

        cur = conn.execute("""
            UPDATE recharge_requests
            SET
                status='approved',
                approved_at=CURRENT_TIMESTAMP
            WHERE id=?
              AND status='pending'
        """, (
            int(recharge_id),
        ))

        if cur.rowcount != 1:

            return {
                "ok": False,
                "status": "not_pending"
            }

        # =================================================
        # تحديث ledger
        # =================================================

        conn.execute("""
            UPDATE recharge_transaction_ledger
            SET
                status='approved',
                amount=?,
                recharge_id=?,
                approved_at=CURRENT_TIMESTAMP
            WHERE transaction_id_normalized=?
        """, (
            amount,
            int(recharge_id),
            normalized
        ))

        # =================================================
        # التأكد من وجود المستخدم
        # =================================================

        conn.execute("""
            INSERT OR IGNORE INTO users (user_id)
            VALUES (?)
        """, (
            int(row["user_id"]),
        ))

        # =================================================
        # إضافة الرصيد
        # =================================================

        # كوبون عجلة الحظ «زيادة على الشحن» (إن وُجد) — ذرّي مع الاعتماد
        coupon = _consume_recharge_coupon(
            conn, int(row["user_id"]), amount, int(recharge_id)
        )

        if coupon:
            bonus += coupon["bonus"]
            total += coupon["bonus"]

        # تسجيل البونص الفعلي لهذه العملية (للتقرير اليومي)
        conn.execute(
            "UPDATE recharge_requests SET bonus=? WHERE id=?",
            (int(bonus), int(recharge_id))
        )

        conn.execute("""
            UPDATE users
            SET balance = balance + ?
            WHERE user_id=?
        """, (
            total,
            int(row["user_id"])
        ))

        return {
            "ok": True,
            "status": "approved",
            "recharge_id": int(recharge_id),
            "user_id": int(row["user_id"]),
            "amount": amount,
            "bonus": bonus,
            "total": total,
            "coupon_percent": coupon["percent"] if coupon else 0,
            "coupon_bonus": coupon["bonus"] if coupon else 0,
        }


def reject_pending_recharge(recharge_id) -> bool:
    """يرفض طلباً معلّقاً فقط. يرجع False إذا لم يعد معلّقاً (تمت معالجته)."""
    with _db() as conn:

        cur = conn.execute("""
            UPDATE recharge_requests
            SET status='rejected'
            WHERE id=? AND status='pending'
        """, (int(recharge_id),))

        return cur.rowcount == 1


# =========================================================
# WITHDRAW
# =========================================================

def create_withdraw(user_id, amount, account, method=None, fee_percent=None):

    with _db() as conn:

        cur = conn.execute("""
            INSERT INTO withdraw_requests
                (user_id, amount, account, method, fee_percent)
            VALUES (?, ?, ?, ?, ?)
        """, (
            user_id,
            int(amount),
            account,
            method,
            fee_percent
        ))

        return cur.lastrowid


# =========================================================
# GIFT CODES - أكواد الهدايا
# =========================================================

with _db() as conn:

    # جدول أكواد الهدايا
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gift_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            code TEXT NOT NULL UNIQUE,

            amount INTEGER NOT NULL,

            max_uses INTEGER NOT NULL DEFAULT 1,

            used_count INTEGER NOT NULL DEFAULT 0,

            active INTEGER NOT NULL DEFAULT 1,

            created_by INTEGER,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            expires_at TIMESTAMP DEFAULT NULL
        )
    """)

    # جدول الأشخاص الذين استخدموا الأكواد
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gift_code_uses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            gift_code_id INTEGER NOT NULL,

            user_id INTEGER NOT NULL,

            amount INTEGER NOT NULL,

            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(gift_code_id, user_id),

            FOREIGN KEY(gift_code_id)
                REFERENCES gift_codes(id)
        )
    """)

    # تسريع البحث عن استخدامات الكود
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_gift_code_uses_code
        ON gift_code_uses(gift_code_id)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_gift_codes_code
        ON gift_codes(code)
    """)

# =========================================================
# REPORTING SUPPORT — بيانات إضافية للتقرير اليومي (ترحيلات آمنة/idempotent)
# =========================================================

with _db() as conn:

    def _table_cols(_t):
        return {_r[1] for _r in conn.execute(f"PRAGMA table_info({_t})")}

    # تاريخ التسجيل (يُملأ تلقائياً عند أي INSERT جديد عبر trigger؛ القدامى يبقون NULL)
    if "created_at" not in _table_cols("users"):
        conn.execute("ALTER TABLE users ADD COLUMN created_at TIMESTAMP DEFAULT NULL")

    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_users_created_at
        AFTER INSERT ON users
        WHEN NEW.created_at IS NULL
        BEGIN
            UPDATE users SET created_at = CURRENT_TIMESTAMP WHERE user_id = NEW.user_id;
        END
    """)

    # بونص الشحن الفعلي المصروف مع كل عملية (نسبة + كوبون العجلة)
    if "bonus" not in _table_cols("recharge_requests"):
        conn.execute("ALTER TABLE recharge_requests ADD COLUMN bonus INTEGER DEFAULT NULL")

    # طريقة السحب + وقت الاعتماد
    _wd_cols = _table_cols("withdraw_requests")
    if "method" not in _wd_cols:
        conn.execute("ALTER TABLE withdraw_requests ADD COLUMN method TEXT DEFAULT NULL")
    if "approved_at" not in _wd_cols:
        conn.execute("ALTER TABLE withdraw_requests ADD COLUMN approved_at TIMESTAMP DEFAULT NULL")

    # سجل المكافآت التي لا تملك جدولاً خاصاً (ترحيبي، جوائز العجلة النقدية)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bonus_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            amount INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_bonus_events_kind_time
        ON bonus_events(kind, created_at)
    """)


def log_bonus_event(user_id, kind, amount):
    """تسجيل مكافأة مصروفة (للتقرير اليومي). kind: 'welcome' | 'spin'. لا تكسر المسار الأساسي أبداً."""
    try:
        amount = int(amount)
        if amount <= 0:
            return
        with _db() as conn:
            conn.execute(
                "INSERT INTO bonus_events (user_id, kind, amount) VALUES (?, ?, ?)",
                (int(user_id), str(kind), amount),
            )
    except Exception:
        pass


def user_exists(user_id) -> bool:
    """هل المستخدم مسجّل؟ (بدون إنشاء صف جديد مثل get_user)."""
    with _db() as conn:
        return conn.execute(
            "SELECT 1 FROM users WHERE user_id=?", (int(user_id),)
        ).fetchone() is not None


# =========================================================
# إدارة اللاعبين (لوحة الأدمن): عرض + تصفير الرصيد مع سجل تدقيق
# =========================================================

with _db() as conn:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_balance_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            old_balance INTEGER NOT NULL,
            new_balance INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


# =========================================================
# نظام الإحالات: اختيار اللاعب (هرمي / فردي) — مرة واحدة ونهائي، مع سجل تدقيق
# =========================================================
REFERRAL_PLANS = ("hierarchical", "individual")


def _migrate_referral_plan():
    with _db() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "referral_plan" not in cols:
            conn.execute("""
                ALTER TABLE users
                ADD COLUMN referral_plan TEXT DEFAULT NULL
                CHECK (referral_plan IN ('hierarchical', 'individual'))
            """)
        if "referral_plan_chosen_at" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN referral_plan_chosen_at TIMESTAMP DEFAULT NULL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS referral_plan_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                old_plan TEXT,
                new_plan TEXT,
                actor TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


_migrate_referral_plan()


def get_user_referral_plan(user_id):
    """نظام الإحالات المختار ('hierarchical' | 'individual') أو None إن لم يختر بعد / غير موجود."""
    with _db() as conn:
        row = conn.execute(
            "SELECT referral_plan FROM users WHERE user_id=?", (int(user_id),)
        ).fetchone()
        return row[0] if row else None


def set_user_referral_plan_once(user_id, plan):
    """اختيار اللاعب الأول — ذرّي ولا يمكن استبداله: UPDATE ... WHERE referral_plan IS NULL."""
    plan = str(plan or "").strip().lower()
    if plan not in REFERRAL_PLANS:
        return {"ok": False, "status": "invalid", "plan": None}

    user_id = int(user_id)
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))

        cur = conn.execute("""
            UPDATE users
            SET referral_plan=?, referral_plan_chosen_at=CURRENT_TIMESTAMP
            WHERE user_id=? AND referral_plan IS NULL
        """, (plan, user_id))

        if cur.rowcount == 1:
            conn.execute(
                "INSERT INTO referral_plan_log (user_id, old_plan, new_plan, actor) VALUES (?, NULL, ?, 'user')",
                (user_id, plan),
            )
            return {"ok": True, "status": "set", "plan": plan}

        row = conn.execute("SELECT referral_plan FROM users WHERE user_id=?", (user_id,)).fetchone()
        return {"ok": False, "status": "already_chosen", "plan": row[0] if row else None}


def admin_set_user_referral_plan(user_id, plan, actor):
    """تعديل نظام لاعب من الأدمن (plan=None يمسح الاختيار فيُسأل من جديد). يُسجَّل بسجل التدقيق."""
    if plan is not None and plan not in REFERRAL_PLANS:
        return {"ok": False, "status": "invalid"}

    user_id = int(user_id)
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT referral_plan FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return {"ok": False, "status": "not_found"}
        old = row[0]
        conn.execute("""
            UPDATE users
            SET referral_plan=?,
                referral_plan_chosen_at=CASE WHEN ? IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END
            WHERE user_id=?
        """, (plan, plan, user_id))
        conn.execute(
            "INSERT INTO referral_plan_log (user_id, old_plan, new_plan, actor) VALUES (?, ?, ?, ?)",
            (user_id, old, plan, f"admin:{int(actor)}"),
        )
        return {"ok": True, "status": "updated", "old": old, "new": plan}


# =========================================================
# نسبة (حسم) السحب: عامة + استثناء خاص بلاعب. الطلب يحتفظ بالنسبة وقت إنشائه (fee_percent).
# تُخزَّن ككسر (0.10 = 10%).
# =========================================================
DEFAULT_WITHDRAW_FEE_PERCENT = 0.10
MAX_WITHDRAW_FEE_PERCENT = 0.50          # حدّ أمان ضد أخطاء الإدخال (50%)
_WITHDRAW_FEE_KEY = "withdraw_fee_percent"


def _migrate_withdraw_fee():
    with _db() as conn:
        if "withdraw_fee_percent" not in {r[1] for r in conn.execute("PRAGMA table_info(users)")}:
            conn.execute("ALTER TABLE users ADD COLUMN withdraw_fee_percent REAL DEFAULT NULL")
        if "fee_percent" not in {r[1] for r in conn.execute("PRAGMA table_info(withdraw_requests)")}:
            conn.execute("ALTER TABLE withdraw_requests ADD COLUMN fee_percent REAL DEFAULT NULL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS withdraw_fee_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,                 -- NULL = النسبة العامة
                old_percent REAL,
                new_percent REAL,
                actor TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


_migrate_withdraw_fee()


def calc_withdraw_fee(amount, fee_percent) -> int:
    """حسم السحب بالليرة (تقريب للأسفل، بحساب عشري لتفادي أخطاء الفاصلة العائمة)."""
    from decimal import Decimal, ROUND_DOWN
    return int((Decimal(int(amount)) * Decimal(str(fee_percent))).to_integral_value(rounding=ROUND_DOWN))


def get_global_withdraw_fee_percent() -> float:
    with _db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (_WITHDRAW_FEE_KEY,)).fetchone()
    try:
        return float(row[0]) if row and row[0] is not None else DEFAULT_WITHDRAW_FEE_PERCENT
    except (TypeError, ValueError):
        return DEFAULT_WITHDRAW_FEE_PERCENT


def get_user_withdraw_fee_override(user_id):
    """النسبة الخاصة بلاعب (كسر) أو None إن كان يتبع النسبة العامة."""
    with _db() as conn:
        row = conn.execute("SELECT withdraw_fee_percent FROM users WHERE user_id=?", (int(user_id),)).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def get_withdraw_fee_percent(user_id=None) -> float:
    """النسبة الفعلية: خاصة باللاعب إن وُجدت، وإلا العامة."""
    if user_id is not None:
        override = get_user_withdraw_fee_override(user_id)
        if override is not None:
            return override
    return get_global_withdraw_fee_percent()


def _valid_fee(fraction) -> bool:
    return fraction is not None and 0 <= float(fraction) <= MAX_WITHDRAW_FEE_PERCENT


def set_global_withdraw_fee_percent(fraction, actor):
    if not _valid_fee(fraction):
        return {"ok": False, "status": "invalid"}
    fraction = round(float(fraction), 4)
    old = get_global_withdraw_fee_percent()
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (_WITHDRAW_FEE_KEY, str(fraction)))
        conn.execute(
            "INSERT INTO withdraw_fee_log (user_id, old_percent, new_percent, actor) VALUES (NULL, ?, ?, ?)",
            (old, fraction, f"admin:{int(actor)}"),
        )
    return {"ok": True, "status": "updated", "old": old, "new": fraction}


def set_user_withdraw_fee_percent(user_id, fraction, actor):
    """fraction=None يزيل الاستثناء (يتبع النسبة العامة). اللاعب يجب أن يكون مسجّلاً."""
    if fraction is not None and not _valid_fee(fraction):
        return {"ok": False, "status": "invalid"}
    fraction = None if fraction is None else round(float(fraction), 4)
    user_id = int(user_id)
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT withdraw_fee_percent FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return {"ok": False, "status": "not_found"}
        old = float(row[0]) if row[0] is not None else None
        conn.execute("UPDATE users SET withdraw_fee_percent=? WHERE user_id=?", (fraction, user_id))
        conn.execute(
            "INSERT INTO withdraw_fee_log (user_id, old_percent, new_percent, actor) VALUES (?, ?, ?, ?)",
            (user_id, old, fraction, f"admin:{int(actor)}"),
        )
    return {"ok": True, "status": "updated", "old": old, "new": fraction}


def count_withdraw_fee_overrides() -> int:
    with _db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM users WHERE withdraw_fee_percent IS NOT NULL").fetchone()[0])


def get_user_overview(user_id):
    """ملخص لاعب بدون إنشاء صف جديد. يرجع None إذا غير مسجّل."""
    with _db() as conn:
        row = conn.execute(
            "SELECT balance, ichancy_username, agreed FROM users WHERE user_id=?",
            (int(user_id),),
        ).fetchone()
        if row is None:
            return None
        return {
            "balance": int(row[0] or 0),
            "ichancy_username": row[1],
            "agreed": int(row[2] or 0),
        }


def zero_user_balance(user_id, admin_id) -> dict:
    """
    تصفير رصيد لاعب (ذرّي) وتسجيل العملية في admin_balance_log.
    يرجع {"status": "zeroed", "old_balance": N} أو {"status": "not_found"}.
    """
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT balance FROM users WHERE user_id=?", (int(user_id),)
        ).fetchone()
        if row is None:
            return {"status": "not_found", "old_balance": 0}

        old = int(row[0] or 0)
        conn.execute("UPDATE users SET balance=0 WHERE user_id=?", (int(user_id),))
        conn.execute(
            """INSERT INTO admin_balance_log
               (admin_id, user_id, action, old_balance, new_balance)
               VALUES (?, ?, 'zero', ?, 0)""",
            (int(admin_id), int(user_id), old),
        )
        return {"status": "zeroed", "old_balance": old}


def get_user_recharge_overview(user_id, limit=4) -> dict:
    """
    ملخص شحن الزبون من السجل الدائم (يشمل التلقائي والموافقات اليدوية، المقبول فقط):
      recent: آخر limit عمليات [{amount, method, at}] (الأحدث أولاً)
      by_method: {method: {"count": N, "total": مجموع}} لكل عمليات الشحن
    """
    with _db() as conn:
        recent_rows = conn.execute(
            """SELECT amount, method, COALESCE(approved_at, created_at)
               FROM recharge_transaction_ledger
               WHERE user_id=? AND status='approved'
               ORDER BY COALESCE(approved_at, created_at) DESC, rowid DESC
               LIMIT ?""",
            (int(user_id), int(limit)),
        ).fetchall()
        method_rows = conn.execute(
            """SELECT method, COUNT(*), COALESCE(SUM(amount), 0)
               FROM recharge_transaction_ledger
               WHERE user_id=? AND status='approved'
               GROUP BY method""",
            (int(user_id),),
        ).fetchall()

    return {
        "recent": [{"amount": int(r[0]), "method": r[1], "at": r[2]} for r in recent_rows],
        "by_method": {r[0]: {"count": int(r[1]), "total": int(r[2])} for r in method_rows},
    }


def admin_adjust_balance(user_id, admin_id, amount, action) -> dict:
    """
    إضافة/خصم رصيد يدوي من الأدمن (ذرّي) + تسجيله في admin_balance_log.
    action: 'add' أو 'sub'. يرجع:
      {"status": "ok", "old": N, "new": M} | {"status": "not_found"}
      | {"status": "insufficient", "balance": N} | {"status": "invalid"}
    """
    amount = int(amount)
    if action not in ("add", "sub") or amount <= 0:
        return {"status": "invalid"}

    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT balance FROM users WHERE user_id=?", (int(user_id),)
        ).fetchone()
        if row is None:
            return {"status": "not_found"}

        old = int(row[0] or 0)
        new = old + amount if action == "add" else old - amount
        if new < 0:
            return {"status": "insufficient", "balance": old}

        conn.execute("UPDATE users SET balance=? WHERE user_id=?", (new, int(user_id)))
        conn.execute(
            """INSERT INTO admin_balance_log
               (admin_id, user_id, action, old_balance, new_balance)
               VALUES (?, ?, ?, ?, ?)""",
            (int(admin_id), int(user_id), action, old, new),
        )
        return {"status": "ok", "old": old, "new": new}


# =========================================================
# USERS ADMIN
# =========================================================

def get_all_users():

    with _db() as conn:

        rows = conn.execute("""
            SELECT user_id,
                   balance,
                   ichancy_username
            FROM users
            WHERE agreed=1
            ORDER BY balance DESC
        """).fetchall()

        return [tuple(r) for r in rows]


# =========================================================
# SPIN
# =========================================================

def get_last_spin_date(user_id):

    with _db() as conn:

        row = conn.execute("""
            SELECT last_spin_date
            FROM users
            WHERE user_id=?
        """, (user_id,)).fetchone()

        return row[0] if row and row[0] else None


def update_last_spin_date(user_id, date):

    with _db() as conn:

        conn.execute("""
            INSERT OR IGNORE INTO users (user_id)
            VALUES (?)
        """, (user_id,))

        conn.execute("""
            UPDATE users
            SET last_spin_date=?
            WHERE user_id=?
        """, (
            date,
            user_id
        ))


def get_last_spin_at(user_id):
    """
    توقيت آخر لفة (كنص SQLite TIMESTAMP) أو None إذا لم يلف بعد.
    """

    with _db() as conn:

        row = conn.execute("""
            SELECT last_spin_at
            FROM users
            WHERE user_id=?
        """, (int(user_id),)).fetchone()

        return row[0] if row and row[0] else None


def update_last_spin_at(user_id, reset=False):
    """
    تحديث توقيت آخر لفة إلى الآن. إذا reset=True يصفّر التوقيت (None)
    ليصبح المستخدم مؤهلاً للفة فورية (يُستخدم عند ربح لفة إضافية).
    """

    with _db() as conn:

        conn.execute("""
            INSERT OR IGNORE INTO users (user_id)
            VALUES (?)
        """, (int(user_id),))

        if reset:
            conn.execute("""
                UPDATE users
                SET last_spin_at=NULL
                WHERE user_id=?
            """, (int(user_id),))
        else:
            conn.execute("""
                UPDATE users
                SET last_spin_at=CURRENT_TIMESTAMP
                WHERE user_id=?
            """, (int(user_id),))


# الحد الأدنى لمدة الانتظار بين لفتين (بالساعات) — لا يمكن تخفيضه من لوحة الأدمن
SPIN_MIN_COOLDOWN_HOURS = 24


def get_spin_cooldown_hours() -> int:

    with _db() as conn:

        row = conn.execute("""
            SELECT value
            FROM settings
            WHERE key='spin_cooldown_hours'
        """).fetchone()

        stored = int(row[0]) if row else SPIN_MIN_COOLDOWN_HOURS

        return max(SPIN_MIN_COOLDOWN_HOURS, stored)


def try_claim_spin(user_id, cooldown_hours) -> bool:
    """
    حجز اللفة بشكل ذرّي (Atomic): تنجح فقط إذا لم يلف المستخدم أبداً،
    أو مرّت مدة الانتظار كاملة على آخر لفة. تمنع تكرار اللفة عند الضغط
    المتزامن أو السريع على الزر.

    ترجع True إذا تم الحجز (وتم تسجيل توقيت اللفة الآن)، وإلا False.
    """

    hours = max(SPIN_MIN_COOLDOWN_HOURS, int(cooldown_hours))

    with _db() as conn:

        conn.execute("""
            INSERT OR IGNORE INTO users (user_id)
            VALUES (?)
        """, (int(user_id),))

        cur = conn.execute("""
            UPDATE users
            SET last_spin_at=CURRENT_TIMESTAMP
            WHERE user_id=?
            AND (
                last_spin_at IS NULL
                OR last_spin_at <= datetime('now', ?)
            )
        """, (int(user_id), f"-{hours} hours"))

        return cur.rowcount == 1


def set_spin_cooldown_hours(hours: int):

    hours = int(hours)

    if hours < SPIN_MIN_COOLDOWN_HOURS:
        raise ValueError(
            f"مدة الانتظار لا يمكن أن تقل عن {SPIN_MIN_COOLDOWN_HOURS} ساعة."
        )

    with _db() as conn:

        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value)
            VALUES ('spin_cooldown_hours', ?)
        """, (str(int(hours)),))


# ---------------------------------------------------------
# جوائز عجلة الحظ
# ---------------------------------------------------------

def get_active_spin_prizes():
    """
    الجوائز الفعّالة فقط، مرتبة لعرضها على العجلة.
    """

    with _db() as conn:

        rows = conn.execute("""
            SELECT id, label, prize_type, amount, weight, sort_order
            FROM spin_prizes
            WHERE active=1
            ORDER BY sort_order, id
        """).fetchall()

        return [dict(r) for r in rows]


def get_all_spin_prizes():
    """
    كل الجوائز (فعّالة وغير فعّالة) لعرضها بلوحة الأدمن.
    """

    with _db() as conn:

        rows = conn.execute("""
            SELECT id, label, prize_type, amount, weight, active, sort_order
            FROM spin_prizes
            ORDER BY sort_order, id
        """).fetchall()

        return [dict(r) for r in rows]


def add_spin_prize(label, prize_type, amount=0, weight=1):

    with _db() as conn:

        max_order = conn.execute("""
            SELECT COALESCE(MAX(sort_order), 0) FROM spin_prizes
        """).fetchone()[0]

        conn.execute("""
            INSERT INTO spin_prizes (label, prize_type, amount, weight, sort_order)
            VALUES (?, ?, ?, ?, ?)
        """, (
            str(label),
            str(prize_type),
            int(amount),
            int(weight),
            int(max_order) + 1,
        ))


def set_spin_prize_active(prize_id, active: bool):

    with _db() as conn:

        conn.execute("""
            UPDATE spin_prizes
            SET active=?
            WHERE id=?
        """, (1 if active else 0, int(prize_id)))


def update_spin_prize_weight(prize_id, weight: int):

    with _db() as conn:

        conn.execute("""
            UPDATE spin_prizes
            SET weight=?
            WHERE id=?
        """, (int(weight), int(prize_id)))


def delete_spin_prize(prize_id):

    with _db() as conn:

        conn.execute("""
            DELETE FROM spin_prizes
            WHERE id=?
        """, (int(prize_id),))


def get_spin_prize(prize_id):

    with _db() as conn:

        row = conn.execute("""
            SELECT id, label, prize_type, amount, weight, active, sort_order
            FROM spin_prizes
            WHERE id=?
        """, (int(prize_id),)).fetchone()

        return dict(row) if row else None


def pick_random_spin_prize():
    """
    اختيار جائزة عشوائياً من الجوائز الفعّالة، بالاعتماد على الوزن
    النسبي (weight) لكل جائزة. القرار هنا من السيرفر حصراً — لا يُسمح
    لواجهة العجلة (WebApp) بتحديد النتيجة أبداً.
    """

    prizes = get_active_spin_prizes()

    if not prizes:
        return None

    weights = [max(0, p["weight"]) for p in prizes]

    if sum(weights) <= 0:
        return None

    return random.choices(prizes, weights=weights, k=1)[0]


# =========================================================
# USDT RATE
# =========================================================

def get_usdt_rate() -> int:

    with _db() as conn:

        row = conn.execute("""
            SELECT value
            FROM settings
            WHERE key='usdt_rate'
        """).fetchone()

        return int(row[0]) if row else 147


def set_usdt_rate(rate: int):

    with _db() as conn:

        conn.execute("""
            INSERT OR REPLACE INTO settings
                (key,value)
            VALUES ('usdt_rate',?)
        """, (
            str(int(rate)),
        ))


# =========================================================
# SERVICE NOTICES (رسائل الخدمة الديناميكية — تحجب القسم عند تفعيلها)
# =========================================================

# القسم -> اسم العرض. "general" تحجب كل الأقسام الأخرى.
SERVICE_SECTIONS = {
    "recharge": "📥 شحن رصيد البوت",
    "withdraw": "📤 سحب رصيد البوت",
    "transfer": "🔁 شحن/سحب حساب iChancy",
    "account":  "🧾 حساب iChancy",
    "spin":     "🎰 اللفة المجانية",
    "general":  "🌐 رسالة عامة (كل الأقسام)",
}

NOTICE_MAX_LENGTH = 1000


def _check_section(section: str):
    if section not in SERVICE_SECTIONS:
        raise ValueError(f"قسم غير معروف: {section}")


def get_notice_state(section: str):
    """يرجع (enabled: bool, text: str) لقسم معيّن."""
    _check_section(section)

    with _db() as conn:

        rows = dict(conn.execute("""
            SELECT key, value
            FROM settings
            WHERE key IN (?, ?)
        """, (f"notice_{section}_text", f"notice_{section}_on")).fetchall())

    text = rows.get(f"notice_{section}_text") or ""
    enabled = rows.get(f"notice_{section}_on") == "1" and bool(text.strip())

    return enabled, text


def get_active_service_notice(section: str):
    """
    نص الرسالة الفعّالة التي تحجب هذا القسم (الخاصة بالقسم أولاً، ثم العامة)،
    أو None إذا لا يوجد حجب.
    """
    _check_section(section)

    for key in (section, "general"):
        enabled, text = get_notice_state(key)
        if enabled:
            return text.strip()

    return None


def set_notice_text(section: str, text: str):
    """يحفظ نص الرسالة (لا يفعّلها تلقائياً)."""
    _check_section(section)

    text = (text or "").strip()

    if not text:
        raise ValueError("نص الرسالة فارغ.")

    if len(text) > NOTICE_MAX_LENGTH:
        raise ValueError(f"الرسالة أطول من {NOTICE_MAX_LENGTH} حرف.")

    with _db() as conn:

        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value)
            VALUES (?, ?)
        """, (f"notice_{section}_text", text))


def set_notice_enabled(section: str, enabled: bool) -> bool:
    """
    يفعّل/يوقف الحجب. التفعيل مرفوض إذا لا يوجد نص.
    يرجع الحالة النهائية الفعلية.
    """
    _check_section(section)

    _, text = get_notice_state(section)

    if enabled and not text.strip():
        return False

    with _db() as conn:

        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value)
            VALUES (?, ?)
        """, (f"notice_{section}_on", "1" if enabled else "0"))

    return bool(enabled)


def delete_notice(section: str):
    """يحذف نص الرسالة ويوقفها."""
    _check_section(section)

    with _db() as conn:

        conn.execute("""
            DELETE FROM settings
            WHERE key IN (?, ?)
        """, (f"notice_{section}_text", f"notice_{section}_on"))


def any_service_notice_active() -> bool:
    return any(get_notice_state(sec)[0] for sec in SERVICE_SECTIONS)


# =========================================================
# SHAM CASH USD RATE
# =========================================================

DEFAULT_SHAMCASH_USD_RATE = 147


def get_shamcash_usd_rate() -> int:
    """
    سعر الدولار (Sham Cash) بوحدة رصيد البوت — قابل للتعديل من لوحة الأدمن.
    """

    with _db() as conn:

        row = conn.execute("""
            SELECT value
            FROM settings
            WHERE key='shamcash_usd_rate'
        """).fetchone()

        try:
            rate = int(row[0]) if row else DEFAULT_SHAMCASH_USD_RATE
        except (TypeError, ValueError):
            return DEFAULT_SHAMCASH_USD_RATE

        return rate if rate > 0 else DEFAULT_SHAMCASH_USD_RATE


def set_shamcash_usd_rate(rate: int):

    rate = int(rate)

    if rate <= 0:
        raise ValueError("سعر الصرف يجب أن يكون أكبر من صفر.")

    with _db() as conn:

        conn.execute("""
            INSERT OR REPLACE INTO settings
                (key, value)
            VALUES ('shamcash_usd_rate', ?)
        """, (str(rate),))


# =========================================================
# ================= بونصات الشحن ==========================
# =========================================================

# القيم الافتراضية (تُستخدم فقط إذا لم يتم ضبط نسبة من لوحة الأدمن بعد)
DEFAULT_BONUS_PERCENTS = {
    "syriatel": 0.0,
    "shamcash": 0.05,
    "usdt": 0.08,
}


def get_auto_withdraw() -> bool:
    """هل السحب التلقائي لـ Sham Cash مفعّل (يحاول البوت الدفع أولاً بدون أدمن)؟ الافتراضي: متوقف."""
    with _db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='auto_withdraw_sham'").fetchone()
    return row is not None and str(row[0]).strip() == "1"


def set_auto_withdraw(enabled: bool):
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('auto_withdraw_sham', ?)",
            ("1" if enabled else "0",),
        )


def get_manual_check(method_key: str) -> bool:
    """هل التشييك اليدوي مفعّل لهذه الطريقة؟ method_key: 'syriatel' أو 'shamcash'."""
    with _db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?",
            (f"manual_check_{method_key}",),
        ).fetchone()
    return row is not None and str(row[0]).strip() == "1"


def set_manual_check(method_key: str, enabled: bool):
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (f"manual_check_{method_key}", "1" if enabled else "0"),
        )


def get_bonus_percent(method_key: str) -> float:
    """
    جلب نسبة البونص (كنسبة عشرية، مثال: 0.08 = 8%) لطريقة شحن معينة.
    """

    with _db() as conn:

        row = conn.execute("""
            SELECT value
            FROM settings
            WHERE key=?
        """, (f"bonus_percent_{method_key}",)).fetchone()

        if row is not None:
            return float(row[0])

        return DEFAULT_BONUS_PERCENTS.get(method_key, 0.0)


def set_bonus_percent(method_key: str, percent: float):
    """
    حفظ نسبة البونص الجديدة لطريقة شحن معينة (كنسبة عشرية، مثال: 0.08 = 8%).
    """

    with _db() as conn:

        conn.execute("""
            INSERT OR REPLACE INTO settings
                (key, value)
            VALUES (?, ?)
        """, (
            f"bonus_percent_{method_key}",
            str(float(percent)),
        ))


def get_all_bonus_percents() -> dict:
    """
    جلب كل نسب البونص دفعة واحدة (لعرضها في لوحة الأدمن).
    """

    return {
        key: get_bonus_percent(key)
        for key in DEFAULT_BONUS_PERCENTS
    }


# =========================================================
# ============= الاسترداد الأسبوعي (Cashback) =============
# =========================================================

def log_ichancy_transaction(user_id, direction, amount_nsp):
    """
    تسجيل أي حركة شحن/سحب فعلية بين رصيد البوت وحساب iChancy
    الخاص بالمستخدم. هذا السجل هو أساس حساب صافي الخسارة لاحقاً.

    direction: 'in'  → شحن لحساب iChancy (رصيد داخل)
               'out' → سحب من حساب iChancy (رصيد خارج)
    """

    if direction not in ("in", "out"):
        return

    with _db() as conn:

        conn.execute("""
            INSERT INTO ichancy_transactions
                (user_id, direction, amount_nsp)
            VALUES (?, ?, ?)
        """, (
            int(user_id),
            direction,
            float(amount_nsp),
        ))


def get_ichancy_movement_since(user_id, since_ts):
    """
    ترجع (مجموع الشحن, مجموع السحب) بالـ NSP لحساب iChancy
    الخاص بالمستخدم منذ توقيت معين.
    """

    with _db() as conn:

        row = conn.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN direction='in'  THEN amount_nsp ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN direction='out' THEN amount_nsp ELSE 0 END), 0)
            FROM ichancy_transactions
            WHERE user_id=?
            AND created_at > ?
        """, (int(user_id), since_ts)).fetchone()

        return float(row[0]), float(row[1])


def get_cashback_snapshot(user_id):
    """
    جلب آخر نقطة مرجعية (رصيد iChancy + توقيت) للمستخدم.
    ترجع None إذا لم توجد بعد (أول مرة).
    """

    with _db() as conn:

        row = conn.execute("""
            SELECT last_balance_nsp, last_run_at
            FROM cashback_snapshots
            WHERE user_id=?
        """, (int(user_id),)).fetchone()

        return dict(row) if row else None


def init_cashback_snapshot_if_missing(user_id, balance_nsp):
    """
    إنشاء نقطة مرجعية أولى للمستخدم إذا لم تكن موجودة (لا تُستبدل
    نقطة موجودة مسبقاً).
    """

    with _db() as conn:

        conn.execute("""
            INSERT OR IGNORE INTO cashback_snapshots
                (user_id, last_balance_nsp, last_run_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        """, (int(user_id), float(balance_nsp)))


def update_cashback_snapshot(user_id, balance_nsp):
    """
    تحديث نقطة المرجع بعد كل تنفيذ للاسترداد الأسبوعي.
    """

    with _db() as conn:

        conn.execute("""
            INSERT OR REPLACE INTO cashback_snapshots
                (user_id, last_balance_nsp, last_run_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        """, (int(user_id), float(balance_nsp)))


def record_cashback_payout(user_id, loss_amount, cashback_amount, period_start, period_end):
    """
    تسجيل عملية استرداد أسبوعي منفذة (لأغراض السجل والتدقيق).
    """

    with _db() as conn:

        conn.execute("""
            INSERT INTO cashback_payouts
                (user_id, loss_amount, cashback_amount, period_start, period_end)
            VALUES (?, ?, ?, ?, ?)
        """, (
            int(user_id),
            int(loss_amount),
            int(cashback_amount),
            period_start,
            period_end,
        ))


def get_cashback_percent() -> float:
    """
    نسبة الاسترداد الأسبوعي الحالية (كنسبة عشرية، مثال: 0.1 = 10%).
    القيمة الافتراضية 0 (متوقف) حتى يضبطها الأدمن من اللوحة.
    """

    with _db() as conn:

        row = conn.execute("""
            SELECT value
            FROM settings
            WHERE key='cashback_percent'
        """).fetchone()

        return float(row[0]) if row else 0.0


def set_cashback_percent(percent: float):

    with _db() as conn:

        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value)
            VALUES ('cashback_percent', ?)
        """, (str(float(percent)),))


def get_users_with_ichancy():
    """
    كل المستخدمين الذين لديهم حساب iChancy مربوط (المرشحون للاسترداد الأسبوعي).
    """

    with _db() as conn:

        rows = conn.execute("""
            SELECT user_id, ichancy_username
            FROM users
            WHERE ichancy_username IS NOT NULL
            AND ichancy_username != ''
        """).fetchall()

        return [tuple(r) for r in rows]


def get_recent_cashback_payouts(limit=15):
    """
    آخر عمليات الاسترداد المنفذة (لعرضها للأدمن).
    """

    with _db() as conn:

        rows = conn.execute("""
            SELECT user_id, loss_amount, cashback_amount, created_at
            FROM cashback_payouts
            ORDER BY created_at DESC
            LIMIT ?
        """, (int(limit),)).fetchall()

        return [dict(row) for row in rows]


# =========================================================
# ===== اختيار نظام التعويض لكل مستخدم (يومي / أسبوعي) =====
# =========================================================
#
# - المستخدم يختار مرة واحدة فقط عند أول استخدام، ولا يستطيع التعديل.
# - الأدمن وحده يستطيع تغيير اختيار مستخدم أو مسحه (يُسأل من جديد).
# - كل برنامج (يومي/أسبوعي) له مفتاح تفعيل مستقل من لوحة الأدمن.

CASHBACK_CHOICES = ("daily", "weekly")


def _migrate_cashback_choice():
    with _db() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}

        if "cashback_choice" not in cols:
            conn.execute("""
                ALTER TABLE users
                ADD COLUMN cashback_choice TEXT DEFAULT NULL
                CHECK (cashback_choice IN ('daily', 'weekly'))
            """)

        if "cashback_chosen_at" not in cols:
            conn.execute("""
                ALTER TABLE users
                ADD COLUMN cashback_chosen_at TIMESTAMP DEFAULT NULL
            """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS cashback_choice_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                old_choice TEXT,
                new_choice TEXT,
                actor TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


_migrate_cashback_choice()


def get_user_cashback_info(user_id):
    """(cashback_choice, cashback_chosen_at) أو None إذا المستخدم غير موجود."""
    with _db() as conn:
        row = conn.execute("""
            SELECT cashback_choice, cashback_chosen_at
            FROM users
            WHERE user_id=?
        """, (int(user_id),)).fetchone()

        return dict(row) if row else None


def get_user_cashback_choice(user_id):
    info = get_user_cashback_info(user_id)
    return info["cashback_choice"] if info else None


def set_user_cashback_choice_once(user_id, choice):
    """
    اختيار المستخدم الأول — ذرّي ولا يمكن استبداله:
    UPDATE ... WHERE cashback_choice IS NULL
    """
    choice = str(choice or "").strip().lower()

    if choice not in CASHBACK_CHOICES:
        return {"ok": False, "status": "invalid", "choice": None}

    user_id = int(user_id)

    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")

        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))

        cur = conn.execute("""
            UPDATE users
            SET cashback_choice=?, cashback_chosen_at=CURRENT_TIMESTAMP
            WHERE user_id=? AND cashback_choice IS NULL
        """, (choice, user_id))

        if cur.rowcount == 1:
            conn.execute("""
                INSERT INTO cashback_choice_log (user_id, old_choice, new_choice, actor)
                VALUES (?, NULL, ?, 'user')
            """, (user_id, choice))

            # نقطة مرجعية أسبوعية جديدة: لا نحتسب أي فترة سابقة
            conn.execute("DELETE FROM cashback_snapshots WHERE user_id=?", (user_id,))

            return {"ok": True, "status": "set", "choice": choice}

        row = conn.execute(
            "SELECT cashback_choice FROM users WHERE user_id=?", (user_id,)
        ).fetchone()

        return {
            "ok": False,
            "status": "already_chosen",
            "choice": row[0] if row else None,
        }


def admin_set_user_cashback_choice(user_id, choice, actor):
    """
    تعديل اختيار مستخدم من الأدمن (choice=None يمسح الاختيار فيُسأل من جديد).
    """
    if choice is not None and choice not in CASHBACK_CHOICES:
        return {"ok": False, "status": "invalid"}

    user_id = int(user_id)

    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            "SELECT cashback_choice FROM users WHERE user_id=?", (user_id,)
        ).fetchone()

        if row is None:
            return {"ok": False, "status": "not_found"}

        old = row[0]

        conn.execute("""
            UPDATE users
            SET cashback_choice=?,
                cashback_chosen_at=CASE WHEN ? IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END
            WHERE user_id=?
        """, (choice, choice, user_id))

        conn.execute("""
            INSERT INTO cashback_choice_log (user_id, old_choice, new_choice, actor)
            VALUES (?, ?, ?, ?)
        """, (user_id, old, choice, str(actor)))

        if old != choice:
            conn.execute("DELETE FROM cashback_snapshots WHERE user_id=?", (user_id,))

        return {"ok": True, "status": "updated", "old": old, "choice": choice}


def count_cashback_choices():
    """عدد المشتركين بكل نظام + عدد من وافقوا على الشروط ولم يختاروا بعد."""
    with _db() as conn:
        daily = conn.execute(
            "SELECT COUNT(*) FROM users WHERE cashback_choice='daily'"
        ).fetchone()[0]
        weekly = conn.execute(
            "SELECT COUNT(*) FROM users WHERE cashback_choice='weekly'"
        ).fetchone()[0]
        none = conn.execute(
            "SELECT COUNT(*) FROM users WHERE cashback_choice IS NULL AND agreed=1"
        ).fetchone()[0]

        return {"daily": daily, "weekly": weekly, "none": none}


def bulk_assign_unchosen_cashback(choice, actor):
    """تعيين نظام لكل من وافق على الشروط ولم يختر بعد. يرجع عدد المستخدمين."""
    if choice not in CASHBACK_CHOICES:
        return 0

    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")

        ids = [r[0] for r in conn.execute("""
            SELECT user_id FROM users
            WHERE cashback_choice IS NULL AND agreed=1
        """).fetchall()]

        for uid in ids:
            conn.execute("""
                UPDATE users
                SET cashback_choice=?, cashback_chosen_at=CURRENT_TIMESTAMP
                WHERE user_id=? AND cashback_choice IS NULL
            """, (choice, uid))
            conn.execute("""
                INSERT INTO cashback_choice_log (user_id, old_choice, new_choice, actor)
                VALUES (?, NULL, ?, ?)
            """, (uid, choice, str(actor)))
            conn.execute("DELETE FROM cashback_snapshots WHERE user_id=?", (uid,))

        return len(ids)


def get_users_with_ichancy_by_choice(choice):
    """مشتركو نظام معيّن ولديهم حساب iChancy مربوط: [(user_id, ichancy_username)]."""
    if choice not in CASHBACK_CHOICES:
        return []

    with _db() as conn:
        rows = conn.execute("""
            SELECT user_id, ichancy_username
            FROM users
            WHERE cashback_choice=?
            AND ichancy_username IS NOT NULL
            AND ichancy_username != ''
        """, (choice,)).fetchall()

        return [tuple(r) for r in rows]


# ---------------------------------------------------------
# تفعيل/إيقاف كل برنامج على حدة (بدل وضع واحد عام)
# ---------------------------------------------------------

def _program_keys(program):
    if program not in CASHBACK_CHOICES:
        raise ValueError("program must be 'daily' or 'weekly'")
    return f"cashback_prog_{program}_enabled", f"cashback_prog_{program}_started_at"


def get_cashback_program_enabled(program) -> bool:
    key, _ = _program_keys(program)
    with _db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return bool(row) and row[0] == "1"


def set_cashback_program_enabled(program, enabled: bool):
    """التفعيل يسجّل لحظة البداية (للمدة)، والإيقاف يمسحها."""
    key, started_key = _program_keys(program)

    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        was_on = bool(row) and row[0] == "1"

        if enabled and not was_on:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')", (key,))
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) "
                "VALUES (?, strftime('%Y-%m-%d %H:%M:%S','now'))", (started_key,)
            )
        elif not enabled:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, '0')", (key,))
            conn.execute("DELETE FROM settings WHERE key=?", (started_key,))


def get_program_started_at(program):
    _, started_key = _program_keys(program)
    with _db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (started_key,)).fetchone()
        return row[0] if row and row[0] else None


def ensure_program_started_at(program):
    _, started_key = _program_keys(program)
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM settings WHERE key=?", (started_key,)).fetchone()
        if row and row[0]:
            return row[0]
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) "
            "VALUES (?, strftime('%Y-%m-%d %H:%M:%S','now'))", (started_key,)
        )
        return conn.execute("SELECT value FROM settings WHERE key=?", (started_key,)).fetchone()[0]


def migrate_legacy_cashback_mode(legacy_mode, legacy_started_at):
    """
    مرة واحدة فقط: تحويل الوضع العام القديم (weekly/daily/off) إلى مفاتيح التفعيل الجديدة
    حتى لا يتوقف البرنامج الفعّال حالياً.
    """
    marker = "cashback_per_user_migrated_v1"

    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")

        if conn.execute("SELECT 1 FROM settings WHERE key=?", (marker,)).fetchone():
            return False

        if legacy_mode in CASHBACK_CHOICES:
            key, started_key = _program_keys(legacy_mode)
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')", (key,))
            if legacy_started_at:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (started_key, str(legacy_started_at)),
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) "
                    "VALUES (?, strftime('%Y-%m-%d %H:%M:%S','now'))", (started_key,)
                )

        conn.execute("INSERT INTO settings (key, value) VALUES (?, '1')", (marker,))
        return True


def apply_weekly_cashback(user_id, loss_amount, cashback_amount,
                          period_start, period_end, new_balance_nsp):
    """
    صرف الأسبوعي لمستخدم واحد بمعاملة واحدة:
    إضافة الرصيد + سجل الدفع + تحديث النقطة المرجعية.
    ترجع False إذا النقطة المرجعية تغيّرت (جولة متزامنة) فلا يُصرف شيء مرتين.
    """
    user_id = int(user_id)

    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")

        snap = conn.execute(
            "SELECT last_run_at FROM cashback_snapshots WHERE user_id=?", (user_id,)
        ).fetchone()

        if snap is None or str(snap[0]) != str(period_start):
            return False

        if int(cashback_amount) > 0:
            conn.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id=?",
                (int(cashback_amount), user_id),
            )
            conn.execute("""
                INSERT INTO cashback_payouts
                    (user_id, loss_amount, cashback_amount, period_start, period_end)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, int(loss_amount), int(cashback_amount), period_start, period_end))

        conn.execute("""
            INSERT OR REPLACE INTO cashback_snapshots
                (user_id, last_balance_nsp, last_run_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        """, (user_id, float(new_balance_nsp)))

        return True


# =========================================================
# ===== إهداء الرصيد بين المستخدمين (حد أدنى للمبلغ، بلا حد لعدد المرات) =====
# =========================================================
#
# - لا يوجد حد لعدد الهدايا التي يستلمها المستخدم (أُلغي حد الـ 3 مرات).
# - الحد الأدنى لمبلغ الإهداء الواحد: gift_min_amount (الافتراضي 300)، يعدّله الأدمن من لوحة التحكم.
# - تشغيل/إيقاف الإهداء: gift_enabled (للتوافق: إن لم يوجد وكان الحد القديم gift_max_receives = 0 فهو موقوف).
# - التحويل ذرّي: الخصم + الإضافة + تسجيل الهدية بمعاملة واحدة.
# - سجل balance_gifts دائم (لا يُحذف عند حذف المستخدم).

GIFT_MAX_RECEIVES_DEFAULT = 3          # (قديم) لم يعد يُطبَّق — يُترك للتوافق فقط
GIFT_MAX_RECEIVES_CEILING = 1000
GIFT_MIN_AMOUNT_DEFAULT = 300
GIFT_MIN_AMOUNT_CEILING = 1_000_000


def _migrate_gifts():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS balance_gifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_balance_gifts_receiver
            ON balance_gifts(receiver_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_balance_gifts_sender
            ON balance_gifts(sender_id)
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_balance_gifts_no_delete
            BEFORE DELETE ON balance_gifts
            BEGIN
                SELECT RAISE(ABORT, 'balance gifts rows are permanent');
            END
        """)


_migrate_gifts()


def _read_gift_max_receives(conn) -> int:
    row = conn.execute(
        "SELECT value FROM settings WHERE key='gift_max_receives'"
    ).fetchone()

    if row is None:
        return GIFT_MAX_RECEIVES_DEFAULT

    try:
        value = int(str(row[0]).strip())
    except (TypeError, ValueError):
        return GIFT_MAX_RECEIVES_DEFAULT

    if not (0 <= value <= GIFT_MAX_RECEIVES_CEILING):
        return GIFT_MAX_RECEIVES_DEFAULT

    return value


def get_gift_max_receives() -> int:
    with _db() as conn:
        return _read_gift_max_receives(conn)


def set_gift_max_receives(value: int) -> None:
    if isinstance(value, bool) or int(value) != value:
        raise ValueError("الحد يجب أن يكون عدداً صحيحاً.")

    value = int(value)

    if not (0 <= value <= GIFT_MAX_RECEIVES_CEILING):
        raise ValueError(f"الحد يجب أن يكون بين 0 و {GIFT_MAX_RECEIVES_CEILING}.")

    with _db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value)
            VALUES ('gift_max_receives', ?)
        """, (str(value),))


def _read_gift_min_amount(conn) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key='gift_min_amount'").fetchone()
    if row is None:
        return GIFT_MIN_AMOUNT_DEFAULT
    try:
        value = int(str(row[0]).strip())
    except (TypeError, ValueError):
        return GIFT_MIN_AMOUNT_DEFAULT
    return value if 0 <= value <= GIFT_MIN_AMOUNT_CEILING else GIFT_MIN_AMOUNT_DEFAULT


def get_gift_min_amount() -> int:
    with _db() as conn:
        return _read_gift_min_amount(conn)


def set_gift_min_amount(value: int) -> None:
    if isinstance(value, bool) or int(value) != value:
        raise ValueError("الحد الأدنى يجب أن يكون عدداً صحيحاً.")
    value = int(value)
    if not (0 <= value <= GIFT_MIN_AMOUNT_CEILING):
        raise ValueError(f"الحد الأدنى يجب أن يكون بين 0 و {GIFT_MIN_AMOUNT_CEILING:,}.")
    with _db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gift_min_amount', ?)", (str(value),))


def _read_gift_enabled(conn) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key='gift_enabled'").fetchone()
    if row is not None:
        return str(row[0]).strip() != "0"
    # توافق: كان الأدمن يوقف الإهداء بجعل الحد القديم 0
    legacy = conn.execute("SELECT value FROM settings WHERE key='gift_max_receives'").fetchone()
    try:
        return not (legacy is not None and int(str(legacy[0]).strip()) == 0)
    except (TypeError, ValueError):
        return True


def is_gift_enabled() -> bool:
    with _db() as conn:
        return _read_gift_enabled(conn)


def set_gift_enabled(enabled: bool) -> None:
    with _db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gift_enabled', ?)", ("1" if enabled else "0",))


def count_gifts_received(user_id) -> int:
    with _db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM balance_gifts WHERE receiver_id=?",
            (int(user_id),)
        ).fetchone()[0]


def _gift_receive_status(conn, target_id: int) -> dict:
    """هل يمكن إهداء هذا المستخدم الآن؟ (لا يوجد حد لعدد الهدايا المستلمة)."""
    if not _read_gift_enabled(conn):
        return {"status": "disabled", "received": 0}

    if conn.execute(
        "SELECT 1 FROM users WHERE user_id=?", (target_id,)
    ).fetchone() is None:
        return {"status": "target_not_found", "received": 0}

    received = conn.execute(
        "SELECT COUNT(*) FROM balance_gifts WHERE receiver_id=?", (target_id,)
    ).fetchone()[0]

    return {"status": "ok", "received": received}


def get_gift_receive_status(target_id) -> dict:
    """فحص مبكر (للواجهة): هل يستطيع هذا المستخدم استلام هدية الآن؟"""
    with _db() as conn:
        return _gift_receive_status(conn, int(target_id))


def transfer_gift(sender_id, target_id, amount) -> dict:
    """
    إهداء رصيد من مستخدم لآخر — ذرّي بالكامل.
    الحالات: ok | invalid_amount | below_minimum | self | disabled | target_not_found |
             insufficient | error
    """
    try:
        sender_id = int(sender_id)
        target_id = int(target_id)
        amount = int(amount)
    except (TypeError, ValueError):
        return {"status": "invalid_amount"}

    if amount <= 0:
        return {"status": "invalid_amount"}

    if sender_id == target_id:
        return {"status": "self"}

    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")

        check = _gift_receive_status(conn, target_id)

        if check["status"] != "ok":
            return check

        minimum = _read_gift_min_amount(conn)
        if amount < minimum:
            return {"status": "below_minimum", "minimum": minimum}

        debit = conn.execute("""
            UPDATE users
            SET balance = balance - ?
            WHERE user_id=? AND balance >= ?
        """, (amount, sender_id, amount))

        if debit.rowcount != 1:
            return {"status": "insufficient"}

        credit = conn.execute("""
            UPDATE users
            SET balance = balance + ?
            WHERE user_id=?
        """, (amount, target_id))

        if credit.rowcount != 1:
            conn.rollback()
            return {"status": "error"}

        conn.execute("""
            INSERT INTO balance_gifts (sender_id, receiver_id, amount)
            VALUES (?, ?, ?)
        """, (sender_id, target_id, amount))

        return {
            "status": "ok",
            "received": check["received"] + 1,
        }


# =========================================================
# ===== عجلة الحظ: كوبونات (زيادة على الشحن / تعويض يومي) =====
# =========================================================
#
# أنواع الجوائز: cash | respin | none | recharge_bonus | daily_cashback
# (في الأخيرين amount = النسبة المئوية مثل 10 أو 25)
#
# - recharge_bonus : كوبون يُستهلك تلقائياً عند أول شحن معتمد تالٍ (+X% على مبلغ الشحن).
# - daily_cashback : كوبون ليوم واحد: أول يوم كامل بعد الفوز (applies_day) يُعوَّض
#                    بنسبة الكوبون بنفس شروط التعويض اليومي (رصيد 0 عند 12 ليلاً...).
#                    إن كان المستخدم أصلاً على اليومي تُطبَّق الأعلى من النسبتين (لا جمع).

SPIN_PERCENT_PRIZE_TYPES = ("recharge_bonus", "daily_cashback")

_SPIN_DEFAULT_PRIZES_V3 = [
    # (label, type, amount, weight)  — بالعملة السورية الجديدة
    ("💰 10 ل.س",               "cash",           10,   25),
    ("💵 50 ل.س",               "cash",           50,   6),
    ("📈 زيادة 10% على الشحن",  "recharge_bonus", 10,   15),
    ("🚀 زيادة 25% على الشحن",  "recharge_bonus", 25,   5),
    ("🌅 تعويض يومي 10%",       "daily_cashback", 10,   14),
    ("🌟 تعويض يومي 25%",       "daily_cashback", 25,   4),
    ("😔 حظ أوفر",              "none",           0,    22),
    ("🔄 لفة جديدة",            "respin",         0,    9),
]


def _migrate_spin_v3():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS spin_coupons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                percent INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                applies_day TEXT,
                ref TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                used_at TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_spin_coupons_user
            ON spin_coupons(user_id, kind, status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_spin_coupons_day
            ON spin_coupons(kind, status, applies_day)
        """)

        # مرة واحدة: استبدال جوائز العجلة بالتشكيلة الجديدة (8 أجزاء)
        done = conn.execute(
            "SELECT 1 FROM settings WHERE key='spin_prizes_migrated_v3'"
        ).fetchone()

        if not done:
            conn.execute("DELETE FROM spin_prizes")

            for order, (label, ptype, amount, weight) in enumerate(_SPIN_DEFAULT_PRIZES_V3, 1):
                conn.execute("""
                    INSERT INTO spin_prizes (label, prize_type, amount, weight, active, sort_order)
                    VALUES (?, ?, ?, ?, 1, ?)
                """, (label, ptype, amount, weight, order))

            conn.execute("""
                INSERT OR REPLACE INTO settings (key, value)
                VALUES ('spin_prizes_migrated_v3', '1')
            """)


_migrate_spin_v3()


def grant_spin_coupon(user_id, kind, percent, applies_day=None) -> int:
    if kind not in SPIN_PERCENT_PRIZE_TYPES:
        raise ValueError("نوع كوبون غير معروف")

    percent = int(percent)

    if not (0 < percent <= 100):
        raise ValueError("نسبة الكوبون يجب أن تكون بين 1 و 100")

    if kind == "daily_cashback" and not applies_day:
        raise ValueError("كوبون التعويض اليومي يحتاج يوماً محدداً")

    with _db() as conn:
        cur = conn.execute("""
            INSERT INTO spin_coupons (user_id, kind, percent, applies_day)
            VALUES (?, ?, ?, ?)
        """, (int(user_id), kind, percent, applies_day if kind == "daily_cashback" else None))

        return cur.lastrowid


def get_active_spin_coupons(user_id) -> list:
    with _db() as conn:
        rows = conn.execute("""
            SELECT id, kind, percent, applies_day, created_at
            FROM spin_coupons
            WHERE user_id=? AND status='active'
            ORDER BY id
        """, (int(user_id),)).fetchall()

        return [dict(r) for r in rows]


def _consume_recharge_coupon(conn, user_id: int, amount: int, recharge_id):
    """
    يستهلك أفضل كوبون «زيادة على الشحن» فعّال للمستخدم (إن وُجد) داخل نفس معاملة الشحن.
    UPDATE ... WHERE status='active' يضمن استهلاكه مرة واحدة فقط.
    """
    row = conn.execute("""
        SELECT id, percent
        FROM spin_coupons
        WHERE user_id=? AND kind='recharge_bonus' AND status='active'
        ORDER BY percent DESC, id ASC
        LIMIT 1
    """, (int(user_id),)).fetchone()

    if row is None:
        return None

    cur = conn.execute("""
        UPDATE spin_coupons
        SET status='used', used_at=CURRENT_TIMESTAMP, ref=?
        WHERE id=? AND status='active'
    """, (f"recharge:{recharge_id}", int(row[0])))

    if cur.rowcount != 1:
        return None

    percent = int(row[1])

    return {"percent": percent, "bonus": int(int(amount) * percent / 100)}


# ---------------------------------------------------------
# كوبونات التعويض اليومي
# ---------------------------------------------------------

def get_daily_coupon_users(day) -> list:
    """[(user_id, ichancy_username)] لأصحاب كوبون تعويض يومي فعّال لهذا اليوم ولديهم حساب iChancy."""
    with _db() as conn:
        rows = conn.execute("""
            SELECT DISTINCT u.user_id, u.ichancy_username
            FROM spin_coupons c
            JOIN users u ON u.user_id = c.user_id
            WHERE c.kind='daily_cashback' AND c.status='active' AND c.applies_day=?
            AND u.ichancy_username IS NOT NULL AND u.ichancy_username != ''
        """, (day,)).fetchall()

        return [tuple(r) for r in rows]


def get_daily_coupon_percent(user_id, day) -> int:
    with _db() as conn:
        row = conn.execute("""
            SELECT COALESCE(MAX(percent), 0)
            FROM spin_coupons
            WHERE user_id=? AND kind='daily_cashback' AND status='active' AND applies_day=?
        """, (int(user_id), day)).fetchone()

        return int(row[0])


def has_daily_coupon_for_day(day) -> bool:
    with _db() as conn:
        return conn.execute("""
            SELECT 1 FROM spin_coupons
            WHERE kind='daily_cashback' AND status='active' AND applies_day=?
            LIMIT 1
        """, (day,)).fetchone() is not None


def has_active_daily_coupons() -> bool:
    with _db() as conn:
        return conn.execute("""
            SELECT 1 FROM spin_coupons
            WHERE kind='daily_cashback' AND status='active'
            LIMIT 1
        """).fetchone() is not None


def settle_daily_coupons(day) -> int:
    """بعد معالجة يوم كامل: كل كوبونات ذلك اليوم تُعتبر مستهلكة (صالحة ليوم واحد فقط)."""
    with _db() as conn:
        cur = conn.execute("""
            UPDATE spin_coupons
            SET status='used', used_at=CURRENT_TIMESTAMP, ref='daily:' || ?
            WHERE kind='daily_cashback' AND status='active' AND applies_day=?
        """, (day, day))

        return cur.rowcount


def expire_old_daily_coupons(before_day) -> int:
    """كوبونات يوم فاتت نافذة صرفه (البوت كان متوقفاً مثلاً) تنتهي بدل أن تبقى معلّقة."""
    with _db() as conn:
        cur = conn.execute("""
            UPDATE spin_coupons
            SET status='expired', used_at=CURRENT_TIMESTAMP
            WHERE kind='daily_cashback' AND status='active' AND applies_day < ?
        """, (before_day,))

        return cur.rowcount


def get_daily_payout_loss_since(user_id, since_ts) -> int:
    """
    مجموع الخسارة التي عُوِّضت يومياً (كوبونات) لمستخدم بعد لحظة معينة.
    يُخصم من احتساب الأسبوعي حتى لا تُعوَّض نفس الخسارة مرتين.
    """
    try:
        with _db() as conn:
            row = conn.execute("""
                SELECT COALESCE(SUM(loss_amount), 0)
                FROM daily_cashback_payouts
                WHERE user_id=? AND created_at > ?
            """, (int(user_id), str(since_ts))).fetchone()

            return int(row[0])
    except sqlite3.OperationalError:
        return 0


# =========================================================
# ================= REFERRAL SYSTEM ======================
# =========================================================


def set_referrer(referred_user_id, referrer_id):

    referred_user_id = int(referred_user_id)
    referrer_id = int(referrer_id)

    # لا يمكن إحالة المستخدم لنفسه
    if referred_user_id == referrer_id:
        return False

    with _db() as conn:

        # تأكد أن الاثنين موجودين
        conn.execute("""
            INSERT OR IGNORE INTO users (user_id)
            VALUES (?)
        """, (referred_user_id,))

        conn.execute("""
            INSERT OR IGNORE INTO users (user_id)
            VALUES (?)
        """, (referrer_id,))

        # لا نسمح بتغيير المحيل لاحقاً
        existing = conn.execute("""
            SELECT id
            FROM referrals
            WHERE referred_user_id=?
            LIMIT 1
        """, (referred_user_id,)).fetchone()

        if existing:
            return False

        conn.execute("""
            INSERT INTO referrals
                (
                    referrer_id,
                    referred_user_id,
                    status
                )
            VALUES (?, ?, 'pending')
        """, (
            referrer_id,
            referred_user_id
        ))

        return True


def get_referral_info(user_id):

    with _db() as conn:

        total = conn.execute("""
            SELECT COUNT(*)
            FROM referrals
            WHERE referrer_id=?
        """, (user_id,)).fetchone()[0]

        active = conn.execute("""
            SELECT COUNT(*)
            FROM referrals
            WHERE referrer_id=?
            AND status='active'
        """, (user_id,)).fetchone()[0]

        earnings = conn.execute("""
            SELECT COALESCE(SUM(reward), 0)
            FROM referrals
            WHERE referrer_id=?
            AND rewarded=1
        """, (user_id,)).fetchone()[0]

        return {
            "total": int(total),
            "active": int(active),
            "earnings": int(earnings)
        }


def get_referrer(user_id):

    with _db() as conn:

        row = conn.execute("""
            SELECT referrer_id
            FROM referrals
            WHERE referred_user_id=?
            LIMIT 1
        """, (user_id,)).fetchone()

        return int(row[0]) if row else None


def activate_referral_after_recharge(
    referred_user_id,
    reward_amount=50
):

    referred_user_id = int(referred_user_id)
    reward_amount = int(reward_amount)
   

    with _db() as conn:

        # ==============================================
        # لا تفعل الإحالة إلا بعد شحن ناجح فعلياً
        # ==============================================

        successful_recharge = conn.execute("""
            SELECT id
            FROM recharge_requests
            WHERE user_id=?
              AND status='approved'
            LIMIT 1
        """, (referred_user_id,)).fetchone()

        if not successful_recharge:
            return {
                "ok": False,
                "status": "no_successful_recharge"
            }

              

        referral = conn.execute("""
            SELECT id,
                   referrer_id,
                   status,
                   rewarded
            FROM referrals
            WHERE referred_user_id=?
            LIMIT 1
        """, (referred_user_id,)).fetchone()

        # لا يوجد محيل
        if not referral:
            return {
                "ok": False,
                "status": "no_referral"
            }

        # الإحالة مفعلة مسبقاً
        if referral["status"] == "active":
            return {
                "ok": False,
                "status": "already_active",
                "referrer_id": referral["referrer_id"]
            }

        referrer_id = int(referral["referrer_id"])

        # ================================================
        # تفعيل الإحالة
        # ================================================

        conn.execute("""
            UPDATE referrals
            SET status='active',
                activated_at=CURRENT_TIMESTAMP
            WHERE id=?
            AND status='pending'
        """, (referral["id"],))

        # ================================================
        # عدد الإحالات النشطة
        # ================================================

        active_count = conn.execute("""
            SELECT COUNT(*)
            FROM referrals
            WHERE referrer_id=?
            AND status='active'
        """, (referrer_id,)).fetchone()[0]

        # ================================================
        # الشرط: يجب أن يكون لديه 3 إحالات نشطة
        # ================================================

        if active_count < 3:

            return {
                "ok": True,
                "status": "activated_no_reward",
                "referrer_id": referrer_id,
                "active_count": active_count,
                "reward": 0
            }

        # ================================================
        # إعطاء المكافأة لهذه الإحالة فقط
        # ================================================

        if referral["rewarded"] == 0:

            conn.execute("""
                UPDATE users
                SET balance=balance+?
                WHERE user_id=?
            """, (
                reward_amount,
                referrer_id
            ))

            conn.execute("""
                UPDATE referrals
                SET reward=?,
                    rewarded=1
                WHERE id=?
            """, (
                reward_amount,
                referral["id"]
            ))

            return {
                "ok": True,
                "status": "rewarded",
                "referrer_id": referrer_id,
                "active_count": active_count,
                "reward": reward_amount
            }

        return {
            "ok": True,
            "status": "already_rewarded",
            "referrer_id": referrer_id,
            "active_count": active_count,
            "reward": 0
        }


def get_referral_users(user_id):

    with _db() as conn:

        rows = conn.execute("""
            SELECT referred_user_id,
                   status,
                   reward,
                   created_at,
                   activated_at
            FROM referrals
            WHERE referrer_id=?
            ORDER BY created_at DESC
        """, (user_id,)).fetchall()

        return [dict(r) for r in rows]


def get_all_referrals():

    with _db() as conn:

        rows = conn.execute("""
            SELECT referrer_id,
                   referred_user_id,
                   status,
                   reward,
                   created_at,
                   activated_at
            FROM referrals
            ORDER BY created_at DESC
        """).fetchall()

        return [dict(r) for r in rows]


# =========================================================
# ========= عمولة الإحالة المستمرة (Referral Commission) ==
# =========================================================

def get_referral_commission_percent() -> float:
    """
    نسبة عمولة الإحالة المستمرة (كنسبة عشرية، مثال: 0.05 = 5%).
    القيمة الافتراضية 0 (متوقف) حتى يضبطها الأدمن من اللوحة.
    """

    with _db() as conn:

        row = conn.execute("""
            SELECT value
            FROM settings
            WHERE key='referral_commission_percent'
        """).fetchone()

        return float(row[0]) if row else 0.0


def set_referral_commission_percent(percent: float):

    with _db() as conn:

        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value)
            VALUES ('referral_commission_percent', ?)
        """, (str(float(percent)),))


def get_referral_period_days() -> int:
    """
    عدد أيام دورة تراكم/توزيع أرباح الإحالة. القيمة الافتراضية 10 أيام.
    """

    with _db() as conn:

        row = conn.execute("""
            SELECT value
            FROM settings
            WHERE key='referral_period_days'
        """).fetchone()

        return int(row[0]) if row else 10


def set_referral_period_days(days: int):

    with _db() as conn:

        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value)
            VALUES ('referral_period_days', ?)
        """, (str(int(days)),))


def log_referral_commission(referrer_id, referred_user_id, recharge_amount, commission_amount):
    """
    تسجيل عمولة إحالة متراكمة (لم تُصرف بعد) نتيجة شحن ناجح لمستخدم مُحال.
    """

    with _db() as conn:

        # عند تفعيل نظام الإحالات حسب الحرق (هرمي/إفرادي) تتوقف عمولة الشحن القديمة كي لا تُصرف الإحالات مرتين
        _enabled_row = conn.execute("SELECT value FROM settings WHERE key='ref2_enabled'").fetchone()
        if not (_enabled_row and _enabled_row[0] == "0"):
            return

        conn.execute("""
            INSERT INTO referral_commissions
                (referrer_id, referred_user_id, recharge_amount, commission_amount)
            VALUES (?, ?, ?, ?)
        """, (
            int(referrer_id),
            int(referred_user_id),
            int(recharge_amount),
            int(commission_amount),
        ))


def get_pending_commission_total(referrer_id) -> int:

    with _db() as conn:

        row = conn.execute("""
            SELECT COALESCE(SUM(commission_amount), 0)
            FROM referral_commissions
            WHERE referrer_id=?
            AND paid=0
        """, (int(referrer_id),)).fetchone()

        return int(row[0])


def mark_referral_commissions_paid(referrer_id):

    with _db() as conn:

        conn.execute("""
            UPDATE referral_commissions
            SET paid=1, paid_at=CURRENT_TIMESTAMP
            WHERE referrer_id=?
            AND paid=0
        """, (int(referrer_id),))


def init_referral_cycle_if_missing(referrer_id):

    with _db() as conn:

        conn.execute("""
            INSERT OR IGNORE INTO referral_payout_cycles (referrer_id)
            VALUES (?)
        """, (int(referrer_id),))


def get_referral_cycle_start(referrer_id):
    """
    ترجع توقيت بداية دورة الأرباح الحالية لهذا المُحيل (نص بصيغة SQLite).
    """

    with _db() as conn:

        row = conn.execute("""
            SELECT cycle_start_at
            FROM referral_payout_cycles
            WHERE referrer_id=?
        """, (int(referrer_id),)).fetchone()

        return row[0] if row else None


def reset_referral_cycle(referrer_id):

    with _db() as conn:

        conn.execute("""
            INSERT OR REPLACE INTO referral_payout_cycles (referrer_id, cycle_start_at)
            VALUES (?, CURRENT_TIMESTAMP)
        """, (int(referrer_id),))


def get_all_referrer_ids():
    """
    كل معرّفات المستخدمين الذين لديهم إحالة واحدة على الأقل (مُحيلين).
    """

    with _db() as conn:

        rows = conn.execute("""
            SELECT DISTINCT referrer_id
            FROM referrals
        """).fetchall()

        return [int(r[0]) for r in rows]


def get_recent_referral_payouts(limit=15):

    with _db() as conn:

        rows = conn.execute("""
            SELECT referrer_id, SUM(commission_amount) as total, MAX(paid_at) as paid_at
            FROM referral_commissions
            WHERE paid=1
            GROUP BY referrer_id, paid_at
            ORDER BY paid_at DESC
            LIMIT ?
        """, (int(limit),)).fetchall()

        return [dict(row) for row in rows]
    # =========================================================
# GIFT CODES FUNCTIONS
# =========================================================

def create_gift_code(code, amount, max_uses, created_by=None, expires_at=None):
    """
    إنشاء كود هدية جديد.

    code        = نص الكود
    amount      = قيمة الهدية
    max_uses    = عدد مرات الاستخدام
    created_by  = ID الأدمن
    expires_at  = تاريخ الانتهاء اختياري
    """

    code = str(code).strip().upper()
    amount = int(amount)
    max_uses = int(max_uses)

    if not code:
        return False

    if amount <= 0:
        return False

    if max_uses <= 0:
        return False

    try:
        with _db() as conn:

            cur = conn.execute("""
                INSERT INTO gift_codes
                (
                    code,
                    amount,
                    max_uses,
                    used_count,
                    active,
                    created_by,
                    expires_at
                )
                VALUES (?, ?, ?, 0, 1, ?, ?)
            """, (
                code,
                amount,
                max_uses,
                created_by,
                expires_at
            ))

            return cur.lastrowid

    except sqlite3.IntegrityError:
        # الكود موجود مسبقاً
        return False


def get_gift_code(code):
    """
    جلب معلومات كود هدية.
    """

    code = str(code).strip().upper()

    with _db() as conn:

        row = conn.execute("""
            SELECT
                id,
                code,
                amount,
                max_uses,
                used_count,
                active,
                created_by,
                created_at,
                expires_at
            FROM gift_codes
            WHERE code=?
            LIMIT 1
        """, (code,)).fetchone()

        return dict(row) if row else None


def get_all_gift_codes():
    """
    جلب جميع أكواد الهدايا للأدمن.
    """

    with _db() as conn:

        rows = conn.execute("""
            SELECT
                id,
                code,
                amount,
                max_uses,
                used_count,
                active,
                created_by,
                created_at,
                expires_at
            FROM gift_codes
            ORDER BY created_at DESC
        """).fetchall()

        return [dict(row) for row in rows]


def deactivate_gift_code(code):
    """
    إيقاف كود هدية.
    """

    code = str(code).strip().upper()

    with _db() as conn:

        cur = conn.execute("""
            UPDATE gift_codes
            SET active=0
            WHERE code=?
        """, (code,))

        return cur.rowcount > 0


def activate_gift_code(code):
    """
    إعادة تفعيل كود هدية.
    """

    code = str(code).strip().upper()

    with _db() as conn:

        cur = conn.execute("""
            UPDATE gift_codes
            SET active=1
            WHERE code=?
        """, (code,))

        return cur.rowcount > 0


def has_user_used_gift_code(user_id, gift_code_id):
    """
    التحقق إذا المستخدم استخدم الكود سابقاً.
    """

    with _db() as conn:

        row = conn.execute("""
            SELECT id
            FROM gift_code_uses
            WHERE gift_code_id=?
            AND user_id=?
            LIMIT 1
        """, (
            int(gift_code_id),
            int(user_id)
        )).fetchone()

        return row is not None


def redeem_gift_code(user_id, code):
    """
    استبدال كود هدية وإضافة الرصيد للمستخدم.

    العملية كلها تتم داخل Transaction واحدة
    لمنع استخدام آخر استخدام للكود مرتين.
    """

    user_id = int(user_id)
    code = str(code).strip().upper()

    if not code:
        return {
            "ok": False,
            "status": "invalid_code"
        }

    with _db() as conn:

        # =====================================================
        # جلب الكود
        # =====================================================

        gift = conn.execute("""
            SELECT
                id,
                code,
                amount,
                max_uses,
                used_count,
                active,
                expires_at
            FROM gift_codes
            WHERE code=?
            LIMIT 1
        """, (code,)).fetchone()

        if not gift:

            return {
                "ok": False,
                "status": "not_found"
            }

        gift = dict(gift)

        # =====================================================
        # التحقق من حالة الكود
        # =====================================================

        if int(gift["active"]) != 1:

            return {
                "ok": False,
                "status": "inactive"
            }

        # =====================================================
        # التحقق من تاريخ الانتهاء
        # =====================================================

        if gift["expires_at"]:

            expired = conn.execute("""
                SELECT
                    CASE
                        WHEN CURRENT_TIMESTAMP > ?
                        THEN 1
                        ELSE 0
                    END
            """, (gift["expires_at"],)).fetchone()[0]

            if expired:

                return {
                    "ok": False,
                    "status": "expired"
                }

        # =====================================================
        # التحقق من عدد الاستخدامات
        # =====================================================

        if int(gift["used_count"]) >= int(gift["max_uses"]):

            return {
                "ok": False,
                "status": "limit_reached"
            }

        # =====================================================
        # التحقق هل المستخدم استخدم الكود سابقاً
        # =====================================================

        already_used = conn.execute("""
            SELECT id
            FROM gift_code_uses
            WHERE gift_code_id=?
            AND user_id=?
            LIMIT 1
        """, (
            gift["id"],
            user_id
        )).fetchone()

        if already_used:

            return {
                "ok": False,
                "status": "already_used"
            }

        # =====================================================
        # إنشاء المستخدم إذا غير موجود
        # =====================================================

        conn.execute("""
            INSERT OR IGNORE INTO users (user_id)
            VALUES (?)
        """, (user_id,))

        # =====================================================
        # تسجيل استخدام الكود
        # =====================================================

        try:

            conn.execute("""
                INSERT INTO gift_code_uses
                (
                    gift_code_id,
                    user_id,
                    amount
                )
                VALUES (?, ?, ?)
            """, (
                gift["id"],
                user_id,
                gift["amount"]
            ))

        except sqlite3.IntegrityError:

            return {
                "ok": False,
                "status": "already_used"
            }

        # =====================================================
        # إضافة الرصيد للمستخدم
        # =====================================================

        conn.execute("""
            UPDATE users
            SET balance = balance + ?
            WHERE user_id=?
        """, (
            int(gift["amount"]),
            user_id
        ))

        # =====================================================
        # زيادة عدد استخدامات الكود
        # =====================================================

        conn.execute("""
            UPDATE gift_codes
            SET used_count = used_count + 1
            WHERE id=?
        """, (gift["id"],))

        # =====================================================
        # عدد الاستخدامات بعد العملية
        # =====================================================

        new_used_count = int(gift["used_count"]) + 1

        # إذا وصل للحد يتم تعطيله تلقائياً
        if new_used_count >= int(gift["max_uses"]):

            conn.execute("""
                UPDATE gift_codes
                SET active=0
                WHERE id=?
            """, (gift["id"],))

        # =====================================================
        # النتيجة
        # =====================================================

        return {
            "ok": True,
            "status": "redeemed",
            "code": gift["code"],
            "amount": int(gift["amount"]),
            "used_count": new_used_count,
            "max_uses": int(gift["max_uses"])
        }


def get_gift_code_uses(code):
    """
    جلب الأشخاص الذين استخدموا كود معين.
    """

    code = str(code).strip().upper()

    with _db() as conn:

        rows = conn.execute("""
            SELECT
                gcu.user_id,
                gcu.amount,
                gcu.used_at
            FROM gift_code_uses gcu
            INNER JOIN gift_codes gc
                ON gc.id = gcu.gift_code_id
            WHERE gc.code=?
            ORDER BY gcu.used_at DESC
        """, (code,)).fetchall()

        return [dict(row) for row in rows]
def get_welcome_bonus_config():
    """جلب حالة وقيمة البونص الترحيبي الحالي."""
    with _db() as conn:
        rows = dict(conn.execute("""
            SELECT key, value FROM settings 
            WHERE key IN ('welcome_bonus_amount', 'welcome_bonus_enabled')
        """).fetchall())

        amount = int(rows.get('welcome_bonus_amount', '50'))
        enabled = rows.get('welcome_bonus_enabled', '1') == '1'
        return enabled, amount

def set_welcome_bonus_config(enabled: bool, amount: int):
    """تعديل قيمة وحالة البونص الترحيبي من قبل الأدمن."""
    with _db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value)
            VALUES ('welcome_bonus_enabled', ?)
        """, ('1' if enabled else '0',))

        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value)
            VALUES ('welcome_bonus_amount', ?)
        """, (str(int(amount)),))

def apply_welcome_bonus(user_id: int) -> int:
    """منح البونص الترحيبي للمستخدم الجديد مرة واحدة فقط عند التسجيل/الموافقة."""
    enabled, amount = get_welcome_bonus_config()
    if not enabled or amount <= 0:
        return 0

    with _db() as conn:
        row = conn.execute("""
            SELECT received_welcome_bonus FROM users WHERE user_id=?
        """, (user_id,)).fetchone()

        if row and row['received_welcome_bonus'] == 0:
            conn.execute("""
                UPDATE users 
                SET balance = balance + ?, received_welcome_bonus = 1 
                WHERE user_id=?
            """, (amount, user_id))
            conn.execute(
                "INSERT INTO bonus_events (user_id, kind, amount) VALUES (?, 'welcome', ?)",
                (int(user_id), int(amount))
            )
            return amount
    return 0
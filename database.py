"""Работа с базой данных SQLite с реферальной системой."""

import sqlite3
from datetime import datetime, timedelta


def get_connection():
    conn = sqlite3.connect("club_bot.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Создаёт таблицы."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TIMESTAMP,
            trial_end TIMESTAMP,
            subscription_end TIMESTAMP,
            was_in_club BOOLEAN DEFAULT 0,
            subscription_cancelled BOOLEAN DEFAULT 0,
            blocked_bot BOOLEAN DEFAULT 0,
            last_active TIMESTAMP,
            trial_feedback_sent BOOLEAN DEFAULT 0,
            removed_from_group BOOLEAN DEFAULT 0,
            preferred_currency TEXT DEFAULT 'rub',
            referred_by INTEGER,
            referral_count INTEGER DEFAULT 0,
            bonus_days INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            currency TEXT,
            tariff TEXT,
            payment_system TEXT,
            status TEXT,
            is_recurrent BOOLEAN DEFAULT 0,
            payment_date TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            message TEXT,
            feedback_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


# ===== Пользователи =====
def save_user(user_id: int, username: str, first_name: str, referred_by: int = None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO users 
        (user_id, username, first_name, joined_at, last_active, referred_by)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, username, first_name, datetime.now(), datetime.now(), referred_by))
    
    # Если пользователь уже есть, но пришёл по рефералке — обновляем
    if referred_by:
        cursor.execute("""
            UPDATE users SET referred_by = ? 
            WHERE user_id = ? AND referred_by IS NULL
        """, (referred_by, user_id))
    
    conn.commit()
    conn.close()


def get_user(user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user


def update_last_active(user_id: int):
    conn = get_connection()
    conn.execute("UPDATE users SET last_active = ? WHERE user_id = ?", (datetime.now(), user_id))
    conn.commit()
    conn.close()


def update_user_trial(user_id: int, trial_end: datetime):
    conn = get_connection()
    conn.execute("UPDATE users SET trial_end = ?, trial_feedback_sent = 0 WHERE user_id = ?",
                 (trial_end, user_id))
    conn.commit()
    conn.close()


def update_user_subscription(user_id: int, subscription_end: datetime):
    conn = get_connection()
    conn.execute(
        "UPDATE users SET subscription_end = ?, was_in_club = 1, subscription_cancelled = 0, removed_from_group = 0 WHERE user_id = ?",
        (subscription_end, user_id)
    )
    conn.commit()
    conn.close()


def cancel_subscription(user_id: int):
    conn = get_connection()
    conn.execute("UPDATE users SET subscription_cancelled = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def mark_feedback_sent(user_id: int):
    conn = get_connection()
    conn.execute("UPDATE users SET trial_feedback_sent = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def mark_removed_from_group(user_id: int):
    conn = get_connection()
    conn.execute("UPDATE users SET removed_from_group = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def check_if_was_in_club(user_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT was_in_club FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return bool(result and result["was_in_club"])


def change_currency(user_id: int, currency: str):
    """Меняет предпочтительную валюту пользователя."""
    conn = get_connection()
    conn.execute("UPDATE users SET preferred_currency = ? WHERE user_id = ?", (currency, user_id))
    conn.commit()
    conn.close()


# ===== Реферальная система =====
def increment_referral_count(referrer_id: int):
    """Увеличивает счётчик рефералов и добавляет бонусные дни."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Увеличиваем счётчик
    cursor.execute("""
        UPDATE users 
        SET referral_count = referral_count + 1,
            bonus_days = bonus_days + 3
        WHERE user_id = ?
    """, (referrer_id,))
    
    # Добавляем 3 дня к текущему периоду
    user = get_user(referrer_id)
    if user:
        if user["subscription_end"] and datetime.fromisoformat(user["subscription_end"]) > datetime.now():
            # Активная подписка — добавляем к ней
            new_end = datetime.fromisoformat(user["subscription_end"]) + timedelta(days=3)
            conn.execute("UPDATE users SET subscription_end = ? WHERE user_id = ?",
                        (new_end, referrer_id))
        elif user["trial_end"] and datetime.fromisoformat(user["trial_end"]) > datetime.now():
            # Активный пробный — добавляем к нему
            new_end = datetime.fromisoformat(user["trial_end"]) + timedelta(days=3)
            conn.execute("UPDATE users SET trial_end = ? WHERE user_id = ?",
                        (new_end, referrer_id))
    
    conn.commit()
    conn.close()


def get_referral_stats(user_id: int):
    """Получает реферальную статистику пользователя."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT referral_count, bonus_days FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result


def get_all_referrals():
    """Получает всех пользователей с реферальной информацией."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, username, first_name, joined_at, referred_by, referral_count, bonus_days
        FROM users
        ORDER BY referral_count DESC
    """)
    users = cursor.fetchall()
    conn.close()
    return users


# ===== Отзывы =====
def save_feedback(user_id: int, message: str, feedback_type: str = "impressions"):
    conn = get_connection()
    conn.execute(
        "INSERT INTO feedback (user_id, message, feedback_type) VALUES (?, ?, ?)",
        (user_id, message, feedback_type)
    )
    conn.commit()
    conn.close()


# ===== Платежи =====
def save_payment(user_id, amount, currency, tariff, payment_system, status, is_recurrent=False):
    conn = get_connection()
    conn.execute("""
        INSERT INTO payments (user_id, amount, currency, tariff, payment_system, status, is_recurrent, payment_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, amount, currency, tariff, payment_system, status, is_recurrent, datetime.now()))
    conn.commit()
    conn.close()


# ===== Запросы для планировщика =====
def get_users_trial_ending_in(days: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM users
        WHERE trial_end IS NOT NULL
          AND subscription_end IS NULL
          AND trial_feedback_sent = 0
          AND date(trial_end) = date('now', '+' || ? || ' days')
    """, (days,))
    users = cursor.fetchall()
    conn.close()
    return users


def get_users_trial_ended_not_paid():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM users
        WHERE trial_end IS NOT NULL
          AND subscription_end IS NULL
          AND date(trial_end) < date('now')
          AND removed_from_group = 0
    """)
    users = cursor.fetchall()
    conn.close()
    return users


# ===== Статистика для админа =====
def get_all_users():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    users = cursor.fetchall()
    conn.close()
    return users


def get_all_payments():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM payments ORDER BY payment_date DESC")
    payments = cursor.fetchall()
    conn.close()
    return payments


def get_all_feedback():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM feedback ORDER BY created_at DESC")
    feedback = cursor.fetchall()
    conn.close()
    return feedback


def get_stats():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE trial_end > datetime('now') AND subscription_end IS NULL")
    on_trial = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE subscription_end > datetime('now') AND subscription_cancelled = 0")
    active_subscriptions = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE subscription_cancelled = 1 AND subscription_end > datetime('now')")
    cancelled = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE blocked_bot = 1")
    blocked = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*), SUM(amount) FROM payments WHERE status = 'success'")
    row = cursor.fetchone()
    payments_count = row[0] or 0
    payments_sum = row[1] or 0

    cursor.execute("SELECT COUNT(*) FROM feedback")
    feedback_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL")
    referral_users = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(referral_count) FROM users")
    total_referrals = cursor.fetchone()[0] or 0

    conn.close()

    return {
        "total_users": total_users,
        "on_trial": on_trial,
        "active_subscriptions": active_subscriptions,
        "cancelled": cancelled,
        "blocked": blocked,
        "payments_count": payments_count,
        "payments_sum": payments_sum,
        "feedback_count": feedback_count,
        "referral_users": referral_users,
        "total_referrals": total_referrals,
    }
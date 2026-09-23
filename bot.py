"""Основной файл бота с реферальной системой."""

import os
import logging
from datetime import datetime, timedelta, time
from threading import Thread

from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import database as db
import texts as t

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== НАСТРОЙКИ ====================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
PRIVATE_CHAT_ID = int(os.environ.get("PRIVATE_CHAT_ID"))
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID"))
BOT_USERNAME = os.environ.get("BOT_USERNAME", "your_bot_username")  # Имя бота без @

LAVATOP_PROJECT_ID = os.environ.get("LAVATOP_PROJECT_ID", "")

TARIFFS = {
    "1_month":  {"rub": 2500,  "eur": 25,  "days": 30,  "name": "1 месяц — знакомство"},
    "3_months": {"rub": 6000,  "eur": 60,  "days": 90,  "name": "3 месяца — глубокое погружение"},
    "6_months": {"rub": 10000, "eur": 100, "days": 180, "name": "6 месяцев — трансформация"},
}

TRIAL_DAYS = 7
REFERRAL_BONUS_DAYS = 3

AWAITING_FEEDBACK = "awaiting_feedback"


# ==================== КОМАНДЫ ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запуск бота с поддержкой реферальной ссылки."""
    user = update.effective_user
    user_id = user.id
    
    # Проверяем, есть ли реферальный параметр
    referred_by = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            referrer_id = int(context.args[0].replace("ref_", ""))
            if referrer_id != user_id:  # Не может пригласить сам себя
                referred_by = referrer_id
        except ValueError:
            pass
    
    # Сохраняем пользователя
    db.save_user(user_id, user.username, user.first_name, referred_by)
    db.update_last_active(user_id)
    
    # Если пришёл по рефералке — сразу даём пробный период
    if referred_by:
        trial_end = datetime.now() + timedelta(days=TRIAL_DAYS)
        db.update_user_trial(user_id, trial_end)
        
        try:
            await context.bot.restrict_chat_member(
                PRIVATE_CHAT_ID, user_id,
                can_send_messages=True, can_send_media_messages=True,
                can_send_other_messages=True, can_add_web_page_previews=True,
                until_date=int(trial_end.timestamp())
            )
            
            # Уведомляем реферера о бонусе
            ref_stats = db.get_referral_stats(referred_by)
            await context.bot.send_message(
                referred_by,
                t.REFERRAL_BONUS_ADDED.format(
                    referral_count=ref_stats["referral_count"],
                    bonus_days=ref_stats["bonus_days"]
                ),
                parse_mode="HTML"
            )
            
            # Увеличиваем счётчик рефералов
            db.increment_referral_count(referred_by)
            
            await update.message.reply_text(
                t.REFERRAL_WELCOME.format(
                    expert=t.EXPERT_NAME,
                    date=trial_end.strftime('%d.%m.%Y %H:%M')
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка добавления в группу: {e}")
            await update.message.reply_text("Произошла ошибка. Попробуйте позже.")
        return
    
    # Обычный запуск
    if db.check_if_was_in_club(user_id):
        await update.message.reply_text(
            f"✨ Рада снова видеть вас в «{t.CLUB_NAME}»!\n\n"
            "Бесплатный пробный период доступен только один раз, "
            "но вы можете сразу оформить подписку и продолжить своё путешествие к себе."
        )
        await show_tariffs(update, context)
        return

    keyboard = [
        [InlineKeyboardButton("🎁 Получить бесплатный доступ на 7 дней", callback_data="trial")],
        [InlineKeyboardButton("💎 Оформить подписку", callback_data="pay_now")]
    ]
    await update.message.reply_text(
        t.WELCOME,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список команд."""
    db.update_last_active(update.effective_user.id)
    await update.message.reply_text(t.BOT_HELP, parse_mode="HTML")


async def show_tariffs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("1 месяц — знакомство", callback_data="tariff_1_month")],
        [InlineKeyboardButton("3 месяца — глубокое погружение", callback_data="tariff_3_months")],
        [InlineKeyboardButton("6 месяцев — трансформация", callback_data="tariff_6_months")],
    ]
    text = (
        f"💎 <b>Выберите свой путь в «{t.CLUB_NAME}»</b>\n\n"
        "Каждая практика — это не просто упражнение для лица. "
        "Это момент соединения с собой, когда ваши руки становятся проводником любви и заботы."
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    db.update_last_active(user_id)

    if data == "trial":
        user = db.get_user(user_id)
        if user and user["trial_end"]:
            await query.edit_message_text("Вы уже использовали пробный период 💎")
            return

        trial_end = datetime.now() + timedelta(days=TRIAL_DAYS)
        db.update_user_trial(user_id, trial_end)

        try:
            await context.bot.restrict_chat_member(
                PRIVATE_CHAT_ID, user_id,
                can_send_messages=True, can_send_media_messages=True,
                can_send_other_messages=True, can_add_web_page_previews=True,
                until_date=int(trial_end.timestamp())
            )
            await query.edit_message_text(
                t.TRIAL_ACTIVATED.format(date=trial_end.strftime('%d.%m.%Y %H:%M')),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка добавления в группу: {e}")
            await query.edit_message_text("Произошла ошибка. Попробуйте позже.")

    elif data == "pay_now":
        await show_tariffs(update, context)

    elif data.startswith("tariff_"):
        tariff_key = data.replace("tariff_", "")
        context.user_data["selected_tariff"] = tariff_key
        
        # Получаем предпочтительную валюту пользователя
        user = db.get_user(user_id)
        preferred_currency = user["preferred_currency"] if user else "rub"
        
        # Показываем обе валюты, но выделяем предпочтительную
        if preferred_currency == "rub":
            keyboard = [
                [InlineKeyboardButton("🇷🇺 Оплатить в рублях (рекомендуется)", callback_data=f"currency_rub_{tariff_key}")],
                [InlineKeyboardButton("🇪🇺 Оплатить в евро", callback_data=f"currency_eur_{tariff_key}")],
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("🇷🇺 Оплатить в рублях", callback_data=f"currency_rub_{tariff_key}")],
                [InlineKeyboardButton("🇪🇺 Оплатить в евро (рекомендуется)", callback_data=f"currency_eur_{tariff_key}")],
            ]
        
        await query.edit_message_text(
            "💳 <b>Выберите валюту оплаты</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    elif data.startswith("currency_"):
        parts = data.split("_")
        currency = parts[1]
        tariff_key = parts[2]
        await show_payment_screen(update, context, tariff_key, currency)


async def show_payment_screen(update: Update, context: ContextTypes.DEFAULT_TYPE, tariff_key: str, currency: str):
    user_id = update.effective_user.id
    tariff_info = TARIFFS[tariff_key]
    amount = tariff_info[currency]
    symbol = "₽" if currency == "rub" else "€"

    if currency == "rub":
        payment_url = f"https://lava.top/pay?project={LAVATOP_PROJECT_ID}&amount={amount}&custom_data={user_id}"
        btn_text = "💳 Оплатить через LavaTop"
    else:
        payment_url = f"https://checkout.stripe.com/pay?amount={amount}&currency=eur&metadata[user_id]={user_id}"
        btn_text = "💳 Pay with Stripe"

    keyboard = [[InlineKeyboardButton(btn_text, url=payment_url)]]
    await update.callback_query.edit_message_text(
        f"💎 <b>Тариф: {tariff_info['name']} в «{t.CLUB_NAME}»</b>\n\n"
        f"Сумма к оплате: <b>{amount:,} {symbol}</b>\n\n"
        "Что вас ждёт в клубе:\n"
        "• Авторские техники самомассажа от Жанны Валентэ\n"
        "• Практики фейсфитнеса для разных зон лица\n"
        "• Разборы: как работать с конкретными запросами\n"
        "• Медитации и упражнения для соединения с телом\n"
        "• Поддержка сообщества единомышленниц\n\n"
        "Нажмите на кнопку ниже 👇",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


# ==================== КОМАНДЫ КЛИЕНТА ====================
async def my_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    db.update_last_active(user_id)

    if not user:
        await update.message.reply_text("Вы ещё не зарегистрированы.")
        return

    trial_end = user["trial_end"]
    subscription_end = user["subscription_end"]
    cancelled = user["subscription_cancelled"]
    currency = user["preferred_currency"]
    currency_name = "рубли (₽)" if currency == "rub" else "евро (€)"

    if trial_end and datetime.fromisoformat(trial_end) > datetime.now():
        days_left = (datetime.fromisoformat(trial_end) - datetime.now()).days
        text = t.STATUS_TRIAL.format(
            date=datetime.fromisoformat(trial_end).strftime('%d.%m.%Y %H:%M'),
            days=days_left,
            currency=currency_name
        )
    elif subscription_end and datetime.fromisoformat(subscription_end) > datetime.now():
        days_left = (datetime.fromisoformat(subscription_end) - datetime.now()).days
        renewal_info = (
            "⚠️ Автоматическое продление отменено" if cancelled
            else "✅ Автоматическое продление включено"
        )
        text = t.STATUS_ACTIVE.format(
            date=datetime.fromisoformat(subscription_end).strftime('%d.%m.%Y'),
            days=days_left,
            renewal_info=renewal_info,
            currency=currency_name
        )
    else:
        text = t.STATUS_NONE

    await update.message.reply_text(text, parse_mode="HTML")


async def cancel_subscription_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    db.update_last_active(user_id)

    if not user or not user["subscription_end"] or datetime.fromisoformat(user["subscription_end"]) <= datetime.now():
        await update.message.reply_text("У вас нет активной подписки для отмены.")
        return

    if user["subscription_cancelled"]:
        await update.message.reply_text(
            f"Автоматическое продление уже отменено.\n"
            f"Подписка действует до {datetime.fromisoformat(user['subscription_end']).strftime('%d.%m.%Y')}."
        )
        return

    db.cancel_subscription(user_id)
    await update.message.reply_text(
        f"✅ Автоматическое продление отменено.\n\n"
        f"Подписка будет действовать до {datetime.fromisoformat(user['subscription_end']).strftime('%d.%m.%Y')}."
    )


async def currency_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Смена валюты оплаты."""
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    db.update_last_active(user_id)

    if not user:
        await update.message.reply_text("Вы ещё не зарегистрированы.")
        return

    current_currency = user["preferred_currency"]
    current_name = "рубли (₽)" if current_currency == "rub" else "евро (€)"
    current_symbol = "₽" if current_currency == "rub" else "€"

    keyboard = [
        [InlineKeyboardButton("🇷🇺 Рубли (₽)", callback_data="set_currency_rub")],
        [InlineKeyboardButton("🇪🇺 Евро (€)", callback_data="set_currency_eur")],
    ]

    await update.message.reply_text(
        t.CURRENCY_CURRENT.format(currency_name=current_name, symbol=current_symbol),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def currency_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора валюты."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data.startswith("set_currency_"):
        currency = data.replace("set_currency_", "")
        db.change_currency(user_id, currency)
        currency_name = "рубли (₽)" if currency == "rub" else "евро (€)"
        symbol = "₽" if currency == "rub" else "€"
        await query.edit_message_text(
            t.CURRENCY_CHANGED.format(currency_name=currency_name, symbol=symbol),
            parse_mode="HTML"
        )


async def referral_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает реферальную информацию."""
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    db.update_last_active(user_id)

    if not user:
        await update.message.reply_text("Вы ещё не зарегистрированы.")
        return

    ref_stats = db.get_referral_stats(user_id)
    referral_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"

    await update.message.reply_text(
        t.REFERRAL_INFO.format(
            referral_link=referral_link,
            referral_count=ref_stats["referral_count"],
            bonus_days=ref_stats["bonus_days"]
        ),
        parse_mode="HTML"
    )


# ==================== ОБРАБОТКА ОТЗЫВОВ ====================
async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get(AWAITING_FEEDBACK):
        user_id = update.effective_user.id
        message = update.message.text
        db.save_feedback(user_id, message, "trial_impressions")
        db.mark_feedback_sent(user_id)
        context.user_data[AWAITING_FEEDBACK] = False
        await update.message.reply_text(t.FEEDBACK_THANKS, parse_mode="HTML")


# ==================== ЗАДАЧИ ПЛАНИРОВЩИКА ====================
async def check_trial_feedback(context: ContextTypes.DEFAULT_TYPE):
    """За 3 дня до окончания пробного — спрашиваем впечатления."""
    users = db.get_users_trial_ending_in(3)
    for user in users:
        try:
            await context.bot.send_message(user["user_id"], t.FEEDBACK_REQUEST, parse_mode="HTML")
            db.mark_feedback_sent(user["user_id"])
            logger.info(f"Отправлен запрос отзыва пользователю {user['user_id']}")
        except Exception as e:
            logger.error(f"Ошибка отправки отзыва {user['user_id']}: {e}")


async def check_trial_ending(context: ContextTypes.DEFAULT_TYPE):
    """В день окончания пробного — предлагаем оплатить."""
    users = db.get_users_trial_ending_in(0)
    for user in users:
        try:
            keyboard = [
                [InlineKeyboardButton("💎 Оформить подписку", callback_data="pay_now")]
            ]
            await context.bot.send_message(
                user["user_id"],
                t.TRIAL_ENDING.format(expert=t.EXPERT_NAME),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            logger.info(f"Отправлено предложение оплаты пользователю {user['user_id']}")
        except Exception as e:
            logger.error(f"Ошибка {user['user_id']}: {e}")


async def check_unpaid_users(context: ContextTypes.DEFAULT_TYPE):
    """Удаляем из группы тех, кто не оплатил после пробного."""
    users = db.get_users_trial_ended_not_paid()
    for user in users:
        try:
            await context.bot.ban_chat_member(PRIVATE_CHAT_ID, user["user_id"])
            await context.bot.unban_chat_member(
                PRIVATE_CHAT_ID, user["user_id"],
                only_if_banned=True
            )
            await context.bot.send_message(user["user_id"], t.REMOVAL_MESSAGE, parse_mode="HTML")
            db.mark_removed_from_group(user["user_id"])
            logger.info(f"Пользователь {user['user_id']} удалён из группы")
        except Exception as e:
            logger.error(f"Ошибка удаления {user['user_id']}: {e}")


# ==================== ВЕБХУКИ ====================
app = Flask(__name__)


def _process_successful_payment(user_id: int, amount: float, currency: str,
                                 payment_system: str, is_recurrent: bool):
    tariff_key = None
    for key, info in TARIFFS.items():
        if info[currency] == amount:
            tariff_key = key
            break

    if not tariff_key:
        logger.error(f"Неизвестная сумма {amount} {currency}")
        return None, None

    db.save_payment(user_id, amount, currency.upper(), tariff_key, payment_system, "success", is_recurrent)

    tariff_info = TARIFFS[tariff_key]
    subscription_end = datetime.now() + timedelta(days=tariff_info["days"])
    db.update_user_subscription(user_id, subscription_end)

    return tariff_info, subscription_end


@app.route("/lavatop-webhook", methods=["POST"])
def lavatop_webhook():
    data = request.json
    logger.info(f"LavaTop webhook: {data}")

    if data.get("status") in ["success", "paid", "completed"]:
        user_id = int(data.get("custom_data"))
        amount = float(data.get("amount"))
        user = db.get_user(user_id)
        is_recurrent = bool(user and user["subscription_end"]
                           and datetime.fromisoformat(user["subscription_end"]) > datetime.now())

        tariff_info, subscription_end = _process_successful_payment(
            user_id, amount, "rub", "lavatop", is_recurrent
        )
        if not tariff_info:
            return jsonify({"error": "invalid amount"}), 400

        bot = Application.builder().token(TELEGRAM_BOT_TOKEN).build().bot
        symbol = "₽"

        if is_recurrent:
            text = t.AUTO_RENEWAL.format(
                club=t.CLUB_NAME,
                tariff=tariff_info["name"],
                amount=f"{amount:,.0f}",
                symbol=symbol,
                date=subscription_end.strftime('%d.%m.%Y')
            )
        else:
            text = t.PAYMENT_SUCCESS.format(
                tariff=tariff_info["name"],
                amount=f"{amount:,.0f}",
                symbol=symbol,
                date=subscription_end.strftime('%d.%m.%Y')
            )

        try:
            bot.send_message(user_id, text, parse_mode="HTML")
            bot.restrict_chat_member(
                PRIVATE_CHAT_ID, user_id,
                can_send_messages=True, can_send_media_messages=True,
                can_send_other_messages=True, can_add_web_page_previews=True,
                until_date=int(subscription_end.timestamp())
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления {user_id}: {e}")

    return jsonify({"status": "ok"}), 200


@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    data = request.json
    logger.info(f"Stripe webhook: {data}")

    if data.get("type") == "checkout.session.completed":
        session = data.get("data", {}).get("object", {})
        user_id = int(session.get("metadata", {}).get("user_id"))
        amount = session.get("amount_total", 0) / 100

        user = db.get_user(user_id)
        is_recurrent = bool(user and user["subscription_end"]
                           and datetime.fromisoformat(user["subscription_end"]) > datetime.now())

        tariff_info, subscription_end = _process_successful_payment(
            user_id, amount, "eur", "stripe", is_recurrent
        )
        if not tariff_info:
            return jsonify({"error": "invalid amount"}), 400

        bot = Application.builder().token(TELEGRAM_BOT_TOKEN).build().bot
        symbol = "€"

        if is_recurrent:
            text = t.AUTO_RENEWAL.format(
                club=t.CLUB_NAME,
                tariff=tariff_info["name"],
                amount=f"{amount:.0f}",
                symbol=symbol,
                date=subscription_end.strftime('%d.%m.%Y')
            )
        else:
            text = t.PAYMENT_SUCCESS.format(
                tariff=tariff_info["name"],
                amount=f"{amount:.0f}",
                symbol=symbol,
                date=subscription_end.strftime('%d.%m.%Y')
            )

        try:
            bot.send_message(user_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка уведомления {user_id}: {e}")

    return jsonify({"status": "ok"}), 200


def run_flask():
    app.run(host="0.0.0.0", port=8080)


# ==================== КОМАНДЫ АДМИНА ====================
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    stats = db.get_stats()
    text = (
        f"📊 <b>Статистика «{t.CLUB_NAME}»</b>\n\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"🎁 На пробном периоде: {stats['on_trial']}\n"
        f"💎 Активных подписок: {stats['active_subscriptions']}\n"
        f"⚠️ Отменили продление: {stats['cancelled']}\n"
        f"🚫 Заблокировали бота: {stats['blocked']}\n"
        f"💬 Получено отзывов: {stats['feedback_count']}\n\n"
        f"💰 Всего оплат: {stats['payments_count']}\n"
        f"💵 Сумма оплат: {stats['payments_sum']:,.0f} ₽\n\n"
        f"👥 <b>Реферальная система:</b>\n"
        f"• Пришли по рефералке: {stats['referral_users']}\n"
        f"• Всего приглашений: {stats['total_referrals']}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    users = db.get_all_users()
    if not users:
        await update.message.reply_text("Пользователей пока нет.")
        return

    text = f"👥 <b>Список пользователей ({len(users)})</b>\n\n"
    for user in users[:50]:
        uid = user["user_id"]
        username = user["username"]
        first_name = user["first_name"]
        trial_end = user["trial_end"]
        sub_end = user["subscription_end"]
        blocked = user["blocked_bot"]
        referred_by = user["referred_by"]
        referral_count = user["referral_count"]

        status = "🚫" if blocked else ("💎" if sub_end and datetime.fromisoformat(sub_end) > datetime.now() else ("🎁" if trial_end and datetime.fromisoformat(trial_end) > datetime.now() else "❌"))
        username_str = f"@{username}" if username else "без ника"
        ref_info = f" (от {referred_by})" if referred_by else ""
        ref_count = f" | 👥{referral_count}" if referral_count else ""
        
        text += f"{status} {first_name or 'Без имени'} ({username_str}) - ID: {uid}{ref_info}{ref_count}\n"
    
    if len(users) > 50:
        text += f"\n... и ещё {len(users) - 50} пользователей"

    await update.message.reply_text(text, parse_mode="HTML")


async def blocked_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    users = db.get_all_users()
    blocked_users = [u for u in users if u["blocked_bot"]]

    if not blocked_users:
        await update.message.reply_text("Никто не заблокировал бота.")
        return

    text = f"🚫 <b>Заблокировали бота ({len(blocked_users)})</b>\n\n"
    for user in blocked_users:
        uid = user["user_id"]
        username = user["username"]
        first_name = user["first_name"]
        username_str = f"@{username}" if username else "без ника"
        text += f"• {first_name or 'Без имени'} ({username_str}) - ID: {uid}\n"

    await update.message.reply_text(text, parse_mode="HTML")


async def payments_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    payments = db.get_all_payments()
    if not payments:
        await update.message.reply_text("Платежей пока нет.")
        return

    text = f"💰 <b>Список платежей ({len(payments)})</b>\n\n"
    for payment in payments[:30]:
        uid = payment["user_id"]
        amount = payment["amount"]
        currency = payment["currency"]
        tariff = payment["tariff"]
        pay_system = payment["payment_system"]
        pay_date = payment["payment_date"]
        is_recurrent = payment["is_recurrent"]
        
        symbol = "₽" if currency == "RUB" else "€"
        recurrent_mark = " 🔄" if is_recurrent else ""
        text += f"💎 {amount:,.0f} {symbol}{recurrent_mark} - {tariff} ({pay_system})\n"
        text += f"   Пользователь ID: {uid} - {pay_date}\n\n"
    
    if len(payments) > 30:
        text += f"... и ещё {len(payments) - 30} платежей"

    await update.message.reply_text(text, parse_mode="HTML")


async def feedback_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    feedback = db.get_all_feedback()
    if not feedback:
        await update.message.reply_text("Отзывов пока нет.")
        return
    text = f"💬 <b>Отзывы клиентов ({len(feedback)})</b>\n\n"
    for fb in feedback[:30]:
        text += f"👤 ID {fb['user_id']} ({fb['created_at']}):\n{fb['message']}\n\n"
    await update.message.reply_text(text, parse_mode="HTML")


async def referrals_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает реферальную статистику."""
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    users = db.get_all_referrals()
    referrers = [u for u in users if u["referral_count"] > 0]

    if not referrers:
        await update.message.reply_text("Рефералов пока нет.")
        return

    text = f"👥 <b>Реферальная статистика ({len(referrers)})</b>\n\n"
    for user in referrers[:30]:
        uid = user["user_id"]
        username = user["username"]
        first_name = user["first_name"]
        ref_count = user["referral_count"]
        bonus_days = user["bonus_days"]
        username_str = f"@{username}" if username else "без ника"
        text += f"• {first_name or 'Без имени'} ({username_str}) - ID: {uid}\n"
        text += f"  Приглашено: {ref_count} | Бонусных дней: {bonus_days}\n\n"

    await update.message.reply_text(text, parse_mode="HTML")


# ==================== ЗАПУСК ====================
async def post_init(application: Application):
    """Устанавливает команды бота в меню."""
    await application.bot.set_my_commands([
        BotCommand("start", "Запуск бота"),
        BotCommand("mystatus", "Статус подписки"),
        BotCommand("currency", "Сменить валюту"),
        BotCommand("referral", "Реферальная программа"),
        BotCommand("cancel", "Отменить продление"),
        BotCommand("help", "Список команд"),
    ])


def main():
    db.init_db()

    flask_thread = Thread(target=run_flask)
    flask_thread.start()

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("mystatus", my_status))
    application.add_handler(CommandHandler("cancel", cancel_subscription_cmd))
    application.add_handler(CommandHandler("currency", currency_cmd))
    application.add_handler(CommandHandler("referral", referral_cmd))
    
    # Команды админа
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("users", users_cmd))
    application.add_handler(CommandHandler("blocked", blocked_cmd))
    application.add_handler(CommandHandler("payments", payments_cmd))
    application.add_handler(CommandHandler("feedback", feedback_cmd))
    application.add_handler(CommandHandler("referrals", referrals_cmd))

    # Callback'и
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(CallbackQueryHandler(currency_callback, pattern="^set_currency_"))

    # Обработчик текстовых сообщений (для отзывов)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_feedback))

    # Планировщик
    job_queue = application.job_queue
    job_queue.run_daily(check_trial_feedback, time=time(hour=10, minute=0))
    job_queue.run_daily(check_trial_ending, time=time(hour=10, minute=5))
    job_queue.run_daily(check_unpaid_users, time=time(hour=11, minute=0))

    logger.info(f"Бот «{t.CLUB_NAME}» запущен")
    application.run_polling()


if __name__ == "__main__":
    main()
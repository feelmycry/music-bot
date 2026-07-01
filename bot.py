import os
import logging
import asyncio
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from scraper import search_music, download_track
from database import init_db, log_search, get_stats, get_user_searches

load_dotenv()
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

WAITING_QUERY = 1
ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))


async def _retry(coro_fn, retries=3, delay=2):
    """Повторяет вызов при сетевых ошибках."""
    for attempt in range(retries):
        try:
            return await coro_fn()
        except (NetworkError, TimedOut) as e:
            if attempt == retries - 1:
                raise
            logger.warning(f"Сетевая ошибка (попытка {attempt+1}/{retries}): {e}")
            await asyncio.sleep(delay)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🔍 Поиск музыки", callback_data="search")]]
    await update.message.reply_text(
        "Привет! 🎵 Я помогу найти и скачать музыку.\n\nНажми кнопку ниже:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def on_search_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Введите название трека или исполнителя:")
    return WAITING_QUERY


async def on_search_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_query = update.message.text.strip()
    if not user_query:
        await update.message.reply_text("Пустой запрос, попробуйте ещё раз.")
        return WAITING_QUERY

    try:
        msg = await _retry(lambda: update.message.reply_text(f"🔎 Ищу: «{user_query}»..."))
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение: {e}")
        return ConversationHandler.END

    try:
        results = await search_music(user_query)
    except Exception as e:
        logger.error(f"Ошибка поиска: {e}")
        await _retry(lambda: msg.edit_text("❌ Ошибка при поиске. Попробуйте позже."))
        return ConversationHandler.END

    user = update.effective_user
    await log_search(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        query=user_query,
        results_count=len(results),
    )

    if not results:
        await _retry(lambda: msg.edit_text(
            "😔 Ничего не найдено. Попробуйте другой запрос.\n\n"
            "Используйте /start чтобы начать заново."
        ))
        return ConversationHandler.END

    context.user_data['results'] = results

    keyboard = []
    for i, track in enumerate(results[:8]):
        label = f"{track['artist']} — {track['title']}"
        if len(label) > 60:
            label = label[:57] + "..."
        keyboard.append([InlineKeyboardButton(label, callback_data=f"dl_{i}")])

    await _retry(lambda: msg.edit_text(
        f"🎵 Найдено {len(results)} треков. Выберите:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    ))
    return ConversationHandler.END


async def on_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    idx = int(query.data.split('_')[1])
    results = context.user_data.get('results', [])

    if idx >= len(results):
        await query.message.reply_text("❌ Трек не найден. Попробуйте поиск заново.")
        return

    track = results[idx]
    artist = track['artist']
    title = track['title']
    url = track['download_url']

    msg = await _retry(lambda: query.message.reply_text(f"⏳ Скачиваю: {artist} — {title}..."))

    file_path = await download_track(url)

    if not file_path:
        await _retry(lambda: msg.edit_text(
            "❌ Не удалось скачать трек. Возможно, файл недоступен.\n"
            "Попробуйте другой трек или выполните новый поиск (/start)."
        ))
        return

    try:
        await _retry(lambda: msg.edit_text(f"📤 Отправляю: {artist} — {title}..."))
        with open(file_path, 'rb') as audio:
            await _retry(lambda: query.message.reply_audio(
                audio=audio,
                title=title,
                performer=artist,
                caption=f"🎵 {artist} — {title}\n\nСпизжено с бота <a href=\"https://t.me/my_realmusic_bot\">Мой музон</a>",
                parse_mode="HTML",
            ))
        await msg.delete()
    except Exception as e:
        logger.error(f"Ошибка отправки аудио: {e}")
        await _retry(lambda: msg.edit_text("❌ Не удалось отправить файл. Попробуйте ещё раз."))
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


async def on_search_again(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🔍 Поиск музыки", callback_data="search")]]
    await update.message.reply_text(
        "Нажмите кнопку для нового поиска:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено. Используйте /start для нового поиска.")
    return ConversationHandler.END


def _check_admin(user) -> str | None:
    """Возвращает текст ошибки или None если доступ разрешён."""
    if ADMIN_ID == 0:
        return (
            f"⚠️ ADMIN_TELEGRAM_ID не задан.\n\n"
            f"Твой Telegram ID: <code>{user.id}</code>\n\n"
            f"Добавь в Railway переменную:\n"
            f"<code>ADMIN_TELEGRAM_ID = {user.id}</code>"
        )
    if user.id != ADMIN_ID:
        return "⛔ Нет доступа."
    return None


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    err = _check_admin(user)
    if err:
        await update.message.reply_text(err, parse_mode="HTML")
        return

    stats = await get_stats()

    lines = [
        f"👥 <b>Пользователей:</b> {stats['total_users']}",
        f"🔎 <b>Всего запросов:</b> {stats['total_searches']}",
        "",
        "👤 <b>Нажми на пользователя чтобы увидеть его запросы:</b>",
    ]
    keyboard = []
    for u in stats["users"]:
        name = u["full_name"] or u["username"] or f"id{u['user_id']}"
        label = f"{name} — {u['cnt']} запр."
        if len(label) > 60:
            label = label[:57] + "..."
        keyboard.append([InlineKeyboardButton(label, callback_data=f"adm_{u['user_id']}")])

    text = "\n".join(lines)
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
    )


async def on_admin_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    err = _check_admin(user)
    if err:
        await query.message.reply_text(err, parse_mode="HTML")
        return

    target_id = int(query.data.split('_')[1])
    data = await get_user_searches(target_id)

    if not data:
        await query.message.reply_text("Пользователь не найден.")
        return

    name = data["full_name"] or data["username"] or f"id{target_id}"
    tag = f"@{data['username']}" if data["username"] else "—"

    lines = [
        f"👤 <b>{name}</b>",
        f"🆔 ID: <code>{target_id}</code>",
        f"📱 Username: {tag}",
        f"🔎 Запросов: {len(data['searches'])}",
        "",
        "<b>Все запросы:</b>",
    ]
    for s in data["searches"]:
        dt = s["created_at"][:16].replace("T", " ")
        found = f"✅{s['results_count']}" if s["results_count"] else "❌0"
        lines.append(f"  [{dt}] {s['query']} ({found})")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n\n…(обрезано)"

    keyboard = [[InlineKeyboardButton("← Назад", callback_data="adm_back")]]
    await query.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def on_admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    err = _check_admin(user)
    if err:
        await query.message.reply_text(err, parse_mode="HTML")
        return

    stats = await get_stats()
    lines = [
        f"👥 <b>Пользователей:</b> {stats['total_users']}",
        f"🔎 <b>Всего запросов:</b> {stats['total_searches']}",
        "",
        "👤 <b>Нажми на пользователя чтобы увидеть его запросы:</b>",
    ]
    keyboard = []
    for u in stats["users"]:
        name = u["full_name"] or u["username"] or f"id{u['user_id']}"
        label = f"{name} — {u['cnt']} запр."
        if len(label) > 60:
            label = label[:57] + "..."
        keyboard.append([InlineKeyboardButton(label, callback_data=f"adm_{u['user_id']}")])

    text = "\n".join(lines)
    await query.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(f"Твой Telegram ID: <code>{user.id}</code>", parse_mode="HTML")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Необработанная ошибка: {context.error}", exc_info=context.error)
    if isinstance(update, Update):
        chat = update.effective_chat
        if chat:
            try:
                await context.bot.send_message(
                    chat_id=chat.id,
                    text="⚠️ Произошла ошибка. Попробуйте ещё раз или нажмите /start."
                )
            except Exception:
                pass


def main():
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError("Не задан TELEGRAM_BOT_TOKEN в файле .env")

    async def _post_init(application):
        await init_db()

    app = (
        Application.builder()
        .token(token)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(60)
        .pool_timeout(30)
        .post_init(_post_init)
        .build()
    )

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_search_button, pattern='^search$')],
        states={
            WAITING_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_search_query)
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_chat=True,
        per_user=True,
        per_message=False,
        allow_reentry=True,
    )

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('search', on_search_again))
    app.add_handler(CommandHandler('admin', admin))
    app.add_handler(CommandHandler('myid', myid))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(on_download, pattern='^dl_'))
    app.add_handler(CallbackQueryHandler(on_admin_back, pattern='^adm_back$'))
    app.add_handler(CallbackQueryHandler(on_admin_user, pattern='^adm_\\d+$'))
    app.add_error_handler(error_handler)

    print("Бот запущен...", flush=True)
    logger.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"FATAL ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise

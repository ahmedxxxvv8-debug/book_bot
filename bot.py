import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from supabase import create_client

# ============ الإعدادات ============
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
ACCESS_PASSWORD = os.environ["ACCESS_PASSWORD"]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(level=logging.INFO)

YEARS = {
    "1": "السنة الأولى",
    "2": "السنة الثانية",
    "3": "السنة الثالثة",
    "4": "السنة الرابعة",
    "5": "السنة الخامسة",
}

TERMS = {
    "1": "الترم الأول",
    "2": "الترم الثاني",
}


# ============ التحقق من الصلاحية ============
def is_authorized(chat_id):
    result = (
        supabase.table("authorized_users")
        .select("chat_id")
        .eq("chat_id", chat_id)
        .execute()
    )
    return len(result.data) > 0


def authorize_user(chat_id):
    supabase.table("authorized_users").insert({"chat_id": chat_id}).execute()


async def require_auth_message(update: Update) -> bool:
    """يرجع True لو المستخدم مسموح له، وإلا يرد برسالة ويرجع False"""
    chat_id = update.message.chat_id
    if is_authorized(chat_id):
        return True
    await update.message.reply_text(
        "🔒 البوت خاص، اكتب الباسورد الأول:\n/login الباسورد"
    )
    return False


async def require_auth_callback(update: Update) -> bool:
    query = update.callback_query
    chat_id = query.message.chat_id
    if is_authorized(chat_id):
        return True
    await query.answer("🔒 لازم تسجل دخول الأول بالباسورد", show_alert=True)
    return False


# ============ تسجيل الدخول ============
async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id

    if is_authorized(chat_id):
        await update.message.reply_text("✅ أنت مسجل دخول بالفعل.")
        return

    if not context.args:
        await update.message.reply_text("اكتب كده: /login الباسورد")
        return

    entered_password = " ".join(context.args)

    if entered_password == ACCESS_PASSWORD:
        authorize_user(chat_id)
        await update.message.reply_text("✅ تم الدخول بنجاح! اكتب /start عشان تشوف الأوامر.")
    else:
        await update.message.reply_text("❌ باسورد غلط.")


def years_keyboard(prefix):
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"{prefix}:{key}")]
        for key, label in YEARS.items()
    ]
    return InlineKeyboardMarkup(buttons)


def terms_keyboard(prefix, year):
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"{prefix}:{year}:{key}")]
        for key, label in TERMS.items()
    ]
    return InlineKeyboardMarkup(buttons)


def subjects_keyboard(prefix, year, term, subjects, include_new=False):
    buttons = [
        [InlineKeyboardButton(s, callback_data=f"{prefix}:{year}:{term}:{s}")]
        for s in subjects
    ]
    if include_new:
        buttons.append(
            [InlineKeyboardButton("➕ مادة جديدة", callback_data=f"newsubject:{year}:{term}")]
        )
    return InlineKeyboardMarkup(buttons)


def get_subjects(year, term):
    result = (
        supabase.table("books")
        .select("subject")
        .eq("year", year)
        .eq("term", term)
        .execute()
    )
    subjects = sorted(set(r["subject"] for r in result.data if r.get("subject")))
    return subjects


# ============ استقبال ملف PDF أو صورة ============
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return

    message = update.message
    caption = message.caption

    if not caption:
        await message.reply_text(
            "⚠️ لازم تكتب اسم للملف في خانة الوصف (caption) قبل الإرسال.\n"
            "مثال: تشريح - محاضرة 1"
        )
        return

    if message.document:
        file_id_original = message.document.file_id
        file_name = message.document.file_name or "ملف"
    elif message.photo:
        file_id_original = message.photo[-1].file_id
        file_name = "صورة"
    else:
        return

    forwarded = await context.bot.forward_message(
        chat_id=CHANNEL_ID,
        from_chat_id=message.chat_id,
        message_id=message.message_id,
    )

    context.user_data["pending_upload"] = {
        "title": caption,
        "file_name": file_name,
        "telegram_file_id": file_id_original,
        "channel_message_id": forwarded.message_id,
        "owner_chat_id": message.chat_id,
    }

    await message.reply_text(
        "📅 اختار السنة الدراسية لهذا الملف:",
        reply_markup=years_keyboard("saveyear"),
    )


# ============ اختيار السنة وقت الحفظ ============
async def handle_save_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_callback(update):
        return
    query = update.callback_query
    await query.answer()
    year = query.data.split(":")[1]

    if "pending_upload" not in context.user_data:
        await query.edit_message_text("⚠️ حصل خطأ، ابعت الملف تاني من فضلك.")
        return

    await query.edit_message_text(
        f"📆 اختار الترم ({YEARS[year]}):",
        reply_markup=terms_keyboard("saveterm", year),
    )


# ============ اختيار الترم وقت الحفظ ============
async def handle_save_term(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_callback(update):
        return
    query = update.callback_query
    await query.answer()
    _, year, term = query.data.split(":")

    subjects = get_subjects(year, term)

    await query.edit_message_text(
        f"📚 اختار المادة ({YEARS[year]} - {TERMS[term]}):",
        reply_markup=subjects_keyboard("savesubject", year, term, subjects, include_new=True),
    )


# ============ طلب اسم مادة جديدة ============
async def handle_new_subject_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_callback(update):
        return
    query = update.callback_query
    await query.answer()
    _, year, term = query.data.split(":")
    context.user_data["awaiting_new_subject"] = (year, term)
    await query.edit_message_text("✏️ اكتب اسم المادة الجديدة في رسالة:")


# ============ استقبال اسم مادة جديدة كنص ============
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "awaiting_new_subject" not in context.user_data:
        return

    if not await require_auth_message(update):
        return

    year, term = context.user_data.pop("awaiting_new_subject")
    subject = update.message.text.strip()

    pending = context.user_data.get("pending_upload")
    if not pending:
        await update.message.reply_text("⚠️ حصل خطأ، ابعت الملف تاني من فضلك.")
        return

    await finalize_save(update.message, context, year, term, subject)


# ============ اختيار مادة موجودة وقت الحفظ ============
async def handle_save_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_callback(update):
        return
    query = update.callback_query
    await query.answer()
    _, year, term, subject = query.data.split(":", 3)
    await finalize_save(query, context, year, term, subject, is_callback=True)


async def finalize_save(target, context, year, term, subject, is_callback=False):
    pending = context.user_data.pop("pending_upload", None)
    if not pending:
        text = "⚠️ حصل خطأ، ابعت الملف تاني من فضلك."
        if is_callback:
            await target.edit_message_text(text)
        else:
            await target.reply_text(text)
        return

    supabase.table("books").insert(
        {
            **pending,
            "year": year,
            "term": term,
            "subject": subject,
        }
    ).execute()

    text = f"✅ اتحفظ: {pending['title']}\n📅 {YEARS[year]} - {TERMS[term]}\n📚 {subject}"
    if is_callback:
        await target.edit_message_text(text)
    else:
        await target.reply_text(text)


# ============ قائمة التصفح: /menu ============
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    await update.message.reply_text(
        "📅 اختار السنة الدراسية:",
        reply_markup=years_keyboard("browseyear"),
    )


async def handle_browse_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_callback(update):
        return
    query = update.callback_query
    await query.answer()
    year = query.data.split(":")[1]
    await query.edit_message_text(
        f"📆 اختار الترم ({YEARS[year]}):",
        reply_markup=terms_keyboard("browseterm", year),
    )


async def handle_browse_term(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_callback(update):
        return
    query = update.callback_query
    await query.answer()
    _, year, term = query.data.split(":")
    subjects = get_subjects(year, term)

    if not subjects:
        await query.edit_message_text(f"لا يوجد كتب محفوظة في {YEARS[year]} - {TERMS[term]} بعد.")
        return

    await query.edit_message_text(
        f"📚 اختار المادة ({YEARS[year]} - {TERMS[term]}):",
        reply_markup=subjects_keyboard("browsesubject", year, term, subjects),
    )


async def handle_browse_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_callback(update):
        return
    query = update.callback_query
    await query.answer()
    _, year, term, subject = query.data.split(":", 3)

    result = (
        supabase.table("books")
        .select("*")
        .eq("year", year)
        .eq("term", term)
        .eq("subject", subject)
        .execute()
    )

    rows = result.data
    if not rows:
        await query.edit_message_text("❌ مفيش ملفات في المادة دي.")
        return

    await query.edit_message_text(f"📚 ملفات {subject}:")
    for row in rows:
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=row["telegram_file_id"],
            caption=f"📘 {row['title']}",
        )


# ============ البحث بالاسم ============
async def find_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return

    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("اكتب كده: /find اسم الكتاب أو جزء من الاسم")
        return

    result = (
        supabase.table("books")
        .select("*")
        .ilike("title", f"%{query}%")
        .execute()
    )

    rows = result.data
    if not rows:
        await update.message.reply_text("❌ مفيش نتايج بالاسم ده.")
        return

    for row in rows:
        await context.bot.send_document(
            chat_id=update.message.chat_id,
            document=row["telegram_file_id"],
            caption=f"📘 {row['title']}",
        )


# ============ رسالة البداية ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if not is_authorized(chat_id):
        await update.message.reply_text(
            "🔒 أهلاً بيك، البوت ده خاص.\nاكتب: /login الباسورد"
        )
        return

    await update.message.reply_text(
        "أهلاً بيك 👋\n\n"
        "علشان تحفظ ملف: ابعته PDF أو صورة، واكتب اسم الكتاب في خانة الوصف (caption)، وهيطلب منك تحدد السنة والترم والمادة.\n"
        "علشان تتصفح: /menu\n"
        "علشان تدور بالاسم: /find اسم الكتاب"
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("login", login))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("find", find_book))

    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_handler(CallbackQueryHandler(handle_save_year, pattern=r"^saveyear:"))
    app.add_handler(CallbackQueryHandler(handle_save_term, pattern=r"^saveterm:"))
    app.add_handler(CallbackQueryHandler(handle_new_subject_button, pattern=r"^newsubject:"))
    app.add_handler(CallbackQueryHandler(handle_save_subject, pattern=r"^savesubject:"))
    app.add_handler(CallbackQueryHandler(handle_browse_year, pattern=r"^browseyear:"))
    app.add_handler(CallbackQueryHandler(handle_browse_term, pattern=r"^browseterm:"))
    app.add_handler(CallbackQueryHandler(handle_browse_subject, pattern=r"^browsesubject:"))

    app.run_polling()


if __name__ == "__main__":
    main()

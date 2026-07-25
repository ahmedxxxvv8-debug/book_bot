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
VIEWER_PASSWORD = os.environ.get("VIEWER_PASSWORD")  # اختياري

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

FILE_TYPES = {
    "lecture": "📖 محاضرة",
    "sheet": "📄 شيت",
    "summary": "📝 ملخص",
}


# ============ الصلاحيات ============
def get_role(chat_id):
    result = (
        supabase.table("authorized_users")
        .select("role")
        .eq("chat_id", chat_id)
        .execute()
    )
    if not result.data:
        return None
    return result.data[0].get("role", "admin")


def is_authorized(chat_id):
    return get_role(chat_id) is not None


def is_admin(chat_id):
    return get_role(chat_id) == "admin"


def authorize_user(chat_id, role):
    supabase.table("authorized_users").insert({"chat_id": chat_id, "role": role}).execute()


async def require_auth_message(update: Update) -> bool:
    chat_id = update.message.chat_id
    if is_authorized(chat_id):
        return True
    await update.message.reply_text("🔒 البوت خاص، اكتب الباسورد الأول:\n/login الباسورد")
    return False


async def require_auth_callback(update: Update) -> bool:
    query = update.callback_query
    chat_id = query.message.chat_id
    if is_authorized(chat_id):
        return True
    await query.answer("🔒 لازم تسجل دخول الأول بالباسورد", show_alert=True)
    return False


async def require_admin_message(update: Update) -> bool:
    chat_id = update.message.chat_id
    if not is_authorized(chat_id):
        await update.message.reply_text("🔒 البوت خاص، اكتب الباسورد الأول:\n/login الباسورد")
        return False
    if not is_admin(chat_id):
        await update.message.reply_text("⛔ الميزة دي للأدمن بس.")
        return False
    return True


async def require_admin_callback(update: Update) -> bool:
    query = update.callback_query
    chat_id = query.message.chat_id
    if not is_authorized(chat_id):
        await query.answer("🔒 لازم تسجل دخول الأول", show_alert=True)
        return False
    if not is_admin(chat_id):
        await query.answer("⛔ الميزة دي للأدمن بس", show_alert=True)
        return False
    return True


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
        authorize_user(chat_id, "admin")
        await update.message.reply_text("✅ تم الدخول كأدمن! اكتب /start.")
    elif VIEWER_PASSWORD and entered_password == VIEWER_PASSWORD:
        authorize_user(chat_id, "viewer")
        await update.message.reply_text("✅ تم الدخول كمشاهد! اكتب /start.")
    else:
        await update.message.reply_text("❌ باسورد غلط.")


# ============ أدوات الأزرار ============
def years_keyboard(prefix):
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"{prefix}:{key}")]
        for key, label in YEARS.items()
    ]
    return InlineKeyboardMarkup(buttons)


def terms_keyboard(prefix):
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"{prefix}:{key}")]
        for key, label in TERMS.items()
    ]
    return InlineKeyboardMarkup(buttons)


def types_keyboard(prefix):
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"{prefix}:{key}")]
        for key, label in FILE_TYPES.items()
    ]
    return InlineKeyboardMarkup(buttons)


def subjects_keyboard(prefix, subjects, include_new=False):
    buttons = [
        [InlineKeyboardButton(s, callback_data=f"{prefix}:{i}")]
        for i, s in enumerate(subjects)
    ]
    if include_new:
        buttons.append([InlineKeyboardButton("➕ مادة جديدة", callback_data="newsubject")])
    return InlineKeyboardMarkup(buttons)


def get_subjects(year, term):
    result = (
        supabase.table("books")
        .select("subject")
        .eq("year", year)
        .eq("term", term)
        .execute()
    )
    return sorted(set(r["subject"] for r in result.data if r.get("subject")))


def type_label(file_type):
    return FILE_TYPES.get(file_type, "")


# ============ استقبال ملف PDF أو صورة (رفع) ============
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_message(update):
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

    last = context.user_data.get("last_selection")
    if last:
        text = (
            f"📌 آخر تصنيف استخدمته:\n"
            f"{YEARS[last['year']]} - {TERMS[last['term']]} - {last['subject']} - {type_label(last['file_type'])}\n\n"
            "تحب تستخدم نفس التصنيف؟"
        )
        buttons = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ نفس التصنيف", callback_data="quickuse")],
                [InlineKeyboardButton("🔄 تصنيف جديد", callback_data="newclassification")],
            ]
        )
        await message.reply_text(text, reply_markup=buttons)
        return

    await message.reply_text(
        "📅 اختار السنة الدراسية لهذا الملف:",
        reply_markup=years_keyboard("saveyear"),
    )


async def handle_quickuse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    last = context.user_data.get("last_selection")
    if not last:
        await query.edit_message_text("⚠️ حصل خطأ، اختار من الأول.")
        return
    await finalize_save(query, context, last["year"], last["term"], last["subject"], last["file_type"], is_callback=True)


async def handle_newclassification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📅 اختار السنة الدراسية لهذا الملف:",
        reply_markup=years_keyboard("saveyear"),
    )


# ============ اختيار السنة ============
async def handle_save_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    year = query.data.split(":")[1]

    if "pending_upload" not in context.user_data:
        await query.edit_message_text("⚠️ حصل خطأ، ابعت الملف تاني من فضلك.")
        return

    context.user_data["pending_year"] = year
    await query.edit_message_text(
        f"📆 اختار الترم ({YEARS[year]}):",
        reply_markup=terms_keyboard("saveterm"),
    )


# ============ اختيار الترم ============
async def handle_save_term(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    term = query.data.split(":")[1]
    year = context.user_data.get("pending_year")

    context.user_data["pending_term"] = term
    subjects = get_subjects(year, term)
    context.user_data["save_subjects_list"] = subjects

    await query.edit_message_text(
        f"📚 اختار المادة ({YEARS[year]} - {TERMS[term]}):",
        reply_markup=subjects_keyboard("savesubject", subjects, include_new=True),
    )


# ============ اختيار مادة موجودة ============
async def handle_save_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[1])
    subjects = context.user_data.get("save_subjects_list", [])
    if idx >= len(subjects):
        await query.edit_message_text("⚠️ حصل خطأ، ابدأ من الأول.")
        return
    subject = subjects[idx]
    context.user_data["pending_subject"] = subject

    await query.edit_message_text(
        "🏷️ نوع الملف؟",
        reply_markup=types_keyboard("savetype"),
    )


# ============ طلب اسم مادة جديدة ============
async def handle_new_subject_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting_new_subject"] = True
    await query.edit_message_text("✏️ اكتب اسم المادة الجديدة في رسالة:")


# ============ اختيار نوع الملف ============
async def handle_save_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    file_type = query.data.split(":")[1]

    year = context.user_data.get("pending_year")
    term = context.user_data.get("pending_term")
    subject = context.user_data.get("pending_subject")

    await finalize_save(query, context, year, term, subject, file_type, is_callback=True)


async def finalize_save(target, context, year, term, subject, file_type, is_callback=False):
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
            "file_type": file_type,
        }
    ).execute()

    context.user_data["last_selection"] = {
        "year": year,
        "term": term,
        "subject": subject,
        "file_type": file_type,
    }
    context.user_data.pop("pending_year", None)
    context.user_data.pop("pending_term", None)
    context.user_data.pop("pending_subject", None)

    text = (
        f"✅ اتحفظ: {pending['title']}\n"
        f"📅 {YEARS[year]} - {TERMS[term]}\n"
        f"📚 {subject} | {type_label(file_type)}"
    )
    if is_callback:
        await target.edit_message_text(text)
    else:
        await target.reply_text(text)


# ============ استقبال نص (مادة جديدة / إعادة تسمية / بحث مباشر) ============
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if not is_authorized(chat_id):
        return

    text = update.message.text.strip()

    # 1) مادة جديدة وقت الحفظ
    if context.user_data.get("awaiting_new_subject"):
        if not is_admin(chat_id):
            await update.message.reply_text("⛔ الميزة دي للأدمن بس.")
            return
        context.user_data.pop("awaiting_new_subject")
        year = context.user_data.get("pending_year")
        term = context.user_data.get("pending_term")
        context.user_data["pending_subject"] = text
        await update.message.reply_text(
            "🏷️ نوع الملف؟",
            reply_markup=types_keyboard("savetype"),
        )
        return

    # 2) إعادة تسمية ملف
    if "awaiting_rename_id" in context.user_data:
        if not is_admin(chat_id):
            await update.message.reply_text("⛔ الميزة دي للأدمن بس.")
            return
        book_id = context.user_data.pop("awaiting_rename_id")
        supabase.table("books").update({"title": text}).eq("id", book_id).execute()
        await update.message.reply_text(f"✅ اتغير الاسم إلى: {text}")
        return

    # 3) بحث مباشر بأي كلمة (اسم كتاب أو مادة)
    result = (
        supabase.table("books")
        .select("*")
        .or_(f"title.ilike.%{text}%,subject.ilike.%{text}%")
        .execute()
    )
    rows = result.data
    if not rows:
        return  # متردش لو مفيش نتايج، عشان ميضايقش لو كتب كلام عادي

    for row in rows:
        caption = f"📘 {row['title']}"
        if row.get("file_type"):
            caption += f" | {type_label(row['file_type'])}"
        await context.bot.send_document(
            chat_id=chat_id,
            document=row["telegram_file_id"],
            caption=caption,
        )


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
    context.user_data["browse_year"] = year
    await query.edit_message_text(
        f"📆 اختار الترم ({YEARS[year]}):",
        reply_markup=terms_keyboard("browseterm"),
    )


async def handle_browse_term(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_callback(update):
        return
    query = update.callback_query
    await query.answer()
    term = query.data.split(":")[1]
    year = context.user_data.get("browse_year")
    context.user_data["browse_term"] = term

    subjects = get_subjects(year, term)
    if not subjects:
        await query.edit_message_text(f"لا يوجد كتب محفوظة في {YEARS[year]} - {TERMS[term]} بعد.")
        return

    context.user_data["browse_subjects_list"] = subjects
    await query.edit_message_text(
        f"📚 اختار المادة ({YEARS[year]} - {TERMS[term]}):",
        reply_markup=subjects_keyboard("browsesubject", subjects),
    )


async def handle_browse_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_callback(update):
        return
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[1])
    subjects = context.user_data.get("browse_subjects_list", [])
    if idx >= len(subjects):
        await query.edit_message_text("⚠️ حصل خطأ، ابدأ من /menu تاني.")
        return
    subject = subjects[idx]
    year = context.user_data.get("browse_year")
    term = context.user_data.get("browse_term")

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
        caption = f"📘 {row['title']}"
        if row.get("file_type"):
            caption += f" | {type_label(row['file_type'])}"
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=row["telegram_file_id"],
            caption=caption,
        )


# ============ البحث بالاسم: /find ============
async def find_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return

    query_text = " ".join(context.args)
    if not query_text:
        await update.message.reply_text("اكتب كده: /find اسم الكتاب أو المادة")
        return

    result = (
        supabase.table("books")
        .select("*")
        .or_(f"title.ilike.%{query_text}%,subject.ilike.%{query_text}%")
        .execute()
    )

    rows = result.data
    if not rows:
        await update.message.reply_text("❌ مفيش نتايج بالاسم ده.")
        return

    for row in rows:
        caption = f"📘 {row['title']}"
        if row.get("file_type"):
            caption += f" | {type_label(row['file_type'])}"
        await context.bot.send_document(
            chat_id=update.message.chat_id,
            document=row["telegram_file_id"],
            caption=caption,
        )


# ============ حذف ملف: /delete ============
async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_message(update):
        return

    query_text = " ".join(context.args)
    if not query_text:
        await update.message.reply_text("اكتب كده: /delete اسم الكتاب اللي عايز تمسحه")
        return

    result = (
        supabase.table("books")
        .select("id, title")
        .ilike("title", f"%{query_text}%")
        .execute()
    )
    rows = result.data
    if not rows:
        await update.message.reply_text("❌ مفيش نتايج بالاسم ده.")
        return

    buttons = [
        [InlineKeyboardButton(r["title"][:40], callback_data=f"delete:{r['id']}")]
        for r in rows
    ]
    await update.message.reply_text(
        "اختار الملف اللي عايز تمسحه:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_delete_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    book_id = query.data.split(":")[1]

    buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ تأكيد الحذف", callback_data=f"deleteconfirm:{book_id}"),
                InlineKeyboardButton("❌ إلغاء", callback_data="deletecancel"),
            ]
        ]
    )
    await query.edit_message_text("متأكد إنك عايز تمسح الملف ده؟", reply_markup=buttons)


async def handle_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    book_id = query.data.split(":")[1]
    supabase.table("books").delete().eq("id", book_id).execute()
    await query.edit_message_text("🗑️ اتمسح الملف بنجاح.")


async def handle_delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("تم الإلغاء.")


# ============ إعادة تسمية: /rename ============
async def rename_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_message(update):
        return

    query_text = " ".join(context.args)
    if not query_text:
        await update.message.reply_text("اكتب كده: /rename اسم الكتاب اللي عايز تعدله")
        return

    result = (
        supabase.table("books")
        .select("id, title")
        .ilike("title", f"%{query_text}%")
        .execute()
    )
    rows = result.data
    if not rows:
        await update.message.reply_text("❌ مفيش نتايج بالاسم ده.")
        return

    buttons = [
        [InlineKeyboardButton(r["title"][:40], callback_data=f"rename:{r['id']}")]
        for r in rows
    ]
    await update.message.reply_text(
        "اختار الملف اللي عايز تغير اسمه:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_rename_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    book_id = query.data.split(":")[1]
    context.user_data["awaiting_rename_id"] = book_id
    await query.edit_message_text("✏️ اكتب الاسم الجديد في رسالة:")


# ============ عداد الملفات: /stats ============
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return

    result = supabase.table("books").select("year, term, subject").execute()
    rows = result.data
    total = len(rows)

    if total == 0:
        await update.message.reply_text("لسه مفيش أي ملفات محفوظة.")
        return

    counts = {}
    for r in rows:
        y = r.get("year")
        t = r.get("term")
        if y and t:
            key = f"{YEARS.get(y, y)} - {TERMS.get(t, t)}"
            counts[key] = counts.get(key, 0) + 1

    lines = [f"📊 إجمالي الملفات: {total}\n"]
    for key, count in sorted(counts.items()):
        lines.append(f"• {key}: {count} ملف")

    await update.message.reply_text("\n".join(lines))


# ============ رسالة البداية ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if not is_authorized(chat_id):
        await update.message.reply_text("🔒 أهلاً بيك، البوت ده خاص.\nاكتب: /login الباسورد")
        return

    admin = is_admin(chat_id)
    text = "أهلاً بيك 👋\n\n"
    if admin:
        text += (
            "علشان تحفظ ملف: ابعته PDF أو صورة، واكتب اسم الكتاب في الوصف.\n"
            "علشان تمسح ملف: /delete اسم الملف\n"
            "علشان تغير اسم ملف: /rename اسم الملف\n"
        )
    text += (
        "علشان تتصفح: /menu\n"
        "علشان تدور: /find اسم الكتاب أو المادة (أو اكتبه عادي من غير أمر)\n"
        "علشان تشوف عدد الملفات: /stats"
    )
    await update.message.reply_text(text)


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("login", login))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("find", find_book))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("rename", rename_command))
    app.add_handler(CommandHandler("stats", stats_command))

    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_handler(CallbackQueryHandler(handle_quickuse, pattern=r"^quickuse$"))
    app.add_handler(CallbackQueryHandler(handle_newclassification, pattern=r"^newclassification$"))
    app.add_handler(CallbackQueryHandler(handle_save_year, pattern=r"^saveyear:"))
    app.add_handler(CallbackQueryHandler(handle_save_term, pattern=r"^saveterm:"))
    app.add_handler(CallbackQueryHandler(handle_new_subject_button, pattern=r"^newsubject$"))
    app.add_handler(CallbackQueryHandler(handle_save_subject, pattern=r"^savesubject:"))
    app.add_handler(CallbackQueryHandler(handle_save_type, pattern=r"^savetype:"))

    app.add_handler(CallbackQueryHandler(handle_browse_year, pattern=r"^browseyear:"))
    app.add_handler(CallbackQueryHandler(handle_browse_term, pattern=r"^browseterm:"))
    app.add_handler(CallbackQueryHandler(handle_browse_subject, pattern=r"^browsesubject:"))

    app.add_handler(CallbackQueryHandler(handle_delete_select, pattern=r"^delete:"))
    app.add_handler(CallbackQueryHandler(handle_delete_confirm, pattern=r"^deleteconfirm:"))
    app.add_handler(CallbackQueryHandler(handle_delete_cancel, pattern=r"^deletecancel$"))
    app.add_handler(CallbackQueryHandler(handle_rename_select, pattern=r"^rename:"))

    app.run_polling()


if __name__ == "__main__":
    main()

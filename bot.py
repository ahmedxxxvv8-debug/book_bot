import os
import csv
import io
import gc
import asyncio
import logging
import tempfile
import datetime
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
from google import genai

# ============ الإعدادات ============
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
ACCESS_PASSWORD = os.environ["ACCESS_PASSWORD"]
VIEWER_PASSWORD = os.environ.get("VIEWER_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
else:
    gemini_client = None

GEMINI_MODEL_NAME = "gemini-flash-latest"

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

NO_SECTION = "__NONE__"


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


def get_all_admin_chat_ids():
    result = supabase.table("authorized_users").select("chat_id").eq("role", "admin").execute()
    return [r["chat_id"] for r in result.data]


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
    buttons = [[InlineKeyboardButton(l, callback_data=f"{prefix}:{k}")] for k, l in YEARS.items()]
    return InlineKeyboardMarkup(buttons)


def terms_keyboard(prefix):
    buttons = [[InlineKeyboardButton(l, callback_data=f"{prefix}:{k}")] for k, l in TERMS.items()]
    return InlineKeyboardMarkup(buttons)


def types_keyboard(prefix):
    buttons = [[InlineKeyboardButton(l, callback_data=f"{prefix}:{k}")] for k, l in FILE_TYPES.items()]
    return InlineKeyboardMarkup(buttons)


def subjects_keyboard(prefix, subjects, include_new=False):
    buttons = [[InlineKeyboardButton(s, callback_data=f"{prefix}:{i}")] for i, s in enumerate(subjects)]
    if include_new:
        buttons.append([InlineKeyboardButton("➕ مادة جديدة", callback_data="newsubject")])
    return InlineKeyboardMarkup(buttons)


def sections_keyboard_for_save(sections):
    buttons = [[InlineKeyboardButton(s, callback_data=f"savesection:{i}")] for i, s in enumerate(sections)]
    buttons.append([InlineKeyboardButton("🚫 بدون سكشن", callback_data="savesection_none")])
    buttons.append([InlineKeyboardButton("➕ سكشن جديد", callback_data="newsection")])
    return InlineKeyboardMarkup(buttons)


def sections_keyboard_for_browse(sections, has_none):
    buttons = [[InlineKeyboardButton(s, callback_data=f"browsesection:{i}")] for i, s in enumerate(sections)]
    if has_none:
        buttons.append([InlineKeyboardButton("🚫 بدون سكشن", callback_data="browsesection_none")])
    return InlineKeyboardMarkup(buttons)


def get_subjects(year, term):
    result = (
        supabase.table("books").select("subject")
        .eq("year", year).eq("term", term).is_("deleted_at", "null").execute()
    )
    return sorted(set(r["subject"] for r in result.data if r.get("subject")))


def get_sections(year, term, subject):
    result = (
        supabase.table("books").select("section")
        .eq("year", year).eq("term", term).eq("subject", subject).is_("deleted_at", "null").execute()
    )
    sections = sorted(set(r["section"] for r in result.data if r.get("section")))
    has_none = any(not r.get("section") for r in result.data)
    return sections, has_none


def type_label(file_type):
    return FILE_TYPES.get(file_type, "")


def build_caption(row):
    caption = f"📘 {row['title']}"
    if row.get("section"):
        caption += f" | سكشن {row['section']}"
    if row.get("file_type"):
        caption += f" | {type_label(row['file_type'])}"
    if row.get("reviewed"):
        caption += " | ✅ اتذاكر"
    return caption


# ============ استقبال ملف (رفع) ============
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_message(update):
        return

    message = update.message
    caption = message.caption

    if not caption:
        await message.reply_text(
            "⚠️ لازم تكتب اسم للملف في خانة الوصف (caption) قبل الإرسال.\nمثال: تشريح - محاضرة 1"
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
        chat_id=CHANNEL_ID, from_chat_id=message.chat_id, message_id=message.message_id,
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
        section_text = f" - سكشن {last['section']}" if last.get("section") else ""
        text = (
            f"📌 آخر تصنيف استخدمته:\n"
            f"{YEARS[last['year']]} - {TERMS[last['term']]} - {last['subject']}{section_text} - {type_label(last['file_type'])}\n\n"
            "تحب تستخدم نفس التصنيف؟"
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ نفس التصنيف", callback_data="quickuse")],
            [InlineKeyboardButton("🔄 تصنيف جديد", callback_data="newclassification")],
        ])
        await message.reply_text(text, reply_markup=buttons)
        return

    await message.reply_text("📅 اختار السنة الدراسية لهذا الملف:", reply_markup=years_keyboard("saveyear"))


async def handle_quickuse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    last = context.user_data.get("last_selection")
    if not last:
        await query.edit_message_text("⚠️ حصل خطأ، اختار من الأول.")
        return
    await finalize_save(query, context, last["year"], last["term"], last["subject"], last.get("section"), last["file_type"], is_callback=True)


async def handle_newclassification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📅 اختار السنة الدراسية لهذا الملف:", reply_markup=years_keyboard("saveyear"))


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
    await query.edit_message_text(f"📆 اختار الترم ({YEARS[year]}):", reply_markup=terms_keyboard("saveterm"))


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


async def go_to_section_step(target, context, is_callback=True):
    year = context.user_data.get("pending_year")
    term = context.user_data.get("pending_term")
    subject = context.user_data.get("pending_subject")
    sections, _ = get_sections(year, term, subject)
    context.user_data["save_sections_list"] = sections
    text = f"🔖 المادة دي ليها سكشن؟ ({subject})"
    keyboard = sections_keyboard_for_save(sections)
    if is_callback:
        await target.edit_message_text(text, reply_markup=keyboard)
    else:
        await target.reply_text(text, reply_markup=keyboard)


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
    context.user_data["pending_subject"] = subjects[idx]
    await go_to_section_step(query, context, is_callback=True)


async def handle_new_subject_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting_new_subject"] = True
    await query.edit_message_text("✏️ اكتب اسم المادة الجديدة في رسالة:")


async def handle_save_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[1])
    sections = context.user_data.get("save_sections_list", [])
    if idx >= len(sections):
        await query.edit_message_text("⚠️ حصل خطأ، ابدأ من الأول.")
        return
    context.user_data["pending_section"] = sections[idx]
    await query.edit_message_text("🏷️ نوع الملف؟", reply_markup=types_keyboard("savetype"))


async def handle_save_section_none(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    context.user_data["pending_section"] = None
    await query.edit_message_text("🏷️ نوع الملف؟", reply_markup=types_keyboard("savetype"))


async def handle_new_section_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting_new_section"] = True
    await query.edit_message_text("✏️ اكتب اسم أو رقم السكشن في رسالة:")


async def handle_save_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    file_type = query.data.split(":")[1]
    year = context.user_data.get("pending_year")
    term = context.user_data.get("pending_term")
    subject = context.user_data.get("pending_subject")
    section = context.user_data.get("pending_section")
    await finalize_save(query, context, year, term, subject, section, file_type, is_callback=True)


async def finalize_save(target, context, year, term, subject, section, file_type, is_callback=False):
    pending = context.user_data.pop("pending_upload", None)
    if not pending:
        text = "⚠️ حصل خطأ، ابعت الملف تاني من فضلك."
        if is_callback:
            await target.edit_message_text(text)
        else:
            await target.reply_text(text)
        return

    supabase.table("books").insert({
        **pending, "year": year, "term": term, "subject": subject,
        "section": section, "file_type": file_type, "reviewed": False,
    }).execute()

    context.user_data["last_selection"] = {
        "year": year, "term": term, "subject": subject, "section": section, "file_type": file_type,
    }
    for key in ("pending_year", "pending_term", "pending_subject", "pending_section"):
        context.user_data.pop(key, None)

    section_text = f" | سكشن {section}" if section else ""
    text = (
        f"✅ اتحفظ: {pending['title']}\n"
        f"📅 {YEARS[year]} - {TERMS[term]}\n"
        f"📚 {subject}{section_text} | {type_label(file_type)}"
    )
    if is_callback:
        await target.edit_message_text(text)
    else:
        await target.reply_text(text)


# ============ استقبال نص عام ============
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if not is_authorized(chat_id):
        return

    text = update.message.text.strip()

    if context.user_data.get("awaiting_new_subject"):
        if not is_admin(chat_id):
            await update.message.reply_text("⛔ الميزة دي للأدمن بس.")
            return
        context.user_data.pop("awaiting_new_subject")
        context.user_data["pending_subject"] = text
        await go_to_section_step(update.message, context, is_callback=False)
        return

    if context.user_data.get("awaiting_new_section"):
        if not is_admin(chat_id):
            await update.message.reply_text("⛔ الميزة دي للأدمن بس.")
            return
        context.user_data.pop("awaiting_new_section")
        context.user_data["pending_section"] = text
        await update.message.reply_text("🏷️ نوع الملف؟", reply_markup=types_keyboard("savetype"))
        return

    if "awaiting_rename_id" in context.user_data:
        if not is_admin(chat_id):
            await update.message.reply_text("⛔ الميزة دي للأدمن بس.")
            return
        book_id = context.user_data.pop("awaiting_rename_id")
        supabase.table("books").update({"title": text}).eq("id", book_id).execute()
        await update.message.reply_text(f"✅ اتغير الاسم إلى: {text}")
        return

    if "awaiting_question_book_id" in context.user_data:
        book_id = context.user_data.pop("awaiting_question_book_id")
        await answer_question_about_book(update.message, context, book_id, text)
        return

    if context.user_data.get("awaiting_remind_time"):
        context.user_data.pop("awaiting_remind_time")
        await handle_remind_time_input(update.message, context, text)
        return

    # بحث مباشر
    result = (
        supabase.table("books").select("*")
        .or_(f"title.ilike.%{text}%,subject.ilike.%{text}%")
        .is_("deleted_at", "null").execute()
    )
    rows = result.data
    if not rows:
        return

    for row in rows:
        await context.bot.send_document(chat_id=chat_id, document=row["telegram_file_id"], caption=build_caption(row))


# ============ التصفح: /menu ============
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    await update.message.reply_text("📅 اختار السنة الدراسية:", reply_markup=years_keyboard("browseyear"))


async def handle_browse_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_callback(update):
        return
    query = update.callback_query
    await query.answer()
    year = query.data.split(":")[1]
    context.user_data["browse_year"] = year
    await query.edit_message_text(f"📆 اختار الترم ({YEARS[year]}):", reply_markup=terms_keyboard("browseterm"))


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


async def send_files_for(query, context, year, term, subject, section):
    q = supabase.table("books").select("*").eq("year", year).eq("term", term).eq("subject", subject).is_("deleted_at", "null")
    if section == NO_SECTION or section is None:
        q = q.is_("section", "null")
    else:
        q = q.eq("section", section)
    result = q.execute()
    rows = result.data
    if not rows:
        await query.edit_message_text("❌ مفيش ملفات هنا.")
        return
    await query.edit_message_text(f"📚 ملفات {subject}:")
    for row in rows:
        await context.bot.send_document(chat_id=query.message.chat_id, document=row["telegram_file_id"], caption=build_caption(row))


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
    context.user_data["browse_subject"] = subject
    sections, has_none = get_sections(year, term, subject)
    if not sections:
        await send_files_for(query, context, year, term, subject, None)
        return
    context.user_data["browse_sections_list"] = sections
    await query.edit_message_text(f"🔖 اختار السكشن ({subject}):", reply_markup=sections_keyboard_for_browse(sections, has_none))


async def handle_browse_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_callback(update):
        return
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[1])
    sections = context.user_data.get("browse_sections_list", [])
    if idx >= len(sections):
        await query.edit_message_text("⚠️ حصل خطأ، ابدأ من /menu تاني.")
        return
    section = sections[idx]
    year = context.user_data.get("browse_year")
    term = context.user_data.get("browse_term")
    subject = context.user_data.get("browse_subject")
    await send_files_for(query, context, year, term, subject, section)


async def handle_browse_section_none(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_callback(update):
        return
    query = update.callback_query
    await query.answer()
    year = context.user_data.get("browse_year")
    term = context.user_data.get("browse_term")
    subject = context.user_data.get("browse_subject")
    await send_files_for(query, context, year, term, subject, NO_SECTION)


# ============ /find ============
async def find_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    query_text = " ".join(context.args)
    if not query_text:
        await update.message.reply_text("اكتب كده: /find اسم الكتاب أو المادة")
        return
    result = (
        supabase.table("books").select("*")
        .or_(f"title.ilike.%{query_text}%,subject.ilike.%{query_text}%")
        .is_("deleted_at", "null").execute()
    )
    rows = result.data
    if not rows:
        await update.message.reply_text("❌ مفيش نتايج بالاسم ده.")
        return
    for row in rows:
        await context.bot.send_document(chat_id=update.message.chat_id, document=row["telegram_file_id"], caption=build_caption(row))


# ============ /recent ============
async def recent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    result = (
        supabase.table("books").select("*")
        .is_("deleted_at", "null").order("created_at", desc=True).limit(5).execute()
    )
    rows = result.data
    if not rows:
        await update.message.reply_text("لسه مفيش أي ملفات محفوظة.")
        return
    await update.message.reply_text("🕐 آخر 5 ملفات:")
    for row in rows:
        await context.bot.send_document(chat_id=update.message.chat_id, document=row["telegram_file_id"], caption=build_caption(row))


# ============ حذف آمن: /delete (استرجاع خلال 24 ساعة) ============
async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_message(update):
        return
    query_text = " ".join(context.args)
    if not query_text:
        await update.message.reply_text("اكتب كده: /delete اسم الكتاب اللي عايز تمسحه")
        return
    result = (
        supabase.table("books").select("id, title")
        .ilike("title", f"%{query_text}%").is_("deleted_at", "null").execute()
    )
    rows = result.data
    if not rows:
        await update.message.reply_text("❌ مفيش نتايج بالاسم ده.")
        return
    buttons = [[InlineKeyboardButton(r["title"][:40], callback_data=f"delete:{r['id']}")] for r in rows]
    await update.message.reply_text("اختار الملف اللي عايز تمسحه:", reply_markup=InlineKeyboardMarkup(buttons))


async def handle_delete_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    book_id = query.data.split(":")[1]
    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تأكيد الحذف", callback_data=f"deleteconfirm:{book_id}"),
        InlineKeyboardButton("❌ إلغاء", callback_data="deletecancel"),
    ]])
    await query.edit_message_text("متأكد إنك عايز تمسح الملف ده؟ (هيفضل قابل للاسترجاع 24 ساعة)", reply_markup=buttons)


async def handle_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    book_id = query.data.split(":")[1]
    now = datetime.datetime.utcnow().isoformat()
    supabase.table("books").update({"deleted_at": now}).eq("id", book_id).execute()
    await query.edit_message_text("🗑️ اتمسح الملف. تقدر تسترجعه خلال 24 ساعة بأمر /trash.")


async def handle_delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("تم الإلغاء.")


# ============ /trash (استرجاع) ============
async def trash_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_message(update):
        return
    result = (
        supabase.table("books").select("id, title")
        .not_.is_("deleted_at", "null").execute()
    )
    rows = result.data
    if not rows:
        await update.message.reply_text("سلة المحذوفات فاضية.")
        return
    buttons = [[InlineKeyboardButton(f"↩️ {r['title'][:35]}", callback_data=f"restore:{r['id']}")] for r in rows]
    await update.message.reply_text("الملفات القابلة للاسترجاع (24 ساعة):", reply_markup=InlineKeyboardMarkup(buttons))


async def handle_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    book_id = query.data.split(":")[1]
    supabase.table("books").update({"deleted_at": None}).eq("id", book_id).execute()
    await query.edit_message_text("✅ اترجع الملف.")


# ============ /rename ============
async def rename_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_message(update):
        return
    query_text = " ".join(context.args)
    if not query_text:
        await update.message.reply_text("اكتب كده: /rename اسم الكتاب اللي عايز تعدله")
        return
    result = (
        supabase.table("books").select("id, title")
        .ilike("title", f"%{query_text}%").is_("deleted_at", "null").execute()
    )
    rows = result.data
    if not rows:
        await update.message.reply_text("❌ مفيش نتايج بالاسم ده.")
        return
    buttons = [[InlineKeyboardButton(r["title"][:40], callback_data=f"rename:{r['id']}")] for r in rows]
    await update.message.reply_text("اختار الملف اللي عايز تغير اسمه:", reply_markup=InlineKeyboardMarkup(buttons))


async def handle_rename_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_callback(update):
        return
    query = update.callback_query
    await query.answer()
    book_id = query.data.split(":")[1]
    context.user_data["awaiting_rename_id"] = book_id
    await query.edit_message_text("✏️ اكتب الاسم الجديد في رسالة:")


# ============ ✅ علامة اتذاكر: /reviewed ============
async def reviewed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    query_text = " ".join(context.args)
    if not query_text:
        await update.message.reply_text("اكتب كده: /reviewed اسم الكتاب")
        return
    result = (
        supabase.table("books").select("id, title, reviewed")
        .ilike("title", f"%{query_text}%").is_("deleted_at", "null").execute()
    )
    rows = result.data
    if not rows:
        await update.message.reply_text("❌ مفيش نتايج بالاسم ده.")
        return
    buttons = []
    for r in rows:
        mark = "✅" if r.get("reviewed") else "⬜"
        buttons.append([InlineKeyboardButton(f"{mark} {r['title'][:35]}", callback_data=f"toggleReviewed:{r['id']}")])
    await update.message.reply_text("دوس على الملف عشان تبدل حالة المذاكرة:", reply_markup=InlineKeyboardMarkup(buttons))


async def handle_toggle_reviewed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_callback(update):
        return
    query = update.callback_query
    await query.answer()
    book_id = query.data.split(":")[1]
    result = supabase.table("books").select("reviewed").eq("id", book_id).execute()
    current = result.data[0].get("reviewed", False) if result.data else False
    supabase.table("books").update({"reviewed": not current}).eq("id", book_id).execute()
    await query.edit_message_text("✅ اتحدثت حالة المذاكرة." if not current else "⬜ اتشالت علامة المذاكرة.")


# ============ /stats ============
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    result = supabase.table("books").select("year, term, subject, reviewed").is_("deleted_at", "null").execute()
    rows = result.data
    total = len(rows)
    if total == 0:
        await update.message.reply_text("لسه مفيش أي ملفات محفوظة.")
        return
    reviewed_count = sum(1 for r in rows if r.get("reviewed"))
    counts = {}
    for r in rows:
        y, t = r.get("year"), r.get("term")
        if y and t:
            key = f"{YEARS.get(y, y)} - {TERMS.get(t, t)}"
            counts[key] = counts.get(key, 0) + 1
    lines = [f"📊 إجمالي الملفات: {total}", f"✅ اتذاكر: {reviewed_count}\n"]
    for key, count in sorted(counts.items()):
        lines.append(f"• {key}: {count} ملف")
    await update.message.reply_text("\n".join(lines))


# ============ الذكاء الاصطناعي: تلخيص وسؤال ============
async def get_book_row(book_id):
    result = supabase.table("books").select("*").eq("id", book_id).execute()
    if not result.data:
        return None
    return result.data[0]


async def download_to_temp_file(context, telegram_file_id):
    """ينزل الملف مؤقتًا على قرص السيرفر (مش في الذاكرة) ويرجع مسار الملف"""
    tg_file = await context.bot.get_file(telegram_file_id)
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    await tg_file.download_to_drive(tmp_path)
    return tmp_path


async def cleanup_temp_and_gemini_file(tmp_path, gemini_file):
    """يمسح الملف المؤقت من Railway والملف المرفوع على جوجل، ويعمل تنظيف للذاكرة"""
    if gemini_file is not None:
        try:
            await asyncio.to_thread(gemini_client.files.delete, name=gemini_file.name)
        except Exception:
            logging.warning("تعذر مسح الملف من جوجل، هيتمسح تلقائيًا بعد 48 ساعة على أي حال.")

    if tmp_path and os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except Exception:
            logging.warning("تعذر مسح الملف المؤقت من السيرفر.")

    gc.collect()


async def send_long_text(message_or_query, text, is_callback=False):
    chunks = [text[i:i + 3500] for i in range(0, len(text), 3500)] or ["(مفيش نتيجة)"]
    for i, chunk in enumerate(chunks):
        if is_callback and i == 0:
            await message_or_query.edit_message_text(chunk)
        else:
            target = message_or_query.message if is_callback else message_or_query
            await target.reply_text(chunk)


async def summarize_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    if not gemini_client:
        await update.message.reply_text("⚠️ ميزة التلخيص مش مفعّلة، لازم يتضاف GEMINI_API_KEY.")
        return
    query_text = " ".join(context.args)
    if not query_text:
        await update.message.reply_text("اكتب كده: /summarize اسم الكتاب")
        return
    result = (
        supabase.table("books").select("id, title")
        .ilike("title", f"%{query_text}%").is_("deleted_at", "null").execute()
    )
    rows = result.data
    if not rows:
        await update.message.reply_text("❌ مفيش نتايج بالاسم ده.")
        return
    buttons = [[InlineKeyboardButton(r["title"][:40], callback_data=f"summarize:{r['id']}")] for r in rows]
    await update.message.reply_text("اختار الملف اللي عايز تلخيصه:", reply_markup=InlineKeyboardMarkup(buttons))


async def handle_summarize_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_callback(update):
        return
    query = update.callback_query
    await query.answer()
    book_id = query.data.split(":")[1]
    await query.edit_message_text("⏳ بلخص الملف، استنى شوية...")

    row = await get_book_row(book_id)
    if not row:
        await query.edit_message_text("⚠️ الملف مش موجود.")
        return

    tmp_path = None
    gemini_file = None
    try:
        # 1) تنزيل مؤقت من تليجرام على قرص السيرفر (مش في الذاكرة)
        tmp_path = await download_to_temp_file(context, row["telegram_file_id"])

        # 2) رفع فوري لسيرفرات جوجل
        gemini_file = await asyncio.to_thread(
            gemini_client.files.upload, file=tmp_path
        )

        # 3) طلب التلخيص
        prompt = (
            "لخص هذا الملف بشكل واضح ومنظم بالنقاط. "
            "اكتب التلخيص بنفس لغة الملف الأصلي بالضبط "
            "(لو الملف بالإنجليزي لخص بالإنجليزي، لو عربي لخص بالعربي)."
        )
        response = await asyncio.to_thread(
            gemini_client.models.generate_content, model=GEMINI_MODEL_NAME, contents=[gemini_file, prompt]
        )
        summary = response.text

        await query.edit_message_text(f"📝 ملخص: {row['title']}")
        await send_long_text(query.message, summary, is_callback=False)

    except Exception as e:
        await query.edit_message_text(f"⚠️ حصل خطأ أثناء التلخيص: {e}")

    finally:
        # 4) تنظيف مضمون يحصل دايمًا، حتى لو فشلت أي خطوة فوق
        await cleanup_temp_and_gemini_file(tmp_path, gemini_file)


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    if not gemini_client:
        await update.message.reply_text("⚠️ ميزة السؤال مش مفعّلة، لازم يتضاف GEMINI_API_KEY.")
        return
    query_text = " ".join(context.args)
    if not query_text:
        await update.message.reply_text("اكتب كده: /ask اسم الكتاب")
        return
    result = (
        supabase.table("books").select("id, title")
        .ilike("title", f"%{query_text}%").is_("deleted_at", "null").execute()
    )
    rows = result.data
    if not rows:
        await update.message.reply_text("❌ مفيش نتايج بالاسم ده.")
        return
    buttons = [[InlineKeyboardButton(r["title"][:40], callback_data=f"askfile:{r['id']}")] for r in rows]
    await update.message.reply_text("اختار الملف اللي عايز تسأل عنه:", reply_markup=InlineKeyboardMarkup(buttons))


async def handle_ask_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_callback(update):
        return
    query = update.callback_query
    await query.answer()
    book_id = query.data.split(":")[1]
    context.user_data["awaiting_question_book_id"] = book_id
    await query.edit_message_text("✏️ اكتب سؤالك عن الملف ده في رسالة:")


async def answer_question_about_book(message, context, book_id, question):
    if not gemini_client:
        await message.reply_text("⚠️ ميزة السؤال مش مفعّلة.")
        return

    await message.reply_text("⏳ بدور في الملف، استنى شوية...")

    row = await get_book_row(book_id)
    if not row:
        await message.reply_text("⚠️ الملف مش موجود.")
        return

    tmp_path = None
    gemini_file = None
    try:
        # 1) تنزيل مؤقت من تليجرام على قرص السيرفر
        tmp_path = await download_to_temp_file(context, row["telegram_file_id"])

        # 2) رفع فوري لجوجل
        gemini_file = await asyncio.to_thread(
            gemini_client.files.upload, file=tmp_path
        )

        # 3) طلب الإجابة
        prompt = (
            f"أجب عن السؤال التالي بالاعتماد فقط على محتوى الملف المرفق، "
            f"واذكر رقم الصفحة إن أمكن. جاوب بنفس لغة الملف الأصلي.\n\nالسؤال: {question}"
        )
        response = await asyncio.to_thread(
            gemini_client.models.generate_content, model=GEMINI_MODEL_NAME, contents=[gemini_file, prompt]
        )
        answer = response.text

        await send_long_text(message, f"📘 من ملف: {row['title']}\n\n{answer}")

    except Exception as e:
        await message.reply_text(f"⚠️ حصل خطأ: {e}")

    finally:
        # 4) تنظيف مضمون في كل الحالات
        await cleanup_temp_and_gemini_file(tmp_path, gemini_file)


# ============ التذكيرات اليومية ============
async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    subject = " ".join(context.args)
    if not subject:
        await update.message.reply_text("اكتب كده: /remind اسم المادة\n(هيسألك بعد كده على أنهي وقت)")
        return
    context.user_data["_remind_subject_cache"] = subject
    context.user_data["awaiting_remind_time"] = True
    await update.message.reply_text("🕐 اكتب الوقت اليومي للتذكير (بالصيغة HH:MM) زي: 17:30")


async def handle_remind_time_input(message, context, text):
    chat_id = message.chat_id
    try:
        hour, minute = map(int, text.strip().split(":"))
        assert 0 <= hour <= 23 and 0 <= minute <= 59
    except Exception:
        await message.reply_text("⚠️ الصيغة غلط، اكتب زي كده: 17:30")
        return

    subject_name = context.user_data.pop("_remind_subject_cache", None)
    if not subject_name:
        await message.reply_text("⚠️ حصل خطأ، ابدأ من /remind تاني.")
        return

    supabase.table("reminders").insert({"chat_id": chat_id, "subject": subject_name, "hour": hour, "minute": minute}).execute()
    schedule_reminder_job(context.application, chat_id, subject_name, hour, minute)

    await message.reply_text(f"🔔 تمام، هفكرك كل يوم الساعة {hour:02d}:{minute:02d} تراجع {subject_name}.")


def schedule_reminder_job(application, chat_id, subject, hour, minute):
    job_name = f"remind:{chat_id}:{subject}"
    application.job_queue.run_daily(
        reminder_callback,
        time=datetime.time(hour=hour, minute=minute),
        chat_id=chat_id,
        name=job_name,
        data={"subject": subject},
    )


async def reminder_callback(context: ContextTypes.DEFAULT_TYPE):
    subject = context.job.data["subject"]
    await context.bot.send_message(chat_id=context.job.chat_id, text=f"🔔 وقت مراجعة: {subject}")


async def myreminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    chat_id = update.message.chat_id
    result = supabase.table("reminders").select("*").eq("chat_id", chat_id).execute()
    rows = result.data
    if not rows:
        await update.message.reply_text("مفيش تذكيرات محفوظة.")
        return
    lines = [f"• {r['subject']} — {r['hour']:02d}:{r['minute']:02d}" for r in rows]
    await update.message.reply_text("🔔 تذكيراتك:\n" + "\n".join(lines) + "\n\nلإلغاء تذكير: /unremind اسم المادة")


async def unremind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    chat_id = update.message.chat_id
    subject = " ".join(context.args)
    if not subject:
        await update.message.reply_text("اكتب كده: /unremind اسم المادة")
        return
    supabase.table("reminders").delete().eq("chat_id", chat_id).eq("subject", subject).execute()
    jobs = context.application.job_queue.get_jobs_by_name(f"remind:{chat_id}:{subject}")
    for job in jobs:
        job.schedule_removal()
    await update.message.reply_text(f"✅ اتلغى تذكير {subject}.")


# ============ النسخة الاحتياطية الأسبوعية ============
async def weekly_backup_job(context: ContextTypes.DEFAULT_TYPE):
    result = supabase.table("books").select("*").is_("deleted_at", "null").execute()
    rows = result.data
    if not rows:
        return

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["السنة", "الترم", "المادة", "السكشن", "النوع", "الاسم", "متذاكر"])
    for r in rows:
        writer.writerow([
            YEARS.get(r.get("year"), ""), TERMS.get(r.get("term"), ""),
            r.get("subject", ""), r.get("section", ""),
            type_label(r.get("file_type")), r.get("title", ""),
            "نعم" if r.get("reviewed") else "لا",
        ])
    csv_bytes = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    csv_bytes.name = "نسخة_احتياطية.csv"

    for admin_chat_id in get_all_admin_chat_ids():
        try:
            csv_bytes.seek(0)
            await context.bot.send_document(
                chat_id=admin_chat_id, document=csv_bytes,
                filename="نسخة_احتياطية.csv",
                caption=f"📊 نسخة احتياطية أسبوعية — إجمالي {len(rows)} ملف",
            )
        except Exception:
            pass


async def purge_deleted_job(context: ContextTypes.DEFAULT_TYPE):
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(hours=24)).isoformat()
    supabase.table("books").delete().not_.is_("deleted_at", "null").lt("deleted_at", cutoff).execute()


# ============ البداية ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if not is_authorized(chat_id):
        await update.message.reply_text("🔒 أهلاً بيك، البوت ده خاص.\nاكتب: /login الباسورد")
        return

    admin = is_admin(chat_id)
    text = "أهلاً بيك 👋\n\n"
    if admin:
        text += (
            "📤 حفظ ملف: ابعته PDF/صورة مع اسمه في الوصف\n"
            "🗑️ حذف: /delete اسم الملف\n"
            "↩️ استرجاع: /trash\n"
            "✏️ تعديل الاسم: /rename اسم الملف\n"
            "🔔 تذكير يومي: /remind اسم المادة\n"
        )
    text += (
        "📂 تصفح: /menu\n"
        "🔍 بحث: /find اسم الكتاب أو المادة (أو اكتبه عادي)\n"
        "📊 إحصائيات: /stats\n"
        "🕐 آخر الملفات: /recent\n"
        "✅ علامة مذاكرة: /reviewed اسم الملف\n"
        "📝 تلخيص ملف بالذكاء الاصطناعي: /summarize اسم الملف\n"
        "❓ سؤال عن محتوى ملف: /ask اسم الملف\n"
        "🔔 تذكيراتي: /myreminders"
    )
    await update.message.reply_text(text)


async def restore_reminders(app_):
    result = supabase.table("reminders").select("*").execute()
    for r in result.data:
        schedule_reminder_job(app_, r["chat_id"], r["subject"], r["hour"], r["minute"])


def main():
    app = Application.builder().token(BOT_TOKEN).post_init(restore_reminders).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("login", login))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("find", find_book))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("trash", trash_command))
    app.add_handler(CommandHandler("rename", rename_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("recent", recent_command))
    app.add_handler(CommandHandler("reviewed", reviewed_command))
    app.add_handler(CommandHandler("summarize", summarize_command))
    app.add_handler(CommandHandler("ask", ask_command))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("myreminders", myreminders_command))
    app.add_handler(CommandHandler("unremind", unremind_command))

    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_handler(CallbackQueryHandler(handle_quickuse, pattern=r"^quickuse$"))
    app.add_handler(CallbackQueryHandler(handle_newclassification, pattern=r"^newclassification$"))
    app.add_handler(CallbackQueryHandler(handle_save_year, pattern=r"^saveyear:"))
    app.add_handler(CallbackQueryHandler(handle_save_term, pattern=r"^saveterm:"))
    app.add_handler(CallbackQueryHandler(handle_new_subject_button, pattern=r"^newsubject$"))
    app.add_handler(CallbackQueryHandler(handle_save_subject, pattern=r"^savesubject:"))
    app.add_handler(CallbackQueryHandler(handle_save_section, pattern=r"^savesection:"))
    app.add_handler(CallbackQueryHandler(handle_save_section_none, pattern=r"^savesection_none$"))
    app.add_handler(CallbackQueryHandler(handle_new_section_button, pattern=r"^newsection$"))
    app.add_handler(CallbackQueryHandler(handle_save_type, pattern=r"^savetype:"))

    app.add_handler(CallbackQueryHandler(handle_browse_year, pattern=r"^browseyear:"))
    app.add_handler(CallbackQueryHandler(handle_browse_term, pattern=r"^browseterm:"))
    app.add_handler(CallbackQueryHandler(handle_browse_subject, pattern=r"^browsesubject:"))
    app.add_handler(CallbackQueryHandler(handle_browse_section, pattern=r"^browsesection:"))
    app.add_handler(CallbackQueryHandler(handle_browse_section_none, pattern=r"^browsesection_none$"))

    app.add_handler(CallbackQueryHandler(handle_delete_select, pattern=r"^delete:"))
    app.add_handler(CallbackQueryHandler(handle_delete_confirm, pattern=r"^deleteconfirm:"))
    app.add_handler(CallbackQueryHandler(handle_delete_cancel, pattern=r"^deletecancel$"))
    app.add_handler(CallbackQueryHandler(handle_restore, pattern=r"^restore:"))
    app.add_handler(CallbackQueryHandler(handle_rename_select, pattern=r"^rename:"))
    app.add_handler(CallbackQueryHandler(handle_toggle_reviewed, pattern=r"^toggleReviewed:"))
    app.add_handler(CallbackQueryHandler(handle_summarize_select, pattern=r"^summarize:"))
    app.add_handler(CallbackQueryHandler(handle_ask_select, pattern=r"^askfile:"))

    # النسخة الاحتياطية الأسبوعية - كل يوم جمعة الساعة 20:00
    app.job_queue.run_daily(weekly_backup_job, time=datetime.time(hour=20, minute=0), days=(4,))
    # تنظيف سلة المحذوفات كل ساعة
    app.job_queue.run_repeating(purge_deleted_job, interval=3600, first=60)

    app.run_polling()


if __name__ == "__main__":
    main()

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


async def send_book_row(bot, chat_id, row):
    """يبعت الملف كمستند، أو النص كامل لو كانت ملاحظة صوتية محولة لنص"""
    if row.get("is_text_note"):
        caption = build_caption(row)
        content = row.get("text_content") or "(مفيش محتوى)"
        full_text = f"{caption}\n\n{content}"
        for chunk in (full_text[i:i + 3800] for i in range(0, len(full_text), 3800)):
            await bot.send_message(chat_id=chat_id, text=chunk)
    else:
        await bot.send_document(chat_id=chat_id, document=row["telegram_file_id"], caption=build_caption(row))


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
        if gemini_client:
            asyncio.create_task(run_ocr_and_reply(context, message.chat_id, file_id_original))
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


# ============ استقبال رسالة صوتية (تحويل لنص) ============
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin_message(update):
        return
    if not gemini_client:
        await update.message.reply_text("⚠️ ميزة تحويل الصوت مش مفعّلة، لازم يتضاف GEMINI_API_KEY.")
        return

    message = update.message
    voice = message.voice or message.audio
    if not voice:
        return

    await message.reply_text("⏳ بحول الصوت لنص، استنى شوية...")

    tmp_path = None
    gemini_file = None
    try:
        tmp_path = await download_to_temp_file(context, voice.file_id, suffix=".ogg")
        gemini_file = await asyncio.to_thread(gemini_client.files.upload, file=tmp_path)
        prompt = (
            "حوّل هذا التسجيل الصوتي إلى نص مكتوب منظم ومرتب في فقرات واضحة، "
            "صحح أي أخطاء إملائية بسيطة، واكتب بنفس لغة التسجيل الأصلي."
        )
        response = await asyncio.to_thread(
            gemini_client.models.generate_content, model=GEMINI_MODEL_NAME, contents=[gemini_file, prompt]
        )
        transcript = response.text
    except Exception as e:
        await message.reply_text(f"⚠️ حصل خطأ أثناء التحويل: {e}")
        return
    finally:
        await cleanup_temp_and_gemini_file(tmp_path, gemini_file)

    context.user_data["pending_text_note"] = transcript
    await send_long_text(message, f"📝 النص المستخرج:\n\n{transcript}")
    context.user_data["awaiting_note_title"] = True
    await message.reply_text(
        "لو عايز تحفظ النص ده في الأرشيف، اكتب اسم/عنوان للملاحظة دلوقتي.\n"
        "لو مش محتاج تحفظه، تجاهل الرسالة دي وكمل عادي."
    )


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

    if context.user_data.get("awaiting_remind_day"):
        context.user_data.pop("awaiting_remind_day")
        await handle_remind_day_input(update.message, context, text)
        return

    if context.user_data.get("awaiting_remind_month"):
        context.user_data.pop("awaiting_remind_month")
        await handle_remind_month_input(update.message, context, text)
        return

    if context.user_data.get("awaiting_remind_time"):
        context.user_data.pop("awaiting_remind_time")
        await handle_remind_time_input(update.message, context, text)
        return

    if context.user_data.get("awaiting_examday_day"):
        context.user_data.pop("awaiting_examday_day")
        await handle_examday_day_input(update.message, context, text)
        return

    if context.user_data.get("awaiting_examday_month"):
        context.user_data.pop("awaiting_examday_month")
        await handle_examday_month_input(update.message, context, text)
        return

    if context.user_data.get("awaiting_examday_time"):
        context.user_data.pop("awaiting_examday_time")
        await handle_examday_time_input(update.message, context, text)
        return

    if context.user_data.get("awaiting_note_title"):
        context.user_data.pop("awaiting_note_title")
        if not is_admin(chat_id):
            await update.message.reply_text("⛔ الميزة دي للأدمن بس.")
            return
        transcript = context.user_data.pop("pending_text_note", None)
        if not transcript:
            await update.message.reply_text("⚠️ حصل خطأ، جرب تبعت التسجيل الصوتي تاني.")
            return
        context.user_data["pending_upload"] = {
            "title": text,
            "file_name": "ملاحظة صوتية",
            "telegram_file_id": None,
            "channel_message_id": None,
            "owner_chat_id": chat_id,
            "is_text_note": True,
            "text_content": transcript,
        }
        await update.message.reply_text(
            "📅 اختار السنة الدراسية لهذه الملاحظة:",
            reply_markup=years_keyboard("saveyear"),
        )
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
        await send_book_row(context.bot, chat_id, row)


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
        await send_book_row(context.bot, query.message.chat_id, row)


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
        await send_book_row(context.bot, update.message.chat_id, row)


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
        await send_book_row(context.bot, update.message.chat_id, row)


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


async def progress_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    result = supabase.table("books").select("subject, reviewed").is_("deleted_at", "null").execute()
    rows = result.data
    if not rows:
        await update.message.reply_text("لسه مفيش أي ملفات محفوظة.")
        return

    per_subject = {}
    for r in rows:
        subject = r.get("subject") or "بدون مادة"
        per_subject.setdefault(subject, {"total": 0, "reviewed": 0})
        per_subject[subject]["total"] += 1
        if r.get("reviewed"):
            per_subject[subject]["reviewed"] += 1

    lines = ["📈 نسبة المذاكرة لكل مادة:\n"]
    for subject, counts in sorted(per_subject.items()):
        pct = round((counts["reviewed"] / counts["total"]) * 100) if counts["total"] else 0
        filled = round(pct / 10)
        bar = "▓" * filled + "░" * (10 - filled)
        lines.append(f"{subject}\n{bar} {pct}% ({counts['reviewed']}/{counts['total']})")

    await update.message.reply_text("\n\n".join(lines))


# ============ الذكاء الاصطناعي: تلخيص وسؤال ============
async def get_book_row(book_id):
    result = supabase.table("books").select("*").eq("id", book_id).execute()
    if not result.data:
        return None
    return result.data[0]


async def download_to_temp_file(context, telegram_file_id, suffix=".pdf"):
    """ينزل الملف مؤقتًا على قرص السيرفر (مش في الذاكرة) ويرجع مسار الملف"""
    tg_file = await context.bot.get_file(telegram_file_id)
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    await tg_file.download_to_drive(tmp_path)
    return tmp_path


async def run_ocr_and_reply(context, chat_id, photo_file_id):
    """يستخرج النص من الصورة تلقائيًا ويبعته كرسالة إضافية بعد الحفظ"""
    tmp_path = None
    gemini_file = None
    try:
        tmp_path = await download_to_temp_file(context, photo_file_id, suffix=".jpg")
        gemini_file = await asyncio.to_thread(gemini_client.files.upload, file=tmp_path)
        prompt = (
            "استخرج كل النص الموجود في هذه الصورة بالكامل، ورتبه بشكل واضح ومنظم. "
            "لو مفيش نص واضح، قول 'مفيش نص واضح في الصورة'. اكتب بنفس لغة النص الأصلي."
        )
        response = await asyncio.to_thread(
            gemini_client.models.generate_content, model=GEMINI_MODEL_NAME, contents=[gemini_file, prompt]
        )
        extracted = response.text
        await context.bot.send_message(chat_id=chat_id, text=f"🔎 النص المستخرج من الصورة:\n\n{extracted}")
    except Exception:
        pass  # لو فشل الاستخراج، ميضايقش المستخدم، الصورة الأصلية اتحفظت عادي
    finally:
        await cleanup_temp_and_gemini_file(tmp_path, gemini_file)


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


async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    if not gemini_client:
        await update.message.reply_text("⚠️ ميزة الكويز مش مفعّلة، لازم يتضاف GEMINI_API_KEY.")
        return
    query_text = " ".join(context.args)
    if not query_text:
        await update.message.reply_text("اكتب كده: /quiz اسم الكتاب")
        return
    result = (
        supabase.table("books").select("id, title")
        .ilike("title", f"%{query_text}%").is_("deleted_at", "null").execute()
    )
    rows = result.data
    if not rows:
        await update.message.reply_text("❌ مفيش نتايج بالاسم ده.")
        return
    buttons = [[InlineKeyboardButton(r["title"][:40], callback_data=f"quiz:{r['id']}")] for r in rows]
    await update.message.reply_text("اختار الملف اللي عايز تعمله كويز:", reply_markup=InlineKeyboardMarkup(buttons))


async def handle_quiz_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_callback(update):
        return
    query = update.callback_query
    await query.answer()
    book_id = query.data.split(":")[1]
    await query.edit_message_text("⏳ بجهز الكويز، ده ممكن ياخد دقيقة أو اتنين...")

    row = await get_book_row(book_id)
    if not row:
        await query.edit_message_text("⚠️ الملف مش موجود.")
        return

    base_prompt = (
        "اعمل اختبار (كويز) كامل من المحتوى المرفق يتكون من جزئين:\n"
        "1) 20 سؤال اختيار من متعدد (MCQ)، كل سؤال له 4 اختيارات مرقمة (أ، ب، ج، د)، "
        "بدون كتابة الإجابة الصحيحة جنب السؤال.\n"
        "2) 5 أسئلة مقالية تحتاج شرح وتفكير، بدون إجابات.\n"
        "بعد كل الأسئلة، اكتب قسم منفصل بعنوان 'الإجابات الصحيحة' فيه إجابات الـ 20 سؤال الاختياري فقط "
        "(رقم السؤال والحرف الصحيح).\n"
        "اكتب الكويز كامل بنفس لغة المحتوى الأصلي بالضبط."
    )

    tmp_path = None
    gemini_file = None
    try:
        if row.get("is_text_note"):
            content = row.get("text_content", "")
            response = await asyncio.to_thread(
                gemini_client.models.generate_content,
                model=GEMINI_MODEL_NAME,
                contents=[f"المحتوى:\n{content}\n\n{base_prompt}"],
            )
        else:
            tmp_path = await download_to_temp_file(context, row["telegram_file_id"])
            gemini_file = await asyncio.to_thread(gemini_client.files.upload, file=tmp_path)
            response = await asyncio.to_thread(
                gemini_client.models.generate_content, model=GEMINI_MODEL_NAME, contents=[gemini_file, base_prompt]
            )
        quiz_text = response.text

        subject_for_bank = row.get("subject", "")
        supabase.table("quiz_bank").insert({
            "chat_id": query.message.chat_id, "subject": subject_for_bank, "quiz_text": quiz_text,
        }).execute()

        await query.edit_message_text(f"📋 كويز: {row['title']}")
        await send_long_text(query.message, quiz_text, is_callback=False)

    except Exception as e:
        await query.edit_message_text(f"⚠️ حصل خطأ أثناء عمل الكويز: {e}")

    finally:
        await cleanup_temp_and_gemini_file(tmp_path, gemini_file)


async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    chat_id = update.message.chat_id
    subject_filter = " ".join(context.args)

    q = supabase.table("quiz_bank").select("*").eq("chat_id", chat_id)
    if subject_filter:
        q = q.ilike("subject", f"%{subject_filter}%")
    result = q.execute()
    rows = result.data
    if not rows:
        await update.message.reply_text("مفيش كويزات محفوظة في بنك الأسئلة لسه. اعمل /quiz على أي ملف الأول.")
        return

    import random
    chosen = random.choice(rows)
    await update.message.reply_text(f"🧠 مراجعة عشوائية — مادة: {chosen.get('subject') or 'غير محدد'}")
    await send_long_text(update.message, chosen["quiz_text"])




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


# ============ سؤال عام للذكاء الاصطناعي (مش مربوط بملف) ============
async def ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    if not gemini_client:
        await update.message.reply_text("⚠️ ميزة الذكاء الاصطناعي مش مفعّلة، لازم يتضاف GEMINI_API_KEY.")
        return

    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("اكتب كده: /ai سؤالك\nمثال: /ai اشرحلي الفرق بين الالتهاب الحاد والمزمن")
        return

    thinking_msg = await update.message.reply_text("⏳ بفكر في الإجابة...")

    prompt = (
        "أجب عن السؤال التالي بشكل منظم وواضح ومرتب (استخدم عناوين ونقاط لو الموضوع فيه أكتر من جزء). "
        "جاوب بنفس لغة السؤال (لو بالعربي جاوب بالعربي، لو بالإنجليزي جاوب بالإنجليزي).\n\n"
        f"السؤال: {question}"
    )
    try:
        response = await asyncio.to_thread(
            gemini_client.models.generate_content, model=GEMINI_MODEL_NAME, contents=[prompt]
        )
        answer = response.text
    except Exception as e:
        await thinking_msg.edit_text(f"⚠️ حصل خطأ: {e}")
        return

    await thinking_msg.delete()
    await send_long_text(update.message, answer)


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
        prompt = (
            f"أجب عن السؤال التالي بالاعتماد فقط على المحتوى المرفق، "
            f"واذكر رقم الصفحة إن أمكن. جاوب بنفس لغة المحتوى الأصلي.\n\nالسؤال: {question}"
        )
        if row.get("is_text_note"):
            content = row.get("text_content", "")
            response = await asyncio.to_thread(
                gemini_client.models.generate_content,
                model=GEMINI_MODEL_NAME,
                contents=[f"المحتوى:\n{content}\n\n{prompt}"],
            )
        else:
            tmp_path = await download_to_temp_file(context, row["telegram_file_id"])
            gemini_file = await asyncio.to_thread(gemini_client.files.upload, file=tmp_path)
            response = await asyncio.to_thread(
                gemini_client.models.generate_content, model=GEMINI_MODEL_NAME, contents=[gemini_file, prompt]
            )
        answer = response.text

        # اقتراح ملفات تانية في نفس المادة ممكن يكون فيها نفس الموضوع
        related_note = ""
        if row.get("subject"):
            related = (
                supabase.table("books").select("title")
                .eq("subject", row["subject"]).neq("id", book_id)
                .is_("deleted_at", "null").limit(4).execute()
            )
            if related.data:
                titles = "، ".join(r["title"] for r in related.data)
                related_note = f"\n\n📎 الموضوع ده ممكن يكون موجود كمان في: {titles}"

        await send_long_text(message, f"📘 من ملف: {row['title']}\n\n{answer}{related_note}")

    except Exception as e:
        await message.reply_text(f"⚠️ حصل خطأ: {e}")

    finally:
        # تنظيف مضمون في كل الحالات
        await cleanup_temp_and_gemini_file(tmp_path, gemini_file)


# ============ وضع الامتحان الشامل: /examday ============
async def examday_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    if not gemini_client:
        await update.message.reply_text("⚠️ الميزة دي محتاجة GEMINI_API_KEY مفعّل.")
        return
    subject = " ".join(context.args)
    if not subject:
        await update.message.reply_text("اكتب كده: /examday اسم المادة\n(هيسألك بعد كده على تاريخ الامتحان)")
        return
    context.user_data["_examday_subject_cache"] = subject
    context.user_data["awaiting_examday_day"] = True
    await update.message.reply_text("📅 امتحان المادة دي إمتى؟ اكتب رقم اليوم (من 1 لـ 31):")


async def handle_examday_day_input(message, context, text):
    try:
        day = int(text.strip())
        assert 1 <= day <= 31
    except Exception:
        await message.reply_text("⚠️ اكتب رقم صحيح من 1 لـ 31.")
        return
    context.user_data["_examday_day_cache"] = day
    context.user_data["awaiting_examday_month"] = True
    await message.reply_text("📆 اكتب رقم الشهر (من 1 لـ 12):")


async def handle_examday_month_input(message, context, text):
    try:
        month = int(text.strip())
        assert 1 <= month <= 12
    except Exception:
        await message.reply_text("⚠️ اكتب رقم صحيح من 1 لـ 12.")
        return
    context.user_data["_examday_month_cache"] = month
    context.user_data["awaiting_examday_time"] = True
    await message.reply_text("🕐 اكتب معاد الامتحان بصيغة 12 ساعة، زي: 9:00 AM")


async def handle_examday_time_input(message, context, text):
    parsed = parse_12h_time(text)
    if not parsed:
        await message.reply_text("⚠️ الصيغة غلط، اكتب زي كده: 9:00 AM")
        return
    hour, minute = parsed

    subject = context.user_data.pop("_examday_subject_cache", None)
    day = context.user_data.pop("_examday_day_cache", None)
    month = context.user_data.pop("_examday_month_cache", None)
    if not subject or not day or not month:
        await message.reply_text("⚠️ حصل خطأ، ابدأ من /examday تاني.")
        return

    exam_at = compute_next_occurrence(day, month, hour, minute)
    if not exam_at:
        await message.reply_text("⚠️ التاريخ ده مش موجود.")
        return

    chat_id = message.chat_id

    # جمع كل ملفات المادة دي (حد أقصى 6 ملفات عشان السرعة والتكلفة)
    result = (
        supabase.table("books").select("*")
        .ilike("subject", f"%{subject}%").is_("deleted_at", "null")
        .limit(6).execute()
    )
    rows = result.data
    if not rows:
        await message.reply_text(f"❌ مفيش ملفات محفوظة في مادة '{subject}'.")
        return

    await message.reply_text(f"⏳ بجهز مذاكرة شاملة من {len(rows)} ملف، ده ممكن ياخد شوية وقت...")

    tmp_paths = []
    gemini_files = []
    contents_parts = []
    try:
        for row in rows:
            if row.get("is_text_note"):
                contents_parts.append(f"ملاحظة بعنوان {row['title']}:\n{row.get('text_content', '')}")
            else:
                tmp_path = await download_to_temp_file(context, row["telegram_file_id"])
                tmp_paths.append(tmp_path)
                gfile = await asyncio.to_thread(gemini_client.files.upload, file=tmp_path)
                gemini_files.append(gfile)
                contents_parts.append(gfile)

        summary_prompt = (
            f"دي كل ملفات مادة '{subject}' المطلوبة للامتحان. "
            "اعمل ملخص شامل ومركز يجمع أهم النقاط من كل الملفات دي مع بعض في ملخص واحد منظم بالنقاط. "
            "اكتب بنفس لغة أغلب الملفات."
        )
        summary_response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model=GEMINI_MODEL_NAME,
            contents=contents_parts + [summary_prompt],
        )
        await message.reply_text(f"📝 ملخص شامل لمادة {subject}:")
        await send_long_text(message, summary_response.text)

        quiz_prompt = (
            f"من نفس ملفات مادة '{subject}' دي، اعمل كويز شامل: "
            "20 سؤال اختيار من متعدد (4 اختيارات لكل سؤال) بدون إجابات جنبها، "
            "و5 أسئلة مقالية، وفي الآخر قسم منفصل بعنوان 'الإجابات الصحيحة' للأسئلة الاختيارية فقط. "
            "اكتب بنفس لغة أغلب الملفات."
        )
        quiz_response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model=GEMINI_MODEL_NAME,
            contents=contents_parts + [quiz_prompt],
        )
        await message.reply_text(f"📋 كويز شامل لمادة {subject}:")
        await send_long_text(message, quiz_response.text)

        supabase.table("quiz_bank").insert({
            "chat_id": chat_id, "subject": subject, "quiz_text": quiz_response.text,
        }).execute()

    except Exception as e:
        await message.reply_text(f"⚠️ حصل خطأ أثناء التجهيز: {e}")
    finally:
        for gfile in gemini_files:
            try:
                await asyncio.to_thread(gemini_client.files.delete, name=gfile.name)
            except Exception:
                pass
        for path in tmp_paths:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        gc.collect()

    # تذكير تلقائي قبل الامتحان بـ 24 ساعة
    reminder_at = exam_at - datetime.timedelta(hours=24)
    now = datetime.datetime.now()
    if reminder_at > now:
        result = supabase.table("reminders").insert({
            "chat_id": chat_id, "subject": f"امتحان {subject} بكرة!", "remind_at": reminder_at.isoformat(),
        }).execute()
        reminder_id = result.data[0]["id"]
        schedule_reminder_job(context.application, chat_id, f"امتحان {subject} بكرة!", reminder_at, reminder_id)
        await message.reply_text(
            f"✅ خلصت المذاكرة الشاملة، وهفكرك تلقائي قبل الامتحان بـ 24 ساعة "
            f"({reminder_at.strftime('%d/%m/%Y الساعة %I:%M %p')})."
        )
    else:
        await message.reply_text("✅ خلصت المذاكرة الشاملة. (الامتحان قريب جدًا فمقدرتش أظبط تذكير قبله بـ24 ساعة)")


# ============ تذكير لمرة واحدة (يوم + شهر محددين) ============
async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    subject = " ".join(context.args)
    if not subject:
        await update.message.reply_text("اكتب كده: /remind اسم المادة\n(هيسألك بعد كده على اليوم والشهر والساعة)")
        return
    context.user_data["_remind_subject_cache"] = subject
    context.user_data["awaiting_remind_day"] = True
    await update.message.reply_text("📅 اكتب رقم اليوم (من 1 لـ 31):")


async def handle_remind_day_input(message, context, text):
    try:
        day = int(text.strip())
        assert 1 <= day <= 31
    except Exception:
        await message.reply_text("⚠️ اكتب رقم صحيح من 1 لـ 31.")
        return

    context.user_data["_remind_day_cache"] = day
    context.user_data["awaiting_remind_month"] = True
    await message.reply_text("📆 اكتب رقم الشهر (من 1 لـ 12):")


async def handle_remind_month_input(message, context, text):
    try:
        month = int(text.strip())
        assert 1 <= month <= 12
    except Exception:
        await message.reply_text("⚠️ اكتب رقم صحيح من 1 لـ 12.")
        return

    context.user_data["_remind_month_cache"] = month
    context.user_data["awaiting_remind_time"] = True
    await message.reply_text("🕐 اكتب الساعة بصيغة 12 ساعة، زي: 5:30 PM أو 5:30 صباحًا")


def parse_12h_time(text):
    """يقبل صيغ زي: 5:30 PM / 05:30 pm / 5:30 م / 5:30 ص"""
    cleaned = text.strip().upper()
    cleaned = cleaned.replace("صباحا", "AM").replace("صباحاً", "AM").replace("ص", "AM")
    cleaned = cleaned.replace("مساء", "PM").replace("مساءً", "PM").replace("م", "PM")
    cleaned = cleaned.replace("  ", " ").strip()

    for fmt in ("%I:%M %p", "%I:%M%p", "%I %p"):
        try:
            dt = datetime.datetime.strptime(cleaned, fmt)
            return dt.hour, dt.minute
        except ValueError:
            continue
    return None


def compute_next_occurrence(day, month, hour, minute):
    """يحسب أقرب تاريخ ووقت مستقبلي بنفس اليوم والشهر (السنادي أو اللي بعده لو التاريخ فات)"""
    now = datetime.datetime.now()
    for year in (now.year, now.year + 1):
        try:
            target = datetime.datetime(year, month, day, hour, minute)
        except ValueError:
            continue  # يوم مش موجود في الشهر ده (زي 30 فبراير)
        if target > now:
            return target
    return None


async def handle_remind_time_input(message, context, text):
    chat_id = message.chat_id
    parsed = parse_12h_time(text)
    if not parsed:
        await message.reply_text("⚠️ الصيغة غلط، اكتب زي كده: 5:30 PM أو 5:30 صباحًا")
        return
    hour, minute = parsed

    subject_name = context.user_data.pop("_remind_subject_cache", None)
    day = context.user_data.pop("_remind_day_cache", None)
    month = context.user_data.pop("_remind_month_cache", None)
    if not subject_name or not day or not month:
        await message.reply_text("⚠️ حصل خطأ، ابدأ من /remind تاني.")
        return

    remind_at = compute_next_occurrence(day, month, hour, minute)
    if not remind_at:
        await message.reply_text("⚠️ التاريخ ده مش موجود (تأكد من رقم اليوم بالنسبة للشهر ده).")
        return

    result = supabase.table("reminders").insert({
        "chat_id": chat_id, "subject": subject_name, "remind_at": remind_at.isoformat(),
    }).execute()
    reminder_id = result.data[0]["id"]
    schedule_reminder_job(context.application, chat_id, subject_name, remind_at, reminder_id)

    await message.reply_text(
        f"🔔 تمام، هفكرك مرة واحدة يوم {remind_at.strftime('%d/%m/%Y')} "
        f"الساعة {remind_at.strftime('%I:%M %p')} تراجع {subject_name}."
    )


def schedule_reminder_job(application, chat_id, subject, remind_at, reminder_id):
    job_name = f"remind:{chat_id}:{subject}:{reminder_id}"
    application.job_queue.run_once(
        reminder_callback,
        when=remind_at,
        chat_id=chat_id,
        name=job_name,
        data={"subject": subject, "reminder_id": reminder_id},
    )


async def reminder_callback(context: ContextTypes.DEFAULT_TYPE):
    subject = context.job.data["subject"]
    reminder_id = context.job.data.get("reminder_id")
    await context.bot.send_message(chat_id=context.job.chat_id, text=f"🔔 تذكير: {subject}")
    if reminder_id:
        supabase.table("reminders").delete().eq("id", reminder_id).execute()


async def myreminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_auth_message(update):
        return
    chat_id = update.message.chat_id
    result = supabase.table("reminders").select("*").eq("chat_id", chat_id).execute()
    rows = result.data
    if not rows:
        await update.message.reply_text("مفيش تذكيرات محفوظة.")
        return
    lines = []
    for r in rows:
        if r.get("remind_at"):
            dt = datetime.datetime.fromisoformat(r["remind_at"])
            lines.append(f"• {r['subject']} — {dt.strftime('%d/%m/%Y الساعة %I:%M %p')}")
        else:
            lines.append(f"• {r['subject']}")
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
    prefix = f"remind:{chat_id}:{subject}:"
    for job in context.application.job_queue.jobs():
        if job.name and job.name.startswith(prefix):
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
            "🔔 تذكير لمرة واحدة: /remind اسم المادة\n"
        )
    text += (
        "📂 تصفح: /menu\n"
        "🔍 بحث: /find اسم الكتاب أو المادة (أو اكتبه عادي)\n"
        "📊 إحصائيات: /stats\n"
        "🕐 آخر الملفات: /recent\n"
        "✅ علامة مذاكرة: /reviewed اسم الملف\n"
        "📝 تلخيص ملف بالذكاء الاصطناعي: /summarize اسم الملف\n"
        "📋 كويز 20 اختياري + 5 مقالي: /quiz اسم الملف\n"
        "🎯 وضع امتحان شامل لمادة كاملة: /examday اسم المادة\n"
        "🧠 مراجعة عشوائية من بنك الأسئلة: /review (أو /review اسم مادة)\n"
        "📈 تقدمك في كل مادة: /progress\n"
        "🎙️ ابعت رسالة صوتية وهحولها نص منظم\n"
        "❓ سؤال عن محتوى ملف: /ask اسم الملف\n"
        "🤖 سؤال عام لأي موضوع: /ai سؤالك\n"
        "🔔 تذكيراتي: /myreminders"
    )
    await update.message.reply_text(text)


async def restore_reminders(app_):
    now = datetime.datetime.now()
    result = supabase.table("reminders").select("*").execute()
    for r in result.data:
        if not r.get("remind_at"):
            continue
        remind_at = datetime.datetime.fromisoformat(r["remind_at"])
        if remind_at <= now:
            # فات وقته وقت ما البوت كان واقف، امسحه من غير ما يبعت متأخر
            supabase.table("reminders").delete().eq("id", r["id"]).execute()
            continue
        schedule_reminder_job(app_, r["chat_id"], r["subject"], remind_at, r["id"])


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
    app.add_handler(CommandHandler("quiz", quiz_command))
    app.add_handler(CommandHandler("ask", ask_command))
    app.add_handler(CommandHandler("ai", ai_command))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("myreminders", myreminders_command))
    app.add_handler(CommandHandler("unremind", unremind_command))
    app.add_handler(CommandHandler("examday", examday_command))
    app.add_handler(CommandHandler("review", review_command))
    app.add_handler(CommandHandler("progress", progress_command))

    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
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
    app.add_handler(CallbackQueryHandler(handle_quiz_select, pattern=r"^quiz:"))
    app.add_handler(CallbackQueryHandler(handle_ask_select, pattern=r"^askfile:"))

    # النسخة الاحتياطية الأسبوعية - كل يوم جمعة الساعة 20:00
    app.job_queue.run_daily(weekly_backup_job, time=datetime.time(hour=20, minute=0), days=(4,))
    # تنظيف سلة المحذوفات كل ساعة
    app.job_queue.run_repeating(purge_deleted_job, interval=3600, first=60)

    app.run_polling()


if __name__ == "__main__":
    main()

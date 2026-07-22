import os
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from supabase import create_client

# ============ الإعدادات (بتيجي من Environment Variables على Railway) ============
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]  # آي دي القناة الخاصة اللي هيتخزن فيها الملفات
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(level=logging.INFO)


# ============ استقبال ملف PDF أو صورة ============
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    caption = message.caption  # الاسم اللي المستخدم كاتبه مع الملف

    if not caption:
        await message.reply_text(
            "⚠️ لازم تكتب اسم/تاج للملف في نفس رسالة الإرسال (في خانة الـ caption).\n"
            "مثال: فيزياء 2 - الفصل 3"
        )
        return

    # تحديد نوع الملف (PDF أو صورة)
    if message.document:
        file_id_original = message.document.file_id
        file_name = message.document.file_name or "ملف"
    elif message.photo:
        file_id_original = message.photo[-1].file_id  # أعلى جودة
        file_name = "صورة"
    else:
        return

    # إعادة إرسال الملف لقناة الأرشيف الخاصة عشان ناخد نسخة دائمة منه
    forwarded = await context.bot.forward_message(
        chat_id=CHANNEL_ID,
        from_chat_id=message.chat_id,
        message_id=message.message_id,
    )

    # تخزين البيانات في قاعدة البيانات
    supabase.table("books").insert(
        {
            "title": caption,
            "file_name": file_name,
            "telegram_file_id": file_id_original,
            "channel_message_id": forwarded.message_id,
            "owner_chat_id": message.chat_id,
        }
    ).execute()

    await message.reply_text(f"✅ اتحفظ باسم: {caption}")


# ============ البحث عن كتاب ============
async def find_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


# ============ عرض كل الكتب المخزنة ============
async def list_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = supabase.table("books").select("title").execute()
    rows = result.data
    if not rows:
        await update.message.reply_text("لسه مفيش كتب متخزنة.")
        return
    titles = "\n".join(f"• {r['title']}" for r in rows)
    await update.message.reply_text(f"📚 الكتب المتاحة:\n\n{titles}")


# ============ رسالة البداية ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً بيك 👋\n\n"
        "علشان تحفظ ملف: ابعته PDF أو صورة، واكتب اسم الكتاب في خانة الوصف (caption).\n"
        "علشان تدور على كتاب: /find اسم الكتاب\n"
        "علشان تشوف كل الكتب: /list"
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("find", find_book))
    app.add_handler(CommandHandler("list", list_books))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))

    app.run_polling()


if __name__ == "__main__":
    main()

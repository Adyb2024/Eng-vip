import os
import sqlite3
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# إعداد السجلات
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# جلب البيانات من الأسرار
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")  # معرف القناة مثل -100123456

# إعداد قاعدة البيانات
def init_db():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS episodes (id TEXT PRIMARY KEY, file_id TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    # إضافة قيم افتراضية للمهام
    c.execute("INSERT OR IGNORE INTO settings VALUES ('task_link', 'https://t.me/example')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('task_text', 'تابع حسابنا على تيك توك')")
    conn.commit()
    conn.close()

def get_setting(key):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else ""

# التحقق من الاشتراك الإجباري
async def is_subscribed(user_id, context):
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False

# رسالة البداية
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_subscribed(user_id, context):
        invite_link = f"https://t.me/{CHANNEL_ID.replace('-100', '')}"
        keyboard = [[InlineKeyboardButton("الاشتراك في القناة 📢", url=invite_link)],
                    [InlineKeyboardButton("تم الاشتراك ✅", callback_data="check_sub")]]
        await update.message.reply_text("مرحباً بك! لكي تستخدم البوت، يجب عليك الاشتراك في القناة أولاً.", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    keyboard = [[InlineKeyboardButton("قائمة الحلقات 📺", callback_data="list_eps")]]
    await update.message.reply_text("أهلاً بك في بوت المسلسلات! اختر ما تريد:", reply_markup=InlineKeyboardMarkup(keyboard))

# معالجة الضغط على الأزرار
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "check_sub":
        if await is_subscribed(user_id, context):
            await query.edit_message_text("تم التحقق! أرسل /start للبدء.")
        else:
            await query.answer("لم تشترك بعد!", show_alert=True)

    elif query.data == "list_eps":
        conn = sqlite3.connect('bot_data.db')
        c = conn.cursor()
        c.execute("SELECT id FROM episodes")
        eps = c.fetchall()
        conn.close()
        
        if not eps:
            await query.edit_message_text("لا توجد حلقات مضافة حالياً.")
            return

        keyboard = [[InlineKeyboardButton(f"الحلقة {e[0]}", callback_data=f"get_{e[0]}")] for e in eps]
        await query.edit_message_text("اختر الحلقة التي تريدها:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith("get_"):
        ep_id = query.data.split("_")[1]
        task_url = get_setting('task_link')
        task_text = get_setting('task_text')
        
        keyboard = [[InlineKeyboardButton(task_text, url=task_url)],
                    [InlineKeyboardButton("تم تنفيذ المهمة ✅", callback_data=f"verify_{ep_id}")]]
        await query.edit_message_text(f"للحصول على الحلقة {ep_id}، أكمل المهمة التالية أولاً:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith("verify_"):
        ep_id = query.data.split("_")[1]
        # هنا التحقق (تلقائي للأمانة)
        conn = sqlite3.connect('bot_data.db')
        c = conn.cursor()
        c.execute("SELECT file_id FROM episodes WHERE id=?", (ep_id,))
        file_id = c.fetchone()
        conn.close()
        
        if file_id:
            await context.bot.send_message(chat_id=user_id, text=f"شكراً لتنفيذ المهمة! تفضل الحلقة {ep_id}:")
            # إرسال الملف (سواء كان رابط أو معرف ملف تليجرام)
            await context.bot.send_message(chat_id=user_id, text=file_id[0])

# أوامر الأدمن
async def add_episode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        _, ep_id, link = update.message.text.split(" ", 2)
        conn = sqlite3.connect('bot_data.db')
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO episodes VALUES (?, ?)", (ep_id, link))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ تم إضافة الحلقة {ep_id} بنجاح.")
    except:
        await update.message.reply_text("الرجاء استخدام الصيغة: /add رقم_الحلقة الرابط")

async def update_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        _, link = update.message.text.split(" ", 1)
        conn = sqlite3.connect('bot_data.db')
        c = conn.cursor()
        c.execute("UPDATE settings SET value=? WHERE key='task_link'", (link,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ تم تحديث رابط المهمة إلى: {link}")
    except:
        await update.message.reply_text("استخدم: /set_task الرابط")

if __name__ == '__main__':
    init_db()
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_episode))
    app.add_handler(CommandHandler("set_task", update_task))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("Bot is running...")
    app.run_polling()

import os
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# إعداد السجلات (Logs)
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# الأسرار (جلبها من إعدادات جيت هوب)
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")

# --- إدارة البيانات ---
DATA_FILE = "data.json"

def load_data():
    if not os.path.exists(DATA_FILE):
        default = {"task_link": "https://t.me/example", "task_text": "اشترك في قناتنا الأخرى", "episodes": {}}
        save_data(default)
        return default
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# --- التحقق من الاشتراك الإجباري ---
async def is_subscribed(user_id, context):
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False

# --- الواجهة الرئيسية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = load_data()

    if not await is_subscribed(user_id, context):
        invite_link = f"https://t.me/{CHANNEL_ID.replace('-100', '')}"
        keyboard = [[InlineKeyboardButton("انضم للقناة أولاً 📢", url=invite_link)],
                    [InlineKeyboardButton("تم الاشتراك ✅ تحقّق الآن", callback_data="check_sub")]]
        await update.message.reply_text("⚠️ توقف! للاستفادة من البوت وفتح الحلقات، يجب عليك الاشتراك في القناة أولاً.", 
                                       reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # إذا كان المشترك هو الأدمن، تظهر له لوحة التحكم أيضاً
    keyboard = [[InlineKeyboardButton("📺 قائمة الحلقات", callback_data="list_eps")]]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚙️ لوحة تحكم الأدمن", callback_data="admin_panel")])
        
    await update.message.reply_text("✅ أهلاً بك مجدداً! اختر ما تريد من الأسفل:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- معالجة الضغط على الأزرار ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    db = load_data()
    await query.answer()

    if query.data == "check_sub":
        if await is_subscribed(user_id, context):
            await query.edit_message_text("✅ أحسنت! تم التحقق من اشتراكك بنجاح. أرسل /start للبدء.")
        else:
            await query.answer("❌ لم تشترك بعد! يرجى الانضمام للقناة والمحاولة مرة أخرى.", show_alert=True)

    elif query.data == "list_eps":
        if not db["episodes"]:
            await query.edit_message_text("📭 لا توجد حلقات مضافة حالياً.")
            return
        keyboard = [[InlineKeyboardButton(f"🎬 حلقة رقم {k}", callback_data=f"get_{k}")] for k in db["episodes"].keys()]
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_home")])
        await query.edit_message_text("📺 اختر الحلقة التي تريد مشاهدتها:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith("get_"):
        ep_id = query.data.split("_")[1]
        keyboard = [[InlineKeyboardButton(db["task_text"], url=db["task_link"])],
                    [InlineKeyboardButton("تم تنفيذ المهمة ✅", callback_data=f"show_{ep_id}")]]
        await query.edit_message_text(f"🚀 للحصول على الحلقة رقم {ep_id}، يرجى تنفيذ المهمة التالية أولاً:", 
                                       reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith("show_"):
        ep_id = query.data.split("_")[1]
        link = db["episodes"].get(ep_id, "رابط مفقود")
        await query.edit_message_text(f"🎁 تفضل الحلقة رقم {ep_id}:\n\n{link}\n\nمشاهدة ممتعة!")

    elif query.data == "admin_panel":
        if user_id != ADMIN_ID: return
        keyboard = [
            [InlineKeyboardButton("➕ إضافة حلقة", callback_data="add_ep")],
            [InlineKeyboardButton("🔗 تعديل رابط المهمة", callback_data="edit_task")],
            [InlineKeyboardButton("📝 تعديل نص المهمة", callback_data="edit_text")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_home")]
        ]
        await query.edit_message_text("🛠️ أهلاً بك يا مدير! ماذا تريد أن تفعل؟", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "back_home":
        await start(update, context)

    # أوامر الأدمن التفاعلية (إضافة حلقة)
    elif query.data == "add_ep":
        await query.edit_message_text("أرسل الآن رقم الحلقة متبوعاً بالرابط بهذا الشكل:\n\n`1 https://t.me/your_channel/123`", parse_mode="Markdown")
        context.user_data['action'] = 'adding_ep'

# --- معالجة الرسائل النصية (للأدمن) ---
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID: return
    
    action = context.user_data.get('action')
    text = update.message.text
    db = load_data()

    if action == 'adding_ep':
        try:
            ep_id, link = text.split(" ", 1)
            db["episodes"][ep_id] = link
            save_data(db)
            await update.message.reply_text(f"✅ تم بنجاح إضافة الحلقة {ep_id}")
            context.user_data['action'] = None
        except:
            await update.message.reply_text("❌ خطأ! أرسل (الرقم ثم مسافة ثم الرابط)")

# --- التشغيل ---
if __name__ == '__main__':
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    print("Bot is running...")
    app.run_polling()

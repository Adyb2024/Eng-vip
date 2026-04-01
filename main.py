import os
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from pymongo import MongoClient
from bson import ObjectId

# إعداد السجلات
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# إعدادات البوت
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")
MONGODB_URI = os.getenv("MONGODB_URI")

# الاتصال بقاعدة البيانات MongoDB
client = MongoClient(MONGODB_URI)
db = client['telegram_bot']
episodes_col = db['episodes']
tasks_col = db['tasks']
user_tasks_col = db['user_tasks']
settings_col = db['settings']
users_col = db['users']  # لتسجيل المستخدمين للبث

# دوال مساعدة
def get_tasks():
    return list(tasks_col.find().sort('priority', -1))

def add_task(task_type, target, description, priority=0):
    tasks_col.insert_one({
        'type': task_type,
        'target': target,
        'description': description,
        'priority': priority
    })

def delete_task(task_id):
    tasks_col.delete_one({'_id': ObjectId(task_id)})

def get_episodes():
    return list(episodes_col.find().sort('created_at', -1))

def add_episode(ep_id, title, link):
    episodes_col.insert_one({
        '_id': ep_id,
        'title': title,
        'link': link,
        'views': 0,
        'created_at': datetime.now()
    })

def update_episode(ep_id, title=None, link=None):
    update_data = {}
    if title:
        update_data['title'] = title
    if link:
        update_data['link'] = link
    if update_data:
        episodes_col.update_one({'_id': ep_id}, {'$set': update_data})

def delete_episode(ep_id):
    episodes_col.delete_one({'_id': ep_id})

def increment_views(ep_id):
    episodes_col.update_one({'_id': ep_id}, {'$inc': {'views': 1}})

def mark_task_completed(user_id, task_id):
    user_tasks_col.update_one(
        {'user_id': user_id, 'task_id': task_id},
        {'$set': {'completed_at': datetime.now()}},
        upsert=True
    )

def has_completed_task(user_id, task_id):
    return user_tasks_col.find_one({'user_id': user_id, 'task_id': task_id}) is not None

def get_setting(key, default=None):
    setting = settings_col.find_one({'_id': key})
    return setting['value'] if setting else default

def set_setting(key, value):
    settings_col.update_one({'_id': key}, {'$set': {'value': value}}, upsert=True)

def register_user(user_id, username=None):
    users_col.update_one(
        {'user_id': user_id},
        {'$set': {'username': username, 'last_active': datetime.now()}},
        upsert=True
    )

# دوال التحقق
async def is_subscribed(user_id, channel_id):
    try:
        member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False

async def check_tasks(user_id, context):
    incomplete = []
    tasks = get_tasks()
    for task in tasks:
        task_id = str(task['_id'])
        if has_completed_task(user_id, task_id):
            continue
        if task['type'] == 'channel':
            if not await is_subscribed(user_id, task['target']):
                incomplete.append(task)
        # يمكن إضافة أنواع أخرى هنا
    return incomplete

# عرض القائمة الرئيسية
async def main_menu(user_id, context, message=None, edit=False):
    tasks = await check_tasks(user_id, context)
    keyboard = []
    if not tasks:
        episodes = get_episodes()
        if episodes:
            for ep in episodes:
                ep_id = ep['_id']
                title = ep['title']
                keyboard.append([InlineKeyboardButton(f"🎬 {title}", callback_data=f"ep_{ep_id}")])
        else:
            keyboard.append([InlineKeyboardButton("📭 لا توجد حلقات بعد", callback_data="none")])
    else:
        for task in tasks:
            target = task['target']
            desc = task['description']
            url = f"https://t.me/{target.lstrip('@')}" if task['type'] == 'channel' else None
            if url:
                keyboard.append([InlineKeyboardButton(f"📢 {desc}", url=url)])
        keyboard.append([InlineKeyboardButton("✅ تم تنفيذ المهام", callback_data="check_tasks")])

    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚙️ لوحة التحكم", callback_data="admin_panel")])
    else:
        keyboard.append([InlineKeyboardButton("🔄 تحديث", callback_data="refresh")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "🎯 *مرحباً بك في البوت!*\n\n"
    if tasks:
        text += "للمتابعة، يرجى إكمال المهام التالية أولاً:\n"
        for task in tasks:
            text += f"• {task['description']}\n"
        text += "\nبعد الاشتراك، اضغط على زر التحديث."
    else:
        text += "اختر الحلقة التي تريد مشاهدتها:"

    if edit and message:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

# أمر /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    register_user(user_id, username)
    await main_menu(user_id, context, message=update.message, edit=False)

# معالج الأزرار
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

    if data == "refresh":
        await main_menu(user_id, context, message=query.message, edit=True)

    elif data == "check_tasks":
        incomplete = await check_tasks(user_id, context)
        if not incomplete:
            await main_menu(user_id, context, message=query.message, edit=True)
        else:
            await query.answer("❌ لا تزال هناك مهام غير مكتملة!", show_alert=True)
            await main_menu(user_id, context, message=query.message, edit=True)

    elif data.startswith("ep_"):
        ep_id = data.split("_")[1]
        incomplete = await check_tasks(user_id, context)
        if incomplete:
            await query.answer("⚠️ يجب إكمال المهام أولاً!", show_alert=True)
            await main_menu(user_id, context, message=query.message, edit=True)
            return
        episode = episodes_col.find_one({'_id': ep_id})
        if episode:
            increment_views(ep_id)
            text = f"🎬 *{episode['title']}*\n\n{episode['link']}\n\n🎉 استمتع بالمشاهدة!"
            await query.edit_message_text(text, parse_mode="Markdown")
        else:
            await query.edit_message_text("❌ الحلقة غير موجودة.")

    elif data == "admin_panel":
        if user_id != ADMIN_ID:
            await query.answer("غير مصرح", show_alert=True)
            return
        keyboard = [
            [InlineKeyboardButton("➕ إضافة حلقة", callback_data="admin_add_ep")],
            [InlineKeyboardButton("📝 تعديل حلقة", callback_data="admin_edit_ep")],
            [InlineKeyboardButton("🗑 حذف حلقة", callback_data="admin_del_ep")],
            [InlineKeyboardButton("📢 إضافة مهمة", callback_data="admin_add_task")],
            [InlineKeyboardButton("📋 عرض المهام", callback_data="admin_list_tasks")],
            [InlineKeyboardButton("🗑 حذف مهمة", callback_data="admin_del_task")],
            [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")],
            [InlineKeyboardButton("📢 إرسال إشعار", callback_data="admin_broadcast")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ]
        await query.edit_message_text("🛠️ *لوحة تحكم الأدمن*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "back_to_main":
        await main_menu(user_id, context, message=query.message, edit=True)

    # دوال الأدمن (سيتم تنفيذها لاحقاً)
    elif data == "admin_add_ep":
        context.user_data['admin_state'] = 'waiting_ep_id'
        await query.edit_message_text("أرسل رقم الحلقة (مثال: 1):")

    elif data == "admin_edit_ep":
        episodes = get_episodes()
        if not episodes:
            await query.edit_message_text("لا توجد حلقات لتعديلها.")
            return
        keyboard = [[InlineKeyboardButton(f"{ep['title']} ({ep['_id']})", callback_data=f"edit_ep_{ep['_id']}")] for ep in episodes]
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
        await query.edit_message_text("اختر الحلقة لتعديلها:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("edit_ep_"):
        ep_id = data.split("_")[2]
        context.user_data['edit_ep_id'] = ep_id
        context.user_data['admin_state'] = 'waiting_ep_title_edit'
        await query.edit_message_text("أرسل العنوان الجديد (أو أرسل 'تخطي' للبقاء على نفس العنوان):")

    elif data == "admin_del_ep":
        episodes = get_episodes()
        if not episodes:
            await query.edit_message_text("لا توجد حلقات لحذفها.")
            return
        keyboard = [[InlineKeyboardButton(f"{ep['title']} ({ep['_id']})", callback_data=f"del_ep_{ep['_id']}")] for ep in episodes]
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
        await query.edit_message_text("اختر الحلقة لحذفها:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("del_ep_"):
        ep_id = data.split("_")[2]
        delete_episode(ep_id)
        await query.edit_message_text("✅ تم حذف الحلقة بنجاح!")
        await main_menu(user_id, context, message=query.message, edit=False)  # العودة للقائمة الرئيسية

    elif data == "admin_add_task":
        context.user_data['admin_state'] = 'waiting_task_type'
        keyboard = [
            [InlineKeyboardButton("قناة تليجرام", callback_data="task_type_channel")],
            [InlineKeyboardButton("إلغاء", callback_data="admin_panel")]
        ]
        await query.edit_message_text("اختر نوع المهمة:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("task_type_"):
        task_type = data.split("_")[2]  # channel
        context.user_data['task_type'] = task_type
        context.user_data['admin_state'] = 'waiting_task_target'
        await query.edit_message_text(f"أرسل معرف القناة (مثال: @username أو -100xxxxx) للمهمة من نوع {task_type}:")

    elif data == "admin_list_tasks":
        tasks = get_tasks()
        if not tasks:
            await query.edit_message_text("لا توجد مهام حالياً.")
            return
        text = "*المهام الحالية:*\n\n"
        for t in tasks:
            text += f"🔹 {t['description']}\n   النوع: {t['type']}\n   الهدف: {t['target']}\n   الأولوية: {t['priority']}\n   المعرف: `{t['_id']}`\n\n"
        await query.edit_message_text(text, parse_mode="Markdown")

    elif data == "admin_del_task":
        tasks = get_tasks()
        if not tasks:
            await query.edit_message_text("لا توجد مهام لحذفها.")
            return
        keyboard = [[InlineKeyboardButton(t['description'], callback_data=f"del_task_{t['_id']}")] for t in tasks]
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
        await query.edit_message_text("اختر المهمة لحذفها:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("del_task_"):
        task_id = data.split("_")[2]
        delete_task(task_id)
        await query.edit_message_text("✅ تم حذف المهمة بنجاح!")
        await main_menu(user_id, context, message=query.message, edit=False)

    elif data == "admin_stats":
        episodes = get_episodes()
        users_count = users_col.count_documents({})
        text = f"📊 *الإحصائيات*\n\n👥 عدد المستخدمين: {users_count}\n🎬 عدد الحلقات: {len(episodes)}\n\n*أكثر الحلقات مشاهدة:*\n"
        sorted_eps = sorted(episodes, key=lambda x: x['views'], reverse=True)[:5]
        for ep in sorted_eps:
            text += f"• {ep['title']}: {ep['views']} مشاهدة\n"
        await query.edit_message_text(text, parse_mode="Markdown")

    elif data == "admin_broadcast":
        context.user_data['admin_state'] = 'waiting_broadcast'
        await query.edit_message_text("أرسل الرسالة التي تريد بثها لجميع المستخدمين (يمكن استخدام Markdown):")

# معالج الرسائل النصية للأدمن
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    text = update.message.text
    state = context.user_data.get('admin_state')

    if state == 'waiting_ep_id':
        context.user_data['temp_ep_id'] = text
        context.user_data['admin_state'] = 'waiting_ep_title'
        await update.message.reply_text("أرسل عنوان الحلقة:")

    elif state == 'waiting_ep_title':
        context.user_data['temp_ep_title'] = text
        context.user_data['admin_state'] = 'waiting_ep_link'
        await update.message.reply_text("أرسل رابط الحلقة:")

    elif state == 'waiting_ep_link':
        ep_id = context.user_data['temp_ep_id']
        title = context.user_data['temp_ep_title']
        link = text
        add_episode(ep_id, title, link)
        await update.message.reply_text(f"✅ تم إضافة الحلقة {title} بنجاح!")
        context.user_data['admin_state'] = None
        # عرض القائمة الرئيسية
        await main_menu(user_id, context, message=update.message, edit=False)

    elif state == 'waiting_ep_title_edit':
        ep_id = context.user_data['edit_ep_id']
        if text.lower() != 'تخطي':
            update_episode(ep_id, title=text)
        context.user_data['admin_state'] = 'waiting_ep_link_edit'
        await update.message.reply_text("أرسل الرابط الجديد (أو 'تخطي' للبقاء على نفس الرابط):")

    elif state == 'waiting_ep_link_edit':
        ep_id = context.user_data['edit_ep_id']
        if text.lower() != 'تخطي':
            update_episode(ep_id, link=text)
        await update.message.reply_text("✅ تم تعديل الحلقة بنجاح!")
        context.user_data['admin_state'] = None
        await main_menu(user_id, context, message=update.message, edit=False)

    elif state == 'waiting_task_target':
        task_type = context.user_data['task_type']
        target = text
        context.user_data['task_target'] = target
        context.user_data['admin_state'] = 'waiting_task_desc'
        await update.message.reply_text("أرسل وصف المهمة (النص الذي سيظهر للمستخدم):")

    elif state == 'waiting_task_desc':
        desc = text
        target = context.user_data['task_target']
        task_type = context.user_data['task_type']
        priority = 0  # يمكن جعله اختياري
        add_task(task_type, target, desc, priority)
        await update.message.reply_text(f"✅ تم إضافة المهمة '{desc}' بنجاح!")
        context.user_data['admin_state'] = None
        await main_menu(user_id, context, message=update.message, edit=False)

    elif state == 'waiting_broadcast':
        users = users_col.find()
        success = 0
        fail = 0
        for user in users:
            try:
                await context.bot.send_message(chat_id=user['user_id'], text=text, parse_mode="Markdown")
                success += 1
            except Exception as e:
                fail += 1
        await update.message.reply_text(f"📢 تم الإرسال\n✅ نجح: {success}\n❌ فشل: {fail}")
        context.user_data['admin_state'] = None
        await main_menu(user_id, context, message=update.message, edit=False)

# التشغيل
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    logging.info("Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()

import os
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from pymongo import MongoClient
from bson import ObjectId

# إعداد السجلات
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# متغيرات البيئة
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")
MONGODB_URI = os.getenv("MONGODB_URI")

# الاتصال بقاعدة البيانات
client = MongoClient(MONGODB_URI)
db = client['telegram_bot']
episodes_col = db['episodes']
tasks_col = db['tasks']
user_tasks_col = db['user_tasks']
pending_verifications = db['pending_verifications']
users_col = db['users']
settings_col = db['settings']

# دوال مساعدة
def get_setting(key, default=None):
    s = settings_col.find_one({'_id': key})
    return s['value'] if s else default

def set_setting(key, value):
    settings_col.update_one({'_id': key}, {'$set': {'value': value}}, upsert=True)

def get_verification_mode():
    return get_setting('verification_mode', 'manual')

def set_verification_mode(mode):
    set_setting('verification_mode', mode)

def get_tasks():
    return list(tasks_col.find().sort('priority', -1))

def add_task(task_type, target, description, action='follow', priority=0):
    tasks_col.insert_one({
        'type': task_type,
        'target': target,
        'description': description,
        'action': action,
        'priority': priority,
        'created_at': datetime.now()
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

def register_user(user_id, username=None):
    users_col.update_one(
        {'user_id': user_id},
        {'$set': {'username': username, 'last_active': datetime.now()}},
        upsert=True
    )

# التحقق من الاشتراك في القناة
async def is_subscribed(user_id, context):
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False

# التحقق من المهام الأخرى
async def check_other_tasks(user_id):
    incomplete = []
    tasks = get_tasks()
    for task in tasks:
        task_id = str(task['_id'])
        if has_completed_task(user_id, task_id):
            continue
        if task['type'] == 'channel':
            continue
        pending = pending_verifications.find_one({'user_id': user_id, 'task_id': task_id})
        if pending and pending.get('status') == 'approved':
            mark_task_completed(user_id, task_id)
        elif not pending:
            incomplete.append(task)
        elif pending.get('status') == 'pending':
            incomplete.append(task)
    return incomplete

# بناء لوحة مفاتيح نصية للقائمة الرئيسية
def get_main_keyboard(user_id, tasks, episodes):
    keyboard = []
    if tasks:
        for task in tasks:
            keyboard.append([KeyboardButton(f"✅ {task['description']}")])
        keyboard.append([KeyboardButton("🔄 تحديث")])
    elif episodes:
        for ep in episodes:
            keyboard.append([KeyboardButton(f"🎬 {ep['title']}")])
        keyboard.append([KeyboardButton("🔄 تحديث")])
    else:
        keyboard.append([KeyboardButton("🔄 تحديث")])
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton("⚙️ لوحة التحكم")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# بناء لوحة مفاتيح نصية للأدمن
def get_admin_keyboard():
    keyboard = [
        ["➕ إضافة حلقة", "📝 تعديل حلقة", "🗑 حذف حلقة"],
        ["📢 إضافة مهمة", "📋 عرض المهام", "🗑 حذف مهمة"],
        ["📊 الإحصائيات", "📢 إرسال إشعار", "🔄 تبديل الوضع"],
        ["🔐 طلبات التحقق المعلقة", "🔙 رجوع"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# عرض القائمة الرئيسية
async def show_main_menu(user_id, context, message, edit=False):
    # التحقق من القناة الإجبارية
    if not await is_subscribed(user_id, context):
        if CHANNEL_ID.startswith('-100'):
            link = f"https://t.me/{CHANNEL_ID[4:]}"
        else:
            link = f"https://t.me/{CHANNEL_ID.lstrip('@')}"
        text = "⚠️ توقف! للاستفادة من البوت، يجب عليك الاشتراك في القناة أولاً.\n\n"
        text += f"📢 [انضم للقناة]({link})\n\n"
        text += "بعد الاشتراك، اضغط على الزر أدناه للتحقق."
        keyboard = ReplyKeyboardMarkup([["✅ تم الاشتراك ✅ تحقّق الآن"]], resize_keyboard=True)
        await message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return

    # بقية المهام
    other_tasks = await check_other_tasks(user_id)
    if other_tasks:
        keyboard = get_main_keyboard(user_id, other_tasks, [])
        text = "🎯 للمتابعة، يرجى إكمال المهام التالية:\n"
        for t in other_tasks:
            text += f"• {t['description']}\n"
        text += "\nبعد تنفيذ المهمة، اضغط على الزر المطابق لها وأرسل لقطة شاشة."
        if get_verification_mode() == 'auto':
            text += "\n🟢 *الوضع الآلي مفعل*: سيتم قبول طلبك تلقائياً."
        else:
            text += "\n🔴 *الوضع اليدوي مفعل*: سيتم مراجعة طلبك من قبل الأدمن."
        await message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return

    # عرض الحلقات
    episodes = get_episodes()
    if episodes:
        keyboard = get_main_keyboard(user_id, [], episodes)
        text = "🎬 *مرحباً بك!*\nاختر الحلقة التي تريد مشاهدتها:"
        await message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        keyboard = ReplyKeyboardMarkup([["🔄 تحديث"]], resize_keyboard=True)
        await message.reply_text("📭 لا توجد حلقات مضافة حالياً. تواصل مع الأدمن.", reply_markup=keyboard)

# أمر /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    register_user(user_id, username)
    await show_main_menu(user_id, context, update.message, edit=False)

# أمر /admin للأدمن
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ هذا الأمر مخصص للأدمن فقط.")
        return
    await update.message.reply_text("🛠️ *لوحة تحكم الأدمن*", reply_markup=get_admin_keyboard(), parse_mode="Markdown")

# معالج الرسائل النصية (الأزرار النصية)
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # إذا كان الأدمن في حالة إدخال (إضافة حلقة، إلخ)
    if user_id == ADMIN_ID and context.user_data.get('admin_state'):
        await admin_text_handler(update, context)
        return

    # معالجة الأزرار النصية العامة
    if text == "✅ تم الاشتراك ✅ تحقّق الآن":
        if await is_subscribed(user_id, context):
            await update.message.reply_text("✅ أحسنت! تم التحقق من اشتراكك بنجاح.")
            await show_main_menu(user_id, context, update.message, edit=False)
        else:
            await update.message.reply_text("❌ لم تشترك بعد! يرجى الانضمام للقناة والمحاولة مرة أخرى.")
        return

    if text == "🔄 تحديث":
        await show_main_menu(user_id, context, update.message, edit=False)
        return

    # معالجة المهام (الضغط على زر مهمة)
    tasks = get_tasks()
    for task in tasks:
        if text == f"✅ {task['description']}":
            task_id = str(task['_id'])
            context.user_data['pending_task_id'] = task_id
            context.user_data['awaiting_screenshot'] = True
            await update.message.reply_text(
                "📸 يرجى إرسال لقطة شاشة تثبت إكمال المهمة.\nيمكنك إرسال الصورة الآن.",
                reply_markup=ReplyKeyboardRemove()
            )
            return

    # معالجة الحلقات
    episodes = get_episodes()
    for ep in episodes:
        if text == f"🎬 {ep['title']}":
            other_tasks = await check_other_tasks(user_id)
            if other_tasks:
                await update.message.reply_text("⚠️ يجب إكمال المهام أولاً!")
                await show_main_menu(user_id, context, update.message, edit=False)
                return
            increment_views(ep['_id'])
            msg = f"🎬 *{ep['title']}*\n\n{ep['link']}\n\n🎉 استمتع بالمشاهدة!"
            await update.message.reply_text(msg, parse_mode="Markdown")
            return

    # لوحة التحكم للأدمن
    if user_id == ADMIN_ID and text == "⚙️ لوحة التحكم":
        await update.message.reply_text("🛠️ *لوحة تحكم الأدمن*", reply_markup=get_admin_keyboard(), parse_mode="Markdown")
        return

    # إذا لم يتطابق أي زر
    await update.message.reply_text("لا أفهم هذا الأمر. استخدم الأزرار المتاحة.")

# معالج النصوص الخاص بالأدمن (مراحل الإدخال)
async def admin_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    state = context.user_data.get('admin_state')

    # معالجة الأزرار الرئيسية في لوحة الأدمن
    if text == "➕ إضافة حلقة":
        context.user_data['admin_state'] = 'waiting_ep_id'
        await update.message.reply_text("أرسل رقم الحلقة (مثال: 1):", reply_markup=ReplyKeyboardRemove())
        return
    elif text == "📝 تعديل حلقة":
        episodes = get_episodes()
        if not episodes:
            await update.message.reply_text("لا توجد حلقات لتعديلها.")
            return
        keyboard = ReplyKeyboardMarkup([[f"📝 {ep['title']} ({ep['_id']})"] for ep in episodes] + [["🔙 رجوع"]], resize_keyboard=True)
        await update.message.reply_text("اختر الحلقة لتعديلها:", reply_markup=keyboard)
        return
    elif text == "🗑 حذف حلقة":
        episodes = get_episodes()
        if not episodes:
            await update.message.reply_text("لا توجد حلقات لحذفها.")
            return
        keyboard = ReplyKeyboardMarkup([[f"🗑 {ep['title']} ({ep['_id']})"] for ep in episodes] + [["🔙 رجوع"]], resize_keyboard=True)
        await update.message.reply_text("اختر الحلقة لحذفها:", reply_markup=keyboard)
        return
    elif text == "📢 إضافة مهمة":
        context.user_data['admin_state'] = 'waiting_task_type'
        keyboard = ReplyKeyboardMarkup([["قناة تليجرام", "تويتر"], ["فيسبوك", "إنستا", "تيك توك"], ["🔙 رجوع"]], resize_keyboard=True)
        await update.message.reply_text("اختر نوع المهمة:", reply_markup=keyboard)
        return
    elif text == "📋 عرض المهام":
        tasks = get_tasks()
        if not tasks:
            await update.message.reply_text("لا توجد مهام حالياً.")
            return
        msg = "*المهام الحالية:*\n\n"
        for t in tasks:
            msg += f"🔹 {t['description']}\n   النوع: {t['type']}\n   الهدف: {t['target']}\n   المعرف: `{t['_id']}`\n\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return
    elif text == "🗑 حذف مهمة":
        tasks = get_tasks()
        if not tasks:
            await update.message.reply_text("لا توجد مهام لحذفها.")
            return
        keyboard = ReplyKeyboardMarkup([[t['description']] for t in tasks] + [["🔙 رجوع"]], resize_keyboard=True)
        await update.message.reply_text("اختر المهمة لحذفها:", reply_markup=keyboard)
        return
    elif text == "📊 الإحصائيات":
        episodes = get_episodes()
        users_count = users_col.count_documents({})
        msg = f"📊 *الإحصائيات*\n\n👥 عدد المستخدمين: {users_count}\n🎬 عدد الحلقات: {len(episodes)}\n\n*أكثر الحلقات مشاهدة:*\n"
        sorted_eps = sorted(episodes, key=lambda x: x['views'], reverse=True)[:5]
        for ep in sorted_eps:
            msg += f"• {ep['title']}: {ep['views']} مشاهدة\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return
    elif text == "📢 إرسال إشعار":
        context.user_data['admin_state'] = 'waiting_broadcast'
        await update.message.reply_text("أرسل الرسالة التي تريد بثها لجميع المستخدمين (يمكن استخدام Markdown):", reply_markup=ReplyKeyboardRemove())
        return
    elif text == "🔄 تبديل الوضع":
        current = get_verification_mode()
        new_mode = 'auto' if current == 'manual' else 'manual'
        set_verification_mode(new_mode)
        await update.message.reply_text(f"تم التبديل إلى الوضع {'الآلي' if new_mode == 'auto' else 'اليدوي'}")
        return
    elif text == "🔐 طلبات التحقق المعلقة":
        pendings = list(pending_verifications.find({'status': 'pending'}))
        if not pendings:
            await update.message.reply_text("لا توجد طلبات تحقق معلقة.")
            return
        for p in pendings:
            user = users_col.find_one({'user_id': p['user_id']})
            username = user.get('username') if user else p['user_id']
            task = tasks_col.find_one({'_id': ObjectId(p['task_id'])})
            task_desc = task['description'] if task else 'غير معروف'
            await update.message.reply_text(f"📝 *طلب من @{username}*\nالمهمة: {task_desc}\nالمرسل: {p['user_id']}")
            await context.bot.send_photo(chat_id=user_id, photo=p['photo_file_id'])
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ موافقة", callback_data=f"approve_{p['_id']}")],
                [InlineKeyboardButton("❌ رفض", callback_data=f"reject_{p['_id']}")]
            ])
            await update.message.reply_text("اختر الإجراء:", reply_markup=keyboard)
        return
    elif text == "🔙 رجوع":
        await show_main_menu(user_id, context, update.message, edit=False)
        return

    # معالجة اختيار حلقة للتعديل/الحذف
    if text.startswith("📝 "):
        ep_id = text.split("(")[-1].rstrip(")")
        context.user_data['edit_ep_id'] = ep_id
        context.user_data['admin_state'] = 'waiting_ep_title_edit'
        await update.message.reply_text("أرسل العنوان الجديد (أو 'تخطي' للبقاء على نفس العنوان):", reply_markup=ReplyKeyboardRemove())
        return
    if text.startswith("🗑 "):
        ep_id = text.split("(")[-1].rstrip(")")
        delete_episode(ep_id)
        await update.message.reply_text("✅ تم حذف الحلقة بنجاح!")
        await show_main_menu(user_id, context, update.message, edit=False)
        return

    # معالجة اختيار مهمة للحذف
    if text in [t['description'] for t in get_tasks()]:
        task = tasks_col.find_one({'description': text})
        if task:
            delete_task(str(task['_id']))
            await update.message.reply_text("✅ تم حذف المهمة بنجاح!")
            await show_main_menu(user_id, context, update.message, edit=False)
        return

    # معالجة اختيار نوع المهمة
    if text in ["قناة تليجرام", "تويتر", "فيسبوك", "إنستا", "تيك توك"]:
        task_type_map = {
            "قناة تليجرام": "channel",
            "تويتر": "twitter",
            "فيسبوك": "facebook",
            "إنستا": "instagram",
            "تيك توك": "tiktok"
        }
        context.user_data['task_type'] = task_type_map[text]
        context.user_data['admin_state'] = 'waiting_task_target'
        await update.message.reply_text("أرسل معرف الحساب (مثال: username):", reply_markup=ReplyKeyboardRemove())
        return

    # معالجة مراحل الإدخال
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
        await show_main_menu(user_id, context, update.message, edit=False)
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
        await show_main_menu(user_id, context, update.message, edit=False)
    elif state == 'waiting_task_target':
        context.user_data['task_target'] = text
        context.user_data['admin_state'] = 'waiting_task_desc'
        await update.message.reply_text("أرسل وصف المهمة:")
    elif state == 'waiting_task_desc':
        desc = text
        target = context.user_data['task_target']
        task_type = context.user_data.get('task_type', 'channel')
        add_task(task_type, target, desc)
        await update.message.reply_text(f"✅ تم إضافة المهمة '{desc}' بنجاح!")
        context.user_data['admin_state'] = None
        await show_main_menu(user_id, context, update.message, edit=False)
    elif state == 'waiting_broadcast':
        users = users_col.find()
        success = 0
        fail = 0
        for user in users:
            try:
                await context.bot.send_message(chat_id=user['user_id'], text=text, parse_mode="Markdown")
                success += 1
            except Exception:
                fail += 1
        await update.message.reply_text(f"📢 تم الإرسال\n✅ نجح: {success}\n❌ فشل: {fail}")
        context.user_data['admin_state'] = None
        await show_main_menu(user_id, context, update.message, edit=False)
    else:
        await update.message.reply_text("لا أفهم هذا الأمر. استخدم الأزرار المتاحة.")

# معالج الصور
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.user_data.get('awaiting_screenshot'):
        return

    task_id = context.user_data.get('pending_task_id')
    if not task_id:
        await update.message.reply_text("حدث خطأ، حاول مرة أخرى.")
        context.user_data['awaiting_screenshot'] = False
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id

    mode = get_verification_mode()
    if mode == 'auto':
        pending_verifications.insert_one({
            'user_id': user_id,
            'task_id': task_id,
            'photo_file_id': file_id,
            'status': 'approved',
            'created_at': datetime.now()
        })
        mark_task_completed(user_id, task_id)
        await update.message.reply_text("✅ تم التحقق من لقطة الشاشة تلقائيًا. يمكنك الآن مشاهدة الحلقات.")
        context.user_data['awaiting_screenshot'] = False
        context.user_data['pending_task_id'] = None
        await show_main_menu(user_id, context, update.message, edit=False)
    else:
        pending_verifications.insert_one({
            'user_id': user_id,
            'task_id': task_id,
            'photo_file_id': file_id,
            'status': 'pending',
            'created_at': datetime.now()
        })
        task = tasks_col.find_one({'_id': ObjectId(task_id)})
        task_desc = task['description'] if task else 'غير معروف'
        await context.bot.send_message(
            ADMIN_ID,
            f"📢 *طلب تحقق جديد*\nالمستخدم: {user_id}\nالمهمة: {task_desc}",
            parse_mode="Markdown"
        )
        await update.message.reply_text("✅ تم استلام لقطة الشاشة. سيتم مراجعتها من قبل الأدمن قريبًا.")
        context.user_data['awaiting_screenshot'] = False
        context.user_data['pending_task_id'] = None
        await show_main_menu(user_id, context, update.message, edit=False)

# معالج الاستعلامات (للأزرار الشفافة في الموافقة/الرفض)
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("approve_"):
        pending_id = data.split("_")[1]
        pending = pending_verifications.find_one({'_id': ObjectId(pending_id)})
        if pending:
            pending_verifications.update_one({'_id': ObjectId(pending_id)}, {'$set': {'status': 'approved'}})
            try:
                await context.bot.send_message(pending['user_id'], "✅ تمت الموافقة على طلبك! يمكنك الآن مشاهدة الحلقات.")
            except:
                pass
        await query.edit_message_text("✅ تمت الموافقة على الطلب.")
    elif data.startswith("reject_"):
        pending_id = data.split("_")[1]
        pending_verifications.delete_one({'_id': ObjectId(pending_id)})
        await query.edit_message_text("❌ تم رفض الطلب.")
    await query.message.delete()

def main():
    # إضافة مهمة القناة الافتراضية إذا لم توجد مهام
    if tasks_col.count_documents({}) == 0:
        add_task('channel', CHANNEL_ID, f"الاشتراك في القناة", priority=1)

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))
    logging.info("🚀 البوت يعمل...")
    app.run_polling()

if __name__ == '__main__':
    main()

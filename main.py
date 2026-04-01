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

# الاتصال بقاعدة البيانات
client = MongoClient(MONGODB_URI)
db = client['telegram_bot']
episodes_col = db['episodes']
tasks_col = db['tasks']
user_tasks_col = db['user_tasks']
pending_verifications = db['pending_verifications']
users_col = db['users']
settings_col = db['settings']

# دوال مساعدة للقاعدة
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
    episodes = list(episodes_col.find().sort('created_at', -1))
    logging.info(f"📊 عدد الحلقات المسترجعة: {len(episodes)}")
    for ep in episodes:
        logging.info(f"  - {ep.get('_id')}: {ep.get('title')}")
    return episodes

def add_episode(ep_id, title, link):
    episodes_col.insert_one({
        '_id': ep_id,
        'title': title,
        'link': link,
        'views': 0,
        'created_at': datetime.now()
    })
    logging.info(f"✅ تم إضافة حلقة جديدة: {ep_id} - {title}")

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
    logging.info(f"✅ تم إكمال المهمة {task_id} للمستخدم {user_id}")

def has_completed_task(user_id, task_id):
    result = user_tasks_col.find_one({'user_id': user_id, 'task_id': task_id})
    return result is not None

def register_user(user_id, username=None):
    users_col.update_one(
        {'user_id': user_id},
        {'$set': {'username': username, 'last_active': datetime.now()}},
        upsert=True
    )

async def is_subscribed(user_id, channel_id, context):
    try:
        member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logging.error(f"خطأ في التحقق من الاشتراك: {e}")
        return False

async def check_tasks(user_id, context):
    incomplete = []
    tasks = get_tasks()
    logging.info(f"🔍 التحقق من المهام للمستخدم {user_id}، عدد المهام: {len(tasks)}")
    
    for task in tasks:
        task_id = str(task['_id'])
        if has_completed_task(user_id, task_id):
            logging.info(f"  ✅ المستخدم أكمل المهمة {task['description']}")
            continue
            
        if task['type'] == 'channel':
            subscribed = await is_subscribed(user_id, task['target'], context)
            if subscribed:
                mark_task_completed(user_id, task_id)
                logging.info(f"  ✅ المستخدم اشترك في القناة وتم تسجيل المهمة")
            else:
                incomplete.append(task)
                logging.info(f"  ❌ المستخدم لم يكمل المهمة: {task['description']}")
        else:
            pending = pending_verifications.find_one({'user_id': user_id, 'task_id': task_id})
            if pending and pending.get('status') == 'approved':
                mark_task_completed(user_id, task_id)
                logging.info(f"  ✅ تمت الموافقة على الطلب للمستخدم {user_id}")
            elif not pending:
                incomplete.append(task)
                logging.info(f"  ⏳ المهمة {task['description']} في انتظار طلب التحقق")
            elif pending.get('status') == 'pending':
                incomplete.append(task)
                logging.info(f"  ⏳ طلب التحقق للمستخدم {user_id} قيد المراجعة")
    
    logging.info(f"📋 المهام غير المكتملة للمستخدم {user_id}: {len(incomplete)}")
    return incomplete

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
            logging.info(f"📺 عرض {len(episodes)} حلقة للمستخدم {user_id}")
        else:
            keyboard.append([InlineKeyboardButton("📭 لا توجد حلقات مضافة بعد", callback_data="none")])
            logging.warning(f"⚠️ لا توجد حلقات في قاعدة البيانات للمستخدم {user_id}")
    else:
        for task in tasks:
            task_type = task['type']
            target = task['target']
            desc = task['description']
            
            if task_type == 'channel':
                url = f"https://t.me/{target.lstrip('@')}"
            elif task_type == 'twitter':
                url = f"https://twitter.com/{target}"
            elif task_type == 'facebook':
                url = f"https://www.facebook.com/{target}"
            elif task_type == 'instagram':
                url = f"https://www.instagram.com/{target}"
            elif task_type == 'tiktok':
                url = f"https://www.tiktok.com/@{target}"
            else:
                url = None
                
            if url:
                keyboard.append([InlineKeyboardButton(f"📢 {desc}", url=url)])
            keyboard.append([InlineKeyboardButton(f"✅ تمت المتابعة ({desc})", callback_data=f"verify_{task['_id']}")])
        
        keyboard.append([InlineKeyboardButton("🔄 تحديث", callback_data="refresh")])

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
        text += "\nبعد تنفيذ المهمة، اضغط على زر 'تمت المتابعة' وأرسل لقطة شاشة."
        if get_verification_mode() == 'auto':
            text += "\n🟢 *الوضع الآلي مفعل*: سيتم قبول طلبك تلقائياً."
        else:
            text += "\n🔴 *الوضع اليدوي مفعل*: سيتم مراجعة طلبك من قبل الأدمن."
    else:
        if episodes := get_episodes():
            text += f"📺 يوجد {len(episodes)} حلقة متاحة.\nاختر الحلقة التي تريد مشاهدتها:"
        else:
            text += "📭 لا توجد حلقات مضافة حالياً. تواصل مع الأدمن لإضافة الحلقات."

    try:
        if edit and message:
            await message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"خطأ في عرض القائمة: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    register_user(user_id, username)
    logging.info(f"🚀 مستخدم جديد: {user_id} (@{username})")
    await main_menu(user_id, context, message=update.message, edit=False)

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
        await main_menu(user_id, context, message=update.message, edit=False)
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
        await main_menu(user_id, context, message=update.message, edit=False)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

    if data == "refresh":
        await main_menu(user_id, context, message=query.message, edit=True)

    elif data.startswith("verify_"):
        task_id = data.split("_")[1]
        context.user_data['pending_task_id'] = task_id
        context.user_data['awaiting_screenshot'] = True
        await query.edit_message_text(
            "📸 يرجى إرسال لقطة شاشة تثبت إكمال المهمة.\n"
            "يمكنك إرسال الصورة الآن."
        )

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
        mode = get_verification_mode()
        mode_text = "🟢 الوضع الآلي" if mode == 'auto' else "🔴 الوضع اليدوي"
        mode_button = InlineKeyboardButton(f"🔄 تبديل الوضع ({mode_text})", callback_data="toggle_mode")
        keyboard = [
            [InlineKeyboardButton("➕ إضافة حلقة", callback_data="admin_add_ep")],
            [InlineKeyboardButton("📝 تعديل حلقة", callback_data="admin_edit_ep")],
            [InlineKeyboardButton("🗑 حذف حلقة", callback_data="admin_del_ep")],
            [InlineKeyboardButton("📢 إضافة مهمة", callback_data="admin_add_task")],
            [InlineKeyboardButton("📋 عرض المهام", callback_data="admin_list_tasks")],
            [InlineKeyboardButton("🗑 حذف مهمة", callback_data="admin_del_task")],
            [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")],
            [InlineKeyboardButton("📢 إرسال إشعار", callback_data="admin_broadcast")],
            [mode_button],
            [InlineKeyboardButton("🔐 طلبات التحقق المعلقة", callback_data="admin_pending")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ]
        await query.edit_message_text(
            f"🛠️ *لوحة تحكم الأدمن*\n\nحالة التحقق: {mode_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data == "toggle_mode":
        if user_id != ADMIN_ID:
            return
        current = get_verification_mode()
        new_mode = 'auto' if current == 'manual' else 'manual'
        set_verification_mode(new_mode)
        await query.answer(f"تم التبديل إلى الوضع {'الآلي' if new_mode == 'auto' else 'اليدوي'}")
        await button_handler(update, context)  # إعادة عرض اللوحة

    elif data == "admin_pending":
        if user_id != ADMIN_ID:
            return
        pendings = list(pending_verifications.find({'status': 'pending'}))
        if not pendings:
            await query.edit_message_text("لا توجد طلبات تحقق معلقة.")
            return
        keyboard = []
        for p in pendings:
            user = users_col.find_one({'user_id': p['user_id']})
            username = user.get('username') if user else p['user_id']
            task = tasks_col.find_one({'_id': ObjectId(p['task_id'])})
            task_desc = task['description'] if task else 'غير معروف'
            text = f"@{username} | {task_desc}"
            keyboard.append([InlineKeyboardButton(text, callback_data=f"review_{p['_id']}")])
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
        await query.edit_message_text("اختر طلبًا للمراجعة:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("review_"):
        pending_id = data.split("_")[1]
        pending = pending_verifications.find_one({'_id': ObjectId(pending_id)})
        if not pending:
            await query.edit_message_text("الطلب غير موجود.")
            return
        user = users_col.find_one({'user_id': pending['user_id']})
        username = user.get('username') if user else pending['user_id']
        task = tasks_col.find_one({'_id': ObjectId(pending['task_id'])})
        task_desc = task['description'] if task else 'غير معروف'
        photo_file_id = pending.get('photo_file_id')
        
        await query.edit_message_text(
            f"📝 *مراجعة الطلب*\nالمستخدم: @{username}\nالمهمة: {task_desc}",
            parse_mode="Markdown"
        )
        await context.bot.send_photo(chat_id=user_id, photo=photo_file_id)
        keyboard = [
            [InlineKeyboardButton("✅ موافقة", callback_data=f"approve_{pending_id}")],
            [InlineKeyboardButton("❌ رفض", callback_data=f"reject_{pending_id}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_pending")]
        ]
        await context.bot.send_message(chat_id=user_id, text="اختر الإجراء:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("approve_"):
        pending_id = data.split("_")[1]
        pending = pending_verifications.find_one({'_id': ObjectId(pending_id)})
        if pending:
            pending_verifications.update_one({'_id': ObjectId(pending_id)}, {'$set': {'status': 'approved'}})
            try:
                await context.bot.send_message(pending['user_id'], "✅ تمت الموافقة على طلبك! يمكنك الآن مشاهدة الحلقات.")
            except:
                pass
        await query.edit_message_text("✅ تمت الموافقة على الطلب.")
        await main_menu(user_id, context, message=query.message, edit=False)

    elif data.startswith("reject_"):
        pending_id = data.split("_")[1]
        pending_verifications.delete_one({'_id': ObjectId(pending_id)})
        await query.edit_message_text("❌ تم رفض الطلب.")
        await main_menu(user_id, context, message=query.message, edit=False)

    elif data == "back_to_main":
        await main_menu(user_id, context, message=query.message, edit=True)

    elif data == "admin_add_ep":
        context.user_data['admin_state'] = 'waiting_ep_id'
        await query.edit_message_text("أرسل رقم الحلقة (مثال: 1):")

    elif data == "admin_list_tasks":
        tasks = get_tasks()
        if not tasks:
            await query.edit_message_text("لا توجد مهام حالياً.")
            return
        text = "*المهام الحالية:*\n\n"
        for t in tasks:
            text += f"🔹 {t['description']}\n   النوع: {t['type']}\n   الهدف: {t['target']}\n   المعرف: `{t['_id']}`\n\n"
        await query.edit_message_text(text, parse_mode="Markdown")

    elif data == "admin_stats":
        episodes = get_episodes()
        users_count = users_col.count_documents({})
        text = f"📊 *الإحصائيات*\n\n👥 عدد المستخدمين: {users_count}\n🎬 عدد الحلقات: {len(episodes)}\n\n*أكثر الحلقات مشاهدة:*\n"
        sorted_eps = sorted(episodes, key=lambda x: x['views'], reverse=True)[:5]
        for ep in sorted_eps:
            text += f"• {ep['title']}: {ep['views']} مشاهدة\n"
        await query.edit_message_text(text, parse_mode="Markdown")

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
        await main_menu(user_id, context, message=update.message, edit=False)

    elif state == 'waiting_task_type':
        context.user_data['admin_state'] = 'waiting_task_target'
        await update.message.reply_text("أرسل معرف الحساب (مثال: username):")

    elif state == 'waiting_task_target':
        context.user_data['task_target'] = text
        context.user_data['admin_state'] = 'waiting_task_desc'
        await update.message.reply_text("أرسل وصف المهمة:")

    elif state == 'waiting_task_desc':
        desc = text
        target = context.user_data['task_target']
        add_task('channel', target, desc)
        await update.message.reply_text(f"✅ تم إضافة المهمة '{desc}' بنجاح!")
        context.user_data['admin_state'] = None
        await main_menu(user_id, context, message=update.message, edit=False)

def main():
    # إضافة مهمة افتراضية إذا لم توجد مهام
    if tasks_col.count_documents({}) == 0:
        logging.info("📌 إضافة مهمة افتراضية (الاشتراك في القناة)")
        add_task('channel', CHANNEL_ID, f"الاشتراك في القناة", priority=1)
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    logging.info("🚀 البوت يعمل...")
    app.run_polling()

if __name__ == '__main__':
    main()

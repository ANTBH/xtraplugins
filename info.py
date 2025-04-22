import os
import re # استيراد مكتبة التعبيرات النمطية

from pyrogram import Client, enums, filters
from pyrogram.types import Message
from pyrogram.errors import PeerIdInvalid, UsernameNotOccupied, UserIsBlocked, ChatAdminRequired # استيراد بعض الأخطاء الشائعة للتعامل معها

# افترض أن 'app' هو الكائن الرئيسي للبوت الخاص بك (Client)
# تأكد من أن هذه الاستيرادات صحيحة لمشروعك
from YukkiMusic import app # اسم البوت أو المكتبة الرئيسية
from YukkiMusic.misc import SUDOERS # إذا كنت لا تزال بحاجة إليها لأغراض أخرى
from YukkiMusic.utils.database import is_gbanned_user # إذا كنت لا تزال بحاجة إليها لأغراض أخرى

# --- تعريف المالك ---
OWNER_ID = 6504095190

# --- الدوال المساعدة للتنسيق (كما هي) ---
n = "\n"
w = " "

def bold(x):
    return f"**{x}:** "

def bold_ul(x):
    return f"**--{x}:**-- "

def mono(x):
    return f"`{x}`{n}"

def section(
    title: str,
    body: dict,
    indent: int = 2,
    underline: bool = False,
) -> str:
    text = (bold_ul(title) + n) if underline else bold(title) + n
    for key, value in body.items():
        if value is not None:
            # تعديل بسيط للتعامل مع القوائم الفارغة أو None بشكل أفضل
            item_text = "غير متوفر" # قيمة افتراضية
            if isinstance(value, list) and value and isinstance(value[0], str):
                 item_text = value[0] + n
            elif not isinstance(value, list):
                 item_text = mono(value)

            text += indent * w + bold(key) + item_text
    return text

# --- دوال جلب المعلومات (معدلة قليلاً لتلقي الكائن مباشرة) ---

async def userstatus(user_id):
    # دالة للحصول على حالة المستخدم (متصل، غير متصل، ...)
    # هذه الدالة تتطلب استدعاء get_users لذا سنبقيها منفصلة
    try:
        user = await app.get_users(user_id)
        x = user.status
        if x == enums.UserStatus.RECENTLY:
            return "متصل مؤخرًا."
        elif x == enums.UserStatus.LAST_WEEK:
            return "متصل الأسبوع الماضي."
        elif x == enums.UserStatus.LONG_AGO:
            return "متصل منذ فترة طويلة."
        elif x == enums.UserStatus.OFFLINE:
            return "غير متصل."
        elif x == enums.UserStatus.ONLINE:
            return "متصل."
    except IndexError:
         return "غير متصل." # قد يحدث إذا لم يتم العثور على الحالة
    except Exception:
        # لا نرجع رسالة خطأ هنا، قد يكون الحساب محذوفًا أو لا يمكن الوصول إليه
        return "لا يمكن تحديد الحالة."

async def get_user_info_formatted(user):
    # دالة لتنسيق معلومات المستخدم من كائن المستخدم
    if not user or not user.first_name:
        return ["حساب محذوف", None]

    user_id = user.id
    online_status = await userstatus(user_id) # الحصول على الحالة بشكل منفصل
    username = user.username
    first_name = user.first_name
    mention = user.mention("رابط")
    dc_id = user.dc_id
    photo_id = user.photo.big_file_id if user.photo else None
    # is_gbanned = await is_gbanned_user(user_id) # يمكن تفعيلها إذا لزم الأمر
    # is_sudo = user_id in SUDOERS # يمكن تفعيلها إذا لزم الأمر
    is_premium = user.is_premium
    is_bot = user.is_bot
    is_scam = user.is_scam
    is_fake = user.is_fake
    is_support = user.is_support # هل هو حساب دعم تليجرام
    language_code = user.language_code

    body = {
        "الاسم": [first_name],
        "اسم المستخدم": [("@" + username) if username else "لا يوجد"],
        "المعرف (ID)": user_id,
        "روبوت (Bot)": is_bot,
        "حساب دعم": is_support,
        "حساب احتيال (Scam)": is_scam,
        "حساب مزيف (Fake)": is_fake,
        "العضوية المميزة (Premium)": is_premium,
        "معرف مركز البيانات (DC ID)": dc_id,
        "لغة المستخدم": language_code if language_code else "غير محددة",
        "الإشارة (Mention)": [mention],
        "آخر ظهور": online_status,
        # "محظور عالميًا": is_gbanned, # يمكن إضافتها
        # "من المطورين": is_sudo, # يمكن إضافتها
    }
    caption = section("👤 معلومات المستخدم", body)
    return [caption, photo_id]


async def get_chat_info_formatted(chat):
    # دالة لتنسيق معلومات المحادثة من كائن المحادثة
    username = chat.username
    link = f"https://t.me/{username}" if username else "لا يوجد"
    photo_id = chat.photo.big_file_id if chat.photo else None
    chat_type = "غير معروف"
    if chat.type == enums.ChatType.PRIVATE:
        chat_type = "خاص" # لن يتم استدعاؤها لهذا النوع هنا عادةً
    elif chat.type == enums.ChatType.GROUP:
        chat_type = "مجموعة أساسية"
    elif chat.type == enums.ChatType.SUPERGROUP:
        chat_type = "مجموعة خارقة"
    elif chat.type == enums.ChatType.CHANNEL:
        chat_type = "قناة"

    title = "المحادثة" # عنوان افتراضي
    if chat.type == enums.ChatType.CHANNEL:
        title = "القناة"
    elif chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        title = "المجموعة"


    info = f"""
❅─────✧❅✦❅✧─────❅
          ✦ معلومات {title} ✦

➻ الاسم ‣ {chat.title}
➻ المعرف (ID) ‣ `{chat.id}`
➻ النوع ‣ {chat_type}
➻ اسم المستخدم ‣ @{username if username else "لا يوجد"}
➻ معرف مركز البيانات (DC ID) ‣ {chat.dc_id}
➻ الوصف ‣ {chat.description if chat.description else "لا يوجد"}
➻ موثقة ‣ {chat.is_verified}
➻ مقيدة ‣ {chat.is_restricted}
➻ احتيال (Scam) ‣ {chat.is_scam}
➻ مزيفة (Fake) ‣ {chat.is_fake}
➻ عدد الأعضاء/المشتركين ‣ {chat.members_count if chat.members_count else 'غير متاح'}
➻ الرابط ‣ {link}

❅─────✧❅✦❅✧─────❅"""

    return info, photo_id


# --- المعالج الجديد للأمر 'تحليل' ---
@app.on_message(
    filters.text &
    filters.user(OWNER_ID) & # فلتر المالك
    filters.regex(r"^\s*تحليل(?:\s+|$)", flags=re.IGNORECASE) # يبدأ بـ "تحليل" (مع تجاهل حالة الأحرف) متبوعًا بمسافة أو نهاية السطر
)
async def analyze_command_handler(client: Client, message: Message):
    """Handles the 'تحليل' command for the owner."""
    target_entity = None
    target_input = None
    command_parts = message.text.split(None, 1)

    # 1. تحديد الهدف
    if message.reply_to_message:
        # الأولوية للرد
        if message.reply_to_message.from_user:
            # الرد على رسالة مستخدم
            target_entity = message.reply_to_message.from_user.id
        elif message.reply_to_message.sender_chat:
            # الرد على رسالة من قناة أو مجموعة ترسل باسمها
            target_entity = message.reply_to_message.sender_chat.id
        elif message.reply_to_message.forward_from_chat:
            # الرد على رسالة محولة من قناة/مجموعة
            target_entity = message.reply_to_message.forward_from_chat.id
        elif message.reply_to_message.forward_from:
            # الرد على رسالة محولة من مستخدم
            target_entity = message.reply_to_message.forward_from.id
        # يمكنك إضافة المزيد من الحالات إذا لزم الأمر (مثل الرد على رسالة الخدمة)

    elif len(command_parts) > 1:
        # إذا لم يكن هناك رد، تحقق من وجود وسيطة (معرف أو اسم مستخدم)
        target_input = command_parts[1].strip()
        if target_input.startswith("@"):
            target_entity = target_input # اسم مستخدم
        elif target_input.isdigit() or (target_input.startswith("-") and target_input[1:].isdigit()):
            target_entity = int(target_input) # معرف رقمي
        else:
            # قد يكون رابطًا أو اسم مستخدم بدون @ (أقل موثوقية)
            # Pyrogram يمكنه التعامل مع بعض الروابط في get_chat
            target_entity = target_input

    else:
        # إذا كان الأمر "تحليل" فقط في مجموعة/قناة، قم بتحليل تلك المحادثة
        if message.chat.type != enums.ChatType.PRIVATE:
            target_entity = message.chat.id
        else:
            # في الخاص، يجب تحديد هدف
            await message.reply_text(
                "⚠️ يرجى استخدام الأمر `تحليل` كالتالي:\n"
                "- بالرد على رسالة مستخدم/قناة/مجموعة.\n"
                "- بكتابة `تحليل @username` أو `تحليل ID`."
            )
            return

    if not target_entity:
        await message.reply_text("❌ لم أتمكن من تحديد الهدف للتحليل.")
        return

    # 2. محاولة جلب المعلومات وعرضها
    m = await message.reply_text("⏳ جاري التحليل...")
    photo_id = None
    info_caption = None

    try:
        # استخدم get_chat لأنه يعمل مع المستخدمين، المجموعات، والقنوات
        chat = await app.get_chat(target_entity)

        if chat.type == enums.ChatType.PRIVATE:
            # إذا كان الهدف مستخدمًا خاصًا
            # نحتاج لاستدعاء get_users للحصول على معلومات كاملة مثل الحالة
            user = await app.get_users(chat.id)
            info_caption, photo_id = await get_user_info_formatted(user)
        elif chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP, enums.ChatType.CHANNEL]:
            # إذا كان الهدف مجموعة أو قناة
            info_caption, photo_id = await get_chat_info_formatted(chat)
        else:
            await m.edit(f"🧐 نوع غير معروف أو غير مدعوم: {chat.type}")
            return

    # التعامل مع الأخطاء الشائعة
    except PeerIdInvalid:
        await m.edit("❌ المعرف (ID) المحدد غير صالح أو لم يتم العثور عليه.")
        return
    except UsernameNotOccupied:
        await m.edit(f"❌ اسم المستخدم المحدد `{target_input}` غير موجود.")
        return
    except UserIsBlocked:
         await m.edit("❌ لقد قام هذا المستخدم بحظرك، لا يمكن الحصول على معلوماته.")
         return
    except ChatAdminRequired:
         await m.edit("❌ ليس لدي الصلاحيات الكافية للوصول إلى معلومات هذه المحادثة.")
         return
    except Exception as e:
        # التعامل مع أي أخطاء أخرى غير متوقعة
        print(f"Error during analysis: {e}") # طباعة الخطأ في الطرفية للمطور
        await m.edit(f"❌ حدث خطأ غير متوقع أثناء محاولة التحليل.\n`{e}`")
        return

    # 3. إرسال الرد (صورة + نص أو نص فقط)
    if info_caption:
        if photo_id:
            try:
                # محاولة تحميل وإرسال الصورة
                photo = await app.download_media(photo_id, file_name=f"analysis_{target_entity}.jpg")
                await message.reply_photo(
                    photo=photo,
                    caption=info_caption,
                    quote=False # الرد بدون اقتباس للرسالة الأصلية
                )
                await m.delete() # حذف رسالة "جاري التحليل"
                if os.path.exists(photo):
                    os.remove(photo) # حذف الصورة المؤقتة
            except Exception as photo_err:
                # إذا فشل إرسال الصورة، أرسل النص فقط
                print(f"Photo send/download failed: {photo_err}")
                await m.edit(info_caption, disable_web_page_preview=True)
        else:
            # لا توجد صورة، أرسل النص فقط
            await m.edit(info_caption, disable_web_page_preview=True)
    else:
        # في حالة عدم وجود معلومات لسبب ما (نادر)
        await m.edit("لم يتم العثور على معلومات لهذا الهدف.")


# --- تحديث قسم المساعدة ---
__MODULE__ = "تحليل" # اسم الوحدة بالعربية
__HELP__ = f"""
**أداة التحليل (خاص بالمالك - {OWNER_ID}):**

الأمر: `تحليل`

**الاستخدام:**
• اكتب `تحليل` في أي مجموعة أو قناة لتحليلها.
• قم بالرد على رسالة أي مستخدم أو قناة أو مجموعة بالأمر `تحليل` للحصول على معلوماتها.
• اكتب `تحليل @username` أو `تحليل ID` للحصول على معلومات المستخدم أو المحادثة المحددة.

يقوم البوت تلقائيًا باكتشاف نوع الهدف (مستخدم، مجموعة، قناة) وعرض التفاصيل المتاحة.
"""

# --- إزالة المعالجات القديمة (إذا كانت موجودة في نفس الملف) ---
# @app.on_message(filters.command("info")) ...  <- قم بحذف أو تعطيل هذا
# @app.on_message(filters.command("chatinfo")) ... <- قم بحذف أو تعطيل هذا

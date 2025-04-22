import logging
import asyncio
import traceback
import math
import re # <-- لاستخدام Regex
from typing import Dict, Tuple, Optional # <-- لإدارة المهام النشطة

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, MessageDeleteForbidden, UserNotParticipant
from pyrogram.types import Message
from pyrogram.enums import ChatMemberStatus # <-- لاستخدام حالة العضو
import pytimeparse

# --- استيراد تطبيق البوت الرئيسي ---
# Import the main bot app instance
try:
    from YukkiMusic import app
except ImportError:
    raise ImportError("لا يمكن استيراد 'app' من 'YukkiMusic'. تأكد من صحة المسار وهيكلة المشروع.")

# --- تهيئة مسجل خاص بهذه الوحدة ---
# Initialize a logger specific to this module
logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG) # يمكنك تفعيل هذا مؤقتًا لرؤية المزيد من التفاصيل

# --- قاموس لتتبع المؤقتات النشطة، رسائل التنبيه، وحالة المشرف ---
# Dictionary to track active timers, warning messages, and admin status
# key: chat_id, value: (asyncio.Task, warning_message_id | None, started_by_admin: bool)
active_timers: Dict[int, Tuple[asyncio.Task, Optional[int], bool]] = {}


# --- دالة مساعد للتحقق مما إذا كان المستخدم مشرفًا ---
# Helper function to check if a user is an admin
async def is_admin(client: Client, chat_id: int, user_id: int) -> bool:
    """Checks if a user is an admin or owner in the chat."""
    if not chat_id or not user_id:
        return False
    try:
        member = await client.get_chat_member(chat_id, user_id)
        # اعتبر المالك والمشرفين مشرفين لهذا الغرض
        # Consider Owner and Administrators as admins for this purpose
        return member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]
    except UserNotParticipant:
        logger.debug(f"User {user_id} is not a participant in chat {chat_id}.")
        return False
    except Exception as e:
        logger.error(f"Error checking admin status for user {user_id} in chat {chat_id}: {e}")
        return False # افترض أنه ليس مشرفًا في حالة حدوث خطأ

# --- دالة مساعد لرسم شريط التقدم ---
# Helper function to render a progress bar
def render_progressbar(
    total, iteration, prefix="", suffix="", length=30, fill="█", zfill="░"
):
    """Generates a text-based progress bar string."""
    iteration = min(total, iteration)
    if total == 0:
        percent = "0.0"
        filled_length = 0
    else:
        percent = "{0:.1f}".format(100 * (iteration / float(total)))
        filled_length = int(length * iteration // total)
    pbar = fill * filled_length + zfill * (length - filled_length)
    return "{0} |{1}| {2}% {3}".format(prefix, pbar, percent, suffix)

# --- دالة مساعد لتحويل الثواني إلى دقائق وثواني كنص удобочитаемый ---
# Helper function to format seconds into a readable minutes and seconds string
def format_seconds_to_readable_time(total_seconds: int) -> str:
    """Converts seconds to a human-readable string (e.g., '5 دقائق', '2 دقيقتان و 30 ثانية')."""
    if total_seconds < 0:
        return "مدة غير صالحة"
    if total_seconds == 0:
        return "0 ثانية"

    minutes = total_seconds // 60
    seconds = total_seconds % 60

    parts = []
    if minutes == 1:
        parts.append("1 دقيقة")
    elif minutes == 2:
         parts.append("2 دقيقتان") # مثنى
    elif minutes > 2:
        parts.append(f"{minutes} دقائق")

    if seconds == 1:
         parts.append("1 ثانية")
    elif seconds > 1:
         parts.append(f"{seconds} ثانية") # استخدم "ثوانٍ" إذا أردت التنوين

    if not parts:
        return f"{total_seconds:.2f} ثانية"

    return " و ".join(parts)


# --- دالة غير متزامنة لتشغيل العد التنازلي وتحديث الرسالة ---
# Asynchronous function to run the countdown and update the message
async def run_countdown(client: Client, chat_id: int, message_id: int, total_seconds: int):
    """
    Manages the countdown timer, updating the message periodically with
    minutes/seconds display, sending/deleting a warning, and handling cancellation.
    """
    seconds_left = total_seconds
    last_update_time = asyncio.get_event_loop().time()
    timer_id = f"{chat_id}_{message_id}" # معرف فريد للمؤقت (للتسجيل)
    warning_sent = False
    warning_message_id: int | None = None # لتخزين ID رسالة التنبيه
    logger.info(f"بدء العد التنازلي {timer_id} لمدة {total_seconds} ثانية.")

    try:
        while seconds_left > 0:

            # --- إرسال تنبيه عند 30 ثانية ---
            if seconds_left <= 30 and not warning_sent:
                try:
                    sent_warning_msg = await client.send_message(
                        chat_id=chat_id,
                        text="⚠️ **تنبيه:** تبقى 30 ثانية أو أقل على انتهاء المؤقت!",
                        reply_to_message_id=message_id
                    )
                    warning_message_id = sent_warning_msg.id # تخزين ID الرسالة
                    # تحديث القاموس بمعرف رسالة التنبيه
                    if chat_id in active_timers:
                         task, _, started_by_admin = active_timers[chat_id] # تجاهل معرف التنبيه القديم
                         active_timers[chat_id] = (task, warning_message_id, started_by_admin) # تحديث بمعرف التنبيه الجديد

                    warning_sent = True
                    logger.info(f"تم إرسال تنبيه الـ 30 ثانية ({warning_message_id}) للمؤقت {timer_id}.")
                except Exception as warn_err:
                    logger.error(f"فشل إرسال تنبيه الـ 30 ثانية للمؤقت {timer_id}: {warn_err}")
            # ------------------------------------

            # حساب التقدم وعرض الوقت المتبقي بالدقائق والثواني
            seconds_elapsed = total_seconds - seconds_left
            progress_bar = render_progressbar(total_seconds, seconds_elapsed)
            readable_time_left = format_seconds_to_readable_time(seconds_left)
            message_text = f"⏳ الوقت المتبقي: **{readable_time_left}**\n{progress_bar}"

            current_time = asyncio.get_event_loop().time()
            update_interval = max(1.0, min(5.0, total_seconds / 100.0))

            if current_time - last_update_time >= update_interval:
                try:
                    await client.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=message_text
                    )
                    last_update_time = current_time
                except FloodWait as e:
                    logger.warning(f"FloodWait للمؤقت {timer_id}: الانتظار لمدة {e.value} ثانية.")
                    await asyncio.sleep(e.value + 0.5)
                except Exception as e:
                    logger.error(f"خطأ أثناء تحديث المؤقت {timer_id}: {e}")
                    if "MESSAGE_ID_INVALID" in str(e) or "MESSAGE_NOT_MODIFIED" in str(e):
                        logger.warning(f"إيقاف المؤقت {timer_id} بسبب خطأ في تعديل الرسالة.")
                        return # الخروج من الدالة سيؤدي إلى تنفيذ finally
            # إنقاص الوقت والانتظار
            seconds_left -= 1
            await asyncio.sleep(1)

        # --- العد التنازلي انتهى ---
        final_message = "✅ الوقت انتهى!"
        progress_bar = render_progressbar(total_seconds, total_seconds)
        final_message += f"\n{progress_bar}"
        try:
            await client.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=final_message
            )
            logger.info(f"اكتمل المؤقت {timer_id}.")
        except Exception as e:
            logger.error(f"خطأ أثناء تحديث الرسالة النهائية للمؤقت {timer_id}: {e}")

    except asyncio.CancelledError:
        logger.info(f"تم إلغاء العد التنازلي للمؤقت {timer_id}")
        try:
             await client.edit_message_text(chat_id, message_id, "🚫 تم إيقاف المؤقت.")
        except Exception as edit_err:
             logger.error(f"فشل تعديل رسالة الإيقاف للمؤقت {timer_id}: {edit_err}")
    except Exception as e:
        logger.error(f"خطأ غير متوقع في run_countdown للمؤقت {timer_id}: {e}")
        traceback.print_exc()
        try:
            await client.send_message(chat_id, "حدث خطأ أثناء تشغيل المؤقت.")
        except Exception as send_err:
            logger.error(f"فشل إرسال رسالة الخطأ إلى {chat_id}: {send_err}")
    finally:
        # --- التنظيف: إزالة المؤقت من القائمة وحذف رسالة التنبيه ---
        logger.debug(f"Entering finally block for timer {timer_id}")
        warn_msg_id_to_delete = None
        if chat_id in active_timers:
            # استرجع معرف رسالة التنبيه قبل الحذف
            _, warn_msg_id_to_delete, _ = active_timers.pop(chat_id)
            logger.info(f"تمت إزالة المؤقت {timer_id} من القائمة النشطة.")
        else:
             logger.warning(f"المؤقت {timer_id} لم يكن في القائمة النشطة عند محاولة الإزالة.")

        # حذف رسالة التنبيه إن وجدت
        if warn_msg_id_to_delete:
            logger.debug(f"محاولة حذف رسالة التنبيه {warn_msg_id_to_delete} للمؤقت {timer_id}")
            try:
                await client.delete_messages(chat_id=chat_id, message_ids=warn_msg_id_to_delete)
                logger.info(f"تم حذف رسالة التنبيه {warn_msg_id_to_delete} للمؤقت {timer_id}.")
            except MessageDeleteForbidden:
                 logger.warning(f"ليس لدي صلاحية حذف رسالة التنبيه {warn_msg_id_to_delete} في الدردشة {chat_id}.")
            except Exception as del_err:
                logger.error(f"فشل حذف رسالة التنبيه {warn_msg_id_to_delete}: {del_err}")
        else:
            logger.debug(f"لا يوجد معرف لرسالة التنبيه لحذفه للمؤقت {timer_id}")
        # ---------------------------------------------------------


# --- دالة مساعدة لبدء منطق المؤقت ---
# Helper function to start the timer logic
async def start_timer_logic(client: Client, message: Message, time_input: str):
    """
    Parses time, validates permissions, stops existing timer if allowed,
    sends confirmation, and starts countdown task.
    """
    chat_id = message.chat.id
    user_id = message.from_user.id
    seconds = None
    original_input_for_confirmation = time_input

    # --- التحقق من صلاحيات المستخدم ---
    # --- Check user permissions ---
    current_user_is_admin = await is_admin(client, chat_id, user_id)
    logger.debug(f"User {user_id} admin status in chat {chat_id}: {current_user_is_admin}")

    # --- التحقق من المؤقت القديم وصلاحيات الاستبدال ---
    # --- Check old timer and replacement permissions ---
    if chat_id in active_timers:
        old_task, old_warning_id, old_timer_is_admin_started = active_timers[chat_id]
        logger.info(f"العثور على مؤقت نشط في الدردشة {chat_id} (بدأه مشرف: {old_timer_is_admin_started}).")

        # المنع إذا كان المستخدم الحالي ليس مشرفًا والمؤقت القديم بدأه مشرف
        if not current_user_is_admin and old_timer_is_admin_started:
            await message.reply_text("🚫 لا يمكنك استبدال مؤقت نشط بدأه أحد المشرفين.")
            logger.info(f"User {user_id} (non-admin) blocked from replacing admin timer in chat {chat_id}.")
            return

        # إذا كان المستخدم مشرفًا أو المؤقت القديم لم يبدأه مشرف، قم بالإلغاء
        logger.info(f"User {user_id} allowed to replace timer in chat {chat_id}. Cancelling old timer.")
        try:
            if not old_task.done():
                 old_task.cancel()
        except Exception as cancel_err:
             logger.error(f"خطأ أثناء محاولة إلغاء المؤقت القديم في الدردشة {chat_id}: {cancel_err}")
        # لا تقم بالإزالة من active_timers هنا، سيتم ذلك في finally الخاص بالمهمة القديمة
    # ---------------------------------------------

    # التحقق مما إذا كان الإدخال رقمًا فقط (يعني دقائق)
    if time_input.isdigit():
        try:
            minutes = int(time_input)
            seconds = minutes * 60
            logger.debug(f"Input '{time_input}' interpreted as {minutes} minutes ({seconds} seconds).")
            original_input_for_confirmation = format_seconds_to_readable_time(seconds)
        except ValueError:
            logger.warning(f"Failed to convert digit input '{time_input}' to int.")
            seconds = None

    # إذا لم يكن رقمًا، استخدم pytimeparse
    if seconds is None:
        seconds = pytimeparse.parse(time_input)
        if seconds is not None:
             logger.debug(f"Input '{time_input}' parsed by pytimeparse to {seconds} seconds.")
             original_input_for_confirmation = format_seconds_to_readable_time(seconds)

    # التحقق من الصحة
    if seconds is None:
        await message.reply_text(
            f"لم أستطع فهم الوقت: '{time_input}'.\n"
            "يرجى استخدام تنسيق مثل '10s', '5m', '1h30m' أو رقم للدقائق (مثل '5')."
        )
        return

    if seconds <= 0:
        await message.reply_text("يرجى تقديم مدة زمنية موجبة.")
        return

    max_duration = 24 * 60 * 60 # 24 hours
    if seconds > max_duration:
        await message.reply_text(f"أقصى مدة للمؤقت هي 24 ساعة.")
        return

    # --- رسالة التأكيد بالتنسيق الجديد ---
    confirmation_message_text = f"⏳ تم بدء مؤقت لمدة **{original_input_for_confirmation}**..."
    try:
        sent_message = await message.reply_text(confirmation_message_text, quote=True)
        message_id = sent_message.id
    except Exception as e:
        logger.error(f"فشل في إرسال رسالة التأكيد إلى {chat_id}: {e}")
        return

    # بدء العد التنازلي وتخزين المهمة وحالة المشرف
    task = asyncio.create_task(run_countdown(client, chat_id, message_id, seconds))
    # تخزين المهمة ومعرف التنبيه (None مبدئيًا) وحالة المشرف
    active_timers[chat_id] = (task, None, current_user_is_admin)
    logger.info(f"تم تخزين المؤقت الجديد (بدأه مشرف: {current_user_is_admin}) للدردشة {chat_id} في القائمة النشطة.")


# --- دالة مساعدة لمنطق إيقاف المؤقت ---
# Helper function for stop timer logic
async def stop_timer_logic(client: Client, message: Message, chat_id: int, user_id: int):
    """Handles the logic for stopping a timer with permission checks."""
    logger.debug(f"stop_timer_logic called by user {user_id} in chat {chat_id}")

    if chat_id in active_timers:
        task, warning_id, timer_is_admin_started = active_timers[chat_id]
        logger.info(f"Found active timer in chat {chat_id} to stop (admin started: {timer_is_admin_started}).")

        # التحقق من صلاحية الإيقاف
        user_is_admin = await is_admin(client, chat_id, user_id)
        logger.debug(f"User {user_id} admin status for stopping in chat {chat_id}: {user_is_admin}")

        # المنع إذا كان المستخدم ليس مشرفًا والمؤقت بدأه مشرف
        if not user_is_admin and timer_is_admin_started:
            await message.reply_text("🚫 لا يمكنك إيقاف مؤقت نشط بدأه أحد المشرفين.")
            logger.info(f"User {user_id} (non-admin) blocked from stopping admin timer in chat {chat_id}.")
            return

        # السماح بالإيقاف (إما المستخدم مشرف أو المؤقت لم يبدأه مشرف)
        if not task.done():
            try:
                task.cancel()
                # الرسالة سيتم إرسالها بواسطة run_countdown عند الإلغاء
                logger.info(f"Cancel request sent for timer in chat {chat_id} by user {user_id}.")
                # يمكن إضافة رد هنا إذا أردت تأكيدًا فوريًا
                # await message.reply_text("✅ تم إرسال طلب إيقاف المؤقت.")
            except Exception as e:
                await message.reply_text("❌ حدث خطأ أثناء محاولة إيقاف المؤقت.")
                logger.error(f"Error cancelling timer in chat {chat_id}: {e}")
        else:
            await message.reply_text("⚠️ يبدو أن المؤقت قد انتهى بالفعل.")
            logger.warning(f"Attempted to stop an already finished timer in chat {chat_id}.")
            active_timers.pop(chat_id, None) # إزالته احتياطًا
    else:
        await message.reply_text("⚠️ لا يوجد مؤقت نشط لإيقافه في هذه الدردشة.")


# --- معالج أمر إيقاف المؤقت (مع /) ---
# Handler for the stop timer command (with /)
# !! يعمل في المجموعات فقط !!
@app.on_message(filters.command(["stop_timer", "الغاء_المؤقت", "stop"]) & filters.group)
async def stop_timer_command_handler(client: Client, message: Message):
    """Handles the /stop_timer command in groups."""
    logger.debug(f"Handler triggered: stop_timer_command_handler by user {message.from_user.id} in chat {message.chat.id}")
    await stop_timer_logic(client, message, message.chat.id, message.from_user.id)


# --- معالج أمر إيقاف المؤقت (بدون / باستخدام Regex) ---
# Handler for the stop timer command (without / using Regex)
# !! يعمل في المجموعات فقط !!
@app.on_message(filters.regex(r"^الغاء المؤقت$", re.IGNORECASE) & filters.group)
async def stop_timer_regex_handler(client: Client, message: Message):
    """Handles 'الغاء المؤقت' command in groups."""
    logger.debug(f"Handler triggered: stop_timer_regex_handler by user {message.from_user.id} in chat {message.chat.id}")
    # التأكد أنها ليست رسالة تبدأ بـ / (احتياطي)
    if message.text and not message.text.startswith('/'):
         await stop_timer_logic(client, message, message.chat.id, message.from_user.id)


# --- معالج أمر المؤقت (مع /) ---
# Handler for the timer command (with /)
# !! يعمل في المجموعات فقط !!
@app.on_message(filters.command(["timer", "مؤقت", "عداد", "countdown"]) & filters.group)
async def timer_command_handler(client: Client, message: Message):
    """Handles the /timer command in groups."""
    logger.debug(f"Handler triggered: timer_command_handler by user {message.from_user.id} in chat {message.chat.id}")
    if len(message.command) > 1:
        user_input = " ".join(message.command[1:])
        await start_timer_logic(client, message, user_input.strip())
    else:
        await message.reply_text(
            "يرجى تحديد مدة زمنية بعد الأمر.\n"
            "مثال: `/timer 5m` أو `/مؤقت 10` (يعني 10 دقائق)"
        )

# --- معالج أمر المؤقت (بدون / باستخدام Regex) ---
# Handler for the timer command (without / using Regex)
# !! يعمل في المجموعات فقط !!
@app.on_message(filters.regex(r"^(موقت|مؤقت|عداد)\s+(.+)", re.IGNORECASE) & filters.group)
async def timer_regex_handler(client: Client, message: Message):
    """Handles timer commands without '/' using regex in groups."""
    logger.debug(f"Handler triggered: timer_regex_handler by user {message.from_user.id} in chat {message.chat.id}")
    if message.text and not message.text.startswith('/'):
        if message.matches:
            time_input = message.matches[0].group(2).strip()
            await start_timer_logic(client, message, time_input)
    else:
         logger.debug("Ignoring regex match because message starts with /")


# --- معلومات المساعدة للوحدة (محدثة بالكامل لصلاحيات المشرفين) ---
# Help information for the module (fully updated for admin permissions)
__MODULE__ = "Tɪᴍᴇʀ"
__HELP__ = """
**⏱️ مؤقت العد التنازلي (للمجموعات فقط)**

يبدأ مؤقتًا ويحدث رسالة بالوقت المتبقي (دقائق/ثواني).
يتم إرسال وحذف تنبيه عند آخر 30 ثانية.

**صلاحيات المشرفين:**
- يعمل هذا الأمر **فقط في المجموعات**.
- إذا بدأ **مشرف** مؤقتًا، فلا يمكن **لعضو عادي** إيقافه أو استبداله.
- يمكن **للمشرفين** إيقاف أو استبدال أي مؤقت (حتى لو بدأه مشرف آخر).
- بدء مؤقت جديد يلغي أي مؤقت قديم في المجموعة (إذا كانت لديك الصلاحية).

**كيفية تحديد المدة:**
1.  **رقم فقط:** يعتبر عدد **الدقائق**. (مثال: `5`)
2.  **تنسيق الوقت:** 's' ثواني، 'm' دقائق، 'h' ساعات. (مثال: `10s`, `5m`, `1h30m`)

**أوامر البدء (مع /):**
- `/timer [مدة]`
- `/مؤقت [مدة]`
- `/عداد [مدة]`
- `/countdown [مدة]`

**أوامر البدء (بدون /):**
- `موقت [مدة]`
- `مؤقت [مدة]`
- `عداد [مدة]`

**أوامر الإيقاف:**
- `/stop_timer`
- `/الغاء_المؤقت`
- `/stop`
- `الغاء المؤقت` (بدون /)

**أمثلة:**
`موقت 10` (يبدأ مؤقت 10 دقائق)
`/timer 1m30s` (يبدأ مؤقت دقيقة و 30 ثانية)
`الغاء المؤقت` (يوقف المؤقت الحالي إذا كان لديك الصلاحية)
"""

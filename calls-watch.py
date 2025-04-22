import asyncio
import sqlite3
import traceback # لاستيراد traceback لطباعة الأخطاء التفصيلية
import re # !!! تم إضافة استيراد re !!!
from datetime import datetime, timedelta, timezone, date
from dateutil.relativedelta import relativedelta
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.enums import ChatMembersFilter, ChatMemberStatus, ParseMode # استيراد ParseMode
from pyrogram.errors import (
    UserAlreadyParticipant, UserNotParticipant, ChatAdminRequired,
    ChannelPrivate, UserNotParticipant as PyrogramUserNotParticipant,
    AuthKeyUnregistered, UserDeactivated, UserDeactivatedBan, SessionPasswordNeeded
)
from pyrogram.errors import RPCError

# --- استيراد مكونات YukkiMusic ---
from YukkiMusic import app # استيراد العميل الرئيسي للبوت

# --- استيراد المجدول ---
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# --- الإعدادات ---
TARGET_CHAT_ID = -1002215457580
REPORT_CHAT_ID = -1009876543210
ASSISTANT_API_ID = 20966394
ASSISTANT_API_HASH = "39bf1ab736102a2b123aaa474c4af3c0"
ASSISTANT_SESSION_STRING = "AgE_6_oAaLFkW2tRvVcoKky-jGXKwYOe1I64N7NLbndpcORK23rYywCk-L3crcFedjNkHctuwe2BUgK0aEIWK6hE3Zz0iqXHWgaVaEvhlHc8nsH4W2NX7IasYglOMuikVu90CsMvuOiOEfiAgNUlOt3oqxg95NNt8ZelGyFQYRNWQXSgdQtM5qK3zmiQG-pLUXckPqNhaJBSbpYZtRs3_WbyyUjz762PhuvOvkhbNZwifDWXo8tBOKxBrea592afUSOIIFOSMlxRlBmb2Idu1ujXl-Z46sRH5Qe2RXPI7GTV1ymEjzLFxW4MlPgsXmTaWHLyZSuy0atffckgM3HfSdsiLv2whQAAAAHVPWZTAA"
CONTROLLER_ID = 6504095190 # معرف المستخدم الوحيد المسموح له باستخدام الأوامر
EXCLUDED_ADMIN_IDS = set() # إفراغ القائمة لأن المتحكم لا يجب أن يكون مستثنى
DB_FILE = "vc_monitor_data.db" # تأكد من أن هذا المسار يشير لمكان دائم في بيئة التشغيل
REPORT_TIMEZONE_STR = 'UTC'
try:
    import pytz
    REPORT_TIMEZONE = pytz.timezone(REPORT_TIMEZONE_STR)
except ImportError:
    print(f"[Monitor Warning] مكتبة pytz غير مثبتة، سيتم استخدام UTC.")
    REPORT_TIMEZONE = timezone.utc
REPORT_HOUR = 23
REPORT_MINUTE = 55
POLLING_INTERVAL_SECONDS = 30

# --- تهيئة العميل المساعد المخصص ---
assistant_client: Client | None = None

# --- تخزين الحالة الحالية (في الذاكرة) ---
admin_status = {}
current_admin_ids = set() # مجموعة تحتوي على IDs المشرفين الذين يتم تتبعهم حالياً

# كائن المجدول
scheduler = AsyncIOScheduler(timezone=REPORT_TIMEZONE_STR)

# --- إعداد قاعدة البيانات ---
db_path = Path(DB_FILE)
conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
cursor = conn.cursor()

def init_db():
    """إنشاء جدول قاعدة البيانات إذا لم يكن موجوداً."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            event_type TEXT NOT NULL, -- 'join', 'leave', 'speak_start', 'speak_stop'
            timestamp TEXT NOT NULL -- ISO 8601 format (YYYY-MM-DD HH:MM:SS.sss+ZZ:ZZ)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity_log (timestamp)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_activity_admin_chat ON activity_log (admin_id, chat_id)')
    conn.commit()
    print(f"[{datetime.now()}] [DB] تم تهيئة قاعدة البيانات '{DB_FILE}' بنجاح.")

# --- دوال قاعدة البيانات ---
def log_event(admin_id: int, chat_id: int, event_type: str, timestamp: datetime):
    """تسجيل حدث في قاعدة البيانات."""
    if admin_id in EXCLUDED_ADMIN_IDS: return
    ts_iso = timestamp.isoformat()
    try:
        with sqlite3.connect(db_path, isolation_level=None) as thread_conn:
             thread_conn.execute(
                 "INSERT INTO activity_log (admin_id, chat_id, event_type, timestamp) VALUES (?, ?, ?, ?)",
                 (admin_id, chat_id, event_type, ts_iso)
             )
    except Exception as e:
        print(f"[{datetime.now()}] [DB Error] فشل تسجيل الحدث: {e}")

def get_events_for_period(start_time: datetime, end_time: datetime, chat_id: int, admin_id: int = None) -> list:
    """جلب الأحداث لفترة زمنية معينة ومحادثة معينة (واختيارياً لمشرف معين)."""
    start_iso = start_time.isoformat()
    end_iso = end_time.isoformat()
    query = "SELECT admin_id, event_type, timestamp FROM activity_log WHERE chat_id = ? AND timestamp >= ? AND timestamp < ? "
    params = [chat_id, start_iso, end_iso]
    if admin_id:
        if admin_id in EXCLUDED_ADMIN_IDS: return []
        query += "AND admin_id = ? "
        params.append(admin_id)
    else:
        if EXCLUDED_ADMIN_IDS:
            excluded_placeholders = ','.join('?' * len(EXCLUDED_ADMIN_IDS))
            query += f"AND admin_id NOT IN ({excluded_placeholders}) "
            params.extend(list(EXCLUDED_ADMIN_IDS))
    query += "ORDER BY timestamp ASC"
    try:
        with sqlite3.connect(db_path) as thread_conn:
            read_cursor = thread_conn.cursor()
            read_cursor.execute(query, params)
            events = [(row[0], row[1], datetime.fromisoformat(row[2])) for row in read_cursor.fetchall()]
            return events
    except Exception as e:
        print(f"[{datetime.now()}] [DB Error] فشل جلب الأحداث: {e}")
        return []

def calculate_durations_from_events(events: list) -> (timedelta, timedelta):
    """حساب مدة التواجد والتحدث من قائمة أحداث مرتبة زمنياً (تتوقع قائمة بـ (event_type, timestamp))."""
    presence_duration = timedelta(0)
    speak_duration = timedelta(0)
    last_join_time = None
    last_speak_start_time = None
    for event_type, timestamp in events:
        if event_type == 'join':
            if last_join_time is None: last_join_time = timestamp
        elif event_type == 'leave':
            if last_join_time is not None:
                # التأكد من أن وقت المغادرة بعد وقت الانضمام
                if timestamp > last_join_time:
                    presence_duration += (timestamp - last_join_time)
                last_join_time = None # إعادة التعيين دائمًا
            if last_speak_start_time is not None:
                # التأكد من أن وقت المغادرة بعد وقت بدء التحدث
                if timestamp > last_speak_start_time:
                    speak_duration += (timestamp - last_speak_start_time)
                last_speak_start_time = None # إعادة التعيين دائمًا
        elif event_type == 'speak_start':
            if last_speak_start_time is None and last_join_time is not None:
                last_speak_start_time = timestamp
        elif event_type == 'speak_stop':
            if last_speak_start_time is not None:
                 # التأكد من أن وقت الإيقاف بعد وقت البدء
                if timestamp > last_speak_start_time:
                    speak_duration += (timestamp - last_speak_start_time)
                last_speak_start_time = None # إعادة التعيين دائمًا
    return presence_duration, speak_duration

# --- دوال مساعدة ---
def format_timedelta(td: timedelta) -> str:
    """تنسيق كائن timedelta إلى سلسلة نصية HH:MM:SS."""
    if not isinstance(td, timedelta): return "00:00:00"
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"

def format_timedelta_arabic(td: timedelta) -> str:
    """تنسيق كائن timedelta إلى سلسلة نصية باللغة العربية."""
    if not isinstance(td, timedelta) or td.total_seconds() < 1: # اعتبار أقل من ثانية كصفر
        return "صفر"

    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []

    if hours == 1: parts.append("ساعة")
    elif hours == 2: parts.append("ساعتان")
    elif hours > 2: parts.append(f"{hours} ساعات")

    if minutes == 1: parts.append("دقيقة")
    elif minutes == 2: parts.append("دقيقتان")
    elif minutes > 2: parts.append(f"{minutes} دقيقة")

    # إضافة الثواني فقط إذا كانت هي الوحدة الوحيدة غير الصفرية
    if hours == 0 and minutes == 0 and seconds > 0:
        if seconds == 1: parts.append("ثانية")
        elif seconds == 2: parts.append("ثانيتان")
        else: parts.append(f"{seconds} ثانية")

    return " و ".join(parts)

async def update_admin_list():
    """تجلب وتحدث قائمة معرفات المشرفين للمحادثة المستهدفة (مع استثناء المحددين)."""
    global current_admin_ids, admin_status
    print(f"[{datetime.now()}] [Monitor] جاري تحديث قائمة المشرفين للمحادثة {TARGET_CHAT_ID}...")
    fetched_admin_ids = set()
    try:
        async for member in app.get_chat_members(TARGET_CHAT_ID, filter=ChatMembersFilter.ADMINISTRATORS):
            if not member.user.is_bot: fetched_admin_ids.add(member.user.id)
        admins_to_track = fetched_admin_ids - EXCLUDED_ADMIN_IDS
        print(f"[Monitor] تم جلب {len(fetched_admin_ids)} مشرف، سيتم تتبع {len(admins_to_track)} مشرف (بعد استثناء {len(EXCLUDED_ADMIN_IDS)}).")
        if admins_to_track:
            try:
                users = await app.get_users(list(admins_to_track))
                for user in users:
                    if user.id not in admin_status:
                         admin_status[user.id] = {'in_call': False, 'speaking': False, 'user_info': user}
                    else:
                         admin_status[user.id]['user_info'] = user
            except Exception as e:
                print(f"[{datetime.now()}] [Monitor] خطأ أثناء جلب معلومات المستخدمين للمشرفين المتعقبين: {e}")
        previous_tracked_ids = set(admin_status.keys())
        ids_to_remove = (previous_tracked_ids - admins_to_track) | (previous_tracked_ids & EXCLUDED_ADMIN_IDS)
        for admin_id in ids_to_remove:
            if admin_id in admin_status:
                del admin_status[admin_id]
                print(f"[Monitor] تمت إزالة المشرف {admin_id} من التتبع النشط.")
        current_admin_ids = admins_to_track
    except (ChannelPrivate, PyrogramUserNotParticipant):
         print(f"[{datetime.now()}] [Monitor] خطأ: البوت الرئيسي ليس عضواً في المجموعة المستهدفة {TARGET_CHAT_ID} أو المجموعة خاصة.")
    except Exception as e:
        print(f"[{datetime.now()}] [Monitor] خطأ أثناء تحديث قائمة المشرفين: {type(e).__name__} {e}")

# --- دالة التحقق الدوري ---
async def check_vc_status():
    """تتحقق دورياً من حالة المشاركين وتسجل الأحداث في قاعدة البيانات."""
    global admin_status
    if not assistant_client or not assistant_client.is_connected:
        return
    try:
        current_participants_map = {}
        all_participant_ids = set()
        async for member in assistant_client.get_call_members(TARGET_CHAT_ID):
            chat_obj = getattr(member, 'chat', None)
            user_id = getattr(chat_obj, 'id', None) if chat_obj else None
            if user_id:
                current_participants_map[user_id] = member
                all_participant_ids.add(user_id)
            # else:
                # print(f"[Monitor Warning] لم يتم العثور على user_id أو chat.id للعضو: {member}")

        now = datetime.now(timezone.utc)
        current_participant_ids = all_participant_ids
        tracked_admin_ids_in_memory = set(admin_status.keys())

        for admin_id in current_admin_ids:
            if admin_id not in admin_status:
                 print(f"[Monitor Warning] المشرف المتعقب {admin_id} غير موجود في admin_status.")
                 continue
            status = admin_status[admin_id]
            member = current_participants_map.get(admin_id)
            is_currently_in_call = status.get('in_call', False)
            is_currently_speaking_status = status.get('speaking', False)

            if member and not is_currently_in_call: # انضم الآن
                status['in_call'] = True
                status['join_time'] = now
                log_event(admin_id, TARGET_CHAT_ID, 'join', now)
                print(f"[Event Logged] Admin {admin_id} JOINED")

            elif not member and is_currently_in_call: # غادر الآن
                status['in_call'] = False
                status.pop('join_time', None)
                status.pop('speak_start_time', None)
                log_event(admin_id, TARGET_CHAT_ID, 'leave', now)
                print(f"[Event Logged] Admin {admin_id} LEFT")
                if is_currently_speaking_status:
                    status['speaking'] = False
                    log_event(admin_id, TARGET_CHAT_ID, 'speak_stop', now)
                    print(f"[Event Logged] Admin {admin_id} SPEAK_STOP (due to leave)")

            if member and is_currently_in_call:
                 is_vc_speaking = getattr(member, 'is_speaking', not getattr(member, 'is_muted', True))
                 if is_vc_speaking and not is_currently_speaking_status: # بدأ التحدث
                     status['speaking'] = True
                     status['speak_start_time'] = now
                     log_event(admin_id, TARGET_CHAT_ID, 'speak_start', now)
                     print(f"[Event Logged] Admin {admin_id} SPEAK_START")
                 elif not is_vc_speaking and is_currently_speaking_status: # توقف عن التحدث
                     status['speaking'] = False
                     status.pop('speak_start_time', None)
                     log_event(admin_id, TARGET_CHAT_ID, 'speak_stop', now)
                     print(f"[Event Logged] Admin {admin_id} SPEAK_STOP")

        admins_in_memory_but_not_in_call = tracked_admin_ids_in_memory - current_participant_ids
        for admin_id in admins_in_memory_but_not_in_call:
             if admin_id in current_admin_ids:
                status = admin_status.get(admin_id)
                if status and status.get('in_call', False):
                    status['in_call'] = False
                    status.pop('join_time', None)
                    status.pop('speak_start_time', None)
                    log_event(admin_id, TARGET_CHAT_ID, 'leave', now)
                    print(f"[Event Logged - Fallback] Admin {admin_id} LEFT")
                    if status.get('speaking', False):
                        status['speaking'] = False
                        log_event(admin_id, TARGET_CHAT_ID, 'speak_stop', now)
                        print(f"[Event Logged - Fallback] Admin {admin_id} SPEAK_STOP (due to leave)")

    except Exception as e:
        error_str = str(e).upper()
        is_call_not_found_error = False
        if "GROUPCALL_NOT_FOUND" in error_str or "GROUPCALL_INVALID" in error_str:
             is_call_not_found_error = True
        elif isinstance(e, RPCError) and e.ID == "PHONE_CALL_NOT_FOUND":
             is_call_not_found_error = True
        elif isinstance(e, asyncio.TimeoutError):
             is_call_not_found_error = True

        if is_call_not_found_error:
            now = datetime.now(timezone.utc)
            active_admins = {uid for uid, data in admin_status.items() if data.get('in_call')}
            if active_admins:
                print(f"[{datetime.now()}] [Monitor] تسجيل مغادرة للمشرفين النشطين بسبب انتهاء المكالمة أو عدم توفرها...")
                for admin_id in active_admins:
                    status = admin_status[admin_id]
                    status['in_call'] = False
                    status.pop('join_time', None)
                    status.pop('speak_start_time', None)
                    log_event(admin_id, TARGET_CHAT_ID, 'leave', now)
                    if status.get('speaking', False):
                        status['speaking'] = False
                        log_event(admin_id, TARGET_CHAT_ID, 'speak_stop', now)
        elif isinstance(e, (AuthKeyUnregistered, UserDeactivated, UserDeactivatedBan)):
             assistant_id = assistant_client.me.id if assistant_client and assistant_client.is_connected and assistant_client.me else "N/A"
             print(f"[{datetime.now()}] [Monitor CRITICAL ERROR] مشكلة في الحساب المساعد ({assistant_id}): {type(e).__name__}. قد تحتاج لإعادة إنشاء الجلسة أو التحقق من الحساب.")
        else:
            print(f"[{datetime.now()}] [Monitor Error] خطأ غير متوقع أثناء التحقق من حالة المكالمة: {type(e).__name__} - {e}")
            traceback.print_exc()

# --- دوال التقارير ---
async def generate_report_text(start_time: datetime, end_time: datetime, chat_id: int, report_title: str) -> str:
    """ينشئ نص التقرير لفترة زمنية معينة (يشمل جميع المشرفين المتعقبين، مرتبين حسب التواجد)."""
    report_lines = [f"📊 <b>{report_title}</b>"]
    report_lines.append(f"   <i>الفترة:</i> {start_time.strftime('%Y-%m-%d %H:%M')} إلى {end_time.strftime('%Y-%m-%d %H:%M')} ({REPORT_TIMEZONE_STR})")
    report_lines.append("-" * 20)

    all_events = get_events_for_period(start_time, end_time, chat_id)
    events_by_admin = {}
    for admin_id_event, event_type, timestamp in all_events:
        if admin_id_event not in events_by_admin: events_by_admin[admin_id_event] = []
        events_by_admin[admin_id_event].append((event_type, timestamp))

    admins_to_report = list(current_admin_ids)
    admin_user_map = {}
    if admins_to_report:
        try:
            admin_users = await app.get_users(admins_to_report)
            admin_user_map = {user.id: user for user in admin_users}
        except Exception as e:
            print(f"[Monitor Report Error] خطأ أثناء جلب معلومات المستخدمين للتقرير: {e}")

    admin_report_data_list = []
    for admin_id in admins_to_report:
        admin_events_tuples = sorted(events_by_admin.get(admin_id, []), key=lambda x: x[1])
        presence_duration, speak_duration = calculate_durations_from_events(admin_events_tuples)

        admin_user = admin_user_map.get(admin_id)
        status = admin_status.get(admin_id)
        if not admin_user and status and status.get('user_info'): admin_user = status.get('user_info')
        if admin_user: admin_name = admin_user.mention or admin_user.first_name or f"المستخدم ({admin_id})"
        else: admin_name = f"المستخدم ({admin_id})"

        admin_report_data_list.append({
            "id": admin_id, # إضافة المعرف للتحقق لاحقاً
            "name": admin_name,
            "presence": presence_duration,
            "speak": speak_duration
        })

    admin_report_data_list.sort(key=lambda x: x['presence'], reverse=True)

    report_data_formatted = []
    for data in admin_report_data_list:
        # --- !!! تم التعديل هنا: إضافة منطق النصوص المخصصة للمدد الصفرية !!! ---
        if data['presence'] <= timedelta(0):
            presence_str = "لم يتواجد"
            speak_str = "لم يتواجد" # إذا لم يتواجد، فلن يتكلم
        else:
            presence_str = format_timedelta_arabic(data['presence'])
            if data['speak'] <= timedelta(0):
                speak_str = "لم يتكلم"
            else:
                speak_str = format_timedelta_arabic(data['speak'])
        # ---------------------------------------------------------------------

        report_data_formatted.append(f"👤 <b>{data['name']}</b>:\n   - ⏱️ <b>مدة التواجد:</b> {presence_str}\n   - 🎤 <b>مدة التحدث:</b> {speak_str}")

    if not report_data_formatted:
         return f"{report_lines[0]}\n\nلا يوجد مشرفون يتم تتبعهم حاليًا."

    report_lines.extend(report_data_formatted)
    return "\n".join(report_lines)

async def generate_current_day_report_text(chat_id: int, report_title: str) -> str:
    """ينشئ نص التقرير لليوم الحالي حتى اللحظة (يشمل جميع المشرفين المتعقبين، مرتبين حسب التواجد)."""
    now = datetime.now(REPORT_TIMEZONE)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_time = now
    report_lines = [f"<b>{report_title}</b>"]
    report_lines.append(f"   <i>الوقت الحالي:</i> {end_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    report_lines.append("-" * 20)

    all_events = get_events_for_period(start_of_day, end_time, chat_id)
    events_by_admin = {}
    for admin_id_event, event_type, timestamp in all_events:
        if admin_id_event not in events_by_admin: events_by_admin[admin_id_event] = []
        events_by_admin[admin_id_event].append((event_type, timestamp.astimezone(REPORT_TIMEZONE)))

    admins_to_report = list(current_admin_ids)
    admin_user_map = {}
    if admins_to_report:
        try:
            admin_users = await app.get_users(admins_to_report)
            admin_user_map = {user.id: user for user in admin_users}
        except Exception as e:
            print(f"[Monitor Report Error] خطأ أثناء جلب معلومات المستخدمين للتقرير الحالي: {e}")

    admin_report_data_list = []
    for admin_id in admins_to_report:
        admin_events_tuples = sorted(events_by_admin.get(admin_id, []), key=lambda x: x[1])
        presence_duration, speak_duration = calculate_durations_from_events(admin_events_tuples)
        status = admin_status.get(admin_id)
        current_state_indicator = ""
        last_join_event_tuple = None

        # حساب المدة المستمرة إذا كان المستخدم نشطاً الآن
        if status:
            if status.get('in_call'):
                current_state_indicator += " [متواجد الآن]"
                join_time_to_use = status.get('join_time')
                if join_time_to_use:
                    join_time_local = join_time_to_use.astimezone(REPORT_TIMEZONE) if join_time_to_use.tzinfo else join_time_to_use
                    current_duration_start = max(join_time_local, start_of_day)
                    if end_time > current_duration_start:
                         presence_duration += (end_time - current_duration_start)

            if status.get('speaking'):
                 current_state_indicator += " [يتحدث]"
                 speak_start_time_to_use = status.get('speak_start_time')
                 if speak_start_time_to_use:
                     speak_start_local = speak_start_time_to_use.astimezone(REPORT_TIMEZONE) if speak_start_time_to_use.tzinfo else speak_start_time_to_use
                     current_speak_duration_start = max(speak_start_local, start_of_day)

                     last_join_time_in_status = status.get('join_time')
                     last_join_time_local_status = None
                     if last_join_time_in_status:
                          last_join_time_local_status = last_join_time_in_status.astimezone(REPORT_TIMEZONE) if last_join_time_in_status.tzinfo else last_join_time_in_status

                     if last_join_time_local_status and speak_start_local >= last_join_time_local_status:
                         if end_time > current_speak_duration_start:
                              speak_duration += (end_time - current_speak_duration_start)
                     elif not last_join_time_local_status:
                          if end_time > current_speak_duration_start:
                               speak_duration += (end_time - current_speak_duration_start)

        admin_user = admin_user_map.get(admin_id)
        if not admin_user and status and status.get('user_info'): admin_user = status.get('user_info')
        if admin_user: admin_name = admin_user.mention or admin_user.first_name or f"المستخدم ({admin_id})"
        else: admin_name = f"المستخدم ({admin_id})"

        admin_report_data_list.append({
            "id": admin_id,
            "name": admin_name,
            "presence": presence_duration,
            "speak": speak_duration,
            "indicator": current_state_indicator
        })

    admin_report_data_list.sort(key=lambda x: x['presence'], reverse=True)

    report_data_formatted = []
    for data in admin_report_data_list:
         # --- !!! تم التعديل هنا: إضافة منطق النصوص المخصصة للمدد الصفرية !!! ---
        if data['presence'] <= timedelta(0):
            # التحقق من الحالة الحالية حتى لو المدة المسجلة صفر (قد يكون انضم للتو)
            status = admin_status.get(data['id'])
            if status and status.get('in_call'):
                 presence_str = format_timedelta_arabic(data['presence']) # قد تكون صفر لكنه متواجد
                 speak_str = "لم يتكلم" # إذا المدة صفر، فمدة التحدث صفر أيضاً
                 current_state_indicator = data['indicator'] # استخدام المؤشر المحسوب
            else:
                 presence_str = "لم يتواجد"
                 speak_str = "لم يتواجد"
                 current_state_indicator = "" # لا يوجد مؤشر إذا لم يتواجد
        else:
            presence_str = format_timedelta_arabic(data['presence'])
            if data['speak'] <= timedelta(0):
                # التحقق إذا كان يتحدث الآن رغم أن المدة المسجلة صفر
                status = admin_status.get(data['id'])
                if status and status.get('speaking'):
                     speak_str = format_timedelta_arabic(data['speak']) # قد تكون صفر لكنه يتحدث
                else:
                     speak_str = "لم يتكلم"
            else:
                speak_str = format_timedelta_arabic(data['speak'])
            current_state_indicator = data['indicator'] # استخدام المؤشر المحسوب
        # ---------------------------------------------------------------------

        report_data_formatted.append(f"👤 <b>{data['name']}</b>{current_state_indicator}:\n   - ⏱️ <b>مدة التواجد:</b> {presence_str}\n   - 🎤 <b>مدة التحدث:</b> {speak_str}")


    if not report_data_formatted:
        return f"{report_lines[0]}\n\nلا يوجد مشرفون يتم تتبعهم حاليًا."
    report_lines.extend(report_data_formatted)
    return "\n".join(report_lines)


# --- دوال إرسال التقارير المجدولة ---
async def send_daily_report():
    now = datetime.now(REPORT_TIMEZONE)
    end_of_report_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_report_day = end_of_report_day - timedelta(days=1)
    title = f"التقرير اليومي للمشرفين ليوم {start_of_report_day.strftime('%Y-%m-%d')}"
    print(f"[{datetime.now(REPORT_TIMEZONE)}] [Monitor] إنشاء التقرير اليومي لـ {start_of_report_day.date()}...")
    report_text = await generate_report_text(start_of_report_day, end_of_report_day, TARGET_CHAT_ID, title)
    try:
        await app.send_message(REPORT_CHAT_ID, report_text, parse_mode=ParseMode.HTML)
        print(f"[{datetime.now(REPORT_TIMEZONE)}] [Monitor] تم إرسال التقرير اليومي بنجاح.")
    except Exception as e:
        print(f"[{datetime.now(REPORT_TIMEZONE)}] [Monitor] فشل إرسال التقرير اليومي: {e}")

async def send_weekly_report():
    now = datetime.now(REPORT_TIMEZONE)
    end_of_report_week = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_report_week = end_of_report_week - timedelta(days=7)
    title = f"التقرير الأسبوعي للمشرفين ({start_of_report_week.strftime('%Y-%m-%d')} - {end_of_report_week.strftime('%Y-%m-%d')})"
    print(f"[{datetime.now(REPORT_TIMEZONE)}] [Monitor] إنشاء التقرير الأسبوعي...")
    report_text = await generate_report_text(start_of_report_week, end_of_report_week, TARGET_CHAT_ID, title)
    try:
        await app.send_message(REPORT_CHAT_ID, report_text, parse_mode=ParseMode.HTML)
        print(f"[{datetime.now(REPORT_TIMEZONE)}] [Monitor] تم إرسال التقرير الأسبوعي بنجاح.")
    except Exception as e:
        print(f"[{datetime.now(REPORT_TIMEZONE)}] [Monitor] فشل إرسال التقرير الأسبوعي: {e}")

async def send_monthly_report():
    now = datetime.now(REPORT_TIMEZONE)
    end_of_report_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_of_report_month = end_of_report_month - relativedelta(months=1)
    title = f"التقرير الشهري للمشرفين لشهر {start_of_report_month.strftime('%Y-%m')}"
    print(f"[{datetime.now(REPORT_TIMEZONE)}] [Monitor] إنشاء التقرير الشهري...")
    report_text = await generate_report_text(start_of_report_month, end_of_report_month, TARGET_CHAT_ID, title)
    try:
        await app.send_message(REPORT_CHAT_ID, report_text, parse_mode=ParseMode.HTML)
        print(f"[{datetime.now(REPORT_TIMEZONE)}] [Monitor] تم إرسال التقرير الشهري بنجاح.")
    except Exception as e:
        print(f"[{datetime.now(REPORT_TIMEZONE)}] [Monitor] فشل إرسال التقرير الشهري: {e}")

# --- أمر التقرير الحالي (/stage) ---
# --- !!! تم التعديل هنا: استخدام regex بدلاً من command !!! ---
@app.on_message(filters.regex(r"^(stage|استيج)$", flags=re.IGNORECASE) & (filters.private | (filters.group & filters.chat(TARGET_CHAT_ID))) & filters.user(CONTROLLER_ID))
async def stage_report_command(client, message):
    """يرسل تقريرًا عن نشاط المشرفين لليوم الحالي حتى الآن."""
    chat_to_report = TARGET_CHAT_ID
    if not assistant_client or not assistant_client.is_connected:
         await message.reply("⚠️ يبدو أن العميل المساعد للمراقبة غير متصل حاليًا. لا يمكن إنشاء التقرير.")
         return
    if not current_admin_ids:
         await message.reply("⚠️ لا يوجد مشرفون يتم تتبعهم حاليًا في المجموعة المستهدفة.")
         return

    msg = await message.reply("⏳ جاري تحديث الحالة وإنشاء تقرير الحالة الحالية للمشرفين...")
    try:
        print("[Monitor] Running immediate status check for /stage command...")
        await check_vc_status() # تحديث الحالة في الذاكرة
        await asyncio.sleep(0.5) # انتظار قصير جداً لضمان اكتمال التحديثات المحتملة
        print("[Monitor] Status check complete, generating /stage report...")

        report_title = "📊 تقرير حالة المشرفين الآن لمجموعة دعوة اهل البيت"
        report_text = await generate_current_day_report_text(chat_to_report, report_title)
        await msg.edit_text(report_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        print(f"[Monitor Error] خطأ عند إنشاء تقرير /stage: {e}")
        traceback.print_exc()
        await msg.edit_text("حدث خطأ أثناء إنشاء التقرير.")


# --- بدء مهام المراقبة والجدولة ---
async def start_monitoring_tasks():
    """تبدأ المهام المجدولة للمراقبة والتقارير."""
    global assistant_client
    print(f"[{datetime.now()}] [Monitor] بدء مهام المراقبة...")
    init_db()
    print(f"[{datetime.now()}] [Monitor] محاولة تهيئة وتشغيل العميل المساعد المخصص...")
    if not ASSISTANT_SESSION_STRING or ASSISTANT_SESSION_STRING == "YOUR_ASSISTANT_SESSION_STRING_HERE":
         print(f"[{datetime.now()}] [Monitor CRITICAL ERROR] لم يتم توفير سلسلة جلسة للحساب المساعد (ASSISTANT_SESSION_STRING). لا يمكن بدء المراقبة.")
         return
    try:
        assistant_client = Client(
            name="vc_monitor_assistant",
            api_id=ASSISTANT_API_ID,
            api_hash=ASSISTANT_API_HASH,
            session_string=ASSISTANT_SESSION_STRING,
            no_updates=True
        )
        await assistant_client.start()
        my_assistant_info = assistant_client.me
        print(f"[{datetime.now()}] [Monitor] تم تشغيل العميل المساعد بنجاح: {my_assistant_info.first_name} (ID: {my_assistant_info.id})")
    except SessionPasswordNeeded:
         print(f"[{datetime.now()}] [Monitor CRITICAL ERROR] الحساب المساعد يتطلب كلمة مرور تحقق بخطوتين (2FA).")
         assistant_client = None; return
    except (AuthKeyUnregistered, UserDeactivated, UserDeactivatedBan) as e:
         print(f"[{datetime.now()}] [Monitor CRITICAL ERROR] مشكلة في مصادقة الحساب المساعد: {type(e).__name__}.")
         assistant_client = None; return
    except Exception as e:
        print(f"[{datetime.now()}] [Monitor CRITICAL ERROR] فشل تشغيل العميل المساعد: {type(e).__name__} - {e}")
        assistant_client = None; return

    await update_admin_list()
    scheduler.add_job(update_admin_list, 'interval', hours=1, id='monitor_update_admins')
    await asyncio.sleep(5)
    scheduler.add_job(check_vc_status, 'interval', seconds=POLLING_INTERVAL_SECONDS, id='monitor_check_vc')
    scheduler.add_job(send_daily_report, trigger=CronTrigger(hour=REPORT_HOUR, minute=REPORT_MINUTE, timezone=REPORT_TIMEZONE_STR), id='monitor_daily_report')
    scheduler.add_job(send_weekly_report, trigger=CronTrigger(day_of_week='mon', hour=REPORT_HOUR, minute=REPORT_MINUTE + 1, timezone=REPORT_TIMEZONE_STR), id='monitor_weekly_report')
    scheduler.add_job(send_monthly_report, trigger=CronTrigger(day='1', hour=REPORT_HOUR, minute=REPORT_MINUTE + 2, timezone=REPORT_TIMEZONE_STR), id='monitor_monthly_report')

    if not scheduler.running:
        try:
            scheduler.start()
            print(f"[{datetime.now()}] [Monitor] المجدول بدأ.")
        except Exception as e:
            print(f"[{datetime.now()}] [Monitor] خطأ فادح عند بدء المجدول: {e}")
            if assistant_client and assistant_client.is_connected:
                 await assistant_client.stop()
                 print(f"[{datetime.now()}] [Monitor] تم إيقاف العميل المساعد بسبب فشل المجدول.")
    else:
         print(f"[{datetime.now()}] [Monitor] المجدول يعمل بالفعل.")

# --- أمر التحقق من الحالة (اختياري) ---
@app.on_message(filters.command("monitorstatus") & filters.private & filters.user(CONTROLLER_ID))
async def monitor_status_command(client, message):
     """يعرض حالة المراقبة (يعمل فقط للمستخدم المتحكم وفي الخاص)."""
     assistant_id_str = assistant_client.me.id if assistant_client and assistant_client.is_connected else "غير متصل"
     if scheduler.running:
         jobs = scheduler.get_jobs()
         job_details = "\n".join([f"- {job.id} (Next run: {job.next_run_time.strftime('%Y-%m-%d %H:%M:%S %Z') if job.next_run_time else 'N/A'})" for job in jobs if job.id.startswith('monitor_')])
         excluded_ids_str = ', '.join(map(str, EXCLUDED_ADMIN_IDS)) if EXCLUDED_ADMIN_IDS else "لا يوجد"
         await message.reply(f"📊 **حالة مراقبة المكالمات:**\n\n- المجدول يعمل.\n- العميل المساعد: `{assistant_id_str}`\n- قاعدة البيانات: `{DB_FILE}`\n- المجموعة المراقبة: `{TARGET_CHAT_ID}`\n- مجموعة التقارير: `{REPORT_CHAT_ID}`\n- المشرفون المستثنون: `{excluded_ids_str}`\n- المهام المجدولة:\n{job_details}\n- عدد المشرفين المتعقبين حالياً: {len(current_admin_ids)}")
     else:
         await message.reply(f"⚠️ **حالة مراقبة المكالمات:**\n\n- المجدول لا يعمل.\n- العميل المساعد: `{assistant_id_str}`")

# --- بدء التشغيل التلقائي ---
print("[VC Monitor Plugin] تم تحميل ملحق مراقبة المكالمات (تعديل نصوص المدد وتشغيل الأمر).")
loop = asyncio.get_event_loop()
if loop.is_running():
    asyncio.create_task(start_monitoring_tasks())
else:
    print("[VC Monitor Plugin Warning] حلقة الأحداث غير نشطة بعد، قد لا تبدأ مهام المراقبة تلقائيًا.")


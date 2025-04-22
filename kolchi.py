import datetime
import re
import time
import asyncio
import os
import logging
import sqlite3
import html
from datetime import datetime, timedelta, timezone

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

from pyrogram import filters, Client
from pyrogram import ContinuePropagation
from pyrogram.enums import UserStatus, ParseMode, ChatMemberStatus, ChatType, ChatAction, ChatMembersFilter
from pyrogram.errors import (
    PeerIdInvalid, FloodWait, UserIsBlocked, ChatAdminRequired, ChatNotModified,
    MessageDeleteForbidden, MessageIdsEmpty, UserNotParticipant, ChannelPrivate,
    ChatForwardsRestricted, RightForbidden, UserAdminInvalid, ChatWriteForbidden, UserIsBot,
    BadRequest
)
from pyrogram.types import (
    Message, User, Chat, ChatMemberUpdated, ChatPrivileges, ChatPermissions, ChatMember
)

app: Client | None = None
try:
    from YukkiMusic import app as yukki_app
    app = yukki_app
    log.info("Combined Plugin: Imported YukkiMusic app.")
except ImportError:
    log.warning("Combined Plugin: Could not import YukkiMusic app. Using placeholder.")
    class DummyApp:
        def on_message(self, *args, **kwargs): return lambda f: f
        def on_chat_member_updated(self, *args, **kwargs): return lambda f: f
        me = None
    app = DummyApp()
except Exception as e:
     log.error(f"Combined Plugin: Error importing YukkiMusic app: {e}")

DB_FILE = "user_stats.db"
ADMIN_DB_FILE = "admin_actions.db"
STATS_DB_V2 = "group_stats_v2.db"

DELETE_CMD_LIMIT = 3
DELETE_CMD_WINDOW_SECONDS = 3600
MAX_DELETE_COUNT = 200
DEFAULT_KICK_THRESHOLD = 3
DEFAULT_MUTE_DAYS = 3
WEEK_START_DAY = 0

mangof = []

regular_member_permissions = ChatPermissions(
    can_send_messages=True, can_send_media_messages=True,
    can_send_polls=True, can_send_other_messages=True,
    can_add_web_page_previews=True, can_change_info=False,
    can_invite_users=True, can_pin_messages=False
)
special_member_permissions = ChatPermissions(
    can_send_messages=True, can_send_media_messages=True,
    can_send_polls=True, can_send_other_messages=True,
    can_add_web_page_previews=False,
    can_change_info=False, can_invite_users=True, can_pin_messages=False
)
restricted_permissions = ChatPermissions()

def init_databases():
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS kick_tracker (admin_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, kick_count INTEGER DEFAULT 0, first_kick_timestamp INTEGER DEFAULT 0, PRIMARY KEY (admin_id, chat_id))''')
            cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                kick_threshold INTEGER DEFAULT {DEFAULT_KICK_THRESHOLD},
                is_chat_locked INTEGER DEFAULT 0,
                is_forward_locked INTEGER DEFAULT 0,
                protection_enabled INTEGER DEFAULT 1
            )
            ''')
            cols_to_add = {
                "is_chat_locked": "INTEGER DEFAULT 0",
                "is_forward_locked": "INTEGER DEFAULT 0",
                "protection_enabled": "INTEGER DEFAULT 1",
                "admin_log_channel_id": "INTEGER",
                "monitor_log_channel_id": "INTEGER",
                "stats_report_channel_id": "INTEGER"
            }
            for col, definition in cols_to_add.items():
                try:
                    cursor.execute(f"ALTER TABLE chat_settings ADD COLUMN {col} {definition}")
                except sqlite3.OperationalError: pass
            cursor.execute('''CREATE TABLE IF NOT EXISTS allowed_forward_sources (chat_id INTEGER NOT NULL, source_id INTEGER NOT NULL, PRIMARY KEY (chat_id, source_id))''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS bot_settings (setting_key TEXT PRIMARY KEY, setting_value TEXT)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS excluded_admins (chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY (chat_id, user_id))''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS monitored_users (chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY (chat_id, user_id))''')
            conn.commit()
            log.info(f"Tables in '{ADMIN_DB_FILE}' initialized/updated successfully.")
    except sqlite3.Error as e: log.exception(f"Database initialization error for {ADMIN_DB_FILE}: {e}")
    except Exception as e: log.exception(f"Unexpected error during {ADMIN_DB_FILE} DB init: {e}")

    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS user_chat_status (chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, status TEXT NOT NULL, expiry_timestamp INTEGER, PRIMARY KEY (chat_id, user_id))''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS message_counts (chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, count INTEGER DEFAULT 0, PRIMARY KEY (chat_id, user_id))''')
            conn.commit()
            log.info(f"Tables in '{DB_FILE}' initialized/updated successfully.")
    except sqlite3.Error as e: log.exception(f"Database initialization error for {DB_FILE}: {e}")
    except Exception as e: log.exception(f"Unexpected error during {DB_FILE} DB init: {e}")

    try:
        with sqlite3.connect(STATS_DB_V2) as conn:
            cursor = conn.cursor()
            cursor.execute(''' CREATE TABLE IF NOT EXISTS messages ( message_pk INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP ) ''')
            cursor.execute(''' CREATE TABLE IF NOT EXISTS admin_actions ( action_id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, action_type TEXT NOT NULL, target_user_id INTEGER NOT NULL, actor_user_id INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP ) ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_chat_user_ts ON messages (chat_id, user_id, timestamp)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages (chat_id, timestamp)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_actions_chat_ts ON admin_actions (chat_id, timestamp)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_actions_chat_type ON admin_actions (chat_id, action_type)')
            conn.commit()
            log.info(f"Stats Reporter V2: Database '{STATS_DB_V2}' initialized.")
    except sqlite3.Error as e: log.exception(f"Stats Reporter V2: Database initialization error for {STATS_DB_V2}: {e}")
    except Exception as e: log.exception(f"Unexpected error during {STATS_DB_V2} DB init: {e}")

init_databases()

def get_admin_log_channel_id(chat_id: int) -> int | None:
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor(); cursor.execute("SELECT admin_log_channel_id FROM chat_settings WHERE chat_id = ?", (chat_id,)); result = cursor.fetchone()
            if result and result[0]: return int(result[0])
            else: return None
    except Exception as e: log.exception(f"[DB:{ADMIN_DB_FILE}] Error reading admin_log_channel_id for chat {chat_id}: {e}"); return None

def set_admin_log_channel_id(chat_id: int, target_log_channel_id: int) -> bool:
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            conn.execute("INSERT OR IGNORE INTO chat_settings (chat_id) VALUES (?)", (chat_id,))
            conn.execute("UPDATE chat_settings SET admin_log_channel_id = ? WHERE chat_id = ?", (target_log_channel_id, chat_id)); conn.commit()
            log.info(f"Admin log (Reasons) channel ID for chat {chat_id} set to {target_log_channel_id}"); return True
    except Exception as e: log.exception(f"[DB:{ADMIN_DB_FILE}] Error setting admin_log_channel_id for chat {chat_id}: {e}"); return False

def get_monitor_log_channel_id(chat_id: int) -> int | None:
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor(); cursor.execute("SELECT monitor_log_channel_id FROM chat_settings WHERE chat_id = ?", (chat_id,)); result = cursor.fetchone()
            if result and result[0]: return int(result[0])
            else: return None
    except Exception as e: log.exception(f"[DB:{ADMIN_DB_FILE}] Error reading monitor_log_channel_id for chat {chat_id}: {e}"); return None

def set_monitor_log_channel_id(chat_id: int, target_log_channel_id: int) -> bool:
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            conn.execute("INSERT OR IGNORE INTO chat_settings (chat_id) VALUES (?)", (chat_id,))
            conn.execute("UPDATE chat_settings SET monitor_log_channel_id = ? WHERE chat_id = ?", (target_log_channel_id, chat_id)); conn.commit()
            log.info(f"Monitor log channel ID for chat {chat_id} set to {target_log_channel_id}"); return True
    except Exception as e: log.exception(f"[DB:{ADMIN_DB_FILE}] Error setting monitor_log_channel_id for chat {chat_id}: {e}"); return False

def get_stats_report_channel_id(chat_id: int) -> int | None:
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor(); cursor.execute("SELECT stats_report_channel_id FROM chat_settings WHERE chat_id = ?", (chat_id,)); result = cursor.fetchone()
            if result and result[0]: return int(result[0])
            else: return None
    except Exception as e: log.exception(f"[DB:{ADMIN_DB_FILE}] Error reading stats_report_channel_id for chat {chat_id}: {e}"); return None

def set_stats_report_channel_id(chat_id: int, target_report_channel_id: int) -> bool:
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            conn.execute("INSERT OR IGNORE INTO chat_settings (chat_id) VALUES (?)", (chat_id,))
            conn.execute("UPDATE chat_settings SET stats_report_channel_id = ? WHERE chat_id = ?", (target_report_channel_id, chat_id)); conn.commit()
            log.info(f"Stats report channel ID for chat {chat_id} set to {target_report_channel_id}"); return True
    except Exception as e: log.exception(f"[DB:{ADMIN_DB_FILE}] Error setting stats_report_channel_id for chat {chat_id}: {e}"); return False

def get_excluded_admin_ids_from_db(chat_id: int) -> set[int]:
    excluded_ids = set()
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor(); cursor.execute("SELECT user_id FROM excluded_admins WHERE chat_id = ?", (chat_id,)); excluded_ids = {row[0] for row in cursor.fetchall()}
    except Exception as e: log.exception(f"[DB:{ADMIN_DB_FILE}] Error reading excluded_admins for chat {chat_id}: {e}")
    return excluded_ids

def add_excluded_admin_db(chat_id: int, user_id_to_exclude: int) -> bool:
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn: conn.execute("INSERT OR IGNORE INTO excluded_admins (chat_id, user_id) VALUES (?, ?)", (chat_id, user_id_to_exclude,)); conn.commit(); return True
    except Exception as e: log.exception(f"[DB:{ADMIN_DB_FILE}] Error adding excluded admin {user_id_to_exclude} for chat {chat_id}: {e}"); return False

def remove_excluded_admin_db(chat_id: int, user_id_to_remove: int) -> bool:
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor(); cursor.execute("DELETE FROM excluded_admins WHERE chat_id = ? AND user_id = ?", (chat_id, user_id_to_remove,)); conn.commit(); return cursor.rowcount > 0
    except Exception as e: log.exception(f"[DB:{ADMIN_DB_FILE}] Error removing excluded admin {user_id_to_remove} for chat {chat_id}: {e}"); return False

def get_monitored_user_ids_from_db(chat_id: int) -> set[int]:
    monitored_ids = set()
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM monitored_users WHERE chat_id = ?", (chat_id,))
            monitored_ids = {row[0] for row in cursor.fetchall()}
    except sqlite3.Error as e:
        log.exception(f"[DB:{ADMIN_DB_FILE}] Error reading monitored_users for chat {chat_id}: {e}")
    return monitored_ids

def add_monitored_user_db(chat_id: int, user_id_to_monitor: int) -> bool:
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            conn.execute("INSERT OR IGNORE INTO monitored_users (chat_id, user_id) VALUES (?, ?)", (chat_id, user_id_to_monitor,))
            conn.commit()
            log.info(f"Added/ignored user {user_id_to_monitor} in monitored_users table for chat {chat_id}.")
            return True
    except sqlite3.Error as e:
        log.exception(f"[DB:{ADMIN_DB_FILE}] Error adding monitored user {user_id_to_monitor} for chat {chat_id}: {e}")
        return False

def remove_monitored_user_db(chat_id: int, user_id_to_remove: int) -> bool:
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM monitored_users WHERE chat_id = ? AND user_id = ?", (chat_id, user_id_to_remove,))
            conn.commit()
            log.info(f"Removed user {user_id_to_remove} from monitored_users table for chat {chat_id} (if existed). Affected rows: {cursor.rowcount}")
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        log.exception(f"[DB:{ADMIN_DB_FILE}] Error removing monitored user {user_id_to_remove} for chat {chat_id}: {e}")
        return False

def get_user_mute_status(chat_id: int, user_id: int) -> tuple[bool, int | None]:
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT expiry_timestamp FROM user_chat_status WHERE chat_id = ? AND user_id = ? AND status = 'muted'", (chat_id, user_id))
            result = cursor.fetchone()
            if result:
                expiry_ts = result[0]
                now_ts = int(time.time())
                if expiry_ts is not None and expiry_ts < now_ts:
                     log.info(f"Mute expired for user {user_id} in chat {chat_id}. Removing record.")
                     cursor.execute("DELETE FROM user_chat_status WHERE chat_id = ? AND user_id = ? AND status = 'muted'", (chat_id, user_id))
                     conn.commit()
                     return False, None
                else:
                     return True, expiry_ts
            else:
                return False, None
    except sqlite3.Error as e:
        log.exception(f"[DB:{DB_FILE}] Error checking mute status table: {e}")
    return False, None

def is_special_member(chat_id: int, user_id: int) -> bool:
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM user_chat_status WHERE chat_id = ? AND user_id = ? AND status = 'special'", (chat_id, user_id))
            return cursor.fetchone() is not None
    except sqlite3.Error as e:
        log.exception(f"[DB:{DB_FILE}] Error checking special status table: {e}")
    return False

def is_bot_admin(chat_id: int, user_id: int) -> bool:
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM user_chat_status WHERE chat_id = ? AND user_id = ? AND status = 'admin'", (chat_id, user_id))
            return cursor.fetchone() is not None
    except sqlite3.Error as e:
        log.exception(f"[DB:{DB_FILE}] Error checking bot admin status table: {e}")
    return False

def is_forward_source_allowed(chat_id: int, source_id: int) -> bool:
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM allowed_forward_sources WHERE chat_id = ? AND source_id = ?", (chat_id, source_id))
            return cursor.fetchone() is not None
    except sqlite3.Error as e:
        log.exception(f"[DB:{ADMIN_DB_FILE}] Error checking allowed forward sources table: {e}")
    return False

def add_message_db_v2(user_id: int, chat_id: int):
    try:
        with sqlite3.connect(STATS_DB_V2) as conn:
            cursor = conn.cursor(); cursor.execute("INSERT INTO messages (user_id, chat_id) VALUES (?, ?)", (user_id, chat_id)); conn.commit()
    except sqlite3.Error as e: log.error(f"Stats Reporter V2: Database error adding message for chat {chat_id}: {e}")

def add_admin_action_db_v2(chat_id: int, action_type: str, target_user_id: int, actor_user_id: int | None):
    excluded_ids = get_excluded_admin_ids_from_db(chat_id)
    if target_user_id in excluded_ids:
        log.info(f"Stats Reporter V2: Ignoring action on excluded user {target_user_id} in chat {chat_id}")
        return
    try:
        with sqlite3.connect(STATS_DB_V2) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO admin_actions (chat_id, action_type, target_user_id, actor_user_id) VALUES (?, ?, ?, ?)", (chat_id, action_type, target_user_id, actor_user_id))
            conn.commit()
            log.info(f"Stats Reporter V2: Logged action '{action_type}' on user {target_user_id} in chat {chat_id}.")
    except sqlite3.Error as e: log.error(f"Stats Reporter V2: Database error adding admin action for chat {chat_id}: {e}")

def get_period_totals_v2(chat_id: int, start_dt: datetime, end_dt: datetime) -> dict:
    stats = {'messages': 0, 'bans': 0, 'mutes': 0}
    start_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')
    end_str = end_dt.strftime('%Y-%m-%d %H:%M:%S')
    excluded_ids = get_excluded_admin_ids_from_db(chat_id)
    monitored_ids = get_monitored_user_ids_from_db(chat_id)
    ids_to_exclude_from_msg_count = excluded_ids | monitored_ids

    try:
        with sqlite3.connect(STATS_DB_V2) as conn:
            cursor = conn.cursor()
            excluded_placeholders = ','.join('?' * len(ids_to_exclude_from_msg_count)) if ids_to_exclude_from_msg_count else 'NULL'
            query_params = [chat_id] + list(ids_to_exclude_from_msg_count) + [start_str, end_str]
            cursor.execute(f"SELECT COUNT(*) FROM messages WHERE chat_id = ? AND user_id NOT IN ({excluded_placeholders}) AND timestamp >= ? AND timestamp < ?", query_params)
            stats['messages'] = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM admin_actions WHERE chat_id = ? AND action_type = 'ban' AND timestamp >= ? AND timestamp < ?", (chat_id, start_str, end_str))
            stats['bans'] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM admin_actions WHERE chat_id = ? AND action_type = 'mute' AND timestamp >= ? AND timestamp < ?", (chat_id, start_str, end_str))
            stats['mutes'] = cursor.fetchone()[0]
    except sqlite3.Error as e: log.error(f"Stats Reporter V2: Database error getting period totals for chat {chat_id} ({start_str} to {end_str}): {e}")
    return stats

def get_user_counts_for_period_v2(chat_id: int, start_dt: datetime, end_dt: datetime) -> dict[int, int]:
    user_counts = {}
    start_str = start_dt.strftime('%Y-%m-%d %H:%M:%S')
    end_str = end_dt.strftime('%Y-%m-%d %H:%M:%S')
    try:
        with sqlite3.connect(STATS_DB_V2) as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT user_id, COUNT(*) FROM messages WHERE chat_id = ? AND timestamp >= ? AND timestamp < ? GROUP BY user_id", (chat_id, start_str, end_str))
            user_counts = dict(cursor.fetchall())
    except sqlite3.Error as e: log.error(f"Stats Reporter V2: Database error getting user counts for period in chat {chat_id} ({start_str} to {end_str}): {e}")
    return user_counts

def get_overall_user_counts_v2(chat_id: int) -> dict[int, int]:
    user_counts = {}
    try:
        with sqlite3.connect(STATS_DB_V2) as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT user_id, COUNT(*) FROM messages WHERE chat_id = ? GROUP BY user_id", (chat_id,))
            user_counts = dict(cursor.fetchall())
    except sqlite3.Error as e: log.error(f"Stats Reporter V2: Database error getting overall user counts for chat {chat_id}: {e}")
    return user_counts

def get_overall_action_counts_v2(chat_id: int) -> dict:
    action_counts = {'bans': 0, 'mutes': 0}
    try:
        with sqlite3.connect(STATS_DB_V2) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM admin_actions WHERE chat_id = ? AND action_type = 'ban'", (chat_id,))
            action_counts['bans'] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM admin_actions WHERE chat_id = ? AND action_type = 'mute'", (chat_id,))
            action_counts['mutes'] = cursor.fetchone()[0]
    except sqlite3.Error as e: log.error(f"Stats Reporter V2: Database error getting overall action counts for chat {chat_id}: {e}")
    return action_counts

async def check_tg_restrict_permissions(client: Client, chat_id: int, user_id: int) -> bool:
    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status == ChatMemberStatus.OWNER: return True
        elif member.status == ChatMemberStatus.ADMINISTRATOR:
            if member.privileges and member.privileges.can_restrict_members: return True
    except UserNotParticipant: return False
    except Exception as e: log.error(f"Error checking TG restrict permissions for user {user_id} in chat {chat_id}: {e}")
    return False

async def check_tg_promote_permissions(client: Client, chat_id: int, user_id: int) -> bool:
    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status == ChatMemberStatus.OWNER:
            return True
        elif member.status == ChatMemberStatus.ADMINISTRATOR:
            if member.privileges and member.privileges.can_promote_members:
                 return True
    except UserNotParticipant:
         return False
    except Exception as e:
        log.error(f"Error checking TG promote permissions for user {user_id} in chat {chat_id}: {e}")
    return False

async def check_bot_admin_permissions(client: Client, chat_id: int, user_id: int) -> bool:
    if await check_tg_restrict_permissions(client, chat_id, user_id):
        return True
    if is_bot_admin(chat_id, user_id):
        return True
    return False

async def check_forward_control_permissions(client: Client, chat_id: int, user_id: int) -> bool:
    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status == ChatMemberStatus.OWNER: return True
        elif member.status == ChatMemberStatus.ADMINISTRATOR:
            if (member.privileges and member.privileges.can_change_info and member.privileges.can_promote_members): return True
    except UserNotParticipant: return False
    except Exception as e: log.error(f"Error checking forward control permissions for user {user_id} in chat {chat_id}: {e}")
    return False

async def check_delete_permissions(client: Client, chat_id: int, user_id: int) -> tuple[bool, bool, bool]:
    is_owner = False; can_delete = False; can_promote = False
    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status == ChatMemberStatus.OWNER: is_owner = True; can_delete = True; can_promote = True
        elif member.status == ChatMemberStatus.ADMINISTRATOR:
            if member.privileges:
                if member.privileges.can_delete_messages: can_delete = True
                if member.privileges.can_promote_members: can_promote = True
    except UserNotParticipant: pass
    except Exception as e: log.error(f"Error checking delete permissions for user {user_id} in chat {chat_id}: {e}")
    return can_delete, is_owner, can_promote

async def is_exempt_from_lock(client: Client, chat_id: int, user_id: int) -> bool:
    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]: return True
    except UserNotParticipant: pass
    except Exception as e: log.error(f"Error checking admin status for exemption in chat {chat_id}, user {user_id}: {e}")

    if is_bot_admin(chat_id, user_id):
        return True
    if is_special_member(chat_id, user_id):
        return True
    return False

async def get_target_user(client: Client, message: Message) -> User | None:
     target_user = None
     if message.reply_to_message:
         if message.reply_to_message.from_user: target_user = message.reply_to_message.from_user
         elif message.reply_to_message.sender_chat and message.reply_to_message.sender_chat.id == message.chat.id: await message.reply_text("لا يمكن استهداف الرسائل المرسلة نيابة عن الدردشة."); return None
         elif not message.reply_to_message.from_user: await message.reply_text("لا يمكنني تحديد المستخدم من هذه الرسالة المردود عليها."); return None
     else:
         args = message.command
         if len(args) > 1:
             try: target_user = await client.get_users(args[1])
             except PeerIdInvalid: await message.reply_text(f"لم أتمكن من العثور على المستخدم: <code>{args[1]}</code>", parse_mode=ParseMode.HTML); return None
             except Exception as e: log.error(f"Error in get_users for '{args[1]}': {e}"); await message.reply_text(f"⚠️ حدث خطأ أثناء البحث عن المستخدم: <code>{args[1]}</code>", parse_mode=ParseMode.HTML); return None
         else: await message.reply_text("⚠️ يرجى الرد على المستخدم أو كتابة معرفه/اسم مستخدمه بعد الأمر."); return None
     if not target_user: await message.reply_text("⚠️ لم يتم تحديد المستخدم بشكل صحيح."); return None
     return target_user

async def is_group_owner(client: Client, chat_id: int, user_id: int) -> bool:
    try: member = await client.get_chat_member(chat_id, user_id); return member.status == ChatMemberStatus.OWNER
    except Exception: return False

user_cache_stats_v2 = {}
async def get_user_display_name_pyrogram_v2(user_id: int, chat_id: int, client: Client) -> str:
    global user_cache_stats_v2; lrm = "\u200E"
    if user_id in user_cache_stats_v2: return user_cache_stats_v2[user_id]
    display_name = f"User (<code>{user_id}</code>)"
    try:
        member = await client.get_chat_member(chat_id=chat_id, user_id=user_id); user = member.user
    except (UserNotParticipant, PeerIdInvalid, ValueError, BadRequest):
        try: user = await client.get_users(user_id)
        except Exception as e: log.warning(f"Could not get user info for {user_id} via get_users: {e}"); user = None
    except Exception as e: log.error(f"Unexpected error getting chat member {user_id} in {chat_id}: {e}"); user = None
    if user:
        first_name_html = html.escape(user.first_name) if user.first_name else ""
        last_name_html = html.escape(user.last_name) if user.last_name else ""
        full_name_html = (first_name_html + " " + last_name_html).strip() or f"User {user.id}"
        if user.username: display_name = f"<a href='https://t.me/{user.username}'>{full_name_html}</a>"
        else: display_name = f"<a href='tg://user?id={user.id}'>{full_name_html}</a>"
        display_name += f" (<code>{user.id}</code>)"
    formatted_name = f"{lrm}{display_name}{lrm}"; user_cache_stats_v2[user_id] = formatted_name; return formatted_name

def get_period_start_end(period_type: str) -> tuple[datetime, datetime] | None:
    now_utc = datetime.now(timezone.utc)
    if period_type == "current_day": start_dt = now_utc.replace(hour=0, minute=0, second=0, microsecond=0); end_dt = now_utc; return start_dt, end_dt
    elif period_type == "previous_day": yesterday = now_utc - timedelta(days=1); start_dt = yesterday.replace(hour=0, minute=0, second=0, microsecond=0); end_dt = start_dt + timedelta(days=1); return start_dt, end_dt
    elif period_type == "current_week": days_since_start = now_utc.weekday() - WEEK_START_DAY; start_dt = (now_utc - timedelta(days=days_since_start)).replace(hour=0, minute=0, second=0, microsecond=0); end_dt = now_utc; return start_dt, end_dt
    elif period_type == "previous_week": days_since_start = now_utc.weekday() - WEEK_START_DAY; start_of_current_week = (now_utc - timedelta(days=days_since_start)).replace(hour=0, minute=0, second=0, microsecond=0); end_dt = start_of_current_week; start_dt = end_dt - timedelta(weeks=1); return start_dt, end_dt
    elif period_type == "current_month": start_dt = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0); end_dt = now_utc; return start_dt, end_dt
    elif period_type == "previous_month": first_day_current_month = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0); end_dt = first_day_current_month; last_day_previous_month = end_dt - timedelta(days=1); start_dt = last_day_previous_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0); return start_dt, end_dt
    return None

async def log_admin_action(client: Client, action: str, admin: User | None, target: User | None, chat: Chat | None, duration_days: int | None = None, extra_info: str | None = None):
    if not chat: log.warning("log_admin_action called without a chat object."); return
    source_chat_id = chat.id; log_channel_id = get_admin_log_channel_id(source_chat_id)
    if not log_channel_id: return
    admin_name_html = html.escape(admin.first_name) if admin and admin.first_name else "غير معروف"
    admin_link = f"<a href='tg://user?id={admin.id}'>{admin_name_html}</a> (<code>{admin.id}</code>)" if admin else "<i>النظام</i>"
    target_name_html = html.escape(target.first_name) if target and target.first_name else ""
    target_link = f"<a href='tg://user?id={target.id}'>{target_name_html}</a> (<code>{target.id}</code>)" if target else "<i>لا يوجد</i>"
    chat_title_html = html.escape(chat.title) if chat.title else "<i>اسم غير معروف</i>"
    chat_link = f"<a href='https://t.me/{chat.username}'>{chat_title_html}</a>" if chat.username else chat_title_html
    now_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
    log_text = f"📝 <b>تسجيل اجراء</b> 📝\n\n<b>النوع:</b> {action}\n<b>الدردشة المصدر:</b> {chat_link} (<code>{source_chat_id}</code>)\n"
    if target: log_text += f"<b>الهدف:</b> {target_link}\n"
    if admin: log_text += f"<b>بواسطة:</b> {admin_link}\n"
    if duration_days is not None: log_text += f"<b>المدة:</b> {duration_days} أيام\n"
    if extra_info: log_text += f"<b>معلومات إضافية:</b> {html.escape(extra_info)}\n"
    log_text += f"⏰ <b>الوقت:</b> {now_time}"
    try: await client.send_message(chat_id=log_channel_id, text=log_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except (PeerIdInvalid, ChannelPrivate): log.error(f"Admin log (Reasons) channel ID {log_channel_id} (for source chat {source_chat_id}) is invalid or private.")
    except ChatAdminRequired: log.error(f"Bot lacks permission to send messages in the admin log (Reasons) channel {log_channel_id} (for source chat {source_chat_id}).")
    except FloodWait as e: log.warning(f"Flood wait of {e.value} seconds when logging admin action '{action}' to {log_channel_id}"); await asyncio.sleep(e.value + 1)
    except Exception as e: log.exception(f"Failed to send admin action log '{action}' to channel {log_channel_id} for source chat {source_chat_id}")

async def format_detailed_period_report_pyrogram(chat_id: int, period_name: str, start_dt: datetime, end_dt: datetime, client: Client) -> str | None:
    global user_cache_stats_v2; user_cache_stats_v2 = {}
    try:
        period_totals = get_period_totals_v2(chat_id, start_dt, end_dt)
        period_user_counts = get_user_counts_for_period_v2(chat_id, start_dt, end_dt)
        excluded_admin_ids = get_excluded_admin_ids_from_db(chat_id)
        monitored_user_ids = get_monitored_user_ids_from_db(chat_id)
        admins = []; admin_ids = set()
        try:
            async for member in client.get_chat_members(chat_id, filter=ChatMembersFilter.ADMINISTRATORS):
                admins.append(member.user); admin_ids.add(member.user.id)
        except Exception as e: log.error(f"Could not fetch administrators for chat {chat_id}: {e}")
        users_in_admin_section = (admin_ids - excluded_admin_ids) | monitored_user_ids
        lines = [f"📊 <b>إحصائيات {period_name}:</b>", "━" * 20, f"  ✉️ إجمالي الرسائل (الأعضاء فقط): <b>{period_totals.get('messages', 0)}</b>", f"  🚫 عمليات الحظر: <b>{period_totals.get('bans', 0)}</b>", f"  🔇 عمليات الكتم: <b>{period_totals.get('mutes', 0)}</b>", "", f"👑⭐ <b>إحصائيات المشرفين والمراقبين (خلال {period_name}):</b>"]
        admin_monitor_counts_display = { user_id: period_user_counts.get(user_id, 0) for user_id in users_in_admin_section }
        if not admin_monitor_counts_display: lines.append("  <i>لم يتم العثور على مشرفين أو مراقبين (أو تم استثناء الجميع).</i>")
        else:
            sorted_admin_monitor_counts = sorted(admin_monitor_counts_display.items(), key=lambda item: item[1], reverse=True)
            rank = 1; active_found = False
            for user_id, count in sorted_admin_monitor_counts:
                user_display_name = await get_user_display_name_pyrogram_v2(user_id, chat_id, client)
                role_icon = "👑" if user_id in admin_ids else "⭐"
                icon = "🔹" if count > 0 else "▫️"
                rank_str = f"{rank}. " if count > 0 else ""
                lines.append(f"  {icon} {rank_str}{role_icon}<b>{user_display_name}</b> :  ✉️ <b>{count}</b>")
                if count > 0: rank += 1; active_found = True
            if not active_found: lines.append("  <i>لا يوجد رسائل مسجلة للمشرفين/المراقبين (غير المستثنين) في هذه الفترة.</i>")
        lines.append(""); lines.append(f"🏆 <b>أكثر 20 عضوًا نشاطًا (خلال {period_name}):</b>")
        member_period_counts = { user_id: count for user_id, count in period_user_counts.items() if user_id not in admin_ids and user_id not in monitored_user_ids and count > 0 }
        sorted_members = sorted(member_period_counts.items(), key=lambda item: item[1], reverse=True); top_20_members = sorted_members[:20]
        if not top_20_members: lines.append("  <i>لا يوجد رسائل مسجلة للأعضاء الآخرين في هذه الفترة.</i>")
        else:
            member_rank = 1
            for user_id, count in top_20_members:
                user_display_name = await get_user_display_name_pyrogram_v2(user_id, chat_id, client)
                lines.append(f"  {member_rank}. 👤 <b>{user_display_name}</b> :  ✉️ <b>{count}</b>"); member_rank += 1
        user_cache_stats_v2 = {}; report_text = "\n".join(lines)
        return report_text
    except Exception as e: log.error(f"Failed to generate detailed period report message for chat {chat_id}: {e}", exc_info=True); user_cache_stats_v2 = {}; return None

async def format_overall_report_pyrogram(chat_id: int, client: Client) -> str | None:
    global user_cache_stats_v2; user_cache_stats_v2 = {}
    try:
        overall_actions = get_overall_action_counts_v2(chat_id)
        overall_user_counts = get_overall_user_counts_v2(chat_id)
        excluded_admin_ids = get_excluded_admin_ids_from_db(chat_id)
        monitored_user_ids = get_monitored_user_ids_from_db(chat_id)
        admins = []; admin_ids = set()
        try:
            async for member in client.get_chat_members(chat_id, filter=ChatMembersFilter.ADMINISTRATORS):
                admins.append(member.user); admin_ids.add(member.user.id)
        except Exception as e: log.error(f"Could not fetch administrators for chat {chat_id}: {e}")
        users_in_admin_section = (admin_ids - excluded_admin_ids) | monitored_user_ids
        lines = ["📊 <b>التقرير الإجمالي الشامل</b> 📊", "━" * 20, "<b>الإحصائيات الكلية (منذ البداية):</b>"]
        total_messages_filtered = sum( count for uid, count in overall_user_counts.items() if uid not in admin_ids and uid not in monitored_user_ids )
        lines.append(f"  ✉️ إجمالي الرسائل (الأعضاء فقط): <b>{total_messages_filtered}</b>")
        lines.append(f"  🚫 إجمالي الحظر: <b>{overall_actions.get('bans', 0)}</b>")
        lines.append(f"  🔇 إجمالي الكتم: <b>{overall_actions.get('mutes', 0)}</b>")
        lines.append(""); lines.append("👑⭐ <b>إحصائيات المشرفين والمراقبين (الإجمالية):</b>")
        admin_monitor_counts_display = { user_id: overall_user_counts.get(user_id, 0) for user_id in users_in_admin_section }
        if not admin_monitor_counts_display: lines.append("  <i>لم يتم العثور على مشرفين أو مراقبين (أو تم استثناء الجميع).</i>")
        else:
            sorted_admin_monitor_counts = sorted(admin_monitor_counts_display.items(), key=lambda item: item[1], reverse=True)
            if not sorted_admin_monitor_counts or all(count == 0 for _, count in sorted_admin_monitor_counts): lines.append("  <i>لا يوجد رسائل مسجلة للمشرفين/المراقبين (غير المستثنين).</i>")
            else:
                rank = 1
                for user_id, count in sorted_admin_monitor_counts:
                    user_display_name = await get_user_display_name_pyrogram_v2(user_id, chat_id, client)
                    role_icon = "👑" if user_id in admin_ids else "⭐"
                    icon = "🔹" if count > 0 else "▫️"
                    rank_str = f"{rank}. " if count > 0 else ""
                    lines.append(f"  {icon} {rank_str}{role_icon}<b>{user_display_name}</b> :  ✉️ <b>{count}</b>")
                    if count > 0: rank += 1
        lines.append(""); lines.append("🏆 <b>أكثر 20 عضوًا نشاطًا (غير المشرفين/المراقبين):</b>")
        member_counts = { user_id: count for user_id, count in overall_user_counts.items() if user_id not in admin_ids and user_id not in monitored_user_ids and count > 0 }
        sorted_members = sorted(member_counts.items(), key=lambda item: item[1], reverse=True); top_20_members = sorted_members[:20]
        if not top_20_members: lines.append("  <i>لا يوجد رسائل مسجلة للأعضاء الآخرين.</i>")
        else:
            member_rank = 1
            for user_id, count in top_20_members:
                user_display_name = await get_user_display_name_pyrogram_v2(user_id, chat_id, client)
                lines.append(f"  {member_rank}. 👤 <b>{user_display_name}</b> :  ✉️ <b>{count}</b>"); member_rank += 1
        user_cache_stats_v2 = {}; report_text = "\n".join(lines)
        return report_text
    except Exception as e: log.error(f"Failed to generate overall report message for chat {chat_id}: {e}", exc_info=True); user_cache_stats_v2 = {}; return None

@app.on_message(filters.command("تعيين الاسباب", prefixes=[""]) & filters.group)
async def set_admin_log_channel_command(client: Client, message: Message):
    chat_id = message.chat.id; user_id = message.from_user.id
    if not await is_group_owner(client, chat_id, user_id): return await message.reply_text("👮‍♂️ عذراً، هذا الأمر مخصص لـ <b>مالك المجموعة</b> فقط.", parse_mode=ParseMode.HTML)
    if len(message.command) != 2: return await message.reply_text("⚠️ <b>خطأ في الاستخدام!</b>\n<code>تعيين الاسباب [ID]</code>", parse_mode=ParseMode.HTML)
    try: new_log_id = int(message.command[1])
    except ValueError: return await message.reply_text("❌ المعرف المدخل غير صالح.")
    if not (-2000000000000 < new_log_id < 0): return await message.reply_text("❌ المعرف المدخل غير صالح. يرجى إدخال معرف مجموعة أو قناة (يبدأ بـ -100 عادةً).")
    try:
        log_chat = await client.get_chat(new_log_id); await client.send_chat_action(new_log_id, ChatAction.TYPING)
    except Exception as e: return await message.reply_text(f"❌ فشل التحقق من القناة/المجموعة <code>{new_log_id}</code>.\nالخطأ: {str(e)}", parse_mode=ParseMode.HTML)
    if set_admin_log_channel_id(chat_id, new_log_id):
        log_chat_title_html = html.escape(log_chat.title) if log_chat.title else f"<code>{new_log_id}</code>"
        await message.reply_text(f"✅ تم تعيين قناة <b>الأسباب</b> لهذه المجموعة إلى: <b>{log_chat_title_html}</b> (<code>{new_log_id}</code>)", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "✅ تعيين قناة الأسباب", message.from_user, None, message.chat, extra_info=f"تم التعيين إلى {log_chat_title_html} (<code>{new_log_id}</code>)")
    else: await message.reply_text("❌ فشل حفظ الإعداد في قاعدة البيانات.")

@app.on_message(filters.command("تعيين المراقبة", prefixes=[""]) & filters.group)
async def set_monitor_log_channel_command(client: Client, message: Message):
    chat_id = message.chat.id; user_id = message.from_user.id
    if not await is_group_owner(client, chat_id, user_id): return await message.reply_text("👮‍♂️ عذراً، هذا الأمر مخصص لـ <b>مالك المجموعة</b> فقط.", parse_mode=ParseMode.HTML)
    if len(message.command) != 2: return await message.reply_text("⚠️ <b>خطأ في الاستخدام!</b>\n<code>تعيين المراقبة [ID]</code>", parse_mode=ParseMode.HTML)
    try: new_log_id = int(message.command[1])
    except ValueError: return await message.reply_text("❌ المعرف المدخل غير صالح.")
    if not (-2000000000000 < new_log_id < 0): return await message.reply_text("❌ المعرف المدخل غير صالح. يرجى إدخال معرف مجموعة أو قناة (يبدأ بـ -100 عادةً).")
    try:
        log_chat = await client.get_chat(new_log_id); await client.send_chat_action(new_log_id, ChatAction.TYPING)
    except Exception as e: return await message.reply_text(f"❌ فشل التحقق من القناة/المجموعة <code>{new_log_id}</code>.\nالخطأ: {str(e)}", parse_mode=ParseMode.HTML)
    if set_monitor_log_channel_id(chat_id, new_log_id):
        log_chat_title_html = html.escape(log_chat.title) if log_chat.title else f"<code>{new_log_id}</code>"
        await message.reply_text(f"✅ تم تعيين قناة <b>المراقبة</b> لهذه المجموعة إلى: <b>{log_chat_title_html}</b> (<code>{new_log_id}</code>)", parse_mode=ParseMode.HTML)
        try: await client.send_message(new_log_id, f"✅ تم تعيين هذه القناة كقناة مراقبة للمجموعة: {html.escape(message.chat.title or '')} (<code>{chat_id}</code>)\nبواسطة: {message.from_user.mention(style='html')}", parse_mode=ParseMode.HTML)
        except Exception as e: log.error(f"Failed to send test message to monitor channel {new_log_id}: {e}")
    else: await message.reply_text("❌ فشل حفظ الإعداد في قاعدة البيانات.")

@app.on_message(filters.command("تعيين الادارة", prefixes=[""]) & filters.group)
async def set_report_channel_command(client: Client, message: Message):
    chat_id = message.chat.id; user_id = message.from_user.id
    if not await is_group_owner(client, chat_id, user_id): return await message.reply_text("👮‍♂️ عذراً، هذا الأمر مخصص لـ <b>مالك المجموعة</b> فقط.", parse_mode=ParseMode.HTML)
    if len(message.command) != 2: return await message.reply_text("⚠️ <b>خطأ في الاستخدام!</b>\n<code>تعيين الادارة [ID]</code>", parse_mode=ParseMode.HTML)
    try: new_report_id = int(message.command[1])
    except ValueError: return await message.reply_text("❌ المعرف المدخل غير صالح.")
    if not (-2000000000000 < new_report_id < 0): return await message.reply_text("❌ المعرف المدخل غير صالح. يرجى إدخال معرف مجموعة أو قناة (يبدأ بـ -100 عادةً).")
    try:
        log_chat = await client.get_chat(new_report_id); await client.send_chat_action(new_report_id, ChatAction.TYPING)
    except Exception as e: return await message.reply_text(f"❌ فشل التحقق من القناة/المجموعة <code>{new_report_id}</code>.\nالخطأ: {str(e)}", parse_mode=ParseMode.HTML)
    if set_stats_report_channel_id(chat_id, new_report_id):
        log_chat_title_html = html.escape(log_chat.title) if log_chat.title else f"<code>{new_report_id}</code>"
        await message.reply_text(f"✅ تم تعيين قناة <b>تقارير الإحصائيات</b> لهذه المجموعة إلى: <b>{log_chat_title_html}</b> (<code>{new_report_id}</code>)", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "📊 تعيين قناة تقارير الإحصائيات", message.from_user, None, message.chat, extra_info=f"تم التعيين إلى {log_chat_title_html} (<code>{new_report_id}</code>)")
    else: await message.reply_text("❌ فشل حفظ الإعداد.")

@app.on_message(filters.command("اضافة استثناء", prefixes=[""]) & filters.group)
async def add_exclusion_command(client: Client, message: Message):
    chat_id = message.chat.id; user_id = message.from_user.id
    if not await is_group_owner(client, chat_id, user_id): return await message.reply_text("👮‍♂️ عذراً، هذا الأمر مخصص لـ <b>مالك المجموعة</b> فقط.", parse_mode=ParseMode.HTML)
    if len(message.command) != 2: return await message.reply_text("⚠️ الاستخدام: <code>اضافة استثناء [ID]</code>", parse_mode=ParseMode.HTML)
    try: user_id_to_exclude = int(message.command[1])
    except ValueError: return await message.reply_text("❌ المعرف المدخل غير صالح.")
    if add_excluded_admin_db(chat_id, user_id_to_exclude):
        try: target_user = await client.get_users(user_id_to_exclude)
        except: target_user = None
        target_mention = target_user.mention(style='html') if target_user else f"<code>{user_id_to_exclude}</code>"
        await message.reply_text(f"✅ تم إضافة {target_mention} لقائمة الاستثناء.", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "🚫 إضافة استثناء إحصائيات", message.from_user, target_user, message.chat)
    else: await message.reply_text("❌ فشل إضافة الاستثناء.")

@app.on_message(filters.command("حذف استثناء", prefixes=[""]) & filters.group)
async def remove_exclusion_command(client: Client, message: Message):
    chat_id = message.chat.id; user_id = message.from_user.id
    if not await is_group_owner(client, chat_id, user_id): return await message.reply_text("👮‍♂️ عذراً، هذا الأمر مخصص لـ <b>مالك المجموعة</b> فقط.", parse_mode=ParseMode.HTML)
    if len(message.command) != 2: return await message.reply_text("⚠️ الاستخدام: <code>حذف استثناء [ID]</code>", parse_mode=ParseMode.HTML)
    try: user_id_to_remove = int(message.command[1])
    except ValueError: return await message.reply_text("❌ المعرف المدخل غير صالح.")
    try: target_user = await client.get_users(user_id_to_remove)
    except: target_user = None
    target_mention = target_user.mention(style='html') if target_user else f"<code>{user_id_to_remove}</code>"
    if remove_excluded_admin_db(chat_id, user_id_to_remove):
        await message.reply_text(f"✅ تم حذف {target_mention} من قائمة الاستثناء.", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "✅ حذف استثناء إحصائيات", message.from_user, target_user, message.chat)
    else: await message.reply_text(f"ℹ️ المستخدم {target_mention} ليس في قائمة الاستثناء.", parse_mode=ParseMode.HTML)

@app.on_message(filters.command("قائمة الاستثناء", prefixes=[""]) & filters.group)
async def list_exclusions_command(client: Client, message: Message):
    chat_id = message.chat.id; user_id = message.from_user.id
    if not await is_group_owner(client, chat_id, user_id): return await message.reply_text("👮‍♂️ عذراً، هذا الأمر مخصص لـ <b>مالك المجموعة</b> فقط.", parse_mode=ParseMode.HTML)
    excluded_ids = get_excluded_admin_ids_from_db(chat_id)
    if not excluded_ids: return await message.reply_text("ℹ️ لا يوجد مستخدمين مستثنين لهذه المجموعة.")
    response_text = "🚫 <b>قائمة المستخدمين المستثنين لهذه المجموعة:</b>\n"
    user_cache_stats_v2 = {}
    for ex_user_id in excluded_ids:
        user_display = await get_user_display_name_pyrogram_v2(ex_user_id, chat_id, client)
        response_text += f"- {user_display}\n"
    user_cache_stats_v2 = {}
    await message.reply_html(response_text)

@app.on_message(filters.command(["اضافة مراقبة", "اضف مراقبة"], prefixes=[""]) & filters.group)
async def add_monitor_user_command(client: Client, message: Message):
    chat_id = message.chat.id; user_id = message.from_user.id
    if not await is_group_owner(client, chat_id, user_id): return await message.reply_text("👮‍♂️ عذراً، هذا الأمر مخصص لـ <b>مالك المجموعة</b> فقط.", parse_mode=ParseMode.HTML)
    target_user = await get_target_user(client, message)
    if not target_user: return
    user_id_to_monitor = target_user.id; target_mention = target_user.mention(style="html")
    if add_monitored_user_db(chat_id, user_id_to_monitor):
        await message.reply_text(f"✅ تم إضافة {target_mention} إلى قائمة المراقبة الخاصة بالإحصائيات.", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "⭐ إضافة مراقبة إحصائيات", message.from_user, target_user, message.chat)
    else: await message.reply_text("❌ فشل إضافة المستخدم إلى قائمة المراقبة.")

@app.on_message(filters.command(["حذف مراقبة", "ازالة مراقبة"], prefixes=[""]) & filters.group)
async def remove_monitor_user_command(client: Client, message: Message):
    chat_id = message.chat.id; user_id = message.from_user.id
    if not await is_group_owner(client, chat_id, user_id): return await message.reply_text("👮‍♂️ عذراً، هذا الأمر مخصص لـ <b>مالك المجموعة</b> فقط.", parse_mode=ParseMode.HTML)
    target_user = await get_target_user(client, message)
    if not target_user: return
    user_id_to_remove = target_user.id; target_mention = target_user.mention(style="html")
    if remove_monitored_user_db(chat_id, user_id_to_remove):
        await message.reply_text(f"✅ تم حذف {target_mention} من قائمة المراقبة الخاصة بالإحصائيات.", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "➖ حذف مراقبة إحصائيات", message.from_user, target_user, message.chat)
    else: await message.reply_text(f"ℹ️ المستخدم {target_mention} ليس في قائمة المراقبة أصلاً.", parse_mode=ParseMode.HTML)

@app.on_message(filters.command(["قائمة المراقبة", "المراقبين"], prefixes=[""]) & filters.group)
async def list_monitor_user_command(client: Client, message: Message):
    chat_id = message.chat.id; user_id = message.from_user.id
    if not await is_group_owner(client, chat_id, user_id): return await message.reply_text("👮‍♂️ عذراً، هذا الأمر مخصص لـ <b>مالك المجموعة</b> فقط.", parse_mode=ParseMode.HTML)
    monitored_ids = get_monitored_user_ids_from_db(chat_id)
    if not monitored_ids: return await message.reply_text("ℹ️ لا يوجد مستخدمين مراقبين لهذه المجموعة في الإحصائيات.")
    response_text = "⭐ <b>قائمة المستخدمين المراقبين في الإحصائيات لهذه المجموعة:</b>\n"
    user_cache_stats_v2 = {}
    for mon_user_id in monitored_ids:
        user_display = await get_user_display_name_pyrogram_v2(mon_user_id, chat_id, client)
        response_text += f"- {user_display}\n"
    user_cache_stats_v2 = {}
    await message.reply_html(response_text)

async def send_report_to_channel(client: Client, message: Message, report_type: str, report_text: str | None):
    chat_id = message.chat.id
    report_channel_id = get_stats_report_channel_id(chat_id)
    if not report_channel_id: return await message.reply_text("⚠️ لم يتم تعيين قناة إرسال التقارير لهذه المجموعة. استخدم: <code>تعيين الادارة [ID]</code>", quote=True, parse_mode=ParseMode.HTML)
    if not report_text: return await message.reply_text(f"❌ حدث خطأ أثناء إنشاء تقرير {report_type}.", quote=True)
    try:
        await client.send_message(report_channel_id, report_text, parse_mode=ParseMode.HTML)
        await message.reply_text(f"✅ تم إرسال تقرير {report_type} إلى قناة الإدارة المحددة.", quote=True)
        log.info(f"Sent {report_type} report for chat {chat_id} to channel {report_channel_id}")
    except Exception as e:
        log.error(f"Failed to send {report_type} report for chat {chat_id} to channel {report_channel_id}: {e}")
        await message.reply_text(f"❌ فشل إرسال تقرير {report_type} إلى قناة الإدارة.\nالخطأ: {str(e)}", quote=True)

@app.on_message(filters.command("report", prefixes=["/", "!"]) & filters.group)
async def report_overall_command_pyrogram(client: Client, message: Message):
    if not await is_group_owner(client, message.chat.id, message.from_user.id): return
    log.info(f"Overall report requested by owner {message.from_user.id} in chat {message.chat.id}")
    reply_msg = await message.reply_text("⏳ جارٍ إنشاء التقرير الإجمالي...", quote=True)
    report_text = await format_overall_report_pyrogram(message.chat.id, client)
    try: await reply_msg.delete()
    except: pass
    await send_report_to_channel(client, message, "التقرير الإجمالي", report_text)

@app.on_message(filters.command("اليومي", prefixes=[""]) & filters.group)
async def day_report_command_pyrogram(client: Client, message: Message):
    if not await is_group_owner(client, message.chat.id, message.from_user.id): return
    log.info(f"Detailed current day report requested by owner {message.from_user.id} in chat {message.chat.id}")
    period = get_period_start_end("current_day")
    if period:
        reply_msg = await message.reply_text("⏳ جارٍ إنشاء تقرير اليوم الحالي...", quote=True)
        report_text = await format_detailed_period_report_pyrogram(message.chat.id, "اليوم الحالي", period[0], period[1], client)
        try: await reply_msg.delete()
        except: pass
        await send_report_to_channel(client, message, "اليوم الحالي", report_text)
    else: await message.reply_text("❌ خطأ في حساب فترة اليوم الحالي.")

@app.on_message(filters.command("week", prefixes=["/", "!"]) & filters.group)
async def week_report_command_pyrogram(client: Client, message: Message):
    if not await is_group_owner(client, message.chat.id, message.from_user.id): return
    log.info(f"Detailed current week report requested by owner {message.from_user.id} in chat {message.chat.id}")
    period = get_period_start_end("current_week")
    if period:
        reply_msg = await message.reply_text("⏳ جارٍ إنشاء تقرير الأسبوع الحالي...", quote=True)
        report_text = await format_detailed_period_report_pyrogram(message.chat.id, "الأسبوع الحالي (يبدأ الاثنين)", period[0], period[1], client)
        try: await reply_msg.delete()
        except: pass
        await send_report_to_channel(client, message, "الأسبوع الحالي", report_text)
    else: await message.reply_text("❌ خطأ في حساب فترة الأسبوع الحالي.")

@app.on_message(filters.command("month", prefixes=["/", "!"]) & filters.group)
async def month_report_command_pyrogram(client: Client, message: Message):
    if not await is_group_owner(client, message.chat.id, message.from_user.id): return
    log.info(f"Detailed current month report requested by owner {message.from_user.id} in chat {message.chat.id}")
    period = get_period_start_end("current_month")
    if period:
        reply_msg = await message.reply_text("⏳ جارٍ إنشاء تقرير الشهر الحالي...", quote=True)
        report_text = await format_detailed_period_report_pyrogram(message.chat.id, "الشهر الحالي", period[0], period[1], client)
        try: await reply_msg.delete()
        except: pass
        await send_report_to_channel(client, message, "الشهر الحالي", report_text)
    else: await message.reply_text("❌ خطأ في حساب فترة الشهر الحالي.")

@app.on_message(filters.command(["ق", "قفل الدردشة"], prefixes=[""]) & filters.group, group=1)
async def lock_chat_command(client: Client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    user_mention = message.from_user.mention(style="html")

    if chat_id in mangof:
        return await message.reply_text(f"عذراً سيدي {user_mention}، أوامر القفل والفتح معطلة هنا.", parse_mode=ParseMode.HTML)

    # --- التحقق من الصلاحيات يبقى كما هو ---
    if not await check_tg_restrict_permissions(client, chat_id, user_id):
        return await message.reply_text(f"عذراً يا {user_mention}، هذا الأمر يخص المالك والمشرفين الذين لديهم صلاحية التقييد فقط.", parse_mode=ParseMode.HTML)

    try:
        # --- قم بإزالة أو تعليق هذا الجزء الخاص بتغيير صلاحيات تيليجرام ---
        # current_permissions = (await client.get_chat(chat_id)).permissions
        # await client.set_chat_permissions(chat_id, ChatPermissions(
        #     can_send_messages=False, # <--- السطر الذي يتم إزالته/تعليقه
        #     # ... باقي الصلاحيات تبقى كما هي من current_permissions ...
        #     # مثال:
        #     # can_send_media_messages=current_permissions.can_send_media_messages,
        #     # can_send_polls=current_permissions.can_send_polls,
        #     # can_send_other_messages=current_permissions.can_send_other_messages,
        #     # can_add_web_page_previews=current_permissions.can_add_web_page_previews,
        #     # can_change_info=current_permissions.can_change_info,
        #     # can_invite_users=current_permissions.can_invite_users,
        #     # can_pin_messages=current_permissions.can_pin_messages
        # ))
        # --- النهاية ---

        # --- الاحتفاظ بتحديث قاعدة البيانات ---
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            conn.execute("INSERT OR IGNORE INTO chat_settings (chat_id) VALUES (?)", (chat_id,))
            conn.execute("UPDATE chat_settings SET is_chat_locked = 1 WHERE chat_id = ?", (chat_id,))
            conn.commit()
        # --- النهاية ---

        await message.reply_text(f"🔒 تم تفعيل قفل الحذف التلقائي بواسطة {user_mention}. (الرسائل من غير المستثنين سيتم حذفها)", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "🔒 تفعيل قفل الحذف", message.from_user, None, message.chat)

    # --- تعديل التعامل مع الأخطاء إذا لزم الأمر (قد لا تحتاج لـ ChatNotModified الآن) ---
    # except ChatNotModified:
    #     # تأكد من تحديث قاعدة البيانات حتى لو لم تتغير صلاحيات تيليجرام
    #     with sqlite3.connect(ADMIN_DB_FILE) as conn:
    #         conn.execute("INSERT OR IGNORE INTO chat_settings (chat_id, is_chat_locked) VALUES (?, 1)", (chat_id,))
    #         conn.execute("UPDATE chat_settings SET is_chat_locked = 1 WHERE chat_id = ?", (chat_id,))
    #         conn.commit()
    #     await message.reply_text("ℹ️ قفل الحذف التلقائي مفعل بالفعل.")
    except ChatAdminRequired:
        # هذا الخطأ قد لا يظهر إذا لم نعد نستدعي set_chat_permissions
        await message.reply_text("⚠️ ليس لدي صلاحية تعديل الصلاحيات هنا (قد لا تكون مطلوبة الآن).")
    except Exception as e:
        log.exception(f"Error activating delete lock in {chat_id}: {e}")
        await message.reply_text(f"❌ حدث خطأ أثناء تفعيل قفل الحذف: {str(e)}")


@app.on_message(filters.command(["ف","فتح الدردشة"], prefixes=[""]) & filters.group, group=1)
async def unlock_chat_command(client: Client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    user_mention = message.from_user.mention(style="html")

    if chat_id in mangof:
        return await message.reply_text(f"عذراً سيدي {user_mention}، أوامر القفل والفتح معطلة هنا.", parse_mode=ParseMode.HTML)

    # --- التحقق من الصلاحيات يبقى كما هو ---
    if not await check_tg_restrict_permissions(client, chat_id, user_id):
        return await message.reply_text(f"عذراً يا {user_mention}، هذا الأمر يخص المالك والمشرفين الذين لديهم صلاحية التقييد فقط.", parse_mode=ParseMode.HTML)

    try:
        # --- قم بإزالة أو تعليق هذا الجزء الخاص بتغيير صلاحيات تيليجرام ---
        # current_permissions = (await client.get_chat(chat_id)).permissions
        # await client.set_chat_permissions(chat_id, ChatPermissions(
        #     can_send_messages=True, # <--- السطر الذي يتم إزالته/تعليقه
        #     # ... باقي الصلاحيات تبقى كما هي من current_permissions ...
        # ))
        # --- النهاية ---

        # --- الاحتفاظ بتحديث قاعدة البيانات ---
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            conn.execute("INSERT OR IGNORE INTO chat_settings (chat_id) VALUES (?)", (chat_id,))
            conn.execute("UPDATE chat_settings SET is_chat_locked = 0 WHERE chat_id = ?", (chat_id,))
            conn.commit()
        # --- النهاية ---

        await message.reply_text(f"🔓 تم إلغاء تفعيل قفل الحذف التلقائي بواسطة {user_mention}.", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "🔓 إلغاء قفل الحذف", message.from_user, None, message.chat)

    # --- تعديل التعامل مع الأخطاء إذا لزم الأمر ---
    # except ChatNotModified:
    #     # تأكد من تحديث قاعدة البيانات
    #     with sqlite3.connect(ADMIN_DB_FILE) as conn:
    #          conn.execute("INSERT OR IGNORE INTO chat_settings (chat_id, is_chat_locked) VALUES (?, 0)", (chat_id,))
    #          conn.execute("UPDATE chat_settings SET is_chat_locked = 0 WHERE chat_id = ?", (chat_id,))
    #          conn.commit()
    #     await message.reply_text("ℹ️ قفل الحذف التلقائي غير مفعل بالفعل.")
    except ChatAdminRequired:
        await message.reply_text("⚠️ ليس لدي صلاحية تعديل الصلاحيات هنا (قد لا تكون مطلوبة الآن).")
    except Exception as e:
        log.exception(f"Error deactivating delete lock in {chat_id}: {e}")
        await message.reply_text(f"❌ حدث خطأ أثناء إلغاء تفعيل قفل الحذف: {str(e)}")

@app.on_message(filters.command(["حذف", "مسح"], prefixes=[""]) & filters.group, group=1)
async def delete_messages_command(client: Client, message: Message):
    chat_id = message.chat.id; user_id = message.from_user.id; user_mention = message.from_user.mention(style="html")

    can_tg_delete, is_owner, can_promote = await check_delete_permissions(client, chat_id, user_id)
    is_bot_adm = is_bot_admin(chat_id, user_id)
    is_reply = message.reply_to_message is not None

    count = 1
    is_multi_delete = False
    if len(message.command) > 1:
        try:
            count = int(message.command[1])
            if count < 1: count = 1
            if count > 1: is_multi_delete = True
        except ValueError:
            is_multi_delete = False
            count = 1

    if not can_tg_delete and not (is_bot_adm and is_reply and not is_multi_delete):
         return await message.reply_text(f"👮‍♂️ عذراً يا {user_mention}، هذا الأمر يتطلب صلاحية حذف الرسائل أو رتبة أدمن في البوت (للرد فقط).", parse_mode=ParseMode.HTML)

    if is_multi_delete and not can_tg_delete:
         return await message.reply_text(f"⚠️ عذراً {user_mention}، صلاحياتك تسمح بحذف رسالة واحدة فقط في المرة (عبر الرد).", parse_mode=ParseMode.HTML)

    original_count = count; count = min(count, MAX_DELETE_COUNT)
    target_message_id = message.id; ids_to_delete_additionally = [message.id]
    if message.reply_to_message: target_message_id = message.reply_to_message.id; ids_to_delete_additionally.append(target_message_id); log.info(f"Delete command is a reply to message {target_message_id}. Deleting {count} messages before it.")
    else: log.info(f"Delete command is not a reply. Deleting {count} messages before {message.id}.")

    message_ids_to_delete = []
    try:
        if count > 0 and target_message_id != message.id: # Only fetch history if count > 0 and it's not just deleting the command itself
             async for msg in client.get_chat_history(chat_id, limit=count, offset_id=target_message_id, reverse=False): message_ids_to_delete.append(msg.id)

        message_ids_to_delete.extend(ids_to_delete_additionally); message_ids_to_delete = list(set(message_ids_to_delete))

        if original_count == 1 and not message.reply_to_message: message_ids_to_delete = [message.id]
        elif original_count == 1 and message.reply_to_message: message_ids_to_delete = list(set([message.id, message.reply_to_message.id]))

        deleted_count = 0
        if message_ids_to_delete: deleted_count = await client.delete_messages(chat_id, message_ids_to_delete, revoke=True)
        else: return await message.reply_text("لم أجد رسائل للحذف.")

        conf_msg = await message.reply_text(f"🗑️ تم حذف <b>{deleted_count}</b> من الرسائل بنجاح.", parse_mode=ParseMode.HTML); await asyncio.sleep(5); await conf_msg.delete()
        if deleted_count > 0: await log_admin_action(client, f"🗑️ حذف رسائل ({deleted_count})", message.from_user, None, message.chat)
    except MessageDeleteForbidden: await message.reply_text("⚠️ ليس لدي صلاحية حذف الرسائل هنا.")
    except MessageIdsEmpty: await message.reply_text("⚠️ لم يتم تحديد رسائل صالحة للحذف.")
    except FloodWait as e: log.warning(f"Flood wait ({e.value}s) during message deletion in chat {chat_id}."); await message.reply_text(f"⏳ تم حذف بعض الرسائل، ولكن يجب الانتظار {e.value} ثانية للمتابعة بسبب قيود تيليجرام.")
    except Exception as e: log.exception(f"Error deleting messages in chat {chat_id}: {e}"); await message.reply_text(f"❌ حدث خطأ أثناء حذف الرسائل: {str(e)}")

@app.on_message(filters.command(["رفع مميز", "مميز", "م"], prefixes=[""]) & filters.group, group=1)
async def promote_special_command(client: Client, message: Message):
    chat_id = message.chat.id; user_making_request = message.from_user
    if not await check_bot_admin_permissions(client, chat_id, user_making_request.id): return await message.reply_text(f"عذراً {message.from_user.mention(style='html')}، يجب أن تكون مشرفاً أو أدمن في البوت لرفع الأعضاء المميزين.", parse_mode=ParseMode.HTML)
    target_user = await get_target_user(client, message)
    if not target_user: return
    target_user_id = target_user.id; target_mention = target_user.mention(style="html"); requester_mention = user_making_request.mention(style="html")
    if target_user.is_bot: return await message.reply_text("لا يمكنك رفع بوت كعضو مميز.")
    if target_user_id == user_making_request.id: return await message.reply_text("لا يمكنك رفع نفسك.")
    try:
        target_member = await client.get_chat_member(chat_id, target_user_id)
        if target_member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]: return await message.reply_text("لا يمكنك رفع المالك أو المشرفين كأعضاء مميزين.")
    except UserNotParticipant: pass
    except Exception as e: log.error(f"Error checking target status before promoting special: {e}")
    try:
        with sqlite3.connect(DB_FILE) as conn: conn.execute("INSERT OR REPLACE INTO user_chat_status (chat_id, user_id, status, expiry_timestamp) VALUES (?, ?, 'special', NULL)", (chat_id, target_user_id)); conn.commit()
        await client.restrict_chat_member(chat_id, target_user_id, special_member_permissions)
        log.info(f"User {target_user_id} promoted to special member in chat {chat_id} by {user_making_request.id}")
        reply_msg = f"✨ أهلاً بك {target_mention} في قائمة الأعضاء المميزين للمجموعة!\n يمكنك الآن إرسال الوسائط والملصقات بحرية (ما عدا الروابط).\n\n تم التمييز بواسطة: {requester_mention}"
        await message.reply_text(reply_msg, parse_mode=ParseMode.HTML)
        await log_admin_action(client, "✨ رفع مميز", message.from_user, target_user, message.chat)
    except ChatAdminRequired: await message.reply_text("⚠️ ليس لدي صلاحية تقييد الأعضاء هنا.")
    except RightForbidden:
        await message.reply_text(f"⚠️ لا أملك الصلاحية الكافية لتقييد {target_mention}.", parse_mode=ParseMode.HTML)
        with sqlite3.connect(DB_FILE) as conn: conn.execute("DELETE FROM user_chat_status WHERE chat_id = ? AND user_id = ? AND status = 'special'", (chat_id, target_user_id)); conn.commit(); log.info(f"Rolled back special status for {target_user_id} in chat {chat_id} due to RightForbidden error.")
    except Exception as e: log.exception(f"Error promoting special member {target_user_id} in chat {chat_id}: {e}"); await message.reply_text(f"❌ حدث خطأ أثناء رفع العضو المميز: {str(e)}")

@app.on_message(filters.command("تنزيل مميز", prefixes=[""]) & filters.group, group=1)
async def demote_special_command(client: Client, message: Message):
    chat_id = message.chat.id; user_making_request = message.from_user
    if not await check_bot_admin_permissions(client, chat_id, user_making_request.id): return await message.reply_text(f"عذراً {message.from_user.mention(style='html')}، يجب أن تكون مشرفاً أو أدمن في البوت لتنزيل الأعضاء المميزين.", parse_mode=ParseMode.HTML)
    target_user = await get_target_user(client, message)
    if not target_user: return
    target_user_id = target_user.id; target_mention = target_user.mention(style="html")
    try:
        rows_deleted = 0
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor(); cursor.execute("DELETE FROM user_chat_status WHERE chat_id = ? AND user_id = ? AND status = 'special'", (chat_id, target_user_id)); rows_deleted = cursor.rowcount; conn.commit()
        if rows_deleted == 0: return await message.reply_text("ℹ️ هذا المستخدم ليس عضواً مميزاً أصلاً.")
        await client.restrict_chat_member(chat_id, target_user_id, regular_member_permissions)
        log.info(f"User {target_user_id} demoted from special member in chat {chat_id} by {user_making_request.id}")
        await message.reply_text(f"✅ تم تنزيل {target_mention} من قائمة الأعضاء المميزين.", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "✅ تنزيل مميز", message.from_user, target_user, message.chat)
    except ChatAdminRequired: await message.reply_text("⚠️ ليس لدي صلاحية تقييد الأعضاء هنا.")
    except RightForbidden: await message.reply_text(f"⚠️ لا أملك الصلاحية الكافية لإلغاء تقييد {target_mention}.", parse_mode=ParseMode.HTML)
    except Exception as e: log.exception(f"Error demoting special member {target_user_id} in chat {chat_id}: {e}"); await message.reply_text(f"❌ حدث خطأ أثناء تنزيل العضو المميز: {str(e)}")

@app.on_message(filters.command("حظر", prefixes=[""]) & filters.group, group=1)
async def ban_command(client: Client, message: Message):
    chat_id = message.chat.id; user_making_request = message.from_user
    if not await check_tg_restrict_permissions(client, chat_id, user_making_request.id): return await message.reply_text(f"عذراً {message.from_user.mention(style='html')}، يجب أن تملك صلاحية تقييد الأعضاء لاستخدام هذا الأمر.", parse_mode=ParseMode.HTML)
    target_user = await get_target_user(client, message)
    if not target_user: return
    target_user_id = target_user.id; target_mention = target_user.mention(style="html")
    if target_user_id == user_making_request.id: return await message.reply_text("لا يمكنك حظر نفسك!")
    if target_user.is_bot and hasattr(client, 'me') and client.me and target_user.id == client.me.id: return await message.reply_text("لا أستطيع حظر نفسي.")
    try:
        target_member = await client.get_chat_member(chat_id, target_user_id)
        if target_member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]: return await message.reply_text(f"لا يمكنك حظر المالك أو المشرفين {target_mention}.", parse_mode=ParseMode.HTML)
    except UserNotParticipant: pass
    except Exception as e: log.error(f"Error checking target status before ban: {e}")
    try:
        await client.ban_chat_member(chat_id, target_user_id)
        log.info(f"User {target_user_id} banned in chat {chat_id} by {user_making_request.id}")
        await message.reply_text(f"🚫 تم حظر {target_mention} من المجموعة بنجاح.", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "🚫 حظر", message.from_user, target_user, message.chat)
        add_admin_action_db_v2(chat_id, 'ban', target_user_id, user_making_request.id)
    except ChatAdminRequired: await message.reply_text("⚠️ ليس لدي صلاحية حظر الأعضاء هنا.")
    except RightForbidden: await message.reply_text(f"⚠️ لا أملك الصلاحية الكافية لحظر هذا المستخدم ({target_mention}). قد يكون مشرفاً آخر.", parse_mode=ParseMode.HTML)
    except Exception as e: log.exception(f"Error banning user {target_user_id} in chat {chat_id}: {e}"); await message.reply_text(f"❌ حدث خطأ أثناء حظر المستخدم: {str(e)}")

@app.on_message(filters.command(["الغاء الحظر", "الغاء حظر"], prefixes=[""]) & filters.group, group=1)
async def unban_command(client: Client, message: Message):
    chat_id = message.chat.id; user_making_request = message.from_user
    if not await check_tg_restrict_permissions(client, chat_id, user_making_request.id): return await message.reply_text(f"عذراً {message.from_user.mention(style='html')}، يجب أن تملك صلاحية تقييد الأعضاء لاستخدام هذا الأمر.", parse_mode=ParseMode.HTML)
    target_user = await get_target_user(client, message)
    if not target_user: return
    target_user_id = target_user.id; target_mention = target_user.mention(style="html")
    try:
        await client.unban_chat_member(chat_id, target_user_id)
        log.info(f"User {target_user_id} unbanned in chat {chat_id} by {user_making_request.id}")
        await message.reply_text(f"✅ تم إلغاء حظر {target_mention} بنجاح.", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "✅ الغاء حظر", message.from_user, target_user, message.chat)
    except ChatAdminRequired: await message.reply_text("⚠️ ليس لدي صلاحية إلغاء حظر الأعضاء هنا.")
    except RightForbidden: await message.reply_text(f"⚠️ لا أملك الصلاحية الكافية لإلغاء حظر هذا المستخدم ({target_mention}).", parse_mode=ParseMode.HTML)
    except Exception as e: log.exception(f"Error unbanning user {target_user_id} in chat {chat_id}: {e}"); await message.reply_text(f"❌ حدث خطأ أثناء إلغاء حظر المستخدم: {str(e)}")

@app.on_message(filters.command("تقييد", prefixes=[""]) & filters.group, group=1)
async def restrict_command(client: Client, message: Message):
    chat_id = message.chat.id; user_making_request = message.from_user
    if not await check_bot_admin_permissions(client, chat_id, user_making_request.id): return await message.reply_text(f"عذراً {message.from_user.mention(style='html')}، يجب أن تكون مشرفاً أو أدمن في البوت لاستخدام هذا الأمر.", parse_mode=ParseMode.HTML)
    target_user = await get_target_user(client, message)
    if not target_user: return
    target_user_id = target_user.id; target_mention = target_user.mention(style="html")
    if target_user_id == user_making_request.id: return await message.reply_text("لا يمكنك تقييد نفسك!")
    if target_user.is_bot and hasattr(client, 'me') and client.me and target_user.id == client.me.id: return await message.reply_text("لا أستطيع تقييد نفسي.")
    try:
        target_member = await client.get_chat_member(chat_id, target_user_id)
        if target_member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]: return await message.reply_text(f"لا يمكنك تقييد المالك أو المشرفين {target_mention}.", parse_mode=ParseMode.HTML)
    except UserNotParticipant: pass
    except Exception as e: log.error(f"Error checking target status before restrict: {e}")
    try:
        await client.restrict_chat_member(chat_id, target_user_id, restricted_permissions)
        log.info(f"User {target_user_id} restricted in chat {chat_id} by {user_making_request.id}")
        await message.reply_text(f"🔗 تم تقييد {target_mention} بنجاح.", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "🔗 تقييد", message.from_user, target_user, message.chat)
        add_admin_action_db_v2(chat_id, 'mute', target_user_id, user_making_request.id) # Consider restrict as a type of mute for stats
    except ChatAdminRequired: await message.reply_text("⚠️ ليس لدي صلاحية تقييد الأعضاء هنا.")
    except RightForbidden: await message.reply_text(f"⚠️ لا أملك الصلاحية الكافية لتقييد هذا المستخدم ({target_mention}).", parse_mode=ParseMode.HTML)
    except Exception as e: log.exception(f"Error restricting user {target_user_id} in chat {chat_id}: {e}"); await message.reply_text(f"❌ حدث خطأ أثناء تقييد المستخدم: {str(e)}")

@app.on_message(filters.command("الغاء تقييد", prefixes=[""]) & filters.group, group=1)
async def unrestrict_command(client: Client, message: Message):
    chat_id = message.chat.id; user_making_request = message.from_user
    if not await check_bot_admin_permissions(client, chat_id, user_making_request.id): return await message.reply_text(f"عذراً {message.from_user.mention(style='html')}، يجب أن تكون مشرفاً أو أدمن في البوت لاستخدام هذا الأمر.", parse_mode=ParseMode.HTML)
    target_user = await get_target_user(client, message)
    if not target_user: return
    target_user_id = target_user.id; target_mention = target_user.mention(style="html")
    try:
        await client.restrict_chat_member(chat_id, target_user_id, regular_member_permissions)
        log.info(f"User {target_user_id} unrestricted in chat {chat_id} by {user_making_request.id}")
        await message.reply_text(f"✅ تم إلغاء تقييد {target_mention} بنجاح.", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "✅ الغاء تقييد", message.from_user, target_user, message.chat)
    except ChatAdminRequired: await message.reply_text("⚠️ ليس لدي صلاحية تقييد الأعضاء هنا.")
    except RightForbidden: await message.reply_text(f"⚠️ لا أملك الصلاحية الكافية لإلغاء تقييد هذا المستخدم ({target_mention}).", parse_mode=ParseMode.HTML)
    except Exception as e: log.exception(f"Error unrestricting user {target_user_id} in chat {chat_id}: {e}"); await message.reply_text(f"❌ حدث خطأ أثناء إلغاء تقييد المستخدم: {str(e)}")

@app.on_message(filters.command(["كتم", "ت"], prefixes=[""]) & filters.group, group=1)
async def mute_command(client: Client, message: Message):
    chat_id = message.chat.id; user_making_request = message.from_user
    if not await check_bot_admin_permissions(client, chat_id, user_making_request.id): return await message.reply_text(f"عذراً {message.from_user.mention(style='html')}، يجب أن تكون مشرفاً أو أدمن في البوت لاستخدام هذا الأمر.", parse_mode=ParseMode.HTML)
    target_user = await get_target_user(client, message)
    if not target_user: return
    target_user_id = target_user.id; target_mention = target_user.mention(style="html")
    if target_user_id == user_making_request.id: return await message.reply_text("لا يمكنك كتم نفسك!")
    if target_user.is_bot and hasattr(client, 'me') and client.me and target_user.id == client.me.id: return await message.reply_text("لا أستطيع كتم نفسي.")
    try:
        target_member = await client.get_chat_member(chat_id, target_user_id)
        if target_member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]: return await message.reply_text(f"لا يمكنك كتم المالك أو المشرفين {target_mention}.", parse_mode=ParseMode.HTML)
    except UserNotParticipant: pass
    except Exception as e: log.error(f"Error checking target status before mute: {e}")
    duration_days = DEFAULT_MUTE_DAYS; args = message.command
    if len(args) > 2:
        try: duration_input = int(args[2]); duration_days = max(1, duration_input)
        except ValueError: await message.reply_text(f"⚠️ مدة الكتم غير صالحة. تم استخدام الافتراضي: {DEFAULT_MUTE_DAYS} أيام.")
    expiry_timestamp = int(time.time()) + (duration_days * 86400)
    try:
        with sqlite3.connect(DB_FILE) as conn: conn.execute("INSERT OR REPLACE INTO user_chat_status (chat_id, user_id, status, expiry_timestamp) VALUES (?, ?, 'muted', ?)", (chat_id, target_user_id, expiry_timestamp)); conn.commit()
        log.info(f"User {target_user_id} muted in chat {chat_id} by {user_making_request.id} for {duration_days} days.")
        await message.reply_text(f"🔇 تم كتم {target_mention} لمدة <b>{duration_days}</b> أيام .", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "🔇 كتم", message.from_user, target_user, message.chat, duration_days=duration_days)
        add_admin_action_db_v2(chat_id, 'mute', target_user_id, user_making_request.id)
    except sqlite3.Error as db_err: log.exception(f"DB error muting user {target_user_id} in chat {chat_id}: {db_err}"); await message.reply_text("❌ حدث خطأ في قاعدة البيانات أثناء كتم المستخدم.")
    except Exception as e: log.exception(f"Error muting user {target_user_id} in chat {chat_id}: {e}"); await message.reply_text(f"❌ حدث خطأ أثناء كتم المستخدم: {str(e)}")

@app.on_message(filters.command("الغاء الكتم", prefixes=[""]) & filters.group, group=1)
async def unmute_command(client: Client, message: Message):
    chat_id = message.chat.id; user_making_request = message.from_user
    if not await check_bot_admin_permissions(client, chat_id, user_making_request.id): return await message.reply_text(f"عذراً {message.from_user.mention(style='html')}، يجب أن تكون مشرفاً أو أدمن في البوت لاستخدام هذا الأمر.", parse_mode=ParseMode.HTML)
    target_user = await get_target_user(client, message)
    if not target_user: return
    target_user_id = target_user.id; target_mention = target_user.mention(style="html")
    try:
        rows_deleted = 0
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor(); cursor.execute("DELETE FROM user_chat_status WHERE chat_id = ? AND user_id = ? AND status = 'muted'", (chat_id, target_user_id)); rows_deleted = cursor.rowcount; conn.commit()
        if rows_deleted == 0: return await message.reply_text("ℹ️ هذا المستخدم غير مكتوم أصلاً.")
        log.info(f"User {target_user_id} unmuted in chat {chat_id} by {user_making_request.id}")
        await message.reply_text(f"🔊 تم إلغاء كتم {target_mention} بنجاح.", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "🔊 الغاء كتم", message.from_user, target_user, message.chat)
    except sqlite3.Error as db_err: log.exception(f"DB error unmuting user {target_user_id} in chat {chat_id}: {db_err}"); await message.reply_text("❌ حدث خطأ في قاعدة البيانات أثناء إلغاء الكتم.")
    except Exception as e: log.exception(f"Error unmuting user {target_user_id} in chat {chat_id}: {e}"); await message.reply_text(f"❌ حدث خطأ أثناء إلغاء كتم المستخدم: {str(e)}")

@app.on_message(filters.command("رفع ادمن", prefixes=[""]) & filters.group, group=1)
async def promote_admin_command(client: Client, message: Message):
    chat_id = message.chat.id; user_making_request = message.from_user
    if not await check_tg_promote_permissions(client, chat_id, user_making_request.id):
        return await message.reply_text(f"👮‍♂️ عذراً {message.from_user.mention(style='html')}، يجب أن تملك صلاحية رفع المشرفين لاستخدام هذا الأمر.", parse_mode=ParseMode.HTML)
    target_user = await get_target_user(client, message)
    if not target_user: return
    target_user_id = target_user.id
    if target_user.is_bot: return await message.reply_text("ℹ️ لا يمكنك رفع بوت كأدمن.")
    if target_user_id == user_making_request.id: return await message.reply_text("ℹ️ لا يمكنك رفع نفسك.")
    try:
        target_member = await client.get_chat_member(chat_id, target_user_id)
        if target_member.status == ChatMemberStatus.OWNER: return await message.reply_text("🛡️ لا يمكنك تغيير صلاحيات مالك المجموعة.")
    except UserNotParticipant: return await message.reply_text("ℹ️ لا يمكن رفع شخص غير موجود في المجموعة.")
    except Exception as e: log.error(f"Error checking target status before promoting bot admin: {e}")
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("INSERT OR REPLACE INTO user_chat_status (chat_id, user_id, status, expiry_timestamp) VALUES (?, ?, 'admin', NULL)", (chat_id, target_user_id))
            conn.commit()
        log.info(f"User {target_user_id} promoted to bot admin in chat {chat_id} by {user_making_request.id}")
        await message.reply_text(f"👮‍♀️ تم رفع {target_user.mention(style='html')} إلى رتبة <b>ادمن في البوت</b>.", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "👮‍♀️ رفع ادمن بوت", message.from_user, target_user, message.chat)
    except sqlite3.Error as db_err:
        log.exception(f"DB error promoting bot admin {target_user_id} in chat {chat_id}: {db_err}")
        await message.reply_text("❌ حدث خطأ في قاعدة البيانات أثناء رفع الأدمن.")
    except Exception as e:
        log.exception(f"Error promoting bot admin {target_user_id} in chat {chat_id}: {e}")
        await message.reply_text(f"❌ حدث خطأ أثناء رفع الأدمن: {str(e)}")

@app.on_message(filters.command("تنزيل ادمن", prefixes=[""]) & filters.group, group=1)
async def demote_admin_command(client: Client, message: Message):
    chat_id = message.chat.id; user_making_request = message.from_user
    if not await check_tg_promote_permissions(client, chat_id, user_making_request.id):
        return await message.reply_text(f"👮‍♂️ عذراً {message.from_user.mention(style='html')}، يجب أن تملك صلاحية رفع المشرفين لاستخدام هذا الأمر.", parse_mode=ParseMode.HTML)
    target_user = await get_target_user(client, message)
    if not target_user: return
    target_user_id = target_user.id
    try:
        rows_deleted = 0
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_chat_status WHERE chat_id = ? AND user_id = ? AND status = 'admin'", (chat_id, target_user_id))
            rows_deleted = cursor.rowcount
            conn.commit()
        if rows_deleted == 0:
            try:
                target_member = await client.get_chat_member(chat_id, target_user_id)
                if target_member.status == ChatMemberStatus.ADMINISTRATOR: return await message.reply_text("ℹ️ هذا المستخدم مشرف      .")
                elif target_member.status == ChatMemberStatus.OWNER: return await message.reply_text("🛡️ لا يمكنك تنزيل مالك المجموعة.")
                else: return await message.reply_text("ℹ️ هذا المستخدم ليس أدمن في البوت.")
            except UserNotParticipant: return await message.reply_text("ℹ️ المستخدم ليس عضواً في المجموعة أصلاً.")
            except Exception: return await message.reply_text("ℹ️ هذا المستخدم ليس أدمن في البوت.")
        log.info(f"User {target_user_id} demoted from bot admin in chat {chat_id} by {user_making_request.id}")
        await message.reply_text(f"⬇️ تم تنزيل {target_user.mention(style='html')} من رتبة ادمن البوت بنجاح.", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "⬇️ تنزيل ادمن بوت", message.from_user, target_user, message.chat)
    except sqlite3.Error as db_err:
        log.exception(f"DB error demoting bot admin {target_user_id} in chat {chat_id}: {db_err}")
        await message.reply_text("❌ حدث خطأ في قاعدة البيانات أثناء تنزيل الأدمن.")
    except Exception as e:
        log.exception(f"Error demoting bot admin {target_user_id} in chat {chat_id}: {e}")
        await message.reply_text(f"❌ حدث خطأ أثناء تنزيل الأدمن: {str(e)}")

@app.on_message(filters.group & ~filters.service & ~filters.me, group=0)
async def delete_msg_in_locked_chat_handler(client: Client, message: Message):
    if not message.from_user: raise ContinuePropagation
    chat_id = message.chat.id; user_id = message.from_user.id
    is_locked = False
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor(); cursor.execute("SELECT is_chat_locked FROM chat_settings WHERE chat_id = ?", (chat_id,)); result = cursor.fetchone()
            if result and result[0] == 1: is_locked = True
    except sqlite3.Error as e: log.exception(f"[DB:{ADMIN_DB_FILE}] Error checking lock status for chat {chat_id}: {e}"); raise ContinuePropagation
    if not is_locked: raise ContinuePropagation
    if await is_exempt_from_lock(client, chat_id, user_id): raise ContinuePropagation
    try: await message.delete(); log.info(f"Deleted message {message.id} from non-exempt user {user_id} in locked chat {chat_id}")
    except MessageDeleteForbidden: pass
    except Exception as e: log.exception(f"Error deleting message {message.id} in locked chat {chat_id}: {e}"); raise ContinuePropagation


@app.on_message(filters.group & ~filters.service & ~filters.me, group=0)
async def handle_muted_user_messages_handler(client: Client, message: Message):
    if not message.from_user: raise ContinuePropagation
    chat_id = message.chat.id; user_id = message.from_user.id
    is_muted, expiry_ts = get_user_mute_status(chat_id, user_id)
    if is_muted:
        try: await message.delete(); log.info(f"Deleted message {message.id} from muted user {user_id} in chat {chat_id}")
        except MessageDeleteForbidden: raise ContinuePropagation
        except Exception as e: log.exception(f"Error deleting message {message.id} from muted user {user_id} in chat {chat_id}: {e}"); raise ContinuePropagation
    else: raise ContinuePropagation

@app.on_chat_member_updated(filters.group, group=9)
async def log_member_updates_handler(client: Client, update: ChatMemberUpdated):
    chat = update.chat; user = update.new_chat_member.user if update.new_chat_member else update.old_chat_member.user; actor = update.from_user
    if actor and hasattr(client, 'me') and client.me and actor.id == client.me.id: return
    if hasattr(client, 'me') and client.me and user.id == client.me.id: return
    monitor_channel_id = get_monitor_log_channel_id(chat.id)
    if not monitor_channel_id: return
    log_message = ""; now_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
    chat_title_html = html.escape(chat.title) if chat.title else "<i>اسم غير معروف</i>"
    chat_link = f"<a href='https://t.me/{chat.username}'>{chat_title_html}</a>" if chat.username else chat_title_html
    user_first_name_html = html.escape(user.first_name) if user.first_name else ""
    user_link = f"<a href='tg://user?id={user.id}'>{user_first_name_html}</a> (<code>{user.id}</code>)"
    actor_first_name_html = html.escape(actor.first_name) if actor and actor.first_name else ""
    actor_link = f"<a href='tg://user?id={actor.id}'>{actor_first_name_html}</a> (<code>{actor.id}</code>)" if actor else "<i>النظام</i>"
    old_status = update.old_chat_member.status if update.old_chat_member else None
    new_status = update.new_chat_member.status if update.new_chat_member else None
    log_header = "📝 <b>السجلات</b> 📝\n\n"; event_type_str = ""; details = ""
    if (not old_status or old_status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]) and new_status == ChatMemberStatus.MEMBER:
        if actor and actor.id != user.id: event_type_str = "✅ إضافة عضو"; details = f"👤 <b>العضو:</b> {user_link}\n➕ <b>أضيف بواسطة:</b> {actor_link}\n"
        else: event_type_str = "✅ انضمام عضو"; details = f"👤 <b>العضو:</b> {user_link}\n"
        details += f"🏠 <b>المجموعة:</b> {chat_link} (<code>{chat.id}</code>)\n⏰ <b>الوقت:</b> {now_time}"
    elif old_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER] and new_status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
        kicker_actor = update.new_chat_member.restricted_by if update.new_chat_member and update.new_chat_member.restricted_by else actor
        kicker_name_html = html.escape(kicker_actor.first_name) if kicker_actor and kicker_actor.first_name else "غير معروف"
        kicker_link = f"<a href='tg://user?id={kicker_actor.id}'>{kicker_name_html}</a> (<code>{kicker_actor.id}</code>)" if kicker_actor else "<i>النظام</i>"
        if new_status == ChatMemberStatus.LEFT: event_type_str = "❌ مغادرة عضو"; details = f"👤 <b>العضو:</b> {user_link}\n"
        elif new_status == ChatMemberStatus.BANNED: event_type_str = "🚫 حظر/طرد عضو"; details = f"👤 <b>العضو:</b> {user_link}\n👮‍♂️ <b>بواسطة:</b> {kicker_link}\n"
        details += f"🏠 <b>المجموعة:</b> {chat_link} (<code>{chat.id}</code>)\n⏰ <b>الوقت:</b> {now_time}"
    elif new_status == ChatMemberStatus.RESTRICTED:
        old_perms = update.old_chat_member.permissions if update.old_chat_member else None; new_perms = update.new_chat_member.permissions if update.new_chat_member else None
        if old_status != ChatMemberStatus.RESTRICTED or old_perms != new_perms:
            restricter_actor = update.new_chat_member.restricted_by if update.new_chat_member and update.new_chat_member.restricted_by else actor
            restricter_name_html = html.escape(restricter_actor.first_name) if restricter_actor and restricter_actor.first_name else "النظام"
            restricter_link = f"<a href='tg://user?id={restricter_actor.id}'>{restricter_name_html}</a> (<code>{restricter_actor.id}</code>)" if restricter_actor else "<i>النظام</i>"
            action_type = "تقييد"; until_date_str = ""
            if update.new_chat_member and update.new_chat_member.until_date:
                try:
                    if update.new_chat_member.until_date > (time.time() + 31536000 * 10): until_date_str = "\n⏳ <b>المدة:</b> دائم"
                    else: until_dt = datetime.fromtimestamp(update.new_chat_member.until_date, timezone.utc); until_date_str = f"\n⏳ <b>حتى:</b> {until_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}"
                except: pass
            if isinstance(new_perms, ChatPermissions) and not new_perms.can_send_messages and not new_perms.can_send_media_messages: action_type = "كتم"
            event_type_str = f"🔇 {action_type} عضو"
            details = f"👤 <b>العضو:</b> {user_link}\n👮‍♂️ <b>بواسطة:</b> {restricter_link}\n🏠 <b>المجموعة:</b> {chat_link} (<code>{chat.id}</code>){until_date_str}\n⏰ <b>الوقت:</b> {now_time}"
    elif old_status in [ChatMemberStatus.BANNED, ChatMemberStatus.RESTRICTED] and new_status == ChatMemberStatus.MEMBER:
         unbanner_actor = actor; unbanner_name_html = html.escape(unbanner_actor.first_name) if unbanner_actor and unbanner_actor.first_name else "النظام"
         unbanner_link = f"<a href='tg://user?id={unbanner_actor.id}'>{unbanner_name_html}</a> (<code>{unbanner_actor.id}</code>)" if unbanner_actor else "<i>النظام</i>"
         action_type = "فك الحظر عن" if old_status == ChatMemberStatus.BANNED else "إلغاء تقييد"
         event_type_str = f"🔓 {action_type} عضو"
         details = f"👤 <b>العضو:</b> {user_link}\n👮‍♂️ <b>بواسطة:</b> {unbanner_link}\n🏠 <b>المجموعة:</b> {chat_link} (<code>{chat.id}</code>)\n⏰ <b>الوقت:</b> {now_time}"
    elif new_status == ChatMemberStatus.ADMINISTRATOR and old_status != ChatMemberStatus.ADMINISTRATOR:
        promoter_actor = update.new_chat_member.promoted_by if update.new_chat_member and update.new_chat_member.promoted_by else actor
        promoter_name_html = html.escape(promoter_actor.first_name) if promoter_actor and promoter_actor.first_name else "النظام"
        promoter_link = f"<a href='tg://user?id={promoter_actor.id}'>{promoter_name_html}</a> (<code>{promoter_actor.id}</code>)" if promoter_actor else "<i>النظام</i>"
        event_type_str = "⬆️ ترقية مشرف"
        details = f"👤 <b>المشرف الجديد:</b> {user_link}\n👮‍♂️ <b>بواسطة:</b> {promoter_link}\n🏠 <b>المجموعة:</b> {chat_link} (<code>{chat.id}</code>)\n⏰ <b>الوقت:</b> {now_time}"
    elif old_status == ChatMemberStatus.ADMINISTRATOR and new_status != ChatMemberStatus.ADMINISTRATOR:
         demoter_actor = actor; demoter_name_html = html.escape(demoter_actor.first_name) if demoter_actor and demoter_actor.first_name else "النظام"
         demoter_link = f"<a href='tg://user?id={demoter_actor.id}'>{demoter_name_html}</a> (<code>{demoter_actor.id}</code>)" if demoter_actor else "<i>النظام</i>"
         event_type_str = "⬇️ تنزيل مشرف"
         details = f"👤 <b>المشرف السابق:</b> {user_link}\n👮‍♂️ <b>بواسطة:</b> {demoter_link}\n🏠 <b>المجموعة:</b> {chat_link} (<code>{chat.id}</code>)\n⏰ <b>الوقت:</b> {now_time}"
    if event_type_str and details:
        log_message = log_header + f"<b>النوع:</b> {event_type_str}\n" + details
        try: await client.send_message(chat_id=monitor_channel_id, text=log_message, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except (PeerIdInvalid, ChannelPrivate): log.error(f"Monitor log channel ID {monitor_channel_id} (for source chat {chat.id}) is invalid or private.")
        except ChatAdminRequired: log.error(f"Bot lacks permission to send messages in the monitor log channel {monitor_channel_id} (for source chat {chat.id}).")
        except FloodWait as e: log.warning(f"Flood wait of {e.value} seconds when logging member event '{event_type_str}' to {monitor_channel_id}"); await asyncio.sleep(e.value + 1)
        except Exception as e: log.exception(f"Failed to send member event log '{event_type_str}' to monitor channel {monitor_channel_id} for source chat {chat.id}")

@app.on_message(filters.group & ~filters.service & ~filters.command("any"), group=12)
async def count_message_v2_handler(client: Client, message: Message):
    if message.from_user:
        excluded_ids = get_excluded_admin_ids_from_db(message.chat.id)
        if message.from_user.id not in excluded_ids:
             add_message_db_v2(message.from_user.id, message.chat.id)

@app.on_chat_member_updated(filters.group, group=8)
async def track_actions_v2_handler(client: Client, update: ChatMemberUpdated):
    chat = update.chat
    if not update.new_chat_member or not update.new_chat_member.user: return
    user = update.new_chat_member.user; new_status = update.new_chat_member.status; old_status = update.old_chat_member.status if update.old_chat_member else None; actor = update.from_user
    if actor and actor.is_self: return
    actor_id = actor.id if actor else None
    is_ban = new_status == ChatMemberStatus.BANNED and old_status != ChatMemberStatus.BANNED
    is_restriction_change = new_status == ChatMemberStatus.RESTRICTED and (old_status != ChatMemberStatus.RESTRICTED or (update.old_chat_member and update.new_chat_member and update.old_chat_member.permissions != update.new_chat_member.permissions))
    if is_ban: log.info(f"Stats Reporter V2: User {user.id} was banned in chat {chat.id}"); add_admin_action_db_v2(chat.id, 'ban', user.id, actor_id)
    elif is_restriction_change:
        perms_obj = update.new_chat_member.permissions
        if isinstance(perms_obj, ChatPermissions) and not getattr(perms_obj, 'can_send_messages', True) and getattr(perms_obj, 'can_send_media_messages', True) is False:
             log.info(f"Stats Reporter V2: User {user.id} was muted in chat {chat.id}"); add_admin_action_db_v2(chat.id, 'mute', user.id, actor_id)
        else: log.info(f"Stats Reporter V2: User {user.id} was restricted (not a full mute) in chat {chat.id}")

log.info("Combined Management, Logger, Stats V2, and Bot Admin Plugin loaded successfully.")


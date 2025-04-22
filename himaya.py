import datetime
import re
import time
import asyncio
import os
import logging
import sqlite3
import html
from datetime import datetime, timedelta, timezone

# --- Configure Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

from pyrogram import filters, Client
from pyrogram import ContinuePropagation
from pyrogram.enums import UserStatus, ParseMode, ChatMemberStatus, ChatType, MessageEntityType
from pyrogram.types.messages_and_media.message_origin import MessageOrigin
from pyrogram.errors import (
    PeerIdInvalid, FloodWait, UserIsBlocked, ChatAdminRequired, ChatNotModified,
    MessageDeleteForbidden, MessageIdsEmpty, UserNotParticipant, ChannelPrivate,
    ChatForwardsRestricted, RightForbidden, UserAdminInvalid, ChatWriteForbidden, UserIsBot,
    BadRequest
)
from pyrogram.types import (
    Message, User, Chat, ChatMemberUpdated, ChatPrivileges, ChatPermissions, ChatMember,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    MessageOriginUser, MessageOriginHiddenUser, MessageOriginChannel, MessageOriginChat
)

# --- App Instance Handling ---
try:
    from YukkiMusic import app as yukki_app
    log.info("Protection Plugin: Imported YukkiMusic app.")
    app = yukki_app
except ImportError:
    log.error("Protection Plugin: Could not import YukkiMusic app! Plugin may not function correctly.")
    class DummyApp:
        def on_message(self, *args, **kwargs): return lambda f: f
        def on_callback_query(self, *args, **kwargs): return lambda f: f
        def on_chat_member_updated(self, *args, **kwargs): return lambda f: f
        def on_edited_message(self, *args, **kwargs): return lambda f: f
    app = DummyApp()


# --- Configuration ---
DB_FILE = "user_stats.db"
ADMIN_DB_FILE = "admin_actions.db"
DEFAULT_MAX_MESSAGE_LENGTH = -1 # Default: No limit for long messages

# --- Constants for Protection ---
LOCK_TYPES = {
    "photo": "الصور 🖼️",
    "video": "الفيديو 🎬",
    "link": "الروابط 🔗",
    "mention": "المعرفات  👤",
    "sticker": "الملصقات ✨",
    "gif": "المتحركة 💫",
    "voice": "الصوتيات 🎤",
    "audio": "الموسيقى 🎵",
    "document": "الملفات 📄",
    "contact": "جهات الاتصال 📞",
    "game": "الألعاب 🎮",
    "location": "المواقع 📍",
    "poll": "الاستفتاءات 📊",
    "dice": "الرموز المتحركة 🎲",
    "bots": "البوتات  🤖",
    "english": "الإنجليزية",
    "inline": "الأزرار الشفافة",
    "markdown": "الماركدوان",
    "spoiler_media": "الوسائط المشوشة 🤫",
    "spoiler_text": "النص المشوش 🤫",
    "edit": "تعديل الرسائل 📝",
    "blockquote": "الاقتباس  💬",
    "long_text": "الرسائل الطويلة 📏", # Now configurable per chat
    "swear": "الكلمات المحظورة",
}

ACTIONS = {
    "delete": "حذف 🗑️",
    "mute": "كتم 🔇",
    "ban": "حظر 🚫",
    "disabled": "تعطيل/سماح 🔓"
}

DEFAULT_MUTE_DAYS = 1
EDIT_LOCK_DELAY_SECONDS = 60

# --- Database Initialization ---
def init_protection_db():
    """Initializes tables and adds new columns if they don't exist."""
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS protection_settings (
                chat_id INTEGER NOT NULL,
                lock_type TEXT NOT NULL,
                action TEXT NOT NULL DEFAULT 'disabled',
                PRIMARY KEY (chat_id, lock_type)
            )
            ''')
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS banned_words (
                chat_id INTEGER NOT NULL,
                word TEXT NOT NULL,
                PRIMARY KEY (chat_id, word)
            )
            ''')
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS allowed_forward_sources (
                chat_id INTEGER NOT NULL,
                source_id INTEGER NOT NULL,
                PRIMARY KEY (chat_id, source_id)
            )
            ''')
            # Ensure chat_settings table exists (might be created elsewhere)
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY
            )
            ''')
            # Add columns if they don't exist
            try:
                cursor.execute("ALTER TABLE chat_settings ADD COLUMN protection_enabled INTEGER DEFAULT 1")
            except sqlite3.OperationalError: pass
            try:
                cursor.execute("ALTER TABLE chat_settings ADD COLUMN is_forward_locked INTEGER DEFAULT 0")
            except sqlite3.OperationalError: pass
            try:
                # Add max_message_length column with default -1 (no limit)
                cursor.execute(f"ALTER TABLE chat_settings ADD COLUMN max_message_length INTEGER DEFAULT {DEFAULT_MAX_MESSAGE_LENGTH}")
            except sqlite3.OperationalError: pass

            # Initialize default lock settings
            cursor.execute("SELECT chat_id FROM chat_settings")
            existing_chats = [row[0] for row in cursor.fetchall()]
            default_settings = []
            known_lock_types = LOCK_TYPES.keys()
            for chat_id_db in existing_chats:
                for lock_type in [lt for lt in known_lock_types if lt != 'swear']:
                    default_settings.append((chat_id_db, lock_type, 'disabled'))

            if default_settings:
                cursor.executemany("INSERT OR IGNORE INTO protection_settings (chat_id, lock_type, action) VALUES (?, ?, ?)", default_settings)

            conn.commit()
            log.info(f"Protection tables and columns in '{ADMIN_DB_FILE}' initialized/updated successfully.")
    except sqlite3.Error as e:
        log.exception(f"Database initialization error for protection tables in {ADMIN_DB_FILE}: {e}")
    except Exception as e:
        log.exception(f"Unexpected error during {ADMIN_DB_FILE} protection DB init: {e}")

init_protection_db()

# --- Helper Functions ---

async def check_tg_restrict_permissions(client: Client, chat_id: int, user_id: int) -> bool:
    """Checks if a user is Owner or TG Admin with restrict permissions."""
    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status == ChatMemberStatus.OWNER: return True
        if member.status == ChatMemberStatus.ADMINISTRATOR:
            if member.privileges and member.privileges.can_restrict_members: return True
    except UserNotParticipant: pass
    except Exception as e: log.error(f"Error checking TG restrict permissions for user {user_id} in chat {chat_id}: {e}")
    return False

async def check_tg_promote_permissions(client: Client, chat_id: int, user_id: int) -> bool:
    """Checks if a user is Owner or TG Admin with promote permissions."""
    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status == ChatMemberStatus.OWNER: return True
        if member.status == ChatMemberStatus.ADMINISTRATOR:
            if member.privileges and member.privileges.can_promote_members: return True
    except UserNotParticipant: pass
    except Exception as e: log.error(f"Error checking TG promote permissions for user {user_id} in chat {chat_id}: {e}")
    return False

async def check_forward_control_permissions(client: Client, chat_id: int, user_id: int) -> bool:
    """Checks if user is Owner or Admin with change_info AND promote_members."""
    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status == ChatMemberStatus.OWNER: return True
        if member.status == ChatMemberStatus.ADMINISTRATOR:
            if (member.privileges and member.privileges.can_change_info and member.privileges.can_promote_members): return True
    except UserNotParticipant: pass
    except Exception as e: log.error(f"Error checking forward control permissions for user {user_id} in chat {chat_id}: {e}")
    return False

def is_bot_admin(chat_id: int, user_id: int) -> bool:
    """Checks if a user has the 'admin' status in the database."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM user_chat_status WHERE chat_id = ? AND user_id = ? AND status = 'admin'", (chat_id, user_id))
            return cursor.fetchone() is not None
    except sqlite3.Error as e:
        if "no such table" in str(e) or "no such column" in str(e): log.warning(f"Bot admin check failed: table 'user_chat_status' or column 'status'/'admin' not found in {DB_FILE}. Assuming not bot admin.")
        else: log.exception(f"[DB:{DB_FILE}] Error checking bot admin status table: {e}")
    return False

async def check_bot_admin_permissions(client: Client, chat_id: int, user_id: int) -> bool:
    """Checks if user is Owner, TG Admin with restrict rights, OR a Bot Admin."""
    if await check_tg_restrict_permissions(client, chat_id, user_id): return True
    if is_bot_admin(chat_id, user_id): return True
    return False

async def is_tg_admin_or_owner(client: Client, chat_id: int, user_id: int) -> bool:
    """Checks if user is Owner or TG Admin (any rights)."""
    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]: return True
    except UserNotParticipant: pass
    except Exception as e: log.error(f"Error checking Owner/Admin status for exemption in chat {chat_id}, user {user_id}: {e}")
    return False

async def is_exempt_from_protection(client: Client, chat_id: int, user_id: int, lock_type: str | None = None) -> bool:
    """Checks if user is exempt from protection based on lock type."""
    if lock_type == "link": return await is_tg_admin_or_owner(client, chat_id, user_id)
    if lock_type == "blockquote": return await check_bot_admin_permissions(client, chat_id, user_id)

    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]: return True
    except UserNotParticipant: pass
    except Exception as e: log.error(f"Error checking TG admin status for protection exemption in chat {chat_id}, user {user_id}: {e}")

    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM user_chat_status WHERE chat_id = ? AND user_id = ? AND status IN ('admin', 'special')", (chat_id, user_id))
            if cursor.fetchone() is not None: return True
    except sqlite3.Error as e:
        if "no such table" in str(e) or "no such column" in str(e): log.warning(f"DB exemption check failed: table 'user_chat_status' or relevant status not found in {DB_FILE}. Assuming not exempt via DB.")
        else: log.exception(f"[DB:{DB_FILE}] Error checking bot admin/special status table: {e}")

    return False

# --- Database Access Functions ---

def get_protection_status(chat_id: int) -> bool:
    """Checks if protection is globally enabled for the chat."""
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT protection_enabled FROM chat_settings WHERE chat_id = ?", (chat_id,))
            result = cursor.fetchone()
            # Default to True if chat not found or column missing
            return bool(result[0]) if result and result[0] is not None else True
    except sqlite3.Error as e:
        log.exception(f"DB error getting protection status for chat {chat_id}: {e}")
        return True # Fail safe

def set_protection_status(chat_id: int, enabled: bool):
    """Enables or disables protection globally for the chat."""
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            conn.execute("INSERT OR IGNORE INTO chat_settings (chat_id) VALUES (?)", (chat_id,))
            conn.execute("UPDATE chat_settings SET protection_enabled = ? WHERE chat_id = ?", (int(enabled), chat_id))
            conn.commit()
            log.info(f"Protection status for chat {chat_id} set to {enabled}")
            return True
    except sqlite3.Error as e:
        log.exception(f"DB error setting protection status for chat {chat_id}: {e}")
        return False

def get_lock_action(chat_id: int, lock_type: str) -> str:
    """Gets the configured action for a specific lock type in a chat."""
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT action FROM protection_settings WHERE chat_id = ? AND lock_type = ?", (chat_id, lock_type))
            result = cursor.fetchone()
            return result[0] if result else 'disabled'
    except sqlite3.Error as e:
        log.exception(f"DB error getting lock action for {lock_type} in chat {chat_id}: {e}")
        return 'disabled'

def set_lock_action(chat_id: int, lock_type: str, action: str):
    """Sets the action for a specific lock type in a chat."""
    if action not in ACTIONS:
        log.error(f"Invalid action '{action}' provided for set_lock_action.")
        return False
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            conn.execute("INSERT OR REPLACE INTO protection_settings (chat_id, lock_type, action) VALUES (?, ?, ?)", (chat_id, lock_type, action))
            conn.commit()
            log.info(f"Protection action for {lock_type} in chat {chat_id} set to {action}")
            return True
    except sqlite3.Error as e:
        log.exception(f"DB error setting lock action for {lock_type} in chat {chat_id}: {e}")
        return False

def get_max_message_length(chat_id: int) -> int:
    """Gets the configured max message length for a chat."""
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT max_message_length FROM chat_settings WHERE chat_id = ?", (chat_id,))
            result = cursor.fetchone()
            # Return the value if found and not None, otherwise default
            return result[0] if result and result[0] is not None else DEFAULT_MAX_MESSAGE_LENGTH
    except sqlite3.Error as e:
        log.exception(f"DB error getting max message length for chat {chat_id}: {e}")
        return DEFAULT_MAX_MESSAGE_LENGTH # Fail safe

def set_max_message_length(chat_id: int, length: int):
    """Sets the max message length for a chat."""
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            conn.execute("INSERT OR IGNORE INTO chat_settings (chat_id) VALUES (?)", (chat_id,))
            conn.execute("UPDATE chat_settings SET max_message_length = ? WHERE chat_id = ?", (length, chat_id))
            conn.commit()
            log.info(f"Max message length for chat {chat_id} set to {length}")
            return True
    except sqlite3.Error as e:
        log.exception(f"DB error setting max message length for chat {chat_id}: {e}")
        return False

def get_banned_words(chat_id: int) -> list[str]:
    """Gets the list of banned words for a chat."""
    words = []
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT word FROM banned_words WHERE chat_id = ?", (chat_id,))
            words = [row[0] for row in cursor.fetchall()]
    except sqlite3.Error as e: log.exception(f"DB error getting banned words for chat {chat_id}: {e}")
    return words

def add_banned_words(chat_id: int, words: list[str]) -> int:
    """Adds words to the banned list for a chat. Returns number added."""
    added_count = 0
    if not words: return 0
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            words_to_insert = [(chat_id, str(word).lower()) for word in words]
            cursor.executemany("INSERT OR IGNORE INTO banned_words (chat_id, word) VALUES (?, ?)", words_to_insert)
            added_count = cursor.rowcount
            conn.commit()
            log.info(f"Attempted to add {len(words)} banned words for chat {chat_id}. {added_count} were new.")
    except sqlite3.Error as e: log.exception(f"DB error adding banned words for chat {chat_id}: {e}")
    return added_count

def remove_banned_words(chat_id: int, words: list[str]) -> int:
    """Removes words from the banned list for a chat. Returns number removed."""
    removed_count = 0
    if not words: return 0
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            words_to_delete = [(chat_id, str(word).lower()) for word in words]
            cursor.executemany("DELETE FROM banned_words WHERE chat_id = ? AND word = ?", words_to_delete)
            removed_count = cursor.rowcount
            conn.commit()
            log.info(f"Attempted to remove {len(words)} banned words for chat {chat_id}. {removed_count} were found and removed.")
    except sqlite3.Error as e: log.exception(f"DB error removing banned words for chat {chat_id}: {e}")
    return removed_count

def get_allowed_forward_sources(chat_id: int) -> list[int]:
    """Gets the list of allowed forward source IDs for a chat."""
    source_ids = []
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT source_id FROM allowed_forward_sources WHERE chat_id = ?", (chat_id,))
            source_ids = [row[0] for row in cursor.fetchall()]
    except sqlite3.Error as e: log.exception(f"[DB:{ADMIN_DB_FILE}] Error getting allowed forward sources for chat {chat_id}: {e}")
    return source_ids

def is_forward_source_allowed(chat_id: int, source_id: int) -> bool:
    """Checks if a specific source ID is allowed for forwarding in a chat."""
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM allowed_forward_sources WHERE chat_id = ? AND source_id = ?", (chat_id, source_id))
            return cursor.fetchone() is not None
    except sqlite3.Error as e: log.exception(f"[DB:{ADMIN_DB_FILE}] Error checking allowed forward source {source_id} for chat {chat_id}: {e}")
    return False

async def log_admin_action(client: Client, action_name: str, admin: User, target_user: User | None, chat: Chat, extra_info: str | None = None):
    """Placeholder function to log administrative actions."""
    admin_mention = admin.mention(style="html") if admin else "Unknown Admin"
    chat_link = f"<a href='https://t.me/c/{str(chat.id).replace('-100', '')}/1'>{html.escape(chat.title)}</a>" if chat.id else html.escape(chat.title or "Unknown Chat")
    log_message = f"<b>Admin Action Log</b>\n\n"
    log_message += f"<b>Action:</b> {html.escape(action_name)}\n"
    log_message += f"<b>Admin:</b> {admin_mention} (<code>{admin.id if admin else 'N/A'}</code>)\n"
    log_message += f"<b>Chat:</b> {chat_link} (<code>{chat.id}</code>)\n"
    if target_user:
        target_mention = target_user.mention(style="html")
        log_message += f"<b>Target:</b> {target_mention} (<code>{target_user.id}</code>)\n"
    if extra_info:
        log_message += f"<b>Details:</b> {html.escape(extra_info)}\n"
    log.info(f"ADMIN LOG: Action='{action_name}' Admin='{admin.id if admin else 'N/A'}' Chat='{chat.id}' Target='{target_user.id if target_user else 'N/A'}' Extra='{extra_info}'")

# --- Mute/Ban Actions ---
async def mute_user_for_violation(client: Client, chat_id: int, user_id: int, reason: str):
    """Mutes a user by adding to DB."""
    expiry_timestamp = int(time.time()) + (DEFAULT_MUTE_DAYS * 86400)
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("INSERT OR REPLACE INTO user_chat_status (chat_id, user_id, status, expiry_timestamp) VALUES (?, ?, 'muted', ?)",(chat_id, user_id, expiry_timestamp))
            conn.commit()
            log.info(f"User {user_id} muted in chat {chat_id} for {DEFAULT_MUTE_DAYS} days due to {reason}.")
    except sqlite3.Error as db_err: log.exception(f"DB error muting user {user_id} for violation in chat {chat_id}: {db_err}")
    except Exception as e: log.exception(f"Error muting user {user_id} for violation in chat {chat_id}: {e}")

async def ban_user_for_violation(client: Client, chat_id: int, user_id: int, reason: str):
    """Bans a user via Telegram API."""
    try:
        await client.ban_chat_member(chat_id, user_id)
        log.info(f"User {user_id} banned in chat {chat_id} due to {reason}.")
    except (ChatAdminRequired, RightForbidden): log.warning(f"Failed to ban user {user_id} in chat {chat_id} for {reason}: Insufficient permissions.")
    except Exception as e: log.exception(f"Error banning user {user_id} for violation in chat {chat_id}: {e}")

# --- Send Violation Replies (Permanent) ---
async def send_violation_reply(client: Client, message: Message, lock_type: str, action: str):
    """Sends a permanent reply explaining the content violation."""
    user_mention = "عذراً"
    if message.from_user:
        user_mention = message.from_user.mention(style=ParseMode.MARKDOWN)

    reason_text = LOCK_TYPES.get(lock_type, "محتوى ممنوع")
    reply_msg_text = f"⚠️ عذراً {user_mention}، إرسال **{reason_text}** مقيد حالياً."

    # Customize messages for specific lock types
    if lock_type == "link": reply_msg_text = f"⚠️ عذراً {user_mention}، إرسال الروابط مسموح فقط للمشرفين."
    elif lock_type == "blockquote": reply_msg_text = f"⚠️ عذراً {user_mention}، استخدام الاقتباس (> نص) مقيد حالياً لغير المشرفين/الأدمن."
    elif lock_type == "swear": reply_msg_text = f"⚠️ عذراً {user_mention}، تم اكتشاف كلمة مسيئة."
    elif lock_type == "edit": reply_msg_text = f"⚠️ عذراً {user_mention}، تم حذف الرسالة بسبب تعديلها بعد الوقت المسموح به."
    elif lock_type == "photo": reply_msg_text = f"⚠️ عذراً {user_mention}، إرسال الصور مقيد حالياً."
    elif lock_type == "poll": reply_msg_text = f"⚠️ عذراً {user_mention}، إرسال **{reason_text}** مقيد حالياً."
    elif lock_type == "dice": reply_msg_text = f"⚠️ عذراً {user_mention}، إرسال **{reason_text}** مقيد حالياً."
    elif lock_type == "spoiler_media": reply_msg_text = f"⚠️ عذراً {user_mention}، إرسال **{reason_text}** مقيد حالياً."
    elif lock_type == "spoiler_text": reply_msg_text = f"⚠️ عذراً {user_mention}، إرسال **{reason_text}** مقيد حالياً."
    # elif lock_type == "arabic": reply_msg_text = f"⚠️ عذراً {user_mention}، إرسال رسائل بـ**{reason_text}** مقيد حالياً." # Removed
    elif lock_type == "long_text":
        current_limit = get_max_message_length(message.chat.id)
        limit_text = f"(أطول من {current_limit} حرف)" if current_limit > 0 else ""
        reply_msg_text = f"⚠️ عذراً {user_mention}، إرسال **{reason_text}** {limit_text} مقيد حالياً."

    try:
        await message.reply_text(reply_msg_text, quote=True, parse_mode=ParseMode.MARKDOWN)
    except Exception as e: log.error(f"Failed to send content violation reply: {e}")

async def send_forward_violation_reply(client: Client, message: Message):
    """Sends a permanent reply explaining the forward violation and listing allowed sources."""
    chat_id = message.chat.id
    user_mention = message.from_user.mention(style="html") if message.from_user else "عذراً"

    allowed_source_ids = get_allowed_forward_sources(chat_id)
    reply_msg_text = f"⚠️ عذراً {user_mention}، إعادة التوجيه مقيدة حالياً."

    if not allowed_source_ids: pass
    else:
        reply_msg_text += "\n\n<b>يُسمح فقط بإعادة التوجيه من المصادر التالية:</b>"
        allowed_source_details = []
        for source_id in allowed_source_ids:
            try:
                source_chat = await client.get_chat(source_id)
                title = html.escape(source_chat.title or str(source_id))
                detail = title
                if source_chat.username: detail = f"<a href='https://t.me/{source_chat.username}'>{title}</a>"
                allowed_source_details.append(f"- {detail}")
            except BadRequest as e: log.warning(f"Could not get chat info for allowed forward source ID {source_id} in chat {chat_id}: {e} - Skipping.")
            except Exception as e:
                 log.error(f"Unexpected error getting chat info for allowed forward source ID {source_id}: {e}")
                 allowed_source_details.append(f"- <code>{source_id}</code> (خطأ في جلب المعلومات)")

        if allowed_source_details: reply_msg_text += "\n" + "\n".join(allowed_source_details)
        else: reply_msg_text = f"⚠️ عذراً {user_mention}، إعادة التوجيه مقيدة حالياً من مصادر غير مسموح بها."

    try:
        await message.reply_text(reply_msg_text, quote=True, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e: log.error(f"Failed to send forward violation reply: {e}")


# --- Command Handlers ---

@app.on_message(filters.command(["تعطيل الحماية", "قفل الحماية"], prefixes=[""]) & filters.group)
async def disable_protection_command(client: Client, message: Message):
    """Command to disable protection for the chat."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not await check_tg_promote_permissions(client, chat_id, user_id): return await message.reply_text(f"👮‍♂️ عذراً، هذا الأمر مخصص للمالك أو المشرفين بصلاحية رفع مشرفين فقط.")
    if set_protection_status(chat_id, False): await message.reply_text("✅ تم تعطيل نظام الحماية لهذه المجموعة.")
    else: await message.reply_text("❌ حدث خطأ أثناء تعطيل الحماية.")

@app.on_message(filters.command(["تفعيل الحماية", "فتح الحماية"], prefixes=[""]) & filters.group)
async def enable_protection_command(client: Client, message: Message):
    """Command to enable protection for the chat."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not await check_tg_promote_permissions(client, chat_id, user_id): return await message.reply_text(f"👮‍♂️ عذراً، هذا الأمر مخصص للمالك أو المشرفين بصلاحية رفع مشرفين فقط.")
    if set_protection_status(chat_id, True): await message.reply_text("✅ تم تفعيل نظام الحماية لهذه المجموعة.")
    else: await message.reply_text("❌ حدث خطأ أثناء تفعيل الحماية.")

@app.on_message(filters.command("قفل الكل", prefixes=[""]) & filters.group, group=2)
async def lock_all_command(client: Client, message: Message):
    """Command to lock all content types with the 'delete' action."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not await check_forward_control_permissions(client, chat_id, user_id): return await message.reply_text(f"👮‍♂️ عذراً، هذا الأمر يتطلب صلاحية تغيير معلومات المجموعة ورفع المشرفين.")
    updated_count = 0
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            lock_types_to_update = [lt for lt in LOCK_TYPES.keys() if lt not in ['swear', 'edit', 'long_text', 'english']] # Keep english/long_text etc. separate
            data_to_update = [(chat_id, lock_type, 'delete') for lock_type in lock_types_to_update]
            cursor.executemany("INSERT OR REPLACE INTO protection_settings (chat_id, lock_type, action) VALUES (?, ?, ?)", data_to_update)
            updated_count = cursor.rowcount
            conn.commit()
        log.info(f"User {user_id} used 'lock all' in chat {chat_id}. Set {len(lock_types_to_update)} items to delete.")
        await message.reply_text(f"🔒 تم قفل جميع أنواع المحتوى الأساسية بالإجراء: **حذف**.")
    except sqlite3.Error as e: log.exception(f"DB error during 'lock all' for chat {chat_id}: {e}"); await message.reply_text("❌ حدث خطأ في قاعدة البيانات أثناء قفل الكل.")
    except Exception as e: log.exception(f"Error during 'lock all' for chat {chat_id}: {e}"); await message.reply_text("❌ حدث خطأ غير متوقع أثناء قفل الكل.")

@app.on_message(filters.command("فتح الكل", prefixes=[""]) & filters.group, group=2)
async def unlock_all_command(client: Client, message: Message):
    """Command to unlock (disable) all content types."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not await check_forward_control_permissions(client, chat_id, user_id): return await message.reply_text(f"👮‍♂️ عذراً، هذا الأمر يتطلب صلاحية تغيير معلومات المجموعة ورفع المشرفين.")
    updated_count = 0
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            lock_types_to_update = [lt for lt in LOCK_TYPES.keys() if lt not in ['swear', 'edit', 'long_text', 'english']] # Keep english/long_text etc. separate
            data_to_update = [(chat_id, lock_type, 'disabled') for lock_type in lock_types_to_update]
            cursor.executemany("INSERT OR REPLACE INTO protection_settings (chat_id, lock_type, action) VALUES (?, ?, ?)", data_to_update)
            updated_count = cursor.rowcount
            conn.commit()
        log.info(f"User {user_id} used 'unlock all' in chat {chat_id}. Set {len(lock_types_to_update)} items to disabled.")
        await message.reply_text(f"🔓 تم فتح جميع أنواع المحتوى الأساسية.")
    except sqlite3.Error as e: log.exception(f"DB error during 'unlock all' for chat {chat_id}: {e}"); await message.reply_text("❌ حدث خطأ في قاعدة البيانات أثناء فتح الكل.")
    except Exception as e: log.exception(f"Error during 'unlock all' for chat {chat_id}: {e}"); await message.reply_text("❌ حدث خطأ غير متوقع أثناء فتح الكل.")

@app.on_message(filters.command("قفل التوجيه", prefixes=[""]) & filters.group, group=2)
async def lock_forward_command(client: Client, message: Message):
    """Command to enable the forward lock."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    user_mention = message.from_user.mention(style="html")
    if not await check_forward_control_permissions(client, chat_id, user_id): return await message.reply_text(f"عذراً يا {user_mention}، هذا الأمر يتطلب صلاحية تغيير معلومات المجموعة ورفع المشرفين.", parse_mode=ParseMode.HTML)
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            conn.execute("INSERT OR IGNORE INTO chat_settings (chat_id) VALUES (?)", (chat_id,))
            conn.execute("UPDATE chat_settings SET is_forward_locked = 1 WHERE chat_id = ?", (chat_id,))
            conn.commit()
        await message.reply_text(f"🔒 تم تفعيل <b>منع إعادة التوجيه</b> بواسطة {user_mention}.\nسيتم حذف أي رسالة معاد توجيهها (ما عدا من المصادر المسموحة).\nاستخدم الأمر <code>مسموح للتوجيه</code> لإضافة استثناءات.", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "🔒 قفل التوجيه", message.from_user, None, message.chat)
    except sqlite3.Error as e: log.exception(f"DB error locking forwards in chat {chat_id}: {e}"); await message.reply_text("❌ حدث خطأ في قاعدة البيانات أثناء قفل التوجيه.")
    except Exception as e: log.exception(f"Error locking forwards in {chat_id}: {e}"); await message.reply_text(f"❌ حدث خطأ أثناء قفل التوجيه: {str(e)}")

@app.on_message(filters.command("فتح التوجيه", prefixes=[""]) & filters.group, group=2)
async def unlock_forward_command(client: Client, message: Message):
    """Command to disable the forward lock."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    user_mention = message.from_user.mention(style="html")
    if not await check_forward_control_permissions(client, chat_id, user_id): return await message.reply_text(f"عذراً يا {user_mention}، هذا الأمر يتطلب صلاحية تغيير معلومات المجموعة ورفع المشرفين.", parse_mode=ParseMode.HTML)
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            conn.execute("INSERT OR IGNORE INTO chat_settings (chat_id) VALUES (?)", (chat_id,))
            conn.execute("UPDATE chat_settings SET is_forward_locked = 0 WHERE chat_id = ?", (chat_id,))
            conn.commit()
        await message.reply_text(f"🔓 تم إلغاء تفعيل <b>منع إعادة التوجيه</b> بواسطة {user_mention}.", parse_mode=ParseMode.HTML)
        await log_admin_action(client, "🔓 فتح التوجيه", message.from_user, None, message.chat)
    except sqlite3.Error as e: log.exception(f"DB error unlocking forwards in chat {chat_id}: {e}"); await message.reply_text("❌ حدث خطأ في قاعدة البيانات أثناء فتح التوجيه.")
    except Exception as e: log.exception(f"Error unlocking forwards in {chat_id}: {e}"); await message.reply_text(f"❌ حدث خطأ أثناء فتح التوجيه: {str(e)}")

@app.on_message(filters.command("مسموح للتوجيه", prefixes=[""]) & filters.group, group=2)
async def allow_forward_source_command(client: Client, message: Message):
    """Command to add allowed sources for forwarding when forward lock is active."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    user_mention = message.from_user.mention(style="html")
    if not await check_forward_control_permissions(client, chat_id, user_id): return await message.reply_text(f"عذراً يا {user_mention}، هذا الأمر يتطلب صلاحية تغيير معلومات المجموعة ورفع المشرفين.", parse_mode=ParseMode.HTML)

    args = message.command[1:]
    if not args: return await message.reply_text("⚠️ الاستخدام: <code>مسموح للتوجيه [معرفات/روابط القنوات/البوتات]</code>\nمثال: <code>مسموح للتوجيه @ChannelUsername bot_username -100123456789</code>", parse_mode=ParseMode.HTML)

    added_sources, failed_sources, added_ids = [], [], []
    with sqlite3.connect(ADMIN_DB_FILE) as conn:
        cursor = conn.cursor()
        for source_arg in args:
            try:
                source_chat = await client.get_chat(source_arg)
                if source_chat.type in [ChatType.CHANNEL, ChatType.BOT, ChatType.SUPERGROUP, ChatType.GROUP]:
                    source_id = source_chat.id
                    cursor.execute("INSERT OR IGNORE INTO allowed_forward_sources (chat_id, source_id) VALUES (?, ?)", (chat_id, source_id))
                    source_name = source_chat.title or source_chat.username or str(source_id)
                    source_name_html = html.escape(source_name)
                    added_sources.append(f"{source_name_html} (<code>{source_id}</code>)")
                    added_ids.append(str(source_id))
                else: failed_sources.append(f"{html.escape(source_arg)} (ليس قناة أو بوت أو مجموعة)")
            except Exception as e: log.warning(f"Failed to resolve or add allowed forward source '{source_arg}' for chat {chat_id}: {e}"); failed_sources.append(html.escape(source_arg))
        conn.commit()

    reply_text = ""
    if added_sources: reply_text += f"✅ تم السماح بالتوجيه من المصادر التالية:\n- " + "\n- ".join(added_sources) + "\n\n"; await log_admin_action(client, "➕ إضافة مصدر توجيه مسموح", message.from_user, None, message.chat, extra_info=f"المعرفات: {', '.join(added_ids)}")
    if failed_sources: reply_text += f"⚠️ فشل في إضافة المصادر التالية (تأكد من المعرف/الرابط وأن البوت عضو فيها إذا كانت خاصة):\n- " + "\n- ".join(failed_sources)
    if not reply_text: reply_text = "لم يتم العثور على مصادر صالحة للإضافة."
    await message.reply_text(reply_text.strip(), parse_mode=ParseMode.HTML)

# --- NEW: Command to set max message length ---
@app.on_message(filters.command("عدد حروف", prefixes=[""]) & filters.group, group=2)
async def set_max_chars_command(client: Client, message: Message):
    """Command to set the maximum allowed characters for messages."""
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not await check_forward_control_permissions(client, chat_id, user_id):
        return await message.reply_text(f"👮‍♂️ عذراً، هذا الأمر يتطلب صلاحية تغيير معلومات المجموعة ورفع المشرفين.")

    if len(message.command) != 2:
        current_limit = get_max_message_length(chat_id)
        limit_text = f"الحد الحالي: {current_limit} حرف." if current_limit > 0 else "الحد الحالي: لا يوجد حد."
        return await message.reply_text(f"⚠️ الاستخدام: `عدد حروف [رقم]` لتحديد الحد الأقصى لعدد الحروف (أو `0` لإلغاء الحد).\n{limit_text}")

    try:
        new_limit = int(message.command[1])
        if new_limit < 0:
            return await message.reply_text("❌ لا يمكن وضع رقم سالب. استخدم `0` لإلغاء الحد.")
        elif new_limit == 0:
            if set_max_message_length(chat_id, -1): # Use -1 internally for no limit
                 await message.reply_text("✅ تم إلغاء الحد الأقصى لعدد حروف الرسائل.")
                 await log_admin_action(client, "📏 إلغاء حد الحروف", message.from_user, None, message.chat)
            else:
                 await message.reply_text("❌ حدث خطأ في قاعدة البيانات.")
        else:
            if set_max_message_length(chat_id, new_limit):
                 await message.reply_text(f"✅ تم تعيين الحد الأقصى لعدد حروف الرسائل إلى **{new_limit}** حرف.")
                 await log_admin_action(client, "📏 تعيين حد الحروف", message.from_user, None, message.chat, extra_info=f"الحد الجديد: {new_limit}")
            else:
                 await message.reply_text("❌ حدث خطأ في قاعدة البيانات.")
    except ValueError:
        await message.reply_text("❌ يرجى إدخال رقم صحيح لعدد الحروف.")
    except Exception as e:
        log.exception(f"Error setting max message length in chat {chat_id}: {e}")
        await message.reply_text("❌ حدث خطأ غير متوقع.")


# --- Protection Settings Command & Callbacks ---

PROTECTION_CALLBACK_PREFIX = "pro:"

def build_protection_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Builds the inline keyboard for protection settings."""
    keyboard = []
    row = []
    settings = {}
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT lock_type, action FROM protection_settings WHERE chat_id = ?", (chat_id,))
            for lock_type, action in cursor.fetchall():
                settings[lock_type] = action
    except sqlite3.Error as e: log.exception(f"DB error getting protection settings for keyboard in chat {chat_id}: {e}")

    lock_types_sorted = sorted(LOCK_TYPES.keys())

    for i, lock_type in enumerate(lock_types_sorted):
        if lock_type == 'swear': continue
        if lock_type not in LOCK_TYPES: continue

        lock_name = LOCK_TYPES.get(lock_type, lock_type)
        current_action = settings.get(lock_type, 'disabled')
        action_symbol = "🔒" if current_action != 'disabled' else "🔓"

        button_text = f"{lock_name} ({action_symbol})"
        callback_data = f"{PROTECTION_CALLBACK_PREFIX}menu:{lock_type}"
        row.append(InlineKeyboardButton(button_text, callback_data=callback_data))

        if len(row) == 2 or i == len(lock_types_sorted) - 1:
            if row: keyboard.append(row)
            row = []

    swear_action = settings.get('swear', 'disabled')
    swear_symbol = "🔒" if swear_action != 'disabled' else "🔓"
    swear_button = InlineKeyboardButton(f"{LOCK_TYPES['swear']} ({swear_symbol})", callback_data=f"{PROTECTION_CALLBACK_PREFIX}menu:swear")
    keyboard.append([swear_button])

    keyboard.append([InlineKeyboardButton("إغلاق ❌", callback_data=f"{PROTECTION_CALLBACK_PREFIX}close")])
    return InlineKeyboardMarkup(keyboard)

@app.on_message(filters.command("الحماية", prefixes=[""]) & filters.group)
async def protection_settings_command(client: Client, message: Message):
    """Command to display the protection settings menu."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not await check_forward_control_permissions(client, chat_id, user_id): return await message.reply_text(f"👮‍♂️ عذراً، هذا الأمر يتطلب صلاحية تغيير معلومات المجموعة ورفع المشرفين.")
    if not get_protection_status(chat_id): await message.reply_text("ℹ️ نظام الحماية معطل حالياً لهذه المجموعة.\nاستخدم `تفعيل الحماية` لتشغيله."); return
    keyboard = build_protection_keyboard(chat_id)
    await message.reply_text("🛡️ **إعدادات الحماية:**\n\nاختر نوع القفل لتغيير الإجراء (🔒=ممنوع، 🔓=مسموح):", reply_markup=keyboard)


@app.on_callback_query(filters.regex(f"^{PROTECTION_CALLBACK_PREFIX}"))
async def protection_callback_handler(client: Client, query: CallbackQuery):
    """Handles button clicks from the protection settings menu."""
    chat_id = query.message.chat.id
    user_id = query.from_user.id
    data = query.data.split(":")[1:]

    if not await check_forward_control_permissions(client, chat_id, user_id): await query.answer("👮‍♂️ ليس لديك الصلاحية لتغيير هذه الإعدادات.", show_alert=True); return

    action_type = data[0]

    if action_type == "close":
        try: await query.message.delete()
        except Exception as e: log.warning(f"Failed to delete protection menu message: {e}")
        return

    if action_type == "menu":
        if len(data) < 2: return await query.answer("خطأ في بيانات الزر.", show_alert=True)
        lock_type = data[1]
        if lock_type not in LOCK_TYPES: return await query.answer("نوع قفل غير صالح.", show_alert=True)

        if lock_type == "swear":
            buttons = []
            info_text = ("🚫 **إدارة الكلمات المسيئة:**\n\n"
                         "يتم التحكم في قائمة الكلمات المحظورة عبر الأوامر:\n"
                         "- `اضف كلمة [كلمة1] [كلمة2] ...`\n"
                         "- `حذف كلمة [كلمة1] [كلمة2] ...`\n"
                         "- `الكلمات المحظورة`\n\n"
                         "يمكنك أيضاً تعيين **الإجراء** الذي سيتم تطبيقه عند اكتشاف كلمة مسيئة من الأزرار أدناه، أو عرض القائمة الحالية.")
            action_buttons_row1 = [InlineKeyboardButton(ACTIONS['delete'], callback_data=f"{PROTECTION_CALLBACK_PREFIX}set:{lock_type}:delete"), InlineKeyboardButton(ACTIONS['mute'], callback_data=f"{PROTECTION_CALLBACK_PREFIX}set:{lock_type}:mute")]
            action_buttons_row2 = [InlineKeyboardButton(ACTIONS['ban'], callback_data=f"{PROTECTION_CALLBACK_PREFIX}set:{lock_type}:ban"), InlineKeyboardButton(ACTIONS['disabled'], callback_data=f"{PROTECTION_CALLBACK_PREFIX}set:{lock_type}:disabled")]
            list_button_row = [InlineKeyboardButton("عرض الكلمات المحظورة 🚫", callback_data=f"{PROTECTION_CALLBACK_PREFIX}list_swear")]
            back_button_row = [InlineKeyboardButton("رجوع ⬅️", callback_data=f"{PROTECTION_CALLBACK_PREFIX}back")]
            buttons.extend([action_buttons_row1, action_buttons_row2, list_button_row, back_button_row])
            current_action = get_lock_action(chat_id, lock_type)
            current_action_text = ACTIONS.get(current_action, 'غير معروف')
            info_text += f"\n\n(الإجراء الحالي المطبق على الكلمات المسيئة: {current_action_text})"
            try: await query.edit_message_text(info_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN)
            except MessageNotModified: await query.answer()
            except Exception as e: log.error(f"Error editing message for swear info: {e}"); await query.answer("حدث خطأ.", show_alert=True)
            return

        # Generic menu for other lock types
        lock_name = LOCK_TYPES.get(lock_type, lock_type)
        current_action = get_lock_action(chat_id, lock_type)
        buttons = []
        buttons.append([InlineKeyboardButton(ACTIONS['delete'], callback_data=f"{PROTECTION_CALLBACK_PREFIX}set:{lock_type}:delete"), InlineKeyboardButton(ACTIONS['mute'], callback_data=f"{PROTECTION_CALLBACK_PREFIX}set:{lock_type}:mute")])
        buttons.append([InlineKeyboardButton(ACTIONS['ban'], callback_data=f"{PROTECTION_CALLBACK_PREFIX}set:{lock_type}:ban"), InlineKeyboardButton(ACTIONS['disabled'], callback_data=f"{PROTECTION_CALLBACK_PREFIX}set:{lock_type}:disabled")])
        buttons.append([InlineKeyboardButton("رجوع ⬅️", callback_data=f"{PROTECTION_CALLBACK_PREFIX}back")])

        info_text = f"🛡️ اختر الإجراء المطلوب عند إرسال **{lock_name}**:\n(الحالة الحالية: {ACTIONS.get(current_action, 'غير معروف')})"
        # Add extra info for long_text lock
        if lock_type == "long_text":
            current_limit = get_max_message_length(chat_id)
            limit_info = f"الحد الحالي للحروف: {current_limit}" if current_limit > 0 else "لا يوجد حد حالي للحروف."
            info_text += f"\n\nℹ️ يمكنك تغيير الحد باستخدام الأمر:\n`عدد حروف [رقم]` (مثال: `عدد حروف 1000`).\n`عدد حروف 0` لإلغاء الحد.\n({limit_info})"

        try: await query.edit_message_text(info_text, reply_markup=InlineKeyboardMarkup(buttons))
        except MessageNotModified: await query.answer("لم يتم تغيير شيء.")
        except Exception as e: log.error(f"Error editing message for protection menu: {e}"); await query.answer("حدث خطأ.", show_alert=True)

    elif action_type == "set":
        if len(data) < 3: return await query.answer("خطأ في بيانات الزر.", show_alert=True)
        lock_type = data[1]
        new_action = data[2]
        if lock_type not in LOCK_TYPES: return await query.answer("نوع قفل غير صالح.", show_alert=True)
        if new_action not in ACTIONS: return await query.answer("إجراء غير صالح.", show_alert=True)

        if set_lock_action(chat_id, lock_type, new_action):
            await query.answer(f"تم تعيين الإجراء لـ {LOCK_TYPES.get(lock_type, lock_type)} إلى {ACTIONS[new_action]}.")
            keyboard = build_protection_keyboard(chat_id)
            try: await query.edit_message_text("🛡️ **إعدادات الحماية:**\n\nاختر نوع القفل لتغيير الإجراء (🔒=ممنوع، 🔓=مسموح):", reply_markup=keyboard)
            except MessageNotModified: pass
            except Exception as e: log.error(f"Error editing reply markup after setting protection: {e}")
        else: await query.answer("❌ فشل تحديث الإعداد في قاعدة البيانات.", show_alert=True)

    elif action_type == "list_swear":
        banned_words = get_banned_words(chat_id)
        response_text = "🚫 **الكلمات المحظورة حالياً:**\n\n"
        response_text += "، ".join(f"`{word}`" for word in banned_words) if banned_words else "لا توجد كلمات محظورة حالياً."
        if len(response_text) > 4000: response_text = response_text[:3950] + "\n... (القائمة طويلة جداً للعرض)"
        buttons = [[InlineKeyboardButton("رجوع ⬅️", callback_data=f"{PROTECTION_CALLBACK_PREFIX}menu:swear")]]
        try: await query.edit_message_text(response_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN)
        except MessageNotModified: await query.answer()
        except Exception as e: log.error(f"Error showing banned words list: {e}"); await query.answer("حدث خطأ أثناء عرض القائمة.", show_alert=True)

    elif action_type == "back":
         keyboard = build_protection_keyboard(chat_id)
         try: await query.edit_message_text("🛡️ **إعدادات الحماية:**\n\nاختر نوع القفل لتغيير الإجراء (🔒=ممنوع، 🔓=مسموح):", reply_markup=keyboard)
         except MessageNotModified: await query.answer()
         except Exception as e: log.error(f"Error editing message for protection back button: {e}"); await query.answer("حدث خطأ.", show_alert=True)
    else: await query.answer("زر غير معروف.", show_alert=True)


# --- Banned Words Management Commands ---

@app.on_message(filters.command("اضف كلمة", prefixes=[""]) & filters.group, group=2)
async def add_banned_word_command(client: Client, message: Message):
    """Command to add words to the banned list."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not await check_forward_control_permissions(client, chat_id, user_id): return await message.reply_text(f"👮‍♂️ عذراً، هذا الأمر يتطلب صلاحية تغيير معلومات المجموعة ورفع المشرفين.")
    words_to_add = message.command[1:]
    if not words_to_add: return await message.reply_text("⚠️ الاستخدام: `اضف كلمة كلمة1 كلمة2 ...`")
    added_count = add_banned_words(chat_id, words_to_add)
    await message.reply_text(f"✅ تمت محاولة إضافة {len(words_to_add)} كلمة. تمت إضافة {added_count} كلمة جديدة إلى قائمة الحظر.")

@app.on_message(filters.command("حذف كلمة", prefixes=[""]) & filters.group, group=2)
async def remove_banned_word_command(client: Client, message: Message):
    """Command to remove words from the banned list."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not await check_forward_control_permissions(client, chat_id, user_id): return await message.reply_text(f"👮‍♂️ عذراً، هذا الأمر يتطلب صلاحية تغيير معلومات المجموعة ورفع المشرفين.")
    words_to_remove = message.command[1:]
    if not words_to_remove: return await message.reply_text("⚠️ الاستخدام: `حذف كلمة كلمة1 كلمة2 ...`")
    removed_count = remove_banned_words(chat_id, words_to_remove)
    if removed_count > 0: await message.reply_text(f"🗑️ تمت إزالة {removed_count} كلمة من قائمة الحظر.")
    else: await message.reply_text(f"ℹ️ لم يتم العثور على أي من الكلمات المحددة في قائمة الحظر.")

@app.on_message(filters.command("الكلمات المحظورة", prefixes=[""]) & filters.group, group=2)
async def list_banned_words_command(client: Client, message: Message):
    """Command to list all currently banned words."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not await check_forward_control_permissions(client, chat_id, user_id): return await message.reply_text(f"👮‍♂️ عذراً، هذا الأمر يتطلب صلاحية تغيير معلومات المجموعة ورفع المشرفين.")
    banned_words = get_banned_words(chat_id)
    if not banned_words: return await message.reply_text("ℹ️ لا توجد كلمات محظورة حالياً في هذه المجموعة.")
    response_text = "🚫 **الكلمات المحظورة حالياً:**\n\n"
    response_text += "، ".join(f"`{word}`" for word in banned_words)
    if len(response_text) > 4096: response_text = response_text[:4000] + "\n... (القائمة طويلة جداً للعرض)"
    await message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN)


# --- Message Handlers ---

@app.on_message(filters.forwarded & filters.group & ~filters.service & ~filters.me, group=2)
async def handle_forwarded_messages_handler(client: Client, message: Message):
    """Handles forwarded messages based on lock status and allowed sources."""
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0

    is_fwd_locked = False
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_forward_locked FROM chat_settings WHERE chat_id = ?", (chat_id,))
            result = cursor.fetchone()
            if result and result[0] == 1: is_fwd_locked = True
    except sqlite3.Error as e: log.exception(f"[DB:{ADMIN_DB_FILE}] Error checking forward lock status for chat {chat_id}: {e}"); raise ContinuePropagation

    if not is_fwd_locked: raise ContinuePropagation

    source_id = None
    origin: MessageOrigin | None = message.forward_origin
    if isinstance(origin, MessageOriginChannel): source_id = origin.chat.id
    elif isinstance(origin, MessageOriginChat): source_id = origin.chat.id
    elif isinstance(origin, MessageOriginUser): source_id = origin.sender_user.id

    is_allowed = False
    if source_id and is_forward_source_allowed(chat_id, source_id): is_allowed = True

    if is_allowed: log.info(f"Allowed forward from explicitly permitted source ID {source_id} in locked chat {chat_id}."); raise ContinuePropagation
    else:
        reason = "إعادة التوجيه مقفلة والمصدر غير مسموح به"
        try: await message.delete(); log.info(f"Deleted forwarded message {message.id} from user {user_id} in locked chat {chat_id}. Reason: {reason}. Original Source ID: {source_id}")
        except MessageDeleteForbidden: log.warning(f"Cannot delete forwarded message {message.id} in locked chat {chat_id} due to permissions.")
        except Exception as e: log.exception(f"Error deleting forwarded message {message.id} in locked chat {chat_id}: {e}"); return
        await send_forward_violation_reply(client, message)


@app.on_message(filters.group & ~filters.service & ~filters.me & ~filters.forwarded, group=2)
async def protection_enforcement_handler(client: Client, message: Message):
    """Handles incoming non-forwarded messages to enforce protection rules."""
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0
    if not user_id: return
    if not get_protection_status(chat_id): raise ContinuePropagation

    lock_type_to_check = None
    violation_reason = ""
    action = 'disabled'

    # --- Check Blockquote ---
    if message.text and message.text.startswith(">"):
        quote_action = get_lock_action(chat_id, "blockquote")
        if quote_action != 'disabled' and not await check_bot_admin_permissions(client, chat_id, user_id):
            action = quote_action; lock_type_to_check = "blockquote"; violation_reason = LOCK_TYPES["blockquote"]

    # --- Check Other Content Types (Priority Order) ---
    if not lock_type_to_check:
        if message.photo: lock_type_to_check = "photo"
        elif message.video or message.video_note: lock_type_to_check = "video"
        elif message.sticker: lock_type_to_check = "sticker"
        elif message.voice: lock_type_to_check = "voice"
        elif message.audio: lock_type_to_check = "audio"
        elif message.poll: lock_type_to_check = "poll"
        elif message.dice: lock_type_to_check = "dice"
        elif message.document: lock_type_to_check = "gif" if message.document.mime_type == "image/gif" or (message.document.file_name and message.document.file_name.lower().endswith(".gif")) else "document"
        elif message.contact: lock_type_to_check = "contact"
        elif message.game: lock_type_to_check = "game"
        elif message.location: lock_type_to_check = "location"
        elif message.has_media_spoiler: lock_type_to_check = "spoiler_media"
        elif message.new_chat_members: lock_type_to_check = "bots" if any(user.is_bot for user in message.new_chat_members) else None
        elif message.text or message.caption:
            text_content = message.text or message.caption or ""
            text_content_lower = text_content.lower()
            entities = message.entities or message.caption_entities or []

            # Check Swear Words
            swear_action_check = get_lock_action(chat_id, "swear")
            if swear_action_check != 'disabled':
                banned_words = get_banned_words(chat_id)
                if any(word in text_content_lower for word in banned_words):
                    # Find the specific word for the reason message
                    found_word = next((word for word in banned_words if word in text_content_lower), "كلمة")
                    lock_type_to_check = "swear"; violation_reason = f"إرسال كلمات مسيئة ({found_word})"; action = swear_action_check

            # Check Other Text Types if no swear word found
            if not lock_type_to_check:
                if any(e.type == MessageEntityType.SPOILER for e in entities): lock_type_to_check = "spoiler_text"
                elif any(e.type == MessageEntityType.URL for e in entities) or \
                     any(e.type == MessageEntityType.TEXT_LINK for e in entities) or \
                     "http://" in text_content_lower or "https://" in text_content_lower or ".com" in text_content_lower: lock_type_to_check = "link"
                elif any(e.type == MessageEntityType.MENTION for e in entities) or "@" in text_content: lock_type_to_check = "mention"
                elif any(e.type in [MessageEntityType.BOLD, MessageEntityType.ITALIC, MessageEntityType.CODE, MessageEntityType.PRE] for e in entities): lock_type_to_check = "markdown"
                elif message.reply_markup and isinstance(message.reply_markup, InlineKeyboardMarkup): lock_type_to_check = "inline"
                # Removed Arabic check
                elif re.search(r'[a-zA-Z]+', text_content): lock_type_to_check = "english"
                else: # Check long text last for text messages
                    chat_max_length = get_max_message_length(chat_id)
                    if chat_max_length > 0 and len(text_content) > chat_max_length: lock_type_to_check = "long_text"

    # If no lock type identified, allow message
    if not lock_type_to_check: raise ContinuePropagation

    # Check Exemption for the identified lock type
    if await is_exempt_from_protection(client, chat_id, user_id, lock_type_to_check): raise ContinuePropagation

    # Get action if not already set (e.g., by swear check)
    if action == 'disabled':
        action = get_lock_action(chat_id, lock_type_to_check)
        if not violation_reason: violation_reason = LOCK_TYPES.get(lock_type_to_check, 'محتوى ممنوع')

    # Perform the action if not 'disabled'
    if action != 'disabled':
        log.info(f"Protection action '{action}' triggered for user {user_id} in chat {chat_id} for lock type '{lock_type_to_check}'. Reason: {violation_reason}")
        message_deleted = False
        try: await message.delete(); message_deleted = True
        except MessageDeleteForbidden: log.warning(f"Failed to delete message {message.id} during protection enforcement: Bot lacks delete permissions.")
        except Exception as del_err: log.error(f"Failed to delete message {message.id} during protection enforcement: {del_err}")

        if message_deleted: await send_violation_reply(client, message, lock_type_to_check, action)

        if action == 'mute': await mute_user_for_violation(client, chat_id, user_id, violation_reason)
        elif action == 'ban': await ban_user_for_violation(client, chat_id, user_id, violation_reason)
        # Stop propagation implicitly
    else: raise ContinuePropagation # Action is 'disabled'


@app.on_edited_message(filters.group & ~filters.service & ~filters.me, group=5)
async def handle_edited_message(client: Client, message: Message):
    """Handles edited messages to enforce protection rules on the new content and check edit time."""
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0
    if not user_id: return
    if not get_protection_status(chat_id): return

    content_action = 'disabled'
    content_lock_type = None
    content_violation_reason = ""
    content_action_triggered = False

    # --- Check Edited Content ---
    if message.text or message.caption:
        text_content = message.text or message.caption or ""
        text_content_lower = text_content.lower()
        entities = message.entities or message.caption_entities or []

        # Check Swear Words
        swear_action_check = get_lock_action(chat_id, "swear")
        if swear_action_check != 'disabled':
            banned_words = get_banned_words(chat_id)
            if any(word in text_content_lower for word in banned_words):
                found_word = next((word for word in banned_words if word in text_content_lower), "كلمة")
                content_lock_type = "swear"; content_violation_reason = f"تعديل رسالة لتضمين كلمات مسيئة ({found_word})"; content_action = swear_action_check

        # Check Spoiler Text
        if not content_lock_type and any(e.type == MessageEntityType.SPOILER for e in entities):
            content_lock_type = "spoiler_text"; content_action = get_lock_action(chat_id, content_lock_type); content_violation_reason = f"تعديل رسالة لتضمين {LOCK_TYPES.get(content_lock_type, 'نص مشوش')}"

        # Check Links
        if not content_lock_type and (any(e.type == MessageEntityType.URL for e in entities) or any(e.type == MessageEntityType.TEXT_LINK for e in entities) or "http://" in text_content_lower or "https://" in text_content_lower or ".com" in text_content_lower):
            content_lock_type = "link"; content_action = get_lock_action(chat_id, content_lock_type); content_violation_reason = f"تعديل رسالة لتضمين {LOCK_TYPES.get(content_lock_type, 'روابط')}"

        # Check Long Text
        if not content_lock_type:
            chat_max_length = get_max_message_length(chat_id)
            if chat_max_length > 0 and len(text_content) > chat_max_length:
                long_text_action = get_lock_action(chat_id, "long_text")
                if long_text_action != 'disabled':
                    content_lock_type = "long_text"; content_action = long_text_action; content_violation_reason = f"تعديل رسالة لتصبح {LOCK_TYPES.get(content_lock_type, 'رسالة طويلة')}"

        # Check English Text (if other text locks didn't trigger)
        if not content_lock_type and re.search(r'[a-zA-Z]+', text_content):
            english_action = get_lock_action(chat_id, "english")
            if english_action != 'disabled':
                 content_lock_type = "english"; content_action = english_action; content_violation_reason = f"تعديل رسالة لتضمين {LOCK_TYPES.get(content_lock_type, 'اللغة الإنجليزية')}"

    # Check Exemption for Content Violation
    if content_lock_type and content_action != 'disabled':
        if await is_exempt_from_protection(client, chat_id, user_id, content_lock_type): content_action = 'disabled'

    # Perform Action for Content Violation
    if content_action != 'disabled':
        content_action_triggered = True
        log.info(f"Protection action '{content_action}' triggered for user {user_id} in chat {chat_id} for edited content type '{content_lock_type}'. Reason: {content_violation_reason}")
        message_deleted = False
        try: await message.delete(); message_deleted = True
        except MessageDeleteForbidden: log.warning(f"Failed to delete message {message.id} during edit content enforcement: Bot lacks delete permissions.")
        except Exception as del_err: log.error(f"Failed to delete message {message.id} during edit content enforcement: {del_err}")

        if message_deleted: await send_violation_reply(client, message, content_lock_type, content_action)

        if content_action == 'mute': await mute_user_for_violation(client, chat_id, user_id, content_violation_reason)
        elif content_action == 'ban': await ban_user_for_violation(client, chat_id, user_id, content_violation_reason); return

    # --- Check Edit Time Lock ---
    if await is_exempt_from_protection(client, chat_id, user_id): return # General exemption applies to edit time lock too

    edit_action = get_lock_action(chat_id, "edit")
    if edit_action == 'disabled': return

    if message.date and message.edit_date and isinstance(message.date, int) and isinstance(message.edit_date, int):
        time_diff = message.edit_date - message.date
        if time_diff > EDIT_LOCK_DELAY_SECONDS:
            if not content_action_triggered or content_action == 'mute':
                violation_reason = f"تعديل رسالة بعد أكثر من {EDIT_LOCK_DELAY_SECONDS} ثانية"
                log.info(f"Protection action '{edit_action}' triggered for user {user_id} in chat {chat_id} for lock type 'edit'. Reason: {violation_reason}")
                message_deleted = False
                try:
                    if not content_action_triggered: await message.delete(); message_deleted = True
                    else: message_deleted = True # Assumed deleted
                except MessageDeleteForbidden: log.warning(f"Failed to delete message {message.id} during edit time enforcement: Bot lacks delete permissions.")
                except Exception as del_err: log.error(f"Failed to delete message {message.id} during edit time enforcement: {del_err}")

                if message_deleted: await send_violation_reply(client, message, "edit", edit_action)

                if edit_action == 'mute': await mute_user_for_violation(client, chat_id, user_id, violation_reason)
                elif edit_action == 'ban': await ban_user_for_violation(client, chat_id, user_id, violation_reason)
                return


log.info("Protection Plugin with Forward Lock loaded successfully.")

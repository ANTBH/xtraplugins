import datetime
import re
import sqlite3
import time
import asyncio
import os # Import os for lock file handling
import logging # Import the logging module
from datetime import datetime, timedelta, timezone # Import timezone

# --- Configure Logging ---
logging.basicConfig(
    level=logging.INFO, # Set to logging.DEBUG for more verbose output if needed
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()] # Log to console
)
log = logging.getLogger(__name__) # Create a logger instance for this module

from pyrogram import filters, Client
# Added ChatMembersFilter for new commands
from pyrogram.enums import UserStatus, ParseMode, ChatMemberStatus, ChatMembersFilter
# Import specific exceptions for error handling
from pyrogram.errors import PeerIdInvalid, FloodWait, UserIsBlocked, ChatAdminRequired, UserNotParticipant
# Import types needed for new features
from pyrogram.types import (
    Message, User, Chat, ChatMemberUpdated, ChatPrivileges, ChatPermissions, ChatMember
)

# --- Configuration ---
DB_FILE = "user_stats.db" # Database for message counts AND user status
ADMIN_DB_FILE = "admin_actions.db" # Separate DB for admin actions
# ADMIN_IDS list removed - Only Owner is exempt from auto-demote now
DEFAULT_KICK_THRESHOLD = 3
# Delay between actions in loops to avoid FloodWait
LOOP_DELAY_SECONDS = 1.5

# --- New Feature Settings & Variables ---
welcome_enabled = True
# mannof list removed - Promote command cannot be disabled per chat anymore
# muttof list removed - Demote command cannot be disabled per chat anymore

# تعريف صلاحيات المستخدم العادي (بعد فك تقييده)
regular_member_permissions = ChatPermissions(
    can_send_messages=True, can_send_media_messages=True,
    can_send_polls=True, can_send_other_messages=True,
    can_add_web_page_previews=True, can_change_info=False,
    can_invite_users=True, can_pin_messages=False
)
# --- End of New Feature Settings ---


# --- Database Initialization ---
def init_db():
    """Initializes the message count and user status database."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            # Message Counts Table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS message_counts (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            )
            ''')
            # User Status Table (Ensure it exists from other plugins)
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_chat_status (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL, -- 'special', 'muted', or 'admin'
                expiry_timestamp INTEGER, -- NULL for special/admin or permanent mute
                PRIMARY KEY (chat_id, user_id)
            )
            ''')
            # Ensure expiry_timestamp column exists
            try:
                 cursor.execute("ALTER TABLE user_chat_status ADD COLUMN expiry_timestamp INTEGER")
            except sqlite3.OperationalError: pass # Column already exists
            conn.commit()
            log.info(f"Database '{DB_FILE}' initialized successfully.")
    except sqlite3.Error as e:
        log.exception(f"Database initialization error for {DB_FILE}: {e}")
        raise

# --- Initialize Admin Actions Database ---
def init_admin_db():
    """Initializes the admin actions database and tables."""
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            cursor = conn.cursor()
            # Table for kick tracking
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS kick_tracker (
                admin_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                kick_count INTEGER DEFAULT 0,
                first_kick_timestamp INTEGER DEFAULT 0,
                PRIMARY KEY (admin_id, chat_id)
            )
            ''')
            # Table for chat settings
            cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                kick_threshold INTEGER DEFAULT {DEFAULT_KICK_THRESHOLD}
            )
            ''')
            # Add other columns if they don't exist (from other plugins)
            try: cursor.execute("ALTER TABLE chat_settings ADD COLUMN is_chat_locked INTEGER DEFAULT 0")
            except sqlite3.OperationalError: pass
            try: cursor.execute("ALTER TABLE chat_settings ADD COLUMN is_forward_locked INTEGER DEFAULT 0")
            except sqlite3.OperationalError: pass
            try: cursor.execute("ALTER TABLE chat_settings ADD COLUMN protection_enabled INTEGER DEFAULT 1")
            except sqlite3.OperationalError: pass

            conn.commit()
            log.info(f"Database '{ADMIN_DB_FILE}' initialized successfully.")
    except sqlite3.Error as e:
        log.exception(f"Database initialization error for {ADMIN_DB_FILE}: {e}")
        raise
# --- End of Admin DB Init ---

# --- Placeholder for Pyrogram App Instance ---
# Assign app instance
try:
    # Attempt to import the main app instance from YukkiMusic
    from YukkiMusic import app as yukki_app
    # Validate if the imported app is a Pyrogram Client instance
    if not isinstance(yukki_app, Client): raise ImportError("App is not a Client instance")
    app = yukki_app # Assign the imported app to the global 'app' variable
    log.info("Successfully imported YukkiMusic app.")
    # Initialize databases only if the app is successfully imported
    init_db()
    init_admin_db()
except ImportError:
    # Log a critical error if the app cannot be imported, as the plugin depends on it
    log.critical("YukkiMusic.app not found or invalid. This plugin WILL NOT WORK without it.")
    # Define a dummy app class to prevent load-time crashes if the real app is missing
    class DummyApp:
        # Dummy decorator to absorb @app.on_message calls
        def on_message(self, *args, **kwargs): return lambda f: f
        # Dummy decorator to absorb @app.on_chat_member_updated calls
        def on_chat_member_updated(self, *args, **kwargs): return lambda f: f
    app = DummyApp() # Assign the dummy app; functionality will be lost
# --- End of Placeholder ---


# --- Helper Functions ---
def LastOnline(user: User):
    """Formats the user's last online status."""
    if user.is_bot: return "بوت 🤖"
    if user.status == UserStatus.RECENTLY: return "متصل مؤخرًا 👤"
    # Corrected UserStatus names
    elif user.status == UserStatus.LAST_WEEK: return "خلال الأسبوع الماضي 📅"
    elif user.status == UserStatus.LAST_MONTH: return "خلال الشهر الماضي 🗓️"
    elif user.status == UserStatus.LONG_AGO: return "منذ فترة طويلة ⏳"
    elif user.status == UserStatus.ONLINE: return "متصل الآن 🟢"
    elif user.status == UserStatus.OFFLINE:
        try:
            if user.last_online_date:
                # Convert timestamp to datetime object with UTC timezone
                last_seen_date = datetime.fromtimestamp(user.last_online_date, tz=timezone.utc)
                # Format the datetime object
                return f"غير متصل | آخر ظهور: {last_seen_date.strftime('%Y/%m/%d %H:%M')} (UTC) ⚫"
            else: return "غير متصل ⚫"
        except Exception: return "غير متصل ⚫" # Fallback for any formatting errors
    else: return "غير معروف ❓"

def FullName(user: User):
    """Returns the user's full name, escaping HTML characters."""
    # Escape HTML special characters in first name, handle None case
    first = user.first_name.replace("<", "&lt;").replace(">", "&gt;") if user.first_name else ""
    # Escape HTML special characters in last name, handle None case
    last = user.last_name.replace("<", "&lt;").replace(">", "&gt;") if user.last_name else ""
    # Combine first and last names, strip leading/trailing whitespace
    full_name = (first + " " + last).strip()
    # Return the full name, or a placeholder if the name is empty (e.g., deleted account)
    return full_name if full_name else "مستخدم محذوف"

def GetRank(message_count: int):
    """Determines the user's rank based on message count."""
    if message_count < 0: return "خطأ في العد" # Handle error case
    if message_count < 100: return "⚰️ عضو ميت"
    elif message_count < 500: return "👻 عضو صامت"
    elif message_count < 1000: return "🌱 عضو جديد"
    elif message_count < 4000: return "💬 عضو نشيط"
    elif message_count < 8000: return "⭐ عضو مميز"
    elif message_count < 10000: return "🌟 عضو خبير"
    else: return "👑 أسطورة المجموعة"

def GetCountPraise(message_count: int) -> str:
    """Returns a praise/insult string based on message count."""
    if message_count < 0: return "حدث خطأ ما في حساب رسائلك."
    elif message_count == 0: return "لم ترسل أي رسالة بعد! 寂"
    elif message_count < 100: return f"يا للكسل! لديك **{message_count}** رسالة فقط؟ تحتاج إلى المزيد من التفاعل! 🧟"
    elif message_count < 500: return f"هممم، **{message_count}** رسالة. بداية مقبولة، لكن لا تزال تحتاج للمزيد! 🤔"
    elif message_count < 1000: return f"جيد! **{message_count}** رسالة. أنت في الطريق الصحيح لتكون عضوًا فعالاً. 👍"
    elif message_count < 4000: return f"ممتاز! **{message_count}** رسالة. تفاعلك ملحوظ ومقدر! ✨"
    elif message_count < 8000: return f"رائع! **{message_count}** رسالة. أنت نجم لامع في هذه المجموعة! ⭐"
    elif message_count < 10000: return f"مذهل! **{message_count}** رسالة. خبرتك وتواجدك قيم جداً! 🌟"
    else: return f"ما شاء الله تبارك الله! **{message_count}** رسالة! أنت حقاً أسطورة هذه المجموعة وعمودها الفقري! 👑💪"

# --- NEW: Helper functions to check bot-defined roles ---
def get_user_bot_status(chat_id: int, user_id: int) -> tuple[str | None, int | None]:
    """
    Checks the user_chat_status table for a user's status ('admin', 'special', 'muted').
    Returns (status, expiry_timestamp). expiry_timestamp is only relevant for 'muted'.
    Returns (None, None) if no status found or on error.
    """
    try:
        # Connect to the database
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            # Query the status and expiry timestamp for the given chat and user
            cursor.execute("SELECT status, expiry_timestamp FROM user_chat_status WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
            result = cursor.fetchone()
            if result:
                status, expiry_ts = result
                # Check if the status is 'muted' and if it has expired
                if status == 'muted' and expiry_ts is not None and expiry_ts < int(time.time()):
                    log.info(f"Mute expired for user {user_id} in chat {chat_id} during status check. Removing record.")
                    # Delete the expired mute record
                    cursor.execute("DELETE FROM user_chat_status WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
                    conn.commit()
                    return None, None # Return None as the user is no longer muted
                return status, expiry_ts # Return the current status and expiry
            else:
                return None, None # No status record found for this user in this chat
    except sqlite3.Error as e:
        # Handle cases where the table or column might be missing (e.g., first run)
        if "no such table" in str(e) or "no such column" in str(e):
             log.warning(f"User status check failed: table 'user_chat_status' or columns not found in {DB_FILE}.")
        else:
             log.exception(f"[DB:{DB_FILE}] Error checking user status table: {e}")
    return None, None # Return None on any error
# --- End NEW ---

# --- Helper function to check basic admin permissions ---
# This remains useful for commands like promote/demote/set threshold
async def check_permission(client: Client, chat_id: int, user_id: int, permission_check: str = "can_restrict_members") -> bool:
    """Checks if a user has the required permission or is the owner."""
    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status == ChatMemberStatus.OWNER:
            return True # Owner always has permission
        if member.status == ChatMemberStatus.ADMINISTRATOR:
            if member.privileges:
                # Check if the specific permission attribute exists and is True
                if getattr(member.privileges, permission_check, False):
                    return True
        log.warning(f"User {user_id} lacks permission '{permission_check}' or admin status in chat {chat_id}")
        return False
    except UserNotParticipant:
         log.warning(f"User {user_id} is not a participant in chat {chat_id} during permission check.")
         return False # User not in chat
    except Exception as e:
        log.exception(f"Error checking permissions for user {user_id} in chat {chat_id}: {e}")
        return False # Assume no permission on error

# --- NEW: Helper function for the specific permission check for clear commands ---
async def check_clear_permission(client: Client, chat_id: int, user_id: int) -> bool:
    """Checks if user is Owner OR Admin with can_promote_members AND can_change_info."""
    try:
        member = await client.get_chat_member(chat_id, user_id)
        # Check if Owner
        if member.status == ChatMemberStatus.OWNER:
            return True
        # Check if Admin with specific privileges
        if member.status == ChatMemberStatus.ADMINISTRATOR:
            if member.privileges and \
               member.privileges.can_promote_members and \
               member.privileges.can_change_info:
                return True
        # If neither condition is met
        log.warning(f"User {user_id} lacks required permissions (Owner or Admin with Promote+ChangeInfo) for clear command in chat {chat_id}")
        return False
    except UserNotParticipant:
        log.warning(f"User {user_id} is not a participant in chat {chat_id} during clear permission check.")
        return False
    except Exception as e:
        log.exception(f"Error checking clear permissions for user {user_id} in chat {chat_id}: {e}")
        return False # Assume no permission on error

# --- End of Helper Functions ---


# --- Message Counting Handler (Using SQLite) ---
@app.on_message(filters.group & ~filters.service & ~filters.bot & filters.text, group=-1)
async def count_new_message(client: Client, message: Message):
    """Increments the message count for a user in a specific chat using SQLite."""
    if not message.from_user: return # Ignore messages without a sender (e.g., channel posts)
    chat_id = message.chat.id
    user_id = message.from_user.id
    try:
        # Connect to the database
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            # Use INSERT OR CONFLICT to handle existing users efficiently
            # If the user exists (conflict on primary key), update the count by adding 1
            # Otherwise, insert a new row with count = 1
            cursor.execute('''
            INSERT INTO message_counts (chat_id, user_id, count) VALUES (?, ?, 1)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET count = count + 1
            ''', (chat_id, user_id))
            conn.commit() # Save the changes
    except sqlite3.Error as e:
        # Log database-specific errors
        log.error(f"[DB:{DB_FILE}] Database error while counting message ({chat_id}, {user_id}): {e}")
    except Exception as ex:
        # Log any other unexpected errors
        log.exception(f"Unexpected error in count_new_message (SQLite) ({chat_id}, {user_id})")


# --- Main 'whois' Command Handler (Reads from SQLite & Checks Roles) ---
@app.on_message(filters.command("كشف", prefixes=[""]) & filters.group)
async def whois_arabic(client: Client, message: Message):
    """Handles the 'كشف' command to display user information including roles."""
    command_used = message.text.split()[0]
    log.info(f"'{command_used}' command triggered by user {message.from_user.id} in chat {message.chat.id}")

    user_id = None
    target_user = None
    args = message.command

    # 1. Determine the target user (Reply > Argument > Self)
    try:
        if message.reply_to_message:
            log.info(f"'{command_used}' is a reply.")
            target_user = message.reply_to_message.from_user
            if target_user: user_id = target_user.id
            else:
                # Handle cases like replies to forwarded messages or deleted accounts
                log.warning("Could not get user from the replied message directly.")
                if message.reply_to_message.forward_from:
                    target_user = message.reply_to_message.forward_from
                    user_id = target_user.id
                    log.info(f"Target user determined from forwarded message: {user_id}")
                elif message.reply_to_message.forward_from_chat:
                    log.warning("Replied to a message forwarded from a channel.")
                    await message.reply("⚠️ لا يمكن الحصول على معلومات المستخدم من رسالة معاد توجيهها من قناة.")
                    return
                else:
                    await message.reply("⚠️ لا يمكن الحصول على معلومات المستخدم من هذه الرسالة المردود عليها (قد يكون الحساب محذوفاً).")
                    return
        elif len(args) > 1:
            arg = args[1]
            log.info(f"'{command_used}' has argument: {arg}")
            try: user_id = int(arg) # Try parsing as ID first
            except ValueError: user_id = arg.lstrip('@') # Assume username if not integer
            log.info(f"Argument parsed as identifier: {user_id}")
            if not user_id:
                log.warning("Invalid user ID or username provided.")
                await message.reply("⚠️ معرف أو اسم المستخدم غير صالح.")
                return
        else: # No reply or argument, target the command sender
            log.info(f"'{command_used}' has no arguments or reply, targeting sender.")
            target_user = message.from_user
            user_id = target_user.id

        if user_id is None:
            log.error(f"Failed to determine target user for '{command_used}'.")
            await message.reply("⚠️ لم يتم تحديد مستخدم.")
            return

        # 2. Get user information object (if not already obtained)
        log.info(f"Attempting to get user info for: {user_id}")
        try:
            # Fetch user info only if we don't have the User object already
            if target_user is None:
                target_user = await client.get_users(user_id)
            log.info(f"Successfully got user info for {target_user.id}")
        except PeerIdInvalid:
            log.warning(f"User not found: {user_id}")
            await message.reply("❌ لا يمكنني العثور على هذا المستخدم.")
            return
        except Exception as e:
            log.exception(f"Error fetching user info for {user_id}")
            await message.reply(f"⚠️ حدث خطأ أثناء جلب معلومات المستخدم: {e}")
            return

        # --- 3. Get user's message count from SQLite ---
        message_count = 0
        log.info(f"Querying database '{DB_FILE}' for message count: chat={message.chat.id}, user={target_user.id}")
        try:
            with sqlite3.connect(DB_FILE) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT count FROM message_counts WHERE chat_id = ? AND user_id = ?',
                               (message.chat.id, target_user.id))
                result = cursor.fetchone()
                if result: message_count = result[0]
                else: message_count = 0 # Default to 0 if no record found
                log.info(f"Found message count in SQLite: {message_count}")
        except sqlite3.Error as e:
            log.error(f"[DB:{DB_FILE}] Database error reading count ({message.chat.id}, {target_user.id}): {e}")
            message_count = -1 # Indicate database error
        except Exception as ex:
            log.exception(f"Unexpected error reading count from SQLite ({message.chat.id}, {target_user.id})")
            message_count = -1 # Indicate other error

        # --- 4. Determine Role (Telegram Status + Bot DB Status) ---
        role_string = "👤 عضو" # Default role
        try:
             # Get the user's membership status in the chat from Telegram
             member = await client.get_chat_member(message.chat.id, target_user.id)
             if member.status == ChatMemberStatus.OWNER:
                 role_string = "👑 المالك"
             elif member.status == ChatMemberStatus.ADMINISTRATOR:
                 role_string = "🛡️ مشرف"
             else: # If not owner/admin in Telegram, check the bot's database status
                  bot_status, expiry = get_user_bot_status(message.chat.id, target_user.id)
                  if bot_status == 'admin':
                      role_string = "🧑‍💼 ادمن بوت"
                  elif bot_status == 'special':
                      role_string = "✨ مميز"
                  elif bot_status == 'muted':
                      # If muted, try to display remaining time
                      if expiry:
                          try:
                              expiry_dt = datetime.fromtimestamp(expiry, tz=timezone.utc)
                              now_dt = datetime.now(timezone.utc)
                              remaining = expiry_dt - now_dt
                              if remaining.total_seconds() > 0:
                                  # Simple remaining time format (d, h, m)
                                  days, rem_secs = divmod(remaining.total_seconds(), 86400)
                                  hours, rem_secs = divmod(rem_secs, 3600)
                                  mins, _ = divmod(rem_secs, 60)
                                  time_left = ""
                                  if days > 0: time_left += f"{int(days)}ي "
                                  if hours > 0: time_left += f"{int(hours)}س "
                                  if mins > 0: time_left += f"{int(mins)}د"
                                  role_string = f"🔇 مكتوم (متبقي: ~{time_left.strip()})"
                              else: # Should have been cleared by get_user_bot_status but wasn't
                                  role_string = "🔇 مكتوم (انتهى)"
                          except Exception as time_err: # Fallback if time calculation fails
                              log.error(f"Error calculating remaining mute time: {time_err}")
                              role_string = "🔇 مكتوم"
                      else: # Mute record exists but no expiry (permanent?)
                          role_string = "🔇 مكتوم (دائم)"
        except UserNotParticipant:
             role_string = " خارج المجموعة" # User is not in the group
             log.info(f"User {target_user.id} is not a participant in chat {message.chat.id}")
        except Exception as e:
             log.error(f"Could not determine role for {target_user.id} in chat {message.chat.id}: {e}")
             role_string = "❓ (خطأ)" # Indicate error determining role
        log.info(f"Determined role for {target_user.id}: {role_string}")
        # --- End Role Determination ---

        # 5. Determine Rank based on message count
        rank = GetRank(message_count)
        # Format message count, showing "خطأ" if there was an error reading count
        message_count_str = str(message_count) if message_count != -1 else "خطأ"
        log.info(f"User rank determined: {rank} (Count: {message_count_str})")

        # 6. Format the response text using HTML
        full_name_html = FullName(target_user) # Get HTML-safe name
        user_id_html = target_user.id
        username_mention_html = f"@{target_user.username}" if target_user.username else "لا يوجد"
        last_online_html = LastOnline(target_user) # Get formatted last online status

        # Construct the HTML response string
        html_text = (
            f"👤 <b>معلومات المستخدم:</b> <a href='tg://user?id={user_id_html}'>{full_name_html}</a>\n\n"
            f"🔹 <b>الايدي (ID):</b> <code>{user_id_html}</code>\n"
            f"🔹 <b>اسم المستخدم:</b> {username_mention_html}\n"
            f"📝 <b> الصلاحيات :</b> {role_string}\n" # <-- Added Role Line
            f"⏱️ <b>آخر ظهور:</b> {last_online_html}\n"
            f"💬 <b>عدد الرسائل هنا:</b> <code>{message_count_str}</code>\n"
            f"🎖️ <b>الرتبة  :</b> {rank}"
        )
        log.info("Formatted response text.")

        # 7. Send the reply using HTML Parse Mode
        await message.reply(
            html_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True, # Disable link previews in the response
        )
        log.info(f"Successfully sent '{command_used}' response for user {target_user.id}")

    except Exception as e:
        # Catch-all for any unexpected errors during the command execution
        log.exception(f"Unexpected error in whois_arabic handler for chat {message.chat.id}")
        try:
            # Try to inform the user about the error
            await message.reply(f"❌ حدث خطأ غير متوقع أثناء معالجة الأمر: {e}")
        except Exception as reply_err:
            # Log if sending the error message itself fails
            log.error(f"Failed to send error reply message: {reply_err}")


# --- My Rank Command (Reads from SQLite) ---
@app.on_message(filters.command("رتبتي", prefixes=[""]) & filters.group )
async def my_rank(client: Client, message: Message):
    """Handles the 'رتبتي' command to display the user's rank, reading count from SQLite."""
    user_id = message.from_user.id
    chat_id = message.chat.id
    log.info(f"'رتبتي' command triggered by user {user_id} in chat {chat_id}")

    message_count = 0
    try:
        # Connect to DB and fetch message count for the user in this chat
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT count FROM message_counts WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
            result = cursor.fetchone()
            if result: message_count = result[0]
            log.info(f"User {user_id} message count from SQLite in chat {chat_id}: {message_count}")
    except sqlite3.Error as e:
        log.error(f"[DB:{DB_FILE}] Database error reading count for rank ({chat_id}, {user_id}): {e}")
        await message.reply("⚠️ حدث خطأ أثناء جلب رتبتك من قاعدة البيانات.")
        return
    except Exception as ex:
        log.exception(f"Unexpected error reading count for rank ({chat_id}, {user_id})")
        await message.reply("⚠️ حدث خطأ غير متوقع.")
        return

    # Determine rank and get praise text based on count
    rank = GetRank(message_count)
    # Extract only the praise part from the GetCountPraise function's result
    praise = GetCountPraise(message_count).split('\n')[-1]

    # --- FIX: Manually construct Markdown mention ---
    # Get user's first name, providing a fallback if it's missing
    user_first_name = message.from_user.first_name or f"User {user_id}"
    # Construct the Markdown mention string manually
    user_mention_markdown = f"[{user_first_name}](tg://user?id={user_id})"

    # Format the reply text using Markdown and the manually constructed mention
    reply_text = f"يا {user_mention_markdown}، رتبتك الحالية هي:\n\n🏅 **{rank}** 🏅\n\n{praise}"

    try:
        # Send the reply using Markdown parse mode
        await message.reply_text(reply_text, parse_mode=ParseMode.MARKDOWN)
        log.info(f"Successfully replied to 'رتبتي' command for user {user_id}")
    except Exception as e:
        log.exception(f"Failed to send 'رتبتي' reply to user {user_id} in chat {chat_id}")


# --- My Messages Command (Reads from SQLite) ---
@app.on_message(filters.command("رسائلي", prefixes=[""]) & filters.group )
async def my_messages(client: Client, message: Message):
    """Handles the 'رسائلي' command to display the user's message count from SQLite."""
    user_id = message.from_user.id
    chat_id = message.chat.id
    log.info(f"'رسائلي' command triggered by user {user_id} in chat {chat_id}")

    message_count = 0
    try:
        # Connect to DB and fetch message count
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT count FROM message_counts WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
            result = cursor.fetchone()
            if result: message_count = result[0]
            log.info(f"User {user_id} message count from SQLite in chat {chat_id}: {message_count}")
    except sqlite3.Error as e:
        log.error(f"[DB:{DB_FILE}] Database error reading count for messages ({chat_id}, {user_id}): {e}")
        await message.reply("⚠️ حدث خطأ أثناء جلب عدد رسائلك من قاعدة البيانات.")
        return
    except Exception as ex:
        log.exception(f"Unexpected error reading count for messages ({chat_id}, {user_id})")
        await message.reply("⚠️ حدث خطأ غير متوقع.")
        return

    # Get the full praise text based on the message count
    praise_text = GetCountPraise(message_count)

    # --- FIX: Manually construct Markdown mention ---
    # Get user's first name, providing a fallback if it's missing
    user_first_name = message.from_user.first_name or f"User {user_id}"
    # Construct the Markdown mention string manually
    user_mention_markdown = f"[{user_first_name}](tg://user?id={user_id})"

    # Format the reply text using Markdown and the manually constructed mention
    reply_text = f"يا {user_mention_markdown}،\n{praise_text}"

    try:
        # Send the reply using Markdown parse mode
        await message.reply_text(reply_text, parse_mode=ParseMode.MARKDOWN)
        log.info(f"Successfully replied to 'رسائلي' command for user {user_id}")
    except Exception as e:
        log.exception(f"Failed to send 'رسائلي' reply to user {user_id} in chat {chat_id}")


# --- Auto-Demote Handler (Using SQLite for Kick Tracking & Settings) ---
@app.on_chat_member_updated()
async def auto_demote_on_kick(client: Client, chat_member_updated: ChatMemberUpdated):
    """
    Monitors chat member updates. If an admin kicks members exceeding the threshold within 24 hours,
    they will be automatically demoted. Uses admin_actions.db SQLite database.
    Owner is exempt.
    """
    if not welcome_enabled: return # Check if the feature is globally enabled

    try:
        # Check if the update represents a user being banned (kicked) by someone else
        if (chat_member_updated.new_chat_member and
            chat_member_updated.new_chat_member.status == ChatMemberStatus.BANNED and
            chat_member_updated.new_chat_member.restricted_by): # Ensure the ban was performed by someone

            kicked_by = chat_member_updated.new_chat_member.restricted_by
            # Ignore kicks performed by the bot itself or if the kicker is somehow None
            if kicked_by is None or kicked_by.is_self: return

            admin_id = kicked_by.id # The ID of the admin who performed the kick
            chat_id = chat_member_updated.chat.id
            now_timestamp = int(datetime.now().timestamp()) # Current time as Unix timestamp
            time_window_seconds = 86400 # 24 hours in seconds

            log.info(f"User {chat_member_updated.new_chat_member.user.id} was kicked by admin {admin_id} in chat {chat_id}.")

            # --- Get kick threshold for this chat from SQLite DB ---
            kick_threshold = DEFAULT_KICK_THRESHOLD # Start with the default
            try:
                with sqlite3.connect(ADMIN_DB_FILE) as conn_settings:
                    cursor_settings = conn_settings.cursor()
                    # Query the specific threshold for this chat_id
                    cursor_settings.execute("SELECT kick_threshold FROM chat_settings WHERE chat_id = ?", (chat_id,))
                    result = cursor_settings.fetchone()
                    if result: kick_threshold = result[0] # Use the chat-specific threshold if found
            except sqlite3.Error as db_settings_err:
                 # Log error if threshold cannot be read, but continue with the default
                 log.exception(f"[DB:{ADMIN_DB_FILE}] Failed to read kick threshold for chat {chat_id}. Using default {DEFAULT_KICK_THRESHOLD}.")
                 kick_threshold = DEFAULT_KICK_THRESHOLD
            # --- End Get threshold ---

            # --- SQLite Interaction for Kick Tracking ---
            current_kick_count = 0
            should_reset_db = False # Flag to indicate if the DB count should be reset later
            try:
                with sqlite3.connect(ADMIN_DB_FILE) as conn:
                    cursor = conn.cursor()
                    # Check if there's an existing kick record for this admin in this chat
                    cursor.execute(
                        "SELECT kick_count, first_kick_timestamp FROM kick_tracker WHERE admin_id = ? AND chat_id = ?",
                        (admin_id, chat_id)
                    )
                    record = cursor.fetchone()

                    if record: # If a record exists
                        count, first_kick_ts = record
                        # Check if the first kick in the current window is older than 24 hours
                        if now_timestamp - first_kick_ts > time_window_seconds:
                            # Reset the count and timestamp as the window has expired
                            log.info(f"Resetting SQLite kick count for admin {admin_id} in chat {chat_id} (over {time_window_seconds}s).")
                            cursor.execute(
                                "UPDATE kick_tracker SET kick_count = 1, first_kick_timestamp = ? WHERE admin_id = ? AND chat_id = ?",
                                (now_timestamp, admin_id, chat_id)
                            )
                            current_kick_count = 1
                        else:
                            # Increment the kick count within the time window
                            new_count = count + 1
                            log.info(f"Incrementing SQLite kick count for admin {admin_id} in chat {chat_id} to {new_count}.")
                            cursor.execute(
                                "UPDATE kick_tracker SET kick_count = ? WHERE admin_id = ? AND chat_id = ?",
                                (new_count, admin_id, chat_id)
                            )
                            current_kick_count = new_count
                    else: # No record exists, this is the first kick in a potential window
                        log.info(f"Admin {admin_id} first kick recorded in chat {chat_id} (SQLite).")
                        cursor.execute(
                            "INSERT INTO kick_tracker (admin_id, chat_id, kick_count, first_kick_timestamp) VALUES (?, ?, 1, ?)",
                            (admin_id, chat_id, now_timestamp)
                        )
                        current_kick_count = 1
                    conn.commit() # Save changes to kick_tracker table

            except sqlite3.Error as db_err:
                log.exception(f"[DB:{ADMIN_DB_FILE}] Error updating kick tracker for admin {admin_id} in chat {chat_id}")
                return # Exit if DB interaction fails
            # --- End SQLite Interaction ---

            # Check if the admin has reached the threshold
            # The check for ADMIN_IDS list was removed. Only Owner check remains.
            if current_kick_count >= kick_threshold:
                 log.warning(f"Admin {admin_id} reached kick threshold ({current_kick_count}/{kick_threshold}) in chat {chat_id}. Attempting demotion.")
                 should_reset_db = True # Mark for DB reset after attempting demotion
                 try:
                     # Check if the target is the chat owner before demoting
                     target_member = await client.get_chat_member(chat_id, admin_id)
                     if target_member.status == ChatMemberStatus.OWNER:
                         log.warning(f"Attempted to auto-demote owner {admin_id} in chat {chat_id}. Skipping (Owner is exempt).")
                         # Don't demote owner, the finally block below will reset the count
                     else:
                         # Demote the admin by setting empty privileges
                         await client.promote_chat_member(
                             chat_id=chat_id, user_id=admin_id,
                             privileges=ChatPrivileges() # Empty privileges demotes
                         )
                         # Send notification message to the chat
                         message_text = (f"🛡️ **منع التصفية التلقائي** 🛡️\n\n"
                                         f"👤 المستخدم: [{kicked_by.first_name}](tg://user?id={admin_id})\n"
                                         f"📉 **تم تنزيله من قائمة المشرفين**\n\n"
                                         f"⚖️ السبب: قام بطرد **{current_kick_count}** أعضاء خلال 24 ساعة (الحد: {kick_threshold}).")
                         await client.send_message(chat_id, message_text)
                         log.info(f"Admin {admin_id} demoted successfully in chat {chat_id}.")

                 except ChatAdminRequired:
                     # Log and notify if the bot lacks permission to demote
                     log.error(f"Failed to auto-demote admin {admin_id} in chat {chat_id}: Bot lacks admin privileges.")
                     await client.send_message(chat_id, f"⚠️ فشل التنزيل التلقائي للمشرف [{kicked_by.first_name}](tg://user?id={admin_id}). البوت لا يملك صلاحيات كافية.")
                 except Exception as e:
                     # Log and notify about other errors during demotion
                     log.exception(f"Error auto-demoting admin {admin_id} in chat {chat_id}")
                     await client.send_message(chat_id, f"⚠️ حدث خطأ أثناء محاولة تنزيل المشرف [{kicked_by.first_name}](tg://user?id={admin_id}) تلقائياً. الخطأ: {e}")
                 # The kick count will be reset in the finally block regardless of success/failure here

            # --- Reset DB count if the threshold was reached (successful demotion, failed demotion, or owner check) ---
            if should_reset_db:
                try:
                    # Connect to the DB again to delete the tracker record
                    with sqlite3.connect(ADMIN_DB_FILE) as conn_reset:
                        conn_reset.execute("DELETE FROM kick_tracker WHERE admin_id = ? AND chat_id = ?", (admin_id, chat_id))
                        log.info(f"Reset DB kick count for admin {admin_id} in chat {chat_id} after demotion/threshold check.")
                except Exception as reset_err:
                    # Log if resetting the count fails
                    log.exception(f"Error resetting kick count for admin {admin_id} in chat {chat_id} after demotion/threshold check: {reset_err}")

    except Exception as e:
        # Catch-all for errors in the main handler logic
        log.exception(f"Error in on_chat_member_updated handler: {e}")


# --- Promote Command ---
@app.on_message(filters.command("رفع مشرف", prefixes=[""]) & filters.group )
async def promote_user_to_admin(client: Client, message: Message):
    """
    Promotes the specified user (by reply or username/ID) to admin
    with specific privileges and no custom title.
    Checks only the can_promote_members permission of the requester.
    """
    group_id = message.chat.id
    user_making_request = message.from_user
    command_used = message.command[0] if message.command else "Unknown Command"
    log.info(f"'{command_used}' command triggered by {user_making_request.id} in chat {group_id}")

    # --- Permission Check: Requester needs 'can_promote_members' or be Owner ---
    # Use the basic permission checker here
    if not await check_permission(client, group_id, user_making_request.id, "can_promote_members"):
         await message.reply_text(f"عذراً [{user_making_request.mention}]، لا تملك الصلاحيات اللازمة لرفع مشرفين.")
         return

    # Check for disabling command via mannof list was removed.

    # Determine target user (Reply > Argument)
    target_user = None
    target_user_id = None
    if message.reply_to_message:
        if message.reply_to_message.from_user:
            target_user = message.reply_to_message.from_user
            target_user_id = target_user.id
            log.info(f"Target user from reply: {target_user_id}")
        else:
             log.warning("Replied message has no 'from_user'. Cannot promote.")
             await message.reply_text("لا يمكن رفع مشرف من هذه الرسالة (قد تكون رسالة قناة أو حساب محذوف).")
             return
    else:
        args = message.command
        if len(args) > 1:
            user_identifier = args[1]
            log.info(f"Target user identifier from args: {user_identifier}")
            try:
                # Fetch the user object using the identifier (ID or username)
                target_user = await client.get_users(user_identifier)
                target_user_id = target_user.id
                log.info(f"Found target user by identifier: {target_user_id}")
            except Exception as e:
                log.error(f"Error getting user by identifier '{user_identifier}': {e}")
                await message.reply_text("لم أتمكن من العثور على المستخدم. يرجى استخدام اسم مستخدم صحيح أو الرد على رسالة المستخدم.")
                return
        else: # No reply and no argument
            await message.reply_text("يرجى الرد على رسالة المستخدم الذي تريد رفعه أو كتابة يوزره او الايدي بعد الأمر.")
            return

    if not target_user_id:
        log.error("Failed to determine target user for promotion.")
        await message.reply_text("لم يتم تحديد المستخدم المستهدف.")
        return

    # Prevent promoting the bot itself
    try:
        bot_me = await client.get_me()
        if target_user_id == bot_me.id:
            log.info("Attempted to promote the bot itself.")
            await message.reply_text("لا يمكنك رفع البوت نفسه.")
            return
    except Exception as e:
        log.error(f"Could not get bot's own info: {e}")
        # Continue, but promotion might fail later if target is bot

    # Define the specific privileges to grant to the new admin
    promote_privileges = ChatPrivileges(
        can_manage_chat=True, can_delete_messages=True,
        can_manage_video_chats=True, can_restrict_members=True,
        can_promote_members=False, # Don't allow them to promote others by default
        can_change_info=False, # Don't allow them to change group info by default
        can_invite_users=True, can_pin_messages=True,
        is_anonymous=False # Don't make them anonymous admin by default
    )
    log.info(f"Attempting to promote user {target_user_id} with privileges: {promote_privileges}")
    try:
        # Promote the user
        await client.promote_chat_member(
            chat_id=group_id, user_id=target_user_id, privileges=promote_privileges
        )
        # Fetch target_user object again if we only had the ID, to ensure we have the mention
        if target_user is None: target_user = await client.get_users(target_user_id)

        log.info(f"User {target_user_id} promoted by {user_making_request.id} in chat {group_id}")
        requester_name = user_making_request.first_name # Get requester's first name for the message
        # Send success message
        await message.reply_text(f"✅ تم رفع العضو [{target_user.mention}](tg://user?id={target_user_id}) إلى مشرف.\n\n بواسطة: {requester_name}")

    except ChatAdminRequired:
         # Handle error if the bot lacks permission to promote
         log.error(f"Failed to promote {target_user_id} in chat {group_id}: Bot lacks admin privileges.")
         target_mention = f"[{target_user_id}](tg://user?id={target_user_id})"
         if target_user: target_mention = target_user.mention # Use mention if available
         await message.reply_text(f"⚠️ فشلت في رفع المستخدم {target_mention}. البوت لا يملك صلاحيات كافية.")
    except Exception as e:
        # Handle other potential errors during promotion
        log.exception(f"Error promoting user {target_user_id} in chat {group_id}")
        target_mention = f"[{target_user_id}](tg://user?id={target_user_id})"
        if target_user: target_mention = target_user.mention # Use mention if available
        await message.reply_text(f"⚠️ حدث خطأ أثناء محاولة رفع المستخدم {target_mention}. الخطأ: {e}")


# --- Demote Command ---
@app.on_message(filters.command(["تنزيل مشرف", "تنزيل المشرف"], prefixes=[""]) & filters.group )
async def demote_admin(client: Client, message: Message):
    """
    Demotes the specified admin (by reply or username/ID) by removing their admin privileges
    and restricting them to regular member permissions.
    Checks only the can_promote_members permission of the requester.
    """
    group_id = message.chat.id
    user_making_request = message.from_user
    command_used = message.command[0] if message.command else "Unknown Command"
    log.info(f"'{command_used}' command triggered by {user_making_request.id} in chat {group_id}")

    # --- Permission Check: Requester needs 'can_promote_members' or be Owner ---
    # Note: Demoting uses the same 'can_promote_members' check as promoting
    # Use the basic permission checker here
    if not await check_permission(client, group_id, user_making_request.id, "can_promote_members"):
         await message.reply_text(f"عذراً [{user_making_request.mention}]، لا تملك الصلاحيات اللازمة لتنزيل مشرفين.")
         return

    # Check for disabling command via muttof list was removed.

    # --- Determine target user (Reply OR Argument) ---
    target_user = None
    target_user_id = None
    if message.reply_to_message:
        if message.reply_to_message.from_user:
            target_user = message.reply_to_message.from_user
            target_user_id = target_user.id
            log.info(f"Target user from reply: {target_user_id}")
        else:
             log.warning("Replied message has no 'from_user'. Cannot demote.")
             await message.reply_text("لا يمكن تنزيل مشرف من هذه الرسالة (قد تكون رسالة قناة أو حساب محذوف).")
             return
    else:
        args = message.command
        if len(args) > 1:
            user_identifier = args[1]
            log.info(f"Target user identifier from args: {user_identifier}")
            try:
                # Fetch user object to check status later and get mention
                target_user = await client.get_users(user_identifier)
                target_user_id = target_user.id
                log.info(f"Found target user by identifier: {target_user_id}")
            except Exception as e:
                log.error(f"Error getting user by identifier '{user_identifier}': {e}")
                await message.reply_text("لم أتمكن من العثور على المستخدم. يرجى استخدام اسم مستخدم صحيح أو الرد على رسالة المستخدم.")
                return
        else: # No reply and no argument
            await message.reply_text("يرجى الرد على رسالة المشرف الذي تريد تنزيله أو كتابة معرفه/اسم مستخدمه بعد الأمر.")
            return

    if not target_user_id:
        log.error("Failed to determine target user for demotion.")
        await message.reply_text("لم يتم تحديد المستخدم المستهدف.")
        return
    # --- End Target User Determination ---

    # Prevent demoting the bot itself
    try:
        bot_me = await client.get_me()
        if target_user_id == bot_me.id:
            log.info("Attempted to demote the bot itself.")
            await message.reply_text("لا يمكنك تنزيل البوت نفسه.")
            return
    except Exception as e:
        log.error(f"Could not get bot's own info: {e}")

    # Prevent demoting the chat owner and check if target is actually an admin
    try:
        target_user_member = await client.get_chat_member(group_id, target_user_id)
        if target_user_member.status == ChatMemberStatus.OWNER:
            log.warning(f"User {user_making_request.id} attempted to demote owner {target_user_id} in chat {group_id}")
            await message.reply_text("لا يمكنك تنزيل مالك المجموعة.")
            return
        # Optional: Check if the target is actually an admin before trying to demote
        if target_user_member.status != ChatMemberStatus.ADMINISTRATOR:
             log.warning(f"Target user {target_user_id} is not an admin.")
             await message.reply_text("هذا المستخدم ليس مشرفًا أصلاً.")
             return
    except UserNotParticipant:
         log.warning(f"Target user {target_user_id} is not in the chat {group_id}.")
         await message.reply_text("هذا المستخدم ليس عضواً في المجموعة.")
         return
    except Exception as e:
        log.error(f"Error checking target user status before demotion: {e}")
        # Continue anyway, restrict_chat_member might still work or fail gracefully

    # Demote the admin by restricting them to regular member permissions
    log.info(f"Attempting to restrict (demote) user {target_user_id} with permissions: {regular_member_permissions}")
    try:
        # Use restrict_chat_member with default permissions to effectively demote
        await client.restrict_chat_member(
            chat_id=group_id,
            user_id=target_user_id,
            permissions=regular_member_permissions # Apply regular member permissions
        )
        # Remove custom title if exists (best practice after demotion)
        try:
            await client.set_administrator_title(chat_id=group_id, user_id=target_user_id, title="")
            log.info(f"Removed custom title for {target_user_id}")
        except Exception as title_error:
            # Log warning if title removal fails, but don't stop the process
            log.warning(f"Could not remove admin title for {target_user_id} during demotion (might be okay): {title_error}")

        # Fetch target_user again if needed for mention string
        if target_user is None:
            try: target_user = await client.get_users(target_user_id)
            except Exception: target_user = None # Handle case where user cannot be fetched

        # Get mention string safely
        target_mention = f"[{target_user_id}](tg://user?id={target_user_id})"
        if target_user: target_mention = target_user.mention

        log.info(f"User {target_user_id} demoted by {user_making_request.id} in chat {group_id}")
        # Send success message
        await message.reply_text(f"✅ تم تنزيل المشرف {target_mention} بنجاح.")

    except ChatAdminRequired:
         # Handle error if bot lacks permission to restrict/demote
         log.error(f"Failed to demote {target_user_id} in chat {group_id}: Bot lacks admin privileges.")
         target_mention = f"[{target_user_id}](tg://user?id={target_user_id})"
         if target_user: target_mention = target_user.mention # Try to get mention
         await message.reply_text(f"⚠️ فشلت في تنزيل المشرف {target_mention}. البوت لا يملك صلاحيات كافية.")
    except Exception as e:
        # Handle other errors during demotion
        log.exception(f"Error demoting user {target_user_id} in chat {group_id}")
        target_mention = f"[{target_user_id}](tg://user?id={target_user_id})"
        if target_user: target_mention = target_user.mention # Try to get mention
        await message.reply_text(f"⚠️ لم أستطع تنزيل المشرف {target_mention}. الخطأ: {e}")


# --- Command to set kick threshold ---
@app.on_message(filters.command("عدد الحظر", prefixes=[""]) & filters.group )
async def set_kick_threshold_command(client: Client, message: Message):
    """Sets the kick threshold for auto-demotion in this chat."""
    chat_id = message.chat.id
    user_making_request = message.from_user
    log.info(f"'عدد الحظر' command triggered by {user_making_request.id} in chat {chat_id}")

    # Permission Check: Only owner or admins who can restrict members
    # Use the basic permission checker here
    if not await check_permission(client, chat_id, user_making_request.id, "can_restrict_members"):
         await message.reply_text(f"عذراً [{user_making_request.mention}]، يجب أن تكون مالك المجموعة أو مشرفاً لديه صلاحية تقييد الأعضاء لاستخدام هذا الأمر.")
         return

    # Parse argument for the new threshold number
    args = message.command
    if len(args) != 2:
        # Get current threshold if no argument provided
        current_threshold = DEFAULT_KICK_THRESHOLD
        try:
            with sqlite3.connect(ADMIN_DB_FILE) as conn_settings:
                cursor_settings = conn_settings.cursor()
                cursor_settings.execute("SELECT kick_threshold FROM chat_settings WHERE chat_id = ?", (chat_id,))
                result = cursor_settings.fetchone()
                if result: current_threshold = result[0]
        except Exception: pass # Ignore errors reading current value
        await message.reply_text(f"ℹ️ حد الطرد الحالي: **{current_threshold}**\n⚠️ الاستخدام: `عدد الحظر [العدد]`\nمثال: `عدد الحظر 5` (يجب أن يكون العدد 2 أو أكثر)")
        return

    try:
        new_threshold = int(args[1])
        # Ensure the threshold is reasonable (at least 2)
        if new_threshold < 2:
             await message.reply_text("⚠️ العدد يجب أن يكون 2 أو أكثر.")
             return
    except ValueError:
        await message.reply_text("⚠️ يرجى إدخال عدد صحيح صالح.")
        return

    # Save the new threshold to the database
    try:
        with sqlite3.connect(ADMIN_DB_FILE) as conn:
            # Use INSERT OR REPLACE to add or update the setting for the chat
            conn.execute(
                "INSERT OR REPLACE INTO chat_settings (chat_id, kick_threshold) VALUES (?, ?)",
                (chat_id, new_threshold)
            )
            conn.commit()
        log.info(f"Kick threshold for chat {chat_id} set to {new_threshold} by user {user_making_request.id}")
        await message.reply_text(f"✅ تم تحديث حد الطرد المسموح به للمشرفين قبل التنزيل التلقائي إلى **{new_threshold}** طردات خلال 24 ساعة.")
    except sqlite3.Error as db_err:
        log.exception(f"[DB:{ADMIN_DB_FILE}] Failed to update kick threshold for chat {chat_id}")
        await message.reply_text("⚠️ حدث خطأ أثناء حفظ الإعداد في قاعدة البيانات.")


# --- NEW: Command to unban all users ---
@app.on_message(filters.command("مسح المحظورين", prefixes=[""]) & filters.group)
async def unban_all_command(client: Client, message: Message):
    """Unbans all banned users in the chat."""
    chat_id = message.chat.id
    user_making_request = message.from_user
    log.info(f"'مسح المحظورين' command triggered by {user_making_request.id} in chat {chat_id}")

    # صلاحية: المالك أو مشرف لديه صلاحية رفع مشرفين وتغيير معلومات المجموعة
    if not await check_clear_permission(client, chat_id, user_making_request.id):
        await message.reply_text(f"عذراً [{user_making_request.mention}]، يجب أن تكون المالك أو مشرفاً لديه صلاحية رفع المشرفين وتغيير معلومات المجموعة لاستخدام هذا الأمر.")
        return

    unbanned_count = 0
    status_message = await message.reply_text("⏳ جارِ البحث عن المحظورين وإلغاء حظرهم...")

    try:
        # جلب قائمة الأعضاء المحظورين
        # ملاحظة: قد يتطلب هذا صلاحيات إدارية كاملة للبوت
        async for member in client.get_chat_members(chat_id, filter=ChatMembersFilter.BANNED):
            try:
                # محاولة إلغاء الحظر
                await client.unban_chat_member(chat_id, member.user.id)
                unbanned_count += 1
                log.info(f"Unbanned user {member.user.id} in chat {chat_id}")
                # تأخير بسيط لتجنب أخطاء FloodWait
                await asyncio.sleep(LOOP_DELAY_SECONDS)
            except FloodWait as e:
                log.warning(f"FloodWait encountered while unbanning in chat {chat_id}. Sleeping for {e.value} seconds.")
                await asyncio.sleep(e.value + 2) # انتظر للمدة المحددة + ثانيتين إضافية
                # أعد محاولة إلغاء الحظر بعد الانتظار
                try:
                     await client.unban_chat_member(chat_id, member.user.id)
                     unbanned_count += 1
                     log.info(f"Unbanned user {member.user.id} in chat {chat_id} after FloodWait.")
                except Exception as retry_err:
                     log.error(f"Failed to unban user {member.user.id} after FloodWait: {retry_err}")
            except Exception as e:
                log.error(f"Failed to unban user {member.user.id} in chat {chat_id}: {e}")

        # تحديث رسالة الحالة بالنتيجة النهائية
        await status_message.edit_text(f"✅ اكتمل مسح المحظورين.\nتم إلغاء حظر **{unbanned_count}** عضو.")
        log.info(f"Unban all completed in chat {chat_id}. Unbanned: {unbanned_count}")

    except ChatAdminRequired:
        log.error(f"Bot lacks admin rights to get banned members in chat {chat_id}")
        await status_message.edit_text("⚠️ فشل الأمر. البوت لا يملك الصلاحيات الكافية لجلب قائمة المحظورين أو إلغاء حظرهم.")
    except Exception as e:
        log.exception(f"Error during unban all process in chat {chat_id}: {e}")
        await status_message.edit_text(f"❌ حدث خطأ غير متوقع أثناء عملية مسح المحظورين: {e}")


# --- NEW: Command to clear bot-mutes ---
@app.on_message(filters.command("مسح المكتومين", prefixes=[""]) & filters.group)
async def unmute_all_bot_command(client: Client, message: Message):
    """Removes 'muted' status from the bot's database for all users in the chat."""
    chat_id = message.chat.id
    user_making_request = message.from_user
    log.info(f"'مسح المكتومين' command triggered by {user_making_request.id} in chat {chat_id}")

    # صلاحية: المالك أو مشرف لديه صلاحية رفع مشرفين وتغيير معلومات المجموعة
    if not await check_clear_permission(client, chat_id, user_making_request.id):
        await message.reply_text(f"عذراً [{user_making_request.mention}]، يجب أن تكون المالك أو مشرفاً لديه صلاحية رفع المشرفين وتغيير معلومات المجموعة لاستخدام هذا الأمر.")
        return

    removed_count = 0
    try:
        # الاتصال بقاعدة البيانات وحذف جميع سجلات الكتم لهذه الدردشة
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_chat_status WHERE chat_id = ? AND status = 'muted'", (chat_id,))
            removed_count = cursor.rowcount # الحصول على عدد السجلات التي تم حذفها
            conn.commit()
        log.info(f"Removed {removed_count} bot-mute records from DB for chat {chat_id}")
        await message.reply_text(f"✅ تم مسح سجلات الكتم الخاصة بالبوت.\nتمت إزالة حالة الكتم عن **{removed_count}** عضو في قاعدة بيانات البوت.")
        # ملاحظة: هذا لا يؤثر على قيود تيليجرام الفعلية، فقط على سجلات البوت.
    except sqlite3.Error as db_err:
        log.exception(f"[DB:{DB_FILE}] Failed to clear bot mutes for chat {chat_id}")
        await message.reply_text("⚠️ حدث خطأ أثناء مسح سجلات الكتم من قاعدة البيانات.")
    except Exception as e:
        log.exception(f"Unexpected error during clear bot mutes in chat {chat_id}: {e}")
        await message.reply_text(f"❌ حدث خطأ غير متوقع: {e}")


# --- NEW: Command to unrestrict all users ---
@app.on_message(filters.command("مسح المقيدين", prefixes=[""]) & filters.group)
async def unrestrict_all_command(client: Client, message: Message):
    """Unrestricts all restricted (but not banned) users in the chat."""
    chat_id = message.chat.id
    user_making_request = message.from_user
    log.info(f"'مسح المقيدين' command triggered by {user_making_request.id} in chat {chat_id}")

    # صلاحية: المالك أو مشرف لديه صلاحية رفع مشرفين وتغيير معلومات المجموعة
    if not await check_clear_permission(client, chat_id, user_making_request.id):
        await message.reply_text(f"عذراً [{user_making_request.mention}]، يجب أن تكون المالك أو مشرفاً لديه صلاحية رفع المشرفين وتغيير معلومات المجموعة لاستخدام هذا الأمر.")
        return

    unrestricted_count = 0
    status_message = await message.reply_text("⏳ جارِ البحث عن المقيدين وإلغاء تقييدهم...")

    try:
        # جلب قائمة الأعضاء المقيدين
        async for member in client.get_chat_members(chat_id, filter=ChatMembersFilter.RESTRICTED):
            # تجاهل البوتات والمستخدمين المحذوفين إن أمكن
            if member.user.is_bot or member.user.is_deleted: continue
            try:
                # محاولة إلغاء التقييد ومنح صلاحيات العضو العادي
                await client.restrict_chat_member(
                    chat_id,
                    member.user.id,
                    permissions=regular_member_permissions # استخدام الصلاحيات المعرفة مسبقاً
                )
                unrestricted_count += 1
                log.info(f"Unrestricted user {member.user.id} in chat {chat_id}")
                # تأخير بسيط
                await asyncio.sleep(LOOP_DELAY_SECONDS)
            except FloodWait as e:
                log.warning(f"FloodWait encountered while unrestricting in chat {chat_id}. Sleeping for {e.value} seconds.")
                await asyncio.sleep(e.value + 2)
                # إعادة المحاولة بعد الانتظار
                try:
                    await client.restrict_chat_member(chat_id, member.user.id, permissions=regular_member_permissions)
                    unrestricted_count += 1
                    log.info(f"Unrestricted user {member.user.id} in chat {chat_id} after FloodWait.")
                except Exception as retry_err:
                     log.error(f"Failed to unrestrict user {member.user.id} after FloodWait: {retry_err}")
            except Exception as e:
                log.error(f"Failed to unrestrict user {member.user.id} in chat {chat_id}: {e}")

        # تحديث رسالة الحالة بالنتيجة النهائية
        await status_message.edit_text(f"✅ اكتمل مسح المقيدين.\nتم إلغاء تقييد **{unrestricted_count}** عضو.")
        log.info(f"Unrestrict all completed in chat {chat_id}. Unrestricted: {unrestricted_count}")

    except ChatAdminRequired:
        log.error(f"Bot lacks admin rights to get/unrestrict members in chat {chat_id}")
        await status_message.edit_text("⚠️ فشل الأمر. البوت لا يملك الصلاحيات الكافية لجلب قائمة المقيدين أو إلغاء تقييدهم.")
    except Exception as e:
        log.exception(f"Error during unrestrict all process in chat {chat_id}: {e}")
        await status_message.edit_text(f"❌ حدث خطأ غير متوقع أثناء عملية مسح المقيدين: {e}")


# --- Help and Module Info (Updated with new command permissions) ---
__HELP__ = """
**أوامر كاشف المستخدمين:**

• `كشف` - **للتحقق من معلومات المستخدم ورتبته.**
  - الاستخدام: رد على رسالة المستخدم بـ `كشف`.
  - أو: `كشف <معرف المستخدم أو اسم المستخدم>`
  - أو: `كشف` (لعرض معلوماتك).
  - *ملاحظة:* يعرض الرتبة (مالك، مشرف، ادمن بوت، مميز، مكتوم، عضو) وعدد الرسائل والرتبة حسب الرسائل.

• `رتبتي` - **لعرض رتبتك الحالية في المجموعة حسب عدد الرسائل.**

• `رسائلي` - **لعرض عدد رسائلك في المجموعة.**

**أوامر إدارة المشرفين:**

• `رفع مشرف` - **لرفع عضو إلى مشرف بصلاحيات محددة.**
  - الاستخدام: رد على رسالة المستخدم بـ `رفع مشرف`.
  - أو: `رفع مشرف <معرف المستخدم أو اسم المستخدم>`
  - الصلاحيات الممنوحة: حذف الرسائل، إدارة المكالمات المرئية، تقييد الأعضاء، دعوة المستخدمين، تثبيت الرسائل.
  - *ملاحظة:* يجب أن تملك صلاحية `can_promote_members` لاستخدام هذا الأمر.

• `تنزيل مشرف` أو `تنزيل المشرف` - **لتنزيل مشرف إلى عضو عادي.**
  - الاستخدام: رد على رسالة المشرف بـ `تنزيل مشرف`.
  - أو: `تنزيل مشرف <معرف المستخدم أو اسم المستخدم>`
  - *ملاحظة:* يجب أن تملك صلاحية `can_promote_members` لاستخدام هذا الأمر. لا يمكنك تنزيل مالك المجموعة.

• `عدد الحظر [العدد]` - **لتحديد عدد مرات الطرد المسموح بها للمشرف قبل تنزيله تلقائياً.** (مثال: `عدد الحظر 5`).
  - *ملاحظة:* يجب أن تكون مشرفاً بصلاحية تقييد الأعضاء لاستخدام هذا الأمر. القيمة الافتراضية هي 3.

**أوامر المسح العام (تتطلب صلاحيات خاصة):**
*ملاحظة: هذه الأوامر يمكن استخدامها فقط بواسطة **المالك** أو **المشرف الذي يمتلك صلاحية رفع مشرفين وتغيير معلومات المجموعة معاً**.*

• `مسح المحظورين` - **لإلغاء الحظر عن جميع الأعضاء المحظورين في المجموعة.**
  - *تحذير:* سيتم إلغاء حظر الجميع، استخدم بحذر.

• `مسح المكتومين` - **لإزالة حالة الكتم المسجلة بواسطة البوت عن جميع الأعضاء في قاعدة بياناته.**
  - *ملاحظة:* هذا لا يزيل القيود المفروضة مباشرة من تيليجرام.

• `مسح المقيدين` - **لإلغاء القيود (مثل منع إرسال الوسائط) عن جميع الأعضاء المقيدين في المجموعة.**
  - *تحذير:* سيتم إلغاء تقييد الجميع، استخدم بحذر.

**ميزة تلقائية:**

• **التنزيل التلقائي للمشرفين:** إذا قام مشرف بطرد أعضاء يتجاوز العدد المحدد (`عدد الحظر`) خلال 24 ساعة، سيتم تنزيله تلقائيًا وإرسال إشعار في المجموعة (المالك فقط معفى). يتم تتبع عدد الطرد في قاعدة بيانات (`admin_actions.db`).
"""

__MODULE__ = "كاشف المستخدمين وإدارة المشرفين" # Updated module name

# Log message indicating successful loading of the plugin with its features
log.info("Whois plugin with Admin Features (DB Kick Track/Settings, Promote/Demote, Kick Threshold Cmd, Role Display, Clear Banned/Muted/Restricted with updated permissions, Removed ADMIN_IDS/mannof/muttof, Fixed UserStatus, Fixed Mentions) loaded successfully.")

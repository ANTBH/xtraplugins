# -*- coding: utf-8 -*-

print("[HADITH_DEBUG] >>> Loading Hadith Plugin...")

# ==============================================================================
#  Imports
# ==============================================================================
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ChatType, ParseMode
from pyrogram.errors import MessageNotModified, UserIsBlocked, InputUserDeactivated

try:
    # !! تأكد من أن هذا الاستيراد يعمل في بيئتك !!
    from YukkiMusic import app
    print(f"[HADITH_DEBUG] >>> Successfully imported 'app': {app}")
except ImportError as e:
    app = None
    print(f"[HADITH_DEBUG] >>> WARNING: Could not import 'app' from 'YukkiMusic'. Error: {e}")

import sqlite3, json, os, re, redis, html, logging, asyncio, uuid
from typing import List, Dict, Optional, Any, Set, Tuple
from datetime import datetime

# ==============================================================================
#  Configuration
# ==============================================================================
BOT_OWNER_ID = 6504095190 # !!! استبدل بمعرف المالك الحقيقي !!!
JSON_FILE = '1.json'
DB_NAME = 'hadith_bot.db'
MAX_MESSAGE_LENGTH = 4000
SNIPPET_CONTEXT_WORDS = 7
MAX_SNIPPETS_DISPLAY = 10
USE_REDIS = True
REDIS_HOST = 'localhost'
REDIS_PORT = 6379
REDIS_DB = 0
CACHE_EXPIRY_SECONDS = 3600 * 6

# ==============================================================================
#  Logging
# ==============================================================================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO, handlers=[logging.StreamHandler()])
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ==============================================================================
#  Redis Connection
# ==============================================================================
redis_pool = None
redis_available = False
if USE_REDIS:
    try:
        redis_pool = redis.ConnectionPool(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True, socket_connect_timeout=5)
        r_conn_test = redis.Redis(connection_pool=redis_pool)
        r_conn_test.ping(); redis_available = True; logger.info(f"Redis pool created ({REDIS_HOST}:{REDIS_PORT})")
    except Exception as e: logger.warning(f"Redis connection failed. Caching disabled. Error: {e}"); USE_REDIS = False
def get_redis_connection() -> Optional[redis.Redis]:
    if redis_available and redis_pool:
        try: return redis.Redis(connection_pool=redis_pool)
        except Exception as e: logger.error(f"Redis connection error: {e}", exc_info=True)
    return None

# ==============================================================================
#  Arabic Text Normalization (Taa Marbuta preserved AGAIN)
# ==============================================================================
alef_regex = re.compile(r'[أإآ]');
# التأكد من أن السطر التالي معطل للحفاظ على التاء المربوطة
# taa_marbuta_regex = re.compile(r'ة');
yaa_regex = re.compile(r'ى')
diacritics_punctuation_regex = re.compile(r'[\u064B-\u065F\u0670\u0640\u0610-\u061A\u06D6-\u06ED.,;:!؟\-_\'"()\[\]{}«»]')
extra_space_regex = re.compile(r'\s+')
def normalize_arabic(text: str) -> str:
    """يطبق تطبيعًا محسنًا للنص العربي مع الحفاظ على التاء المربوطة."""
    if not text or not isinstance(text, str): return ""
    try:
        text = alef_regex.sub('ا', text)
        # text = taa_marbuta_regex.sub('ه', text) # <-- التأكد من أنه معطل
        text = yaa_regex.sub('ي', text)
        text = diacritics_punctuation_regex.sub('', text)
        text = extra_space_regex.sub(' ', text).strip()
        return text
    except Exception as e: logger.error(f"Normalization error: {e}", exc_info=True); return text

# ==============================================================================
#  Database Functions
# ==============================================================================
def get_db_connection() -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(DB_NAME, timeout=10); conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;"); conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn
    except sqlite3.Error as e: logger.critical(f"DB Connect Error: {e}", exc_info=True); raise

def init_db(): # يجب تشغيله مرة واحدة عبر setup_hadith_db.py
    logger.info("Initializing database schema (if needed)...")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE VIRTUAL TABLE IF NOT EXISTS hadiths_fts USING fts5(original_id UNINDEXED, book UNINDEXED, arabic_text, grading UNINDEXED, tokenize='unicode61 remove_diacritics 2');")
            cursor.execute("CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0) WITHOUT ROWID;")
            stats_keys = ['search_count', 'hadith_added_count', 'hadith_approved_count', 'hadith_rejected_count']
            cursor.executemany("INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", [(k,) for k in stats_keys])
            cursor.execute("CREATE TABLE IF NOT EXISTS pending_hadiths (submission_id INTEGER PRIMARY KEY AUTOINCREMENT, submitter_id INTEGER NOT NULL, submitter_username TEXT, book TEXT NOT NULL, arabic_text TEXT NOT NULL, grading TEXT, submission_time DATETIME DEFAULT CURRENT_TIMESTAMP, approval_message_id INTEGER NULL);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pending_submitter ON pending_hadiths(submitter_id);")
            cursor.execute("CREATE TABLE IF NOT EXISTS user_states (user_id INTEGER PRIMARY KEY, state INTEGER NOT NULL, data TEXT) WITHOUT ROWID;")
            logger.info("Database schema initialized/verified.")
    except sqlite3.Error as e: logger.critical(f"CRITICAL: Database initialization failed: {e}", exc_info=True); raise

def populate_db_from_json(filename: str): # يجب تشغيله مرة واحدة عبر setup_hadith_db.py
    """يملأ جدول الأحاديث (FTS) من ملف JSON، ويحذف البيانات القديمة أولاً."""
    logger.info("Checking database population...")
    try:
        if not os.path.exists(filename): logger.error(f"JSON file '{filename}' not found."); return

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # !! حذف البيانات الموجودة أولاً لتطبيق التطبيع الجديد !!
            logger.warning("Dropping existing data from hadiths_fts to apply new normalization...")
            cursor.execute("DELETE FROM hadiths_fts;")
            logger.info("Existing data dropped. Populating with new normalization...")

            with open(filename, 'r', encoding='utf-8') as f: data = json.load(f)
            if not isinstance(data, list): logger.error("JSON is not a list."); return

            added = 0; skipped = 0; to_insert = []
            logger.info(f"Processing {len(data)} entries from JSON...")
            for idx, h in enumerate(data):
                if not isinstance(h, dict): skipped += 1; continue
                text = h.get('arabicText')
                if not text or not isinstance(text, str): skipped += 1; continue
                book = h.get('book') or "غير معروف"; orig_id = str(h.get('id', f'gen_{uuid.uuid4()}'))
                grading = h.get('majlisiGrading'); cleaned = re.sub(r"^\s*\d+[\s\u0640\.\-–—]*", "", text).strip()
                if not cleaned: skipped += 1; continue
                # استخدام الدالة المعدلة التي تحافظ على التاء المربوطة
                normalized = normalize_arabic(cleaned)
                if not normalized: skipped += 1; continue
                to_insert.append((orig_id, book, normalized, grading)); added += 1
                if (idx + 1) % 5000 == 0: logger.info(f"Processed {idx+1}/{len(data)}...")

            if to_insert:
                logger.info(f"Inserting {len(to_insert)} hadiths...")
                cursor.executemany("INSERT INTO hadiths_fts (original_id, book, arabic_text, grading) VALUES (?, ?, ?, ?)", to_insert)
                logger.info(f"Added {added} hadiths with new normalization. Skipped {skipped}.")
            else: logger.warning("No valid hadiths found in JSON to insert.")

    except Exception as e: logger.error(f"Population Error: {e}", exc_info=True)

def update_stats(key: str, increment: int = 1):
    try:
        with get_db_connection() as conn: conn.execute("INSERT INTO stats (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = value + excluded.value;", (key, increment))
    except Exception as e: logger.error(f"Stat Update Error for '{key}': {e}", exc_info=True)

def search_hadiths_db(query: str) -> List[int]:
    original_query_str = query.strip(); normalized_search_query = normalize_arabic(original_query_str) # سيستخدم التطبيع الجديد
    if not normalized_search_query: return []
    print(f"  [HADITH_DEBUG] --- search_hadiths_db: Normalized query: '{normalized_search_query}'")
    cache_key = f"hadith_search:{normalized_search_query}"; unique_rowids: List[int] = []; seen_original_ids: Set[str] = set()
    if USE_REDIS: # Cache Check
        redis_conn = get_redis_connection()
        if redis_conn:
            try:
                cached_data = redis_conn.get(cache_key)
                if cached_data:
                    cached_rowids = json.loads(cached_data)
                    if isinstance(cached_rowids, list): print(f"  [HADITH_DEBUG] --- search_hadiths_db: Cache HIT ({len(cached_rowids)} results)"); return cached_rowids
                    else: redis_conn.delete(cache_key)
            except Exception as e: logger.error(f"Redis GET error: {e}", exc_info=True)
    try: # DB Search
        with get_db_connection() as conn:
            cursor = conn.cursor(); prefixes = ['و', 'ف', 'ب', 'ل', 'ك']
            fts_query_parts = [f'"{normalized_search_query}"'] + [f'"{p}{normalized_search_query}"' for p in prefixes]
            fts_match_query = " OR ".join(fts_query_parts)
            print(f"  [HADITH_DEBUG] --- search_hadiths_db: Executing FTS query: {fts_match_query}")
            cursor.execute("SELECT rowid, original_id FROM hadiths_fts WHERE hadiths_fts MATCH ? ORDER BY rank DESC", (fts_match_query,))
            results = cursor.fetchall()
            print(f"  [HADITH_DEBUG] --- search_hadiths_db: FTS query found {len(results)} potential matches.")
            for row in results:
                original_id_str = str(row['original_id']) if row['original_id'] is not None else None
                if original_id_str and original_id_str not in seen_original_ids:
                    seen_original_ids.add(original_id_str); unique_rowids.append(row['rowid'])
            print(f"  [HADITH_DEBUG] --- search_hadiths_db: Deduplicated results count: {len(unique_rowids)}")
            if USE_REDIS and unique_rowids: # Cache Set
                redis_conn_set = get_redis_connection()
                if redis_conn_set:
                    try: redis_conn_set.set(cache_key, json.dumps(unique_rowids), ex=CACHE_EXPIRY_SECONDS); print("  [HADITH_DEBUG] --- search_hadiths_db: Results cached.")
                    except Exception as e: logger.error(f"Redis SET error: {e}", exc_info=True)
    except sqlite3.Error as e:
         if "no such table" in str(e).lower(): logger.error(f"DB Error: 'hadiths_fts' table missing? {e}")
         else: logger.error(f"DB search error: {e}", exc_info=True)
    except Exception as e: logger.error(f"Unexpected search error: {e}", exc_info=True)
    return unique_rowids

def get_hadith_details_by_db_id(row_id: int) -> Optional[Dict[str, Any]]:
    print(f"  [HADITH_DEBUG] --- get_hadith_details_by_db_id: Fetching details for rowid {row_id}")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor(); cursor.execute("SELECT rowid, original_id, book, arabic_text, grading FROM hadiths_fts WHERE rowid = ?", (row_id,))
            details = cursor.fetchone()
            if details: print("  [HADITH_DEBUG] --- get_hadith_details_by_db_id: Details found.")
            else: print("  [HADITH_DEBUG] --- get_hadith_details_by_db_id: Details NOT found.")
            return dict(details) if details else None
    except sqlite3.Error as e:
         if "no such table" in str(e).lower(): logger.error(f"DB Error: 'hadiths_fts' table missing? {e}")
         else: logger.error(f"DB Detail Fetch Error: {e}", exc_info=True)
    except Exception as e: logger.error(f"Unexpected Detail Fetch Error: {e}", exc_info=True)
    return None

def split_message(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> List[str]:
    parts = [];
    if not text: return []
    text = text.strip()
    while len(text) > max_length:
        split_pos = -1
        try: split_pos = text.rindex('\n', 0, max_length - 1)
        except ValueError: pass
        if split_pos < max_length // 2 :
             try: split_pos = text.rindex(' ', 0, max_length -1)
             except ValueError: pass
        if split_pos <= 0: split_pos = max_length
        parts.append(text[:split_pos].strip())
        text = text[split_pos:].strip()
    if text: parts.append(text)
    return parts

def arabic_number_to_word(n: int) -> str:
    if not isinstance(n, int) or n <= 0: return str(n)
    words = {1: "الأول", 2: "الثاني", 3: "الثالث", 4: "الرابع", 5: "الخامس", 6: "السادس", 7: "السابع", 8: "الثامن", 9: "التاسع", 10: "العاشر", 11: "الحادي عشر", 12: "الثاني عشر", 13: "الثالث عشر", 14: "الرابع عشر", 15: "الخامس عشر", 16: "السادس عشر", 17: "السابع عشر", 18: "الثامن عشر", 19: "التاسع عشر", 20: "العشرون"}
    if n > 20: return f"الـ {n}"
    return words.get(n, str(n))

# ==============================================================================
#  Conversation State Management
# ==============================================================================
STATE_IDLE = 0; STATE_ASK_BOOK = 1; STATE_ASK_TEXT = 2; STATE_ASK_GRADING = 3
def set_user_state(user_id: int, state: int, data: Optional[Dict] = None):
    print(f"  [HADITH_CONVO_DEBUG] Setting state for {user_id} to {state}")
    logger.debug(f"Setting state for user {user_id} to {state} with data: {data}")
    try:
        with get_db_connection() as conn:
            json_data = json.dumps(data, ensure_ascii=False) if data else None
            conn.execute("INSERT OR REPLACE INTO user_states (user_id, state, data) VALUES (?, ?, ?)", (user_id, state, json_data))
    except Exception as e: logger.error(f"DB Error setting state for user {user_id}: {e}", exc_info=True)

def get_user_state(user_id: int) -> Optional[Tuple[int, Optional[Dict]]]:
    print(f"  [HADITH_CONVO_DEBUG] Getting state for {user_id}")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor(); cursor.execute("SELECT state, data FROM user_states WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                state = row['state']; data = json.loads(row['data']) if row['data'] else None
                print(f"  [HADITH_CONVO_DEBUG] Found state for {user_id}: State={state}")
                logger.debug(f"Got state for user {user_id}: State={state}, Data={data}")
                return state, data
            else:
                print(f"  [HADITH_CONVO_DEBUG] No state found for {user_id}, returning IDLE.")
                return STATE_IDLE, None
    except sqlite3.Error as e:
         if "no such table" in str(e).lower(): logger.error(f"DB Error: 'user_states' table missing? Run setup script. {e}")
         else: logger.error(f"DB Error getting state for user {user_id}: {e}", exc_info=True);
         return None, None # Indicate error
    except json.JSONDecodeError as e: logger.error(f"JSON Decode Error state data user {user_id}: {e}", exc_info=True); clear_user_state(user_id); return STATE_IDLE, None
    except Exception as e: logger.error(f"Unexpected error getting state for user {user_id}: {e}", exc_info=True); return None, None

def clear_user_state(user_id: int):
    print(f"  [HADITH_CONVO_DEBUG] Clearing state for {user_id}")
    logger.debug(f"Clearing state for user {user_id}")
    try:
        with get_db_connection() as conn: conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
    except sqlite3.Error as e:
         if "no such table" in str(e).lower(): logger.error(f"DB Error: 'user_states' table missing? Run setup script. {e}")
         else: logger.error(f"DB Error clearing state for user {user_id}: {e}", exc_info=True)
    except Exception as e: logger.error(f"Unexpected error clearing state for user {user_id}: {e}", exc_info=True)

# ==============================================================================
#  Helper Function for Formatting Hadith Output
# ==============================================================================
def format_hadith_parts(details: Dict) -> Tuple[str, str, str]:
    book = html.escape(details.get('book', 'غير معروف')); text = html.escape(details.get('arabic_text', ''))
    grading = html.escape(details.get('grading', 'لم تحدد'))
    header = f"📖 <b>الكتاب:</b> {book}\n\n📜 <b>الحديث:</b>\n"; footer = f"\n\n⚖️ <b>الصحة:</b> {grading}"
    return header, text, footer

# ==============================================================================
#  Helper Function for Sending Paginated Messages
# ==============================================================================
async def send_paginated_message(client: Client, chat_id: int, header: str, text_parts: List[str], footer: str, row_id_for_callback: int, reply_to_message_id: Optional[int] = None):
    if not text_parts: logger.warning("send_paginated_message called with empty text_parts."); return
    current_part_index = 1; part_text = text_parts[current_part_index - 1]; total_parts = len(text_parts)
    part_header_text = f"📄 <b>الجزء {arabic_number_to_word(current_part_index)} من {total_parts}</b>\n\n" if total_parts > 1 else ""
    message_to_send = part_header_text + header + part_text
    if total_parts == 1: message_to_send += footer
    keyboard = None
    if total_parts > 1:
        callback_data = f"more_{row_id_for_callback}_2_{total_parts}"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("المزيد 🔽", callback_data=callback_data)]])
    try:
        await client.send_message(chat_id=chat_id, text=message_to_send, parse_mode=ParseMode.HTML, reply_markup=keyboard, reply_to_message_id=reply_to_message_id)
        logger.info(f"Sent part 1/{total_parts} for rowid {row_id_for_callback} to chat {chat_id}.")
    except Exception as e:
        logger.error(f"Error sending paginated message part 1 for rowid {row_id_for_callback}: {e}", exc_info=True)
        try: await client.send_message(chat_id, "⚠️ حدث خطأ أثناء إرسال الحديث.")
        except Exception: pass

# ==============================================================================
#  Custom Filter Definition
# ==============================================================================
async def is_private_text_not_command_via_bot(flt, client: Client, message: Message) -> bool:
    is_correct = bool(message.text and message.chat and message.chat.type == ChatType.PRIVATE and not message.via_bot and not message.text.startswith("/"))
    return is_correct
non_command_private_text_filter = filters.create(is_private_text_not_command_via_bot)
print("[HADITH_DEBUG] >>> Custom filter 'non_command_private_text_filter' created.")

# ==============================================================================
#  Pyrogram Handlers
# ==============================================================================
SEARCH_PATTERN = r"^(شيعة|شيعه)\s+(.+)"
ADD_HADITH_PATTERN = r"^(اضافة حديث|إضافة حديث)$"

if app:
    print(f"[HADITH_DEBUG] >>> 'app' object is valid ({app}). Registering Hadith handlers...")

    # --- معالج البحث ---
    @app.on_message(filters.regex(SEARCH_PATTERN) & ~filters.via_bot)
    async def handle_search_pyrogram(client: Client, message: Message):
        # ... (الكود الكامل لمعالج البحث) ...
        print(f"[HADITH_DEBUG] >>> handle_search_pyrogram triggered. User: {message.from_user.id if message.from_user else 'N/A'}.")
        if not message.text: return
        search_match = re.match(SEARCH_PATTERN, message.text.strip(), re.IGNORECASE | re.UNICODE)
        if not search_match: return
        search_query = search_match.group(2).strip()
        print(f"[HADITH_DEBUG] ---> Search Query: '{search_query}'")
        if not search_query:
            try: await message.reply_text("⚠️ نص البحث فارغ.", parse_mode=ParseMode.HTML)
            except Exception: pass; return
        user_id = message.from_user.id if message.from_user else "Unknown"; safe_search_query = html.escape(search_query)
        update_stats('search_count')
        try:
            print("[HADITH_DEBUG] ---> Calling search_hadiths_db...")
            matching_rowids = search_hadiths_db(search_query)
            num_results = len(matching_rowids)
            print(f"[HADITH_DEBUG] ---> search_hadiths_db returned {num_results} results.")
            if num_results == 0:
                print("[HADITH_DEBUG] ---> Replying with 'No results'.")
                if not os.path.exists(DB_NAME): await message.reply_text(f"⚠️ خطأ: ملف قاعدة البيانات '{DB_NAME}' غير موجود.", parse_mode=ParseMode.HTML)
                else: await message.reply_text(f"❌ لا توجد نتائج تطابق: '<b>{safe_search_query}</b>'.", parse_mode=ParseMode.HTML)
            elif num_results == 1:
                print("[HADITH_DEBUG] ---> Handling single result (paginated)...")
                row_id = matching_rowids[0]; details = get_hadith_details_by_db_id(row_id)
                if details:
                    header, text, footer = format_hadith_parts(details)
                    text_parts = split_message(text)
                    await send_paginated_message(client, message.chat.id, header, text_parts, footer, row_id, reply_to_message_id=message.id)
                else: print("[HADITH_DEBUG] ---> ERROR: Failed to get details for single result."); await message.reply_text("⚠️ خطأ في جلب تفاصيل النتيجة.")
            elif num_results == 2:
                print(f"[HADITH_DEBUG] ---> Handling {num_results} results directly (paginated)...")
                await message.reply_text(f"✅ تم العثور على نتيجتين. جاري إرسالهما:", quote=True); await asyncio.sleep(0.5)
                for i, row_id in enumerate(matching_rowids):
                    details = get_hadith_details_by_db_id(row_id)
                    if details:
                        header, text, footer = format_hadith_parts(details)
                        result_header = f"--- [ النتيجة {i+1} / {num_results} ] ---\n" + header
                        text_parts = split_message(text)
                        await send_paginated_message(client, message.chat.id, result_header, text_parts, footer, row_id); await asyncio.sleep(1.0)
                    else:
                        logger.warning(f"Could not get details for rowid {row_id} in 2-result send.")
                        try: await client.send_message(message.chat.id, f"⚠️ خطأ في جلب تفاصيل النتيجة رقم {i+1}.")
                        except Exception: pass
            elif 2 < num_results <= MAX_SNIPPETS_DISPLAY: # عرض 3 إلى 10 كمقتطفات وأزرار
                print(f"[HADITH_DEBUG] ---> Handling {num_results} results with snippets/buttons...")
                response_header = f"💡 تم العثور على <b>{num_results}</b> نتائج تطابق '<b>{safe_search_query}</b>'.\n\n"
                response_snippets = ""; buttons_list = []
                print(f"[HADITH_DEBUG] ---> Generating {num_results} snippets/buttons...")
                for i, row_id in enumerate(matching_rowids):
                    details = get_hadith_details_by_db_id(row_id)
                    if details:
                        book = html.escape(details.get('book', 'غير معروف')); text_norm = details.get('arabic_text', '')
                        snippet = "..."; norm_query = normalize_arabic(search_query)
                        try:
                            idx = text_norm.find(norm_query)
                            if idx != -1:
                                start = max(0, idx - (SNIPPET_CONTEXT_WORDS * 7)); end = min(len(text_norm), idx + len(norm_query) + (SNIPPET_CONTEXT_WORDS * 7))
                                ctx = text_norm[start:end]; esc_ctx = html.escape(ctx); esc_kw = html.escape(text_norm[idx : idx + len(norm_query)])
                                snippet = esc_ctx.replace(esc_kw, f"<b>{esc_kw}</b>", 1)
                                if start > 0: snippet = "... " + snippet
                                if end < len(text_norm): snippet = snippet + " ..."
                            else: snippet = html.escape(text_norm[:SNIPPET_CONTEXT_WORDS * 14]) + "..."
                        except Exception as e: snippet = html.escape(text_norm[:50]) + "..." ; logger.error(f"Snippet error: {e}")
                        response_snippets += f"{i + 1}. 📖 <b>{book}</b>\n   📝 <i>{snippet}</i>\n\n"
                        trunc_book = book[:20] + ('...' if len(book) > 20 else '')
                        buttons_list.append(InlineKeyboardButton(f"{i + 1}. {trunc_book}", callback_data=f"view_{row_id}"))
                    else: logger.warning(f"Could not get details for rowid {row_id} in multi-result snippet gen.")
                if buttons_list:
                    print("[HADITH_DEBUG] ---> Sending snippet list and buttons...")
                    keyboard = InlineKeyboardMarkup([[btn] for btn in buttons_list])
                    full_response_text = response_header + response_snippets.strip()
                    # إرسال المقتطفات أولاً
                    await message.reply_text(full_response_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                    # إرسال رسالة الأزرار منفصلة
                    await message.reply_text("اضغط على رقم الحديث لعرضه كاملاً:", reply_markup=keyboard)
                    print("[HADITH_DEBUG] ---> Finished sending snippets/buttons.")
                else: print("[HADITH_DEBUG] ---> ERROR: Failed to generate buttons."); await message.reply_text("⚠️ خطأ في تجهيز النتائج للعرض.")
            elif num_results > MAX_SNIPPETS_DISPLAY: # أكثر من 10 نتائج
                print(f"[HADITH_DEBUG] ---> Handling too many results (>{MAX_SNIPPETS_DISPLAY})...")
                await message.reply_text(f"⚠️ تم العثور على <b>{num_results}</b> نتيجة.\nالنتائج كثيرة جدًا لعرضها.\n<b>يرجى تحديد بحثك بإضافة المزيد من الكلمات.</b>", parse_mode=ParseMode.HTML)
        except Exception as e:
            print(f"[HADITH_DEBUG] ---> EXCEPTION in handle_search_pyrogram: {e}")
            logger.error(f"Error handling search query '{search_query}': {e}", exc_info=True)
            try: await message.reply_text("⚠️ حدث خطأ غير متوقع أثناء البحث.")
            except Exception: pass

    # --- معالج زر عرض التفاصيل ---
    @app.on_callback_query(filters.regex(r"^view_(\d+)"))
    async def handle_view_callback_pyrogram(client: Client, callback_query: CallbackQuery):
        # ... (الكود الكامل لمعالج الزر) ...
        print(f"[HADITH_DEBUG] >>> handle_view_callback_pyrogram triggered. User: {callback_query.from_user.id if callback_query.from_user else 'N/A'}. Data: {callback_query.data}")
        row_id_str = callback_query.data.split("_", 1)[1]
        try: row_id = int(row_id_str)
        except ValueError: logger.error(f"Invalid row_id: {callback_query.data}"); await callback_query.answer("خطأ!", show_alert=True); return
        print(f"[HADITH_DEBUG] ---> Processing view callback for rowid: {row_id}")
        try:
            details = get_hadith_details_by_db_id(row_id)
            if details:
                print("[HADITH_DEBUG] ---> Formatting view result...")
                try: await callback_query.message.delete()
                except Exception as e_del: logger.warning(f"Could not delete button message {callback_query.message.id}: {e_del}")
                header, text, footer = format_hadith_parts(details)
                text_parts = split_message(text)
                print(f"[HADITH_DEBUG] ---> Sending view result (paginated) in {len(text_parts)} parts...")
                await send_paginated_message(client, callback_query.message.chat.id, header, text_parts, footer, row_id)
                await callback_query.answer()
            else: print("[HADITH_DEBUG] ---> ERROR: Details not found for view callback."); await callback_query.answer("خطأ: لم يتم العثور على الحديث!", show_alert=True)
        except Exception as e:
            print(f"[HADITH_DEBUG] ---> EXCEPTION in handle_view_callback_pyrogram: {e}")
            logger.error(f"Error handling view callback for rowid {row_id}: {e}", exc_info=True)
            try: await callback_query.answer("حدث خطأ غير متوقع!", show_alert=True)
            except Exception: pass

    # --- معالج زر "المزيد" ---
    @app.on_callback_query(filters.regex(r"^more_(\d+)_(\d+)_(\d+)")) # more_{row_id}_{next_part_index}_{total_parts}
    async def handle_more_callback_pyrogram(client: Client, callback_query: CallbackQuery):
        # ... (الكود الكامل لمعالج المزيد) ...
        user_id = callback_query.from_user.id
        print(f"[HADITH_DEBUG] >>> handle_more_callback_pyrogram triggered. User: {user_id}. Data: {callback_query.data}")
        try:
            _, row_id_str, next_part_index_str, total_parts_str = callback_query.data.split("_")
            row_id = int(row_id_str); next_part_index = int(next_part_index_str); total_parts = int(total_parts_str)
            current_part_index_in_list = next_part_index - 1
            print(f"[HADITH_DEBUG] ---> Requesting part {next_part_index}/{total_parts} for rowid {row_id}")
            details = get_hadith_details_by_db_id(row_id)
            if not details:
                await callback_query.answer("خطأ: لم يتم العثور على بيانات الحديث!", show_alert=True)
                try: await callback_query.edit_message_reply_markup(reply_markup=None)
                except Exception: pass; return
            header, text, footer = format_hadith_parts(details)
            text_parts = split_message(text)
            if not (0 <= current_part_index_in_list < len(text_parts) and len(text_parts) == total_parts):
                 logger.warning(f"Invalid part index/total parts mismatch. Data: {callback_query.data}, Parts: {len(text_parts)}")
                 await callback_query.answer("خطأ في بيانات التقسيم!", show_alert=True)
                 try: await callback_query.edit_message_reply_markup(reply_markup=None)
                 except Exception: pass; return
            part_to_send = text_parts[current_part_index_in_list]
            part_header_text = f"📄 <b>الجزء {arabic_number_to_word(next_part_index)} من {total_parts}</b>\n\n"
            message_to_send = part_header_text + part_to_send; keyboard = None
            is_last_part = (next_part_index == total_parts)
            if is_last_part: message_to_send += footer
            else:
                next_next_part_index = next_part_index + 1
                callback_data = f"more_{row_id}_{next_next_part_index}_{total_parts}"
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("المزيد 🔽", callback_data=callback_data)]])
            new_msg = await client.send_message(chat_id=callback_query.message.chat.id, text=message_to_send, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            print(f"[HADITH_DEBUG] ---> Sent part {next_part_index}/{total_parts} for rowid {row_id} (New msg: {new_msg.id})")
            try:
                await callback_query.edit_message_reply_markup(reply_markup=None)
                print(f"[HADITH_DEBUG] ---> Edited previous message {callback_query.message.id} to remove button.")
            except MessageNotModified: pass
            except Exception as e_edit: logger.warning(f"Could not edit previous message {callback_query.message.id}: {e_edit}")
            await callback_query.answer()
        except ValueError: logger.error(f"ValueError parsing more callback: {callback_query.data}"); await callback_query.answer("خطأ!", show_alert=True)
        except Exception as e:
            print(f"[HADITH_DEBUG] ---> EXCEPTION in handle_more_callback_pyrogram: {e}")
            logger.error(f"Error handling more callback: {e}", exc_info=True)
            try: await callback_query.answer("حدث خطأ غير متوقع!", show_alert=True)
            except Exception: pass

    # --- معالجات إضافة حديث (معدلة) ---
    @app.on_message(
        (filters.command("addhadith") | filters.regex(ADD_HADITH_PATTERN)) &
        filters.private & ~filters.via_bot
    )
    async def add_hadith_start_pyrogram(client: Client, message: Message):
        user_id = message.from_user.id; logger.info(f"User {user_id} initiated add hadith.")
        clear_user_state(user_id); set_user_state(user_id, STATE_ASK_BOOK, data={})
        await message.reply_text(
            "🔹 <b>بدء عملية إضافة حديث جديد</b> 🔹\n\n"
            "📖 <b>الخطوة 1 من 3:</b>\n"
            "يرجى إرسال <b>اسم الكتاب</b> المصدر.\n\n"
            "<i>مثال: الكافي - ج 1</i>\n\n"
            " لإلغاء العملية أرسل /cancel.",
            parse_mode=ParseMode.HTML
        )

    @app.on_message(filters.command("cancel") & filters.private & ~filters.via_bot)
    async def cancel_hadith_pyrogram(client: Client, message: Message):
        user_id = message.from_user.id; state_info = get_user_state(user_id)
        if state_info and state_info[0] != STATE_IDLE:
            clear_user_state(user_id); logger.info(f"User {user_id} cancelled add hadith.")
            await message.reply_text("❌ تم إلغاء عملية الإضافة.")
        else:
            logger.debug(f"User {user_id} used /cancel with no active conversation.")
            await message.reply_text("⚠️ لا توجد عملية إضافة نشطة لإلغائها.")

    @app.on_message(non_command_private_text_filter) # استخدام الفلتر المخصص
    async def handle_conversation_message_pyrogram(client: Client, message: Message):
        user_id = message.from_user.id;
        current_state_info = get_user_state(user_id)
        print(f"  [HADITH_CONVO_DEBUG] Handler triggered for user {user_id}. Read state: {current_state_info[0] if current_state_info else 'None'}")
        if current_state_info is None or current_state_info[0] == STATE_IDLE:
            print(f"  [HADITH_CONVO_DEBUG] User {user_id} is in IDLE state or state is None. Ignoring message.")
            return
        current_state, current_data = current_state_info; current_data = current_data or {}
        print(f"  [HADITH_CONVO_DEBUG] Processing state {current_state} for user {user_id}")

        if current_state == STATE_ASK_BOOK:
            print(f"  [HADITH_CONVO_DEBUG] In STATE_ASK_BOOK for user {user_id}")
            book_name = message.text.strip();
            if not book_name: await message.reply_text("⚠️ اسم الكتاب لا يمكن أن يكون فارغًا."); return
            logger.info(f"User {user_id} provided book: {book_name}"); current_data['book'] = book_name
            set_user_state(user_id, STATE_ASK_TEXT, data=current_data)
            await message.reply_text(
                f"📖 الكتاب: <b>{html.escape(book_name)}</b>\n\n"
                "📝 <b>الخطوة 2 من 3:</b>\n"
                "الآن يرجى إرسال <b>نص الحديث</b> كاملاً.",
                parse_mode=ParseMode.HTML
            );
            print(f"  [HADITH_CONVO_DEBUG] User {user_id} state changed to ASK_TEXT")
            return

        elif current_state == STATE_ASK_TEXT:
            print(f"  [HADITH_CONVO_DEBUG] In STATE_ASK_TEXT for user {user_id}")
            hadith_text = message.text.strip();
            if not hadith_text: await message.reply_text("⚠️ نص الحديث لا يمكن أن يكون فارغًا."); return
            logger.info(f"User {user_id} provided text (len {len(hadith_text)})."); current_data['text'] = hadith_text
            set_user_state(user_id, STATE_ASK_GRADING, data=current_data)
            await message.reply_text(
                "📝 تم استلام نص الحديث.\n\n"
                "⚖️ <b>الخطوة 3 من 3 (اختياري):</b>\n"
                "الرجاء إرسال <b>درجة صحة الحديث</b> (إن وجدت).\n\n"
                "💡 أرسل /skip لتخطي هذه الخطوة.",
                parse_mode=ParseMode.HTML
            );
            print(f"  [HADITH_CONVO_DEBUG] User {user_id} state changed to ASK_GRADING")
            return

        elif current_state == STATE_ASK_GRADING:
            print(f"  [HADITH_CONVO_DEBUG] In STATE_ASK_GRADING for user {user_id}")
            if message.text.strip().lower() == '/skip':
                 logger.info(f"User {user_id} skipped grading."); current_data['grading'] = None
                 await message.reply_text("☑️ تم تخطي درجة الصحة.")
                 await save_pending_hadith_pyrogram(client, message, current_data); clear_user_state(user_id); return
            grading = message.text.strip();
            if not grading: await message.reply_text("⚠️ الرجاء إرسال درجة الصحة أو استخدم /skip."); return
            logger.info(f"User {user_id} provided grading: {grading}"); current_data['grading'] = grading
            await save_pending_hadith_pyrogram(client, message, current_data); clear_user_state(user_id);
            return
        else:
             print(f"  [HADITH_CONVO_DEBUG] User {user_id} in unhandled state {current_state}. Clearing state.")
             logger.warning(f"User {user_id} was in an unhandled state: {current_state}. Clearing state.")
             clear_user_state(user_id)

    # --- دالة حفظ الطلب المعلق وإبلاغ المالك ---
    async def save_pending_hadith_pyrogram(client: Client, message: Message, data: Dict):
        user_id = message.from_user.id; username = message.from_user.username
        book = data.get('book'); text = data.get('text'); grading = data.get('grading')
        if not book or not text: logger.error(f"Missing data in save_pending: {data}"); await message.reply_text("⚠️ خطأ داخلي."); return
        submission_id = None; owner_message_id = None
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT INTO pending_hadiths (submitter_id, submitter_username, book, arabic_text, grading) VALUES (?, ?, ?, ?, ?)", (user_id, username or f'id_{user_id}', book, text, grading))
                submission_id = cursor.lastrowid; update_stats('hadith_added_count')
                logger.info(f"Saved pending hadith {submission_id} from user {user_id}.")
                await message.reply_text("✅ تم استلام الحديث بنجاح.\nسيتم مراجعته من قبل المشرف، شكرًا لمساهمتك!")
        except Exception as e: logger.error(f"DB Error saving pending: {e}", exc_info=True); await message.reply_text("⚠️ حدث خطأ أثناء حفظ الطلب."); return
        if submission_id and BOT_OWNER_ID:
            try:
                owner_msg = f"""<b>طلب مراجعة حديث جديد</b> ⏳ (#{submission_id})
<b>المرسل:</b> {message.from_user.mention} (<code>{user_id}</code>)
<b>الكتاب:</b> {html.escape(book)}
<b>درجة الصحة المقترحة:</b> {html.escape(grading if grading else 'لم تحدد')}
---
<b>النص الكامل:</b>
<pre>{html.escape(text[:3500])}{'...' if len(text) > 3500 else ''}</pre>"""
                keyboard = InlineKeyboardMarkup([[ InlineKeyboardButton("👍 موافقة", callback_data=f"happrove_{submission_id}"), InlineKeyboardButton("👎 رفض", callback_data=f"hreject_{submission_id}")]])
                sent_owner_msg = await client.send_message(BOT_OWNER_ID, owner_msg, parse_mode=ParseMode.HTML, reply_markup=keyboard)
                owner_message_id = sent_owner_msg.id
                logger.info(f"Sent notification for {submission_id} to owner {BOT_OWNER_ID} (Msg ID: {owner_message_id}).")
                if owner_message_id:
                    with get_db_connection() as conn_upd:
                        conn_upd.execute("UPDATE pending_hadiths SET approval_message_id = ? WHERE submission_id = ?", (owner_message_id, submission_id))
                        logger.debug(f"Updated approval_message_id for submission {submission_id}.")
            except Exception as e: logger.error(f"Failed to notify owner or update msg_id for {submission_id}: {e}", exc_info=True)

    # --- معالجات ردود المالك ---
    @app.on_callback_query(filters.regex(r"^happrove_(\d+)"))
    async def handle_approve_callback(client: Client, callback_query: CallbackQuery):
        owner_id = callback_query.from_user.id
        if owner_id != BOT_OWNER_ID: await callback_query.answer("غير مصرح لك.", show_alert=True); return
        try:
            submission_id = int(callback_query.data.split("_")[1])
            logger.info(f"Owner ({owner_id}) approving submission {submission_id}")
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT submitter_id, book, arabic_text, grading, approval_message_id FROM pending_hadiths WHERE submission_id = ?", (submission_id,))
                pending = cursor.fetchone()
                if not pending: await callback_query.answer("الطلب غير موجود أو تمت معالجته.", show_alert=True); return
                normalized_text = normalize_arabic(pending['arabic_text']) # استخدام التطبيع المحدث
                if not normalized_text: await callback_query.answer("خطأ: النص فارغ بعد التطبيع!", show_alert=True); return
                new_hadith_id = str(uuid.uuid4())
                cursor.execute("INSERT INTO hadiths_fts (original_id, book, arabic_text, grading) VALUES (?, ?, ?, ?)", (new_hadith_id, pending['book'], normalized_text, pending['grading']))
                cursor.execute("DELETE FROM pending_hadiths WHERE submission_id = ?", (submission_id,))
                update_stats('hadith_approved_count')
                logger.info(f"Approved {submission_id}, added as {new_hadith_id}, deleted from pending.")
            try: await callback_query.edit_message_text(f"{callback_query.message.text.html}\n\n--- ✅ تمت الموافقة ---", reply_markup=None)
            except MessageNotModified: pass
            except Exception as e: logger.warning(f"Could not edit owner msg {pending['approval_message_id']} on approve: {e}")
            try: await client.send_message(pending['submitter_id'], f"👍 تم قبول الحديث الذي أضفته في كتاب '{html.escape(pending['book'])}' ونشره.")
            except (UserIsBlocked, InputUserDeactivated): logger.warning(f"Could not notify submitter {pending['submitter_id']} (blocked/deactivated).")
            except Exception as e: logger.error(f"Failed to notify submitter {pending['submitter_id']} of approval: {e}")
            await callback_query.answer("تمت الموافقة بنجاح!")
        except Exception as e:
            logger.error(f"Error handling approve callback for {callback_query.data}: {e}", exc_info=True)
            await callback_query.answer("حدث خطأ أثناء الموافقة!", show_alert=True)

    @app.on_callback_query(filters.regex(r"^hreject_(\d+)"))
    async def handle_reject_callback(client: Client, callback_query: CallbackQuery):
        owner_id = callback_query.from_user.id
        if owner_id != BOT_OWNER_ID: await callback_query.answer("غير مصرح لك.", show_alert=True); return
        try:
            submission_id = int(callback_query.data.split("_")[1])
            logger.info(f"Owner ({owner_id}) rejecting submission {submission_id}")
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT submitter_id, book, approval_message_id FROM pending_hadiths WHERE submission_id = ?", (submission_id,))
                pending = cursor.fetchone()
                if not pending: await callback_query.answer("الطلب غير موجود أو تمت معالجته.", show_alert=True); return
                cursor.execute("DELETE FROM pending_hadiths WHERE submission_id = ?", (submission_id,))
                update_stats('hadith_rejected_count')
                logger.info(f"Rejected and deleted submission {submission_id}.")
            try: await callback_query.edit_message_text(f"{callback_query.message.text.html}\n\n--- ❌ تم الرفض ---", reply_markup=None)
            except MessageNotModified: pass
            except Exception as e: logger.warning(f"Could not edit owner msg {pending['approval_message_id']} on reject: {e}")
            try: await client.send_message(pending['submitter_id'], f"ℹ️ نعتذر، لم تتم الموافقة على الحديث الذي أضفته في كتاب '{html.escape(pending['book'])}'.")
            except (UserIsBlocked, InputUserDeactivated): logger.warning(f"Could not notify submitter {pending['submitter_id']} (blocked/deactivated).")
            except Exception as e: logger.error(f"Failed to notify submitter {pending['submitter_id']} of rejection: {e}")
            await callback_query.answer("تم الرفض بنجاح.")
        except Exception as e:
            logger.error(f"Error handling reject callback for {callback_query.data}: {e}", exc_info=True)
            await callback_query.answer("حدث خطأ أثناء الرفض!", show_alert=True)

    # --- معالج أوامر المساعدة ---
    # تم إزالة معالج /help بناءً على طلب سابق
    # يمكنك إعادة إضافته إذا أردت

else:
    print("[HADITH_DEBUG] >>> ERROR: 'app' object is None or import failed. Cannot register Hadith handlers.")

# ==============================================================================
#  التهيئة والتشغيل (إذا كان ملفًا رئيسيًا)
# ==============================================================================
# (لا تغيير هنا، يبقى معلقًا)
# if __name__ == "__main__":
#    ...

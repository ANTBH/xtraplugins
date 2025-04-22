import httpx  # For making asynchronous HTTP requests
import logging # Import the logging library
import re # Import regex library for text matching
import asyncio # Import asyncio for potential delays
import json # To load the Quran data file
import os # To check if file exists
from urllib.parse import quote # Import the correct function for URL encoding
from pyrogram import Client, filters
# Import necessary types for buttons and callbacks
# استيراد الأنواع اللازمة للأزرار والاستجابات
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

# --- Import thefuzz library ---
# --- استيراد مكتبة thefuzz ---
try:
    from thefuzz import fuzz
    THEFUZZ_AVAILABLE = True
except ImportError:
    logging.error("Library 'thefuzz' not found. Fuzzy search disabled. Install with: pip install thefuzz[speedup]")
    THEFUZZ_AVAILABLE = False

# --- جلب متغير العميل (app) ---
# !! هام: تأكد من أن هذا السطر صحيح بالنسبة لهيكل البوت لديك
try:
    from YukkiMusic import app
except ImportError:
    # Attempt to import from YukkiMusic as per user's provided code
    try:
        from YukkiMusic import app
        log.info("Imported 'app' from 'YukkiMusic'")
    except ImportError:
         logging.critical("لم يتم العثور على متغير العميل 'app' في YukkiMusic أو YukkiMusic. الـ decorator لن يعمل!")
         app = None


# --- إعدادات Logging ---
# Back to INFO level
# العودة إلى مستوى INFO
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    # filename='bot.log',
    # filemode='a'
)
log = logging.getLogger(__name__)

# --- تحميل بيانات القرآن من ملف JSON ---
# --- Load Quran data from JSON file ---
QURAN_JSON_PATH = "Quran.json" # Assume file is in the same directory
quran_data = []
try:
    if os.path.exists(QURAN_JSON_PATH):
        with open(QURAN_JSON_PATH, 'r', encoding='utf-8') as f:
            quran_data = json.load(f)
        log.info(f"Successfully loaded {len(quran_data)} verses from {QURAN_JSON_PATH}")
    else:
        log.error(f"Quran data file not found at: {QURAN_JSON_PATH}. Local search will be disabled.")
except Exception as e:
    log.exception(f"Error loading Quran data from {QURAN_JSON_PATH}. Local search disabled.")
    quran_data = [] # Ensure it's an empty list on error

# --- دالة تطبيع النص العربي (شاملة جداً - تستخدم للبحث الداخلي) ---
# --- Comprehensive Arabic Text Normalization Function (Used for internal search comparison) ---
def normalize_arabic(text: str) -> str:
    """Removes Arabic diacritics, Tatweel, common Quranic symbols, normalizes characters and spacing."""
    if not text:
        return ""
    # Remove common diacritics/Tashkeel and Quranic marks/symbols
    text = re.sub(r"[ًٌٍَُِّْ~۞ٰۚۖۗۦٓۡ۩ۘۥۧ]", "", text)
    # Remove Tatweel/Kashida
    text = re.sub(r"ـ", "", text)
    # Normalize Alef forms to bare Alef
    text = re.sub(r"[إأآٱ]", "ا", text)
    # Normalize Alef Maqsura to Yeh
    text = re.sub(r"ى", "ي", text)
    # Normalize Waw with Hamza above to Waw
    text = re.sub(r"ؤ", "و", text)
    # Normalize Yeh with Hamza above to Yeh
    text = re.sub(r"ئ", "ي", text)
    # Normalize Teh Marbuta to Heh
    text = re.sub(r"ة", "ه", text)
    # Normalize spacing (replace multiple spaces with one and strip)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# --- دالة إنشاء المقتطف (تعرض النص الأصلي) ---
# --- Snippet Creation Function (Displays original text) ---
def create_snippet(verse_text: str, keyword: str, context_chars: int = 30) -> str:
    """Creates a snippet of the original verse centered around the keyword."""
    if not verse_text or not keyword:
        return verse_text[:context_chars*2 + len(keyword)] # Return beginning of original text

    # Use full normalization to find keyword position accurately
    normalized_verse = normalize_arabic(verse_text)
    normalized_keyword = normalize_arabic(keyword)

    try:
        start_index = normalized_verse.find(normalized_keyword)
        if start_index == -1:
            # Attempt fuzzy find just for context positioning if exact not found
            # (More complex: requires fuzzy library here too)
            # Fallback: return beginning of original text
            log.warning(f"Could not find normalized keyword '{normalized_keyword}' in normalized verse for snippet context.")
            return verse_text[:context_chars*2 + len(keyword)] + "..." if len(verse_text) > context_chars*2 + len(keyword) else verse_text

        # Estimate the corresponding index in the original text (approximate)
        original_start_index = start_index
        start = max(0, original_start_index - context_chars)
        end = min(len(verse_text), original_start_index + len(normalized_keyword) + context_chars)

        # Extract snippet from ORIGINAL text
        snippet_orig = verse_text[start:end]

        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(verse_text) else ""

        return f"{prefix}{snippet_orig}{suffix}" # Return original snippet

    except Exception as e:
        log.error(f"Error creating snippet: {e}")
        # Fallback to beginning of original text
        return verse_text[:context_chars*2 + len(keyword)] + "..." if len(verse_text) > context_chars*2 + len(keyword) else verse_text


# --- دالة مساعدة للبحث عن الآية محلياً في JSON (باستخدام Fuzzy Search) ---
# --- Helper function to search Ayah locally in JSON (using Fuzzy Search) ---
async def search_ayah_local_json_fuzzy(keyword: str, data: list) -> list[dict] | None:
    """
    تبحث عن آية تحتوي على الكلمة المفتاحية داخل بيانات JSON المحملة.
    تستخدم البحث التقريبي (fuzzy - token_set_ratio) مع تطبيع شامل.
    - تعيد قائمة تحتوي على أفضل 1-5 نتائج [{verseKey, original_text, score}, ...] إذا تجاوزت درجة التشابه الحد الأدنى.
    - تعيد None إذا لم يتم العثور على نتائج أو حدث خطأ أو المكتبة غير متاحة.

    Searches for an Ayah containing the keyword within the loaded JSON data.
    Uses fuzzy search (token_set_ratio) with comprehensive normalization.
    - Returns a list containing the best 1-5 results [{verseKey, original_text, score}, ...] if score threshold is met.
    - Returns None if no results found, an error occurs, or the library is unavailable.
    """
    if not THEFUZZ_AVAILABLE:
         log.error("Fuzzy search unavailable because 'thefuzz' library is not installed.")
         return None
    if not data:
         log.error("Cannot search locally, Quran JSON data is not loaded.")
         return None

    # Use COMPREHENSIVE normalization for keyword
    normalized_keyword = normalize_arabic(keyword)
    log.info(f"Starting local fuzzy search for normalized keyword: '{normalized_keyword}'")

    if not normalized_keyword:
         log.warning("Keyword became empty after comprehensive normalization.")
         return None

    potential_matches = []
    MIN_SCORE_THRESHOLD = 85 # User requested threshold

    for ayah_obj in data:
         # Use the pre-simplified emlaey text for matching
         # استخدام النص الإملائي المبسط مسبقًا للمطابقة
         text_to_match = ayah_obj.get("aya_text_emlaey")
         if not text_to_match:
             continue # Skip if no emlaey text

         # Calculate similarity score using token_set_ratio
         # No need to normalize text_to_match if it's already simple imlaei
         # لا حاجة لتطبيع text_to_match إذا كان بالفعل إملائي بسيط
         # However, applying normalize_arabic ensures consistency if emlaey isn't perfect
         # ومع ذلك، فإن تطبيق normalize_arabic يضمن الاتساق إذا لم يكن emlaey مثاليًا
         normalized_verse_emlaey = normalize_arabic(text_to_match)
         score = fuzz.token_set_ratio(normalized_keyword, normalized_verse_emlaey)

         verse_key = f"{ayah_obj.get('sura_no')}:{ayah_obj.get('aya_no')}"
         log.debug(f"Fuzzy score (token_set_ratio) for {verse_key}: {score} (Threshold: {MIN_SCORE_THRESHOLD})")

         if score >= MIN_SCORE_THRESHOLD:
             log.info(f"Found potential fuzzy match in {verse_key} with score {score}")
             potential_matches.append({
                 "verseKey": verse_key,
                 "original_text": ayah_obj.get("aya_text"), # Store original Uthmani text
                 "score": score
             })

    if not potential_matches:
         log.info(f"No matches found above fuzzy threshold {MIN_SCORE_THRESHOLD} for keyword: '{keyword}'")
         return None

    # Sort potential matches by score (highest first)
    potential_matches.sort(key=lambda x: x['score'], reverse=True)

    # Return the top 1 to 5 matches
    # إعادة أفضل 1 إلى 5 تطابقات
    MAX_RESULTS_TO_PROCESS = 5
    final_results = potential_matches[:MAX_RESULTS_TO_PROCESS]
    log.info(f"Returning top {len(final_results)} fuzzy match(es).")
    return final_results


# --- دالة مساعدة لجلب تفاصيل الآية والصوت (لا تزال تستخدم alquran.cloud) ---
async def get_ayah_details(surah_number: int, ayah_number: int) -> dict | None:
    """
    تجلب التفاصيل (بما في ذلك الصوت واسم السورة) لآية محددة باستخدام api.alquran.cloud.
    Fetches details (including audio and Surah name) for a specific Ayah using api.alquran.cloud.
    Note: We get the primary text from the local DB now.
    ملاحظة: نحصل على النص الأساسي من قاعدة البيانات المحلية الآن.
    """
    reciter = "ar.saoodshuraym"
    # Fetch only audio and surah name if possible, or fetch reciter edition and extract
    # جلب الصوت واسم السورة فقط إذا أمكن، أو جلب نسخة القارئ واستخلاصها
    details_url = f"https://api.alquran.cloud/v1/surah/{surah_number}/editions/{reciter}"
    log.info(f"Fetching details (audio/surah name) from alquran.cloud: {details_url}")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(details_url)
            response.raise_for_status()
            data = response.json()

        if data.get('code') == 200 and data.get('data'):
            edition_data = data['data'][0] # Assuming reciter is the first element
            surah_name = edition_data.get('name', f'سورة {surah_number}')
            ayah_data = next((ayah for ayah in edition_data.get('ayahs', []) if ayah.get('numberInSurah') == ayah_number), None)

            if ayah_data:
                log.info(f"Audio/Surah Name Details found for {surah_number}:{ayah_number}")
                return {
                    "surahName": surah_name,
                    "audioUrl": ayah_data.get('audio'),
                    # "reciterVerseText": ayah_data.get('text') # We use local DB text now
                }
            else:
                log.warning(f"Ayah {ayah_number} not found in reciter edition (alquran.cloud) for Surah {surah_number}")
                # Still return Surah name if available
                return {"surahName": surah_name, "audioUrl": None}
        else:
            log.warning(f"API Error when fetching details (alquran.cloud): {data.get('status')}")
            return None
    except httpx.HTTPStatusError as e:
        log.exception(f"HTTP Status Error fetching details (alquran.cloud): {e.response.status_code} - {e.response.text}")
        return None
    except httpx.RequestError as e:
        log.exception(f"HTTP Request Error fetching details (alquran.cloud)")
        return None
    except Exception as e:
        log.exception(f"Unexpected error fetching details (alquran.cloud)")
        return None

# --- دالة مساعدة جديدة لجلب التفسير الميسر من alquran.cloud ---
async def get_tafseer_from_api(surah_number: int, ayah_number: int) -> str | None:
    """
    يجلب التفسير الميسر لآية محددة باستخدام api.alquran.cloud.
    Fetches Tafsir Al-Muyassar for a specific Ayah using api.alquran.cloud.
    """
    tafseer_url = f"https://api.alquran.cloud/v1/ayah/{surah_number}:{ayah_number}/ar.muyassar"
    log.info(f"Fetching Tafsir from alquran.cloud: {tafseer_url}")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(tafseer_url)
            response.raise_for_status() # Raise exception for bad status codes
            data = response.json()

        if data.get('code') == 200 and data.get('data'):
            tafseer_text = data['data'].get('text')
            if tafseer_text:
                 log.info(f"Tafsir found for {surah_number}:{ayah_number}")
                 return tafseer_text # Return original text
            else:
                 log.warning(f"Tafsir text not found in response for {surah_number}:{ayah_number}")
                 return None
        else:
            log.warning(f"API Error fetching Tafsir: {data.get('status')}")
            return None
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
             log.warning(f"Tafsir API returned 404 for {surah_number}:{ayah_number}")
        else:
             log.exception(f"HTTP Status Error fetching Tafsir: {e.response.status_code} - {e.response.text}")
        return None
    except httpx.RequestError as e:
        log.exception(f"HTTP Request Error fetching Tafsir")
        return None
    except Exception as e:
        log.exception(f"Unexpected error fetching Tafsir")
        return None


# --- معالج أوامر Pyrogram (معدل للبحث المحلي وعرض النص الأصلي) ---
# --- Modified Pyrogram handler (uses local search and displays original text) ---
if app: # فقط قم بتعريف المعالج إذا تم العثور على 'app'
    @app.on_message(
        (filters.text & filters.regex(r"^بحث\s+", flags=re.IGNORECASE)) | # Trigger on text starting with "بحث " (case-insensitive)
        (filters.command(["quran", "ayah", "آية", "قران"])) # Also trigger on commands
        & filters.private # Optional: Respond only in private chats - اختياري: الاستجابة في المحادثات الخاصة فقط
    )
    async def quran_search_handler(client: Client, message: Message):
        """
        يعالج الأمر /quran أو الرسائل التي تبدأ بـ "بحث " للبحث عن آية قرآنية باستخدام البحث المحلي التقريبي.
        Handles /quran command or messages starting with "بحث " to search for a Quranic verse using local fuzzy search.
        Displays top 1-5 matches (<=2 full original, 3-5 snippet buttons). Shows original text.
        """
        global quran_data # Access the loaded data

        keyword = ""
        if message.text and message.text.lower().startswith("بحث "):
             match = re.match(r"^بحث\s+(.+)", message.text, flags=re.IGNORECASE)
             if match:
                  keyword = match.group(1).strip()
        elif message.command:
             if len(message.command) > 1:
                 keyword = " ".join(message.command[1:])

        if not keyword:
             log.warning(f"User {message.from_user.id} triggered handler without a valid keyword.")
             await message.reply_text(
                 '⚠️ يرجى إدخال كلمة للبحث عنها بعد الأمر أو بعد كلمة "بحث ". \n'
                 'مثال:\n`/quran الله نور السماوات`\n`بحث الله نور السماوات`'
             )
             return

        # Check if data is loaded and fuzzy search is available
        if not quran_data:
             await message.reply_text("⚠️ عذراً، بيانات القرآن المحلية غير محملة. لا يمكن البحث حالياً.")
             return
        if not THEFUZZ_AVAILABLE:
             await message.reply_text("⚠️ عذراً، ميزة البحث التقريبي غير متاحة حالياً بسبب عدم تثبيت المكتبة المطلوبة (`thefuzz`).")
             return

        log.info(f"User {message.from_user.id} initiated local fuzzy search with keyword: '{keyword}'")
        m = await message.reply_text(f"⏳ جار البحث (بشكل تقريبي محلياً) عن آية تحتوي على: `{keyword}`...")

        try:
            # 1. Search using local fuzzy matching helper function
            #    البحث باستخدام دالة البحث التقريبي المحلية المساعدة
            search_outcome = await search_ayah_local_json_fuzzy(keyword, quran_data)

            # --- Handle outcome ---
            if search_outcome is None:
                 # Case 1: No results found
                 log.info(f"No results found for keyword: '{keyword}' using local fuzzy search.")
                 await m.edit_text(f"❌ لم يتم العثور على آيات مشابهة بدرجة كافية لـ: **{keyword}**.")
                 return

            # No "Too Many" case needed here as fuzzy search returns top 5 max
            # لا حاجة لحالة "نتائج كثيرة جداً" هنا لأن البحث التقريبي يعيد 5 كحد أقصى

            elif isinstance(search_outcome, list):
                # Case 2: 1-5 fuzzy matches found
                num_results = len(search_outcome)
                log.info(f"Processing {num_results} fuzzy match(es)...")
                await m.delete() # Delete "Searching..." message

                if num_results <= 2:
                    # Display full details for 1 or 2 results
                    log.info(f"Displaying full details for {num_results} fuzzy result(s).")
                    for index, result_data in enumerate(search_outcome):
                        verse_key = result_data["verseKey"]
                        # Use the original text from the local data for display
                        # استخدام النص الأصلي من البيانات المحلية للعرض
                        display_text = result_data["original_text"]
                        score = result_data["score"]
                        surah_number, ayah_number = map(int, verse_key.split(':'))
                        log.info(f"Processing fuzzy result {index+1}/{num_results}: {verse_key} (Score: {score})")

                        # Get details (Surah Name, Audio only needed now)
                        # جلب التفاصيل (اسم السورة والصوت فقط مطلوبان الآن)
                        details = await get_ayah_details(surah_number, ayah_number)

                        surah_name = f"سورة {surah_number}"
                        audio_url = None
                        if details:
                            surah_name = details["surahName"]
                            audio_url = details["audioUrl"]
                            log.info(f"Successfully fetched details (audio/name) for {verse_key} from alquran.cloud.")
                        else:
                             log.error(f"Failed to fetch details (audio/name) for {verse_key} from alquran.cloud.")

                        # Format the message using the ORIGINAL text from DB
                        # تنسيق الرسالة باستخدام النص الأصلي من قاعدة البيانات
                        formatted_message = (
                            f"📖 **{surah_name}** ({index+1}/{num_results}) [Score: {score}%]\n"
                            f"🔢 الآية: **{ayah_number}**\n\n"
                            f"{display_text}" # Display original text
                        )
                        buttons = InlineKeyboardMarkup(
                            [[InlineKeyboardButton("📜 عرض التفسير الميسر", callback_data=f"get_tafseer_{surah_number}:{ayah_number}")]]
                        )
                        text_message = await message.reply_text(
                            formatted_message, reply_markup=buttons, disable_web_page_preview=True
                        )
                        log.info(f"Sent fuzzy text result {index+1}/{num_results} for {verse_key} to user {message.from_user.id}")

                        if audio_url:
                            try:
                                reply_to_msg_id = text_message.id
                                log.info(f"Sending audio: {audio_url} for {verse_key}")
                                await message.reply_audio(
                                    audio=audio_url, caption=f"🔊 تلاوة الآية {ayah_number} من سورة {surah_name}", reply_to_message_id=reply_to_msg_id
                                )
                                log.info(f"Sent audio for {verse_key} to user {message.from_user.id}")
                            except Exception as audio_error:
                                log.exception(f"Error sending audio for {verse_key}")

                        if num_results > 1 and index < num_results - 1:
                             await asyncio.sleep(1)
                    return

                elif 3 <= num_results <= 5:
                    # Display results as snippet buttons
                    log.info(f"Displaying snippet buttons for {num_results} fuzzy result(s).")
                    keyboard = [] # List to hold button rows
                    intro_message = f"✅ تم العثور على {num_results} نتائج مشابهة. اضغط على زر الآية لعرضها كاملة:"

                    for index, result_data in enumerate(search_outcome):
                        verse_key = result_data["verseKey"]
                        original_text = result_data["original_text"] # Original text from DB
                        score = result_data["score"]
                        surah_number, ayah_number = map(int, verse_key.split(':'))

                        # Create snippet from the original text for button text
                        # إنشاء مقتطف من النص الأصلي لنص الزر
                        snippet = create_snippet(original_text, keyword)
                        max_len = 55 # Adjusted max length slightly
                        # Add score to button text
                        button_text = f"{index+1}. ({score}%) {snippet}"
                        if len(button_text.encode('utf-8')) > max_len:
                             button_text = button_text[:max_len-3] + "..."

                        button = InlineKeyboardButton(
                            button_text,
                            callback_data=f"get_full_{surah_number}:{ayah_number}" # Trigger full ayah handler
                        )
                        keyboard.append([button]) # Add button as a new row

                    await message.reply_text(
                        intro_message,
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    log.info(f"Sent snippet buttons for keyword '{keyword}' to user {message.from_user.id}")
                    return

            else:
                 # Should not happen if search_outcome is not None and not list
                 log.error(f"Unexpected outcome type from search_ayah_local_json_fuzzy: {type(search_outcome)}")
                 await m.edit_text("❌ حدث خطأ غير متوقع أثناء تحليل نتائج البحث.")
                 return


        except Exception as e:
            log.exception(f"Unhandled error in quran_search_handler for keyword '{keyword}'")
            try:
                if m and not m.is_deleted:
                     await m.edit_text(f"❌ حدث خطأ غير متوقع أثناء البحث عن: **{keyword}**.")
            except Exception:
                 log.error("Failed to edit 'searching' message to show final error.")


    # --- معالج استجابة زر التفسير (يعرض النص الأصلي) ---
    # --- Tafsir Callback Handler (displays original text) ---
    @app.on_callback_query(filters.regex(r"^get_tafseer_(\d+):(\d+)$"))
    async def handle_tafseer_callback(client: Client, callback_query: CallbackQuery):
        """
        يعالج الضغط على زر عرض التفسير.
        Handles the 'Show Tafsir' button press.
        """
        try:
            match = callback_query.matches[0]
            s_num = int(match.group(1))
            a_num = int(match.group(2))
            log.info(f"Tafsir requested by user {callback_query.from_user.id} for {s_num}:{a_num}")

            await callback_query.answer("جاري جلب التفسير...", show_alert=False)
            tafseer_text = await get_tafseer_from_api(s_num, a_num) # Fetches from API

            if tafseer_text:
                 # Display original Tafsir text
                 # عرض نص التفسير الأصلي
                 tafseer_message = f"📜 **التفسير الميسر للآية {a_num} من سورة رقم {s_num}:**\n\n{tafseer_text}" # Use original tafseer_text
                 await callback_query.message.reply_text(
                     tafseer_message,
                     disable_web_page_preview=True
                 )
                 log.info(f"Sent Tafsir for {s_num}:{a_num} to user {callback_query.from_user.id}")
            else:
                 log.warning(f"Tafsir not found via API for {s_num}:{a_num}")
                 await callback_query.answer("عذراً، لم يتم العثور على التفسير لهذه الآية.", show_alert=True)

        except Exception as e:
            log.exception(f"Error handling tafseer callback for {callback_query.data}")
            await callback_query.answer("حدث خطأ أثناء جلب التفسير.", show_alert=True)

    # --- معالج استجابة زر عرض الآية كاملة (يعرض النص الأصلي بالتشكيل) ---
    # --- Callback handler for 'Show Full Ayah' button (displays original text with Tashkeel) ---
    @app.on_callback_query(filters.regex(r"^get_full_(\d+):(\d+)$"))
    async def handle_full_ayah_callback(client: Client, callback_query: CallbackQuery):
        """
        يعالج الضغط على زر عرض الآية كاملة، ويرسل النص الكامل الأصلي والصوت وزر التفسير.
        Handles the 'Show Full Ayah' button press, sends full original text, audio, and Tafsir button.
        """
        global quran_data # Access loaded data to get original text
        try:
            match = callback_query.matches[0]
            s_num = int(match.group(1))
            a_num = int(match.group(2))
            log.info(f"Full Ayah requested by user {callback_query.from_user.id} for {s_num}:{a_num}")

            # Acknowledge button press
            await callback_query.answer("جاري جلب الآية والتفاصيل...", show_alert=False)

            # --- Get original text from local data ---
            original_verse_text = None
            if quran_data:
                 # Find the specific verse in the loaded data
                 # البحث عن الآية المحددة في البيانات المحملة
                 for ayah_obj in quran_data:
                     if ayah_obj.get('sura_no') == s_num and ayah_obj.get('aya_no') == a_num:
                         original_verse_text = ayah_obj.get('aya_text')
                         break
            # --- End get original text ---

            if not original_verse_text:
                 log.error(f"Could not find original text for {s_num}:{a_num} in local JSON data.")
                 # Fallback: try fetching from get_ayah_details (might be inconsistent)
                 details_fallback = await get_ayah_details(s_num, a_num)
                 if details_fallback and details_fallback.get("reciterVerseText"):
                      original_verse_text = details_fallback["reciterVerseText"]
                      log.warning("Using fallback text from get_ayah_details for full ayah display.")
                 else:
                      await callback_query.answer("عذراً، لم أتمكن من العثور على نص الآية.", show_alert=True)
                      return

            # Fetch details for Surah name and audio URL only
            # جلب التفاصيل لاسم السورة ورابط الصوت فقط
            details = await get_ayah_details(s_num, a_num)
            surah_name = details.get("surahName", f"سورة {s_num}") if details else f"سورة {s_num}"
            audio_url = details.get("audioUrl") if details else None

            # Use the original text fetched from local data (or fallback)
            # استخدام النص الأصلي الذي تم جلبه من البيانات المحلية (أو البديل)
            display_text = original_verse_text

            # Format message for full verse using ORIGINAL text
            # تنسيق رسالة الآية الكاملة باستخدام النص الأصلي
            full_ayah_message = (
               f"📖 **{surah_name}**\n"
               f"🔢 الآية: **{a_num}**\n\n"
               f"{display_text}" # Display original text
            )

            # Re-add Tafsir button for convenience
            buttons = InlineKeyboardMarkup(
               [
                   [InlineKeyboardButton(
                       "📜 عرض التفسير الميسر",
                       callback_data=f"get_tafseer_{s_num}:{a_num}"
                   )]
               ]
            )

            # Send the full original verse as a new message
            # إرسال الآية الكاملة الأصلية كرسالة جديدة
            sent_message = await callback_query.message.reply_text(
                full_ayah_message,
                reply_markup=buttons,
                disable_web_page_preview=True
            )
            log.info(f"Sent Full Original Ayah {s_num}:{a_num} to user {callback_query.from_user.id}")

            # Send audio if available, replying to the full text message
            if audio_url:
                try:
                    reply_to_msg_id = sent_message.id # ID of the message just sent
                    log.info(f"Sending audio: {audio_url} for {s_num}:{a_num}")
                    await client.send_audio(
                        chat_id=callback_query.message.chat.id,
                        audio=audio_url,
                        caption=f"🔊 تلاوة الآية {a_num} من سورة {surah_name}",
                        reply_to_message_id=reply_to_msg_id
                    )
                    log.info(f"Sent audio for {s_num}:{a_num} to user {callback_query.from_user.id}")
                except Exception as audio_error:
                    log.exception(f"Error sending audio for {s_num}:{a_num} in callback")

        except Exception as e:
            log.exception(f"Error handling full ayah callback for {callback_query.data}")
            await callback_query.answer("حدث خطأ أثناء جلب الآية الكاملة.", show_alert=True)


else:
    log.error("متغير العميل 'app' غير متاح، لن يتم تسجيل معالج القرآن أو التفسير تلقائياً.")

# --- لا حاجة لتعليقات التسجيل اليدوي هنا الآن ---

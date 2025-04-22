import os
import json
import re
import unicodedata
import logging
import httpx # استخدام httpx للطلبات غير المتزامنة
import urllib.parse # لترميز رابط الـ API
import traceback # لاستخدامه في طباعة تتبع الخطأ الكامل
from pathlib import Path
from typing import List, Dict, Any, Optional

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import MessageDeleteForbidden, RPCError

# --- استيراد تطبيق YukkiMusic ---
try:
    from YukkiMusic import app
except ImportError:
    logging.error("Could not import 'app' from YukkiMusic. Ensure the path is correct.")
    app = None

# --- الإعدادات ---
API_TIMEOUT = 20 # مهلة طلب API بالثواني

# --- التهيئة ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- الدوال المساعدة ---
def log_error(message: str, error: Optional[Exception] = None):
    """Logs an error message, including traceback if an exception is provided."""
    if error: logger.error(f"{message}: {error}", exc_info=True)
    else: logger.error(message)

def convert_html_to_text(html_content: str) -> str:
    """Converts HTML content to plain text. Requires `beautifulsoup4`."""
    if not html_content: return ""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')
        for a_tag in soup.find_all('a'): a_tag.unwrap()
        for matn_tag in soup.find_all('a', class_='matn'): matn_tag.unwrap()
        return soup.get_text(separator=' ', strip=True)
    except ImportError:
        log_error("BeautifulSoup4 not installed. Cannot parse HTML.")
        text = re.sub('<[^>]+>', ' ', html_content)
        return ' '.join(text.split())
    except Exception as e:
        log_error("Error converting HTML to text", e)
        return html_content

# --- دالة البحث (API فقط) ---
async def search_hadith_api(query: str) -> List[Dict[str, Any]]:
    """Searches using Alminasa Semantic Search API based on provided JSON structure."""
    if not query: return []
    encoded_query = urllib.parse.quote(query)
    api_url = f"https://alminasa.ai/api/semantic?search={encoded_query}"
    logger.info(f"[Hadith Search] Querying API: {api_url}") # إضافة علامة مميزة للسجل
    processed_results = []
    response = None
    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.get(api_url)
            response.raise_for_status()
            results = response.json()
            if isinstance(results, dict) and 'data' in results and isinstance(results['data'], list):
                 actual_results = results.get('data', [])
                 logger.info(f"[Hadith Search] API returned {len(actual_results)} results under 'data' key.")
                 for item in actual_results:
                    source_data = item.get('_source', {})
                    if not source_data: continue
                    processed_results.append({
                        'hadith_id': source_data.get('hadith_id'), 'book': source_data.get('hadith_book_name', 'غير متوفر'),
                        'text': source_data.get('matn_with_tashkeel', ''), 'chapter': source_data.get('chapter', 'غير متوفر'),
                        'sub_chapter': source_data.get('sub_chapter'), 'page': source_data.get('page'),
                        'volume': source_data.get('volume'), 'narrators': source_data.get('narrators', []),
                        'rulings': source_data.get('rulings', [])
                    })
            else:
                logger.warning(f"[Hadith Search] API returned unexpected structure or no 'data' key: {type(results)}")
                logger.debug(f"[Hadith Search] API Response content (first 500 chars): {str(results)[:500]}")
    except httpx.HTTPStatusError as e:
        log_error(f"[Hadith Search] API request failed (HTTP Status {e.response.status_code}) for URL: {api_url}", e)
        if response: logger.debug(f"[Hadith Search] API Response body: {response.text}")
    except httpx.RequestError as e:
        log_error(f"[Hadith Search] API request failed (Network/Timeout) for URL: {api_url}", e)
    except json.JSONDecodeError as e:
        log_error(f"[Hadith Search] Failed to decode API JSON response from URL: {api_url}", e)
        if response:
            try: logger.debug(f"[Hadith Search] API Raw response text: {response.text}")
            except Exception as debug_err: log_error("[Hadith Search] Error trying to log raw API response text", debug_err)
    except Exception as e:
        log_error(f"[Hadith Search] Unexpected error during API search for URL: {api_url}", e)
    return processed_results


# --- معالج الرسائل (Handler) ---
if app:
    # --- إزالة group=-1 للعودة إلى الأولوية الافتراضية (0) ---
    @app.on_message(filters.regex(r'^حديث\s+(.+)') & (filters.private | filters.group))
    async def hadith_search_handler(client: Client, message: Message):
        """Handles text messages starting with 'حديث ' - API Only."""
        # --- تسجيل الدخول للمعالج ---
        logger.info(f"[Hadith Handler] Entered for user {message.from_user.id} in chat {message.chat.id}.")
        # ---------------------------

        keyword = ""
        if message.matches:
            keyword = message.matches[0].group(1).strip()

        if not keyword:
            logger.warning("[Hadith Handler] No keyword found after 'حديث '. Exiting.")
            return

        logger.info(f"[Hadith Handler] Received keyword: '{keyword}'")

        waiting_msg = None
        api_result = []

        # --- كتلة try...except شاملة للمعالج ---
        try:
            # إرسال رسالة انتظار للمستخدم
            try:
                waiting_msg = await message.reply_text('🔍 جاري البحث عبر API، يرجى الانتظار...', quote=True)
                logger.info("[Hadith Handler] Sent 'waiting' message.")
            except RPCError as e:
                log_error("[Hadith Handler] Could not send 'waiting' message", e)

            # --- البحث باستخدام API ---
            logger.info(f"[Hadith Handler] Attempting API search for '{keyword}'")
            api_result = await search_hadith_api(keyword)

            # --- التحقق من نتيجة API وإرسال الرد ---
            if api_result:
                logger.info(f"[Hadith Handler] API search successful for '{keyword}'. Formatting result.")
                hadith = api_result[0]

                # تنسيق نتيجة API
                narrators_list = hadith.get('narrators', [])
                narrators_str = ', '.join(f"{n.get('full_name', '?')}" for n in narrators_list if isinstance(n, dict)) if isinstance(narrators_list, list) and narrators_list else 'غير متوفر'
                rulings_list = hadith.get('rulings', [])
                rulings_str = '\n'.join(f"  - **{r.get('ruler', '?')}**: {r.get('ruling', '?')} (المصدر: {r.get('book_name', '?')})" for r in rulings_list if isinstance(r, dict)) if isinstance(rulings_list, list) and rulings_list else 'لا يوجد أحكام مرفقة'
                formatted_message = f"📖 **الكتاب:** {hadith.get('book', 'غير متوفر')}\n"
                chapter_info = hadith.get('chapter', ''); sub_chapter_info = hadith.get('sub_chapter')
                if chapter_info: formatted_message += f"📁 **الباب:** {chapter_info}" + (f" ({sub_chapter_info})" if sub_chapter_info else "") + "\n\n"
                else: formatted_message += "\n"
                formatted_message += f"📜 **الحديث:**\n{hadith.get('text', 'النص غير متوفر')}\n\n"
                info_parts = [];
                if hadith.get('volume'): info_parts.append(f"المجلد: {hadith.get('volume')}")
                if hadith.get('page'): info_parts.append(f"الصفحة: {hadith.get('page')}")
                if info_parts: formatted_message += f"ℹ️ **معلومات:** {' | '.join(info_parts)}\n\n"
                if narrators_str != 'غير متوفر': formatted_message += f"👥 **الرواة:** {narrators_str}\n\n"
                if rulings_str != 'لا يوجد أحكام مرفقة': formatted_message += f"⚖️ **الأحكام:**\n{rulings_str}"

                logger.info(f"[Hadith Handler] Attempting to send formatted result for '{keyword}'.")
                await message.reply_text(formatted_message.strip(), quote=True, disable_web_page_preview=True)
                logger.info(f"[Hadith Handler] Successfully sent result for '{keyword}'.")

            else: # إذا لم تُرجع API أي نتائج
                logger.info(f"[Hadith Handler] API search for '{keyword}' returned no results. Sending 'not found' message.")
                await message.reply_text(f'لم يتم العثور على نتائج لكلمة البحث: "{keyword}" عبر API.', quote=True)
                logger.info(f"[Hadith Handler] Successfully sent 'not found' message for '{keyword}'.")

        # --- معالجة الأخطاء الشاملة ---
        except Exception as e:
            log_error(f"[Hadith Handler] An unexpected error occurred for keyword '{keyword}'", e) # تسجيل الخطأ الكامل
            try:
                logger.info(f"[Hadith Handler] Attempting to send generic error message for '{keyword}'.")
                await message.reply_text("حدث خطأ غير متوقع أثناء معالجة طلبك. يرجى المحاولة مرة أخرى لاحقًا.", quote=True)
            except Exception as reply_err:
                log_error("[Hadith Handler] Failed to send error message to user", reply_err)
        # --- نهاية كتلة try...except الشاملة ---

        # --- الخطوة النهائية: حذف رسالة الانتظار (دائماً في النهاية) ---
        finally:
            if waiting_msg:
                try:
                    logger.info("[Hadith Handler] Attempting to delete 'waiting' message.")
                    await waiting_msg.delete()
                    logger.info("[Hadith Handler] Successfully deleted 'waiting' message.")
                except MessageDeleteForbidden:
                    logger.warning("[Hadith Handler] No permission to delete 'waiting' message.")
                except Exception as delete_err:
                    log_error("[Hadith Handler] Could not delete waiting message in finally block", delete_err)
            logger.info(f"[Hadith Handler] Finished processing request for '{keyword}'.")


# --- معلومات المساعدة ---
__MODULE__ = "Hadith"
__HELP__ = """
**Hadith Search (API Only)**

Search for Hadiths using the Alminasa API.

**Usage:**
Send a message starting with `حديث ` followed by your search keyword.

**Example:**
`حديث الصلاة`
`حديث الصيام`

**Note:**
- This command relies entirely on the `https://alminasa.ai/api/semantic` API. Ensure the bot has internet access.
- Only the first result found by the API will be displayed.
"""

# --- رسالة عند تحميل الـ Plugin ---
if not app:
     logger.warning("Pyrogram 'app' not initialized. Hadith plugin handlers are not active.")
else:
     # تم تحديث الرسالة لتعكس المجموعة الافتراضية
     logger.info("Hadith Plugin (API Only) loaded and handler registered in default group (0).")


import os
import json
import re
import asyncio
import logging
import html # Import html module for escaping

# Logging setup (consider using the main bot's logger if available)
# Define logger early so the import attempt below can use it
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__) # Use module-specific logger

# --- Attempt to import the main app instance ---
try:
    # Attempt to import the app instance from YukkiMusic structure
    from YukkiMusic import app
    if app is None:
         # Handle case where app might be imported but is None
         raise ImportError("Imported 'app' is None.")
    logger.info("Successfully imported 'app' from YukkiMusic.")
except ImportError:
    # Log critical error if app import fails and set app to None
    logging.critical("لم يتم العثور على متغير العميل 'app'. الـ decorator لن يعمل!")
    app = None # Set app to None if import fails

# --- Continue with other imports ---
# Only import pyrogram related things if app import potentially succeeded
# or handle the case where they are needed even without app (e.g., for types)
from pyrogram import Client, filters, types # Client might not be needed if only using imported app
from pyrogram.errors import MessageIdInvalid
from pyrogram.enums import ParseMode # Import ParseMode enum
from fuzzywuzzy import process, fuzz # For fuzzy string matching
import pyarabic.araby as araby # For removing Arabic diacritics


# --- Configuration ---
# Path to the fatwas data file
# Corrected path calculation v2: More robust attempt to find project root
try:
    # Get the absolute path of the current script file
    script_path = os.path.abspath(__file__)
    logger.debug(f"Script absolute path (__file__): {script_path}")

    # Assume the project root is the directory named 'YukkiMusic' in the path
    # This relies on the directory structure containing '/YukkiMusic/'
    project_root = None
    parts = script_path.split(os.sep)
    for i in range(len(parts) - 1, 0, -1):
        current_path = os.sep.join(parts[:i+1])
        if parts[i] == 'YukkiMusic':
            project_root = current_path
            logger.debug(f"Found project root marker 'YukkiMusic' at: {project_root}")
            break

    if project_root is None:
        # Fallback if 'YukkiMusic' marker not found in path
        # Assume the current working directory might be the project root
        project_root = os.getcwd()
        logger.warning(f"Could not reliably determine project root from script path. Assuming CWD '{project_root}' is project root.")

    # Join project root with the filename 'fatwas.json'
    FATWAS_FILE_PATH = os.path.join(project_root, 'fatwas.json')
    logger.info(f"Revised absolute path for fatwas.json: {os.path.abspath(FATWAS_FILE_PATH)}")

except NameError:
    # Fallback if __file__ is not defined (e.g., interactive execution)
    # Assume fatwas.json is relative to the current working directory
    FATWAS_FILE_PATH = 'fatwas.json'
    logger.warning(f"__file__ not defined. Using relative fallback path: {FATWAS_FILE_PATH}")
    logger.info(f"Absolute fallback path for fatwas.json: {os.path.abspath(FATWAS_FILE_PATH)}")
except Exception as e:
     # Catch any other error during path calculation
     logger.error(f"Error calculating FATWAS_FILE_PATH: {e}", exc_info=True)
     FATWAS_FILE_PATH = 'fatwas.json' # Default fallback
     logger.error(f"Defaulting FATWAS_FILE_PATH to relative path: {FATWAS_FILE_PATH}")


# --- Globals ---
fatwa_data = [] # To store loaded fatwa data
fatwa_search_list = [] # To store data prepared for fuzzy search

# --- Helper Functions ---

def remove_diacritics(text):
    """Removes Arabic diacritics (tashkeel) from text."""
    if not text:
        return ""
    try:
        # Use pyarabic to remove diacritics
        return araby.strip_tashkeel(text)
    except Exception as e:
        logger.error(f"Error removing diacritics from '{text[:50]}...': {e}")
        return text # Return original text if error occurs

def load_fatwas():
    """Loads fatwa data from the JSON file."""
    global fatwa_data, fatwa_search_list
    absolute_path = os.path.abspath(FATWAS_FILE_PATH)
    logger.info(f"Attempting to load fatwas from: {absolute_path}") # Path should now be corrected

    # Reset lists before loading
    fatwa_data = []
    fatwa_search_list = []
    raw_data = [] # Initialize raw_data

    try:
        # Check if file exists
        if not os.path.exists(absolute_path):
            logger.error(f"Fatwa data file NOT FOUND at the specified path: {absolute_path}")
            # (Removed directory/file creation logic as the file should exist)
            return False # Cannot proceed if file doesn't exist at the corrected path

        # Check file permissions (Read)
        if not os.access(absolute_path, os.R_OK):
             logger.error(f"Read permission denied for file: {absolute_path}")
             return False

        # Proceed to read the file
        logger.info(f"File found and accessible. Reading content from: {absolute_path}")
        with open(absolute_path, 'r', encoding='utf-8') as f:
            # Handle empty file case
            content = f.read()
            if not content.strip(): # Check if content is empty or just whitespace
                logger.warning(f"Fatwa data file is empty or contains only whitespace: {absolute_path}")
                # Keep raw_data as [], lists will remain empty
            else:
                # Rewind file pointer before reading json
                f.seek(0)
                try:
                    raw_data = json.load(f)
                    logger.info(f"Successfully decoded JSON. Found {len(raw_data) if isinstance(raw_data, list) else 'N/A'} potential items.")
                    # Validate if raw_data is a list
                    if not isinstance(raw_data, list):
                        logger.error(f"JSON content is not a list as expected. Type found: {type(raw_data)}")
                        raw_data = [] # Reset to prevent further errors
                        return False
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding JSON from file: {absolute_path} - {e}")
                    return False # Stop processing if JSON is invalid

        # Process the loaded data
        logger.info(f"Processing {len(raw_data)} items from JSON data.")
        items_processed = 0
        for i, item in enumerate(raw_data):
             # Basic validation of item structure
            if not isinstance(item, dict):
                logger.warning(f"Skipping invalid item at index {i} in fatwas.json (not a dictionary). Item: {item}")
                continue

            # Store original data
            # Use 'id' from JSON if present and valid, otherwise use index 'i'
            fatwa_id = item.get('id', i)
            if not isinstance(fatwa_id, (int, str)): # Basic check for valid ID type
                logger.warning(f"Invalid 'id' type found for item at index {i}. Using index as ID. Item: {item}")
                fatwa_id = i

            fatwa_entry = {
                'id': fatwa_id,
                'question': item.get('question', ''),
                'answer': item.get('answer', ''),
                'title': item.get('title', ''),
                'categories': item.get('categories', []),
                'link': item.get('link', ''),
                'audio': item.get('audio', '')
            }
            # Check for duplicate IDs before appending
            if any(f['id'] == fatwa_id for f in fatwa_data):
                 logger.warning(f"Duplicate fatwa ID '{fatwa_id}' found at index {i}. Skipping item: {item}")
                 continue

            fatwa_data.append(fatwa_entry)

            # --- Prepare data for searching (UPDATED LOGIC) ---
            # Get categories, ensure it's a list, handle potential errors
            categories_list = item.get('categories', [])
            if not isinstance(categories_list, list):
                logger.warning(f"Categories field is not a list for fatwa ID {fatwa_id}. Skipping categories in search text.")
                categories_list = []
            # Remove diacritics from each category and join them with spaces
            categories_text = " ".join([remove_diacritics(str(cat)) for cat in categories_list])

            # Combine title, question, and categories text for searching (excludes answer)
            search_text = f"{remove_diacritics(item.get('title', ''))} {remove_diacritics(item.get('question', ''))} {categories_text}"
            # --- End of Updated Logic ---

            if search_text.strip(): # Only add if there's searchable text
                fatwa_search_list.append({
                    'id': fatwa_entry['id'], # Store the same ID used in fatwa_data
                    'search_text': search_text.strip()
                })
            else:
                 logger.warning(f"No searchable text (title/question/categories) found for fatwa ID '{fatwa_id}'. Skipping search list entry.")

            items_processed += 1

        logger.info(f"Successfully processed {items_processed} fatwa items. `fatwa_search_list` size: {len(fatwa_search_list)}")
        if not fatwa_search_list and raw_data:
             logger.warning("Processed JSON data but the search list is still empty. Check item structure or content in JSON.")
        elif not raw_data:
              logger.info("JSON file was empty or contained no valid items.")

        return True # Loading process completed (even if no data was found in an empty file)

    except IOError as e:
         logger.error(f"File I/O error for {absolute_path}: {e}", exc_info=True)
         return False
    except Exception as e:
        logger.error(f"An unexpected error occurred during fatwa loading: {e}", exc_info=True)
        return False

async def search_fatwa_fuzzy(query: str):
    """
    Searches for fatwas using fuzzy matching.
    """
    if not fatwa_search_list:
        # This warning is expected if load_fatwas returned True but file was empty/invalid structure
        logger.warning("Fatwa search list is empty. Cannot perform search. Check previous logs for loading issues.")
        return []

    # Remove diacritics from the search query
    search_term = remove_diacritics(query)
    if not search_term:
        return []

    # Use fuzzywuzzy's process.extractOne to find the best match
    # scorer changed to fuzz.token_set_ratio for potentially better matching with word order/subset differences
    # score_cutoff kept at 65
    try:
        # Create a dictionary mapping the fatwa ID to its searchable text
        # This allows extractOne to return the ID directly
        choices = {item['id']: item['search_text'] for item in fatwa_search_list if item.get('search_text')}
        if not choices:
             logger.warning("Fuzzy search attempted but no search texts available in fatwa_search_list.")
             return []

        # extractOne returns (choice_text, score, choice_key) where key is the dict key (fatwa id)
        logger.info(f"Performing fuzzy search with scorer=token_set_ratio and score_cutoff=65") # Log scorer being used
        best_match = process.extractOne(
            search_term,
            choices, # Pass the dictionary {id: search_text}
            scorer=fuzz.token_set_ratio, # Changed scorer
            score_cutoff=65 # Adjusted score cutoff (was 70)
        )
    except Exception as e:
        logger.error(f"Error during fuzzywuzzy matching: {e}", exc_info=True)
        return []


    if best_match:
        # best_match is a tuple: (matched_text, score, original_fatwa_id)
        matched_text, score, original_fatwa_id = best_match
        logger.info(f"Fuzzy search found match (score {score}): '{matched_text[:100]}...' for query '{query}' (Fatwa ID: {original_fatwa_id})")
        # Find the original fatwa data using the ID returned by extractOne
        original_fatwa = next((f for f in fatwa_data if f['id'] == original_fatwa_id), None)

        if original_fatwa:
            return [original_fatwa] # Return as a list to match original JS structure
        else:
             # This case should be less likely now if IDs are consistent
             logger.error(f"Could not find original fatwa data for matched ID {original_fatwa_id}")
             return []
    else:
        logger.info(f"No fuzzy match found for query: '{query}'")
        return []

# --- Pyrogram Handlers ---
# These handlers will only be registered if `app` was successfully imported.

async def handle_fatwa_request(client: Client, message: types.Message, keyword: str):
    """Common logic to handle fatwa search requests."""
    # `client` argument here is the `app` instance passed by the decorator
    message_id = message.id
    chat_id = message.chat.id

    if not keyword:
        await message.reply_text(
            'يرجى إدخال كلمة للبحث عنها في الفتاوى.',
            reply_to_message_id=message_id
        )
        return

    # Send "searching" message
    waiting_message = None # Initialize to None
    try:
        waiting_message = await message.reply_text(
            '🔍 جاري البحث عن الفتوى، يرجى الانتظار...',
            reply_to_message_id=message_id,
            disable_notification=True # Optional: avoid notification for waiting message
        )
    except Exception as e:
         logger.error(f"Failed to send 'searching' message: {e}")
         # Continue without the waiting message if sending failed

    try:
        # Perform the search
        search_result = await search_fatwa_fuzzy(keyword)

        if search_result:
            # Get the first result (matching JS behavior)
            result = search_result[0]

            # --- Format the message using HTML ---
            # Get original title
            original_title = result.get('title', 'بدون عنوان')

            # --- REMOVED: Prefix removal logic ---
            # Now uses the original title directly for display
            title_to_display = original_title
            # --- END REMOVED ---

            # Escape potentially problematic HTML characters in the data itself
            title = html.escape(title_to_display) # Use the (original) title for display
            question = html.escape(result.get('question', ''))
            answer = html.escape(result.get('answer', '')) # Escape answer even if not searched
            link = html.escape(result.get('link', ''))

            # Removed <pre> tags to avoid code-like formatting
            formatted_message = f"📜 <b>{title}</b> 📜\n\n" # Use <b> for bold
            if question:
                 formatted_message += f"❓ <b>السؤال:</b>\n{question}\n\n" # Just use escaped text
            if answer:
                 formatted_message += f"📜 <b>الإجابة:</b>\n{answer}\n\n" # Just use escaped text
            if link:
                 # Use <a> for link
                 formatted_message += f'🔗 <a href="{link}">رابط الفتوى</a>'
            else:
                 formatted_message += "🔗 (لا يوجد رابط متوفر)"
            # --- End of HTML Formatting ---


            # Send the text result (Pyrogram handles splitting long messages)
            # Use ParseMode.HTML
            sent_message = await client.send_message( # Use the client passed to the handler
                chat_id=chat_id,
                text=formatted_message,
                parse_mode=ParseMode.HTML, # Corrected parse mode to HTML
                disable_web_page_preview=True,
                reply_to_message_id=message_id
            )

            # Send the audio file if available
            if result.get('audio'):
                audio_url_or_path = result['audio']
                try:
                    # Sanitize title for filename
                    safe_title = re.sub(r'[\\/*?:"<>|]', "", result.get('title', 'audio')) # Remove invalid chars
                    filename = f"{safe_title[:50].strip()}.mp3" # Limit length and add extension

                    # --- Refined Audio Caption Logic ---
                    # Use the ORIGINAL title for the audio caption
                    original_caption_title = result.get('title', '')
                    # REMOVE "درجة حديث " prefix specifically for the caption if it exists
                    prefix_to_remove_caption = "درجة حديث " # Define prefix again for clarity
                    if original_caption_title.startswith(prefix_to_remove_caption):
                        caption_title_to_use = original_caption_title[len(prefix_to_remove_caption):]
                    else:
                        caption_title_to_use = original_caption_title
                    # Escape the potentially modified title for the caption
                    escaped_caption_title = html.escape(caption_title_to_use)

                    # Corrected audio caption prefix
                    audio_caption = f"🔊 تسجيل صوتي: {escaped_caption_title}"
                    # --- End of Refined Audio Caption Logic ---


                    logger.info(f"Attempting to send audio: {audio_url_or_path}")
                    await client.send_audio( # Use the client passed to the handler
                        chat_id=chat_id,
                        audio=audio_url_or_path,
                        caption=audio_caption, # Use updated caption
                        # caption parse_mode defaults to the client's default, usually None or HTML
                        # Set explicitly if needed: parse_mode=ParseMode.HTML
                        file_name=filename,
                        reply_to_message_id=message_id # Reply to original user message
                        # Consider replying to the text result instead: reply_to_message_id=sent_message.id
                    )
                    logger.info(f"Successfully sent audio for fatwa '{result.get('title', '')}'")
                except Exception as audio_err:
                    logger.error(f"Failed to send audio {audio_url_or_path} for fatwa '{result.get('title', '')}': {audio_err}", exc_info=True)
                    # Optionally notify user about audio failure
                    # await client.send_message(chat_id, "لم أتمكن من إرسال الملف الصوتي لهذه الفتوى.", reply_to_message_id=message_id)

        else:
            # No results found
            await message.reply_text(
                f'لم يتم العثور على نتائج لكلمة البحث: "{keyword}".',
                reply_to_message_id=message_id
            )

    except ValueError as e:
        # Catch the specific ValueError for parse mode
         logger.error(f"ValueError during message sending (likely parse mode): {e}", exc_info=True)
         await message.reply_text(
             'حدث خطأ أثناء تنسيق الرسالة. يرجى مراجعة المطور.',
             reply_to_message_id=message_id
         )
    except Exception as e:
        logger.error(f"Error processing fatwa search for keyword '{keyword}': {e}", exc_info=True)
        try:
            # Send a generic error message for other exceptions
            await message.reply_text(
                'حدث خطأ غير متوقع أثناء البحث عن الفتوى.',
                reply_to_message_id=message_id
            )
        except Exception as reply_err:
             logger.error(f"Failed to send error message to user: {reply_err}")
    finally:
        # Try to delete the "searching" message
        if waiting_message:
            try:
                await waiting_message.delete()
            except MessageIdInvalid:
                 logger.warning(f"Could not delete 'searching' message (ID: {waiting_message.id}). Maybe it was already deleted or inaccessible.")
            except Exception as delete_err:
                logger.error(f"Error deleting 'searching' message (ID: {waiting_message.id}): {delete_err}")

# --- Register handlers only if app was imported successfully ---
if app:
    # Define filters (consider adding BANNED_USERS if needed, imported from your config)
    # from config import BANNED_USERS # Uncomment if you have BANNED_USERS
    # command_filter = filters.command("fatwa") & ~BANNED_USERS # Example with BANNED_USERS
    command_filter = filters.command("fatwa")

    # --- Updated Regex Filter ---
    # Regex to match messages starting with new trigger words followed by a space and keyword
    # Accounts for optional diacritics on "فتوى"
    fatwa_regex_pattern = r"^(فتوى|فَتْوَى|فُتْوَى|فتوتي|fatwa)\s+(.+)" # Added فتوتي and fatwa
    text_filter = filters.regex(fatwa_regex_pattern, flags=re.IGNORECASE | re.DOTALL)
    # --- End of Updated Regex Filter ---


    # Decorators to register handlers with the main `app` instance
    @app.on_message(command_filter & ~filters.private) # Handle /fatwa command in groups/channels
    @app.on_message(command_filter & filters.private)  # Handle /fatwa command in private chats
    async def fatwa_command_handler(client: Client, message: types.Message):
        """Handles the /fatwa command."""
        # Use message.command to get arguments more reliably
        keyword = ""
        if len(message.command) > 1:
            # Join arguments after the command itself
            keyword = message.text.split(' ', 1)[1].strip()
        await handle_fatwa_request(client, message, keyword)

    # --- Text Trigger Handler - Priority Removed ---
    # Works in private chats and groups/channels again with default priority (group 0)
    @app.on_message(text_filter & ~filters.private) # Removed group=-1
    @app.on_message(text_filter & filters.private)  # Removed group=-1
    async def fatwa_text_handler(client: Client, message: types.Message):
        """Handles messages starting with 'فتوى', 'فتوتي', 'fatwa' in private and group chats."""
        # Add logging to see if this handler is triggered
        logger.info(f"[Fatwa Text Handler - Group 0] Triggered for message (Chat ID: {message.chat.id}): {message.text[:100]}")
        # Use message.matches which contains the result of filters.regex
        if message.matches:
            # Group 2 should contain the keyword after the trigger word and space
            keyword = message.matches[0].group(2).strip()
            # Group 1 contains the trigger word itself (e.g., "فتوى", "فتوتي", "fatwa")
            trigger_word = message.matches[0].group(1)
            logger.info(f"[Fatwa Text Handler - Group 0] Trigger word: '{trigger_word}', Extracted keyword: '{keyword}'")
            await handle_fatwa_request(client, message, keyword)
        else:
             # This case should ideally not happen if the filter matched
             logger.warning("[Fatwa Text Handler - Group 0] Filter matched but no regex groups found.")
    # --- End of Text Trigger Handler ---

    logger.info(f"Fatwa command handler registered for all chats.")
    logger.info(f"Fatwa text handler registered for all chats (Private and Groups/Channels) in default group 0.") # Updated log message


else:
    logger.warning("Fatwa search plugin loaded, but handlers were NOT registered because 'app' could not be imported.")


# --- Load data when module is imported ---
# This runs regardless of whether 'app' was imported, as data might be needed elsewhere
logger.info("Attempting to load fatwa data at module import...")
if not load_fatwas():
    # Log critical error if loading fails definitively (e.g., file not found after checks, permissions error, JSON error)
    logger.critical("Failed to load fatwa data during module import. The fatwa search plugin might not work correctly.")
# Optional: Raise an exception here if data is critical for the bot to run
# else:
#    raise RuntimeError("Failed to load essential fatwa data.")
else:
     # Log success or warning based on list content
     if fatwa_search_list:
        logger.info(f"Fatwa data loaded successfully at module import. Search list size: {len(fatwa_search_list)}")
     else:
        logger.warning("Fatwa data loading function completed at module import, but the search list is empty (e.g., file was empty or contained no valid items).")

# logger.info("Fatwa search plugin module initialized.") # Removed duplicate log line


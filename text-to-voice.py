# استيراد المكتبات اللازمة
import io
import re
import sys
from gtts import gTTS
from pyrogram import filters
from pyrogram.enums import ChatAction
from YukkiMusic import app

# --- معلومات التشخيص (تبقى كما هي) ---
print("--- DIAGNOSTIC INFO ---")
print(f"DEBUG_SYS: Python Executable: {sys.executable}")
print(f"DEBUG_SYS: Python Version: {sys.version}")
print(f"DEBUG_SYS: Python Path: {sys.path}")
print("--- END DIAGNOSTIC INFO ---")
# -----------------------------------------------------

# --- إزالة محاولة الاستيراد المبكر لـ pydub ---
# سيتم استيرادها لاحقًا عند الحاجة فقط

# تعريف الحد الأقصى لعدد الأحرف
MAX_CHARS = 1000
# تعريف معامل سرعة الصوت
PLAYBACK_SPEED = 1.25

@app.on_message(filters.regex(r"^[./!]?تكلم(?: |$)(.*)", flags=re.IGNORECASE))
async def text_to_speech_arabic_enhanced(client, message):
    text_to_convert = ""

    # 1. التحقق من الردود
    if message.reply_to_message:
        if message.reply_to_message.text:
            text_to_convert = message.reply_to_message.text
        else:
            return await message.reply_text("الرجاء الرد على رسالة تحتوي على نص لتحويله إلى كلام.")
    else:
        # 2. استخراج النص من Regex
        match = message.matches[0]
        text_to_convert = match.group(1).strip()
        if not text_to_convert:
            return await message.reply_text(
                "الرجاء كتابة النص الذي تريد تحويله بعد كلمة 'تكلم' أو الرد على رسالة نصية."
            )

    # 3. التحقق من طول النص
    if len(text_to_convert) > MAX_CHARS:
        return await message.reply_text(
            f"عذراً، النص طويل جداً. الحد الأقصى المسموح به هو {MAX_CHARS} حرفاً."
        )

    # 4. التحقق من النص (احتياطي)
    if not text_to_convert:
        return await message.reply_text("لم يتم العثور على نص صالح لتحويله.")

    try:
        print(f"DEBUG: بدأ معالجة النص: '{text_to_convert[:30]}...'")
        await message.reply_chat_action(ChatAction.RECORD_AUDIO)

        # 5. إنشاء الصوت باستخدام gTTS
        print("DEBUG: يتم الآن إنشاء الصوت بواسطة gTTS...")
        tts = gTTS(text=text_to_convert, lang="ar")
        audio_data = io.BytesIO()
        tts.write_to_fp(audio_data)
        audio_data.seek(0)
        print("DEBUG: تم إنشاء الصوت بواسطة gTTS.")

        # 6. محاولة تسريع الصوت (مع استيراد pydub هنا)
        output_audio = audio_data # افتراضياً نستخدم الصوت الأصلي
        file_name = "audio_original.mp3" # اسم الملف الافتراضي

        print("DEBUG: محاولة تسريع الصوت...")
        try:
            # --- تعديل: استيراد pydub هنا ---
            from pydub import AudioSegment
            print("DEBUG: تم استيراد pydub بنجاح.")

            # التأكد من تثبيت ffmpeg (هذا لا يؤكد وجوده فعلياً لكنه تذكير)
            if "ffmpeg" not in AudioSegment.converter:
                 # قد تحتاج pydub إلى تحديد مسار ffmpeg يدوياً في بعض الحالات
                 # AudioSegment.converter = "/path/to/ffmpeg"
                 print("WARN: قد تحتاج إلى تحديد مسار ffmpeg يدوياً في إعدادات pydub إذا لم يتم العثور عليه تلقائياً.")

            print("DEBUG: تحميل الصوت إلى pydub (اكتشاف تلقائي للصيغة)...")
            # نعود للمؤشر الأصلي قبل القراءة بواسطة pydub
            audio_data.seek(0)
            sound = AudioSegment.from_file(audio_data)
            print(f"DEBUG: تم تحميل الصوت بنجاح. المدة: {len(sound) / 1000.0} ثانية.")

            print(f"DEBUG: يتم الآن تسريع الصوت بمعامل {PLAYBACK_SPEED}...")
            faster_sound = sound.speedup(playback_speed=PLAYBACK_SPEED)
            print(f"DEBUG: تم تسريع الصوت. المدة الجديدة: {len(faster_sound) / 1000.0} ثانية.")

            final_audio_data = io.BytesIO()
            print("DEBUG: يتم الآن تصدير الصوت المُسرَّع بصيغة OGG...")
            faster_sound.export(final_audio_data, format="ogg", codec="libopus")
            final_audio_data.seek(0)

            # إذا نجح كل شيء، نستخدم الصوت المُسرَّع
            output_audio = final_audio_data
            file_name = "audio_sped_up.ogg"
            print("DEBUG: تم تصدير الصوت المُسرَّع بنجاح.")

        except Exception as speedup_error:
            # سيتم التقاط خطأ استيراد pydub هنا أيضاً إذا فشل
            print(f"ERROR: حدث خطأ أثناء محاولة استيراد أو تسريع الصوت!")
            print(f"ERROR_DETAILS: {speedup_error}") # طباعة تفاصيل الخطأ (قد يكون خطأ استيراد أو خطأ معالجة)
            print("INFO: سيتم إرسال الصوت الأصلي بدلاً من ذلك.")
            # التأكد من أننا نستخدم بيانات الصوت الأصلية
            audio_data.seek(0)
            output_audio = audio_data
            file_name = "audio_original.mp3"

        # 7. إرسال ملف الصوت
        print(f"DEBUG: يتم الآن إرسال الملف الصوتي: {file_name}")
        output_audio.name = file_name
        await message.reply_audio(
            audio=output_audio,
            caption=f"🔊 تم تحويل النص (حد {MAX_CHARS} حرف):\n\n{text_to_convert[:50]}..."
        )
        print("DEBUG: تم إرسال الملف الصوتي.")
        await message.reply_chat_action(ChatAction.CANCEL)

    except Exception as e:
        print(f"FATAL_ERROR: حدث خطأ عام في معالج الأوامر!")
        print(f"ERROR_DETAILS: {e}")
        try:
            await message.reply_chat_action(ChatAction.CANCEL)
        except Exception as cancel_error:
            print(f"WARN: خطأ أثناء محاولة إلغاء الإجراء بعد خطأ سابق: {cancel_error}")

        await message.reply_text(f"عذراً، حدث خطأ أثناء محاولة تحويل النص إلى كلام: {e}")


# قسم المساعدة (لا تغيير)
__HELP__ = f"""
**أوامر بوت تحويل النص إلى كلام**

استخدم الأمر `تكلم` (مع أو بدون بادئة مثل / أو !) لتحويل النص إلى كلام مسموع باللغة العربية.

**طرق الاستخدام:**

1.  **بعد الأمر مباشرة:**
    `تكلم <النص المراد تحويله>`
    *مثال:* `تكلم السلام عليكم ورحمة الله وبركاته`
    *مثال آخر:* `/تكلم كيف حالك؟`

2.  **بالرد على رسالة:**
    قم بالرد على أي رسالة نصية باستخدام الأمر `تكلم` (بدون كتابة أي نص بعده). سيقوم البوت بتحويل نص الرسالة التي رددت عليها إلى كلام.

**ملاحظات:**
* الحد الأقصى لعدد الأحرف المسموح به هو **{MAX_CHARS} حرفاً**.
* سيتم تسريع الصوت الناتج قليلاً (إذا نجحت العملية).
* تأكد من توفير نص بعد الأمر `تكلم` أو الرد على رسالة نصية تحتوي على كلام.
* قد تحتاج إلى تثبيت `ffmpeg` على نظامك ليعمل تسريع الصوت بشكل صحيح.
* قد تكون هناك مشكلة في وحدة `pyaudioop` المفقودة في بيئة بايثون الحالية لديك مما يمنع التسريع.
"""

# اسم الوحدة (لا تغيير)
__MODULE__ = "تحويل النص إلى كلام (مُحسَّن)"

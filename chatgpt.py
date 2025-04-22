# -*- coding: utf-8 -*-

import re  # استيراد وحدة التعبيرات النمطية
from config import BANNED_USERS
from g4f.client import AsyncClient
from pyrogram import filters
from pyrogram.enums import ParseMode
from YukkiMusic import app

client = AsyncClient()

# تعديل الفلتر لاستخدام التعبيرات النمطية للتحقق من بدء الرسالة بالكلمات المفتاحية
# يتجاهل حالة الأحرف ويسمح بوجود مسافة بعد الكلمة المفتاحية (أو لا)
@app.on_message(
    filters.regex(r"^(ai|chatgpt|ask|معصوم)(\s+.*)?$", flags=re.IGNORECASE) & ~BANNED_USERS
)
async def chatgpt_chat(bot, message):
    # تقسيم نص الرسالة إلى الكلمة المفتاحية وبقية النص
    parts = message.text.split(' ', 1)
    trigger_word = parts[0].lower() # الكلمة المفتاحية المستخدمة (بحروف صغيرة)

    # تحديد نص الإدخال للمستخدم
    if message.reply_to_message and message.reply_to_message.text:
        # إذا كانت الرسالة ردًا، استخدم نص الرسالة التي تم الرد عليها كإدخال
        user_input = message.reply_to_message.text
        # لا يزال trigger_word هو الكلمة التي بدأت بها الرسالة الحالية
    elif len(parts) < 2:
         # إذا لم تكن ردًا ولم يكن هناك نص بعد الكلمة المفتاحية
        await message.reply_text(
            "مثال:\n\n`معصوم كيف طار الامام الباقر بالفيل في السماء؟؟`" # تم تحديث المثال بدون "/"
        )
        return
    else:
        # إذا لم تكن ردًا وكان هناك نص بعد الكلمة المفتاحية
        user_input = parts[1]

    # إرسال رسالة انتظار
    x = await message.reply("انتظر...")

    # تحديد النموذج بناءً على الكلمة المفتاحية المستخدمة
    model = "gpt-4" if trigger_word == "gpt4" else "gpt-4o-mini"

    # استدعاء واجهة برمجة التطبيقات للحصول على الرد
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": user_input},
            ],
        )

        # استخراج وتنظيف نص الرد
        response_text = (
            response.choices[0]
            .message.content.replace("[[Login to OpenAI ChatGPT]]()", "") # إزالة نص تسجيل الدخول (إذا وجد)
            .strip()
        )

        # تقسيم الرد إذا كان طويلاً جدًا (أكثر من 4000 حرف)
        if len(response_text) > 4000:
            parts = [
                response_text[i : i + 4000] for i in range(0, len(response_text), 4000)
            ]
            # تعديل رسالة الانتظار بالجزء الأول
            await x.edit(parts[0], parse_mode=ParseMode.DISABLED)
            # إرسال الأجزاء المتبقية كرسائل جديدة
            for part in parts[1:]:
                await message.reply_text(part, parse_mode=ParseMode.DISABLED)
        else:
            # تعديل رسالة الانتظار بالرد الكامل
            await x.edit(response_text, parse_mode=ParseMode.DISABLED)

    except Exception as e:
        # في حالة حدوث خطأ أثناء استدعاء الـ API
        await x.edit(f"حدث خطأ: {e}") # إبلاغ المستخدم بالخطأ

    # إيقاف انتشار الرسالة لمنع معالجتها بواسطة معالجات أخرى (اختياري، قد لا يكون ضروريًا مع regex)
    # await message.stop_propagation()


# اسم الوحدة (Module Name)
__MODULE__ = "الدردشة الذكية"

# نص المساعدة (Help Text) - تم التحديث لإزالة "/"
__HELP__ = """
**الدردشة مع الذكاء الاصطناعي**

فقط ابدأ رسالتك بإحدى الكلمات التالية متبوعة بسؤالك:

`advice` - احصل على نصيحة عشوائية من البوت (ملاحظة: هذا الأمر قد لا يزال يحتاج "/" إذا لم يتم تعديل الفلتر الخاص به)
`ai` [سؤالك] - اسأل سؤالك باستخدام ذكاء ChatGPT الاصطناعي (نموذج gpt-4o-mini)
`chatgpt` [سؤالك] - نفس الأمر `ai`
`ask` [سؤالك] - نفس الأمر `ai`
`gpt4` [سؤالك] - اسأل سؤالك باستخدام ذكاء ChatGPT الاصطناعي (نموذج GPT-4)
`gemini` [سؤالك] - اسأل سؤالك باستخدام ذكاء Gemini الاصطناعي من جوجل (ملاحظة: هذا الأمر قد لا يزال يحتاج "/" إذا لم يتم تعديل الفلتر الخاص به)

**مثال:**
`ai ما هي عاصمة المغرب؟`
"""

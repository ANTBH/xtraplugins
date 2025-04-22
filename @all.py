# -*- coding: utf-8 -*-

# استيراد المكتبات اللازمة
import asyncio  # للعمليات غير المتزامنة (مثل الانتظار)

from pyrogram import filters  # لاستخدام فلاتر الرسائل (لتحديد الأوامر)
from pyrogram.enums import ChatMembersFilter  # لتحديد نوع الأعضاء (مثل المشرفين)
from pyrogram.errors import FloodWait  # للتعامل مع أخطاء الإرسال المتكرر (Flood)
from YukkiMusic import app  # استيراد كائن التطبيق الرئيسي للبوت

# قائمة لتخزين معرفات الدردشات التي تجري فيها عملية المنشن حاليًا لمنع التكرار
SPAM_CHATS = []


# دالة للتحقق مما إذا كان المستخدم مشرفًا في الدردشة
async def is_admin(chat_id, user_id):
    """
    يتحقق مما إذا كان معرف المستخدم المحدد هو مشرف في معرف الدردشة المحدد.
    """
    admin_ids = [
        admin.user.id
        # حلقة غير متزامنة للحصول على قائمة المشرفين
        async for admin in app.get_chat_members(
            chat_id, filter=ChatMembersFilter.ADMINISTRATORS
        )
    ]
    # التحقق مما إذا كان معرف المستخدم موجودًا في قائمة معرفات المشرفين
    if user_id in admin_ids:
        return True
    return False


# معالج الرسائل للأوامر المتعلقة بمنشن جميع الأعضاء
@app.on_message(
    filters.command(["all", "تاك", "mentionall", "tagall"], prefixes=["/", "@", ""])
)
async def tag_all_users(_, message):
    """
    يقوم بعمل منشن لجميع الأعضاء في المجموعة (باستثناء البوتات والمحذوفين).
    يتطلب أن يكون المستخدم الذي استدعى الأمر مشرفًا.
    """
    # التحقق مما إذا كان مرسل الرسالة مشرفًا
    admin = await is_admin(message.chat.id, message.from_user.id)
    if not admin:
        # إذا لم يكن مشرفًا، لا تفعل شيئًا
        return

    # التحقق مما إذا كانت عملية منشن جارية بالفعل في هذه الدردشة
    if message.chat.id in SPAM_CHATS:
        # إذا كانت جارية، أرسل رسالة تحذيرية
        return await message.reply_text(
            "النداء بدا اذا اردت ايقافه اضغط  /cancel"
            # الترجمة: "عملية المنشن قيد التشغيل بالفعل. إذا كنت تريد إيقافها، استخدم /cancel"
        )

    # التحقق مما إذا كانت الرسالة ردًا على رسالة أخرى
    replied = message.reply_to_message

    # التحقق مما إذا كان الأمر يحتوي على نص أو كان ردًا
    if len(message.command) < 2 and not replied:
        # إذا لم يكن هناك نص ولم يكن ردًا، اطلب من المستخدم إضافة نص أو الرد
        await message.reply_text(
            "** اكتب النداء الذي تريد ان تنادي به الكل, مثلا »** `@all حوار مع نكراني`"
            # الترجمة: "** أعطِ نصًا لعمل منشن للكل، مثل »** `@all مرحبًا يا أصدقاء`"
        )
        return

    # إذا كانت الرسالة ردًا
    if replied:
        usernum = 0  # عداد المستخدمين في الدفعة الحالية
        usertxt = ""  # النص الذي يحتوي على المنشنات
        try:
            # أضف معرف الدردشة إلى قائمة الدردشات النشطة
            SPAM_CHATS.append(message.chat.id)
            # حلقة للحصول على جميع أعضاء الدردشة
            async for m in app.get_chat_members(message.chat.id):
                # إذا تم إلغاء العملية أثناء الحلقة، توقف
                if message.chat.id not in SPAM_CHATS:
                    break
                # تجاهل الحسابات المحذوفة والبوتات
                if m.user.is_deleted or m.user.is_bot:
                    continue
                usernum += 1
                # إضافة منشن للمستخدم إلى النص
                usertxt += f"[{m.user.first_name}](tg://user?id={m.user.id})  "
                # إذا وصل عدد المستخدمين في الدفعة إلى 7
                if usernum == 7:
                    # أرسل المنشنات كرد على الرسالة الأصلية
                    await replied.reply_text(
                        usertxt,
                        disable_web_page_preview=True,  # تعطيل معاينة الروابط
                    )
                    # انتظر ثانية واحدة لتجنب قيود تيليجرام
                    await asyncio.sleep(1)
                    # إعادة تعيين العداد والنص للدفعة التالية
                    usernum = 0
                    usertxt = ""

            # إرسال أي منشنات متبقية بعد انتهاء الحلقة
            if usernum != 0:
                await replied.reply_text(
                    usertxt,
                    disable_web_page_preview=True,
                )
        # التعامل مع خطأ FloodWait (الإرسال المتكرر)
        except FloodWait as e:
            # انتظر للمدة المحددة في الخطأ
            await asyncio.sleep(e.value)
        # في النهاية، حاول إزالة معرف الدردشة من القائمة النشطة
        try:
            SPAM_CHATS.remove(message.chat.id)
        except Exception:
            pass
    # إذا لم تكن الرسالة ردًا (ولكنها تحتوي على نص)
    else:
        usernum = 0
        usertxt = ""
        try:
            # الحصول على النص من الرسالة (بعد الأمر)
            text = message.text.split(None, 1)[1]
            # أضف معرف الدردشة إلى القائمة النشطة
            SPAM_CHATS.append(message.chat.id)
            # حلقة للحصول على جميع أعضاء الدردشة
            async for m in app.get_chat_members(message.chat.id):
                # إذا تم إلغاء العملية، توقف
                if message.chat.id not in SPAM_CHATS:
                    break
                # تجاهل الحسابات المحذوفة والبوتات
                if m.user.is_deleted or m.user.is_bot:
                    continue
                usernum += 1
                # إضافة منشن للمستخدم
                usertxt += f"[{m.user.first_name}](tg://user?id={m.user.id})  "
                # إذا وصلت الدفعة إلى 7 مستخدمين
                if usernum == 7:
                    # أرسل رسالة جديدة تحتوي على النص الأصلي والمنشنات
                    await app.send_message(
                        message.chat.id,
                        f"{text}\n{usertxt}",
                        disable_web_page_preview=True,
                    )
                    # انتظر ثانيتين
                    await asyncio.sleep(2)
                    # إعادة تعيين العداد والنص
                    usernum = 0
                    usertxt = ""
            # إرسال أي منشنات متبقية
            if usernum != 0:
                await app.send_message(
                    message.chat.id,
                    f"{text}\n\n{usertxt}",
                    disable_web_page_preview=True,
                )
        # التعامل مع خطأ FloodWait
        except FloodWait as e:
            await asyncio.sleep(e.value)
        # في النهاية، حاول إزالة معرف الدردشة من القائمة النشطة
        try:
            SPAM_CHATS.remove(message.chat.id)
        except Exception:
            pass


# دالة لعمل منشن لجميع المشرفين (تُستدعى داخليًا)
async def tag_all_admins(_, message):
    """
    يقوم بعمل منشن لجميع المشرفين في المجموعة.
    مشابهة لدالة tag_all_users ولكن تستهدف المشرفين فقط.
    """
    # التحقق مما إذا كانت عملية منشن جارية بالفعل
    if message.chat.id in SPAM_CHATS:
        return await message.reply_text(
            "النداء بدا اذا اردت ايقافه اضغط  /cancel"
            # الترجمة: "عملية المنشن قيد التشغيل بالفعل. إذا كنت تريد إيقافها، استخدم /cancel"
        )

    replied = message.reply_to_message
    # التحقق من وجود نص أو رد
    if len(message.command) < 2 and not replied:
        await message.reply_text(
            "** اكتب النداء الذي تريد ان تنادي به الكل, مثلا »** `@all حوار مع نكراني`"
            # الترجمة: "** أعطِ نصًا لعمل منشن للكل، مثل »** `@admins مرحبًا يا أصدقاء`"
        )
        return

    # إذا كانت الرسالة ردًا
    if replied:
        usernum = 0
        usertxt = ""
        try:
            SPAM_CHATS.append(message.chat.id)
            # حلقة للحصول على المشرفين فقط
            async for m in app.get_chat_members(
                message.chat.id, filter=ChatMembersFilter.ADMINISTRATORS
            ):
                if message.chat.id not in SPAM_CHATS:
                    break
                if m.user.is_deleted or m.user.is_bot:
                    continue
                usernum += 1
                usertxt += f"[{m.user.first_name}](tg://user?id={m.user.id})  "
                if usernum == 7:
                    # إرسال المنشنات كرد
                    await replied.reply_text(
                        usertxt,
                        disable_web_page_preview=True,
                    )
                    await asyncio.sleep(1)
                    usernum = 0
                    usertxt = ""
            # إرسال المنشنات المتبقية
            if usernum != 0:
                await replied.reply_text(
                    usertxt,
                    disable_web_page_preview=True,
                )
        except FloodWait as e:
            await asyncio.sleep(e.value)
        try:
            SPAM_CHATS.remove(message.chat.id)
        except Exception:
            pass
    # إذا لم تكن الرسالة ردًا (ولكنها تحتوي على نص)
    else:
        usernum = 0
        usertxt = ""
        try:
            text = message.text.split(None, 1)[1]
            SPAM_CHATS.append(message.chat.id)
            # حلقة للحصول على المشرفين فقط
            async for m in app.get_chat_members(
                message.chat.id, filter=ChatMembersFilter.ADMINISTRATORS
            ):
                if message.chat.id not in SPAM_CHATS:
                    break
                if m.user.is_deleted or m.user.is_bot:
                    continue
                usernum += 1
                usertxt += f"[{m.user.first_name}](tg://user?id={m.user.id})  "
                if usernum == 7:
                    # إرسال رسالة جديدة بالنص والمنشنات
                    await app.send_message(
                        message.chat.id,
                        f"{text}\n{usertxt}",
                        disable_web_page_preview=True,
                    )
                    await asyncio.sleep(2)
                    usernum = 0
                    usertxt = ""
            # إرسال المنشنات المتبقية
            if usernum != 0:
                await app.send_message(
                    message.chat.id,
                    f"{text}\n\n{usertxt}",
                    disable_web_page_preview=True,
                )
        except FloodWait as e:
            await asyncio.sleep(e.value)
        try:
            SPAM_CHATS.remove(message.chat.id)
        except Exception:
            pass


# معالج الرسائل لأوامر المشرفين والإبلاغ
@app.on_message(
    filters.command(["admin", "مشرف", "مشرفين"], prefixes=["/", "@", ""]) & filters.group
)
async def admintag_with_reporting(client, message):
    """
    يعالج أوامر منشن المشرفين والإبلاغ عن الرسائل للمشرفين.
    إذا كان المرسل مشرفًا، يقوم بمنشن جميع المشرفين الآخرين.
    إذا لم يكن المرسل مشرفًا، يقوم بالإبلاغ عن الرسالة التي تم الرد عليها للمشرفين.
    """
    # التأكد من أن الرسالة من مستخدم وليس قناة مثلاً
    if not message.from_user:
        return

    chat_id = message.chat.id
    from_user_id = message.from_user.id

    # الحصول على قائمة معرفات المشرفين
    admins = [
        admin.user.id
        async for admin in client.get_chat_members(
            chat_id, filter=ChatMembersFilter.ADMINISTRATORS
        )
    ]

    # إذا كان الأمر هو "report"
    if message.command[0] == "report":
        # وإذا كان المرسل مشرفًا
        if from_user_id in admins:
            # لا يمكن للمشرف الإبلاغ
            return await message.reply_text(
                "يبدو انك مشرف!\nلماذا لا تتصرف انت؟"
                # الترجمة: "عفوًا! يبدو أنك مشرف!\nلا يمكنك الإبلاغ عن أي مستخدمين للمشرفين"
            )

    # إذا كان المرسل مشرفًا (وليس أمر report)
    if from_user_id in admins:
        # قم بمنشن جميع المشرفين
        return await tag_all_admins(client, message)

    # --- القسم التالي خاص بالمستخدمين غير المشرفين ---

    # إذا لم يكن الأمر ردًا ولم يكن هناك نص كافٍ (للإبلاغ، يجب الرد)
    if len(message.text.split()) <= 1 and not message.reply_to_message:
        return await message.reply_text("قم بالرد على رسالته للابلاغ عنه.")
        # الترجمة: "قم بالرد على رسالة للإبلاغ عن ذلك المستخدم."

    # تحديد الرسالة التي سيتم الإبلاغ عنها (إما الرسالة التي تم الرد عليها أو الرسالة الحالية)
    reply = message.reply_to_message or message
    # الحصول على معرف المستخدم الذي أرسل الرسالة المبلغ عنها
    reply_user_id = reply.from_user.id if reply.from_user else reply.sender_chat.id
    # الحصول على معلومات الدردشة المرتبطة (إن وجدت)
    linked_chat = (await client.get_chat(chat_id)).linked_chat

    # منع الإبلاغ عن البوت نفسه
    if reply_user_id == app.id:
        return await message.reply_text("لماذاسابلغ عن نفسي ?")
        # الترجمة: "لماذا أبلغ عن نفسي؟"

    # منع الإبلاغ عن المشرفين أو الدردشة نفسها أو الدردشة المرتبطة
    if (
        reply_user_id in admins
        or reply_user_id == chat_id
        or (linked_chat and reply_user_id == linked_chat.id)
    ):
        return await message.reply_text(
            "المستخدم الذي ترد عليه هو مشرف?"
            # الترجمة: "هل تعلم أن المستخدم الذي ترد عليه هو مشرف؟"
        )

    # إنشاء نص رسالة الإبلاغ
    user_mention = reply.from_user.mention if reply.from_user else "the user" # منشن المستخدم المبلغ عنه
    text = f"Reported {user_mention} to admins!." # النص الأساسي للرسالة
    # الترجمة: "تم الإبلاغ عن {user_mention} للمشرفين!."

    # إضافة منشنات مخفية لجميع المشرفين (غير البوتات وغير المحذوفين)
    for admin in admins:
        admin_member = await client.get_chat_member(chat_id, admin)
        if not admin_member.user.is_bot and not admin_member.user.is_deleted:
            # استخدام الحرف Unicode U+2063 لإنشاء منشن مخفي
            text += f"[\u2063](tg://user?id={admin})"

    # إرسال رسالة الإبلاغ كرد على الرسالة المبلغ عنها
    await reply.reply_text(text)


# معالج الرسائل لأوامر إلغاء عملية المنشن
@app.on_message(
    filters.command(
        [
            "stopmention",
            "cancel",
            "توقف",
            "وقف",
            "mentionoff",
            "cancelall",
        ],
        prefixes=["/", "@", ""],
    )
)
async def cancelcmd(_, message):
    """
    يقوم بإلغاء عملية النداء الجارية في الدردشة.
    يتطلب أن يكون المستخدم الذي استدعى الأمر مشرفًا.
    """
    chat_id = message.chat.id
    # التحقق مما إذا كان المرسل مشرفًا
    admin = await is_admin(chat_id, message.from_user.id)
    if not admin:
        return

    # التحقق مما إذا كانت الدردشة في قائمة العمليات النشطة
    if chat_id in SPAM_CHATS:
        try:
            # إزالة الدردشة من القائمة لإيقاف العملية
            SPAM_CHATS.remove(chat_id)
        except Exception:
            pass
        # إرسال رسالة تأكيد بالإيقاف
        return await message.reply_text("**تم ايقاف عمية النداء!**")
        # الترجمة: "**تم إيقاف عملية المنشن بنجاح!**"
    else:
        # إذا لم تكن هناك عملية جارية
        await message.reply_text("**لا تتم اي عملية نداء!**")
        # الترجمة: "**لا توجد عملية جارية!**"
        return


# تعريف اسم الوحدة (للاستخدام داخل البوت)
__MODULE__ = "Tᴀɢᴀʟʟ"
# نص المساعدة الخاص بهذه الوحدة
__HELP__ = """

@all أو /all | /tagall أو @tagall | /mentionall أو @mentionall [نص] أو [بالرد على رسالة] - لعمل منشن لجميع المستخدمين في مجموعتك بواسطة البوت

/admins | @admins | /report [نص] أو [بالرد على رسالة] - لعمل منشن لجميع المشرفين في مجموعتك أو الإبلاغ عن رسالة لهم


/cancel أو @cancel | /offmention أو @offmention | /mentionoff أو @mentionoff | /cancelall أو @cancelall - لإيقاف أي عملية منشن جارية

**__ملاحظة__** هذه الأوامر يمكن استخدامها فقط بواسطة مشرفي الدردشة وتأكد من أن البوت ومساعده (إذا كان موجودًا) لديهم صلاحيات المشرف في مجموعتك.
"""

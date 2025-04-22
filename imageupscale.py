# -*- coding: utf-8 -*-

import os
import re
import logging
import uuid
import requests

# Pyrogram imports
from pyrogram import filters
from pyrogram.errors.exceptions.bad_request_400 import PhotoInvalidDimensions

# Cloudinary imports
import cloudinary
import cloudinary.uploader
import cloudinary.utils

# Assuming 'app' is your Pyrogram Client instance, imported from your main bot file
# Example: from YukkiMusic import app
# Make sure 'app' is correctly defined and imported in your actual environment
try:
    from YukkiMusic import app
except ImportError:
    # Fallback or error handling if YukkiMusic/app cannot be imported
    # This is just a placeholder, adjust based on your project structure
    logging.error("Failed to import 'app' from 'YukkiMusic'. Please ensure it's correctly defined.")
    # You might want to exit or use a dummy app for testing if needed
    # For this example, we'll let it potentially fail later if app is not available.
    app = None # Or some dummy object if needed for structure validation

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Cloudinary Configuration ---
# !! مهم جداً: قم بتحميل هذه القيم من متغيرات البيئة !!
# !! لا تضع المفاتيح السرية مباشرة في الكود !!
CLOUDINARY_CLOUD_NAME = ("daprtkljw")
CLOUDINARY_API_KEY = ("856836452874295")
CLOUDINARY_API_SECRET = ("9MC9EaM716fJFBVr_WXaZ3rjWtQ")

if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
    logger.warning("Cloudinary credentials are not fully set in environment variables!")
    # Consider preventing the bot from starting or disabling the feature
else:
    logger.info("Configuring Cloudinary...")
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True
    )
    logger.info("Cloudinary configured successfully.")

# --- Upscale Function using Cloudinary ---

def upscale_image_cloudinary(image_bytes: bytes) -> bytes | None:
    """
    يرفع الصورة إلى Cloudinary، ينشئ رابطًا للنسخة المحسنة،
    يقوم بتنزيلها، ثم يعيد بايتات الصورة المحسنة.

    Args:
        image_bytes: بايتات الصورة الأصلية.

    Returns:
        بايتات الصورة المحسنة، أو None في حالة الفشل.
    """
    # التحقق مرة أخرى من أن الإعدادات موجودة
    if not all([cloudinary.config().cloud_name, cloudinary.config().api_key, cloudinary.config().api_secret]):
        logger.error("Cloudinary credentials are not configured.")
        return None

    # إنشاء معرف فريد مؤقت للصورة المرفوعة
    temp_public_id = f"telegram_upscale_temp/{uuid.uuid4()}"
    logger.info(f"Attempting to upload {len(image_bytes)} bytes to Cloudinary with public_id: {temp_public_id}")

    upload_result = None
    try:
        # 1. رفع الصورة مباشرة من البايتات
        upload_result = cloudinary.uploader.upload(
            image_bytes,
            public_id=temp_public_id,
            resource_type="image",
            overwrite=True
        )

        if not upload_result or 'public_id' not in upload_result:
            logger.error(f"Cloudinary upload failed or returned unexpected result: {upload_result}")
            return None

        uploaded_public_id = upload_result['public_id']
        logger.info(f"Image uploaded successfully. Public ID: {uploaded_public_id}")

        # 2. إنشاء رابط للنسخة المحسنة
        upscaled_url, _ = cloudinary.utils.cloudinary_url(
            uploaded_public_id,
            effect="upscale",  # تطبيق تأثير التحسين
            fetch_format="png",# طلب صيغة PNG
            quality="auto"     # جودة تلقائية
        )
        logger.info(f"Generated Cloudinary URL for upscaled image: {upscaled_url}")

        # 3. تنزيل الصورة المحسنة من الرابط
        logger.info(f"Downloading upscaled image from {upscaled_url}...")
        try:
            image_response = requests.get(upscaled_url, timeout=90) # مهلة أطول للملفات الكبيرة
            image_response.raise_for_status()
            upscaled_bytes = image_response.content

            if not upscaled_bytes:
                 logger.error("Downloaded upscaled image is empty.")
                 return None # فشل التنزيل

            logger.info(f"Successfully downloaded {len(upscaled_bytes)} bytes of the upscaled image.")
            return upscaled_bytes

        except requests.exceptions.RequestException as download_err:
            logger.error(f"Failed to download upscaled image from Cloudinary URL: {download_err}", exc_info=True)
            return None # فشل التنزيل

        # 4. (اختياري) حذف الصورة الأصلية المؤقتة من Cloudinary
        # يفضل القيام به لاحقاً كعملية تنظيف منفصلة
        # finally:
        #    if upload_result and 'public_id' in upload_result:
        #        try:
        #            logger.info(f"Attempting to delete temporary upload: {upload_result['public_id']}")
        #            cloudinary.uploader.destroy(upload_result['public_id'], resource_type="image")
        #        except Exception as del_err:
        #            logger.error(f"Failed to delete temporary Cloudinary upload: {del_err}")


    except cloudinary.exceptions.Error as cloud_err:
        logger.error(f"Cloudinary API error occurred: {cloud_err}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred in upscale_image_cloudinary: {e}", exc_info=True)
        return None

    # إذا وصلنا إلى هنا، فربما حدث خطأ غير متوقع قبل إرجاع القيمة بنجاح
    # أو ربما يجب حذف الصورة المؤقتة هنا إذا فشل التنزيل
    finally:
        # يمكنك إضافة منطق حذف الصورة المؤقتة هنا إذا أردت
        # التأكد من الحذف فقط إذا تم الرفع بنجاح ولكن فشل التنزيل أو المعالجة اللاحقة
        pass


# --- Pyrogram Message Handler ---

# تأكد من أن 'app' معرف بشكل صحيح قبل استخدام الديكوريتور
if app:
    @app.on_message(filters.regex(r"^تحسين$") & filters.reply)
    # يمكنك إضافة @utils.capture_err إذا كانت لديك وموثوقة
    async def upscale_reply_image(client, message):
        """تعالج طلب تحسين الصورة عند الرد على صورة بكلمة 'تحسين'."""

        # التحقق الأولي: هل الرسالة رد وهل الرد على صورة؟
        if not message.reply_to_message or not message.reply_to_message.photo:
            logger.warning(f"User {message.from_user.id} sent 'تحسين' without replying to a photo.")
            # يمكنك تغيير رسالة الخطأ هذه للعربية
            return await message.reply_text("⚠️ يجب الرد على صورة بكلمة 'تحسين' لتحسين جودتها.")

        status_message = None # رسالة الحالة التي يتم تحديثها
        photo_path = None     # مسار الصورة الأصلية المؤقتة بعد التنزيل
        output_path = f'upscaled_output_{uuid.uuid4()}.png' # اسم ملف مؤقت فريد للنتيجة

        try:
            user_id = message.from_user.id
            logger.info(f"Upscale request initiated by user {user_id}.")
            status_message = await message.reply_text("⏳ جارٍ تحضير الصورة...")

            # --- 1. تنزيل الصورة الأصلية ---
            logger.info("Downloading original photo from Telegram...")
            # تأكد من وجود مجلد downloads أو استخدم مسارًا آخر
            download_dir = "./downloads/"
            if not os.path.exists(download_dir):
                try:
                    os.makedirs(download_dir)
                except OSError as dir_err:
                     logger.error(f"Failed to create download directory {download_dir}: {dir_err}")
                     await status_message.edit("حدث خطأ في إعداد مجلد التنزيل.")
                     return

            # استخدام معرف فريد للملف المحمل لتجنب التضارب
            photo_dl_filename = os.path.join(download_dir, f"{message.reply_to_message.photo.file_unique_id}.png")
            photo_path = await client.download_media(
                message.reply_to_message.photo.file_id,
                file_name=photo_dl_filename
            )
            if not photo_path or not os.path.exists(photo_path):
                 logger.error("Failed to download photo from Telegram.")
                 await status_message.edit("حدث فشل أثناء تنزيل الصورة من تيليجرام.")
                 return
            logger.info(f"Photo downloaded to: {photo_path}")

            # --- 2. قراءة بايتات الصورة ---
            logger.info("Reading image bytes...")
            with open(photo_path, 'rb') as f:
                image_bytes = f.read()
            logger.info(f"Original image size: {len(image_bytes)} bytes.")

            # --- 3. استدعاء دالة التحسين (باستخدام Cloudinary) ---
            await status_message.edit("☁️ جارٍ التحسين...")
            logger.info("Calling upscale_image_cloudinary function...")
            upscaled_image_bytes = upscale_image_cloudinary(image_bytes)

            # --- 4. التحقق من نتيجة التحسين ---
            if not upscaled_image_bytes:
                logger.error("Upscaling failed (upscale_image_cloudinary returned None).")
                await status_message.edit("حدث فشل أثناء عملية التحسين باستخدام دعوة. قد تكون الخدمة غير متاحة أو أن الصورة غير مدعومة.")
                # التنظيف سيتم في finally
                return
            logger.info(f"Upscaling successful. Received {len(upscaled_image_bytes)} bytes.")
            await status_message.edit("💾 جارٍ حفظ الصورة المحسّنة مؤقتًا...")

            # --- 5. كتابة الملف الناتج مؤقتًا ---
            logger.info(f"Writing upscaled image to temporary file: {output_path}...")
            with open(output_path, 'wb') as f:
                f.write(upscaled_image_bytes)
            logger.info(f"Finished writing output file: {output_path}")

            # --- 6. إرسال النتيجة إلى المستخدم ---
            await status_message.edit("📤 جارٍ إرسال الصورة المحسّنة...")
            send_success = False
            caption_text = " تم تحسين الصورة بواسطة دعوة\n☁️   "
            try:
                logger.info("Attempting to send as photo...")
                await message.reply_photo(photo=output_path, caption=caption_text)
                logger.info("Successfully sent as photo.")
                send_success = True
            except PhotoInvalidDimensions:
                logger.warning("Sending as photo failed (Invalid Dimensions). Attempting to send as document.")
                await status_message.edit("⚠️ أبعاد الصورة غير مدعومة كصورة، جارٍ الإرسال كملف...")
                try:
                    await message.reply_document(document=output_path, caption=caption_text)
                    logger.info("Successfully sent as document.")
                    send_success = True
                except Exception as doc_err:
                    logger.error(f"Failed to send as document: {doc_err}", exc_info=True)
                    await status_message.edit(f"حدث فشل أثناء إرسال الملف: `{doc_err}`")
            except Exception as send_err:
                logger.error(f"Failed to send photo (other error): {send_err}", exc_info=True)
                await status_message.edit(f"حدث فشل أثناء إرسال الصورة: `{send_err}`")

            # --- 7. حذف رسالة الحالة عند النجاح التام ---
            if send_success and status_message:
                try:
                    await status_message.delete()
                    status_message = None # للإشارة إلى أنه تم حذفه
                    logger.info("Status message deleted after successful send.")
                except Exception as del_err:
                     logger.warning(f"Could not delete status message after success: {del_err}")


        except Exception as e:
            # التقاط أي خطأ غير متوقع أثناء العملية بأكملها
            logger.error(f"An unexpected error occurred in upscale_reply_image for user {user_id}: {e}", exc_info=True)
            if status_message:
                try:
                    await status_message.edit(f"حدث خطأ غير متوقع أثناء المعالجة ⚠️. تم تسجيل التفاصيل.")
                except Exception as edit_err:
                     logger.error(f"Failed to edit status message with final error: {edit_err}")

        finally:
            # --- 8. التنظيف (دائما يتم تنفيذه) ---
            logger.info("Performing cleanup...")
            # حذف الملف الناتج المؤقت
            if os.path.exists(output_path):
                logger.info(f"Removing output file: {output_path}")
                try:
                    os.remove(output_path)
                except OSError as rem_err:
                     logger.error(f"Error removing output file {output_path}: {rem_err}")
            # حذف الملف الأصلي المؤقت
            if photo_path and os.path.exists(photo_path):
                logger.info(f"Removing downloaded file: {photo_path}")
                try:
                    os.remove(photo_path)
                except OSError as rem_err:
                     logger.error(f"Error removing downloaded file {photo_path}: {rem_err}")

            # حذف رسالة الحالة إذا لم يتم حذفها عند النجاح
            if status_message:
                 logger.warning("Status message might still exist. Attempting final delete.")
                 try:
                     await status_message.delete()
                     logger.info("Final status message deleted during cleanup.")
                 except Exception as final_del_err:
                     logger.warning(f"Could not delete final status message during cleanup: {final_del_err}")

            logger.info(f"Cleanup finished for user {user_id}.")

# رسالة تأكيد أن الوحدة تم تحميلها (اختياري)
logger.info("Upscale plugin (using Cloudinary) loaded.")


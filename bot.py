import os
import subprocess
import sys
from collections import defaultdict
import imageio_ffmpeg
from pyrogram import Client, filters

# 🎯 الحصول على مسار FFmpeg الثابت والجاهز تلقائياً
FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()

# 🔒 جلب البيانات من متغيرات البيئة (Railway Variables)
try:
    API_ID = int(os.environ.get("API_ID"))
    API_HASH = os.environ.get("API_HASH")
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
except (TypeError, ValueError):
    print("❌ تنبيه أمني: تأكد من إضافة API_ID و API_HASH و BOT_TOKEN في إعدادات Railway.")
    sys.exit(1)

if not all([API_ID, API_HASH, BOT_TOKEN]):
    print("❌ تنبيه أمني: إحدى البيانات الأساسية مفقودة في Railway.")
    sys.exit(1)

app = Client("video_merger_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

USER_VIDEOS = defaultdict(list)


# 1️⃣ استلام المقاطع والصور وحفظها بالترتيب
@app.on_message(filters.private & (filters.video | filters.document | filters.photo))
async def collect_media(client, message):
    user_id = message.from_user.id

    # التأكد من أن المستند أرسل كفيديو لو كان Document
    if message.document and not (message.document.mime_type or "").startswith("video/"):
        return

    msg = await message.reply_text("⏳ جاري حفظ وتجهيز الملف...")

    try:
        # إذا أرسل المستخدم صورة: نحولها إلى مقطع فيديو مدته 3 ثوانٍ
        if message.photo:
            photo_path = await message.download()
            temp_video_path = f"img_video_{message.id}_{user_id}.mp4"
            
            # تحويل الصورة إلى فيديو باستخدام مسار FFMPEG_BIN المضمون
            convert_cmd = [
                FFMPEG_BIN, "-loop", "1", "-i", photo_path,
                "-c:v", "libx264", "-t", "3", "-pix_fmt", "yuv420p",
                "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                temp_video_path, "-y"
            ]
            
            subprocess.run(convert_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            if os.path.exists(photo_path):
                os.remove(photo_path)
                
            file_path = temp_video_path
        else:
            file_path = await message.download()

        USER_VIDEOS[user_id].append(file_path)

        count = len(USER_VIDEOS[user_id])
        await msg.edit_text(
            f"✅ **تمت إضافة العنصر رقم ({count}) بنجاح!**\n\n"
            f"• يمكنك إرسال فيديوهات أو صور.\n"
            f"• أرسل كلمة **دمج** أو أمر `/merge` لجمعها في فيديو واحد.\n"
            f"• أرسل `/clear` للتفريغ."
        )
    except Exception as e:
        await msg.edit_text(f"❌ حدث خطأ أثناء المعالجة: {e}")


# 2️⃣ عملية دمج الملفات معاً
@app.on_message(filters.private & (filters.command(["merge", "دمج"]) | filters.regex(r"^دمج$")))
async def merge_videos(client, message):
    user_id = message.from_user.id
    video_list = USER_VIDEOS.get(user_id, [])

    if not video_list:
        await message.reply_text("❌ لم ترسل أي مقاطع أو صور للدمج بعد!")
        return

    if len(video_list) < 2:
        await message.reply_text("⚠️ يجب إرسال عنصرين على الأقل لدمجهما!")
        return

    msg = await message.reply_text(f"⏳ جاري دمج {len(video_list)} عنصر في فيديو واحد...")

    list_file_path = f"list_{user_id}.txt"
    output_video_path = f"merged_{user_id}.mp4"

    try:
        with open(list_file_path, "w", encoding="utf-8") as f:
            for path in video_list:
                abs_path = os.path.abspath(path).replace("\\", "/")
                f.write(f"file '{abs_path}'\n")

        # أمر FFmpeg للدمج بمسار FFMPEG_BIN المضمون
        command = [
            FFMPEG_BIN, "-f", "concat", "-safe", "0",
            "-i", list_file_path,
            "-c", "copy",
            output_video_path, "-y"
        ]

        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if process.returncode != 0:
            await msg.edit_text("❌ حدث خطأ أثناء الدمج. تأكد من توافق صيغ الملفات.")
            return

        await msg.edit_text("📤 جاري رفع المقطع المدموج النهائي...")

        await client.send_video(
            chat_id=user_id,
            video=output_video_path,
            caption=f"✅ **تم دمج {len(video_list)} عنصر بنجاح!**"
        )

    except Exception as e:
        await msg.edit_text(f"❌ حدث خطأ غير متوقع: {e}")

    finally:
        # 🧹 تنظيف السيرفر
        for path in video_list:
            if os.path.exists(path):
                os.remove(path)

        if os.path.exists(list_file_path):
            os.remove(list_file_path)

        if os.path.exists(output_video_path):
            os.remove(output_video_path)

        USER_VIDEOS[user_id] = []
        await msg.delete()


# 3️⃣ مسح القائمة والتراجع
@app.on_message(filters.private & (filters.command(["clear", "مسح", "إلغاء"]) | filters.regex(r"^(تفريغ|إلغاء|الغاء)$")))
async def clear_videos(client, message):
    user_id = message.from_user.id
    video_list = USER_VIDEOS.get(user_id, [])

    for path in video_list:
        if os.path.exists(path):
            os.remove(path)

    USER_VIDEOS[user_id] = []
    await message.reply_text("🗑️ تم مسح جميع العناصر المحفوظة، يمكنك البدء من جديد!")


if __name__ == "__main__":
    print("🤖 Video & Photo Merger Bot is running...")
    app.run()

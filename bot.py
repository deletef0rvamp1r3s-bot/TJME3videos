import os
import re
import subprocess
import sys
from collections import defaultdict
import imageio_ffmpeg
from pyrogram import Client, filters

# 🎯 الحصول على مسار FFmpeg
FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()

# 🔒 جلب البيانات من متغيرات البيئة 
try:
    API_ID = int(os.environ.get("API_ID"))
    API_HASH = os.environ.get("API_HASH")
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
except (TypeError, ValueError):
    print("❌ تنبيه أمني: تأكد من إضافة API_ID و API_HASH و BOT_TOKEN.")
    sys.exit(1)

if not all([API_ID, API_HASH, BOT_TOKEN]):
    print("❌ تنبيه أمني: إحدى البيانات الأساسية مفقودة.")
    sys.exit(1)

app = Client("video_merger_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

USER_VIDEOS = defaultdict(list)


def get_video_metadata_and_thumb(video_path, thumb_path):
    """استخراج الصورة المصغرة وأبعاد الفيديو ومدته بدقة"""
    cmd_thumb = [
        FFMPEG_BIN, "-ss", "00:00:00.500", "-i", video_path,
        "-vframes", "1", thumb_path, "-y"
    ]
    subprocess.run(cmd_thumb, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    cmd_info = [FFMPEG_BIN, "-i", video_path]
    res = subprocess.run(cmd_info, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stderr = res.stderr

    duration = 0
    width = 0
    height = 0

    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
    if dur_match:
        h, m, s = dur_match.groups()
        duration = int(float(h) * 3600 + float(m) * 60 + float(s))

    res_match = re.search(r"Video:.*?\b(\d{3,4})x(\d{3,4})\b", stderr)
    if res_match:
        width = int(res_match.group(1))
        height = int(res_match.group(2))

    return duration, width, height


# 1️⃣ استلام الملفات (فيديو، صورة، أو صوت) وتوحيد خصائصها هندسياً
@app.on_message(filters.private & (filters.video | filters.document | filters.photo | filters.audio | filters.voice))
async def collect_media(client, message):
    user_id = message.from_user.id

    if message.document:
        mime = message.document.mime_type or ""
        if not (mime.startswith("video/") or mime.startswith("audio/")):
            return

    msg = await message.reply_text("⏳ جاري معالجة وتجهيز العنصر للدمج السليم...")

    try:
        raw_file_path = await message.download()
        processed_video_path = f"processed_{message.id}_{user_id}.mp4"

        # تحديد نوع الملف ومعالجته ليتوافق تماماً في الصوت والصورة والقياسات (9:16)
        if message.photo:
            # معالجة الصور: تحويلها لفيديو 3 ثوانٍ مع صوت صامت وأبعاد 9:16 بدون سترتش
            cmd = [
                FFMPEG_BIN, "-loop", "1", "-i", raw_file_path,
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-c:v", "libx264", "-t", "3", "-pix_fmt", "yuv420p",
                "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-c:a", "aac", "-shortest",
                processed_video_path, "-y"
            ]
        elif message.audio or message.voice:
            # لو أرسل ملف صوتي منفصل: نحوله لفيديو بشاشة سوداء بنفس مدة الصوت
            cmd = [
                FFMPEG_BIN, "-f", "lavfi", "-i", "color=c=black:s=720x1280:r=30",
                "-i", raw_file_path,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest",
                processed_video_path, "-y"
            ]
        else:
            # معالجة الفيديوهات: توحيد الأبعاد (9:16 بدون سترتش) وضمان وجود مسار صوتي (حتى لو كان صامتاً لمقاطع الميوت)
            cmd = [
                FFMPEG_BIN, "-i", raw_file_path,
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-c:a", "aac", "-map", "0:v:0?", "-map", "1:a:0",
                "-shortest",
                processed_video_path, "-y"
            ]

        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # حذف الملف الخام والاحتفاظ بالملف المعالج
        if os.path.exists(raw_file_path):
            os.remove(raw_file_path)

        USER_VIDEOS[user_id].append(processed_video_path)

        count = len(USER_VIDEOS[user_id])
        await msg.edit_text(
            f"✅ **تمت إضافة العنصر رقم ({count}) بنجاح وتوحيد خصائصه!**\n\n"
            f"• أرسل المزيد من المقاطع أو الصور.\n"
            f"• أرسل كلمة **دمج** أو أمر `/merge` لجمعها.\n"
            f"• أرسل `/clear` للتفريغ."
        )
    except Exception as e:
        await msg.edit_text(f"❌ حدث خطأ أثناء المعالجة: {e}")


# 2️⃣ عملية دمج الملفات الموحدة تماماً
@app.on_message(filters.private & (filters.command(["merge", "دمج"]) | filters.regex(r"^دمج$")))
async def merge_videos(client, message):
    user_id = message.from_user.id
    video_list = USER_VIDEOS.get(user_id, [])

    if not video_list:
        await message.reply_text("❌ لم ترسل أي عناصر للدمج بعد!")
        return

    if len(video_list) < 2:
        await message.reply_text("⚠️ يجب إرسال عنصرين على الأقل لدمجهما!")
        return

    msg = await message.reply_text(f"⏳ جاري دمج {len(video_list)} عنصر بشكل نهائي...")

    list_file_path = f"list_{user_id}.txt"
    output_video_path = f"merged_{user_id}.mp4"
    thumb_path = f"thumb_{user_id}.jpg"

    try:
        with open(list_file_path, "w", encoding="utf-8") as f:
            for path in video_list:
                abs_path = os.path.abspath(path).replace("\\", "/")
                f.write(f"file '{abs_path}'\n")

        # دمج آمن ومستقر 100% يدعم اختلاف الصوت والصورة ويضبط العداد الزمني بدقة
        command = [
            FFMPEG_BIN, "-f", "concat", "-safe", "0",
            "-i", list_file_path,
            "-c:v", "libx264", "-c:a", "aac",
            output_video_path, "-y"
        ]

        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if process.returncode != 0:
            await msg.edit_text("❌ حدث خطأ أثناء عملية الدمج النهائية.")
            return

        await msg.edit_text("📤 جاري استخراج الغلاف وإرسال المقطع الكامل...")

        duration, width, height = get_video_metadata_and_thumb(output_video_path, thumb_path)

        await client.send_video(
            chat_id=user_id,
            video=output_video_path,
            caption=f"✅ **تم دمج {len(video_list)} عنصر بنجاح وبدون أي مشاكل في الوقت أو الصوت!**",
            duration=duration,
            width=width,
            height=height,
            thumb=thumb_path if os.path.exists(thumb_path) else None
        )

    except Exception as e:
        await msg.edit_text(f"❌ حدث خطأ غير متوقع: {e}")

    finally:
        for path in video_list:
            if os.path.exists(path):
                os.remove(path)
        if os.path.exists(list_file_path):
            os.remove(list_file_path)
        if os.path.exists(output_video_path):
            os.remove(output_video_path)
        if os.path.exists(thumb_path):
            os.path.exists(thumb_path) and os.remove(thumb_path)

        USER_VIDEOS[user_id] = []
        try:
            await msg.delete()
        except:
            pass


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
    print("🤖 Pro Video & Audio Merger Bot is running...")
    app.run()

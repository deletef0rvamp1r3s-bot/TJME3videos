import os
import json
import subprocess
from pyrogram import Client, filters
from pyrogram.types import Message

# ضع بيانات البوت الخاصة بك هنا
API_ID = 1234567
API_HASH = "YOUR_API_HASH"
BOT_TOKEN = "YOUR_BOT_TOKEN"

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


def get_video_metadata(file_path):
    """استخراج أبعاد الفيديو ومدته بدقة عبر ffprobe"""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration",
        "-of", "json",
        file_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    
    width = int(stream.get("width", 0))
    height = int(stream.get("height", 0))
    duration = int(float(stream.get("duration", 0)))
    
    return width, height, duration


def generate_thumbnail(video_path, thumb_path):
    """إنشاء صورة مصغرة (Thumb) بنفس أبعاد الفيديو لمنع زوم المعاينة"""
    cmd = [
        "ffmpeg", "-y",
        "-ss", "00:00:01",
        "-i", video_path,
        "-vframes", "1",
        "-vf", "scale=320:-1",
        thumb_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@app.on_message(filters.video | filters.document)
async def process_video(client: Client, message: Message):
    status = await message.reply("⏳ جاري تحميل الفيديو...")
    
    input_path = f"input_{message.id}.mp4"
    output_path = f"output_{message.id}.mp4"
    thumb_path = f"thumb_{message.id}.jpg"
    
    try:
        # 1. تنزيل الفيديو الأصلي
        await message.download(file_name=input_path)
        await status.edit("⚙️ جاري معالجة الفيديو...")
        
        # 2. أمر المعالجة بـ FFmpeg (تعديل الأبعاد لتكون زوجية لتفادي أخطاء التشفير)
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", "scale='trunc(iw/2)*2':'trunc(ih/2)*2'",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-crf", "23",
            "-preset", "fast",
            "-c:a", "aac",
            output_path
        ]
        subprocess.run(ffmpeg_cmd, check=True)
        
        # 3. استخراج الأبعاد والمدة بدقة بعد المعالجة
        width, height, duration = get_video_metadata(output_path)
        
        # 4. توليد الصورة المصغرة (Thumbnail)
        generate_thumbnail(output_path, thumb_path)
        
        await status.edit("📤 جاري الرفع إلى تليجرام...")
        
        # 5. إرسال الفيديو مع تمرير (width + height + thumb) لمنع التكبير التلقائي
        await client.send_video(
            chat_id=message.chat.id,
            video=output_path,
            caption="✅ تم التعديل بـ الأبعاد الصحيحة دون زوم!",
            width=width,
            height=height,
            duration=duration,
            thumb=thumb_path if os.path.exists(thumb_path) else None,
            supports_streaming=True
        )
        
        await status.delete()

    except Exception as e:
        await status.edit(f"❌ حدث خطأ أثناء المعالجة: {str(e)}")
        
    finally:
        # تنظيف الملفات المؤقتة من السيرفر
        for path in [input_path, output_path, thumb_path]:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    app.run()

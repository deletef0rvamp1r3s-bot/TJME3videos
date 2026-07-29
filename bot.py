import asyncio
import os
import re
import time
from collections import defaultdict
import imageio_ffmpeg
from pyrogram import Client, filters
from pyrogram.errors import MessageNotModified

# 1️⃣ إعداد البوت
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

app = Client("railway_optimal_merger", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

USER_FILES = defaultdict(list)
PROCESSING_USERS = set()

# 2️⃣ حساب مدة الفيديو بالثواني
async def get_duration_async(file_path):
    cmd = [FFMPEG, "-i", file_path]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    stderr_str = stderr.decode('utf-8', errors='ignore')
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr_str)
    if match:
        hours, minutes, seconds = match.groups()
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
    return 0.0

async def has_audio_async(file_path):
    cmd = [FFMPEG, "-i", file_path]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    return b"Audio:" in stderr

# 3️⃣ تشغيل أمر FFmpeg ومتابعة النسبة المئوية (٪)
async def run_cmd_with_progress(cmd, total_duration, msg, header_text="⏳ جاري المعالجة..."):
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    
    last_update = 0
    pattern = re.compile(r"out_time_ms=(\d+)")
    
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        line_str = line.decode("utf-8", errors="ignore").strip()
        match = pattern.search(line_str)
        
        if match and total_duration > 0:
            current_ms = int(match.group(1))
            current_sec = current_ms / 1000000.0
            percent = min(100, int((current_sec / total_duration) * 100))
            
            # رسم شريط التقدم المرئي
            filled = int(percent / 10)
            bar = "█" * filled + "░" * (10 - filled)
            
            now = time.time()
            # تحديث الرسالة كل 3 ثوانٍ لمنع الحظر من تليجرام
            if now - last_update >= 3:
                last_update = now
                try:
                    await msg.edit_text(f"{header_text}\n\n📊 النسبة: **{percent}%**\n`[{bar}]`")
                except Exception:
                    pass
                    
    await proc.wait()
    return proc.returncode

# 4️⃣ استلام المقاطع ومعالجتها مع عرض النسبة
@app.on_message(filters.private & (filters.video | filters.document | filters.photo | filters.audio | filters.voice))
async def process_media(client, message):
    user = message.from_user.id
    msg = await message.reply_text("📥 جاري تحميل المقطع من تليجرام...")
    
    try:
        dl_path = await message.download()
        out_path = f"vid_{message.id}_{user}.ts"
        vf = "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=60"
        
        duration = await get_duration_async(dl_path)
        if duration == 0:
            duration = 3.0  # افتراضي للصور
            
        if message.photo:
            cmd = [
                FFMPEG, "-y", "-progress", "pipe:1", "-loop", "1", "-t", "3", "-i", dl_path,
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-r", "60", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", "-f", "mpegts", out_path
            ]
        elif message.audio or message.voice:
            cmd = [
                FFMPEG, "-y", "-progress", "pipe:1", "-f", "lavfi", "-i", "color=c=black:s=720x1280:r=60",
                "-i", dl_path, "-vf", "setsar=1,fps=60",
                "-c:v", "libx264", "-preset", "ultraf
ل

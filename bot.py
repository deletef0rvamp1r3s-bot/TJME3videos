import asyncio
import os
import re
import time
from collections import defaultdict
import imageio_ffmpeg
from pyrogram import Client, filters
from pyrogram.errors import MessageNotModified

# 1. Bot Setup
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

app = Client("railway_optimal_merger", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

USER_FILES = defaultdict(list)
USER_LOCKS = defaultdict(asyncio.Lock)
PROCESSING_USERS = set()

async def get_duration_async(file_path):
    cmd = [FFMPEG, "-nostdin", "-i", file_path]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    stderr_str = stderr.decode('utf-8', errors='ignore')
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr_str)
    if match:
        hours, minutes, seconds = match.groups()
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
    return 3.0

async def get_video_dimensions_async(file_path):
    """استخراج أبعاد الفيديو"""
    try:
        cmd = [FFMPEG, "-nostdin", "-i", file_path]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        stderr_str = stderr.decode('utf-8', errors='ignore')
        match = re.search(r"(\d{2,5})\s*x\s*(\d{2,5})", stderr_str)
        if match:
            w, h = int(match.group(1)), int(match.group(2))
            if 100 <= w <= 4000 and 100 <= h <= 4000:
                return w, h
        return 1280, 720
    except:
        return 1280, 720


async def get_intelligent_scale_filter_async(file_path, output_w=720, output_h=1280):
    """فلتر ذكي - بدون حواف جانبية"""
    input_w, input_h = await get_video_dimensions_async(file_path)
    input_aspect = input_w / input_h if input_h > 0 else 1.0
    is_vertical = input_aspect < 0.65
    
    if is_vertical:
        return "scale=720:-1,crop=720:1280:(in_w-out_w)/2:0"
    else:
        return "scale=-1:1280,pad=720:1280:(ow-iw)/2:(oh-ih)/2:black"

async def get_intelligent_scale_filter_async(file_path, output_w=720, output_h=1280):
    """فلتر ذكي محسّن - بدون حواف جانبية للعمودي"""
    input_w, input_h = await get_video_dimensions_async(file_path)
    
    input_aspect = input_w / input_h if input_h > 0 else 1.0
    
    # فحص دقيق: هل الفيديو عمودي؟
    is_vertical = input_aspect < 0.65
    
    if is_vertical:
        # فيديو عمودي: بدون حواف جانبية
        return f"scale={output_w}:-1,crop={output_w}:{output_h}:(in_w-out_w)/2:0"
    else:
        # فيديو أفقي: حواف أعلى وأسفل فقط
        return f"scale=-1:{output_h},pad={output_w}:{output_h}:(ow-iw)/2:(oh-ih)/2:black"

        )
    
    return scale_filter

    else:
        # فيديو أفقي - حواف أعلى وأسفل فقط
        return f"scale=min(iw\\,{output_w}):min(ih\\*{output_w}/iw\\,{output_h}),pad={output_w}:{output_h}:(ow-iw)/2:(oh-ih)/2:black"

async def check_streams_async(file_path):
    cmd = [FFMPEG, "-nostdin", "-i", file_path]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    stderr_str = stderr.decode('utf-8', errors='ignore')
    return "Video:" in stderr_str, "Audio:" in stderr_str

async def run_cmd_async(cmd):
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    return proc.returncode

async def run_cmd_with_progress(cmd, total_duration, msg, header_text="**Processing...**", log_file="ffmpeg_error.log"):
    with open(log_file, "w", encoding="utf-8") as err_file:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=err_file
        )
        
        last_update = 0
        pattern = re.compile(r"out_time_ms=(\d+)")
        
        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=2)
            except asyncio.TimeoutError:
                break
            
            if not line:
                break
                
            line_str = line.decode("utf-8", errors="ignore").strip()
            match = pattern.search(line_str)
            
            if match and total_duration > 0:
                current_ms = int(match.group(1))
                current_sec = current_ms / 1000000.0
                percent = min(100, int((current_sec / total_duration) * 100))
                
                filled = int(percent / 10)
                bar = "█" * filled + "░" * (10 - filled)
                
                now = time.time()
                if now - last_update >= 2:
                    last_update = now
                    try:
                        await msg.edit_text(f"{header_text}\n\n📊 **Progress: {percent}%**\n`[{bar}]`")
                    except:
                        pass
                        
        await proc.wait()
        return proc.returncode

# 2. معالجة الوسائط
@app.on_message(filters.private & (filters.video | filters.document | filters.photo | filters.audio | filters.voice))
async def process_media(client, message):
    user = message.from_user.id
    
    ext = ".mp4"
    if message.video and message.video.file_name:
        _, e = os.path.splitext(message.video.file_name)
        if e: ext = e
    elif message.document and message.document.file_name:
        _, e = os.path.splitext(message.document.file_name)
        if e: ext = e

    dl_name = f"dl_{message.id}_{user}{ext}"
    out_path = f"vid_{message.id}_{user}.mp4"
    log_file = f"ffmpeg_err_{message.id}_{user}.log"

    msg = await message.reply_text("⏳ **Queued for processing...**")
    dl_path = None

    async with USER_LOCKS[user]:
        try:
            await msg.edit_text("📥 **Downloading media...**")
            dl_path = await message.download(file_name=dl_name)
            
            if not dl_path or not os.path.exists(dl_path):
                await msg.edit_text("❌ **Download failed**")
                return

            duration = await get_duration_async(dl_path)
            if duration == 0: duration = 3.0

            W, H = 720, 1280
            scale_filter = await get_intelligent_scale_filter_async(dl_path, W, H)
            
            if message.photo:
                cmd = [
                    FFMPEG, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                    "-loop", "1", "-framerate", "30", "-t", "3", "-i", dl_path,
                    "-f", "lavfi", "-t", "3", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-vf", scale_filter,
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k",
                    "-shortest", out_path
                ]
            else:
                has_v, has_a = await check_streams_async(dl_path)
                
                if has_v and has_a:
                    cmd = [
                        FFMPEG, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                        "-i", dl_path,
                        "-map", "0:v:0", "-map", "0:a:0",
                        "-vf", scale_filter,
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "128k",
                        out_path
                    ]
                elif has_v and not has_a:
                    cmd = [
                        FFMPEG, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                        "-i", dl_path,
                        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-vf", scale_filter,
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "128k",
                        "-shortest", out_path
                    ]
                elif not has_v and has_a:
                    cmd = [
                        FFMPEG, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                        "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r=30",
                        "-i", dl_path,
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "128k",
                        "-shortest", out_path
                    ]
                else:
                    await msg.edit_text("❌ **File format not supported**")
                    return

            returncode = await run_cmd_with_progress(cmd, duration, msg, "⚙️ **Processing...**", log_file)
            
            if returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                USER_FILES[user].append(out_path)
                await msg.edit_text(f"✅ **Added Successfully!**\n📋 **Count: ({len(USER_FILES[user])})**")
            else:
                await msg.edit_text(f"❌ **Processing failed (Code: {returncode})**")
                
        except Exception as e:
            try: 
                await msg.edit_text(f"❌ **Error:** `{str(e)[:80]}`")
            except: 
                pass
        finally:
            if dl_path and os.path.exists(dl_path):
                try: os.remove(dl_path)
                except: pass
            if os.path.exists(log_file):
                try: os.remove(log_file)
                except: pass

# 3. دمج الملفات
@app.on_message(filters.private & filters.command(["merge", "دمج"]))
async def merge_media(client, message):
    user = message.from_user.id
    
    if user in PROCESSING_USERS:
        return await message.reply_text("⏳ **Merge in progress...**")
        
    files = USER_FILES.get(user, [])
    valid_files = [f for f in files if os.path.exists(f)]
    if len(valid_files) < 2:
        USER_FILES[user] = [] 
        return await message.reply_text("❌ **Need at least 2 files!**")
        
    PROCESSING_USERS.add(user)
    msg = await message.reply_text("⏳ **Starting merge...**")
    list_txt = f"list_{user}.txt"
    out_merge = f"final_{user}.mp4"
    thumb_path = f"thumb_{user}.jpg"
    log_file = f"ffmpeg_merge_err_{user}.log"
    
    try:
        total_dur = 0.0
        with open(list_txt, "w", encoding="utf-8") as f:
            for path in valid_files:
                f.write(f"file '{os.path.abspath(path)}'\n")
                total_dur += await get_duration_async(path)
                
        cmd = [
            FFMPEG, "-y", "-nostdin",
            "-progress", "pipe:1",
            "-f", "concat",
            "-safe", "0",
            "-i", list_txt,
            "-c", "copy",
            "-movflags", "+faststart",
            out_merge
        ]
        
        returncode = await run_cmd_with_progress(cmd, total_dur, msg, "🔄 **Merging...**", log_file)
        
        if returncode == 0 and os.path.exists(out_merge) and os.path.getsize(out_merge) > 0:
            await msg.edit_text("📤 **Uploading...**")
            
            thumb_cmd = [
                FFMPEG, "-y", "-nostdin",
                "-ss", "0", "-i", out_merge,
                "-vframes", "1", "-vf", "scale=320:568", thumb_path
            ]
            await run_cmd_async(thumb_cmd)
            
            with open(out_merge, "rb") as f:
                await message.reply_video(
                    f,
                    caption="✅ **Merged Successfully!** 🎬",
                    thumb=open(thumb_path, "rb") if os.path.exists(thumb_path) else None
                )
            
            await msg.edit_text("✅ **Complete!**")
        else:
            await msg.edit_text(f"❌ **Merge failed (Code: {returncode})**")
            
    except Exception as e:
        try:
            await msg.edit_text(f"❌ **Error:** `{str(e)[:80]}`")
        except:
            pass
    finally:
        PROCESSING_USERS.discard(user)
        for f in [list_txt, out_merge, thumb_path, log_file]:
            if os.path.exists(f):
                try: os.remove(f)
                except: pass
        USER_FILES[user] = []

# 4. عرض الملفات
@app.on_message(filters.private & filters.command("show"))
async def show_files(client, message):
    user = message.from_user.id
    files = USER_FILES.get(user, [])
    valid = [f for f in files if os.path.exists(f)]
    
    if not valid:
        return await message.reply_text("❌ **No files**")
    
    text = f"📋 **Files: ({len(valid)})**\n\n"
    for i, f in enumerate(valid, 1):
        size_mb = os.path.getsize(f) / 1024 / 1024
        text += f"{i}. `{os.path.basename(f)}` - {size_mb:.2f} MB\n"
    
    await message.reply_text(text)

# 5. مسح الملفات
@app.on_message(filters.private & filters.command(["clear", "مسح"]))
async def clear_files(client, message):
    user = message.from_user.id
    files = USER_FILES.get(user, [])
    
    for f in files:
        if os.path.exists(f):
            try:
                os.remove(f)
            except:
                pass
    
    USER_FILES[user] = []
    await message.reply_text("✅ **Files cleared!**")

# 6. أمر البداية
@app.on_message(filters.private & filters.command("start"))
async def start_command(client, message):
    await message.reply_text(
        "🎬 **Video Merger Bot**\n\n"
        "**Commands:**\n"
        "• Send videos/photos/audio\n"
        "• `/show` - Show files\n"
        "• `/merge` - Merge all\n"
        "• `/clear` - Clear all\n\n"
        "**Features:**\n"
        "✅ Smart aspect ratio (9:16)\n"
        "✅ Fast encoding\n"
        "✅ Progress bar\n"
        "✅ Auto cleanup"
    )

# 7. تشغيل البوت
if __name__ == "__main__":
    app.run()

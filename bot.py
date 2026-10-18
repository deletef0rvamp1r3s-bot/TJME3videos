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
    return 0.0

async def get_video_dimensions_async(file_path):
    """الحصول على أبعاد الفيديو"""
    cmd = [FFMPEG, "-nostdin", "-i", file_path]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    stderr_str = stderr.decode('utf-8', errors='ignore')
    match = re.search(r"(\d{2,5})\s*x\s*(\d{2,5})", stderr_str)
    if match:
        return int(match.group(1)), int(match.group(2))
    return 1280, 720

async def get_intelligent_scale_filter_async(file_path, output_w=720, output_h=1280):
    """
    معالجة ذكية للحواف:
    - الفيديوهات العمودية (9:16) بدون حواف جانبية
    - الفيديوهات الأفقية بحواف سوداء فقط أعلى وأسفل
    """
    input_w, input_h = await get_video_dimensions_async(file_path)
    input_aspect = input_w / input_h if input_h > 0 else 1.0
    
    # إذا كان الفيديو عمودياً (قريب من 9:16)
    if 0.5 < input_aspect < 0.7:
        # فيديو عمودي - لا نضيف حواف جانبية
        return f"setsar=1,scale={output_w}:{output_h}:force_original_aspect_ratio=decrease,pad={output_w}:{output_h}:(ow-iw)/2:(oh-ih)/2:black,setdar=9/16,fps=30"
    else:
        # فيديو أفقي أو مربع - أضف حواف فقط أعلى وأسفل
        intermediate_h = int(output_w / input_aspect)
        return f"setsar=1,scale={output_w}:{intermediate_h}:force_original_aspect_ratio=decrease,pad={output_w}:{output_h}:(ow-iw)/2:(oh-ih)/2:black,setdar=9/16,fps=30"

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
            line = await proc.stdout.readline()
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
                if now - last_update >= 3:
                    last_update = now
                    try:
                        await msg.edit_text(f"{header_text}\n\n📊 **Progress: {percent}%**\n`[{bar}]`")
                    except Exception:
                        pass
                        
        await proc.wait()
        return proc.returncode

# 2. Process media
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
            await msg.edit_text("📥 **Downloading media from Telegram...**")
            
            dl_path = await message.download(file_name=dl_name)
            
            if not dl_path or not os.path.exists(dl_path):
                await msg.edit_text("❌ **Download failed: File not found.**")
                return

            duration = await get_duration_async(dl_path)
            if duration == 0:
                duration = 3.0
                
            W, H = 720, 1280

            scale_filter = await get_intelligent_scale_filter_async(dl_path, W, H)
                
            if message.photo:
                cmd = [
                    FFMPEG, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                    "-loop", "1", "-framerate", "30", "-t", "3", "-i", dl_path,
                    "-f", "lavfi", "-t", "3", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-vf", scale_filter,
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-r", "30", "-video_track_timescale", "90000",
                    "-max_muxing_queue_size", "1024",
                    "-c:a", "aac", "-ar", "44100", "-ac", "2", 
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
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-r", "30", "-video_track_timescale", "90000",
                        "-max_muxing_queue_size", "1024",
                        "-c:a", "aac", "-ar", "44100", "-ac", "2",
                        out_path
                    ]
                elif has_v and not has_a:
                    cmd = [
                        FFMPEG, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                        "-i", dl_path,
                        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-vf", scale_filter,
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-r", "30", "-video_track_timescale", "90000",
                        "-max_muxing_queue_size", "1024",
                        "-c:a", "aac", "-ar", "44100", "-ac", "2",
                        "-shortest", out_path
                    ]
                elif not has_v and has_a:
                    cmd = [
                        FFMPEG, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                        "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r=30",
                        "-i", dl_path,
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-vf", "setdar=9/16",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-r", "30", "-video_track_timescale", "90000",
                        "-max_muxing_queue_size", "1024",
                        "-c:a", "aac", "-ar", "44100", "-ac", "2",
                        "-shortest", out_path
                    ]
                else:
                    await msg.edit_text("❌ **الملف غير مدعوم أو لا يحتوي على مسار فيديو/صوت صالح.**")
                    return

            returncode = await run_cmd_with_progress(cmd, duration, msg, "⚙️ **Processing Media...**", log_file)
            
            if returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                USER_FILES[user].append(out_path)
                await msg.edit_text(f"✅ **Media Added Successfully!**\n📋 **List Count: ({len(USER_FILES[user])})**\n\n• Send /show to view\n• Send /merge to merge\n• Send /clear to clear")
            else:
                err_msg = f"❌ **Error processing this file. (Exit Code: {returncode})**"
                await msg.edit_text(err_msg)
                
        except Exception as e:
            try: 
                await msg.edit_text(f"❌ **Download or processing failed.**\n`{e}`")
            except MessageNotModified: 
                pass
        finally:
            if dl_path and isinstance(dl_path, str) and os.path.exists(dl_path):
                try: os.remove(dl_path)
                except Exception: pass
            if os.path.exists(log_file):
                try: os.remove(log_file)
                except Exception: pass

# 3. Merge Output
@app.on_message(filters.private & filters.command(["merge", "دمج"]))
async def merge_media(client, message):
    user = message.from_user.id
    
    if user in PROCESSING_USERS:
        return await message.reply_text("⏳ **Merge is already in progress, please wait...**")
        
    files = USER_FILES.get(user, [])
    valid_files = [f for f in files if os.path.exists(f)]
    if len(valid_files) < 2:
        USER_FILES[user] = [] 
        return await message.reply_text("❌ **Need at least 2 files to merge!**")
        
    PROCESSING_USERS.add(user)
    msg = await message.reply_text("⏳ **Starting lightning fast merge...**")
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
        
        returncode = await run_cmd_with_progress(cmd, total_dur, msg, "🔄 **Merging Clips instantly...**", log_file)
        
        if returncode == 0 and os.path.exists(out_merge) and os.path.getsize(out_merge) > 0:
            await msg.edit_text("📤 **Merge Complete! Uploading...**")
            
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
            
            await msg.edit_text("✅ **Upload Complete!**\n\n• Send /clear to clear files\n• Send /merge to merge more")
        else:
            await msg.edit_text(f"❌ **Merge failed. (Exit Code: {returncode})**")
            
    except Exception as e:
        try:
            await msg.edit_text(f"❌ **Merge failed.**\n`{str(e)[:100]}`")
        except MessageNotModified:
            pass
    finally:
        PROCESSING_USERS.discard(user)
        for f in [list_txt, out_merge, thumb_path, log_file]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass
        USER_FILES[user] = []

# 4. Show Files
@app.on_message(filters.private & filters.command("show"))
async def show_files(client, message):
    user = message.from_user.id
    files = USER_FILES.get(user, [])
    valid = [f for f in files if os.path.exists(f)]
    
    if not valid:
        return await message.reply_text("❌ **No files in queue.**")
    
    text = f"📋 **Files in Queue: ({len(valid)})**\n\n"
    for i, f in enumerate(valid, 1):
        size_mb = os.path.getsize(f) / 1024 / 1024
        text += f"{i}. `{os.path.basename(f)}` - {size_mb:.2f} MB\n"
    
    await message.reply_text(text)

# 5. Clear Files
@app.on_message(filters.private & filters.command(["clear", "مسح"]))
async def clear_files(client, message):
    user = message.from_user.id
    files = USER_FILES.get(user, [])
    
    for f in files:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass
    
    USER_FILES[user] = []
    await message.reply_text("✅ **All files cleared!**")

# 6. Help Command
@app.on_message(filters.private & filters.command("start"))
async def start_command(client, message):
    await message.reply_text(
        "🎬 **Video Merger Bot**\n\n"
        "**Commands:**\n"
        "• Send videos/photos/audio/voice\n"
        "• `/show` - Show queued files\n"
        "• `/merge` - Merge all files\n"
        "• `/clear` - Clear all files\n\n"
        "**Features:**\n"
        "✅ Smart aspect ratio handling\n"
        "✅ 720x1280 (9:16) Output\n"
        "✅ Fast encoding (ultrafast)\n"
        "✅ Progress tracking\n"
        "✅ Per-user queue protection"
    )

# 7. Run Bot
if __name__ == "__main__":
    app.run()

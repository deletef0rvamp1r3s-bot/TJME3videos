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

async def has_audio_async(file_path):
    cmd = [FFMPEG, "-nostdin", "-i", file_path]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    return b"Audio:" in stderr

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
    msg = await message.reply_text("📥 **Downloading media from Telegram...**")
    
    try:
        dl_path = await message.download()
        out_path = f"vid_{message.id}_{user}.mp4"
        log_file = f"ffmpeg_err_{user}.log"
        
        duration = await get_duration_async(dl_path)
        if duration == 0:
            duration = 3.0
            
        scale_filter = "scale=720:1560:force_original_aspect_ratio=decrease,crop='trunc(iw/2)*2':'trunc(ih/2)*2',pad=720:1560:-1:-1:color=black,fps=30,setsar=1"
            
        if message.photo:
            cmd = [
                FFMPEG, "-y", "-nostdin", "-threads", "1", "-nostats", "-progress", "pipe:1", "-loop", "1", "-t", "3", "-i", dl_path,
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-vf", scale_filter,
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", 
                "-max_muxing_queue_size", "1024", "-shortest", "-video_track_timescale", "90000", out_path
            ]
        elif message.audio or message.voice:
            cmd = [
                FFMPEG, "-y", "-nostdin", "-threads", "1", "-nostats", "-progress", "pipe:1", "-fflags", "+genpts", 
                "-f", "lavfi", "-i", "color=c=black:s=720x1560:r=30",
                "-i", dl_path,
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "44100", "-ac", "2",
                "-max_muxing_queue_size", "1024", "-shortest", "-video_track_timescale", "90000", out_path
            ]
        else:
            has_a = await has_audio_async(dl_path)
            if has_a:
                cmd = [
                    FFMPEG, "-y", "-nostdin", "-threads", "1", "-nostats", "-progress", "pipe:1", "-fflags", "+genpts", "-i", dl_path,
                    "-t", str(duration + 0.5),
                    "-vf", scale_filter,
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", 
                    "-c:a", "aac", "-ar", "44100", "-ac", "2",
                    "-max_muxing_queue_size", "1024", "-video_track_timescale", "90000", out_path
                ]
            else:
                cmd = [
                    FFMPEG, "-y", "-nostdin", "-threads", "1", "-nostats", "-progress", "pipe:1", "-fflags", "+genpts", "-i", dl_path,
                    "-t", str(duration + 0.5),
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-vf", scale_filter,
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "44100", "-ac", "2",
                    "-max_muxing_queue_size", "1024", "-shortest", "-video_track_timescale", "90000", out_path
                ]

        returncode = await run_cmd_with_progress(cmd, duration, msg, "⚙️ **Processing Media...**", log_file)
        
        if os.path.exists(dl_path): 
            os.remove(dl_path)
        
        if returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            USER_FILES[user].append(out_path)
            await msg.edit_text(f"✅ **Media Added Successfully!**\n📋 **List Count: ({len(USER_FILES[user])})**\n\n• Send /show to view\n• Send /merge to merge\n• Send /clear to clear")
            if os.path.exists(log_file): os.remove(log_file)
        else:
            err_msg = f"❌ **Error processing this file. (Exit Code: {returncode})**"
            
            if returncode == -9 or returncode == -137:
                err_msg += "\n\n⚠️ **CRASH DIAGNOSIS:** `Exit Code -9` means Railway forcefully killed FFmpeg because your container ran out of RAM (Out of Memory). You either need to upgrade your Railway RAM, or process smaller/shorter videos."
                
            if os.path.exists(log_file):
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    lines = [line.strip() for line in f.readlines() if line.strip() and not line.startswith("frame=")]
                    err_txt = '\n'.join(lines[-15:])
                err_msg += f"\n\n**FFmpeg Log:**\n`{err_txt}`"
                os.remove(log_file)
            await msg.edit_text(err_msg)
            
    except Exception as e:
        try: 
            await msg.edit_text(f"❌ **Download or processing failed.**\n`{e}`")
        except MessageNotModified: 
            pass

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
        return await message.reply_text("❌ **Server deleted your files due to inactivity. Please re-upload and merge without waiting too long!**")
        
    PROCESSING_USERS.add(user)
    msg = await message.reply_text("⏳ **Calculating duration & starting merge...**")
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
            FFMPEG, "-y", "-nostdin", "-threads", "1", "-nostats",
            "-progress", "pipe:1",
            "-f", "concat",
            "-safe", "0",
            "-i", list_txt,
            "-c", "copy",  # التعديل السحري لنسخ البيانات بدون استهلاك للرامات
            "-movflags", "+faststart",
            out_merge
        ]
        
        returncode = await run_cmd_with_progress(cmd, total_dur, msg, "🔄 **Merging Clips Perfectly...**", log_file)
        
        if returncode == 0 and os.path.exists(out_merge) and os.path.getsize(out_merge) > 0:
            await msg.edit_text("📤 **Merge Complete! Generating Thumbnail...**")
            
            thumb_cmd = [
                FFMPEG, "-y", "-nostdin", "-i", out_merge, "-ss", "00:00:00.500", "-vframes", "1",
                "-vf", "scale=320:320:force_original_aspect_ratio=decrease", 
                thumb_path
            ]
            await run_cmd_async(thumb_cmd)
            
            await msg.edit_text("📤 **Uploading video to Telegram...**")
            
            final_duration = await get_duration_async(out_merge)
            
            try:
                kwargs = {
                    "chat_id": user,
                    "video": out_merge,
                    "caption": "✅ **Here is your merged video!**",
                    "width": 720,
                    "height": 1560,
                    "duration": int(final_duration)
                }
                if os.path.exists(thumb_path):
                    kwargs["thumb"] = thumb_path

                await client.send_video(**kwargs)
                
                for path in valid_files:
                    if os.path.exists(path): os.remove(path)
                USER_FILES[user] = []
                await msg.delete()
                
            except Exception as e:
                await msg.edit_text(f"❌ **Upload failed.**\nError: `{e}`")
        else:
            err_msg = f"❌ **Final merge failed. (Exit Code: {returncode})**"
            
            if returncode == -9 or returncode == -137:
                err_msg += "\n\n⚠️ **CRASH DIAGNOSIS:** `Exit Code -9` means Out of Memory during merge."
                
            if os.path.exists(log_file):
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    lines = [line.strip() for line in f.readlines() if line.strip() and not line.startswith("frame=")]
                    err_txt = '\n'.join(lines[-15:])
                err_msg += f"\n\n**Log:**\n`{err_txt}`"
                os.remove(log_file)
            await msg.edit_text(err_msg)
            
    except Exception as e:
        try: 
            await msg.edit_text(f"❌ **An unexpected error occurred during merge:**\n`{e}`")
        except MessageNotModified: 
            pass
    finally:
        if os.path.exists(list_txt): os.remove(list_txt)
        if os.path.exists(out_merge): os.remove(out_merge)
        if os.path.exists(thumb_path): os.remove(thumb_path)
        if os.path.exists(log_file): os.remove(log_file)
        PROCESSING_USERS.discard(user)

@app.on_message(filters.private & filters.command(["show", "عرض"]))
async def show_media(client, message):
    user = message.from_user.id
    files = USER_FILES.get(user, [])
    valid_files = [f for f in files if os.path.exists(f)]
    count = len(valid_files)
    
    if count == 0:
        USER_FILES[user] = []
        await message.reply_text("📭 **Your list is currently empty.**")
    else:
        await message.reply_text(f"📋 **Current List Status:**\n• **Clips Ready:** **({count})**\n\n• Send /merge to merge\n• Send /clear to clear")

@app.on_message(filters.private & filters.command(["clear", "مسح"]))
async def clear_media(client, message):
    user = message.from_user.id
    for path in USER_FILES.get(user, []):
        if os.path.exists(path): 
            os.remove(path)
    USER_FILES[user] = []
    await message.reply_text("🗑️ **List cleared and memory freed successfully.**")

if __name__ == "__main__":
    print("🤖 Bot is running smoothly on Railway...")
    app.run()

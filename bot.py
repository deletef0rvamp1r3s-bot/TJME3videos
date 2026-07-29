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

async def run_cmd_with_progress(cmd, total_duration, msg, header_text="**Processing...**"):
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

@app.on_message(filters.private & (filters.video | filters.document | filters.photo | filters.audio | filters.voice))
async def process_media(client, message):
    user = message.from_user.id
    msg = await message.reply_text("📥 **Downloading media from Telegram...**")
    
    try:
        dl_path = await message.download()
        # Change to .mp4 for better timestamp handling during concatenation
        out_path = f"vid_{message.id}_{user}.mp4"
        vf = "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=60"
        
        duration = await get_duration_async(dl_path)
        if duration == 0:
            duration = 3.0
            
        # Standardizing all outputs to exact same timebase (90000) for seamless merging
        if message.photo:
            cmd = [
                FFMPEG, "-y", "-progress", "pipe:1", "-loop", "1", "-t", "3", "-i", dl_path,
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-r", "60", "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", out_path
            ]
        elif message.audio or message.voice:
            cmd = [
                FFMPEG, "-y", "-progress", "pipe:1", "-f", "lavfi", "-i", "color=c=black:s=720x1280:r=60",
                "-i", dl_path, "-vf", "setsar=1,fps=60",
                "-c:v", "libx264", "-preset", "ultrafast", "-r", "60", "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", out_path
            ]
        else:
            has_a = await has_audio_async(dl_path)
            if has_a:
                cmd = [
                    FFMPEG, "-y", "-progress", "pipe:1", "-err_detect", "ignore_err", "-i", dl_path,
                    "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-r", "60", "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
                    "-c:a", "aac", "-ar", "44100", "-ac", "2", out_path
                ]
            else:
                cmd = [
                    FFMPEG, "-y", "-progress", "pipe:1", "-err_detect", "ignore_err", "-i", dl_path,
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-r", "60", "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
                    "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", out_path
                ]

        returncode = await run_cmd_with_progress(cmd, duration, msg, "⚙️ **Processing & Preparing (60 FPS)...**")
        
        if os.path.exists(dl_path): 
            os.remove(dl_path)
        
        if returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            USER_FILES[user].append(out_path)
            await msg.edit_text(f"✅ **Media Added Successfully!**\n📋 **List Count: ({len(USER_FILES[user])})**\n\n• Send /show to view\n• Send /merge to merge\n• Send /clear to clear")
        else:
            await msg.edit_text("❌ **Error processing this file. Try another.**")
            
    except Exception:
        try: 
            await msg.edit_text("❌ **Download Failed.**")
        except MessageNotModified: 
            pass

@app.on_message(filters.private & filters.command(["merge", "دمج"]))
async def merge_media(client, message):
    user = message.from_user.id
    
    if user in PROCESSING_USERS:
        return await message.reply_text("⏳ **Merge is already in progress, please wait...**")
        
    files = USER_FILES.get(user, [])
    
    # 🚨 SECURITY CHECK: Ensure files weren't deleted by Railway sleep/cleanup
    valid_files = [f for f in files if os.path.exists(f)]
    if len(valid_files) < 2:
        USER_FILES[user] = [] # Clear the broken list
        return await message.reply_text("❌ **Server deleted your files due to inactivity. Please re-upload and merge without waiting too long!**")
        
    PROCESSING_USERS.add(user)
    msg = await message.reply_text("⏳ **Calculating duration & starting merge...**")
    list_txt = f"list_{user}.txt"
    out_merge = f"final_{user}.mp4"
    
    try:
        total_dur = 0.0
        with open(list_txt, "w") as f:
            for path in valid_files:
                f.write(f"file '{os.path.abspath(path)}'\n")
                total_dur += await get_duration_async(path)
                
        # Fast copy concat without re-encoding to prevent Railway Out-Of-Memory crash
        cmd = [
            FFMPEG, "-y",
            "-progress", "pipe:1",
            "-f", "concat",
            "-safe", "0",
            "-i", list_txt,
            "-c", "copy",
            out_merge
        ]
        
        returncode = await run_cmd_with_progress(cmd, total_dur, msg, "🔄 **Merging Full Clips Instantly...**")
        
        if returncode == 0 and os.path.exists(out_merge) and os.path.getsize(out_merge) > 0:
            await msg.edit_text("📤 **Merge 100% Complete! Uploading full video to Telegram...**")
            
            try:
                await client.send_video(chat_id=user, video=out_merge, caption="✅ **Here is your full merged video! (60 FPS)**")
                
                for path in valid_files:
                    if os.path.exists(path): 
                        os.remove(path)
                USER_FILES[user] = []
                await msg.delete()
                return
            
            except Exception:
                await msg.edit_text("❌ **Upload failed (File might be too large). Your clips are still saved in the list.**")
        else:
            await msg.edit_text("❌ **Final merge failed due to engine error.**")
            
    except Exception:
        try: 
            await msg.edit_text("❌ **An unexpected error occurred during merge.**")
        except MessageNotModified: 
            pass
    finally:
        if os.path.exists(list_txt): 
            os.remove(list_txt)
        if os.path.exists(out_merge): 
            os.remove(out_merge)
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

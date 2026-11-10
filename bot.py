import asyncio
import os
import re
import sys
import time
import threading
from collections import defaultdict

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

import imageio_ffmpeg
from pyrogram import Client, filters
from pyrogram.errors import MessageNotModified

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

if not (API_ID and API_HASH and BOT_TOKEN):
    raise ValueError("⚠️ متغيرات البيئة ناقصة")

app = Client("railway_optimal_merger", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

USER_FILES = defaultdict(lambda: {"files": [], "timestamp": time.time()})
USER_LOCKS = defaultdict(asyncio.Lock)
PROCESSING_USERS = set()


def cleanup_old_files_sync():
    while True:
        time.sleep(600)
        try:
            current_time = time.time()
            users_to_delete = []
            for user_id, data in USER_FILES.items():
                if current_time - data["timestamp"] > 1800:
                    for file_path in data["files"]:
                        if os.path.exists(file_path):
                            try: os.remove(file_path)
                            except Exception: pass
                    users_to_delete.append(user_id)
            for user_id in users_to_delete:
                del USER_FILES[user_id]
                print(f"🗑️ تنظيف {user_id}", flush=True)
        except Exception as e:
            print(f"❌ cleanup error: {e}", flush=True)


def update_user_timestamp(user_id):
    USER_FILES[user_id]["timestamp"] = time.time()


async def detect_dimensions(file_path):
    cmd = [FFMPEG, "-nostdin", "-i", file_path]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    stderr_str = stderr.decode('utf-8', errors='ignore')
    match = re.search(r"Video:.*?(\d+)x(\d+)", stderr_str)
    if match:
        return int(match.group(1)), int(match.group(2))
    return 1280, 720


async def get_smart_filter(file_path):
    W, H = await detect_dimensions(file_path)
    aspect = W / H if H > 0 else 1.0
    print(f"📐 الأبعاد: {W}x{H} | Aspect: {aspect:.2f}", flush=True)
    
    if aspect < 0.65:
        return "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1"
    else:
        return "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:black,setsar=1"


async def get_duration_async(file_path):
    cmd = [FFMPEG, "-nostdin", "-i", file_path]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    stderr_str = stderr.decode('utf-8', errors='ignore')
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr_str)
    if match:
        h, m, s = match.groups()
        return float(h) * 3600 + float(m) * 60 + float(s)
    return 0.0


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
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode('utf-8', errors='ignore')[-500:]
        print(f"❌ FFMPEG Error: {err}", flush=True)
    return proc.returncode


async def run_cmd_with_progress(cmd, total_duration, msg, header_text="**Processing...**"):
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    
    last_update = 0
    pattern = re.compile(r"out_time_ms=(\d+)")
    stderr_lines = []
    
    async def read_stderr():
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            stderr_lines.append(line.decode('utf-8', errors='ignore'))
    
    stderr_task = asyncio.create_task(read_stderr())
    
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        line_str = line.decode("utf-8", errors="ignore").strip()
        match = pattern.search(line_str)
        
        if match and total_duration > 0:
            current_sec = int(match.group(1)) / 1000000.0
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
    await stderr_task
    
    if proc.returncode != 0:
        err = ''.join(stderr_lines[-5:])
        print(f"❌ FFMPEG Error: {err}", flush=True)
    
    return proc.returncode


# ✅ إعدادات الجودة الجديدة - سموذ + حجم معقول
VIDEO_SETTINGS = [
    "-c:v", "libx264",
    "-preset", "veryfast",
    "-crf", "20",
    "-profile:v", "high",
    "-level", "4.0",
    "-pix_fmt", "yuv420p",
    "-r", "30",
    "-maxrate", "4M",
    "-bufsize", "8M",
    "-max_muxing_queue_size", "1024"
]


@app.on_message(filters.private & (filters.video | filters.document | filters.photo | filters.audio | filters.voice))
async def process_media(client, message):
    user = message.from_user.id
    update_user_timestamp(user)
    
    ext = ".mp4"
    if message.video and message.video.file_name:
        _, e = os.path.splitext(message.video.file_name)
        if e: ext = e
    elif message.document and message.document.file_name:
        _, e = os.path.splitext(message.document.file_name)
        if e: ext = e

    dl_name = f"dl_{message.id}_{user}{ext}"
    out_path = f"vid_{message.id}_{user}.mp4"
    dl_path = None

    msg = await message.reply_text("⏳ **جاري المعالجة...**")

    async with USER_LOCKS[user]:
        try:
            await msg.edit_text("📥 **تحميل...**")
            dl_path = await message.download(file_name=dl_name)
            
            if not dl_path or not os.path.exists(dl_path):
                await msg.edit_text("❌ **فشل التحميل.**")
                return

            duration = await get_duration_async(dl_path) or 3.0
            smart_filter = await get_smart_filter(dl_path)
            scale_filter = f"{smart_filter},setdar=9/16,fps=30"
            
            if message.photo:
                cmd = [
                    FFMPEG, "-y", "-nostdin", "-progress", "pipe:1",
                    "-loop", "1", "-framerate", "30", "-t", "3", "-i", dl_path,
                    "-f", "lavfi", "-t", "3", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-vf", scale_filter,
                    *VIDEO_SETTINGS,
                    "-c:a", "aac", "-ar", "44100", "-ac", "2",
                    "-shortest", out_path
                ]
            else:
                has_v, has_a = await check_streams_async(dl_path)
                
                if has_v and has_a:
                    cmd = [
                        FFMPEG, "-y", "-nostdin", "-progress", "pipe:1",
                        "-i", dl_path,
                        "-map", "0:v:0", "-map", "0:a:0",
                        "-vf", scale_filter,
                        *VIDEO_SETTINGS,
                        "-c:a", "aac", "-ar", "44100", "-ac", "2",
                        out_path
                    ]
                elif has_v and not has_a:
                    cmd = [
                        FFMPEG, "-y", "-nostdin", "-progress", "pipe:1",
                        "-i", dl_path,
                        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-vf", scale_filter,
                        *VIDEO_SETTINGS,
                        "-c:a", "aac", "-ar", "44100", "-ac", "2",
                        "-shortest", out_path
                    ]
                elif not has_v and has_a:
                    cmd = [
                        FFMPEG, "-y", "-nostdin", "-progress", "pipe:1",
                        "-f", "lavfi", "-i", "color=c=black:s=720x1280:r=30",
                        "-i", dl_path,
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-vf", "setdar=9/16,fps=30",
                        *VIDEO_SETTINGS,
                        "-c:a", "aac", "-ar", "44100", "-ac", "2",
                        "-shortest", out_path
                    ]
                else:
                    await msg.edit_text("❌ **ملف غير مدعوم.**")
                    return

            returncode = await run_cmd_with_progress(cmd, duration, msg, "⚙️ **معالجة...**")
            
            if returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                USER_FILES[user]["files"].append(out_path)
                await msg.edit_text(f"✅ **تم!**\n📋 القائمة: ({len(USER_FILES[user]['files'])})\n\n• /show\n• /merge\n• /clear")
            else:
                await msg.edit_text(f"❌ **خطأ. Exit: {returncode}**")
                
        except Exception as e:
            print(f"❌ process_media error: {e}", flush=True)
            try: await msg.edit_text("❌ **خطأ.**")
            except: pass
        finally:
            if dl_path and os.path.exists(dl_path):
                try: os.remove(dl_path)
                except: pass


@app.on_message(filters.private & filters.animation)
async def process_gif(client, message):
    user = message.from_user.id
    update_user_timestamp(user)
    
    dl_name = f"gif_{message.id}_{user}.gif"
    out_path = f"vid_{message.id}_{user}.mp4"
    dl_path = None

    msg = await message.reply_text("⏳ **معالجة GIF...**")

    async with USER_LOCKS[user]:
        try:
            await msg.edit_text("📥 **تحميل...**")
            dl_path = await message.download(file_name=dl_name)
            
            if not dl_path or not os.path.exists(dl_path):
                await msg.edit_text("❌ **فشل التحميل.**")
                return

            has_v, has_a = await check_streams_async(dl_path)
            if not has_v:
                await msg.edit_text("❌ **ملف GIF غير صالح.**")
                return

            duration = await get_duration_async(dl_path)
            if duration < 1.0:
                duration = 3.0
            
            print(f"🎞️ GIF Duration: {duration}s", flush=True)
            
            smart_filter = await get_smart_filter(dl_path)
            scale_filter = f"{smart_filter},setdar=9/16,fps=30"
            
            cmd = [
                FFMPEG, "-y", "-nostdin",
                "-i", dl_path,
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-map", "0:v:0", "-map", "1:a:0",
                "-vf", scale_filter,
                *VIDEO_SETTINGS,
                "-c:a", "aac", "-ar", "44100", "-ac", "2",
                "-shortest", "-t", str(duration),
                "-f", "mp4", out_path
            ]
            
            returncode = await run_cmd_async(cmd)
            
            if returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                USER_FILES[user]["files"].append(out_path)
                await msg.edit_text(f"✅ **تم!**\n📋 القائمة: ({len(USER_FILES[user]['files'])})")
            else:
                await msg.edit_text(f"❌ **خطأ. Exit: {returncode}**")
                
        except Exception as e:
            print(f"❌ process_gif error: {e}", flush=True)
            try: await msg.edit_text("❌ **خطأ.**")
            except: pass
        finally:
            if dl_path and os.path.exists(dl_path):
                try: os.remove(dl_path)
                except: pass


@app.on_message(filters.private & filters.command(["merge", "دمج"]))
async def merge_media(client, message):
    user = message.from_user.id
    update_user_timestamp(user)
    
    if user in PROCESSING_USERS:
        return await message.reply_text("⏳ **جاري الدمج...**")
        
    files = USER_FILES.get(user, {}).get("files", [])
    valid_files = [f for f in files if os.path.exists(f)]
    if len(valid_files) < 2:
        USER_FILES[user]["files"] = []
        return await message.reply_text("❌ **نحتاج 2 مقطع على الأقل!**")
        
    PROCESSING_USERS.add(user)
    msg = await message.reply_text("⏳ **بدء الدمج...**")
    list_txt = f"list_{user}.txt"
    out_merge = f"final_{user}.mp4"
    thumb_path = f"thumb_{user}.jpg"
    
    try:
        total_dur = 0.0
        with open(list_txt, "w", encoding="utf-8") as f:
            for path in valid_files:
                f.write(f"file '{os.path.abspath(path)}'\n")
                total_dur += await get_duration_async(path)
                
        cmd = [
            FFMPEG, "-y", "-nostdin", "-progress", "pipe:1",
            "-f", "concat", "-safe", "0", "-i", list_txt,
            "-c", "copy", "-movflags", "+faststart", out_merge
        ]
        
        returncode = await run_cmd_with_progress(cmd, total_dur, msg, "🔄 **دمج...**")
        
        if returncode == 0 and os.path.exists(out_merge) and os.path.getsize(out_merge) > 0:
            await msg.edit_text("📤 **جاري الرفع...**")
            
            thumb_cmd = [
                FFMPEG, "-y", "-nostdin", "-i", out_merge,
                "-ss", "00:00:00.500", "-vframes", "1",
                "-vf", "scale=360:640:force_original_aspect_ratio=decrease,pad=360:640:(ow-iw)/2:(oh-ih)/2:black",
                thumb_path
            ]
            await run_cmd_async(thumb_cmd)
            
            final_duration = await get_duration_async(out_merge)
            
            kwargs = {
                "chat_id": user,
                "video": out_merge,
                "caption": "✅ **جاهز!**",
                "duration": int(final_duration),
                "width": 720,
                "height": 1280
            }
            if os.path.exists(thumb_path):
                kwargs["thumb"] = thumb_path

            await client.send_video(**kwargs, supports_streaming=True)
            
            for path in valid_files:
                if os.path.exists(path):
                    try: os.remove(path)
                    except: pass
            USER_FILES[user]["files"] = []
            await msg.delete()
            
        else:
            await msg.edit_text(f"❌ **خطأ الدمج. Exit: {returncode}**")
            
    except Exception as e:
        print(f"❌ merge error: {e}", flush=True)
        try: await msg.edit_text("❌ **خطأ.**")
        except: pass
    finally:
        for p in [list_txt, out_merge, thumb_path]:
            if os.path.exists(p):
                try: os.remove(p)
                except: pass
        PROCESSING_USERS.discard(user)


@app.on_message(filters.private & filters.command(["show", "عرض"]))
async def show_media(client, message):
    user = message.from_user.id
    update_user_timestamp(user)
    
    files = USER_FILES.get(user, {}).get("files", [])
    valid_files = [f for f in files if os.path.exists(f)]
    count = len(valid_files)
    
    if count == 0:
        USER_FILES[user]["files"] = []
        await message.reply_text("📭 **فارغة.**")
    else:
        await message.reply_text(f"📋 **القائمة: ({count})**\n\n• /merge\n• /clear")


@app.on_message(filters.private & filters.command(["clear", "مسح"]))
async def clear_media(client, message):
    user = message.from_user.id
    update_user_timestamp(user)
    
    for path in USER_FILES.get(user, {}).get("files", []):
        if os.path.exists(path):
            try: os.remove(path)
            except: pass
    USER_FILES[user]["files"] = []
    await message.reply_text("🗑️ **تم الحذف.**")


@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    user = message.from_user.id
    update_user_timestamp(user)
    
    await message.reply_text(
        "🎬 **مرحباً!**\n\n"
        "📤 أرسل:\n• فيديوهات\n• صور\n• صوتيات\n• GIFs\n\n"
        "📋 /show\n🔗 /merge\n🗑️ /clear"
    )


if __name__ == "__main__":
    print("🚀 بدء البوت...", flush=True)
    
    cleanup_thread = threading.Thread(target=cleanup_old_files_sync, daemon=True)
    cleanup_thread.start()
    
    app.run()

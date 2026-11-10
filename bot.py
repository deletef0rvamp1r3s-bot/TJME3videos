import asyncio
import os
import re
import sys
import time
import shutil
from collections import defaultdict

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

from pyrogram import Client, filters
from pyrogram.errors import MessageNotModified, FloodWait

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

if not (API_ID and API_HASH and BOT_TOKEN):
    raise ValueError("⚠️ متغيرات Railway ناقصة")

# ✅✅✅ استخدام app.run() مباشرة - الطريقة الوحيدة المضمونة في Railway
app = Client("railway_merger", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

USER_FILES = defaultdict(lambda: {"files": [], "timestamp": time.time()})
USER_LOCKS = defaultdict(asyncio.Lock)
PROCESSING_USERS = set()


async def download_safe(message, file_name):
    while True:
        try:
            return await message.download(file_name=file_name)
        except FloodWait as e:
            wait = int(e.value) + 10
            print(f"⏳ FloodWait بالتحميل: ننتظر {wait} ثانية", flush=True)
            await asyncio.sleep(wait)
        except Exception as e:
            print("download_safe error:", e)
            return None


async def send_video_safe(client, **kwargs):
    while True:
        try:
            return await client.send_video(**kwargs)
        except FloodWait as e:
            wait = int(e.value) + 10
            print(f"⏳ FloodWait بالإرسال: ننتظر {wait} ثانية", flush=True)
            await asyncio.sleep(wait)
        except Exception as e:
            print("send_video_safe error:", e)
            raise


async def cleanup_old_files():
    while True:
        try:
            await asyncio.sleep(600)
            current_time = time.time()
            users_to_delete = []
            for user_id, data in USER_FILES.items():
                if current_time - data["timestamp"] > 1800:
                    for file_path in data["files"]:
                        if os.path.exists(file_path):
                            try:
                                os.remove(file_path)
                            except Exception:
                                pass
                    users_to_delete.append(user_id)
            for user_id in users_to_delete:
                del USER_FILES[user_id]
                print(f"🗑️ تنظيف {user_id}", flush=True)
        except Exception as e:
            print(f"❌ cleanup error: {e}")


def update_user_timestamp(user_id):
    USER_FILES[user_id]["timestamp"] = time.time()


async def detect_dimensions(file_path):
    cmd = [FFMPEG, "-nostdin", "-i", file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    match = re.search(r"Video:.*?(\d+)x(\d+)", stderr.decode('utf-8', errors='ignore'))
    return (int(match.group(1)), int(match.group(2))) if match else (1280, 720)


async def get_smart_filter(file_path):
    W, H = await detect_dimensions(file_path)
    aspect = W / H if H > 0 else 1.0
    if aspect < 0.65:
        return "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1"
    return "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:black,setsar=1"


async def get_duration_async(file_path):
    cmd = [FFMPEG, "-nostdin", "-i", file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr.decode('utf-8', errors='ignore'))
    if match:
        h, m, s = match.groups()
        return float(h) * 3600 + float(m) * 60 + float(s)
    return 0.0


async def check_streams_async(file_path):
    cmd = [FFMPEG, "-nostdin", "-i", file_path]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    s = stderr.decode('utf-8', errors='ignore')
    return "Video:" in s, "Audio:" in s


async def run_cmd_async(cmd):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return proc.returncode


async def run_cmd_with_progress(cmd, total_duration, msg, header_text="**Processing...**"):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    last_update = 0
    pattern = re.compile(r"out_time_ms=(\d+)")
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        match = pattern.search(line.decode("utf-8", errors="ignore"))
        if match and total_duration > 0:
            current_sec = int(match.group(1)) / 1000000.0
            percent = min(100, int((current_sec / total_duration) * 100))
            bar = "█" * (percent // 10) + "░" * (10 - percent // 10)
            now = time.time()
            if now - last_update >= 3:
                last_update = now
                try:
                    await msg.edit_text(f"{header_text}\n\n📊 **Progress: {percent}%**\n`[{bar}]`")
                except:
                    pass
    await proc.wait()
    return proc.returncode


# ✅✅✅ كل الـ handlers مع logging
@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    user = message.from_user.id
    update_user_timestamp(user)
    print(f"👋 start من {user}", flush=True)
    await message.reply_text(
        "🎬 **مرحباً!**\n\n"
        "📤 أرسل:\n• فيديوهات\n• صور\n• صوتيات\n• GIFs\n\n"
        "📋 /show\n🔗 /merge\n🗑️ /clear"
    )


@app.on_message(filters.command(["show", "عرض"]))
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


@app.on_message(filters.command(["clear", "مسح"]))
async def clear_media(client, message):
    user = message.from_user.id
    update_user_timestamp(user)
    for path in USER_FILES.get(user, {}).get("files", []):
        if os.path.exists(path):
            try: os.remove(path)
            except: pass
    USER_FILES[user]["files"] = []
    await message.reply_text("🗑️ **تم الحذف.**")


@app.on_message(filters.private & (filters.video | filters.document | filters.photo | filters.audio | filters.voice))
async def process_media(client, message):
    user = message.from_user.id
    update_user_timestamp(user)
    print(f"🎬 معالجة وسائط من {user}", flush=True)

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
            dl_path = await download_safe(message, dl_name)
            if not dl_path or not os.path.exists(dl_path):
                await msg.edit_text("❌ **فشل التحميل.**")
                return

            duration = await get_duration_async(dl_path) or 3.0
            smart_filter = await get_smart_filter(dl_path)
            scale_filter = f"{smart_filter},setdar=9/16,fps=30"

            if message.photo:
                cmd = [FFMPEG, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                       "-loop", "1", "-framerate", "30", "-t", "3", "-i", dl_path,
                       "-f", "lavfi", "-t", "3", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                       "-map", "0:v:0", "-map", "1:a:0", "-vf", scale_filter,
                       "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                       "-pix_fmt", "yuv420p", "-r", "30", "-maxrate", "6M", "-bufsize", "12M",
                       "-video_track_timescale", "90000", "-max_muxing_queue_size", "1024",
                       "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", out_path]
            else:
                has_v, has_a = await check_streams_async(dl_path)
                if has_v and has_a:
                    cmd = [FFMPEG, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                           "-i", dl_path, "-map", "0:v:0", "-map", "0:a:0", "-vf", scale_filter,
                           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                           "-pix_fmt", "yuv420p", "-r", "30", "-maxrate", "6M", "-bufsize", "12M",
                           "-video_track_timescale", "90000", "-max_muxing_queue_size", "1024",
                           "-c:a", "aac", "-ar", "44100", "-ac", "2", out_path]
                elif has_v and not has_a:
                    cmd = [FFMPEG, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                           "-i", dl_path, "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                           "-map", "0:v:0", "-map", "1:a:0", "-vf", scale_filter,
                           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                           "-pix_fmt", "yuv420p", "-r", "30", "-maxrate", "6M", "-bufsize", "12M",
                           "-video_track_timescale", "90000", "-max_muxing_queue_size", "1024",
                           "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", out_path]
                elif not has_v and has_a:
                    cmd = [FFMPEG, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                           "-f", "lavfi", "-i", "color=c=black:s=720x1280:r=30",
                           "-i", dl_path, "-map", "0:v:0", "-map", "1:a:0",
                           "-vf", "setdar=9/16,fps=30",
                           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                           "-pix_fmt", "yuv420p", "-r", "30", "-maxrate", "6M", "-bufsize", "12M",
                           "-video_track_timescale", "90000", "-max_muxing_queue_size", "1024",
                           "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", out_path]
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
    print(f"🎬 معالجة GIF من {user}", flush=True)

    dl_name = f"gif_{message.id}_{user}.gif"
    out_path = f"vid_{message.id}_{user}.mp4"
    dl_path = None
    msg = await message.reply_text("⏳ **معالجة GIF...**")

    async with USER_LOCKS[user]:
        try:
            await msg.edit_text("📥 **تحميل...**")
            dl_path = await download_safe(message, dl_name)
            if not dl_path or not os.path.exists(dl_path):
                await msg.edit_text("❌ **فشل التحميل.**")
                return

            duration = await get_duration_async(dl_path) or 3.0
            smart_filter = await get_smart_filter(dl_path)
            scale_filter = f"{smart_filter},setdar=9/16,fps=30"

            cmd = [FFMPEG, "-y", "-nostdin", "-threads", "1",
                   "-loop", "0", "-i", dl_path,
                   "-f", "lavfi", "-t", str(duration), "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                   "-map", "0:v:0", "-map", "1:a:0", "-vf", scale_filter,
                   "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                   "-pix_fmt", "yuv420p", "-r", "30", "-maxrate", "6M", "-bufsize", "12M",
                   "-video_track_timescale", "90000", "-max_muxing_queue_size", "1024",
                   "-c:a", "aac", "-ar", "44100", "-ac", "2", "-t", str(duration), "-f", "mp4", out_path]

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
    print(f"🔗 دمج من {user}", flush=True)

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

        cmd = [FFMPEG, "-y", "-nostdin", "-progress", "pipe:1",
               "-f", "concat", "-safe", "0", "-i", list_txt,
               "-c", "copy", "-movflags", "+faststart", out_merge]

        returncode = await run_cmd_with_progress(cmd, total_dur, msg, "🔄 **دمج...**")
        if returncode == 0 and os.path.exists(out_merge) and os.path.getsize(out_merge) > 0:
            await msg.edit_text("📤 **جاري الرفع...**")
            thumb_cmd = [FFMPEG, "-y", "-nostdin", "-i", out_merge, "-ss", "00:00:00.500", "-vframes", "1",
                         "-vf", "scale=360:640:force_original_aspect_ratio=decrease,pad=360:640:(ow-iw)/2:(oh-ih)/2:black", thumb_path]
            await run_cmd_async(thumb_cmd)
            final_duration = await get_duration_async(out_merge)

            kwargs = {"chat_id": user, "video": out_merge, "caption": "✅ **جاهز!**",
                      "duration": int(final_duration), "width": 720, "height": 1280}
            if os.path.exists(thumb_path):
                kwargs["thumb"] = thumb_path
            await send_video_safe(client, **kwargs, supports_streaming=True)

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


# ✅✅✅ التشغيل المباشر - الطريقة الوحيدة المضمونة
if __name__ == "__main__":
    print("🚀 بدء البوت...", flush=True)
    asyncio.create_task(cleanup_old_files())
    app.run()

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

# ✅ بنية جديدة للـ USER_FILES مع timestamp
USER_FILES = defaultdict(lambda: {"files": [], "timestamp": time.time()})
USER_LOCKS = defaultdict(asyncio.Lock)
PROCESSING_USERS = set()

# ✅ دالة تنظيف الملفات القديمة (أكثر من 30 دقيقة)
async def cleanup_old_files():
    """حذف الملفات والبيانات للمستخدمين غير النشطين"""
    while True:
        try:
            await asyncio.sleep(600)  # كل 10 دقائق
            current_time = time.time()
            users_to_delete = []
            
            for user_id, data in USER_FILES.items():
                # إذا المستخدم ما استخدم البوت لمدة 30 دقيقة
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
                print(f"🗑️ تنظيف بيانات المستخدم {user_id}")
                
        except Exception as e:
            print(f"❌ خطأ في التنظيف التلقائي: {e}")

# ✅ تحديث timestamp
def update_user_timestamp(user_id):
    """تحديث آخر نشاط للمستخدم"""
    USER_FILES[user_id]["timestamp"] = time.time()

async def detect_dimensions(file_path):
    """اكتشف الأبعاد الفعلية للفيديو"""
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
    """فلتر ذكي بدون براتيل - يتعرف على الأيسبكت تلقائي"""
    W, H = await detect_dimensions(file_path)
    aspect = W / H if H > 0 else 1.0
    
    # 9:16 عمودي (aspect < 0.65)
    if aspect < 0.65:
        return f"scale=720:-1,crop=720:1280:(in_w-out_w)/2:0"
    # 16:9 أفقي (aspect > 1.4)
    elif aspect > 1.4:
        return f"scale=-1:1280,pad=720:1280:(ow-iw)/2:(oh-ih)/2:black"
    # 4:3 أو مربع (Square-ish)
    else:
        return f"scale=720:-1,pad=720:1280:0:(oh-ih)/2:black"

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

# 2. معالج الفيديوهات والصور والملفات الصوتية
@app.on_message(filters.private & (filters.video | filters.document | filters.photo | filters.audio | filters.voice))
async def process_media(client, message):
    user = message.from_user.id
    update_user_timestamp(user)  # ✅ تحديث التايمستامب
    
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
    dl_path = None

    msg = await message.reply_text("⏳ **Queued for processing...**")

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
            
            smart_filter = await get_smart_filter(dl_path)
            scale_filter = f"{smart_filter},setdar=9/16,fps=60"  # ✅ 60 فريم
                
            if message.photo:
                cmd = [
                    FFMPEG, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                    "-loop", "1", "-framerate", "60", "-t", "3", "-i", dl_path,  # ✅ 60
                    "-f", "lavfi", "-t", "3", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-vf", scale_filter,
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-r", "60", "-video_track_timescale", "90000",  # ✅ 60
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
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-r", "60", "-video_track_timescale", "90000",  # ✅ 60
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
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-r", "60", "-video_track_timescale", "90000",  # ✅ 60
                        "-max_muxing_queue_size", "1024",
                        "-c:a", "aac", "-ar", "44100", "-ac", "2",
                        "-shortest", out_path
                    ]
                elif not has_v and has_a:
                    cmd = [
                        FFMPEG, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                        "-f", "lavfi", "-i", f"color=c=black:s=720x1280:r=60",  # ✅ 60
                        "-i", dl_path,
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-vf", "setdar=9/16,fps=60",  # ✅ 60
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-r", "60", "-video_track_timescale", "90000",  # ✅ 60
                        "-max_muxing_queue_size", "1024",
                        "-c:a", "aac", "-ar", "44100", "-ac", "2",
                        "-shortest", out_path
                    ]
                else:
                    await msg.edit_text("❌ **الملف غير مدعوم.**")
                    return

            returncode = await run_cmd_with_progress(cmd, duration, msg, "⚙️ **Processing Media...**", log_file)
            
            if returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                USER_FILES[user]["files"].append(out_path)  # ✅ تعديل البنية
                await msg.edit_text(f"✅ **تم إضافة المقطع!**\n📋 **القائمة: ({len(USER_FILES[user]['files'])})**\n\n• /show لعرض\n• /merge للدمج\n• /clear لحذف")
            else:
                await msg.edit_text(f"❌ **خطأ في المعالجة. Exit Code: {returncode}**")
                
        except Exception as e:
            try: 
                await msg.edit_text(f"❌ **خطأ في التحميل أو المعالجة.**")
            except MessageNotModified: 
                pass
        finally:
            if dl_path and isinstance(dl_path, str) and os.path.exists(dl_path):
                try: os.remove(dl_path)
                except Exception: pass
            if os.path.exists(log_file):
                try: os.remove(log_file)
                except Exception: pass

# ✅ معالج جديد للـ GIFs (Animations)
@app.on_message(filters.private & filters.animation)
async def process_gif(client, message):
    user = message.from_user.id
    update_user_timestamp(user)  # ✅ تحديث التايمستامب
    
    dl_name = f"gif_{message.id}_{user}.gif"
    out_path = f"vid_{message.id}_{user}.mp4"
    log_file = f"ffmpeg_gif_err_{message.id}_{user}.log"
    dl_path = None

    msg = await message.reply_text("⏳ **جاري معالجة الـ GIF...**")

    async with USER_LOCKS[user]:
        try:
            await msg.edit_text("📥 **جاري التحميل...**")
            
            dl_path = await message.download(file_name=dl_name)
            
            if not dl_path or not os.path.exists(dl_path):
                await msg.edit_text("❌ **فشل التحميل.**")
                return

            duration = await get_duration_async(dl_path)
            if duration == 0:
                duration = 3.0
            
            # ✅ تطبيق الفلتر الذكي على الـ GIF أيضاً
            smart_filter = await get_smart_filter(dl_path)
            scale_filter = f"{smart_filter},setdar=9/16,fps=60"  # ✅ 60 فريم
            
            cmd = [
                FFMPEG, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                "-i", dl_path,
                "-vf", scale_filter,
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-r", "60", "-video_track_timescale", "90000",  # ✅ 60
                "-max_muxing_queue_size", "1024",
                "-f", "mp4",
                out_path
            ]
            
            returncode = await run_cmd_with_progress(cmd, duration, msg, "⚙️ **معالجة الـ GIF...**", log_file)
            
            if returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                USER_FILES[user]["files"].append(out_path)
                await msg.edit_text(f"✅ **تم تحويل الـ GIF!**\n📋 **القائمة: ({len(USER_FILES[user]['files'])})**\n\n• /show لعرض\n• /merge للدمج\n• /clear لحذف")
            else:
                await msg.edit_text(f"❌ **خطأ في تحويل الـ GIF. Exit Code: {returncode}**")
                
        except Exception as e:
            try: 
                await msg.edit_text(f"❌ **خطأ في معالجة الـ GIF.**")
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
    update_user_timestamp(user)  # ✅ تحديث التايمستامب
    
    if user in PROCESSING_USERS:
        return await message.reply_text("⏳ **جاري الدمج، الرجاء الانتظار...**")
        
    files = USER_FILES.get(user, {}).get("files", [])  # ✅ تعديل البنية
    valid_files = [f for f in files if os.path.exists(f)]
    if len(valid_files) < 2:
        USER_FILES[user]["files"] = [] 
        return await message.reply_text("❌ **نحتاج 2 مقطع على الأقل للدمج!**")
        
    PROCESSING_USERS.add(user)
    msg = await message.reply_text("⏳ **بدء الدمج الفوري...**")
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
        
        returncode = await run_cmd_with_progress(cmd, total_dur, msg, "🔄 **جاري الدمج...**", log_file)
        
        if returncode == 0 and os.path.exists(out_merge) and os.path.getsize(out_merge) > 0:
            await msg.edit_text("📤 **انتهى الدمج! جاري الرفع...**")
            
            thumb_cmd = [
                FFMPEG, "-y", "-nostdin", "-i", out_merge, "-ss", "00:00:00.500", "-vframes", "1",
                "-vf", "scale=360:640:force_original_aspect_ratio=decrease,pad=360:640:(ow-iw)/2:(oh-ih)/2:black", 
                thumb_path
            ]
            await run_cmd_async(thumb_cmd)
            
            final_duration = await get_duration_async(out_merge)
            
            try:
                kwargs = {
                    "chat_id": user,
                    "video": out_merge,
                    "caption": "✅ **فيديوك المدمج جاهز!**",
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
                        except Exception: pass
                
                USER_FILES[user]["files"] = []  # ✅ تعديل البنية
                await msg.delete()
                
            except Exception as e:
                await msg.edit_text(f"❌ **خطأ في الرفع.**")
        else:
            await msg.edit_text(f"❌ **خطأ في الدمج. Exit Code: {returncode}**")
            
    except Exception as e:
        try: 
            await msg.edit_text(f"❌ **خطأ غير متوقع.**")
        except MessageNotModified: 
            pass
    finally:
        if os.path.exists(list_txt):
            try: os.remove(list_txt)
            except Exception: pass
        if os.path.exists(out_merge):
            try: os.remove(out_merge)
            except Exception: pass
        if os.path.exists(thumb_path):
            try: os.remove(thumb_path)
            except Exception: pass
        if os.path.exists(log_file):
            try: os.remove(log_file)
            except Exception: pass
        PROCESSING_USERS.discard(user)

@app.on_message(filters.private & filters.command(["show", "عرض"]))
async def show_media(client, message):
    user = message.from_user.id
    update_user_timestamp(user)  # ✅ تحديث التايمستامب
    
    files = USER_FILES.get(user, {}).get("files", [])  # ✅ تعديل البنية
    valid_files = [f for f in files if os.path.exists(f)]
    count = len(valid_files)
    
    if count == 0:
        USER_FILES[user]["files"] = []
        await message.reply_text("📭 **قائمتك فارغة.**")
    else:
        await message.reply_text(f"📋 **القائمة الحالية:**\n• **المقاطع المجهزة:** **({count})**\n\n• /merge للدمج\n• /clear للحذف")

@app.on_message(filters.private & filters.command(["clear", "مسح"]))
async def clear_media(client, message):
    user = message.from_user.id
    update_user_timestamp(user)  # ✅ تحديث التايمستامب
    
    for path in USER_FILES.get(user, {}).get("files", []):  # ✅ تعديل البنية
        if os.path.exists(path): 
            try: os.remove(path)
            except Exception: pass
    USER_FILES[user]["files"] = []
    await message.reply_text("🗑️ **تم حذف القائمة وتحرير الذاكرة.**")

@app.on_message(filters.command(["start"]))
async def start_cmd(client, message):
    user = message.from_user.id
    update_user_timestamp(user)  # ✅ تحديث التايمستامب
    
    await message.reply_text(
        "🎬 **مرحباً بك في دمج الفيديوهات!**\n\n"
        "📤 أرسل لي:\n"
        "• فيديوهات\n"
        "• صور\n"
        "• ملفات صوتية\n"
        "• مقاطع صوتية\n"
        "• GIFs\n\n"
        "📋 **/show** - عرض المقاطع\n"
        "🔗 **/merge** - دمج المقاطع\n"
        "🗑️ **/clear** - حذف المقاطع"
    )

async def main():
    """تشغيل البوت والتنظيف التلقائي"""
    asyncio.create_task(cleanup_old_files())  # ✅ تشغيل التنظيف
    await app.start()
    print("🤖 البوت يعمل بكفاءة على Railway...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())

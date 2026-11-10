import asyncio
import os
import re
import time
import shutil
import logging
from collections import defaultdict

logging.basicConfig(level=logging.CRITICAL)
try:
    logging.getLogger("pyrogram").setLevel(logging.CRITICAL)
except Exception:
    pass

from pyrogram import Client, filters, idle
from pyrogram.errors import MessageNotModified

try:
    import imageio_ffmpeg
except Exception:
    imageio_ffmpeg = None

# ✅✅✅ المتغيرات من Railway Variables
API_ID = int(os.environ.get("API_ID", "0") or 0)
API_HASH = str(os.environ.get("API_HASH", "") or "").strip()
BOT_TOKEN = str(os.environ.get("BOT_TOKEN", "") or "").strip()

def get_ffmpeg_path():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    if imageio_ffmpeg is not None:
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    return "ffmpeg"


FFMPEG = get_ffmpeg_path()

app = Client(
    "colab_video_merger_bot",
    api_id=API_ID if API_ID else 12345,
    api_hash=API_HASH if API_HASH else "placeholder",
    bot_token=BOT_TOKEN if BOT_TOKEN else "123456:placeholder"
)

USER_FILES = defaultdict(lambda: {"files": [], "timestamp": time.time()})
USER_LOCKS = defaultdict(asyncio.Lock)
PROCESSING_USERS = set()

VIDEO_OPTS = [
    "-c:v", "libx264",
    "-preset", "ultrafast",
    "-crf", "18",
    "-pix_fmt", "yuv420p",
    "-r", "60",
    "-video_track_timescale", "90000",
    "-max_muxing_queue_size", "1024"
]

AUDIO_OPTS = ["-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k"]
SILENT_AUDIO = "anullsrc=channel_layout=stereo:sample_rate=44100"


def update_user_timestamp(user_id):
    USER_FILES[user_id]["timestamp"] = time.time()


async def safe_reply(message, text):
    try:
        return await message.reply_text(text)
    except Exception as e:
        print("safe_reply error:", e)
        return None


async def safe_edit(msg, text):
    if not msg:
        return
    try:
        await msg.edit_text(text)
    except MessageNotModified:
        pass
    except Exception as e:
        print("safe_edit error:", e)


async def safe_delete(msg):
    if not msg:
        return
    try:
        await msg.delete()
    except Exception as e:
        print("safe_delete error:", e)


def mime_startswith(message, prefix):
    try:
        doc = getattr(message, "document", None)
        if doc and getattr(doc, "mime_type", None):
            return str(doc.mime_type).startswith(prefix)
    except Exception:
        pass
    return False


async def cleanup_old_files():
    while True:
        try:
            await asyncio.sleep(600)
            current_time = time.time()
            users_to_delete = []

            for user_id, data in list(USER_FILES.items()):
                if current_time - data.get("timestamp", 0) > 1800:
                    for file_path in data.get("files", []):
                        if os.path.exists(file_path):
                            try:
                                os.remove(file_path)
                            except Exception:
                                pass
                    users_to_delete.append(user_id)

            for user_id in users_to_delete:
                USER_FILES.pop(user_id, None)
                print(f"🗑️ تنظيف بيانات المستخدم {user_id}")

        except Exception as e:
            print("cleanup_old_files error:", e)


async def detect_dimensions(file_path):
    cmd = [FFMPEG, "-nostdin", "-hide_banner", "-i", file_path]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    stderr_str = stderr.decode("utf-8", errors="ignore")

    match = re.search(r"Video:.*?(\d{2,6})x(\d{2,6})", stderr_str)
    if match:
        return int(match.group(1)), int(match.group(2))

    return 1280, 720


async def get_smart_filter(file_path):
    W, H = await detect_dimensions(file_path)
    aspect = W / H if H > 0 else 1.0

    if aspect < 0.65:
        return "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1"

    return "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:black,setsar=1"


async def get_duration_async(file_path):
    cmd = [FFMPEG, "-nostdin", "-hide_banner", "-i", file_path]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    stderr_str = stderr.decode("utf-8", errors="ignore")

    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr_str)
    if match:
        hours, minutes, seconds = match.groups()
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)

    return 0.0


async def check_streams_async(file_path):
    cmd = [FFMPEG, "-nostdin", "-hide_banner", "-i", file_path]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    stderr_str = stderr.decode("utf-8", errors="ignore")

    return "Video:" in stderr_str, "Audio:" in stderr_str


async def run_cmd_async(cmd):
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        return proc.returncode
    except Exception as e:
        print("run_cmd_async error:", e)
        return 1


async def run_cmd_with_progress(cmd, total_duration, msg, header_text="**Processing...**"):
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
    except Exception as e:
        print("run_cmd_with_progress error:", e)
        return 1

    last_update = 0
    pattern = re.compile(r"out_time_ms=(\d+)")

    try:
        if proc.stdout is not None:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break

                line_str = line.decode("utf-8", errors="ignore").strip()
                match = pattern.search(line_str)

                if match and total_duration and total_duration > 0:
                    current_sec = int(match.group(1)) / 1_000_000.0
                    percent = min(100, int((current_sec / total_duration) * 100))

                    filled = int(percent / 10)
                    bar = "█" * filled + "░" * (10 - filled)

                    now = time.time()
                    if now - last_update >= 3:
                        last_update = now
                        await safe_edit(msg, f"{header_text}\n\n📊 **Progress: {percent}%**\n`[{bar}]`")
    except Exception as e:
        print("progress loop error:", e)

    await proc.wait()
    return proc.returncode


@app.on_message(filters.private & (filters.video | filters.document | filters.photo | filters.audio | filters.voice))
async def process_media(client, message):
    try:
        if not message.from_user:
            return

        user = message.from_user.id
        update_user_timestamp(user)

        if user in PROCESSING_USERS:
            await safe_reply(message, "⏳ **جاري عملية أخرى، الرجاء الانتظار...**")
            return

        ext = ".mp4"

        if message.photo:
            ext = ".jpg"
        elif message.voice:
            ext = ".ogg"
        elif message.audio:
            ext = os.path.splitext(message.audio.file_name or "audio.mp3")[1] or ".mp3"
        elif message.video and message.video.file_name:
            ext = os.path.splitext(message.video.file_name)[1] or ".mp4"
        elif message.document and message.document.file_name:
            ext = os.path.splitext(message.document.file_name)[1] or ".mp4"

        ext = ext or ".mp4"

        dl_name = f"dl_{message.id}_{user}{ext}"
        out_path = f"vid_{message.id}_{user}.mp4"

        msg = await safe_reply(message, "⏳ **بالانتظار...**")
        if not msg:
            return

        async with USER_LOCKS[user]:
            if user in PROCESSING_USERS:
                await safe_edit(msg, "⏳ **جاري عملية أخرى، الرجاء الانتظار...**")
                return

            dl_path = None
            success = False

            try:
                await safe_edit(msg, "📥 **جاري التحميل من تليجرام...**")
                dl_path = await message.download(file_name=dl_name)

                if not dl_path or not os.path.exists(dl_path):
                    await safe_edit(msg, "❌ **فشل التحميل.**")
                    return

                original_duration = await get_duration_async(dl_path)
                duration = original_duration if original_duration > 0 else 3.0

                smart_filter = await get_smart_filter(dl_path)
                scale_filter = f"{smart_filter},fps=60"

                is_audio_msg = bool(message.audio or message.voice or mime_startswith(message, "audio/"))
                is_image_msg = bool(message.photo or mime_startswith(message, "image/"))

                file_name_lower = ""
                if message.document and getattr(message.document, "file_name", None):
                    file_name_lower = str(message.document.file_name).lower()

                is_gif_msg = bool(file_name_lower.endswith(".gif") or mime_startswith(message, "image/gif"))

                if os.path.exists(out_path):
                    try:
                        os.remove(out_path)
                    except Exception:
                        pass

                if is_audio_msg:
                    cmd = [
                        FFMPEG, "-y", "-nostdin", "-hide_banner", "-threads", "1", "-progress", "pipe:1",
                        "-f", "lavfi", "-i", "color=c=black:s=720x1280:r=60",
                        "-i", dl_path,
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-vf", "fps=60,setsar=1"
                    ]
                    cmd += VIDEO_OPTS + AUDIO_OPTS + ["-t", str(duration), "-shortest", out_path]

                elif is_gif_msg:
                    cmd = [
                        FFMPEG, "-y", "-nostdin", "-hide_banner", "-threads", "1", "-progress", "pipe:1",
                        "-i", dl_path,
                        "-f", "lavfi", "-t", str(duration), "-i", SILENT_AUDIO,
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-vf", scale_filter
                    ]
                    cmd += VIDEO_OPTS + AUDIO_OPTS + ["-shortest", out_path]

                elif is_image_msg:
                    cmd = [
                        FFMPEG, "-y", "-nostdin", "-hide_banner", "-threads", "1", "-progress", "pipe:1",
                        "-loop", "1", "-framerate", "60", "-t", "3", "-i", dl_path,
                        "-f", "lavfi", "-t", "3", "-i", SILENT_AUDIO,
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-vf", scale_filter
                    ]
                    cmd += VIDEO_OPTS + AUDIO_OPTS + ["-shortest", out_path]

                else:
                    has_v, has_a = await check_streams_async(dl_path)

                    if has_v and has_a:
                        cmd = [
                            FFMPEG, "-y", "-nostdin", "-hide_banner", "-threads", "1", "-progress", "pipe:1",
                            "-i", dl_path,
                            "-map", "0:v:0", "-map", "0:a:0",
                            "-vf", scale_filter
                        ]
                        cmd += VIDEO_OPTS + AUDIO_OPTS + ["-shortest", out_path]

                    elif has_v and not has_a:
                        cmd = [
                            FFMPEG, "-y", "-nostdin", "-hide_banner", "-threads", "1", "-progress", "pipe:1",
                            "-i", dl_path,
                            "-f", "lavfi", "-i", SILENT_AUDIO,
                            "-map", "0:v:0", "-map", "1:a:0",
                            "-vf", scale_filter
                        ]
                        cmd += VIDEO_OPTS + AUDIO_OPTS + ["-shortest", out_path]

                    elif not has_v and has_a:
                        cmd = [
                            FFMPEG, "-y", "-nostdin", "-hide_banner", "-threads", "1", "-progress", "pipe:1",
                            "-f", "lavfi", "-i", "color=c=black:s=720x1280:r=60",
                            "-i", dl_path,
                            "-map", "0:v:0", "-map", "1:a:0",
                            "-vf", "fps=60,setsar=1"
                        ]
                        cmd += VIDEO_OPTS + AUDIO_OPTS + ["-t", str(duration), "-shortest", out_path]

                    else:
                        await safe_edit(msg, "❌ **الملف غير مدعوم.**")
                        return

                returncode = await run_cmd_with_progress(cmd, duration, msg, "⚙️ **جاري معالجة الوسائط...**")

                if returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    USER_FILES[user]["files"].append(out_path)
                    update_user_timestamp(user)
                    success = True
                    await safe_edit(
                        msg,
                        f"✅ **تم إضافة المقطع!**\n📋 **القائمة: ({len(USER_FILES[user]['files'])})**\n\n• /show لعرض المقاطع\n• /merge للدمج\n• /clear للحذف"
                    )
                else:
                    await safe_edit(msg, f"❌ **خطأ في المعالجة. Exit Code: {returncode}**")

            except Exception as e:
                print("process_media inner error:", e)
                await safe_edit(msg, "❌ **خطأ في التحميل أو المعالجة.**")

            finally:
                if dl_path and os.path.exists(dl_path):
                    try:
                        os.remove(dl_path)
                    except Exception:
                        pass

                if not success and os.path.exists(out_path):
                    try:
                        os.remove(out_path)
                    except Exception:
                        pass

    except Exception as e:
        print("process_media outer error:", e)


@app.on_message(filters.private & filters.animation)
async def process_gif(client, message):
    try:
        if not message.from_user:
            return

        user = message.from_user.id
        update_user_timestamp(user)

        if user in PROCESSING_USERS:
            await safe_reply(message, "⏳ **جاري عملية أخرى، الرجاء الانتظار...**")
            return

        ext = os.path.splitext(message.animation.file_name or "animation.mp4")[1] or ".mp4"

        dl_name = f"anim_{message.id}_{user}{ext}"
        out_path = f"vid_{message.id}_{user}.mp4"

        msg = await safe_reply(message, "⏳ **جاري تجهيز الـ GIF / Animation...**")
        if not msg:
            return

        async with USER_LOCKS[user]:
            if user in PROCESSING_USERS:
                await safe_edit(msg, "⏳ **جاري عملية أخرى، الرجاء الانتظار...**")
                return

            dl_path = None
            success = False

            try:
                await safe_edit(msg, "📥 **جاري التحميل...**")
                dl_path = await message.download(file_name=dl_name)

                if not dl_path or not os.path.exists(dl_path):
                    await safe_edit(msg, "❌ **فشل التحميل.**")
                    return

                original_duration = await get_duration_async(dl_path)
                duration = original_duration if original_duration > 0 else 3.0

                smart_filter = await get_smart_filter(dl_path)
                scale_filter = f"{smart_filter},fps=60"

                if os.path.exists(out_path):
                    try:
                        os.remove(out_path)
                    except Exception:
                        pass

                cmd = [
                    FFMPEG, "-y", "-nostdin", "-hide_banner", "-threads", "1", "-progress", "pipe:1",
                    "-i", dl_path,
                    "-f", "lavfi", "-t", str(duration), "-i", SILENT_AUDIO,
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-vf", scale_filter
                ]
                cmd += VIDEO_OPTS + AUDIO_OPTS + ["-shortest", out_path]

                returncode = await run_cmd_with_progress(cmd, duration, msg, "⚙️ **معالجة الـ GIF...**")

                if returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    USER_FILES[user]["files"].append(out_path)
                    update_user_timestamp(user)
                    success = True
                    await safe_edit(
                        msg,
                        f"✅ **تم تحويل الـ GIF!**\n📋 **القائمة: ({len(USER_FILES[user]['files'])})**\n\n• /show لعرض المقاطع\n• /merge للدمج\n• /clear للحذف"
                    )
                else:
                    await safe_edit(msg, f"❌ **خطأ في تحويل الـ GIF. Exit Code: {returncode}**")

            except Exception as e:
                print("process_gif inner error:", e)
                await safe_edit(msg, "❌ **خطأ في معالجة الـ GIF.**")

            finally:
                if dl_path and os.path.exists(dl_path):
                    try:
                        os.remove(dl_path)
                    except Exception:
                        pass

                if not success and os.path.exists(out_path):
                    try:
                        os.remove(out_path)
                    except Exception:
                        pass

    except Exception as e:
        print("process_gif outer error:", e)


@app.on_message(filters.private & filters.command(["merge", "دمج"]))
async def merge_media(client, message):
    try:
        if not message.from_user:
            return

        user = message.from_user.id
        update_user_timestamp(user)

        async with USER_LOCKS[user]:
            if user in PROCESSING_USERS:
                await safe_reply(message, "⏳ **جاري الدمج، الرجاء الانتظار...**")
                return

            files = USER_FILES.get(user, {}).get("files", [])
            valid_files = [f for f in files if os.path.exists(f)]

            if len(valid_files) < 2:
                USER_FILES[user]["files"] = []
                await safe_reply(message, "❌ **نحتاج 2 مقطع على الأقل للدمج!**")
                return

            PROCESSING_USERS.add(user)
            msg = await safe_reply(message, "⏳ **بدء الدمج...**")

            list_txt = f"list_{user}.txt"
            out_merge = f"final_{user}.mp4"
            success = False

            try:
                total_dur = 0.0

                with open(list_txt, "w", encoding="utf-8") as f:
                    for path in valid_files:
                        f.write(f"file '{os.path.abspath(path)}'\n")
                        total_dur += await get_duration_async(path)

                if os.path.exists(out_merge):
                    try:
                        os.remove(out_merge)
                    except Exception:
                        pass

                cmd = [
                    FFMPEG, "-y", "-nostdin", "-hide_banner", "-progress", "pipe:1",
                    "-f", "concat", "-safe", "0", "-i", list_txt,
                    "-c", "copy", "-movflags", "+faststart", out_merge
                ]

                returncode = await run_cmd_with_progress(cmd, total_dur, msg, "🔄 **جاري الدمج...**")

                if returncode == 0 and os.path.exists(out_merge) and os.path.getsize(out_merge) > 0:
                    await safe_edit(msg, "📤 **انتهى الدمج! جاري الرفع...**")

                    final_duration = await get_duration_async(out_merge)
                    if final_duration <= 0:
                        final_duration = 1.0

                    await client.send_video(
                        chat_id=user,
                        video=out_merge,
                        caption="✅ **فيديوك المدمج جاهز!**",
                        duration=int(final_duration),
                        width=720,
                        height=1280,
                        supports_streaming=True
                    )

                    for path in valid_files:
                        if os.path.exists(path):
                            try:
                                os.remove(path)
                            except Exception:
                                pass

                    USER_FILES[user]["files"] = []
                    update_user_timestamp(user)
                    success = True
                    await safe_delete(msg)

                else:
                    await safe_edit(msg, f"❌ **خطأ في الدمج. Exit Code: {returncode}**")

            except Exception as e:
                print("merge_media inner error:", e)
                await safe_edit(msg, "❌ **خطأ في الدمج أو الرفع.**")

            finally:
                PROCESSING_USERS.discard(user)

                if os.path.exists(list_txt):
                    try:
                        os.remove(list_txt)
                    except Exception:
                        pass

                if os.path.exists(out_merge):
                    try:
                        os.remove(out_merge)
                    except Exception:
                        pass

    except Exception as e:
        print("merge_media outer error:", e)


@app.on_message(filters.private & filters.command(["show", "عرض"]))
async def show_media(client, message):
    try:
        if not message.from_user:
            return

        user = message.from_user.id
        update_user_timestamp(user)

        files = USER_FILES.get(user, {}).get("files", [])
        valid_files = [f for f in files if os.path.exists(f)]
        USER_FILES[user]["files"] = valid_files

        count = len(valid_files)

        if count == 0:
            await safe_reply(message, "📭 **قائمتك فارغة.**")
        else:
            await safe_reply(
                message,
                f"📋 **القائمة الحالية:**\n• **المقاطع المجهزة:** **({count})**\n\n• /merge للدمج\n• /clear للحذف"
            )

    except Exception as e:
        print("show_media error:", e)


@app.on_message(filters.private & filters.command(["clear", "مسح"]))
async def clear_media(client, message):
    try:
        if not message.from_user:
            return

        user = message.from_user.id
        update_user_timestamp(user)

        async with USER_LOCKS[user]:
            if user in PROCESSING_USERS:
                await safe_reply(message, "⏳ **لا يمكن الحذف أثناء وجود عملية جارية.**")
                return

            for path in USER_FILES.get(user, {}).get("files", []):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass

            USER_FILES[user]["files"] = []
            await safe_reply(message, "🗑️ **تم حذف القائمة وتحرير الذاكرة.**")

    except Exception as e:
        print("clear_media error:", e)


@app.on_message(filters.private & filters.command(["start"]))
async def start_cmd(client, message):
    try:
        if not message.from_user:
            return

        update_user_timestamp(message.from_user.id)

        await safe_reply(
            message,
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

    except Exception as e:
        print("start_cmd error:", e)


async def main():
    cleanup_task = asyncio.create_task(cleanup_old_files())

    try:
        await app.start()
        print("🤖 البوت يعمل الآن...")
        await idle()
    finally:
        cleanup_task.cancel()

        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

        try:
            await app.stop()
        except Exception:
            pass


if not (API_ID and API_HASH and BOT_TOKEN):
    raise ValueError("⚠️ ضع API_ID و API_HASH و BOT_TOKEN في متغيرات Railway")
else:
    asyncio.run(main())

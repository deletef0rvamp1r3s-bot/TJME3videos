import os
import re
import subprocess
import sys
from collections import defaultdict
import imageio_ffmpeg
from pyrogram import Client, filters

# 🎯 الحصول على مسار FFmpeg
FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()

# 🔒 جلب البيانات من متغيرات البيئة 
try:
    API_ID = int(os.environ.get("API_ID"))
    API_HASH = os.environ.get("API_HASH")
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
except (TypeError, ValueError):
    print("❌ تنبيه أمني: تأكد من إضافة API_ID و API_HASH و BOT_TOKEN.")
    sys.exit(1)

if not all([API_ID, API_HASH, BOT_TOKEN]):
    print("❌ تنبيه أمني: إحدى البيانات الأساسية مفقودة.")
    sys.exit(1)

app = Client("video_merger_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

USER_VIDEOS = defaultdict(list)

def has_audio(file_path):
    cmd = [FFMPEG_BIN, "-i", file_path]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='ignore')
    return "Audio:" in res.stderr

def get_video_duration(video_path):
    cmd_info = [FFMPEG_BIN, "-i", video_path]
    res = subprocess.run(cmd_info, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='ignore')
    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
    if dur_match:
        h, m, s = dur_match.groups()
        return int(float(h) * 3600 + float(m) * 60 + float(s))
    return 0

def get_video_metadata_and_thumb(video_path, thumb_path):
    cmd_thumb = [
        FFMPEG_BIN, "-ss", "00:00:00.500", "-i", video_path,
        "-vframes", "1", thumb_path, "-y"
    ]
    subprocess.run(cmd_thumb, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    cmd_info = [FFMPEG_BIN, "-i", video_path]
    res = subprocess.run(cmd_info, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='ignore')
    duration = get_video_duration(video_path)
    width = 0
    height = 0

    res_match = re.search(r"Video:.*?\b(\d{3,4})x(\d{3,4})\b", res.stderr)
    if res_match:
        width = int(res_match.group(1))
        height = int(res_match.group(2))

    return duration, width, height


# 1️⃣ استلام وتوحيد كل شيء بصرامة
@app.on_message(filters.private & (filters.video | filters.document | filters.photo | filters.audio | filters.voice))
async def collect_media(client, message):
    user_id = message.from_user.id

    if message.document:
        mime = message.document.mime_type or ""
        if not (mime.startswith("video/") or mime.startswith("audio/")):
            return

    msg = await message.reply_text("⏳ جاري تنظيف وتجهيز العنصر...")

    try:
        raw_file_path = await message.download()
        processed_video_path = f"processed_{message.id}_{user_id}.mp4"

        vf_scale_no_stretch = "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1"
        base_encode_flags = [
            "-c:v", "libx264", "-preset", "fast", "-profile:v", "main",
            "-r", "60", "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
            "-vf", vf_scale_no_stretch,
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            "-sn", "-dn", 
            "-avoid_negative_ts", "make_zero"
        ]

        media_type = "فيديو"
        telegram_duration = getattr(message.video or message.audio or message.voice, "duration", 0)

        if message.photo:
            media_type = "صورة"
            telegram_duration = 3
            cmd = [
                FFMPEG_BIN, "-loop", "1", "-t", "3", "-i", raw_file_path,
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-map", "0:v:0", "-map", "1:a:0"
            ] + base_encode_flags + [processed_video_path, "-y"]
            
        elif message.audio or message.voice:
            media_type = "صوت"
            cmd = [
                FFMPEG_BIN, "-f", "lavfi", "-i", "color=c=black:s=720x1280:r=60",
                "-i", raw_file_path,
                "-map", "0:v:0", "-map", "1:a:0",
                "-shortest"
            ] + base_encode_flags + [processed_video_path, "-y"]
            
        else:
            if has_audio(raw_file_path):
                cmd = [
                    FFMPEG_BIN, "-err_detect", "ignore_err", "-i", raw_file_path,
                    "-map", "0:v:0", "-map", "0:a:0?"
                ] + base_encode_flags + [processed_video_path, "-y"]
            else:
                cmd = [
                    FFMPEG_BIN, "-err_detect", "ignore_err", "-i", raw_file_path,
                    "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-shortest"
                ] + base_encode_flags + [processed_video_path, "-y"]

        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if os.path.exists(raw_file_path):
            os.remove(raw_file_path)

        dur = get_video_duration(processed_video_path)
        if dur == 0:
            dur = telegram_duration if telegram_duration > 0 else 1

        USER_VIDEOS[user_id].append({
            "path": processed_video_path,
            "duration": dur,
            "type": media_type
        })

        count = len(USER_VIDEOS[user_id])
        total_sec = sum(item["duration"] for item in USER_VIDEOS[user_id])

        await msg.edit_text(
            f"✅ **تمت إضافة العنصر رقم ({count}) بنجاح!**\n"
            f"📌 **النوع:** {media_type} | **المدة:** {dur} ثانية\n"
            f"⏱️ **إجمالي المدة الآن:** {total_sec // 60} دقيقة و {total_sec % 60} ثانية\n\n"
            f"• أرسل **show** أو `/show` لرؤية القائمة.\n"
            f"• أرسل **دمج** أو `/merge` للجمع النهائي."
        )
    except Exception as e:
        await msg.edit_text(f"❌ حدث خطأ غير متوقع: {e}")


# 2️⃣ عرض تفاصيل القائمة
@app.on_message(filters.private & (filters.command(["show", "عرض"]) | filters.regex(r"^(show|عرض|المقاطع)$")))
async def show_list(client, message):
    user_id = message.from_user.id
    items = USER_VIDEOS.get(user_id, [])

    if not items:
        await message.reply_text("📭 لا توجد أي عناصر مضافة حالياً!")
        return

    total_sec = sum(item["duration"] for item in items)
    m = total_sec // 60
    s = total_sec % 60
    text = f"📋 **قائمة العناصر ({len(items)} عناصر):**\n\n"
    for idx, item in enumerate(items, 1):
        text += f"{idx}. **{item['type']}** - المدة: {item['duration']} ثانية\n"
    text += f"\n⏱️ **المدة الإجمالية:** {m} دقيقة و {s} ثانية ({total_sec} ثانية)"
    await message.reply_text(text)


# 3️⃣ عملية الدمج النهائية الخارقة (الآن لا تحذف القائمة عند الفشل)
@app.on_message(filters.private & (filters.command(["merge", "دمج"]) | filters.regex(r"^دمج$")))
async def merge_videos(client, message):
    user_id = message.from_user.id
    video_list = USER_VIDEOS.get(user_id, [])

    if not video_list:
        await message.reply_text("❌ لم ترسل أي عناصر للدمج بعد!")
        return

    if len(video_list) < 2:
        await message.reply_text("⚠️ يجب إرسال عنصرين على الأقل لدمجهما!")
        return

    msg = await message.reply_text("⏳ جاري الدمج بلمح البصر...")

    list_file_path = f"list_{user_id}.txt"
    output_video_path = f"merged_{user_id}.mp4"
    thumb_path = f"thumb_{user_id}.jpg"

    try:
        with open(list_file_path, "w", encoding="utf-8") as f:
            for item in video_list:
                abs_path = os.path.abspath(item["path"]).replace("\\", "/")
                f.write(f"file '{abs_path}'\n")

        # تم استرجاع -c copy للدمج السريع جداً بدون إعادة ضغط ومشاكل
        command = [
            FFMPEG_BIN, "-f", "concat", "-safe", "0",
            "-i", list_file_path,
            "-c", "copy",
            output_video_path, "-y"
        ]

        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if process.returncode != 0:
            err_log = process.stderr.decode('utf-8', errors='ignore')[-300:]
            await msg.edit_text(f"❌ فشل الدمج التقني.\nالسبب:\n`{err_log}`\n\n(ملاحظة: مقاطعك لم تنحذف، يمكنك المحاولة مجدداً أو مسحها بـ /clear)")
            # نحذف ملفات الدمج المؤقتة فقط، ونترك القائمة لليوزر!
            if os.path.exists(list_file_path): os.remove(list_file_path)
            if os.path.exists(output_video_path): os.remove(output_video_path)
            return

        await msg.edit_text("📤 تم الدمج بنجاح! جاري الرفع...")

        total_sec = sum(item["duration"] for item in video_list)
        duration, width, height = get_video_metadata_and_thumb(output_video_path, thumb_path)
        if duration == 0: duration = total_sec

        await client.send_video(
            chat_id=user_id,
            video=output_video_path,
            caption=f"✅ **اكتمل الدمج!**\n🎬 **العدد:** {len(video_list)}\n⏱️ **المدة:** {duration // 60} د و {duration % 60} ث",
            duration=duration, width=width, height=height,
            thumb=thumb_path if os.path.exists(thumb_path) else None,
            supports_streaming=True
        )

        # إذا نجح كل شيء، هنا فقط نقوم بتفريغ القائمة وحذف المقاطع الأصلية
        for item in video_list:
            if os.path.exists(item["path"]):
                os.remove(item["path"])
        USER_VIDEOS[user_id] = []
        
        try: await msg.delete() 
        except: pass

    except Exception as e:
        await msg.edit_text(f"❌ حدث خطأ برمجي: {e}")
    finally:
        if os.path.exists(list_file_path): os.remove(list_file_path)
        if os.path.exists(output_video_path): os.remove(output_video_path)
        if os.path.exists(thumb_path): os.remove(thumb_path)


# 4️⃣ تفريغ القائمة
@app.on_message(filters.private & (filters.command(["clear", "مسح"]) | filters.regex(r"^(تفريغ|مسح|الغاء)$")))
async def clear_videos(client, message):
    user_id = message.from_user.id
    for item in USER_VIDEOS.get(user_id, []):
        if os.path.exists(item["path"]): os.remove(item["path"])
    USER_VIDEOS[user_id] = []
    await message.reply_text("🗑️ تم مسح جميع العناصر بنجاح!")

if __name__ == "__main__":
    print("🤖 Bot is running with Ghost Bug FIXED...")
    app.run()

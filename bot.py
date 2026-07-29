import os
import subprocess
from collections import defaultdict
import imageio_ffmpeg
from pyrogram import Client, filters

# 1️⃣ إعداد البوت
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

app = Client("ultimate_merger", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
USER_FILES = defaultdict(list)

def run_ffmpeg(cmd):
    """تشغيل أمر الفلترة بصمت"""
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# 2️⃣ استقبال المقاطع وتحويلها لصيغة TS المقاومة للأعطال
@app.on_message(filters.private & (filters.video | filters.document | filters.photo | filters.audio | filters.voice))
async def get_media(client, message):
    user = message.from_user.id
    msg = await message.reply_text("⏳ جاري سحب المقطع...")
    
    try:
        dl_path = await message.download()
        # السر هنا: استخدام صيغة ts تمنع أعطال الدمج تماماً
        out_path = f"vid_{message.id}_{user}.ts"
        
        vf = "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2"
        base_cmd = [FFMPEG, "-y", "-err_detect", "ignore_err", "-i", dl_path]
        encode_flags = ["-vf", vf, "-c:v", "libx264", "-preset", "fast", "-r", "30", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-f", "mpegts", out_path]
        
        if message.photo:
            cmd = [FFMPEG, "-y", "-loop", "1", "-t", "3", "-i", dl_path, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-r", "30", "-c:a", "aac", "-f", "mpegts", out_path]
        elif message.audio or message.voice:
            cmd = [FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=black:s=720x1280:r=30", "-i", dl_path, "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-shortest", "-f", "mpegts", out_path]
        else:
            has_audio = "Audio:" in subprocess.run([FFMPEG, "-i", dl_path], capture_output=True, text=True, errors="ignore").stderr
            if has_audio:
                cmd = base_cmd + encode_flags
            else:
                cmd = [FFMPEG, "-y", "-err_detect", "ignore_err", "-i", dl_path, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-r", "30", "-c:a", "aac", "-shortest", "-f", "mpegts", out_path]

        run_ffmpeg(cmd)
        if os.path.exists(dl_path): os.remove(dl_path)
        
        # التأكد إن المقطع ما اخترب وصار حجمه 0
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            USER_FILES[user].append(out_path)
            await msg.edit_text(f"✅ تم إضافة المقطع بنجاح! (العدد الحالي: {len(USER_FILES[user])})\n\n- أرسل /merge لدمجها وإرسالها.")
        else:
            await msg.edit_text("❌ المقطع هذا فيه مشكلة تقنية وما نفع نظيفه، جرب مقطع غيره.")
            
    except Exception as e:
        await msg.edit_text("❌ فشل تحميل المقطع، أرسله مرة ثانية.")

# 3️⃣ دمج المقاطع في ثانية وإرسالها كـ MP4
@app.on_message(filters.private & filters.command(["merge", "دمج"]))
async def merge_media(client, message):
    user = message.from_user.id
    files = USER_FILES.get(user, [])
    
    if len(files) < 2:
        return await message.reply_text("❌ أرسل مقطعين على الأقل عشان أقدر أدمجهم!")
        
    msg = await message.reply_text("⏳ جاري الدمج (راح يخلص بلمح البصر)...")
    list_txt = f"list_{user}.txt"
    out_merge = f"final_{user}.mp4"
    
    try:
        with open(list_txt, "w") as f:
            for path in files:
                f.write(f"file '{os.path.abspath(path)}'\n")
                
        # الدمج السريع للـ TS بدون أخطاء
        cmd = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_txt, "-c", "copy", "-bsf:a", "aac_adtstoasc", out_merge]
        run_ffmpeg(cmd)
        
        if os.path.exists(out_merge) and os.path.getsize(out_merge) > 0:
            await msg.edit_text("📤 تم الدمج! جاري إرسال المقطع لك...")
            await client.send_video(chat_id=user, video=out_merge, caption="✅ تفضل مقطعك!")
        else:
            await msg.edit_text("❌ فشل الدمج النهائي للأسف.")
        
    finally:
        # تنظيف كل شيء بعد الإرسال
        for path in files:
            if os.path.exists(path): os.remove(path)
        if os.path.exists(list_txt): os.remove(list_txt)
        if os.path.exists(out_merge): os.remove(out_merge)
        USER_FILES[user] = []
        try: await msg.delete() 
        except: pass

# 4️⃣ مسح القائمة
@app.on_message(filters.private & filters.command(["clear", "مسح"]))
async def clear_media(client, message):
    user = message.from_user.id
    for path in USER_FILES.get(user, []):
        if os.path.exists(path): os.remove(path)
    USER_FILES[user] = []
    await message.reply_text("🗑️ تم مسح كل شيء، القائمة فاضية الآن.")


if __name__ == "__main__":
    print("🤖 البوت شغال بنظام الـ TS النهائي...")
    app.run()

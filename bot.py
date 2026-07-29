import asyncio
import os
from collections import defaultdict
import imageio_ffmpeg
from pyrogram import Client, filters
from pyrogram.errors import MessageNotModified

# 1️⃣ إعداد البوت
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

app = Client("railway_optimal_merger", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
USER_FILES = defaultdict(list)

# 2️⃣ دوال الخلفية (عشان البوت ما يعلق أبداً على Railway)
async def has_audio_async(file_path):
    """فحص الصوت في الخلفية بدون تجميد البوت"""
    cmd = [FFMPEG, "-i", file_path]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    return b"Audio:" in stderr

async def run_cmd_async(cmd):
    """تشغيل أوامر الفلترة في الخلفية"""
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    return proc.returncode

# 3️⃣ استلام المقاطع والمعالجة السريعة
@app.on_message(filters.private & (filters.video | filters.document | filters.photo | filters.audio | filters.voice))
async def process_media(client, message):
    user = message.from_user.id
    
    # تنبيه أولي بالتحميل
    msg = await message.reply_text("📥 جاري تحميل المقطع من تليجرام...")
    
    try:
        dl_path = await message.download()
        out_path = f"vid_{message.id}_{user}.ts"
        vf = "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2"
        
        # تنبيه ببدء المعالجة عشان تعرف إنه شغال وما علق
        try:
            await msg.edit_text("⚙️ جاري المعالجة والتجهيز...")
        except MessageNotModified:
            pass
        
        if message.photo:
            cmd = [FFMPEG, "-y", "-loop", "1", "-t", "3", "-i", dl_path, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-r", "30", "-c:a", "aac", "-f", "mpegts", out_path]
        elif message.audio or message.voice:
            cmd = [FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=black:s=720x1280:r=30", "-i", dl_path, "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", "-f", "mpegts", out_path]
        else:
            # الفحص صار Async يعني البوت مستحيل يعلق هنا
            has_a = await has_audio_async(dl_path)
            if has_a:
                cmd = [FFMPEG, "-y", "-err_detect", "ignore_err", "-i", dl_path, "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-r", "30", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-f", "mpegts", out_path]
            else:
                cmd = [FFMPEG, "-y", "-err_detect", "ignore_err", "-i", dl_path, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-r", "30", "-c:a", "aac", "-shortest", "-f", "mpegts", out_path]

        # تنفيذ المعالجة
        returncode = await run_cmd_async(cmd)
        
        if os.path.exists(dl_path): 
            os.remove(dl_path)
        
        if returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            USER_FILES[user].append(out_path)
            await msg.edit_text(f"✅ تم الإضافة بنجاح!\nالعدد في القائمة: ({len(USER_FILES[user])})\n\n• أرسل /merge للدمج\n• أرسل /clear للمسح")
        else:
            await msg.edit_text("❌ حدث خطأ أثناء معالجة هذا الملف، جرب مقطعاً آخر.")
            
    except Exception as e:
        try: 
            await msg.edit_text("❌ حدث خطأ أثناء التحميل.")
        except MessageNotModified: 
            pass

# 4️⃣ دمج المقاطع
@app.on_message(filters.private & filters.command(["merge", "دمج"]))
async def merge_media(client, message):
    user = message.from_user.id
    files = USER_FILES.get(user, [])
    
    if len(files) < 2:
        return await message.reply_text("❌ أرسل مقطعين على الأقل لدمجهما!")
        
    msg = await message.reply_text("⏳ جاري الدمج الفوري...")
    list_txt = f"list_{user}.txt"
    out_merge = f"final_{user}.mp4"
    
    try:
        with open(list_txt, "w") as f:
            for path in files:
                f.write(f"file '{os.path.abspath(path)}'\n")
                
        cmd = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_txt, "-c", "copy", "-bsf:a", "aac_adtstoasc", out_merge]
        returncode = await run_cmd_async(cmd)
        
        if returncode == 0 and os.path.exists(out_merge) and os.path.getsize(out_merge) > 0:
            await msg.edit_text("📤 تم الدمج! جاري إرسال المقطع (قد يستغرق الرفع ثواني حسب حجمه)...")
            await client.send_video(chat_id=user, video=out_merge, caption="✅ تفضل مقطعك المدمج!")
        else:
            await msg.edit_text("❌ فشل الدمج النهائي.")
            
    except Exception as e:
        try: 
            await msg.edit_text("❌ خطأ غير متوقع أثناء الدمج.")
        except MessageNotModified: 
            pass
    finally:
        for path in files:
            if os.path.exists(path): os.remove(path)
        if os.path.exists(list_txt): os.remove(list_txt)
        if os.path.exists(out_merge): os.remove(out_merge)
        USER_FILES[user] = []
        try: await msg.delete()
        except: pass

# 5️⃣ مسح القائمة
@app.on_message(filters.private & filters.command(["clear", "مسح"]))
async def clear_media(client, message):
    user = message.from_user.id
    for path in USER_FILES.get(user, []):
        if os.path.exists(path): os.remove(path)
    USER_FILES[user] = []
    await message.reply_text("🗑️ تم مسح القائمة بنجاح.")

if __name__ == "__main__":
    print("🤖 Bot is running smoothly on Railway...")
    app.run()

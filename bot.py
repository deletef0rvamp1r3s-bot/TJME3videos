import os
import subprocess
from collections import defaultdict
from pyrogram import Client, filters

# بيانات بوتك
API_ID = 35909411
API_HASH = "d2e7f09b5aaeaf64904b8afd6b8057c7"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ضع_توكن_البوت_هنا")

app = Client("video_merger_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# قاموس لحفظ قائمة المقاطع لكل مستخدم على حدة
USER_VIDEOS = defaultdict(list)


# 1️⃣ استلام المقاطع وحفظها بالترتيب
@app.on_message(filters.private & (filters.video | filters.document))
async def collect_video(client, message):
    user_id = message.from_user.id

    # التأكد من أن المستند أرسل كفيديو
    if message.document and not (message.document.mime_type or "").startswith("video/"):
        return

    msg = await message.reply_text("⏳ جاري حفظ المقطع في قائمة الدمج...")

    try:
        # تحميل المقطع إلى سيرفر البوت
        file_path = await message.download()
        USER_VIDEOS[user_id].append(file_path)

        count = len(USER_VIDEOS[user_id])
        await msg.edit_text(
            f"✅ **تمت إضافة المقطع رقم ({count}) بنجاح!**\n\n"
            f"• أرسل المزيد من المقاطع بنفس الترتيب الذي تريده.\n"
            f"• أرسل كلمة **دمج** أو أمر `/merge` لجمعها بمقطع واحد.\n"
            f"• للتراجع وتفريغ القائمة أرسل `/clear`."
        )
    except Exception as e:
        await msg.edit_text(f"❌ حدث خطأ أثناء تحميل المقطع: {e}")


# 2️⃣ عملية دمج المقاطع معاً
@app.on_message(filters.private & (filters.command(["merge", "دمج"]) | filters.regex(r"^دمج$")))
async def merge_videos(client, message):
    user_id = message.from_user.id
    video_list = USER_VIDEOS.get(user_id, [])

    if not video_list:
        await message.reply_text("❌ لم ترسل أي مقاطع للدمج بعد!")
        return

    if len(video_list) < 2:
        await message.reply_text("⚠️ يجب إرسال مقطعين على الأقل لدمجهما!")
        return

    msg = await message.reply_text(f"⏳ جاري دمج {len(video_list)} مقطع في فيديو واحد...")

    list_file_path = f"list_{user_id}.txt"
    output_video_path = f"merged_{user_id}.mp4"

    try:
        # إنشاء ملف النص المطلوب لأداة FFmpeg
        with open(list_file_path, "w", encoding="utf-8") as f:
            for path in video_list:
                abs_path = os.path.abspath(path).replace("\\", "/")
                f.write(f"file '{abs_path}'\n")

        # أمر FFmpeg لدمج المقاطع فورياً بدون إعادة ترميز (خفيف جداً وسريع)
        command = [
            "ffmpeg", "-f", "concat", "-safe", "0",
            "-i", list_file_path,
            "-c", "copy",
            output_video_path, "-y"
        ]

        # تنفيذ عملية الدمج
        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if process.returncode != 0:
            await msg.edit_text("❌ حدث خطأ أثناء الدمج. تأكد أن المقاطع من نفس الصيغة والجودة.")
            return

        await msg.edit_text("📤 جاري رفع المقطع المدموج النهائي...")

        # إرسال المقطع المدموج للمستخدم
        await client.send_video(
            chat_id=user_id,
            video=output_video_path,
            caption=f"✅ **تم دمج {len(video_list)} مقطع بنجاح!**"
        )

    except Exception as e:
        await msg.edit_text(f"❌ حدث خطأ غير متوقع: {e}")

    finally:
        # 🧹 تنظيف سيرفر Railway وحذف جميع المقاطع المؤقتة
        for path in video_list:
            if os.path.exists(path):
                os.remove(path)

        if os.path.exists(list_file_path):
            os.remove(list_file_path)

        if os.path.exists(output_video_path):
            os.remove(output_video_path)

        # إعادة إعادة تعيين قائمة المستخدم
        USER_VIDEOS[user_id] = []
        await msg.delete()


# 3️⃣ مسح القائمة والتراجع
@app.on_message(filters.private & (filters.command(["clear", "مسح", "إلغاء"]) | filters.regex(r"^(تفريغ|إلغاء|الغاء)$")))
async def clear_videos(client, message):
    user_id = message.from_user.id
    video_list = USER_VIDEOS.get(user_id, [])

    for path in video_list:
        if os.path.exists(path):
            os.remove(path)

    USER_VIDEOS[user_id] = []
    await message.reply_text("🗑️ تم مسح جميع المقاطع المحفوظة، يمكنك البدء من جديد!")


if __name__ == "__main__":
    print("🤖 Video Merger Bot is running...")
    app.run()

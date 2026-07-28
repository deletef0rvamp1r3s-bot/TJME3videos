import os
import re
from threading import Thread
from flask import Flask
from pyrogram import Client, filters
from gradio_client import Client as GradioClient, handle_file

web_server = Flask(__name__)

@web_server.route('/')
def home():
    return "Bot is alive and running on Railway!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    web_server.run(host="0.0.0.0", port=port)

# متغيرات البيئة (سيتم وضعها في Railway)
API_ID = int(os.environ.get("API_ID", "35909411"))
API_HASH = os.environ.get("API_HASH", "d2e7f09b5aaeaf64904b8afd6b8057c7")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
HF_TOKEN = os.environ.get("HF_TOKEN", "") # اختياري لكن مفضل لتخطي الطوابير

app = Client("my_account", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)

# اسم المساحة على Hugging Face (مثال: "username/space-name")
HF_SPACE_NAME = os.environ.get("HF_SPACE_NAME", "your-target-space-here")

@app.on_message(filters.me & filters.regex(r"^https://t\.me/(c/)?(.+)/(\d+)$"))
async def fetch_restricted_video(client, message):
    link = message.text
    match = re.search(r"^https://t\.me/(c/)?(.+)/(\d+)$", link)
    if not match:
        return
        
    is_private = match.group(1)
    chat_identifier = match.group(2)
    msg_id = int(match.group(3))
    
    if is_private:
        chat_id = int(f"-100{chat_identifier}")
    else:
        chat_id = chat_identifier
        
    notification = await message.reply_text("⏳ جاري جلب المقطع من القناة...")
    
    try:
        target_msg = await client.get_messages(chat_id, msg_id)
    except Exception as e:
        await notification.edit_text(f"❌ حدث خطأ في الوصول للقناة (تأكد أن حسابك منضم لها):\n`{e}`")
        return

    try:
        if target_msg.video:
            await notification.edit_text("⏳ جاري تحميل المقطع لسيرفر Railway...")
            original_file_path = await target_msg.download()
            
            await notification.edit_text("🤖 جاري إرسال المقطع لـ Hugging Face لمعالجته (قد يأخذ وقتاً)...")
            
            try:
                # الاتصال بالمساحة (مع التوكن إذا وجد)
                hf_client = GradioClient(HF_SPACE_NAME, hf_token=HF_TOKEN if HF_TOKEN else None)
                
                # إرسال الفيديو. ملاحظة: تأكد أن api_name هو فعلاً /predict في المساحة التي اخترتها
                result = hf_client.predict(
                    video=handle_file(original_file_path),
                    api_name="/predict" 
                )
                
                clean_file_path = result
                await notification.edit_text("✅ تمت المعالجة بنجاح! جاري الإرسال إليك...")
                
            except Exception as ai_error:
                await notification.edit_text(f"⚠️ فشلت المعالجة بالذكاء الاصطناعي، سأرسل المقطع الأصلي.\nالسبب: `{ai_error}`")
                clean_file_path = original_file_path 
            
            duration = target_msg.video.duration or 0
            width = target_msg.video.width or 0
            height = target_msg.video.height or 0
            
            thumb_path = None
            if target_msg.video.thumbs:
                thumb_path = await client.download_media(target_msg.video.thumbs[0].file_id)
            
            # إرسال الفيديو إليك
            await client.send_video(
                chat_id="me", 
                video=clean_file_path, 
                caption="✅ تم السحب والمعالجة بنجاح!",
                duration=duration,
                width=width,
                height=height,
                thumb=thumb_path
            )
            
            # تنظيف الملفات
            if os.path.exists(original_file_path): os.remove(original_file_path)
            if clean_file_path != original_file_path and os.path.exists(clean_file_path): os.remove(clean_file_path)
            if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
                
            await notification.delete()
            
        else:
            await notification.edit_text("❌ الرابط لا يحتوي على مقطع فيديو.")
    except Exception as e:
        await notification.edit_text(f"❌ حدث خطأ أثناء التحميل: `{e}`")

if __name__ == "__main__":
    Thread(target=run_web_server).start()
    app.run()

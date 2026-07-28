import os
from threading import Thread
from flask import Flask
from pyrogram import Client, filters
from gradio_client import Client as GradioClient, handle_file

web_server = Flask(__name__)

@web_server.route('/')
def home():
    return "Bot is running perfectly!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    web_server.run(host="0.0.0.0", port=port)

API_ID = int(os.environ.get("API_ID", "35909411"))
API_HASH = os.environ.get("API_HASH", "d2e7f09b5aaeaf64904b8afd6b8057c7")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "") 
HF_SPACE_NAME = os.environ.get("HF_SPACE_NAME", "")

app = Client("watermark_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.video & filters.private)
async def process_video(client, message):
    msg = await message.reply_text("⏳ جاري سحب المقطع لسيرفر البوت...")
    
    try:
        vid_path = await message.download()
        await msg.edit_text("🤖 جاري إرسال المقطع للذكاء الاصطناعي (Hugging Face) للمعالجة...")
        
        # الاتصال بالمساحة
        hf_client = GradioClient(HF_SPACE_NAME)
        
        # إرسال الفيديو فقط (للمساحات الأوتوماتيكية)
        result = hf_client.predict(
            video=handle_file(vid_path),
            api_name="/predict" 
        )
        
        await msg.edit_text("✅ تمت المعالجة! جاري الرفع لتليجرام...")
        
        await client.send_video(
            chat_id=message.chat.id, 
            video=result, 
            caption="✅ تم مسح الحقوق!"
        )
        
        if os.path.exists(vid_path): os.remove(vid_path)
        if os.path.exists(result): os.remove(result)
        await msg.delete()
        
    except Exception as e:
        await msg.edit_text(f"❌ خطأ في المعالجة: `{e}`")

if __name__ == "__main__":
    Thread(target=run_web_server).start()
    app.run()

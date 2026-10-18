import os
import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple
from datetime import datetime
import subprocess
import shutil

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

# إعدادات التسجيل
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# إعدادات البوت
API_ID = "YOUR_API_ID"
API_HASH = "YOUR_API_HASH"
BOT_TOKEN = "YOUR_BOT_TOKEN"

# إعدادات الملفات
TEMP_DIR = Path("temp_videos")
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
DEFAULT_DURATION = 5  # ثواني
MAX_CLIPS = 20
OUTPUT_WIDTH = 720
OUTPUT_HEIGHT = 1280
CRF = 28
PRESET = "ultrafast"

# قاموس لتتبع المستخدمين النشطين
active_users = {}
user_locks = {}

# إنشاء مجلد التخزين المؤقت
TEMP_DIR.mkdir(exist_ok=True)

app = Client("video_merger_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


def get_user_lock(user_id: int) -> asyncio.Lock:
    """الحصول على قفل للمستخدم لمنع المعالجة المتوازية"""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


async def get_video_dimensions(file_path: str) -> Optional[Tuple[int, int]]:
    """الحصول على أبعاد الفيديو"""
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            file_path
        ]
        result = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await result.communicate()
        
        if result.returncode == 0:
            dimensions = stdout.decode().strip().split(',')
            return (int(dimensions[0]), int(dimensions[1]))
    except Exception as e:
        logger.error(f"خطأ في الحصول على الأبعاد: {e}")
    
    return None


def get_optimal_dimensions(width: int, height: int) -> Tuple[str, int, int]:
    """تحديد فلتر التحجيم المناسب بناءً على نسبة العرض إلى الارتفاع"""
    aspect_ratio = width / height if height > 0 else 0
    target_ratio = OUTPUT_WIDTH / OUTPUT_HEIGHT  # 0.5625 (9:16)
    
    # فيديو عمودي (تقريباً 9:16)
    if 0.5 < aspect_ratio < 0.65:
        # بدون حشو، فقط تغيير الحجم
        return f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}", OUTPUT_WIDTH, OUTPUT_HEIGHT
    
    # فيديو أفقي أو آخر - إضافة حشو أسود علوي وسفلي فقط
    else:
        # حساب الحجم المناسب للعرض
        new_width = OUTPUT_WIDTH
        new_height = int(OUTPUT_WIDTH / aspect_ratio)
        
        # إذا كان الارتفاع أكبر من المطلوب، قلل الحجم
        if new_height > OUTPUT_HEIGHT:
            new_height = OUTPUT_HEIGHT
            new_width = int(OUTPUT_HEIGHT * aspect_ratio)
        
        # إضافة حشو أسود
        pad_height = (OUTPUT_HEIGHT - new_height) // 2
        filter_str = f"scale={new_width}:{new_height},pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:0:{pad_height}:black"
        
        return filter_str, OUTPUT_WIDTH, OUTPUT_HEIGHT


async def download_file(client: Client, message: Message, user_id: int) -> Optional[str]:
    """تحميل الملف من رسالة المستخدم"""
    try:
        file_path = None
        
        # تحديد نوع الملف وتحميله
        if message.video:
            if message.video.file_size > MAX_FILE_SIZE:
                await message.reply_text(f"❌ الملف كبير جداً! الحد الأقصى: {MAX_FILE_SIZE // 1024 // 1024}MB")
                return None
            file_path = await message.download(file_name=f"{TEMP_DIR}/video_{user_id}_{datetime.now().timestamp()}.mp4")
        
        elif message.document:
            if message.document.file_size > MAX_FILE_SIZE:
                await message.reply_text(f"❌ الملف كبير جداً! الحد الأقصى: {MAX_FILE_SIZE // 1024 // 1024}MB")
                return None
            file_path = await message.download(file_name=f"{TEMP_DIR}/doc_{user_id}_{datetime.now().timestamp()}")
        
        elif message.photo:
            file_path = await message.download(file_name=f"{TEMP_DIR}/photo_{user_id}_{datetime.now().timestamp()}.jpg")
        
        elif message.audio:
            if message.audio.file_size > MAX_FILE_SIZE:
                await message.reply_text(f"❌ الملف كبير جداً! الحد الأقصى: {MAX_FILE_SIZE // 1024 // 1024}MB")
                return None
            file_path = await message.download(file_name=f"{TEMP_DIR}/audio_{user_id}_{datetime.now().timestamp()}.mp3")
        
        elif message.voice:
            file_path = await message.download(file_name=f"{TEMP_DIR}/voice_{user_id}_{datetime.now().timestamp()}.ogg")
        
        return file_path
    
    except Exception as e:
        logger.error(f"خطأ في التحميل: {e}")
        return None


async def convert_to_video(file_path: str, duration: int, output_path: str) -> bool:
    """تحويل أي ملف إلى فيديو"""
    try:
        dimensions = await get_video_dimensions(file_path)
        
        if dimensions:
            width, height = dimensions
            scale_filter, _, _ = get_optimal_dimensions(width, height)
        else:
            scale_filter = f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}"
        
        cmd = [
            "ffmpeg",
            "-loop", "1",
            "-i", file_path,
            "-vf", scale_filter,
            "-c:v", "libx264",
            "-preset", PRESET,
            "-crf", str(CRF),
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            output_path
        ]
        
        result = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await result.communicate()
        
        if result.returncode != 0:
            logger.error(f"خطأ FFmpeg: {stderr.decode()}")
            return False
        
        return True
    
    except Exception as e:
        logger.error(f"خطأ في التحويل: {e}")
        return False


async def merge_videos(video_files: list, output_path: str, progress_callback=None) -> bool:
    """دمج عدة ملفات فيديو"""
    try:
        # إنشاء ملف قائمة FFmpeg
        concat_file = f"{TEMP_DIR}/concat_{datetime.now().timestamp()}.txt"
        
        with open(concat_file, 'w', encoding='utf-8') as f:
            for video in video_files:
                f.write(f"file '{video}'\n")
        
        # أمر FFmpeg للدمج
        cmd = [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-c:v", "libx264",
            "-preset", PRESET,
            "-crf", str(CRF),
            "-c:a", "aac",
            "-b:a", "128k",
            "-pix_fmt", "yuv420p",
            output_path
        ]
        
        result = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # مراقبة التقدم
        stdout, stderr = await result.communicate()
        
        if progress_callback:
            await progress_callback(100)
        
        # تنظيف ملف القائمة
        Path(concat_file).unlink(missing_ok=True)
        
        if result.returncode != 0:
            logger.error(f"خطأ FFmpeg: {stderr.decode()}")
            return False
        
        return True
    
    except Exception as e:
        logger.error(f"خطأ في دمج الفيديوهات: {e}")
        return False


async def cleanup_user_files(user_id: int):
    """تنظيف ملفات المستخدم المؤقتة"""
    try:
        for file in TEMP_DIR.glob(f"*_{user_id}_*"):
            file.unlink(missing_ok=True)
        
        # تنظيف ملفات قديمة (أكثر من ساعة)
        current_time = datetime.now().timestamp()
        for file in TEMP_DIR.glob("*"):
            if file.is_file() and (current_time - file.stat().st_mtime) > 3600:
                file.unlink(missing_ok=True)
    
    except Exception as e:
        logger.error(f"خطأ في التنظيف: {e}")


async def update_progress(message: Message, current: int, total: int):
    """تحديث شريط التقدم"""
    try:
        percentage = (current / total) * 100
        filled = int(percentage / 5)
        bar = "█" * filled + "░" * (20 - filled)
        
        progress_text = (
            f"🔄 جاري المعالجة...\n\n"
            f"[{bar}] {percentage:.1f}%\n"
            f"({current}/{total})"
        )
        
        await message.edit_text(progress_text)
    except Exception as e:
        logger.error(f"خطأ في تحديث التقدم: {e}")


@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    """معالج بدء البوت"""
    await message.reply_text(
        "🎬 **مرحباً بك في بوت دمج الفيديوهات!**\n\n"
        "📝 **الميزات:**\n"
        "• دمج عدة فيديوهات في فيديو واحد\n"
        "• معالجة نسبة العرض إلى الارتفاع تلقائياً\n"
        "• دعم الصور والصوت والصوت العميق\n"
        "• ضغط فعال للملفات\n\n"
        "📌 **الخطوات:**\n"
        "1️⃣ استخدم /new لبدء مشروع جديد\n"
        "2️⃣ أرسل الملفات التي تريد دمجها\n"
        "3️⃣ استخدم /merge لدمج الملفات\n"
        "4️⃣ استخدم /cancel لإلغاء العملية\n\n"
        "⚙️ **الإعدادات:**\n"
        "• الدقة: 720x1280 (9:16)\n"
        "• الضغط: CRF 28\n"
        "• الحد الأقصى للملف: 100MB\n"
        "• الحد الأقصى للملفات: 20 ملف"
    )


@app.on_message(filters.command("new"))
async def new_project(client: Client, message: Message):
    """بدء مشروع جديد"""
    user_id = message.from_user.id
    
    # تنظيف الملفات القديمة
    await cleanup_user_files(user_id)
    
    active_users[user_id] = []
    
    await message.reply_text(
        "✅ **تم بدء مشروع جديد!**\n\n"
        "📤 أرسل الآن الملفات التي تريد دمجها:\n"
        "• الفيديوهات\n"
        "• الصور\n"
        "• الملفات الصوتية\n\n"
        "بعد إرسال جميع الملفات، استخدم /merge لدمجها"
    )


@app.on_message(filters.command("cancel"))
async def cancel_project(client: Client, message: Message):
    """إلغاء المشروع الحالي"""
    user_id = message.from_user.id
    
    if user_id in active_users:
        # تنظيف الملفات
        for file in active_users[user_id]:
            Path(file).unlink(missing_ok=True)
        
        del active_users[user_id]
        await message.reply_text("❌ تم إلغاء المشروع والملفات المؤقتة")
    else:
        await message.reply_text("⚠️ لا توجد مشاريع نشطة")


@app.on_message(filters.command("merge"))
async def merge_handler(client: Client, message: Message):
    """دمج الملفات"""
    user_id = message.from_user.id
    
    # التحقق من القفل والملفات النشطة
    if user_id not in active_users or not active_users[user_id]:
        await message.reply_text("⚠️ لا توجد ملفات للدمج. استخدم /new أولاً!")
        return
    
    lock = get_user_lock(user_id)
    
    if lock.locked():
        await message.reply_text("⏳ جارٍ معالجة ملفات أخرى... يرجى الانتظار")
        return
    
    async with lock:
        try:
            video_files = active_users[user_id]
            
            if len(video_files) < 2:
                await message.reply_text("⚠️ يجب تحديد ملفين على الأقل للدمج")
                return
            
            if len(video_files) > MAX_CLIPS:
                await message.reply_text(f"⚠️ الحد الأقصى للملفات: {MAX_CLIPS}")
                return
            
            progress_msg = await message.reply_text("🔄 جاري بدء المعالجة...")
            
            # معالجة الملفات
            processed_videos = []
            
            for index, file_path in enumerate(video_files):
                try:
                    await update_progress(progress_msg, index, len(video_files))
                    
                    # تحويل الملف إلى فيديو إذا لزم الأمر
                    if file_path.endswith(('.mp4', '.mkv', '.avi', '.mov')):
                        processed_videos.append(file_path)
                    else:
                        # تحويل الصور والملفات الأخرى إلى فيديو
                        temp_video = f"{TEMP_DIR}/converted_{user_id}_{index}.mp4"
                        success = await convert_to_video(file_path, DEFAULT_DURATION, temp_video)
                        
                        if success:
                            processed_videos.append(temp_video)
                        else:
                            await message.reply_text(f"❌ فشل تحويل الملف #{index + 1}")
                            continue
                except Exception as e:
                    logger.error(f"خطأ في معالجة الملف: {e}")
                    await message.reply_text(f"❌ خطأ في معالجة الملف #{index + 1}")
            
            if not processed_videos:
                await message.reply_text("❌ فشل معالجة جميع الملفات")
                return
            
            await update_progress(progress_msg, len(video_files), len(video_files))
            
            # دمج الفيديوهات
            await progress_msg.edit_text("🎬 جاري دمج الفيديوهات...")
            
            output_file = f"{TEMP_DIR}/output_{user_id}_{datetime.now().timestamp()}.mp4"
            success = await merge_videos(processed_videos, output_file)
            
            if not success:
                await message.reply_text("❌ فشل دمج الفيديوهات")
                return
            
            # التحقق من حجم الملف
            output_size = Path(output_file).stat().st_size
            
            if output_size > MAX_FILE_SIZE:
                await message.reply_text(f"❌ الملف الناتج كبير جداً: {output_size // 1024 // 1024}MB")
                Path(output_file).unlink(missing_ok=True)
                return
            
            # إرسال الفيديو
            await progress_msg.edit_text("📤 جاري إرسال الفيديو...")
            
            try:
                await client.send_video(
                    chat_id=message.chat.id,
                    video=output_file,
                    caption="✅ تم دمج الفيديوهات بنجاح!",
                    supports_streaming=True
                )
                
                await progress_msg.delete()
                
            except Exception as e:
                logger.error(f"خطأ في الإرسال: {e}")
                await message.reply_text(f"❌ فشل إرسال الفيديو: {e}")
            
            finally:
                # تنظيف الملفات
                Path(output_file).unlink(missing_ok=True)
                for video in processed_videos:
                    Path(video).unlink(missing_ok=True)
                
                if user_id in active_users:
                    del active_users[user_id]
        
        except Exception as e:
            logger.error(f"خطأ عام: {e}")
            await message.reply_text(f"❌ حدث خطأ: {e}")


@app.on_message(filters.video | filters.document | filters.photo | filters.audio | filters.voice)
async def handle_files(client: Client, message: Message):
    """معالج استقبال الملفات"""
    user_id = message.from_user.id
    
    # التحقق من وجود مشروع نشط
    if user_id not in active_users:
        await message.reply_text("⚠️ استخدم /new أولاً لبدء مشروع جديد!")
        return
    
    # التحقق من الحد الأقصى للملفات
    if len(active_users[user_id]) >= MAX_CLIPS:
        await message.reply_text(f"⚠️ وصلت إلى الحد الأقصى للملفات ({MAX_CLIPS})")
        return
    
    # التحقق من القفل
    lock = get_user_lock(user_id)
    if lock.locked():
        await message.reply_text("⏳ جاري معالجة... يرجى الانتظار")
        return
    
    try:
        # تحميل الملف
        file_path = await download_file(client, message, user_id)
        
        if file_path:
            active_users[user_id].append(file_path)
            
            # عرض حالة المشروع
            status_text = (
                f"✅ تم إضافة الملف بنجاح!\n\n"
                f"📊 الملفات المضافة: {len(active_users[user_id])}/{MAX_CLIPS}\n\n"
            )
            
            for i, file in enumerate(active_users[user_id], 1):
                file_name = Path(file).name
                status_text += f"{i}. {file_name}\n"
            
            status_text += (
                f"\n💡 الخيارات:\n"
                f"• أرسل المزيد من الملفات\n"
                f"• استخدم /merge لدمج الملفات\n"
                f"• استخدم /cancel لإلغاء المشروع"
            )
            
            await message.reply_text(status_text)
        else:
            await message.reply_text("❌ فشل تحميل الملف. تحقق من حجمه!")
    
    except Exception as e:
        logger.error(f"خطأ: {e}")
        await message.reply_text(f"❌ حدث خطأ: {e}")


@app.on_message(filters.command("help"))
async def help_handler(client: Client, message: Message):
    """معالج المساعدة"""
    await message.reply_text(
        "📚 **دليل الاستخدام:**\n\n"
        "🎯 **الأوامر:**\n"
        "/start - بدء البوت\n"
        "/new - مشروع جديد\n"
        "/merge - دمج الملفات\n"
        "/cancel - إلغاء المشروع\n"
        "/help - هذه الرسالة\n\n"
        "📋 **الملفات المدعومة:**\n"
        "• الفيديو: MP4, MKV, AVI, MOV\n"
        "• الصور: JPG, PNG, GIF\n"
        "• الصوت: MP3, WAV, OGG\n"
        "• الملفات الصوتية: OGG\n\n"
        "⚠️ **القيود:**\n"
        f"• الحد الأقصى للملف: {MAX_FILE_SIZE // 1024 // 1024}MB\n"
        f"• الحد الأقصى للملفات: {MAX_CLIPS}\n"
        f"• مدة الصور الافتراضية: {DEFAULT_DURATION}ثانية\n\n"
        "✨ **الميزات:**\n"
        "• دعم نسب عرض مختلفة\n"
        "• ضغط فعال (CRF 28)\n"
        "• معالجة متزامنة آمنة\n"
        "• تنظيف تلقائي للملفات المؤقتة"
    )


@app.on_message(filters.command("status"))
async def status_handler(client: Client, message: Message):
    """معالج حالة المشروع"""
    user_id = message.from_user.id
    
    if user_id not in active_users:
        await message.reply_text("⚠️ لا يوجد مشروع نشط حالياً")
        return
    
    files = active_users[user_id]
    
    if not files:
        await message.reply_text("⚠️ لم تضف أي ملفات بعد")
        return
    
    status_text = f"📊 **حالة المشروع:**\n\n"
    status_text += f"الملفات المضافة: {len(files)}/{MAX_CLIPS}\n\n"
    
    for i, file_path in enumerate(files, 1):
        try:
            file_size = Path(file_path).stat().st_size / 1024 / 1024
            file_name = Path(file_path).name
            status_text += f"{i}. {file_name} ({file_size:.2f}MB)\n"
        except:
            pass
    
    status_text += (
        f"\n🎯 الخطوات التالية:\n"
        f"• /merge - لدمج الملفات\n"
        f"• /cancel - لإلغاء المشروع"
    )
    
    await message.reply_text(status_text)


def main():
    """تشغيل البوت"""
    logger.info("🚀 بدء تشغيل بوت دمج الفيديوهات...")
    
    try:
        app.run()
    except KeyboardInterrupt:
        logger.info("⛔ تم إيقاف البوت")
    except Exception as e:
        logger.error(f"خطأ: {e}")


if __name__ == "__main__":
    main()


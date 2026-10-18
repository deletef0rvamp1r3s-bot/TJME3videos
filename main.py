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

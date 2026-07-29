# 2. Process media: NO STRETCH, Fill Width (No Side Borders), Pad Top/Bottom if needed
@app.on_message(filters.private & (filters.video | filters.document | filters.photo | filters.audio | filters.voice))
async def process_media(client, message):
    user = message.from_user.id
    msg = await message.reply_text("📥 **Downloading media from Telegram...**")
    
    try:
        dl_path = await message.download()
        out_path = f"vid_{message.id}_{user}.mp4"
        
        duration = await get_duration_async(dl_path)
        if duration == 0:
            duration = 3.0
            
        # الفلتر الجديد: يثبت العرض على 720 دائماً (بدون حواف جانبية)، وإذا المقطع بالعرض يحط أسود فوق وتحت، وإذا طويل بزيادة يقص الزايد من فوق وتحت للحفاظ على الجودة بدون تمطيط
        scale_filter = "scale=720:-2,crop=720:'min(1280,ih)',pad=720:1280:0:'(1280-ih)/2':color=black,fps=30,setsar=1"
            
        if message.photo:
            cmd = [
                FFMPEG, "-y", "-progress", "pipe:1", "-loop", "1", "-t", "3", "-i", dl_path,
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-vf", scale_filter,
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", out_path
            ]
        elif message.audio or message.voice:
            cmd = [
                FFMPEG, "-y", "-progress", "pipe:1", "-f", "lavfi", "-i", "color=c=black:s=720x1280:r=30",
                "-i", dl_path,
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", out_path
            ]
        else:
            has_a = await has_audio_async(dl_path)
            if has_a:
                cmd = [
                    FFMPEG, "-y", "-progress", "pipe:1", "-err_detect", "ignore_err", "-i", dl_path,
                    "-vf", scale_filter,
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", 
                    "-c:a", "aac", "-ar", "44100", "-ac", "2", "-video_track_timescale", "90000", out_path
                ]
            else:
                cmd = [
                    FFMPEG, "-y", "-progress", "pipe:1", "-err_detect", "ignore_err", "-i", dl_path,
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-vf", scale_filter,
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", "-video_track_timescale", "90000", out_path
                ]

        returncode = await run_cmd_with_progress(cmd, duration, msg, "⚙️ **Processing Media (Applying Custom Aspect Ratio)...**")
        
        if os.path.exists(dl_path): 
            os.remove(dl_path)
        
        if returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            USER_FILES[user].append(out_path)
            await msg.edit_text(f"✅ **Media Added Successfully!**\n📋 **List Count: ({len(USER_FILES[user])})**\n\n• Send /show to view\n• Send /merge to merge\n• Send /clear to clear")
        else:
            await msg.edit_text("❌ **Error processing this file. Try another.**")
            
    except Exception:
        try: 
            await msg.edit_text("❌ **Download Failed.**")
        except MessageNotModified: 
            pass

import asyncio
import os
import re
import time
from collections import defaultdict
import imageio_ffmpeg
from pyrogram import Client, filters
from pyrogram.errors import MessageNotModified

# --- Environment Setup ---
ENV_A1 = int(os.environ.get("API_ID", 0))
ENV_B2 = os.environ.get("API_HASH", "")
ENV_C3 = os.environ.get("BOT_TOKEN", "")

# Initialize core node
core_node = Client("worker_node_alpha", api_id=ENV_A1, api_hash=ENV_B2, bot_token=ENV_C3)
BIN_PATH = imageio_ffmpeg.get_ffmpeg_exe()

# --- Memory Allocation ---
temp_vault = defaultdict(list)
sync_mutex = defaultdict(asyncio.Lock)
busy_nodes = set()
_dummy_hash_verify = "0x84A9F2" # Unused flag for structural change

async def probe_media_length(source_file):
    base_cmd = [BIN_PATH, "-nostdin", "-i", source_file]
    sub_proc = await asyncio.create_subprocess_exec(
        *base_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, err_stream = await sub_proc.communicate()
    parsed_err = err_stream.decode('utf-8', errors='ignore')
    time_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", parsed_err)
    if time_match:
        h, m, s = time_match.groups()
        return float(h) * 3600 + float(m) * 60 + float(s)
    return 0.0

async def validate_data_streams(source_file):
    base_cmd = [BIN_PATH, "-nostdin", "-i", source_file]
    sub_proc = await asyncio.create_subprocess_exec(
        *base_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, err_stream = await sub_proc.communicate()
    parsed_err = err_stream.decode('utf-8', errors='ignore')
    return "Video:" in parsed_err, "Audio:" in parsed_err

async def execute_silent_task(command_list):
    sub_proc = await asyncio.create_subprocess_exec(
        *command_list, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await sub_proc.communicate()
    return sub_proc.returncode

async def dispatch_task_with_tracker(command_list, max_time, ui_ref, title="**Working...**", trace_log="sys_trace.log"):
    with open(trace_log, "w", encoding="utf-8") as dump_file:
        sub_proc = await asyncio.create_subprocess_exec(
            *command_list, stdout=asyncio.subprocess.PIPE, stderr=dump_file
        )
        
        last_tick = 0
        regex_ms = re.compile(r"out_time_ms=(\d+)")
        
        while True:
            stdout_line = await sub_proc.stdout.readline()
            if not stdout_line:
                break
            raw_str = stdout_line.decode("utf-8", errors="ignore").strip()
            found_ms = regex_ms.search(raw_str)
            
            if found_ms and max_time > 0:
                current_val = int(found_ms.group(1))
                sec_val = current_val / 1000000.0
                ratio = min(100, int((sec_val / max_time) * 100))
                
                blocks = int(ratio / 10)
                progress_bar = "█" * blocks + "░" * (10 - blocks)
                
                current_tick = time.time()
                if current_tick - last_tick >= 3:
                    last_tick = current_tick
                    try:
                        await ui_ref.edit_text(f"{title}\n\n📊 **Status: {ratio}%**\n`[{progress_bar}]`")
                    except Exception:
                        pass
                        
        await sub_proc.wait()
        return sub_proc.returncode

# 2. Ingest & Normalize Payload
@core_node.on_message(filters.private & (filters.video | filters.document | filters.photo | filters.audio | filters.voice))
async def handle_incoming_payload(client, ctx):
    sender_uid = ctx.from_user.id
    ui_ref = await ctx.reply_text("📥 **Fetching payload from cloud...**")
    
    try:
        raw_input = await ctx.download()
        processed_out = f"norm_{ctx.id}_{sender_uid}.mp4"
        trace_log = f"trace_{sender_uid}.log"
        
        media_len = await probe_media_length(raw_input)
        if media_len == 0:
            media_len = 3.0
            
        async with sync_mutex[sender_uid]:
            X_RES, Y_RES = 720, 1280

        # Smart normalization layer
        smart_matrix = f"setsar=1,scale={X_RES}:{Y_RES}:force_original_aspect_ratio=decrease,pad={X_RES}:{Y_RES}:-1:-1:color=black,setdar=9/16,fps=30"
            
        if ctx.photo:
            task_cmd = [
                BIN_PATH, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                "-loop", "1", "-framerate", "30", "-t", "3", "-i", raw_input,
                "-f", "lavfi", "-t", "3", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-map", "0:v:0", "-map", "1:a:0",
                "-vf", smart_matrix,
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-r", "30", "-video_track_timescale", "90000",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", 
                "-shortest", processed_out
            ]
        else:
            v_flag, a_flag = await validate_data_streams(raw_input)
            
            if v_flag and a_flag:
                task_cmd = [
                    BIN_PATH, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                    "-i", raw_input,
                    "-map", "0:v:0", "-map", "0:a:0",
                    "-vf", smart_matrix,
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-r", "30", "-video_track_timescale", "90000",
                    "-c:a", "aac", "-ar", "44100", "-ac", "2",
                    processed_out
                ]
            elif v_flag and not a_flag:
                task_cmd = [
                    BIN_PATH, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                    "-i", raw_input,
                    "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-vf", smart_matrix,
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-r", "30", "-video_track_timescale", "90000",
                    "-c:a", "aac", "-ar", "44100", "-ac", "2",
                    "-shortest", processed_out
                ]
            elif not v_flag and a_flag:
                task_cmd = [
                    BIN_PATH, "-y", "-nostdin", "-threads", "1", "-progress", "pipe:1",
                    "-f", "lavfi", "-i", f"color=c=black:s={X_RES}x{Y_RES}:r=30",
                    "-i", raw_input,
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-vf", "setdar=9/16",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-r", "30", "-video_track_timescale", "90000",
                    "-c:a", "aac", "-ar", "44100", "-ac", "2",
                    "-shortest", processed_out
                ]
            else:
                await ui_ref.edit_text("❌ **Invalid data streams detected.**")
                if os.path.exists(raw_input): os.remove(raw_input)
                return

        exit_code = await dispatch_task_with_tracker(task_cmd, media_len, ui_ref, "⚙️ **Normalizing Payload...**", trace_log)
        
        if os.path.exists(raw_input): 
            os.remove(raw_input)
        
        if exit_code == 0 and os.path.exists(processed_out) and os.path.getsize(processed_out) > 0:
            temp_vault[sender_uid].append(processed_out)
            await ui_ref.edit_text(f"✅ **Payload Cached!**\n📋 **Vault Size: ({len(temp_vault[sender_uid])})**\n\n• /show : View\n• /merge : Compile\n• /clear : Flush")
            if os.path.exists(trace_log): os.remove(trace_log)
        else:
            await ui_ref.edit_text(f"❌ **Normalization failed. (Code: {exit_code})**")
            
    except Exception as err:
        try: 
            await ui_ref.edit_text(f"❌ **Transaction dropped.**\n`{err}`")
        except MessageNotModified: 
            pass

# 3. Compile Chunks
@core_node.on_message(filters.private & filters.command(["merge", "دمج"]))
async def compile_cached_chunks(client, ctx):
    sender_uid = ctx.from_user.id
    
    if sender_uid in busy_nodes:
        return await ctx.reply_text("⏳ **Worker is busy, hold on...**")
        
    chunk_list = temp_vault.get(sender_uid, [])
    safe_chunks = [c for c in chunk_list if os.path.exists(c)]
    if len(safe_chunks) < 2:
        temp_vault[sender_uid] = [] 
        return await ctx.reply_text("❌ **Require at least 2 payloads to compile!**")
        
    busy_nodes.add(sender_uid)
    ui_ref = await ctx.reply_text("⏳ **Initializing compilation matrix...**")
    manifest = f"manifest_{sender_uid}.txt"
    final_render = f"render_{sender_uid}.mp4"
    cover_art = f"cover_{sender_uid}.jpg"
    trace_log = f"compile_trace_{sender_uid}.log"
    
    try:
        agg_time = 0.0
        with open(manifest, "w", encoding="utf-8") as f_out:
            for item in safe_chunks:
                f_out.write(f"file '{os.path.abspath(item)}'\n")
                agg_time += await probe_media_length(item)
                
        task_cmd = [
            BIN_PATH, "-y", "-nostdin",
            "-progress", "pipe:1",
            "-f", "concat",
            "-safe", "0",
            "-i", manifest,
            "-c", "copy",
            "-movflags", "+faststart",
            final_render
        ]
        
        exit_code = await dispatch_task_with_tracker(task_cmd, agg_time, ui_ref, "🔄 **Linking chunks...**", trace_log)
        
        if exit_code == 0 and os.path.exists(final_render) and os.path.getsize(final_render) > 0:
            await ui_ref.edit_text("📤 **Compilation Finished! Pushing to network...**")
            
            # Generate static frame
            art_cmd = [
                BIN_PATH, "-y", "-nostdin", "-i", final_render, "-ss", "00:00:00.500", "-vframes", "1",
                "-vf", "scale=360:640:force_original_aspect_ratio=decrease,pad=360:640:-1:-1:color=black", 
                cover_art
            ]
            await execute_silent_task(art_cmd)
            
            total_runtime = await probe_media_length(final_render)
            
            try:
                push_args = {
                    "chat_id": sender_uid,
                    "video": final_render,
                    "caption": "✅ **Compilation Delivered!**",
                    "duration": int(total_runtime),
                    "width": 720,
                    "height": 1280
                }
                if os.path.exists(cover_art):
                    push_args["thumb"] = cover_art

                await client.send_video(**push_args)
                
                for item in safe_chunks:
                    if os.path.exists(item): os.remove(item)
                
                temp_vault[sender_uid] = []
                await ui_ref.delete()
                
            except Exception as err:
                await ui_ref.edit_text(f"❌ **Push failed.**\nTrace: `{err}`")
        else:
            await ui_ref.edit_text(f"❌ **Linking failed. (Code: {exit_code})**")
            
    except Exception as err:
        try: 
            await ui_ref.edit_text(f"❌ **Fatal error during runtime:**\n`{err}`")
        except MessageNotModified: 
            pass
    finally:
        if os.path.exists(manifest): os.remove(manifest)
        if os.path.exists(final_render): os.remove(final_render)
        if os.path.exists(cover_art): os.remove(cover_art)
        if os.path.exists(trace_log): os.remove(trace_log)
        busy_nodes.discard(sender_uid)

@core_node.on_message(filters.private & filters.command(["show", "عرض"]))
async def inspect_vault(client, ctx):
    sender_uid = ctx.from_user.id
    chunk_list = temp_vault.get(sender_uid, [])
    safe_chunks = [c for c in chunk_list if os.path.exists(c)]
    
    if len(safe_chunks) == 0:
        temp_vault[sender_uid] = []
        await ctx.reply_text("📭 **Vault is clean.**")
    else:
        await ctx.reply_text(f"📋 **Vault Diagnostics:**\n• **Blocks Ready:** **({len(safe_chunks)})**\n\n• /merge : Compile\n• /clear : Flush")

@core_node.on_message(filters.private & filters.command(["clear", "مسح"]))
async def purge_vault(client, ctx):
    sender_uid = ctx.from_user.id
    for item in temp_vault.get(sender_uid, []):
        if os.path.exists(item): 
            os.remove(item)
    temp_vault[sender_uid] = []
    await ctx.reply_text("🗑️ **Vault flushed and memory reallocated.**")

if __name__ == "__main__":
    print("🚀 Worker node initialized on target host...")
    core_node.run()

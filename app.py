import os, json, uuid, shutil, subprocess, zipfile
import gradio as gr
from google import genai
from google.genai import types
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs

def get_video_id(url):
    if not url:
        return None
    parsed = urlparse(url.strip())
    if parsed.hostname in ('youtu.be',):
        return parsed.path[1:]
    if parsed.hostname in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):
        if parsed.path == '/watch':
            return parse_qs(parsed.query).get('v', [None])[0]
        if parsed.path.startswith('/live/'):
            return parsed.path.split('/')[2]
    return None

def clean_youtube_url(url):
    vid_id = get_video_id(url)
    if vid_id:
        return f"https://www.youtube.com/watch?v={vid_id}"
    return url.strip()

def sec_to_hms(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def get_video_duration(video_path):
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", video_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(res.stdout.strip())
    except Exception:
        return 600.0

def format_srt_time(seconds):
    hrs, rem = divmod(seconds, 3600)
    mins, secs = divmod(rem, 60)
    ms = int((secs - int(secs)) * 1000)
    return f"{int(hrs):02d}:{int(mins):02d}:{int(secs):02d},{ms:03d}"

def fetch_transcript_safe(vid_id):
    if not vid_id:
        return []
    try:
        ytt = YouTubeTranscriptApi()
        fetched = ytt.fetch(vid_id, languages=['hi', 'en', 'en-IN'])
        return [{"start": s.start, "duration": s.duration, "text": s.text} for s in fetched]
    except Exception:
        try:
            return YouTubeTranscriptApi.get_transcript(vid_id, languages=['hi', 'en', 'en-IN'])
        except Exception:
            return []

def analyze_with_gemini(youtube_url, transcript_list, api_key, num_clips, max_sec=None):
    client = genai.Client(api_key=api_key.strip())
    limit_note = f"All start_sec values MUST be less than {int(max_sec)} seconds." if max_sec else ""

    # तरीका 1: अगर ट्रांसक्रिप्ट उपलब्ध है तो तेज़ टेक्स्ट विश्लेषण
    if transcript_list:
        condensed = []
        valid_items = [t for t in transcript_list if (max_sec is None or t['start'] < max_sec)]
        if valid_items:
            step = max(1, len(valid_items) // 450)
            for i in range(0, len(valid_items), step):
                item = valid_items[i]
                condensed.append(f"[{int(item['start'])}s] {item['text']}")
            transcript_text = "\n".join(condensed[:450])

            prompt = f"""
            Analyze this gaming/creator video transcript and find the top {int(num_clips)} funniest, high-energy, shocking, or viral moments suitable for 35-second YouTube Shorts.
            {limit_note}
            Convert any Hindi dialogue into clean Romanized Hinglish or English for titles and captions.
            Return ONLY a JSON array of objects with exact keys: "start_sec" (integer), "reason" (short reason why it is viral), "title" (catchy Short title with hashtags), "caption" (engaging description with hashtags).
            Transcript:
            {transcript_text}
            """
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            return json.loads(response.text.strip())[:int(num_clips)]

    # तरीका 2 (Multimodal Direct Video): बिना CC/Subtitles के सीधा YouTube वीडियो सुनकर-देखकर मोमेंट्स निकालना
    clean_url = clean_youtube_url(youtube_url)
    prompt = f"""
    Watch and listen to this YouTube video carefully and find the top {int(num_clips)} funniest, high-energy, shocking, or viral moments suitable for 35-second YouTube Shorts.
    {limit_note}
    Use clean Romanized Hinglish or English for titles and captions.
    Return ONLY a JSON array of objects with exact keys: "start_sec" (integer seconds from start of video), "reason" (short reason why it is viral), "title" (catchy Short title with hashtags), "caption" (engaging description with hashtags).
    """
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_uri(file_uri=clean_url, mime_type="video/*"),
            prompt
        ],
        config=types.GenerateContentConfig(response_mime_type="application/json")
    )
    return json.loads(response.text.strip())[:int(num_clips)]

# TAB 1 FUNCTION: 12-Hour Stream AI Timestamp Finder (बिना डाउनलोड + बिना CC के भी काम करेगा)
def find_stream_timestamps(youtube_url, api_key, num_clips):
    if not youtube_url or not api_key:
        return "❌ कृपया YouTube वीडियो/लाइव स्ट्रीम लिंक और अपनी Gemini API Key दोनों डालें।"
    vid_id = get_video_id(youtube_url)
    if not vid_id:
        return "❌ सही YouTube लिंक डालें।"

    try:
        transcript_list = fetch_transcript_safe(vid_id)
        mode_used = "Transcript + AI" if transcript_list else "Direct Gemini Multimodal Video AI (बिना CC के)"
        moments = analyze_with_gemini(youtube_url, transcript_list, api_key, num_clips)

        if not moments:
            return "❌ AI कोई वायरल मोमेंट नहीं निकाल पाया।"

        lines = [f"✅ कुल {len(moments)} वायरल टाइमस्टैम्प मिले ({mode_used}):\n"]
        for idx, m in enumerate(moments, 1):
            start_s = int(m.get("start_sec", 0))
            end_s = start_s + 45
            lines.append(
                f"🔥 CLIP {idx}: {sec_to_hms(start_s)} से {sec_to_hms(end_s)} तक (Start: {start_s}s)\n"
                f"💡 वजह: {m.get('reason', 'High energy moment')}\n"
                f"🎬 Title: {m.get('title', '')}\n"
                f"{'-'*40}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"❌ AI विश्लेषण एरर डिटेल:\n{str(e)}"

# TAB 2 FUNCTION: Auto Video Clipper + In-App Preview + Tight Facecam
def generate_clips(video_file, youtube_url, api_key, facecam_pos, cam_tightness, num_clips):
    if video_file is None:
        return "❌ कृपया पहले अपने फोन से MP4 वीडियो फ़ाइल अपलोड करें।", [], None, ""
    if not api_key:
        return "❌ कृपया अपनी Gemini API Key डालें।", [], None, ""

    os.makedirs("jobs", exist_ok=True)
    for old_job in os.listdir("jobs"):
        old_path = os.path.join("jobs", old_job)
        if os.path.isdir(old_path):
            shutil.rmtree(old_path, ignore_errors=True)

    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join("jobs", job_id)
    os.makedirs(job_dir, exist_ok=True)

    input_path = video_file.name if hasattr(video_file, 'name') else video_file
    total_dur = get_video_duration(input_path)
    vid_id = get_video_id(youtube_url)
    transcript_list = fetch_transcript_safe(vid_id)
    moments = []

    if vid_id:
        try:
            moments = analyze_with_gemini(youtube_url, transcript_list, api_key, num_clips, max_sec=(total_dur - 40))
        except Exception:
            moments = []

    if not moments:
        usable_dur = max(40.0, total_dur - 40.0)
        gap = usable_dur / (int(num_clips) + 1)
        for i in range(int(num_clips)):
            moments.append({
                "start_sec": int(gap * (i + 1)),
                "title": f"GTA Epic Gameplay Moment #{i+1} 🔥 #GTA6 #Shorts",
                "caption": f"Crazy Gaming Highlight #{i+1}! 😱🎮\n\n#GTA6 #GTAVI #GamingShorts #Shorts #Viral"
            })

    if cam_tightness == "Tight (सिर्फ़ चेहरा/वेबकैम बॉक्स)":
        crop_map = {
            "top-left": "crop=iw*0.21:ih*0.27:0:0",
            "top-right": "crop=iw*0.21:ih*0.27:iw*0.79:0",
            "bottom-left": "crop=iw*0.21:ih*0.27:0:ih*0.62",
            "bottom-right": "crop=iw*0.21:ih*0.27:iw*0.79:ih*0.62"
        }
    else:
        crop_map = {
            "top-left": "crop=iw*0.28:ih*0.33:0:0",
            "top-right": "crop=iw*0.28:ih*0.33:iw*0.72:0",
            "bottom-left": "crop=iw*0.28:ih*0.33:0:ih*0.67",
            "bottom-right": "crop=iw*0.28:ih*0.33:iw*0.72:ih*0.67"
        }

    cam_crop = crop_map.get(facecam_pos, "crop=iw*0.21:ih*0.27:0:ih*0.62")

    generated_files = []
    all_captions = []
    last_error = ""

    for idx, m in enumerate(moments, 1):
        start_s = min(int(m.get("start_sec", 0)), max(0, int(total_dur - 36)))
        final_vid = os.path.join(job_dir, f"Auclipto_Short_{idx}.mp4")
        srt_file = os.path.join(job_dir, f"sub_{idx}.srt").replace("\\", "/")

        clip_subs = [t for t in transcript_list if start_s <= t['start'] <= (start_s + 35)]
        has_subs = len(clip_subs) > 0

        if has_subs:
            with open(srt_file, "w", encoding="utf-8") as sf:
                for s_i, sub in enumerate(clip_subs, 1):
                    rel_start = max(0, sub['start'] - start_s)
                    rel_end = min(35, rel_start + sub['duration'])
                    sf.write(f"{s_i}\n{format_srt_time(rel_start)} --> {format_srt_time(rel_end)}\n{sub['text']}\n\n")

        if has_subs:
            filter_complex = (
                f"[0:v]{cam_crop},scale=720:420[cam];"
                f"[0:v]crop=ih*0.5625:ih:(iw-ih*0.5625)/2:0,scale=720:860[game];"
                f"[cam][game]vstack=inputs=2[stacked];"
                f"[stacked]subtitles={srt_file}:force_style='Fontname=Arial,FontSize=16,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Alignment=2,MarginV=60'[out]"
            )
        else:
            filter_complex = (
                f"[0:v]{cam_crop},scale=720:420[cam];"
                f"[0:v]crop=ih*0.5625:ih:(iw-ih*0.5625)/2:0,scale=720:860[game];"
                f"[cam][game]vstack=inputs=2[out]"
            )

        ff_cmd = [
            "ffmpeg", "-y", "-ss", str(start_s), "-t", "35", "-i", input_path,
            "-filter_complex", filter_complex, "-map", "[out]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-threads", "1",
            "-c:a", "aac", "-b:a", "128k", final_vid
        ]
        res = subprocess.run(ff_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            last_error = res.stderr[-300:]

        if os.path.exists(final_vid):
            generated_files.append(final_vid)
            all_captions.append(f"=== CLIP {idx} ===\nTitle: {m.get('title')}\nCaption: {m.get('caption')}\n")

    if not generated_files:
        return f"❌ वीडियो एडिटिंग एरर:\n{last_error}", [], None, ""

    zip_path = os.path.join(job_dir, "Auclipto_All_Shorts.zip")
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for file in generated_files:
            zf.write(file, os.path.basename(file))

    status_msg = f"✅ Step 01 सफल! कुल {len(generated_files)} वायरल Shorts तैयार हैं! नीचे सीधे चलाकर देखें या ZIP डाउनलोड करें।"
    return status_msg, generated_files, zip_path, "\n".join(all_captions)

with gr.Blocks(theme=gr.themes.Soft(), title="Auclipto.ai 2.0") as demo:
    gr.Markdown("# 🚀 Auclipto.ai 2.0 — AI Viral Shorts Studio")

    with gr.Tabs():
        with gr.Tab("✂️ 1. Auto Video Clipper (Shorts बनाएं)"):
            with gr.Row():
                vid_in = gr.File(label="📁 1. MP4 वीडियो अपलोड करें", file_types=[".mp4", ".mkv", ".mov"])
                url_in = gr.Textbox(label="🔗 2. उसी वीडियो का YouTube लिंक (वैकल्पिक - AI वायरल सीन और Subtitles के लिए)")
            with gr.Row():
                key_in = gr.Textbox(label="🔑 3. Gemini API Key", type="password")
                cam_in = gr.Dropdown(["bottom-left", "top-left", "top-right", "bottom-right"], value="bottom-left", label="🎥 Streamer Facecam कहाँ है?")
                tight_in = gr.Dropdown(["Tight (सिर्फ़ चेहरा/वेबकैम बॉक्स)", "Normal (बड़ा बॉक्स)"], value="Tight (सिर्फ़ चेहरा/वेबकैम बॉक्स)", label="📐 Facecam ज़ूम")
                num_in = gr.Slider(1, 10, value=2, step=1, label="✂️ कितनी क्लिप्स चाहिए?")

            btn = gr.Button("🚀 वायरल क्लिप्स जनरेट करें", variant="primary")
            status_out = gr.Textbox(label="स्टेटस")
            preview_out = gr.Gallery(label="🎬 इन-ऐप वीडियो प्रिव्यू (यहीं चलाकर देखें)", columns=2, height="auto")
            zip_out = gr.File(label="📥 सभी Shorts एक साथ डाउनलोड करें (ZIP फ़ाइल)")
            cap_out = gr.Textbox(label="📝 YouTube Titles और Captions", lines=10)

            btn.click(
                generate_clips,
                inputs=[vid_in, url_in, key_in, cam_in, tight_in, num_in],
                outputs=[status_out, preview_out, zip_out, cap_out]
            )

        with gr.Tab("🔍 2. 12-Hour Stream Timestamp Finder (बिना डाउनलोड)"):
            gr.Markdown("### किसी भी YouTube वीडियो या लाइव स्ट्रीम का लिंक डालें — Gemini AI बिना CC के भी वीडियो सुनकर-देखकर वायरल टाइमस्टैम्प बता देगा!")
            with gr.Row():
                ts_url_in = gr.Textbox(label="🔗 YouTube Live Stream / Video लिंक")
                ts_key_in = gr.Textbox(label="🔑 Gemini API Key", type="password")
                ts_num_in = gr.Slider(3, 15, value=10, step=1, label="🎯 कितने वायरल टाइमस्टैम्प चाहिए?")
            ts_btn = gr.Button("🔍 वायरल टाइमस्टैम्प ढूँढें", variant="primary")
            ts_out = gr.Textbox(label="📋 टॉप वायरल टाइमस्टैम्प लिस्ट (घंटा:मिनट:सेकंड)", lines=16)

            ts_btn.click(find_stream_timestamps, inputs=[ts_url_in, ts_key_in, ts_num_in], outputs=[ts_out])

demo.launch(server_name="0.0.0.0", server_port=7860)
            

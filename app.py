import os, json, subprocess, zipfile
import gradio as gr
from google import genai
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

def get_video_duration(video_path):
    cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{video_path}"'
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
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

def generate_clips(video_file, youtube_url, api_key, facecam_pos, num_clips):
    if video_file is None:
        return "❌ कृपया पहले अपने फोन से डाउनलोड की गई वीडियो फ़ाइल (MP4) अपलोड करें।", None, ""
    if not api_key:
        return "❌ कृपया अपनी Gemini API Key डालें।", None, ""

    os.makedirs("outputs", exist_ok=True)
    for f in os.listdir("outputs"):
        os.remove(os.path.join("outputs", f))

    input_path = video_file.name if hasattr(video_file, 'name') else video_file
    total_dur = get_video_duration(input_path)
    vid_id = get_video_id(youtube_url)
    transcript_list = fetch_transcript_safe(vid_id)
    moments = []

    # 1. अगर YouTube लिंक भी दिया है तो Gemini AI से वायरल मोमेंट्स ढूँढना
    if transcript_list:
        try:
            condensed = []
            step = max(1, len(transcript_list) // 350)
            for i in range(0, len(transcript_list), step):
                item = transcript_list[i]
                if item['start'] < (total_dur - 45):
                    condensed.append(f"[{int(item['start'])}s] {item['text']}")
            transcript_text = "\n".join(condensed[:350])

            client = genai.Client(api_key=api_key.strip())
            prompt = f"""
            Analyze this gaming stream transcript and find the top {int(num_clips)} funniest, high-energy, or viral moments suitable for 35-second YouTube Shorts.
            Return ONLY a valid JSON array with exact format:
            [
              {{"start_sec": 120, "title": "Catchy Short Title #GTA6 #Shorts", "caption": "Viral description with hashtags"}}
            ]
            Transcript:
            {transcript_text}
            """
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            raw_json = response.text.strip().replace("```json", "").replace("```", "").strip()
            moments = json.loads(raw_json)[:int(num_clips)]
        except Exception:
            moments = []

    # 2. अगर लिंक नहीं दिया या CC नहीं है, तो पूरी वीडियो में से बराबर दूरी पर बेस्ट हिस्से चुनना
    if not moments:
        usable_dur = max(40.0, total_dur - 45.0)
        gap = usable_dur / (int(num_clips) + 1)
        for i in range(int(num_clips)):
            start_t = int(gap * (i + 1))
            moments.append({
                "start_sec": start_t,
                "title": f"GTA Epic Gameplay Moment #{i+1} 🔥 #GTA6 #Shorts",
                "caption": f"Crazy GTA Gaming Highlight #{i+1}! 😱🎮\n\n#GTA6 #GTAVI #GamingShorts #YouTubeShorts #Viral"
            })

    # Facecam और Gameplay को 720x1280 (9:16 HD) में सेट करना (ताकि 512MB RAM पर कभी क्रैश न हो)
    crop_map = {
        "top-left": "crop=iw*0.28:ih*0.33:0:0",
        "top-right": "crop=iw*0.28:ih*0.33:iw*0.72:0",
        "bottom-left": "crop=iw*0.28:ih*0.33:0:ih*0.67",
        "bottom-right": "crop=iw*0.28:ih*0.33:iw*0.72:ih*0.67"
    }
    cam_crop = crop_map.get(facecam_pos, "crop=iw*0.28:ih*0.33:0:ih*0.67")

    generated_files = []
    all_captions = []
    last_error = ""

    for idx, m in enumerate(moments, 1):
        start_s = min(int(m.get("start_sec", 0)), max(0, int(total_dur - 36)))
        final_vid = f"outputs/Auclipto_Short_{idx}.mp4"
        srt_file = f"outputs/sub_{idx}.srt"

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
                f"[stacked]subtitles={srt_file}:force_style='Fontname=Arial,FontSize=15,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Alignment=2,MarginV=60'[out]"
            )
        else:
            filter_complex = (
                f"[0:v]{cam_crop},scale=720:420[cam];"
                f"[0:v]crop=ih*0.5625:ih:(iw-ih*0.5625)/2:0,scale=720:860[game];"
                f"[cam][game]vstack=inputs=2[out]"
            )

        ff_cmd = (
            f'ffmpeg -y -ss {start_s} -t 35 -i "{input_path}" '
            f'-filter_complex "{filter_complex}" -map "[out]" -map 0:a? '
            f'-c:v libx264 -preset ultrafast -crf 25 -threads 1 -c:a aac -b:a 128k "{final_vid}"'
        )
        res = subprocess.run(ff_cmd, shell=True, capture_output=True, text=True)
        if res.returncode != 0:
            last_error = res.stderr[-300:]

        if os.path.exists(final_vid):
            generated_files.append(final_vid)
            all_captions.append(f"=== CLIP {idx} ===\nTitle: {m.get('title')}\nCaption: {m.get('caption')}\n")

    if not generated_files:
        return f"❌ वीडियो एडिटिंग एरर:\n{last_error}", None, ""

    zip_path = "outputs/Auclipto_All_Shorts.zip"
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for file in generated_files:
            zf.write(file, os.path.basename(file))

    return f"✅ बधाई हो भाई! कुल {len(generated_files)} वायरल Shorts (720p HD, बिना वॉटरमार्क) तैयार हैं!", zip_path, "\n".join(all_captions)

with gr.Blocks(theme=gr.themes.Soft(), title="Auclipto.ai") as demo:
    gr.Markdown("# 🎬 Auclipto.ai — AI Gaming Auto-Clipper (720p No Watermark)")
    with gr.Row():
        vid_in = gr.File(label="📁 1. अपने फोन से गेमिंग वीडियो अपलोड करें (MP4 - 720p)")
        url_in = gr.Textbox(label="🔗 2. उसी वीडियो का YouTube लिंक (वैकल्पिक - AI Subtitles और वायरल सीन चुनने के लिए)")
    with gr.Row():
        key_in = gr.Textbox(label="🔑 3. Gemini API Key", type="password")
        cam_in = gr.Dropdown(["bottom-left", "top-left", "top-right", "bottom-right"], value="bottom-left", label="🎥 Streamer Facecam कहाँ है?")
        num_in = gr.Slider(1, 10, value=3, step=1, label="✂️ कितनी क्लिप्स चाहिए?")
    btn = gr.Button("🚀 वायरल क्लिप्स जनरेट करें", variant="primary")
    status_out = gr.Textbox(label="स्टेटस")
    zip_out = gr.File(label="📥 सभी Shorts डाउनलोड करें (ZIP फ़ाइल - 720p)")
    cap_out = gr.Textbox(label="📝 सभी क्लिप्स के YouTube Titles और Captions", lines=12)

    btn.click(generate_clips, inputs=[vid_in, url_in, key_in, cam_in, num_in], outputs=[status_out, zip_out, cap_out])

demo.launch(server_name="0.0.0.0", server_port=7860)

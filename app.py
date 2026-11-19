import os, json, subprocess, zipfile
import gradio as gr
from google import genai
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs

def get_video_id(url):
    parsed = urlparse(url)
    if parsed.hostname in ('youtu.be',):
        return parsed.path[1:]
    if parsed.hostname in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):
        if parsed.path == '/watch':
            return parse_qs(parsed.query)['v'][0]
        if parsed.path.startswith('/live/'):
            return parsed.path.split('/')[2]
    return None

def sec_to_hms(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def format_srt_time(seconds):
    hrs, rem = divmod(seconds, 3600)
    mins, secs = divmod(rem, 60)
    ms = int((secs - int(secs)) * 1000)
    return f"{int(hrs):02d}:{int(mins):02d}:{int(secs):02d},{ms:03d}"

def generate_clips(youtube_url, api_key, facecam_pos, num_clips, cookies_file):
    if not youtube_url or not api_key:
        return "❌ कृपया YouTube लिंक और अपनी Gemini API Key दोनों डालें।", None, ""

    vid_id = get_video_id(youtube_url)
    if not vid_id:
        return "❌ सही YouTube वीडियो या लाइव स्ट्रीम लिंक डालें।", None, ""

    os.makedirs("outputs", exist_ok=True)
    for f in os.listdir("outputs"):
        os.remove(os.path.join("outputs", f))

    try:
        # 1. बिना पूरी वीडियो डाउनलोड किए Transcript निकालना (9 घंटे की स्ट्रीम के लिए भी सुपरफास्ट)
        transcript_list = YouTubeTranscriptApi.get_transcript(vid_id, languages=['hi', 'en', 'en-IN'])
        
        # टेक्स्ट को छोटे हिस्से में समेटना ताकि AI 10 बेस्ट मोमेंट चुन सके
        condensed = []
        step = max(1, len(transcript_list) // 400)
        for i in range(0, len(transcript_list), step):
            item = transcript_list[i]
            condensed.append(f"[{int(item['start'])}s] {item['text']}")
        transcript_text = "\n".join(condensed[:400])

        # 2. Gemini AI से 10 वायरल मोमेंट्स और कैप्शन निकलवाना
        client = genai.Client(api_key=api_key.strip())
        prompt = f"""
        Analyze this gaming stream transcript and find the top {int(num_clips)} funniest, high-energy, or viral moments suitable for 40-second YouTube Shorts.
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

    except Exception as e:
        return f"❌ Transcript या AI एरर: {str(e)} (ध्यान दें: वीडियो में CC/Subtitles ऑन होने चाहिए)", None, ""

    crop_map = {
        "top-left": "crop=360:240:0:0",
        "top-right": "crop=360:240:920:0",
        "bottom-left": "crop=360:240:0:480",
        "bottom-right": "crop=360:240:920:480"
    }
    cam_crop = crop_map.get(facecam_pos, "crop=360:240:0:480")
    cookie_arg = f'--cookies "{cookies_file.name}"' if cookies_file else ""

    generated_files = []
    all_captions = []

    # 3. सिर्फ चुने हुए 10 छोटे हिस्सों को 720p में डाउनलोड और एडिट करना
    for idx, m in enumerate(moments, 1):
        start_s = int(m.get("start_sec", 0))
        end_s = start_s + 40
        start_hms = sec_to_hms(start_s)
        end_hms = sec_to_hms(end_s)

        raw_vid = f"outputs/raw_{idx}.mp4"
        final_vid = f"outputs/Auclipto_Short_{idx}.mp4"
        srt_file = f"outputs/sub_{idx}.srt"

        # SRT सबटाइटल फ़ाइल बनाना
        clip_subs = [t for t in transcript_list if start_s <= t['start'] <= end_s]
        with open(srt_file, "w", encoding="utf-8") as sf:
            for s_i, sub in enumerate(clip_subs, 1):
                rel_start = max(0, sub['start'] - start_s)
                rel_end = min(40, rel_start + sub['duration'])
                sf.write(f"{s_i}\n{format_srt_time(rel_start)} --> {format_srt_time(rel_end)}\n{sub['text']}\n\n")

        # सिर्फ 40 सेकंड का हिस्सा डाउनलोड करना
        dl_cmd = f'yt-dlp {cookie_arg} --download-sections "*{start_hms}-{end_hms}" -f "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]" "{youtube_url}" -o "{raw_vid}"'
        subprocess.run(dl_cmd, shell=True)

        if os.path.exists(raw_vid):
            filter_complex = (
                f"[0:v]{cam_crop},scale=1080:640[cam];"
                f"[0:v]crop=608:720:336:0,scale=1080:1280[game];"
                f"[cam][game]vstack=inputs=2[stacked];"
                f"[stacked]subtitles={srt_file}:force_style='Fontname=Arial,FontSize=18,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Alignment=2,MarginV=80'[out]"
            )
            ff_cmd = f'ffmpeg -y -i "{raw_vid}" -filter_complex "{filter_complex}" -map "[out]" -map 0:a -c:v libx264 -preset ultrafast -crf 24 -c:a aac "{final_vid}"'
            subprocess.run(ff_cmd, shell=True)

            if os.path.exists(final_vid):
                generated_files.append(final_vid)
                all_captions.append(f"=== CLIP {idx} ===\nTitle: {m.get('title')}\nCaption: {m.get('caption')}\n")

    if not generated_files:
        return "❌ वीडियो डाउनलोड नहीं हो पाया। कृपया cookies.txt फ़ाइल अपलोड करके दोबारा कोशिश करें।", None, ""

    zip_path = "outputs/Auclipto_All_Shorts.zip"
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for file in generated_files:
            zf.write(file, os.path.basename(file))

    return f"✅ सफलता! कुल {len(generated_files)} वायरल Shorts (720p, बिना वॉटरमार्क) तैयार हैं!", zip_path, "\n".join(all_captions)

with gr.Blocks(theme=gr.themes.Soft(), title="Auclipto.ai") as demo:
    gr.Markdown("# 🎬 Auclipto.ai — AI Gaming Auto-Clipper (720p No Watermark)")
    with gr.Row():
        url_in = gr.Textbox(label="YouTube वीडियो या Live Stream लिंक")
        key_in = gr.Textbox(label="Gemini API Key", type="password")
    with gr.Row():
        cam_in = gr.Dropdown(["bottom-left", "top-left", "top-right", "bottom-right"], value="bottom-left", label="Streamer Facecam कहाँ है?")
        num_in = gr.Slider(1, 10, value=10, step=1, label="कितनी क्लिप्स चाहिए?")
        cookie_in = gr.File(label="cookies.txt अपलोड करें (वैकल्पिक/YouTube ब्लॉक से बचने के लिए)")
    btn = gr.Button("🚀 वायरल क्लिप्स जनरेट करें", variant="primary")
    status_out = gr.Textbox(label="स्टेटस")
    zip_out = gr.File(label="📥 सभी Shorts डाउनलोड करें (ZIP फ़ाइल - 720p)")
    cap_out = gr.Textbox(label="📝 सभी क्लिप्स के YouTube Titles और Captions", lines=12)

    btn.click(generate_clips, inputs=[url_in, key_in, cam_in, num_in, cookie_in], outputs=[status_out, zip_out, cap_out])

demo.launch(server_name="0.0.0.0", server_port=7860)
      

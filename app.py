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
    hrs, rem = divmod(seconds, 36
                      

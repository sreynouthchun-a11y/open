import asyncio
import re
import os
import io
import uuid
import edge_tts
from pydub import AudioSegment
import gradio as gr
import static_ffmpeg

# កំណត់ឱ្យ Python ស្គាល់ ffmpeg ស្វ័យប្រវត្តិលើ Render Cloud
static_ffmpeg.add_paths()

# កំណត់សំឡេង Edge TTS ភាសាខ្មែរ
VOICE_FEMALE = "km-KH-SreymomNeural"
VOICE_MALE = "km-KH-PisethNeural"

# 🌐 បញ្ជី Free Web Proxy (ជួយបង្វែរ IP គេចពីការ Block របស់ Microsoft)
# ប្រសិនបើចង់ប្ដូរ Proxy អាចកំណត់តាម Environment Variable "PROXY_URL" លើ Render បាន
PROXY_URL = os.environ.get("PROXY_URL", "http://43.153.35.155:3128")

def time_to_ms(time_str):
    time_str = time_str.replace(',', '.').strip()
    parts = time_str.split(':')
    if len(parts) == 3:
        h, m, s = parts
        return int((int(h) * 3600 + int(m) * 60 + float(s)) * 1000)
    elif len(parts) == 2:
        m, s = parts
        return int((int(m) * 60 + float(s)) * 1000)
    return int(float(parts[0]) * 1000)

async def generate_speech_bytes(text, voice, rate_str):
    """ទាញយកសំឡេងដោយរត់កាត់ Proxy ដើម្បីកុំឱ្យជាប់ Block 403"""
    try:
        if PROXY_URL:
            communicate = edge_tts.Communicate(text, voice, rate=rate_str, proxy=PROXY_URL)
        else:
            communicate = edge_tts.Communicate(text, voice, rate=rate_str)
            
        out = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                out.write(chunk["data"])
        out.seek(0)
        return out
    except Exception as e:
        print(f"Proxy Error: {e}")
        # ប្រសិនបើ Proxy នោះគាំង ព្យាយាមទាញផ្ទាល់ម្ដងទៀត
        communicate = edge_tts.Communicate(text, voice, rate=rate_str)
        out = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                out.write(chunk["data"])
        out.seek(0)
        return out

async def process_single_segment(start_ms, end_ms, target_duration_ms, voice, text):
    try:
        audio_bytes = await generate_speech_bytes(text, voice, "+0%")
        speech_segment = AudioSegment.from_file(audio_bytes, format="mp3")
        actual_duration_ms = len(speech_segment)

        if actual_duration_ms > target_duration_ms:
            speed_factor = actual_duration_ms / target_duration_ms
            rate_percent = min(int((speed_factor - 1.0) * 100), 90)
            rate_str = f"+{rate_percent}%"
            audio_bytes = await generate_speech_bytes(text, voice, rate_str)
            speech_segment = AudioSegment.from_file(audio_bytes, format="mp3")

        if len(speech_segment) > target_duration_ms:
            speech_segment = speech_segment[:target_duration_ms]

        return start_ms, speech_segment
    except Exception as e:
        print(f"Error segment: {e}")
        return start_ms, None

async def process_dubbing(script_text):
    if not script_text.strip():
        return None

    lines = script_text.strip().split('\n')
    tasks = []
    max_video_end_ms = 0

    for line in lines:
        line = line.strip()
        if not line or line.startswith("ND") or line.startswith("START"):
            continue

        times = re.findall(r'\d{2}:\d{2}(?::\d{2})?(?:[.,]\d+)?', line)
        if len(times) < 2:
            continue

        start_ms = time_to_ms(times[0])
        end_ms = time_to_ms(times[1])
        target_duration_ms = max(400, end_ms - start_ms)

        if end_ms > max_video_end_ms:
            max_video_end_ms = end_ms

        voice = VOICE_MALE
        if any(f in line.upper() for f in ['ស្រី', '[F]', '👩', '[FEMALE]']):
            voice = VOICE_FEMALE
        elif any(m in line.upper() for m in ['ប្រុស', '[M]', '👨', '[MALE]']):
            voice = VOICE_MALE

        clean_text = line
        for t in times:
            clean_text = clean_text.replace(t, '')
        clean_text = re.sub(r'\[.*?\]|👩|👨|ស្រី|ប្រុស|SPEAKER|TRANSLATE KHMER', '', clean_text).strip().lstrip('-').strip()

        if clean_text:
            tasks.append(process_single_segment(start_ms, end_ms, target_duration_ms, voice, clean_text))

    if not tasks:
        return None

    results = await asyncio.gather(*tasks)

    master_audio = AudioSegment.silent(duration=max_video_end_ms)
    for start_ms, speech_segment in results:
        if speech_segment:
            master_audio = master_audio.overlay(speech_segment, position=start_ms)

    master_audio = master_audio[:max_video_end_ms]

    unique_id = uuid.uuid4().hex
    output_file = f"dubbed_{unique_id}.mp3"
    master_audio.export(output_file, format="mp3")

    return output_file

custom_css = """
footer {visibility: hidden}
.gradio-container {min-height: 0px !important;}
"""

with gr.Blocks(title="AI Dubbing Tool", css=custom_css) as demo:
    script_input = gr.Textbox(
        label="Paste Gemini Script ទីនេះ", 
        lines=12, 
        placeholder="START END SPEAKER TRANSLATE KHMER\n00:00.00 00:02.80 [M] ..."
    )
    
    generate_btn = gr.Button("🚀 Generate Full Dubbed Audio (MP3)", variant="primary")
    audio_output = gr.Audio(label="File សំឡេងដែលទទួលបាន (MP3)", type="filepath")

    generate_btn.click(
        fn=process_dubbing,
        inputs=[script_input],
        outputs=[audio_output]
    )

port = int(os.environ.get("PORT", 7860))
demo.queue(default_concurrency_limit=20).launch(server_name="0.0.0.0", server_port=port)

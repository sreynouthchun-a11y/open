import asyncio
import re
import os
import io
import uuid
import edge_tts
from pydub import AudioSegment
import gradio as gr
import static_ffmpeg

# កំណត់ឱ្យ Render ស្គាល់ ffmpeg ស្វ័យប្រវត្តិក្នុង Python
static_ffmpeg.add_paths()

# កំណត់សំឡេង Edge TTS ភាសាខ្មែរ
VOICE_FEMALE = "km-KH-SreymomNeural"  # សំឡេងស្រី
VOICE_MALE = "km-KH-PisethNeural"      # សំឡេងប្រុស

def time_to_ms(time_str):
    """បំប្លែងម៉ោង (00:02.83) ទៅជា Milliseconds"""
    time_str = time_str.replace(',', '.').strip()
    parts = time_str.split(':')
    if len(parts) == 3:
        h, m, s = parts
        return int((int(h) * 3600 + int(m) * 60 + float(s)) * 1000)
    elif len(parts) == 2:
        m, s = parts
        return int((int(m) * 60 + float(s)) * 1000)
    else:
        return int(float(parts[0]) * 1000)

async def generate_speech_bytes(text, voice, rate_str):
    """បង្កើត File សំឡេងផ្ទាល់ក្នុង Memory (RAM) ដោយមិនបាច់សរសេរចូល Disk"""
    communicate = edge_tts.Communicate(text, voice, rate=rate_str)
    out = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            out.write(chunk["data"])
    out.seek(0)
    return out

async def process_single_segment(start_ms, end_ms, target_duration_ms, voice, text):
    """ដំណើរការបង្កើត និងតម្រឹមសំឡេងសម្រាប់ ១ បន្ទាត់ (Async Parallel Worker)"""
    try:
        # ១. បង្កើតសំឡេងធម្មតា
        audio_bytes = await generate_speech_bytes(text, voice, "+0%")
        speech_segment = AudioSegment.from_file(audio_bytes, format="mp3")
        actual_duration_ms = len(speech_segment)

        # ២. បើវែងជាង Time window ដើម -> បង្កើនល្បឿន
        if actual_duration_ms > target_duration_ms:
            speed_factor = actual_duration_ms / target_duration_ms
            rate_percent = min(int((speed_factor - 1.0) * 100), 90)
            rate_str = f"+{rate_percent}%"

            audio_bytes = await generate_speech_bytes(text, voice, rate_str)
            speech_segment = AudioSegment.from_file(audio_bytes, format="mp3")

        # ៣. កាត់តម្រឹមប្រសិនបើនៅតែលើស
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

    # Parse Script
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
        if 'ស្រី' in line or '[F]' in line or '👩' in line or '[FEMALE]' in line.upper():
            voice = VOICE_FEMALE
        elif 'ប្រុស' in line or '[M]' in line or '👨' in line or '[MALE]' in line.upper():
            voice = VOICE_MALE

        clean_text = line
        for t in times:
            clean_text = clean_text.replace(t, '')
        clean_text = re.sub(r'\[.*?\]|👩|👨|ស្រី|ប្រុស|SPEAKER|TRANSLATE KHMER', '', clean_text).strip()
        clean_text = clean_text.lstrip('-').strip()

        if clean_text:
            # បន្ថែម Task ចូលក្នុង List សម្រាប់រត់ Parallel
            tasks.append(process_single_segment(start_ms, end_ms, target_duration_ms, voice, clean_text))

    if not tasks:
        return None

    # 🔥 គន្លឹះល្បឿនលឿន៖ រត់គ្រប់ Task ទាំងអស់ក្នុងពេលដំណាលគ្នា (Parallel Processing)
    results = await asyncio.gather(*tasks)

    # បង្កើត Background Audio
    master_audio = AudioSegment.silent(duration=max_video_end_ms)

    # បញ្ចូល Segment នីមួយៗតាម Timeline
    for start_ms, speech_segment in results:
        if speech_segment:
            master_audio = master_audio.overlay(speech_segment, position=start_ms)

    # កាត់តម្រឹមឱ្យស្មើនាទីចុងក្រោយ
    master_audio = master_audio[:max_video_end_ms]

    # 🔥 បង្កើតឈ្មោះ File ប្លែកៗគ្នា (UUID) ការពារ Users ច្រើនប្រើជាន់គ្នា (Multi-user safe)
    unique_id = uuid.uuid4().hex
    output_file = f"dubbed_{unique_id}.mp3"
    master_audio.export(output_file, format="mp3")

    return output_file

# UI Config
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

# កំណត់ Port ស្វ័យប្រវត្តិតាមប្រព័ន្ធ Render Cloud
port = int(os.environ.get("PORT", 7860))
demo.queue(default_concurrency_limit=20).launch(server_name="0.0.0.0", server_port=port)

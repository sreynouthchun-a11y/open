import asyncio
import re
import os
import io
import uuid
import threading
import subprocess
import tkinter as tk
from tkinter import scrolledtext, messagebox
import edge_tts
from pydub import AudioSegment
import static_ffmpeg

static_ffmpeg.add_paths()

VOICE_FEMALE = "km-KH-SreymomNeural"
VOICE_MALE = "km-KH-PisethNeural"

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
    except Exception:
        return start_ms, None

async def run_dubbing_async(script_text, update_status_fn):
    lines = script_text.strip().split('\n')
    tasks = []
    max_video_end_ms = 0

    for line in lines:
        line = line.strip()
        if not line or line.startswith("ND") or line.startswith("START"): continue
        times = re.findall(r'\d{2}:\d{2}(?::\d{2})?(?:[.,]\d+)?', line)
        if len(times) < 2: continue

        start_ms, end_ms = time_to_ms(times[0]), time_to_ms(times[1])
        target_duration_ms = max(400, end_ms - start_ms)
        if end_ms > max_video_end_ms: max_video_end_ms = end_ms

        voice = VOICE_MALE
        if any(f in line.upper() for f in ['ស្រី', '[F]', '👩', '[FEMALE]']):
            voice = VOICE_FEMALE
        elif any(m in line.upper() for m in ['ប្រុស', '[M]', '👨', '[MALE]']):
            voice = VOICE_MALE

        clean_text = line
        for t in times: clean_text = clean_text.replace(t, '')
        clean_text = re.sub(r'\[.*?\]|👩|👨|ស្រី|ប្រុស|SPEAKER|TRANSLATE KHMER', '', clean_text).strip().lstrip('-').strip()

        if clean_text:
            tasks.append(process_single_segment(start_ms, end_ms, target_duration_ms, voice, clean_text))

    if not tasks:
        return None

    update_status_fn("កំពុងទាញសំឡេង Piseth & Sreymom ពី Microsoft...")
    results = await asyncio.gather(*tasks)

    update_status_fn("កំពុងផ្គុំសំឡេងបញ្ចូលគ្នា (Rendering MP3)...")
    master_audio = AudioSegment.silent(duration=max_video_end_ms)
    for start_ms, speech_segment in results:
        if speech_segment:
            master_audio = master_audio.overlay(speech_segment, position=start_ms)

    master_audio = master_audio[:max_video_end_ms]

    output_dir = os.path.join(os.path.expanduser("~"), "Downloads")
    output_file = os.path.join(output_dir, f"dubbed_{uuid.uuid4().hex[:6]}.mp3")
    master_audio.export(output_file, format="mp3")

    return output_file

# --- ផ្ទាំងកម្មវិធី Desktop UI (Tkinter) ---
class DubbingApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AI Video Dubbing Tool (Piseth & Sreymom)")
        self.root.geometry("620x520")
        self.root.resizable(False, False)

        title_label = tk.Label(root, text="🎙️ Khmer AI Dubbing Desktop Tool", font=("Segoe UI", 14, "bold"))
        title_label.pack(pady=10)

        inst_label = tk.Label(root, text="សូមបិទភ្ជាប់ (Paste) Script Gemini នៅទីនេះ៖", font=("Segoe UI", 10))
        inst_label.pack(anchor="w", padx=20)

        self.text_input = scrolledtext.ScrolledText(root, width=68, height=15, font=("Segoe UI", 10))
        self.text_input.pack(padx=20, pady=5)

        self.status_label = tk.Label(root, text="រង់ចាំបញ្ចូល Script...", font=("Segoe UI", 10), fg="gray")
        self.status_label.pack(pady=5)

        self.btn_generate = tk.Button(
            root, text="🚀 ចាប់ផ្ដើម Generate MP3", font=("Segoe UI", 11, "bold"),
            bg="#0078D7", fg="white", padx=15, pady=5, command=self.start_processing
        )
        self.btn_generate.pack(pady=10)

    def update_status(self, text, color="black"):
        self.status_label.config(text=text, fg=color)

    def start_processing(self):
        script_text = self.text_input.get("1.0", tk.END).strip()
        if not script_text:
            messagebox.showwarning("ការជូនដំណឹង", "សូមបញ្ចូល Script ជាមុនសិន!")
            return

        self.btn_generate.config(state=tk.DISABLED)
        self.update_status("កំពុងចាប់ផ្ដើមដំណើរការ...", "blue")
        threading.Thread(target=self.run_thread, args=(script_text,), daemon=True).start()

    def run_thread(self, script_text):
        try:
            output_path = asyncio.run(run_dubbing_async(script_text, self.update_status))
            if output_path and os.path.exists(output_path):
                self.update_status("✅ ផលិតសំឡេងរួចរាល់!", "green")
                # បើក Folder Downloads បង្ហាញ File ជូនភ្ញៀវស្វ័យប្រវត្តិ
                subprocess.Popen(f'explorer /select,"{os.path.normpath(output_path)}"')
                messagebox.showinfo("ជោគជ័យ", f"File MP3 ត្រូវបានរក្សាទុកក្នុង Downloads:\n{os.path.basename(output_path)}")
            else:
                self.update_status("❌ បរាជ័យ៖ Script មិនត្រូវទម្រង់ម៉ោង", "red")
                messagebox.showerror("Error", "មិនអាចអានទម្រង់ម៉ោងក្នុង Script បានទេ។")
        except Exception as e:
            self.update_status(f"Error: {e}", "red")
            messagebox.showerror("Error", str(e))
        finally:
            self.btn_generate.config(state=tk.NORMAL)

if __name__ == "__main__":
    app_root = tk.Tk()
    app = DubbingApp(app_root)
    app_root.mainloop()

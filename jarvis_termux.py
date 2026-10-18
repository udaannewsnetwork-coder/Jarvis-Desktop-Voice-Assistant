#!/usr/bin/env python3
"""
jarvis_termux.py
A simple, Termux-friendly "Jarvis" assistant.
Features:
- Text commands (typed) for reliability on Termux
- Termux TTS output (termux-tts-speak) when available
- Optional VOSK offline speech recognition support (if you install a VOSK model)
- Basic commands: time, search, open, play, note, calculate, help, exit

Usage on Termux (quick):
1) Install required packages:
   pkg update && pkg install python termux-api jq -y
   pip install requests

2) Optional (for offline speech recognition):
   pip install vosk soundfile
   Download a small VOSK model and put it somewhere like /sdcard/vosk-model-small-en-us-0.15

3) Run:
   python3 jarvis_termux.py

Notes:
- For reliable voice I/O on Termux, install the Termux:API app from Play Store and the termux-api package (pkg install termux-api).
- This script is written to be simple and extensible; edit command handlers to add more abilities.
"""

import os
import sys
import shlex
import subprocess
import webbrowser
import datetime
import json
import time
import math
import shutil
from urllib.parse import quote_plus

# ------------------------ Helper utilities ------------------------

def which(cmd):
    return shutil.which(cmd) is not None

TERMUX_TTS = which("termux-tts-speak")
TERMUX_MEDIA = which("termux-media-player")

# Try pyttsx3 fallback if termux-tts isn't available
try:
    import pyttsx3
    _pytt_engine = pyttsx3.init()
except Exception:
    _pytt_engine = None


def speak(text, block=False):
    """Speak using termux-tts if available, else pyttsx3, else print."""
    print("Jarvis:", text)
    if TERMUX_TTS:
        cmd = ["termux-tts-speak", text]
        # Non-blocking by default so prompt remains usable
        if block:
            subprocess.run(cmd)
        else:
            subprocess.Popen(cmd)
    elif _pytt_engine:
        if block:
            _pytt_engine.say(text)
            _pytt_engine.runAndWait()
        else:
            # background speak
            try:
                _pytt_engine.say(text)
                _pytt_engine.runAndWait()
            except Exception:
                pass
    else:
        # no TTS available; already printed
        pass


def open_url(url):
    try:
        webbrowser.open(url)
        speak("Opening browser")
    except Exception as e:
        speak(f"Unable to open URL: {e}")


# Safe calculator: allow math functions
SAFE_MATH = {k: getattr(math, k) for k in dir(math) if not k.startswith("__")}
SAFE_MATH.update({
    'abs': abs,
    'round': round,
})


def safe_eval(expr):
    """Evaluate a math expression safely using eval with a restricted globals dict."""
    try:
        expr = expr.strip()
        # ban __
        if "__" in expr:
            raise ValueError("Invalid expression")
        return eval(expr, {"__builtins__": {}}, SAFE_MATH)
    except Exception as e:
        return f"Error: {e}"


# ------------------------ Optional Vosk voice input ------------------------
VOSK_AVAILABLE = False
try:
    import wave
    from vosk import Model, KaldiRecognizer
    VOSK_AVAILABLE = True
except Exception:
    VOSK_AVAILABLE = False


def transcribe_vosk(wav_path, model_path=None):
    """Transcribe a WAV file using VOSK model. model_path should point to a folder with the model."""
    if not VOSK_AVAILABLE:
        return None, "vosk not installed"
    if model_path is None:
        # common location suggestion
        model_path = "/sdcard/vosk-model-small-en-us-0.15"
    if not os.path.isdir(model_path):
        return None, f"Vosk model not found at {model_path}"
    wf = wave.open(wav_path, "rb")
    if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() not in (8000, 16000, 32000, 44100):
        # VOSK likes mono 16-bit WAV; we won't convert here.
        pass
    model = Model(model_path)
    rec = KaldiRecognizer(model, wf.getframerate())
    results = []
    while True:
        data = wf.readframes(4000)
        if len(data) == 0:
            break
        if rec.AcceptWaveform(data):
            res = json.loads(rec.Result())
            results.append(res.get('text', ''))
    final = json.loads(rec.FinalResult())
    results.append(final.get('text', ''))
    return ' '.join([r for r in results if r]), None


# ------------------------ Command handlers ------------------------

def handle_time(_args):
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    speak(f"Current time is {now}")


def handle_search(args):
    if not args:
        speak("What should I search for?")
        return
    q = quote_plus(' '.join(args))
    url = f"https://www.google.com/search?q={q}"
    open_url(url)


def handle_open(args):
    if not args:
        speak("Which URL should I open? Say or type the URL")
        return
    url = args[0]
    if not (url.startswith('http://') or url.startswith('https://')):
        url = 'http://' + url
    open_url(url)


def handle_play(args):
    if not args:
        speak("What should I play? Provide a file path or a URL")
        return
    target = args[0]
    if TERMUX_MEDIA:
        try:
            subprocess.Popen(["termux-media-player", "play", target])
            speak("Playing media")
        except Exception as e:
            speak(f"Play failed: {e}")
    else:
        speak("termux-media-player not available. Try opening the URL in browser.")
        open_url(target)


def handle_note(args):
    if not args:
        speak("What should I note?")
        return
    text = ' '.join(args)
    notes_file = os.path.expanduser('~/jarvis_notes.txt')
    with open(notes_file, 'a', encoding='utf-8') as f:
        now = datetime.datetime.now().isoformat()
        f.write(f"[{now}] {text}\n")
    speak(f"Saved note to {notes_file}")


def handle_calc(args):
    if not args:
        speak("What should I calculate?")
        return
    expr = ' '.join(args)
    res = safe_eval(expr)
    speak(f"Result: {res}")


def handle_help(_args=None):
    help_text = (
        "Available commands:\n"
        "- time: tell current time\n"
        "- search <text>: web search\n"
        "- open <url>: open URL in browser\n"
        "- play <path-or-url>: play media file or URL\n"
        "- note <text>: save a note\n"
        "- calc <expression>: calculate math expression\n"
        "- voice: attempt voice input (optional, needs Vosk or termux-speech-to-text)\n"
        "- help: show this message\n"
        "- exit: quit"
    )
    speak(help_text)


# ------------------------ Interactive loop ------------------------


def listen_text():
    """Ask the user to type a command. Returns the raw text."""
    try:
        # Use input() so user can type in Termux
        txt = input('You: ')
        return txt.strip()
    except EOFError:
        return 'exit'


def handle_voice_flow():
    """Optional voice flow using VOSK if installed and a recorded WAV is available."""
    speak('Voice mode: recording is not automated by this script.\nPlease record audio to /sdcard/jarvis_input.wav (for example using Termux:API or another app), then I will transcribe it with Vosk if installed.')
    wav_path = input('Path to WAV file (or leave empty to cancel): ').strip()
    if not wav_path:
        speak('Cancelled voice input')
        return
    model_path = input('Vosk model path (press enter for /sdcard/vosk-model-small-en-us-0.15): ').strip() or None
    text, err = transcribe_vosk(wav_path, model_path)
    if err:
        speak(f'Could not transcribe: {err}')
        return
    if text:
        speak(f'I heard: {text}')
        process_command(text)
    else:
        speak('No speech recognized')


def process_command(raw):
    if not raw:
        return
    parts = shlex.split(raw)
    if not parts:
        return
    cmd = parts[0].lower()
    args = parts[1:]
    if cmd in ('exit', 'quit', 'bye'):
        speak('Goodbye!', block=True)
        sys.exit(0)
    elif cmd == 'time':
        handle_time(args)
    elif cmd == 'search':
        handle_search(args)
    elif cmd == 'open':
        handle_open(args)
    elif cmd == 'play':
        handle_play(args)
    elif cmd == 'note':
        handle_note(args)
    elif cmd in ('calc', 'calculate'):
        handle_calc(args)
    elif cmd == 'help':
        handle_help()
    elif cmd == 'voice':
        # voice helper
        if VOSK_AVAILABLE:
            handle_voice_flow()
        else:
            speak('Vosk not available. Install vosk and download a model to use voice transcription, or use typed commands.')
    else:
        speak("I don't know that command. Say 'help' for the list of commands.")


def main():
    speak('Hello! I am Jarvis — lightweight Termux assistant. Type help to see commands.')
    while True:
        raw = listen_text()
        if not raw:
            continue
        process_command(raw)


if __name__ == '__main__':
    main()

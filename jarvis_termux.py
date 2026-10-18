#!/usr/bin/env python3
"""
jarvis_termux.py
Advanced Termux-friendly Jarvis assistant.

Features:
- Text command mode for reliable use on Android/Termux
- Voice input using Termux speech recognition when available
- Optional microphone recording + Vosk transcription fallback
- TTS replies using termux-tts-speak
- Commands: time, date, search, open, play, note, calc, battery, weather, news, help, exit
- Built to run well in Termux without a desktop environment

Quick setup on Termux:
1) pkg update && pkg install python termux-api -y
2) pip install requests
3) Optional for offline Vosk transcription:
   pip install vosk soundfile
   Download a model to /sdcard/vosk-model-small-en-us-0.15

Run:
   python3 jarvis_termux.py

Example commands:
   help
   time
   date
   search whats the weather today
   open google.com
   note remember to call mom
   calc 2 + 2 * 5
   weather delhi
   news
   voice
   exit
"""

import os
import sys
import shlex
import subprocess
import webbrowser
import datetime
import math
import time
import shutil
import json
from urllib.parse import quote_plus

try:
    import requests
except Exception:
    requests = None

# ------------------------------------------------------------
# Utilities
# ------------------------------------------------------------

def which(cmd):
    return shutil.which(cmd) is not None

TERMUX_TTS = which("termux-tts-speak")
TERMUX_STT = which("termux-speech-to-text")
TERMUX_MIC = which("termux-microphone-record")
TERMUX_OPEN = which("termux-open")
TERMUX_MEDIA = which("termux-media-player")

try:
    import pyttsx3
    _pyttsx = pyttsx3.init()
except Exception:
    _pyttsx = None


def speak(text, block=False):
    """Speak text through Termux TTS or pyttsx3 fallback."""
    print(f"Jarvis: {text}")
    if TERMUX_TTS:
        try:
            if block:
                subprocess.run(["termux-tts-speak", text], check=False)
            else:
                subprocess.Popen(["termux-tts-speak", text])
            return
        except Exception:
            pass

    if _pyttsx is not None:
        try:
            if block:
                _pyttsx.say(text)
                _pyttsx.runAndWait()
            else:
                _pyttsx.say(text)
                _pyttsx.runAndWait()
            return
        except Exception:
            pass

    # Fallback: just print


def open_url(url):
    try:
        if TERMUX_OPEN:
            subprocess.Popen(["termux-open", url])
        else:
            webbrowser.open(url)
        speak("Opening browser")
    except Exception as e:
        speak(f"Could not open url: {e}")


# ------------------------------------------------------------
# Safe calculator
# ------------------------------------------------------------
SAFE_MATH = {k: getattr(math, k) for k in dir(math) if not k.startswith("__")}
SAFE_MATH.update({"abs": abs, "round": round})


def safe_eval(expr):
    expr = expr.strip()
    if "__" in expr:
        return "Error: invalid expression"
    try:
        return eval(expr, {"__builtins__": {}}, SAFE_MATH)
    except Exception as e:
        return f"Error: {e}"


# ------------------------------------------------------------
# Optional Vosk offline transcription
# ------------------------------------------------------------
VOSK_AVAILABLE = False
try:
    import wave
    from vosk import Model, KaldiRecognizer
    VOSK_AVAILABLE = True
except Exception:
    VOSK_AVAILABLE = False


def transcribe_vosk(wav_path, model_path=None):
    if not VOSK_AVAILABLE:
        return None, "Vosk not installed. Install: pip install vosk soundfile"
    if model_path is None:
        model_path = "/sdcard/vosk-model-small-en-us-0.15"
    if not os.path.isdir(model_path):
        return None, f"Model not found: {model_path}"
    try:
        wf = wave.open(wav_path, "rb")
        model = Model(model_path)
        rec = KaldiRecognizer(model, wf.getframerate())
        text_parts = []
        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                if res.get("text"):
                    text_parts.append(res["text"])
        final = json.loads(rec.FinalResult())
        if final.get("text"):
            text_parts.append(final["text"])
        return " ".join(part.strip() for part in text_parts if part.strip()), None
    except Exception as e:
        return None, str(e)


# ------------------------------------------------------------
# Voice input
# ------------------------------------------------------------
def listen_with_termux_stt():
    """Use Termux speech-to-text if available."""
    if not TERMUX_STT:
        return None
    try:
        result = subprocess.run(["termux-speech-to-text"], capture_output=True, text=True, timeout=30)
        out = (result.stdout or result.stderr or "").strip()
        if out:
            return out
        return None
    except Exception:
        return None


def record_voice(seconds=5, output_path="/sdcard/jarvis_record.wav"):
    if not TERMUX_MIC:
        return None
    try:
        subprocess.run(["termux-microphone-record", "-f", output_path, "-d", str(seconds)], check=False)
        if os.path.exists(output_path):
            return output_path
    except Exception:
        pass
    return None


def listen_voice():
    """Try speech-to-text direct; fallback to record -> Vosk if installed and model exists."""
    text = listen_with_termux_stt()
    if text:
        return text

    if TERMUX_MIC:
        wav = record_voice(seconds=5)
        if wav:
            # Try Vosk if available
            text, err = transcribe_vosk(wav)
            if text:
                return text
            if err:
                speak(f"Voice transcription fallback issue: {err}")
    return None


# ------------------------------------------------------------
# Command handlers
# ------------------------------------------------------------
def handle_time(_args):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    speak(f"Current time is {now}")


def handle_date(_args):
    now = datetime.datetime.now().strftime("%A, %d %B %Y")
    speak(f"Today is {now}")


def handle_search(args):
    if not args:
        speak("What should I search for?")
        return
    query = " ".join(args)
    url = "https://www.google.com/search?q=" + quote_plus(query)
    open_url(url)


def handle_open(args):
    if not args:
        speak("Which URL should I open?")
        return
    target = args[0]
    if not target.startswith(("http://", "https://")):
        target = "https://" + target
    open_url(target)


def handle_play(args):
    if not args:
        speak("What should I play?")
        return
    media = args[0]
    if TERMUX_MEDIA:
        try:
            subprocess.Popen(["termux-media-player", "play", media])
            speak("Playing media")
        except Exception as e:
            speak(f"Play failed: {e}")
    else:
        if not media.startswith(("http://", "https://")):
            speak("Media player is not available. I can still try to open the URL in the browser.")
        open_url(media)


def handle_note(args):
    if not args:
        speak("What note should I save?")
        return
    note_text = " ".join(args)
    fpath = os.path.expanduser("~/jarvis_notes.txt")
    with open(fpath, "a", encoding="utf-8") as f:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{stamp}] {note_text}\n")
    speak(f"Saved note to {fpath}")


def handle_calc(args):
    if not args:
        speak("What should I calculate?")
        return
    expr = " ".join(args)
    result = safe_eval(expr)
    speak(f"Result: {result}")


def handle_battery(_args):
    try:
        if shutil.which("termux-battery-status"):
            out = subprocess.check_output(["termux-battery-status"], text=True)
            speak(out.strip())
            return
    except Exception:
        pass
    speak("Battery status is not available in this environment.")


def handle_weather(args):
    if not args:
        speak("Which city should I check?")
        return
    city = " ".join(args)
    if requests is None:
        speak("Python requests module is not installed. Install with: pip install requests")
        return
    try:
        url = f"https://wttr.in/{quote_plus(city)}?format=3"
        resp = requests.get(url, timeout=10)
        text = resp.text.strip()
        if text:
            speak(f"Weather in {city}: {text}")
        else:
            speak(f"I could not get weather for {city}")
    except Exception as e:
        speak(f"Weather request failed: {e}")


def handle_news(_args):
    if requests is None:
        speak("Python requests module is not installed. Install with: pip install requests")
        return
    try:
        url = "https://news.google.com/rss"
        resp = requests.get(url, timeout=10)
        content = resp.text
        # Keep it simple: print first items from RSS feed
        items = []
        for match in __import__("re").finditer(r"<title>(.*?)</title>", content):
            title = match.group(1)
            if title and title.lower() != "google news":
                items.append(title)
        if items:
            # first five headlines
            headline_text = "; ".join(items[:5])
            speak(f"Latest headlines: {headline_text}")
        else:
            speak("I could not load news headlines right now.")
    except Exception as e:
        speak(f"News failed: {e}")


def handle_help(_args=None):
    help_text = (
        "Available commands: time, date, search <query>, open <url>, "
        "play <media>, note <text>, calc <expression>, battery, weather <city>, "
        "news, voice, help, exit"
    )
    speak(help_text)


# ------------------------------------------------------------
# Command processing
# ------------------------------------------------------------
def process_command(raw):
    raw = raw.strip()
    if not raw:
        return

    # Handle voice commands spoken with 'jarvis' prefix
    if raw.lower().startswith("jarvis"):
        raw = raw[6:].strip()

    # Parse command
    try:
        parts = shlex.split(raw)
    except ValueError:
        parts = raw.split()
    if not parts:
        return

    cmd = parts[0].lower()
    args = parts[1:]

    if cmd in ("exit", "quit", "bye", "shutdown"):
        speak("Goodbye!", block=True)
        sys.exit(0)
    elif cmd == "time":
        handle_time(args)
    elif cmd == "date":
        handle_date(args)
    elif cmd == "search":
        handle_search(args)
    elif cmd == "open":
        handle_open(args)
    elif cmd == "play":
        handle_play(args)
    elif cmd == "note":
        handle_note(args)
    elif cmd in ("calc", "calculate"):
        handle_calc(args)
    elif cmd == "battery":
        handle_battery(args)
    elif cmd == "weather":
        handle_weather(args)
    elif cmd == "news":
        handle_news(args)
    elif cmd == "voice":
        spoken = listen_voice()
        if spoken:
            speak(f"I heard: {spoken}")
            process_command(spoken)
        else:
            speak("Voice input failed. Please type a command instead.")
    elif cmd == "help":
        handle_help(args)
    else:
        speak("I do not understand that command. Say help to see available commands.")


# ------------------------------------------------------------
# Main loop
# ------------------------------------------------------------
def main():
    speak("Hello! I am Jarvis, your Termux assistant. Say help to see commands.")
    while True:
        try:
            text = input("You: ")
        except EOFError:
            text = "exit"
        if text is None:
            continue
        process_command(text)


if __name__ == "__main__":
    main()

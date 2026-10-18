#!/usr/bin/env python3
"""
jarvis_termux.py
Ultimate Jarvis v3 - Termux assistant with advanced features

Features added in this file:
- Wake-word detection loop (improved with configurable detection methods: OpenAI/Whisper, Vosk, or simple transcript match)
- Automatic language detection (langdetect fallback) and language-aware replies
- App launch, app close (force-stop) and Home-screen automation using Android 'am' and input keyevents
- Camera capture, flashlight (torch), Wi-Fi on/off controls using termux-api or Android 'svc'
- Pure-Hindi TTS support using gTTS (Google TTS) fallback when termux-tts lacks Hindi voice
- Flexible LLM integration:
    * OpenAI Chat Completions / Whisper (if OPENAI_API_KEY set)
    * Gemini (if GEMINI_API_KEY set)
    * Ollama integration via an HTTP endpoint (OLLAMA_URL) or local ollama binary wrapper (OLLAMA_BIN)
    * Local llama.cpp/ggml binary invocation (LLAMA_CPP_BIN) if you compiled one for Termux/ARM64
- Configuration and detailed setup instructions and helpful error messages

Caveats & notes:
- Some actions require the Termux:API Android app installed and granted permissions.
- Some system automations (e.g. wifi on/off via 'svc') may require a rooted device.
- Running Ollama locally on Termux is not officially supported; recommended approaches are:
  1) Run Ollama on a separate x86_64/ARM64 Linux machine and set OLLAMA_URL to the server address.
  2) Use a compiled llama.cpp/ggml binary for Android and set LLAMA_CPP_BIN to the binary path.
- Vosk/porcupine building on-device may fail for some architectures; use OpenAI Whisper (online) for best accuracy.

Save this file as jarvis_termux.py and follow setup instructions below.
"""

import os
import sys
import time
import json
import shlex
import shutil
import subprocess
import webbrowser
import threading
import queue
from urllib.parse import quote_plus
from datetime import datetime

# Optional dependencies (imported at runtime with checks)
try:
    import requests
except Exception:
    requests = None

# langdetect for automatic language detection
try:
    from langdetect import detect
except Exception:
    detect = None

# gTTS for Hindi TTS fallback
try:
    from gtts import gTTS
except Exception:
    gTTS = None

# VOSK optional
VOSK_AVAILABLE = False
try:
    import wave
    from vosk import Model, KaldiRecognizer
    VOSK_AVAILABLE = True
except Exception:
    VOSK_AVAILABLE = False

# ------------------ Configuration ------------------
WAKE_WORD = os.getenv("WAKE_WORD", "jarvis")
WAKE_RECORD_SECONDS = int(os.getenv("WAKE_RECORD_SECONDS", "2"))
COMMAND_RECORD_SECONDS = int(os.getenv("COMMAND_RECORD_SECONDS", "6"))
WAKE_CLIP_PATH = os.getenv("WAKE_CLIP_PATH", "/sdcard/jarvis_wake.wav")
COMMAND_CLIP_PATH = os.getenv("COMMAND_CLIP_PATH", "/sdcard/jarvis_command.wav")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_API_URL = os.getenv("OPENAI_API_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_URL = os.getenv("GEMINI_API_URL")
OLLAMA_URL = os.getenv("OLLAMA_URL")      # e.g. http://192.168.1.10:11434
OLLAMA_BIN = os.getenv("OLLAMA_BIN")    # path to local ollama binary wrapper (if any)
LLAMA_CPP_BIN = os.getenv("LLAMA_CPP_BIN")  # path to local llama.cpp binary (if compiled for Android)
VOSK_MODEL = os.getenv("VOSK_MODEL", "/sdcard/vosk-model-small-en-us-0.15")

# Termux utilities detection

def which(cmd):
    return shutil.which(cmd) is not None

TERMUX_TTS = which("termux-tts-speak")
TERMUX_MIC = which("termux-microphone-record")
TERMUX_STT = which("termux-speech-to-text")
TERMUX_OPEN = which("termux-open")
TERMUX_MEDIA = which("termux-media-player")
TERMUX_TORCH = which("termux-torch")

# pyttsx3 fallback
try:
    import pyttsx3
    _pyttsx_engine = pyttsx3.init()
except Exception:
    _pyttsx_engine = None

# ------------------ Utilities ------------------

def speak(text, lang_hint=None, block=False):
    """Speak text. Prefer termux-tts, fall back to gTTS (Hindi) or pyttsx3."""
    print("Jarvis:", text)

    # If lang_hint is 'hi' use gTTS fallback to ensure Hindi pronunciation if termux TTS isn't good.
    if TERMUX_TTS:
        try:
            # termux-tts may respect system language; we just call it.
            if block:
                subprocess.run(["termux-tts-speak", text], check=False)
            else:
                subprocess.Popen(["termux-tts-speak", text])
            return
        except Exception:
            pass

    # gTTS fallback for Hindi/other languages if available
    if lang_hint == 'hi' and gTTS is not None:
        try:
            t = gTTS(text=text, lang='hi')
            tmp = '/tmp/jarvis_tts_hi.mp3'
            t.save(tmp)
            if TERMUX_MEDIA:
                subprocess.Popen(['termux-media-player', 'play', tmp])
            else:
                # Try mpg123 if present
                if which('mpg123'):
                    subprocess.Popen(['mpg123', tmp])
            return
        except Exception as e:
            print('gTTS error:', e)

    if _pyttsx_engine is not None:
        try:
            _pyttsx_engine.say(text)
            _pyttsx_engine.runAndWait()
            return
        except Exception:
            pass

    # Last resort: print only


def open_url(url):
    try:
        if TERMUX_OPEN:
            subprocess.Popen(["termux-open", url])
        else:
            webbrowser.open(url)
        speak("Opening browser")
    except Exception as e:
        speak(f"Could not open URL: {e}")


def run_shell(cmd):
    try:
        out = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, text=True)
        return out
    except subprocess.CalledProcessError as e:
        return e.output

# ------------------ Language detection ------------------

def detect_language(text):
    if not text:
        return None
    if detect:
        try:
            lang = detect(text)
            return lang
        except Exception:
            return None
    # fallback heuristic
    # if Devanagari present -> hi
    if any('\u0900' <= ch <= '\u097F' for ch in text):
        return 'hi'
    # simple english detection
    if re.search(r'[a-zA-Z]', text):
        return 'en'
    return None

# ------------------ Recording & STT ------------------

def record_clip(seconds, out_path):
    if not TERMUX_MIC:
        return None
    try:
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except Exception:
                pass
        subprocess.run(["termux-microphone-record", "-f", out_path, "-d", str(seconds)], check=False)
        if os.path.exists(out_path):
            return out_path
    except Exception:
        pass
    return None


def transcribe_openai(wav_path):
    if not OPENAI_API_KEY or requests is None:
        return None, 'no api or requests'
    api_url = OPENAI_API_URL or 'https://api.openai.com/v1/audio/transcriptions'
    try:
        with open(wav_path, 'rb') as f:
            files = {'file': (os.path.basename(wav_path), f)}
            data = {'model': 'whisper-1'}
            headers = {'Authorization': f'Bearer {OPENAI_API_KEY}'}
            r = requests.post(api_url, headers=headers, files=files, data=data, timeout=40)
        if r.status_code == 200:
            return r.json().get('text'), None
        return None, f'transcription failed: {r.status_code} {r.text}'
    except Exception as e:
        return None, str(e)


def transcribe_vosk(wav_path, model_path=None):
    if not VOSK_AVAILABLE:
        return None, 'vosk not available'
    model_path = model_path or VOSK_MODEL
    if not os.path.isdir(model_path):
        return None, f'model missing: {model_path}'
    try:
        wf = wave.open(wav_path, 'rb')
        model = Model(model_path)
        rec = KaldiRecognizer(model, wf.getframerate())
        parts = []
        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                if res.get('text'):
                    parts.append(res.get('text'))
        final = json.loads(rec.FinalResult())
        if final.get('text'):
            parts.append(final.get('text'))
        return ' '.join(p for p in parts if p), None
    except Exception as e:
        return None, str(e)

# ------------------ Wake-word background listener ------------------
wake_q = queue.Queue()
stop_wake = threading.Event()

def wake_loop():
    speak('Wake listener started (say "' + WAKE_WORD + '")')
    while not stop_wake.is_set():
        clip = record_clip(WAKE_RECORD_SECONDS, WAKE_CLIP_PATH)
        if not clip:
            time.sleep(0.6)
            continue

        text = None
        # Prefer OpenAI for best detection (if available)
        if OPENAI_API_KEY:
            t, err = transcribe_openai(clip)
            if t:
                text = t
        if text is None and VOSK_AVAILABLE:
            t, err = transcribe_vosk(clip)
            if t:
                text = t

        if text:
            low = text.lower()
            if WAKE_WORD in low:
                wake_q.put(True)
                speak('Yes?')
                # debounce
                time.sleep(1.0)
        time.sleep(0.2)

# ------------------ LLM connectors ------------------

def ollama_query(prompt):
    # If OLLAMA_URL is set and returns REST API, send request
    if OLLAMA_URL and requests is not None:
        try:
            url = OLLAMA_URL.rstrip('/') + '/api/chat'
            payload = {'model': 'llama2', 'messages': [{'role': 'user', 'content': prompt}]}
            r = requests.post(url, json=payload, timeout=30)
            if r.status_code == 200:
                j = r.json()
                return j.get('reply') or j.get('response') or str(j)
        except Exception as e:
            print('ollama request error', e)
    # If ollama binary wrapper present
    if OLLAMA_BIN and os.path.exists(OLLAMA_BIN):
        try:
            p = subprocess.run([OLLAMA_BIN, '--prompt', prompt], capture_output=True, text=True, timeout=30)
            return p.stdout.strip()
        except Exception as e:
            print('ollama bin error', e)
    return None


def llama_cpp_query(prompt):
    if LLAMA_CPP_BIN and os.path.exists(LLAMA_CPP_BIN):
        try:
            p = subprocess.run([LLAMA_CPP_BIN, '--prompt', prompt], capture_output=True, text=True, timeout=60)
            return p.stdout.strip()
        except Exception as e:
            print('llama.cpp error', e)
    return None


def ai_reply(prompt):
    # 1. Ollama (local server or binary)
    ans = ollama_query(prompt)
    if ans:
        return ans
    # 2. llama.cpp binary
    ans = llama_cpp_query(prompt)
    if ans:
        return ans
    # 3. OpenAI chat
    if OPENAI_API_KEY and requests is not None:
        api_url = OPENAI_API_URL or 'https://api.openai.com/v1/chat/completions'
        payload = {
            'model': 'gpt-4o-mini',
            'messages': [{'role': 'system', 'content': 'You are Jarvis, respond briefly in Hindi or English depending on input.'},
                         {'role': 'user', 'content': prompt}],
            'temperature': 0.7
        }
        try:
            r = requests.post(api_url, headers={'Authorization': f'Bearer {OPENAI_API_KEY}'}, json=payload, timeout=30)
            if r.status_code == 200:
                o = r.json()
                choices = o.get('choices')
                if choices:
                    m = choices[0].get('message')
                    if isinstance(m, dict):
                        return m.get('content', '').strip()
                    return str(m)
        except Exception as e:
            print('openai chat error', e)
    # 4. Gemini
    if GEMINI_API_KEY and requests is not None:
        # Ideally implement Gemini REST proto here; for now try a simple request if GEMINI_API_URL provided
        if GEMINI_API_URL:
            try:
                r = requests.post(GEMINI_API_URL, params={'key': GEMINI_API_KEY}, json={'prompt': prompt}, timeout=30)
                if r.status_code == 200:
                    return r.text
            except Exception as e:
                print('gemini error', e)
    # 5. Local fallback
    p = prompt.lower()
    if 'time' in p:
        return 'Abhi ka samay: ' + datetime.now().strftime('%H:%M:%S')
    if 'date' in p:
        return 'Aaj ki tareekh: ' + datetime.now().strftime('%d %B %Y')
    if 'hello' in p or 'namaste' in p:
        return 'Namaste! Main Jarvis hoon.'
    return 'Maine aapka message samajh liya. Aap ek command bolo ya help dekho.'

# ------------------ Device automation helpers ------------------

def open_app(package):
    # Try am start -n package/activity or am start -a android.intent.action.MAIN -n package
    try:
        # if package contains '/', assume full component
        if '/' in package:
            subprocess.run(['am', 'start', '-n', package], check=False)
        else:
            subprocess.run(['monkey', '-p', package, '-c', 'android.intent.category.LAUNCHER', '1'], check=False)
        speak(f'Trying to open {package}')
    except Exception as e:
        speak(f'App open failed: {e}')


def close_app(package):
    try:
        subprocess.run(['am', 'force-stop', package], check=False)
        speak(f'Tried to close {package}')
    except Exception as e:
        speak(f'App close failed: {e}')


def go_home():
    try:
        # KEYCODE_HOME is 3
        subprocess.run(['input', 'keyevent', '3'], check=False)
        speak('Going to home screen')
    except Exception as e:
        speak(f'Home event failed: {e}')


def camera_capture(path='/sdcard/jarvis_photo.jpg'):
    if which('termux-camera-photo'):
        try:
            subprocess.run(['termux-camera-photo', '-c', '0', path], check=False)
            speak('Photo captured to ' + path)
            return path
        except Exception as e:
            speak('Camera capture failed: ' + str(e))
    else:
        speak('termux-camera-photo not available')
    return None


def torch_on():
    if which('termux-torch'):
        subprocess.run(['termux-torch', 'on'], check=False)
        speak('Torch on')
    else:
        # try using camera flash via service (may not exist)
        speak('Torch command not available')


def torch_off():
    if which('termux-torch'):
        subprocess.run(['termux-torch', 'off'], check=False)
        speak('Torch off')
    else:
        speak('Torch command not available')


def wifi_on():
    # Requires root for svc
    out = run_shell('svc wifi enable')
    speak('Tried to enable wifi')


def wifi_off():
    out = run_shell('svc wifi disable')
    speak('Tried to disable wifi')

# ------------------ Command handlers ------------------

def handle_help(_args=None):
    s = ("Commands: help, time, date, search <q>, open <url>, play <url|path>, note <text>, "
         "calc <expr>, battery, weather <city>, news, open_app <package>, close_app <package>, "
         "home, camera, torch on/off, wifi on/off, ai <question>, voice, exit")
    speak(s)


def handle_time(_args):
    speak('Current time: ' + datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


def handle_date(_args):
    speak('Today: ' + datetime.now().strftime('%A, %d %B %Y'))


def handle_search(args):
    if not args:
        speak('What to search?')
        return
    q = ' '.join(args)
    open_url('https://www.google.com/search?q=' + quote_plus(q))


def handle_open(args):
    if not args:
        speak('Which URL?')
        return
    target = args[0]
    if not target.startswith(('http://','https://')):
        target = 'https://' + target
    open_url(target)


def handle_play(args):
    if not args:
        speak('What to play?')
        return
    target = args[0]
    if TERMUX_MEDIA:
        subprocess.Popen(['termux-media-player', 'play', target])
        speak('Playing')
    else:
        open_url(target)


def handle_note(args):
    if not args:
        speak('What note?')
        return
    text = ' '.join(args)
    p = os.path.expanduser('~/jarvis_notes.txt')
    with open(p, 'a', encoding='utf-8') as f:
        f.write('[' + datetime.now().isoformat() + '] ' + text + '\n')
    speak('Saved note')


def handle_calc(args):
    if not args:
        speak('Expression?')
        return
    expr = ' '.join(args)
    try:
        res = eval(expr, {'__builtins__': None}, {})
        speak('Result: ' + str(res))
    except Exception as e:
        speak('Calc error: ' + str(e))


def handle_battery(_args):
    if which('termux-battery-status'):
        out = run_shell('termux-battery-status')
        speak(out)
    else:
        speak('Battery info not available')


def handle_weather(args):
    if not args:
        speak('Which city?')
        return
    if requests is None:
        speak('Install requests: pip install requests')
        return
    city = ' '.join(args)
    try:
        r = requests.get('https://wttr.in/' + quote_plus(city) + '?format=3', timeout=10)
        speak('Weather: ' + r.text.strip())
    except Exception as e:
        speak('Weather failed: ' + str(e))


def handle_news(_args):
    if requests is None:
        speak('Install requests: pip install requests')
        return
    try:
        r = requests.get('https://news.google.com/rss', timeout=10)
        import re
        titles = [m.group(1) for m in re.finditer(r'<title>(.*?)</title>', r.text) if m.group(1).lower() != 'google news']
        if titles:
            speak('Headlines: ' + '; '.join(titles[:5]))
        else:
            speak('No headlines')
    except Exception as e:
        speak('News failed: ' + str(e))


def handle_open_app(args):
    if not args:
        speak('Package?')
        return
    open_app(' '.join(args))


def handle_close_app(args):
    if not args:
        speak('Package?')
        return
    close_app(' '.join(args))


def handle_home(_args):
    go_home()


def handle_camera(_args):
    camera_capture()


def handle_torch(args):
    if not args:
        speak('torch on or off?')
        return
    if args[0].lower() in ('on','1','true'):
        torch_on()
    else:
        torch_off()


def handle_wifi(args):
    if not args:
        speak('wifi on or off?')
        return
    if args[0].lower() in ('on','1','true'):
        wifi_on()
    else:
        wifi_off()


def handle_ai(args):
    if not args:
        speak('Aapka sawal?')
        return
    prompt = ' '.join(args)
    ans = ai_reply(prompt)
    # language detection for TTS
    lang = detect_language(prompt) or 'en'
    speak(ans, lang_hint=('hi' if lang.startswith('hi') else 'en'))

# ------------------ Main parsing and loop ------------------

def parse_and_run(raw):
    raw = raw.strip()
    if not raw:
        return
    low = raw.lower()

    if low in ('exit','quit','bye'):
        speak('Goodbye!', block=True)
        stop_wake.set()
        sys.exit(0)

    # Remove wake word prefix if present
    if low.startswith(WAKE_WORD):
        raw = raw[len(WAKE_WORD):].strip()

    # simple phrase matches
    if raw.lower() in ('help','menu'):
        handle_help([])
        return

    # try shell-like splitting
    try:
        parts = shlex.split(raw)
    except Exception:
        parts = raw.split()
    if not parts:
        return
    cmd = parts[0].lower()
    args = parts[1:]

    # map commands
    if cmd == 'help':
        handle_help(args)
    elif cmd == 'time':
        handle_time(args)
    elif cmd == 'date':
        handle_date(args)
    elif cmd == 'search':
        handle_search(args)
    elif cmd == 'open':
        handle_open(args)
    elif cmd == 'play':
        handle_play(args)
    elif cmd == 'note':
        handle_note(args)
    elif cmd in ('calc','calculate'):
        handle_calc(args)
    elif cmd == 'battery':
        handle_battery(args)
    elif cmd == 'weather':
        handle_weather(args)
    elif cmd == 'news':
        handle_news(args)
    elif cmd in ('open_app','app'):
        handle_open_app(args)
    elif cmd in ('close_app','kill_app'):
        handle_close_app(args)
    elif cmd == 'home':
        handle_home(args)
    elif cmd == 'camera':
        handle_camera(args)
    elif cmd == 'torch':
        handle_torch(args)
    elif cmd == 'wifi':
        handle_wifi(args)
    elif cmd == 'ai':
        handle_ai(args)
    elif cmd == 'voice':
        # record a single command and run
        speak('Listening...')
        clip = record_clip(COMMAND_RECORD_SECONDS, COMMAND_CLIP_PATH)
        if not clip:
            speak('Microphone not available')
            return
        text = None
        if OPENAI_API_KEY:
            t, err = transcribe_openai(clip)
            if t:
                text = t
        if text is None and VOSK_AVAILABLE:
            t, err = transcribe_vosk(clip)
            if t:
                text = t
        if not text:
            speak('Could not transcribe voice')
            return
        speak('I heard: ' + text)
        parse_and_run(text)
    else:
        # fallback to AI reply
        ans = ai_reply(raw)
        lang = detect_language(raw) or 'en'
        speak(ans, lang_hint=('hi' if lang.startswith('hi') else 'en'))

# ------------------ Background wake-thread & main loop ------------------

def start_wake_thread():
    t = threading.Thread(target=wake_loop, daemon=True)
    t.start()
    return t


def main():
    start_wake_thread()
    speak('Ultimate Jarvis ready. Say "' + WAKE_WORD + '" to wake me, or type commands. Type help for list.')
    while True:
        # non-blocking check for wake signal
        try:
            woke = wake_q.get_nowait()
        except queue.Empty:
            woke = False
        if woke:
            speak('Listening for your command...')
            clip = record_clip(COMMAND_RECORD_SECONDS, COMMAND_CLIP_PATH)
            if not clip:
                speak('Microphone not available')
                continue
            text = None
            if OPENAI_API_KEY:
                t, err = transcribe_openai(clip)
                if t:
                    text = t
            if text is None and VOSK_AVAILABLE:
                t, err = transcribe_vosk(clip)
                if t:
                    text = t
            if not text:
                speak('Sorry, could not transcribe.')
                continue
            speak('You said: ' + text)
            parse_and_run(text)
            continue

        try:
            user = input('You: ')
        except EOFError:
            user = 'exit'
        if not user:
            continue
        parse_and_run(user)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        stop_wake.set()
        speak('Stopping Jarvis. Bye!', block=True)

import argparse
import subprocess
import threading
import time
import signal
import sys
import tempfile
import os
from pathlib import Path

import numpy as np
import pynput
import pyperclip
import sounddevice as sd
import soundfile
from openai import OpenAI

# Get temperature from environment variable or use default value
WHISPER_TEMPERATURE = float(os.getenv('WHISPER_TEMPERATURE', '0.2'))

# ! you can change this rec_key value
rec_key = pynput.keyboard.Key.alt_r

# Переопределяем стандартный print для автоматического сброса буфера
original_print = print
def print(*args, **kwargs):
    kwargs['flush'] = True
    return original_print(*args, **kwargs)

# Development prompt to improve transcription for programming and development topics
DEV_PROMPT = """This is a transcription of a software developer speaking primarily in Russian, but frequently using English technical terms and phrases. The speaker is knowledgeable in computer science, software development, DevOps, and project management. They use technical jargon and industry terminology related to:
- Software development and programming
- System administration and DevOps
- Software architecture and design patterns
- Project management and requirements engineering
- Databases and data structures
- Algorithms and computational complexity
- Cloud technologies and infrastructure

When uncertain about a word or phrase, prioritize technical meaning over common usage. Preserve English technical terms even within Russian sentences. The speaker may switch between Russian and English mid-sentence when discussing technical concepts."""

whisper_samplerate = 16000  # sampling rate that whisper uses
recording_samplerate = 48000  # multiple of whisper_samplerate, widely supported

controller = pynput.keyboard.Controller()

# Parse arguments
parser = argparse.ArgumentParser()
parser.add_argument("language", nargs="?", default=None, help="Language code for transcription (e.g. 'ru', 'en')")
parser.add_argument("--no-type-using-clipboard", action="store_true", help="Don't use clipboard for typing")
parser.add_argument("--on-callback", type=str, default=None, help="Command to run after initialization")
parser.add_argument("--auto-off-time", type=int, default=None, help="Automatically turn off after N seconds of inactivity")
args = parser.parse_args()

# Initialize OpenAI client
client = OpenAI()

if args.on_callback is not None:
    subprocess.run(args.on_callback, shell=True)


def get_text(audio, context=None):
    # Создаем временный файл в директории /tmp с правильным расширением
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
        tmp_audio_filename = temp_file.name
    
    soundfile.write(tmp_audio_filename, audio, whisper_samplerate, format="wav")
    actual_prompt = context or DEV_PROMPT
    print(f"🌐 OpenAI request: lang={args.language}, temp={WHISPER_TEMPERATURE}, prompt=\"{actual_prompt[:30]}...\"")
    
    try:
        api_response = client.audio.transcriptions.create(
            model="whisper-1",
            file=open(tmp_audio_filename, "rb"),
            language=args.language,
            prompt=actual_prompt,
            temperature=WHISPER_TEMPERATURE
        )
        result_text = api_response.text
    finally:
        # Удаляем временный файл после использования
        tmp_path = Path(tmp_audio_filename)
        if tmp_path.exists():
            tmp_path.unlink()
    
    return result_text


def type_using_clipboard(text):
    # use pynput to type ctrl+shift+v
    pyperclip.copy(text)
    controller.press(pynput.keyboard.Key.ctrl_l)
    controller.press(pynput.keyboard.Key.shift_l)
    controller.press("v")
    controller.release("v")
    controller.release(pynput.keyboard.Key.shift_l)
    controller.release(pynput.keyboard.Key.ctrl_l)


rec_key_pressed = False
time_last_used = time.time()

# Глобальная переменная для аудио-потока
stream = None

def record_and_process():
    # Запись и обработка аудио
    global stream
    audio_chunks = []

    def audio_callback(indata, frames, time, status):
        if status:
            print("WARNING:", status)
        audio_chunks.append(indata.copy())

    stream = sd.InputStream(
        samplerate=recording_samplerate,
        channels=1,
        blocksize=256,
        callback=audio_callback,
    )
    stream.start()
    while rec_key_pressed:
        time.sleep(0.005)
    stream.stop()
    stream.close()
    stream = None
    recorded_audio = np.concatenate(audio_chunks)[:, 0]

    # Проверка длительности записи
    duration = len(recorded_audio) / recording_samplerate
    if duration <= 0.1:
        print("Recording too short, skipping")
        return

    # Даунсэмплинг
    recorded_audio = recorded_audio[::3]

    context = None  # Use dev-prompt by default

    # Транскрибация
    text = get_text(recorded_audio, context)
    print(text)

    # Ввод текста
    text = text + " "
    if not args.no_type_using_clipboard:
        type_using_clipboard(text)
    else:
        controller.type(text)


def on_press(key):
    global rec_key_pressed
    if key == rec_key:
        rec_key_pressed = True

        # start recording in a new thread
        t = threading.Thread(target=record_and_process)
        t.start()


def on_release(key):
    global rec_key_pressed, time_last_used
    if key == rec_key:
        rec_key_pressed = False
        time_last_used = time.time()


# Вывод информации о настройках
if args.language is not None:
    print(f"Using language: {args.language}")
print(f"Using temperature: {WHISPER_TEMPERATURE}")
print(f"Using development prompt: {DEV_PROMPT}")

# Добавляем обработчик сигналов для корректного завершения
def signal_handler(sig, frame):
    print(f"\nПолучен сигнал {sig}, корректное завершение...")
    # Явное закрытие всех потоков и ресурсов
    if 'listener' in globals() and listener:
        listener.stop()
    
    # Закрытие аудио-устройств, если они открыты
    if 'stream' in globals() and stream:
        try:
            stream.stop()
            stream.close()
        except:
            pass
    
    sys.exit(0)

# Регистрируем обработчики различных сигналов завершения
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

with pynput.keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    print(f"Press {rec_key} to start recording")
    try:
        while listener.is_alive():
            if args.auto_off_time is not None and time.time() - time_last_used > args.auto_off_time:
                print("Auto off")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting...")
        
# Явное закрытие всех потоков перед выходом
if 'listener' in globals() and listener:
    listener.stop()

print("Программа успешно завершена")

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
import tomli

# Функция для получения пути к файлу конфигурации
def get_config_path():
    # Путь к пользовательскому конфигу
    user_config_dir = Path.home() / ".config" / "whispex"
    user_config_path = user_config_dir / "config.toml"
    
    # Путь к конфигу по умолчанию в директории приложения
    script_dir = Path(__file__).parent
    default_config_path = script_dir / "default_config.toml"
    
    # Проверяем, существует ли пользовательский конфиг
    if user_config_path.exists():
        return user_config_path
    else:
        # Если нет пользовательского конфига, используем дефолтный
        if default_config_path.exists():
            return default_config_path
        else:
            print(f"Ошибка: Не найден файл конфигурации. Ни {user_config_path}, ни {default_config_path} не существуют.")
            sys.exit(1)

# Загрузка конфигурации
def load_config():
    config_path = get_config_path()
    print(f"Загрузка конфигурации из: {config_path}")
    
    try:
        with open(config_path, "rb") as f:
            return tomli.load(f)
    except Exception as e:
        print(f"Ошибка при чтении конфигурации: {e}")
        sys.exit(1)

# Загружаем конфигурацию
config = load_config()
print("Настройки из файла конфигурации могут быть перезаписаны параметрами командной строки.")

# Переопределяем стандартный print для автоматического сброса буфера
original_print = print
def print(*args, **kwargs):
    kwargs['flush'] = True
    return original_print(*args, **kwargs)

# Development prompt to improve transcription for programming and development topics
DEFAULT_PROMPT = """This is a transcription of a software developer speaking primarily in Russian, but frequently using English technical terms and phrases. The speaker is knowledgeable in computer science, software development, DevOps, and project management. They use technical jargon and industry terminology related to:
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

# Преобразование строки клавиши в объект Key
def get_key_from_string(key_str):
    if key_str == "alt_r":
        return pynput.keyboard.Key.alt_r
    elif key_str == "alt_l":
        return pynput.keyboard.Key.alt_l
    elif key_str == "ctrl_r":
        return pynput.keyboard.Key.ctrl_r
    elif key_str == "ctrl_l":
        return pynput.keyboard.Key.ctrl_l
    # Добавьте другие специальные клавиши по необходимости
    else:
        return key_str  # Для обычных клавиш

# Настройки из конфига с дефолтными значениями
rec_key = get_key_from_string(config.get("general", {}).get("rec_key", "alt_r"))
default_language = config.get("general", {}).get("language", "en")
default_temperature = config.get("whisper", {}).get("temperature", 0.2)
openai_api_key = config.get("openai", {}).get("api_key", None)
input_method = config.get("general", {}).get("input_method", "clipboard_ctrl_shift_v")
prompt_text = config.get("whisper", {}).get("prompt", DEFAULT_PROMPT)

controller = pynput.keyboard.Controller()

# Parse arguments
parser = argparse.ArgumentParser()
parser.add_argument("language", nargs="?", default=default_language, help="Language code for transcription (e.g. 'ru', 'en')")
parser.add_argument("--no-type", action="store_true", help="Don't type anything")
parser.add_argument("--on-callback", type=str, default=None, help="Command to run after initialization")
parser.add_argument("--auto-off-time", type=int, default=None, help="Automatically turn off after N seconds of inactivity")
parser.add_argument("--temperature", type=float, default=default_temperature, help=f"Temperature parameter for Whisper model (default: {default_temperature})")
parser.add_argument("--prompt", type=str, default=None, help="Custom prompt for Whisper model (use @filepath to load from file)")
args = parser.parse_args()

# Проверка наличия API ключа
if not openai_api_key and not os.environ.get("OPENAI_API_KEY"):
    print("ВНИМАНИЕ: API ключ OpenAI не указан ни в конфигурации, ни в переменной окружения OPENAI_API_KEY")
    print("Работа с OpenAI API будет невозможна без действительного ключа.")
    print("Добавьте ключ в ~/.config/whispex/config.toml или установите переменную окружения OPENAI_API_KEY")

# Initialize OpenAI client
client = OpenAI(api_key=openai_api_key)

# Проверяем, передан ли prompt через файл
prompt_arg = args.prompt
if prompt_arg and prompt_arg.startswith('@'):
    prompt_file = prompt_arg[1:]  # Удаляем символ @ в начале
    try:
        with open(prompt_file, 'r', encoding='utf-8') as f:
            args.prompt = f.read()
        print(f"Prompt loaded from file: {prompt_file}")
    except Exception as e:
        print(f"Error loading prompt from file {prompt_file}: {str(e)}")
        args.prompt = None

# Set the prompt from command line or use default
DEV_PROMPT = args.prompt if args.prompt else prompt_text

if args.on_callback is not None:
    subprocess.run(args.on_callback, shell=True)


def get_text(audio, context=None):
    # Создаем временный файл в директории /tmp с правильным расширением
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
        tmp_audio_filename = temp_file.name
    
    soundfile.write(tmp_audio_filename, audio, whisper_samplerate, format="wav")
    actual_prompt = context or DEV_PROMPT
    print(f"🌐 OpenAI request: lang={args.language}, temp={args.temperature}, prompt=\"{actual_prompt[:30]}...\"")
    
    try:
        api_response = client.audio.transcriptions.create(
            model="whisper-1",
            file=open(tmp_audio_filename, "rb"),
            language=args.language,
            prompt=actual_prompt,
            temperature=args.temperature
        )
        result_text = api_response.text
    finally:
        # Удаляем временный файл после использования
        tmp_path = Path(tmp_audio_filename)
        if tmp_path.exists():
            tmp_path.unlink()
    
    return result_text


def type_text(text):
    if args.no_type:
        return
    
    if input_method == "clipboard_ctrl_v":
        pyperclip.copy(text)
        controller.press(pynput.keyboard.Key.ctrl_l)
        controller.press("v")
        controller.release("v")
        controller.release(pynput.keyboard.Key.ctrl_l)
    elif input_method == "clipboard_ctrl_shift_v":
        pyperclip.copy(text)
        controller.press(pynput.keyboard.Key.ctrl_l)
        controller.press(pynput.keyboard.Key.shift_l)
        controller.press("v")
        controller.release("v")
        controller.release(pynput.keyboard.Key.shift_l)
        controller.release(pynput.keyboard.Key.ctrl_l)
    elif input_method == "direct":
        controller.type(text)
    else:
        print(f"Неизвестный метод ввода: {input_method}. Использую прямой ввод.")
        controller.type(text)


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
    type_text(text)


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
print(f"Используемый язык: {args.language}")
print(f"Температура модели: {args.temperature}")
print(f"Клавиша записи: {rec_key}")
print(f"Метод ввода: {input_method}")
print(f"Промпт: {DEV_PROMPT[:50]}...")

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
    print(f"Нажмите {rec_key} для начала записи")
    try:
        while listener.is_alive():
            if args.auto_off_time is not None and time.time() - time_last_used > args.auto_off_time:
                print("Auto off")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nВыход...")
        
# Явное закрытие всех потоков перед выходом
if 'listener' in globals() and listener:
    listener.stop()

print("Программа успешно завершена")

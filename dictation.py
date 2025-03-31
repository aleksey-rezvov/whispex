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

# Function to get the configuration file path
def get_config_path():
    # Path to user config
    user_config_dir = Path.home() / ".config" / "whispex"
    user_config_path = user_config_dir / "config.toml"
    
    # Path to default config in application directory
    script_dir = Path(__file__).parent
    default_config_path = script_dir / "default_config.toml"
    
    # Check if user config exists
    if user_config_path.exists():
        return user_config_path
    else:
        # If no user config, use default
        if default_config_path.exists():
            return default_config_path
        else:
            print(f"Error: Configuration file not found. Neither {user_config_path} nor {default_config_path} exist.")
            sys.exit(1)

# Loading configuration
def load_config():
    config_path = get_config_path()
    print(f"Loading configuration from: {config_path}")
    
    try:
        with open(config_path, "rb") as f:
            return tomli.load(f)
    except Exception as e:
        print(f"Error reading configuration: {e}")
        sys.exit(1)

# Load configuration
config = load_config()
print("Settings from the configuration file can be overridden by command line parameters.")

# Override standard print for automatic buffer flushing
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

# Convert key string to Key object
def get_key_from_string(key_str):
    if key_str == "alt_r":
        return pynput.keyboard.Key.alt_r
    elif key_str == "alt_l":
        return pynput.keyboard.Key.alt_l
    elif key_str == "ctrl_r":
        return pynput.keyboard.Key.ctrl_r
    elif key_str == "ctrl_l":
        return pynput.keyboard.Key.ctrl_l
    # Add other special keys as needed
    else:
        return key_str  # For regular keys

# Settings from config with default values
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

# Check for API key
if not openai_api_key and not os.environ.get("OPENAI_API_KEY"):
    print("WARNING: OpenAI API key is not specified either in the configuration or in the OPENAI_API_KEY environment variable")
    print("Working with OpenAI API will not be possible without a valid key.")
    print("Add the key to ~/.config/whispex/config.toml or set the OPENAI_API_KEY environment variable")

# Initialize OpenAI client
client = OpenAI(api_key=openai_api_key)

# Check if prompt is provided via file
prompt_arg = args.prompt
if prompt_arg and prompt_arg.startswith('@'):
    prompt_file = prompt_arg[1:]  # Remove @ at the beginning
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
    # Create a temporary file in /tmp directory with the correct extension
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
        # Remove the temporary file after use
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
        print(f"Unknown input method: {input_method}. Using direct input.")
        controller.type(text)


rec_key_pressed = False
time_last_used = time.time()

# Global audio stream variable
stream = None

def record_and_process():
    # Recording and processing audio
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

    # Check recording duration
    duration = len(recorded_audio) / recording_samplerate
    if duration <= 0.1:
        print("Recording too short, skipping")
        return

    # Downsampling
    recorded_audio = recorded_audio[::3]

    context = None  # Use dev-prompt by default

    # Transcription
    text = get_text(recorded_audio, context)
    print(text)

    # Input text
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


# Display settings information
print(f"Language: {args.language}")
print(f"Model temperature: {args.temperature}")
print(f"Recording key: {rec_key}")
print(f"Input method: {input_method}")
print(f"Prompt: {DEV_PROMPT[:50]}...")

# Add signal handler for proper termination
def signal_handler(sig, frame):
    print(f"\nReceived signal {sig}, proper termination...")
    # Explicitly close all threads and resources
    if 'listener' in globals() and listener:
        listener.stop()
    
    # Close audio devices if they are open
    if 'stream' in globals() and stream:
        try:
            stream.stop()
            stream.close()
        except:
            pass
    
    sys.exit(0)

# Register handlers for various termination signals
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
        
# Explicitly close all threads before exit
if 'listener' in globals() and listener:
    listener.stop()

print("Program successfully terminated")

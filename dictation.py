import argparse
import subprocess
import threading
import time
import signal
import sys
import tempfile
import os
import psutil
import logging
from pathlib import Path
import importlib

import numpy as np
import pynput
import pyperclip
import sounddevice as sd
import soundfile
from openai import OpenAI
import tomli

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def get_config_path():
    """
    Find and return the path to configuration file.
    Tries user config first, then falls back to default config.
    
    Returns:
        Path: Path to the configuration file
    """
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
            logger.error(f"Configuration file not found. Neither {user_config_path} nor {default_config_path} exist.")
            sys.exit(1)

def load_config():
    """
    Load configuration from TOML file and process settings.
    Handles environment variables for sensitive data like API keys.
    
    Returns:
        dict: Configuration settings
    """
    config_path = get_config_path()
    logger.info(f"Loading configuration from: {config_path}")
    
    try:
        with open(config_path, "rb") as f:
            config = tomli.load(f)
            
        # Handle OpenAI API key from environment if not in config
        if not config.get("openai", {}).get("api_key"):
            env_api_key = os.environ.get("OPENAI_API_KEY")
            if env_api_key:
                if "openai" not in config:
                    config["openai"] = {}
                config["openai"]["api_key"] = env_api_key
                logger.info("Using OpenAI API key from environment variables")
            else:
                logger.warning("OpenAI API key is not specified either in the configuration or in OPENAI_API_KEY environment variable")
                logger.warning("Working with OpenAI API will not be possible without a valid key")
                logger.warning("Add the key to ~/.config/whispex/config.toml or set the OPENAI_API_KEY environment variable")
        
        return config
    except Exception as e:
        logger.error(f"Error reading configuration: {e}")
        sys.exit(1)

# Override standard print for automatic buffer flushing
original_print = print
def print(*args, **kwargs):
    kwargs['flush'] = True
    return original_print(*args, **kwargs)

# Load configuration
config = load_config()
logger.info("Application started with settings from configuration file")

# Extract settings with defaults from config
whisper_settings = config.get("whisper", {})
general_settings = config.get("general", {})

# Get audio settings 
whisper_samplerate = whisper_settings.get("sample_rate", 16000)  # sampling rate that whisper uses
recording_samplerate = whisper_settings.get("recording_sample_rate", 48000)  # multiple of whisper_samplerate, widely supported

def evaluate_key_string(key_str):
    """
    Evaluate a Python expression to get a keyboard key.
    Allows configuration file to specify keys like 'pynput.keyboard.Key.alt_r'
    
    Args:
        key_str (str): Python expression representing a key
        
    Returns:
        object: Key object or string
    """
    try:
        # Try to evaluate the string as Python code
        # First import necessary modules
        keyboard_module = importlib.import_module('pynput.keyboard')
        
        # Create a safe namespace with only allowed modules
        namespace = {
            'pynput': pynput,
            'keyboard': keyboard_module
        }
        
        # If the string doesn't contain any Python expressions, return as is
        if not any(marker in key_str for marker in [".", "(", ")", "pynput"]):
            return key_str
            
        # Evaluate the expression
        return eval(key_str, namespace)
    except Exception as e:
        logger.warning(f"Could not evaluate key string '{key_str}': {e}")
        return key_str  # Return original string if evaluation fails

# Get settings from config
rec_key = evaluate_key_string(general_settings.get("rec_key", "pynput.keyboard.Key.alt_r"))
language = general_settings.get("language", "en")
temperature = whisper_settings.get("temperature", 0.2)
openai_api_key = config.get("openai", {}).get("api_key", None)
input_method = general_settings.get("input_method", "clipboard_ctrl_shift_v")
prompt_text = whisper_settings.get("prompt", "")

# Initialize the keyboard controller
controller = pynput.keyboard.Controller()

# Initialize OpenAI client
client = OpenAI(api_key=openai_api_key)

def get_text(audio, context=None):
    """
    Send audio to OpenAI Whisper API for transcription.
    
    Args:
        audio (numpy.ndarray): Audio data to transcribe
        context (str, optional): Prompt context for the model
        
    Returns:
        str: Transcribed text
    """
    # Create a temporary file in /tmp directory with the correct extension
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
        tmp_audio_filename = temp_file.name
    
    soundfile.write(tmp_audio_filename, audio, whisper_samplerate, format="wav")
    actual_prompt = context or prompt_text
    logger.info(f"OpenAI request: lang={language}, temp={temperature}, prompt_length={len(actual_prompt)}")
    
    try:
        api_response = client.audio.transcriptions.create(
            model="whisper-1",
            file=open(tmp_audio_filename, "rb"),
            language=language,
            prompt=actual_prompt,
            temperature=temperature
        )
        result_text = api_response.text
    finally:
        # Remove the temporary file after use
        tmp_path = Path(tmp_audio_filename)
        if tmp_path.exists():
            tmp_path.unlink()
    
    return result_text


def type_text(text):
    """
    Type text using the configured input method.
    
    Args:
        text (str): Text to type
    """
    # Skip if typing is disabled in config
    if general_settings.get("no_type", False):
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
        logger.warning(f"Unknown input method: {input_method}. Using direct input.")
        controller.type(text)


rec_key_pressed = False
time_last_used = time.time()

# Global audio stream variable
stream = None

def record_and_process():
    """
    Record audio while the key is pressed and process it for transcription.
    """
    # Recording and processing audio
    global stream
    audio_chunks = []

    def audio_callback(indata, frames, time, status):
        if status:
            logger.warning(f"Audio status: {status}")
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
        logger.info("Recording too short, skipping")
        return

    # Downsampling
    recorded_audio = recorded_audio[::int(recording_samplerate/whisper_samplerate)]

    # Transcription
    text = get_text(recorded_audio)
    logger.info(f"Transcribed: {text}")

    # Input text
    text = text + " "
    type_text(text)


def on_press(key):
    """
    Handle key press events, start recording when activation key is pressed.
    
    Args:
        key: The key that was pressed
    """
    global rec_key_pressed
    if key == rec_key:
        rec_key_pressed = True

        # start recording in a new thread
        t = threading.Thread(target=record_and_process)
        t.start()


def on_release(key):
    """
    Handle key release events, stop recording when activation key is released.
    
    Args:
        key: The key that was released
    """
    global rec_key_pressed, time_last_used
    if key == rec_key:
        rec_key_pressed = False
        time_last_used = time.time()


# Display settings information
logger.info(f"Language: {language}")
logger.info(f"Model temperature: {temperature}")
logger.info(f"Recording key: {rec_key}")
logger.info(f"Input method: {input_method}")
logger.info(f"Prompt length: {len(prompt_text) if prompt_text else 0}")

# Function to kill a process and all its children
def terminate_process_tree(pid, timeout=3):
    """
    Terminates a process and all its children processes.
    
    Args:
        pid (int): Process ID to terminate
        timeout (int, optional): Seconds to wait for graceful termination before force kill
    """
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        
        # Send SIGTERM to parent
        logger.info(f"Sending SIGTERM to process {pid}...")
        parent.terminate()
        
        # Wait for parent to terminate
        gone, alive = psutil.wait_procs([parent], timeout=timeout)
        if parent in alive:
            # If still alive, force kill
            logger.warning(f"Process {pid} did not terminate gracefully, force killing...")
            parent.kill()
        else:
            logger.info(f"Process {pid} terminated gracefully")
        
        # Terminate any remaining children
        if children:
            logger.info(f"Terminating {len(children)} child processes...")
            for child in children:
                try:
                    if child.is_running():
                        child.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            
            # Wait for children to terminate and kill if necessary
            gone, alive = psutil.wait_procs(children, timeout=timeout)
            for child in alive:
                try:
                    logger.warning(f"Force killing child process {child.pid}...")
                    child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
    except psutil.NoSuchProcess:
        logger.info(f"Process {pid} no longer exists")
    except Exception as e:
        logger.error(f"Error terminating process tree: {e}")

# Enhanced signal handler with proper cleanup of all resources and child processes
def signal_handler(sig, frame):
    """
    Handle termination signals with proper cleanup.
    
    Args:
        sig: Signal number
        frame: Current stack frame
    """
    logger.info(f"Received signal {sig}, proper termination...")
    
    # First stop any recording in progress
    global rec_key_pressed
    rec_key_pressed = False
    
    # Close keyboard listener
    if 'listener' in globals() and listener:
        try:
            listener.stop()
            logger.info("Keyboard listener stopped")
        except Exception as e:
            logger.error(f"Error stopping keyboard listener: {e}")
    
    # Close audio devices if they are open
    if 'stream' in globals() and stream:
        try:
            stream.stop()
            stream.close()
            logger.info("Audio stream closed")
        except Exception as e:
            logger.error(f"Error closing audio stream: {e}")
    
    # Terminate any child processes related to this application
    current_pid = os.getpid()
    logger.info(f"Cleaning up processes (PID: {current_pid})...")
    
    # Find and terminate processes related to this application
    try:
        # Try to terminate our own process tree
        terminate_process_tree(current_pid)
        
        # Look for other instances of dictation.py that might be orphaned
        output = subprocess.run(
            ["pgrep", "-f", "dictation.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        if output.returncode == 0:
            for pid_str in output.stdout.strip().split():
                pid = int(pid_str)
                if pid != current_pid:  # Don't terminate ourselves
                    logger.info(f"Found other dictation.py process: {pid}, terminating...")
                    terminate_process_tree(pid)
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
    
    logger.info("Cleanup completed, exiting...")
    sys.exit(0)

# Register handlers for various termination signals
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGHUP, signal_handler)

# Get auto-off time from config
auto_off_time = general_settings.get("auto_off_time", None)

with pynput.keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    logger.info(f"Press {rec_key} to start recording")
    try:
        while listener.is_alive():
            if auto_off_time and auto_off_time > 0 and time.time() - time_last_used > auto_off_time:
                logger.info("Auto off timeout reached")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        
# Explicitly close all threads before exit
if 'listener' in globals() and listener:
    listener.stop()

logger.info("Program successfully terminated")

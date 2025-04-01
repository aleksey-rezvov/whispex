import threading
import time
import signal
import sys
import tempfile
import os
import psutil
import logging
import shutil
from pathlib import Path
import importlib
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pynput
import pyperclip
import sounddevice as sd
import soundfile
from openai import OpenAI

# Import settings manager
from settings import SettingsManager

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Initialize settings manager
settings_manager = SettingsManager()

# Application state container with strict typing
@dataclass
class AppState:
    rec_key_pressed: bool = False
    time_last_used: float = field(default_factory=time.time)
    stream: Optional[sd.InputStream] = None  # Audio stream
    controller: Optional[pynput.keyboard.Controller] = None  # Keyboard controller
    signal_handler_running: bool = False  # Flag to prevent multiple signal handler executions
    rec_key_obj: Optional[object] = None  # Keyboard key object for recording

# Create a singleton instance
app_state = AppState()

def main():
    """Main function that runs the application."""
    # Initialize settings from config
    initialize_settings()
    
    # Register handlers for various termination signals
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGHUP, signal_handler)
    
    # Get auto-off time from config
    auto_off_time = settings_manager.get("general", "auto_off_time")
    
    # Start keyboard listener
    with pynput.keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        logger.info(f"Press {app_state.rec_key_obj} to start recording")
        try:
            while listener.is_alive():
                if auto_off_time and auto_off_time > 0 and time.time() - app_state.time_last_used > auto_off_time:
                    logger.info("Auto off timeout reached")
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
            
    # Explicitly close all threads before exit
    if 'listener' in locals() and listener:
        listener.stop()
    
    logger.info("Program successfully terminated")

def initialize_settings():
    """
    Initialize all application settings from configuration.
    Updates the module level settings dictionary.
    """
    # Settings are already loaded in the settings_manager
    logger.info("Application started with settings from configuration file")
    
    # Initialize keyboard controller
    initialize_keyboard()
    
    # Display settings information
    log_settings()

def initialize_keyboard():
    """Initialize keyboard controller and keyboard shortcut key."""
    app_state.controller = pynput.keyboard.Controller()
    
    # Get recording key
    key_str = settings_manager.get("general", "rec_key")
    app_state.rec_key_obj = evaluate_key_string(key_str)

def log_settings():
    """Log information about current settings."""
    logger.info(f"Language: {settings_manager.get('general', 'language')}")
    logger.info(f"Model temperature: {settings_manager.get('whisper', 'temperature')}")
    logger.info(f"Recording key: {app_state.rec_key_obj}")
    logger.info(f"Input method: {settings_manager.get('general', 'input_method')}")
    logger.info(f"Prompt length: {len(settings_manager.get('whisper', 'prompt', ''))}")

def record_and_process():
    """
    Record audio while the key is pressed and process it for transcription.
    """
    # Recording and processing audio
    audio_chunks = []

    def audio_callback(indata, frames, time, status):
        if status:
            logger.warning(f"Audio status: {status}")
        audio_chunks.append(indata.copy())

    recording_samplerate = settings_manager.get("whisper", "recording_sample_rate")
    whisper_samplerate = settings_manager.get("whisper", "sample_rate")
    
    app_state.stream = sd.InputStream(
        samplerate=recording_samplerate,
        channels=1,
        blocksize=256,
        callback=audio_callback,
    )
    app_state.stream.start()
    while app_state.rec_key_pressed:
        time.sleep(0.005)
    app_state.stream.stop()
    app_state.stream.close()
    app_state.stream = None
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
    
    whisper_samplerate = settings_manager.get("whisper", "sample_rate")
    soundfile.write(tmp_audio_filename, audio, whisper_samplerate, format="wav")
    
    language = settings_manager.get("general", "language")
    temperature = settings_manager.get("whisper", "temperature")
    prompt_text = settings_manager.get("whisper", "prompt")
    actual_prompt = context or prompt_text
    
    logger.info(f"OpenAI request: lang={language}, temp={temperature}, prompt_length={len(actual_prompt)}")
    
    try:
        # Create OpenAI client with API key
        client = OpenAI(api_key=settings_manager.get("openai", "api_key"))
        
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
    if settings_manager.get("general", "no_type", False):
        return
    
    input_method = settings_manager.get("general", "input_method")
    
    if input_method == "clipboard_ctrl_v":
        pyperclip.copy(text)
        app_state.controller.press(pynput.keyboard.Key.ctrl_l)
        app_state.controller.press("v")
        app_state.controller.release("v")
        app_state.controller.release(pynput.keyboard.Key.ctrl_l)
    elif input_method == "clipboard_ctrl_shift_v":
        pyperclip.copy(text)
        app_state.controller.press(pynput.keyboard.Key.ctrl_l)
        app_state.controller.press(pynput.keyboard.Key.shift_l)
        app_state.controller.press("v")
        app_state.controller.release("v")
        app_state.controller.release(pynput.keyboard.Key.shift_l)
        app_state.controller.release(pynput.keyboard.Key.ctrl_l)
    elif input_method == "direct":
        app_state.controller.type(text)
    else:
        logger.warning(f"Unknown input method: {input_method}. Using direct input.")
        app_state.controller.type(text)

def on_press(key):
    """
    Handle key press events, start recording when activation key is pressed.
    
    Args:
        key: The key that was pressed
    """
    if key == app_state.rec_key_obj:
        app_state.rec_key_pressed = True

        # start recording in a new thread
        t = threading.Thread(target=record_and_process)
        t.start()

def on_release(key):
    """
    Handle key release events, stop recording when activation key is released.
    
    Args:
        key: The key that was released
    """
    if key == app_state.rec_key_obj:
        app_state.rec_key_pressed = False
        app_state.time_last_used = time.time()

def terminate_process_tree(pid, timeout=3):
    """
    Terminates a process and all its children processes.
    
    Args:
        pid (int): Process ID to terminate
        timeout (int, optional): Seconds to wait for graceful termination before force kill
    """
    try:
        # Get only direct children of current process
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        
        # Log children to terminate
        if children:
            logger.info(f"Terminating {len(children)} child processes...")
            
            # Send SIGTERM to all children first
            for child in children:
                try:
                    if child.is_running():
                        logger.info(f"Sending SIGTERM to child process {child.pid}...")
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

def signal_handler(sig, frame):
    """
    Handle termination signals with proper cleanup.
    
    Args:
        sig: Signal number
        frame: Current stack frame
    """
    # Prevent multiple executions of signal handler
    if app_state.signal_handler_running:
        return
    
    app_state.signal_handler_running = True
    logger.info(f"Received signal {sig}, proper termination...")
    
    # First stop any recording in progress
    app_state.rec_key_pressed = False
    
    # Close keyboard listener if it exists
    if 'listener' in globals():
        try:
            listener.stop()
            logger.info("Keyboard listener stopped")
        except Exception as e:
            logger.error(f"Error stopping keyboard listener: {e}")
    
    # Close audio devices if they are open
    if app_state.stream:
        try:
            app_state.stream.stop()
            app_state.stream.close()
            logger.info("Audio stream closed")
        except Exception as e:
            logger.error(f"Error closing audio stream: {e}")
    
    # Terminate only our direct child processes, don't look for other instances
    try:
        current_pid = os.getpid()
        logger.info(f"Cleaning up direct child processes of {current_pid}...")
        terminate_process_tree(current_pid)
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
    
    logger.info("Cleanup completed, exiting...")
    sys.exit(0)

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

# Only run the main function if this script is executed directly
if __name__ == "__main__":
    main()

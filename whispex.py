import threading
import time
import signal
import sys
import tempfile
import os
import psutil
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

# Import settings manager with typed settings
from settings import SettingsManager, SettingsSection, GeneralSettings, WhisperSettings, OpenAISettings
# Import logger
from logger import log

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
    auto_off_time = settings_manager.get(SettingsSection.GENERAL, GeneralSettings.AUTO_OFF_TIME)
    
    # Start keyboard listener
    with pynput.keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        log.info(f"Press {app_state.rec_key_obj} to start recording")
        try:
            while listener.is_alive():
                if auto_off_time and auto_off_time > 0 and time.time() - app_state.time_last_used > auto_off_time:
                    log.info("Auto off timeout reached")
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Keyboard interrupt received")
            
    # Explicitly close all threads before exit
    if 'listener' in locals() and listener:
        listener.stop()
    
    log.info("Program successfully terminated")

def initialize_settings():
    """
    Initialize all application settings from configuration.
    Updates the module level settings dictionary.
    """
    # Settings are already loaded in the settings_manager
    log.info("Application started with settings from configuration file")
    
    # Initialize keyboard controller
    initialize_keyboard()
    
    # Display settings information
    log_settings()

def initialize_keyboard():
    """Initialize keyboard controller and keyboard shortcut key."""
    app_state.controller = pynput.keyboard.Controller()
    
    # Get recording key
    key_str = settings_manager.get(SettingsSection.GENERAL, GeneralSettings.REC_KEY)
    app_state.rec_key_obj = evaluate_key_string(key_str)

def log_settings():
    """Log information about current settings."""
    log.info(f"Language: {settings_manager.get(SettingsSection.GENERAL, GeneralSettings.LANGUAGE)}")
    log.info(f"Model temperature: {settings_manager.get(SettingsSection.WHISPER, WhisperSettings.TEMPERATURE)}")
    log.info(f"Recording key: {app_state.rec_key_obj}")
    log.info(f"Input method: {settings_manager.get(SettingsSection.GENERAL, GeneralSettings.INPUT_METHOD)}")
    log.info(f"Prompt length: {len(settings_manager.get(SettingsSection.WHISPER, WhisperSettings.PROMPT, ''))}")

def record_and_process():
    """
    Record audio while the key is pressed and process it for transcription.
    """
    # Recording and processing audio
    audio_chunks = []

    def audio_callback(indata, frames, time, status):
        if status:
            log.warning(f"Audio status: {status}")
        audio_chunks.append(indata.copy())

    recording_samplerate = settings_manager.get(SettingsSection.WHISPER, WhisperSettings.RECORDING_SAMPLE_RATE)
    whisper_samplerate = settings_manager.get(SettingsSection.WHISPER, WhisperSettings.SAMPLE_RATE)
    
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
        log.info("Recording too short, skipping")
        return

    # Downsampling
    recorded_audio = recorded_audio[::int(recording_samplerate/whisper_samplerate)]

    # Transcription
    text = get_text(recorded_audio)
    log.info(f"Transcribed: {text}")

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
    
    whisper_samplerate = settings_manager.get(SettingsSection.WHISPER, WhisperSettings.SAMPLE_RATE)
    soundfile.write(tmp_audio_filename, audio, whisper_samplerate, format="wav")
    
    language = settings_manager.get(SettingsSection.GENERAL, GeneralSettings.LANGUAGE)
    temperature = settings_manager.get(SettingsSection.WHISPER, WhisperSettings.TEMPERATURE)
    prompt_text = settings_manager.get(SettingsSection.WHISPER, WhisperSettings.PROMPT)
    actual_prompt = context or prompt_text
    
    log.info(f"OpenAI request: lang={language}, temp={temperature}, prompt_length={len(actual_prompt)}")
    
    try:
        # Create OpenAI client with API key
        client = OpenAI(api_key=settings_manager.get(SettingsSection.OPENAI, OpenAISettings.API_KEY))
        
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
    if settings_manager.get(SettingsSection.GENERAL, GeneralSettings.NO_TYPE, False):
        return
    
    input_method = settings_manager.get(SettingsSection.GENERAL, GeneralSettings.INPUT_METHOD)
    
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
        log.warning(f"Unknown input method: {input_method}. Using direct input.")
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

def signal_handler(signum, frame):
    """
    Handle termination signals.
    
    Args:
        signum: Signal number
        frame: Current stack frame
    """
    # Prevent multiple signal handlers from running
    if app_state.signal_handler_running:
        return
    app_state.signal_handler_running = True
    
    log.info(f"Received signal {signum}, shutting down...")
    
    # Clean up resources
    if app_state.stream:
        log.info("Closing audio stream...")
        app_state.stream.stop()
        app_state.stream.close()
    
    # Terminate all child processes to prevent orphans
    terminate_process_tree(os.getpid())
    
    # Exit gracefully
    sys.exit(0)

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
            log.info(f"Terminating {len(children)} child processes...")
            
            # Send SIGTERM to all children first
            for child in children:
                try:
                    if child.is_running():
                        log.info(f"Sending SIGTERM to child process {child.pid}...")
                        child.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            
            # Wait for children to terminate and kill if necessary
            gone, alive = psutil.wait_procs(children, timeout=timeout)
            for child in alive:
                try:
                    log.warning(f"Force killing child process {child.pid}...")
                    child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        
    except psutil.NoSuchProcess:
        log.info(f"Process {pid} no longer exists")
    except Exception as e:
        log.error(f"Error terminating process tree: {e}")

def evaluate_key_string(key_string):
    """
    Evaluate a key string to get the appropriate key object.
    Can handle special keys like Key.ctrl, Key.f1, etc.
    
    Args:
        key_string (str): String representation of the key
        
    Returns:
        Key object or string
    """
    # Handle special keys like Key.ctrl, Key.f1, etc.
    if key_string.startswith("Key."):
        key_attr = key_string.split(".", 1)[1]
        if hasattr(pynput.keyboard.Key, key_attr):
            return getattr(pynput.keyboard.Key, key_attr)
    
    # For regular keys, just return the character
    return key_string

if __name__ == "__main__":
    main()

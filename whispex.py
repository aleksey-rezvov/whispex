import datetime
import json
import os
import signal
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import psutil
import pynput
import pyperclip
import sounddevice as sd
import soundfile
from openai import OpenAI

# Import logger
from logger import log
# Import settings manager with typed settings
from settings import (DataPathSettings, GeneralSettings, OpenAISettings,
                      SettingsManager, SettingsSection, WhisperSettings)

# Initialize settings manager
settings_manager = SettingsManager()


# Application state container with strict typing
@dataclass
class AppState:
    rec_key_pressed: bool = False
    time_last_used: float = field(default_factory=time.time)
    stream: Optional[sd.InputStream] = None  # Audio stream
    controller: Optional[pynput.keyboard.Controller] = None  # Keyboard controller
    signal_handler_running: bool = (
        False  # Flag to prevent multiple signal handler executions
    )
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
    auto_off_time = settings_manager.get(
        SettingsSection.GENERAL, GeneralSettings.AUTO_OFF_TIME
    )

    log.debug("Starting keyboard listener - waiting for key presses...")

    # Start keyboard listener
    with pynput.keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        log.info(f"Press {app_state.rec_key_obj} to start recording")
        try:
            while listener.is_alive():
                if (
                    auto_off_time
                    and auto_off_time > 0
                    and time.time() - app_state.time_last_used > auto_off_time
                ):
                    log.info("Auto off timeout reached")
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Keyboard interrupt received")

    # Explicitly close all threads before exit
    if "listener" in locals() and listener:
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
    log.debug(f"Raw rec_key value from settings: '{key_str}'")
    app_state.rec_key_obj = evaluate_key_string(key_str)
    log.debug(f"Evaluated rec_key object: {app_state.rec_key_obj}, type: {type(app_state.rec_key_obj)}")


def log_settings():
    """Log information about current settings."""
    log.info(
        f"Language: {settings_manager.get(SettingsSection.GENERAL, GeneralSettings.LANGUAGE)}"
    )
    log.info(
        f"Model temperature: {settings_manager.get(SettingsSection.WHISPER, WhisperSettings.TEMPERATURE)}"
    )
    log.info(f"Recording key: {app_state.rec_key_obj}")
    log.info(
        f"Input method: {settings_manager.get(SettingsSection.GENERAL, GeneralSettings.INPUT_METHOD)}"
    )
    log.info(
        f"Prompt length: {len(settings_manager.get(SettingsSection.WHISPER, WhisperSettings.PROMPT, ''))}"
    )


def record_and_process():
    """
    Record audio while the key is pressed and process it for transcription.
    """
    log.debug("Starting audio recording...")
    # Recording and processing audio
    audio_chunks = []

    def audio_callback(indata, frames, time, status):
        if status:
            log.warning(f"Audio status: {status}")
        audio_chunks.append(indata.copy())

    try:
        recording_samplerate = settings_manager.get(
            SettingsSection.WHISPER, WhisperSettings.RECORDING_SAMPLE_RATE
        )
        whisper_samplerate = settings_manager.get(
            SettingsSection.WHISPER, WhisperSettings.SAMPLE_RATE
        )

        # Check for input device configuration
        use_default_device = settings_manager.get(
            SettingsSection.GENERAL, GeneralSettings.DEFAULT_DEVICE, True
        )

        device = None
        if not use_default_device:
            device_name = settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.INPUT_DEVICE, ""
            )
            if device_name:
                log.debug(f"Using custom input device: {device_name}")
                device = device_name

        if device:
            log.debug(f"Opening audio stream with device: {device}")
            app_state.stream = sd.InputStream(
                samplerate=recording_samplerate,
                device=device,
                channels=1,
                blocksize=256,
                callback=audio_callback,
            )
        else:
            log.debug("Opening audio stream with default device")
            app_state.stream = sd.InputStream(
                samplerate=recording_samplerate,
                channels=1,
                blocksize=256,
                callback=audio_callback,
            )

        app_state.stream.start()
        log.debug("Audio stream started successfully")

        while app_state.rec_key_pressed:
            time.sleep(0.005)

        app_state.stream.stop()
        app_state.stream.close()
        app_state.stream = None
        log.debug("Audio recording stopped")

        if not audio_chunks:
            log.warning("No audio data recorded!")
            return

        log.debug(f"Processing {len(audio_chunks)} audio chunks")
        recorded_audio = np.concatenate(audio_chunks)[:, 0]

        # Check recording duration
        duration = len(recorded_audio) / recording_samplerate
        log.debug(f"Recorded audio duration: {duration:.2f} seconds")
        if duration <= 0.1:
            log.info("Recording too short, skipping")
            return

        # Downsampling
        recorded_audio = recorded_audio[:: int(recording_samplerate / whisper_samplerate)]

        # Transcription
        log.debug("Starting audio transcription...")
        text, transcription_data = get_text(recorded_audio)
        log.debug("Transcription completed")
        log.info(f"Transcribed: {text}")

        # Input text
        text = text + " "
        type_text(text)
    except Exception as e:
        log.error(f"Error during recording and processing: {str(e)}")


def get_text(audio, context=None):
    """
    Send audio to OpenAI Whisper API for transcription and save the results.

    Args:
        audio (numpy.ndarray): Audio data to transcribe
        context (str, optional): Prompt context for the model

    Returns:
        tuple: (transcribed_text, transcription_data_dict)
    """
    # Create timestamp for unique filenames
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Get storage directories from settings
    base_dir, audio_dir = settings_manager.get_data_dirs()

    # Generate filenames
    audio_filename = f"audio_{timestamp}.wav"
    audio_path = audio_dir / audio_filename
    json_filename = f"transcription_{timestamp}.json"
    json_path = base_dir / json_filename

    # Get transcription parameters
    whisper_samplerate = settings_manager.get(
        SettingsSection.WHISPER, WhisperSettings.SAMPLE_RATE
    )
    language = settings_manager.get(SettingsSection.GENERAL, GeneralSettings.LANGUAGE)
    temperature = settings_manager.get(
        SettingsSection.WHISPER, WhisperSettings.TEMPERATURE
    )
    prompt_text = settings_manager.get(SettingsSection.WHISPER, WhisperSettings.PROMPT)
    actual_prompt = context or prompt_text

    # Save audio file permanently
    log.debug(f"Saving audio file: {audio_path}")
    soundfile.write(str(audio_path), audio, whisper_samplerate, format="wav")
    log.debug("Audio file saved")

    # Create a temporary file for API processing
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
        tmp_audio_filename = temp_file.name

    log.debug(f"Saving temporary audio file for API: {tmp_audio_filename}")
    soundfile.write(tmp_audio_filename, audio, whisper_samplerate, format="wav")

    log.info(
        f"OpenAI request: lang={language}, temp={temperature}, prompt_length={len(actual_prompt)}"
    )

    transcription_data = {
        "timestamp": timestamp,
        "audio_file": str(Path("audio") / audio_filename),  # Relative path to audio file
        "whisper_parameters": {
            "language": language,
            "temperature": temperature,
            "prompt": actual_prompt,
            "model": "whisper-1",
            "sample_rate": whisper_samplerate
        },
        "text": ""
    }

    try:
        # Create OpenAI client with API key
        client = OpenAI(
            api_key=settings_manager.get(SettingsSection.OPENAI, OpenAISettings.API_KEY)
        )

        log.debug("Sending request to OpenAI Whisper API...")
        api_response = client.audio.transcriptions.create(
            model="whisper-1",
            file=open(tmp_audio_filename, "rb"),
            language=language,
            prompt=actual_prompt,
            temperature=temperature,
        )
        log.debug("Response received from OpenAI Whisper API")
        result_text = api_response.text

        # Store the result text
        transcription_data["text"] = result_text

        # Store API response details if available
        if hasattr(api_response, "model_dump"):
            response_dict = api_response.model_dump()
            transcription_data["api_response"] = response_dict

        # Save transcription data to JSON file
        with open(json_path, 'w', encoding='utf-8') as json_file:
            json.dump(transcription_data, json_file, ensure_ascii=False, indent=2)

        log.info(f"Transcription data saved to: {json_path}")

    finally:
        # Remove the temporary file after use
        tmp_path = Path(tmp_audio_filename)
        if tmp_path.exists():
            log.debug(f"Removing temporary audio file: {tmp_audio_filename}")
            tmp_path.unlink()

    return result_text, transcription_data


def type_text(text):
    """
    Type text using the configured input method.

    Args:
        text (str): Text to type
    """
    # Skip if typing is disabled in config
    if settings_manager.get(SettingsSection.GENERAL, GeneralSettings.NO_TYPE, False):
        return

    # Check if controller is initialized
    if app_state.controller is None:
        log.error("Keyboard controller not initialized - can't type text")
        return

    input_method = settings_manager.get(
        SettingsSection.GENERAL, GeneralSettings.INPUT_METHOD
    )

    log.debug(f"Using input method: {input_method}")

    if input_method == "clipboard_ctrl_v":
        pyperclip.copy(text)
        log.debug("Text copied to clipboard, sending Ctrl+V")
        app_state.controller.press(pynput.keyboard.Key.ctrl_l)
        app_state.controller.press("v")
        app_state.controller.release("v")
        app_state.controller.release(pynput.keyboard.Key.ctrl_l)
    elif input_method == "clipboard_ctrl_shift_v":
        pyperclip.copy(text)
        log.debug("Text copied to clipboard, sending Ctrl+Shift+V")
        app_state.controller.press(pynput.keyboard.Key.ctrl_l)
        app_state.controller.press(pynput.keyboard.Key.shift_l)
        app_state.controller.press("v")
        app_state.controller.release("v")
        app_state.controller.release(pynput.keyboard.Key.shift_l)
        app_state.controller.release(pynput.keyboard.Key.ctrl_l)
    elif input_method == "direct":
        log.debug("Using direct typing method")
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
    # Only log recording key events to reduce log spam
    if key == app_state.rec_key_obj:
        log.debug(f"Recording key pressed: {key}")
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
    # Only log recording key events to reduce log spam
    if key == app_state.rec_key_obj:
        log.debug(f"Recording key released: {key}")
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
    log.debug(f"Evaluating key string: '{key_string}'")

    # Handle 'pynput.keyboard.Key.X' format
    if key_string.startswith("pynput.keyboard.Key."):
        key_attr = key_string.split(".")[-1]
        log.debug(f"Extracting key attribute: {key_attr}")
        if hasattr(pynput.keyboard.Key, key_attr):
            key_obj = getattr(pynput.keyboard.Key, key_attr)
            log.debug(f"Found special key: {key_obj}")
            return key_obj
        else:
            log.warning(f"Special key '{key_attr}' not found in pynput.keyboard.Key")

    # Handle 'Key.X' format
    elif key_string.startswith("Key."):
        key_attr = key_string.split(".", 1)[1]
        log.debug(f"Special key detected, looking for Key.{key_attr}")
        if hasattr(pynput.keyboard.Key, key_attr):
            key_obj = getattr(pynput.keyboard.Key, key_attr)
            log.debug(f"Found special key: {key_obj}")
            return key_obj
        else:
            log.warning(f"Special key '{key_attr}' not found in pynput.keyboard.Key")

    # For regular keys, just return the character
    log.debug(f"Using character key: '{key_string}'")
    return key_string


if __name__ == "__main__":
    main()

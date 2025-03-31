#!/usr/bin/env python3
import os
import signal
import sys
import subprocess
import threading
import time
import json
from pathlib import Path
import tomli
from PyQt5 import QtWidgets, QtGui, QtCore

# Loading configuration from TOML
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
            return None

def load_config():
    config_path = get_config_path()
    if not config_path:
        return {}
    
    print(f"Loading configuration from: {config_path}")
    
    try:
        with open(config_path, "rb") as f:
            return tomli.load(f)
    except Exception as e:
        print(f"Error reading configuration: {e}")
        return {}

# Load settings from TOML
config = load_config()

# Constants
DEFAULT_SETTINGS = {
    "temperature": 0.2,
    "prompt": """This is a transcription of a software developer speaking primarily in Russian, but frequently using English technical terms and phrases. The speaker is knowledgeable in computer science, software development, DevOps, and project management. They use technical jargon and industry terminology related to:
- Software development and programming
- System administration and DevOps
- Software architecture and design patterns
- Project management and requirements engineering
- Databases and data structures
- Algorithms and computational complexity
- Cloud technologies and infrastructure

When uncertain about a word or phrase, prioritize technical meaning over common usage. Preserve English technical terms even within Russian sentences. The speaker may switch between Russian and English mid-sentence when discussing technical concepts."""
}

class WhisperTrayIcon(QtWidgets.QSystemTrayIcon):
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Use custom icon
        script_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(script_dir, "whispex.png")
        
        if os.path.exists(icon_path):
            self.setIcon(QtGui.QIcon(icon_path))
        else:
            # Fallback to system icon
            self.setIcon(QtGui.QIcon.fromTheme("audio-input-microphone"))
            print(f"Warning: Icon not found at path {icon_path}")
        
        # Save parent widget
        self.parent_widget = parent
        
        # Audio device check will happen after menu creation
        self.has_audio = False
        self.audio_devices = []
        
        # Create log window (before loading settings, so it's available for logging)
        self.log_window = LogWindow(self.parent_widget)
        # Set feedback reference
        self.log_window.tray_icon = self
        
        # Create menu
        self.menu = QtWidgets.QMenu()
        
        # Add start action
        self.start_remote_action = self.menu.addAction("Start")
        self.start_remote_action.triggered.connect(self.start_remote_whisper)
        
        # Add stop action
        self.stop_action = self.menu.addAction("Stop")
        self.stop_action.triggered.connect(self.stop_whisper)
        self.stop_action.setEnabled(False)
        
        # Add log view action
        self.log_action = self.menu.addAction("Show Log")
        self.log_action.triggered.connect(self.show_log)
        
        # Add settings action
        self.settings_action = self.menu.addAction("Settings")
        self.settings_action.triggered.connect(self.show_settings)
        
        # Add separator
        self.menu.addSeparator()
        
        # Add exit action
        exit_action = self.menu.addAction("Exit")
        exit_action.triggered.connect(self.exit_app)
        
        # Set menu
        self.setContextMenu(self.menu)
        
        # Initialize process variables
        self.process = None
        self.output_reader = None
        self.running = False
        
    def start_remote_whisper(self):
        if not self.running:
            self.start_whisper("whispex.py")
    
    def start_whisper(self, script_name):
        """
        Start the Whispex whisper process.
        """
        script_path = os.path.join(self.script_dir, script_name)
        
        if not os.path.exists(script_path):
            self.log_window.append_text(f"Error: Script {script_path} not found")
            return False
        
        try:
            # Start the whisper script in a separate process
            subprocess.Popen([
                "uv", "run", script_path
            ], cwd=self.script_dir)
            
            self.log_window.append_text(f"Started {script_name}")
            return True
        except Exception as e:
            self.log_window.append_text(f"Error starting {script_name}: {e}")
            return False
    
    def read_output(self):
        try:
            if self.process:
                # Create threads for reading stdout and stderr
                stdout_thread = threading.Thread(target=self.read_stream, 
                                               args=(self.process.stdout, "STDOUT"))
                stderr_thread = threading.Thread(target=self.read_stream, 
                                               args=(self.process.stderr, "STDERR"))
                
                # Start threads
                stdout_thread.daemon = True
                stderr_thread.daemon = True
                stdout_thread.start()
                stderr_thread.start()
                
                # Explicitly inform user about waiting for input
                QtCore.QMetaObject.invokeMethod(
                    self.log_window, 
                    "append_text", 
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, "\n🎤 Process started. Press and hold Alt_R to record speech.")
                )
                
                # Monitor execution flow without blocking main thread
                monitor_thread = threading.Thread(target=self.monitor_process)
                monitor_thread.daemon = True
                monitor_thread.start()
                
                return
            else:
                QtCore.QMetaObject.invokeMethod(
                    self.log_window, 
                    "append_text", 
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, "ERROR: Process unavailable")
                )
        except Exception as e:
            QtCore.QMetaObject.invokeMethod(
                self.log_window, 
                "append_text", 
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, f"ERROR reading output: {str(e)}")
            )
            import traceback
            QtCore.QMetaObject.invokeMethod(
                self.log_window, 
                "append_text", 
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, traceback.format_exc())
            )
            
        # In case of error, we still wait for process completion
        QtCore.QMetaObject.invokeMethod(
            self, 
            "process_finished", 
            QtCore.Qt.QueuedConnection
        )
            
    def monitor_process(self):
        """Monitors process and triggers GUI update upon completion."""
        if self.process:
            exit_code = self.process.wait()
            QtCore.QMetaObject.invokeMethod(
                self.log_window, 
                "append_text", 
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, f"Process completed with code: {exit_code}")
            )
            
            # Update GUI in main thread
            QtCore.QMetaObject.invokeMethod(
                self, 
                "process_finished", 
                QtCore.Qt.QueuedConnection
            )
    
    def read_stream(self, stream, name):
        """Reads stream (stdout or stderr) and sends data to log window."""
        try:
            # Set non-blocking reading for stream
            os.set_blocking(stream.fileno(), False)
            
            while self.process and self.process.poll() is None:
                # Read available data without blocking
                line = stream.readline()
                if line:
                    # Add prefix to line depending on stream
                    prefix = "[ERR] " if name == "STDERR" else ""
                    # Send line to GUI thread
                    line_text = f"{prefix}{line.strip()}"
                    QtCore.QMetaObject.invokeMethod(
                        self.log_window, 
                        "append_text", 
                        QtCore.Qt.QueuedConnection,
                        QtCore.Q_ARG(str, line_text)
                    )
                else:
                    # If no new data, let CPU rest
                    QtCore.QThread.msleep(50)
            
            # Read remaining data after process completion
            for line in stream:
                if line:
                    prefix = "[ERR] " if name == "STDERR" else ""
                    line_text = f"{prefix}{line.strip()}"
                    QtCore.QMetaObject.invokeMethod(
                        self.log_window, 
                        "append_text", 
                        QtCore.Qt.QueuedConnection,
                        QtCore.Q_ARG(str, line_text)
                    )
        except Exception as e:
            QtCore.QMetaObject.invokeMethod(
                self.log_window, 
                "append_text", 
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, f"ERROR reading stream {name}: {str(e)}")
            )
    
    @QtCore.pyqtSlot()
    def process_finished(self):
        self.running = False
        self.start_remote_action.setEnabled(True)
        self.stop_action.setEnabled(False)
        self.log_window.append_text("Process terminated.")
    
    def stop_whisper(self):
        if self.process and self.running:
            try:
                self.log_window.append_text("Stopping process...")
                
                # Get all child process IDs before terminating the main process
                try:
                    # Find all child processes
                    child_pids = []
                    parent_pid = self.process.pid
                    ps_command = subprocess.run(
                        ["pgrep", "-P", str(parent_pid)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True
                    )
                    if ps_command.returncode == 0:
                        child_pids = [int(pid) for pid in ps_command.stdout.strip().split()]
                        self.log_window.append_text(f"Found child processes: {child_pids}")
                except Exception as e:
                    self.log_window.append_text(f"Error finding child processes: {str(e)}")
                
                # Send SIGTERM to main process and give it a chance to terminate properly
                os.kill(self.process.pid, signal.SIGTERM)
                
                # Wait a short time for clean termination
                max_wait = 3  # maximum wait time in seconds
                for _ in range(max_wait * 10):  # check every 100 ms
                    if self.process.poll() is not None:  # process terminated
                        self.log_window.append_text(f"Process successfully terminated with code: {self.process.returncode}")
                        break
                    time.sleep(0.1)
                
                # If process didn't terminate, force kill it
                if self.process.poll() is None:
                    self.log_window.append_text("Process did not terminate properly, forcing termination...")
                    os.kill(self.process.pid, signal.SIGKILL)
                    self.log_window.append_text("Process forcefully terminated")
                
                # Check and kill all child processes if they remain
                for pid in child_pids:
                    try:
                        # Check if process exists
                        os.kill(pid, 0)  # 0 - just checking process existence
                        # If process exists, force terminate it
                        self.log_window.append_text(f"Forcefully terminating child process {pid}")
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        # Process no longer exists
                        pass
                
                # GUI is updated in process_finished after process termination
                # Forcefully call termination handler if process_finished has not triggered yet
                if self.running:
                    self.process_finished()
                
            except Exception as e:
                self.log_window.append_text(f"Error stopping process: {str(e)}")
                import traceback
                self.log_window.append_text(traceback.format_exc())
    
    def show_log(self):
        self.log_window.show()
        self.log_window.raise_()
    
    def exit_app(self):
        self.log_window.append_text("Exiting application...")
        
        # Stop process if running
        self.stop_whisper()
        
        # Additional check and termination of remaining Python processes
        try:
            # Find all Python processes related to our whispex.py script
            output = subprocess.run(
                ["pgrep", "-f", "whispex.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            if output.returncode == 0:
                leftover_pids = output.stdout.strip().split()
                self.log_window.append_text(f"Found remaining whispex.py processes: {leftover_pids}")
                
                for pid in leftover_pids:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                        self.log_window.append_text(f"Terminated process {pid}")
                    except ProcessLookupError:
                        self.log_window.append_text(f"Process {pid} already terminated")
                    except Exception as e:
                        self.log_window.append_text(f"Error terminating process {pid}: {e}")
            else:
                self.log_window.append_text("No Whispex processes found to clean up")
        except Exception as e:
            self.log_window.append_text(f"Error terminating remaining processes: {str(e)}")
        
        # Terminate application
        QtWidgets.QApplication.quit()

    def check_audio_devices(self):
        """Checks availability of audio devices using uv run"""
        try:
            # Run script to get audio devices
            check_script = """
import json
import sounddevice as sd
print(json.dumps(sd.query_devices()))
"""
            proc = subprocess.Popen(
                ["uv", "run", "@uv", "-c", check_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            stdout, stderr = proc.communicate()
            
            if proc.returncode == 0:
                import json
                self.audio_devices = json.loads(stdout)
                self.has_audio = True
                self.log_window.append_text("✅ Audio devices access successful")
                
                # Display device information
                input_devices = [d for d in self.audio_devices if d.get('max_input_channels', 0) > 0]
                if input_devices:
                    self.log_window.append_text(f"✅ Found {len(input_devices)} audio recording devices")
                    for i, device in enumerate(input_devices):
                        self.log_window.append_text(f"    {i+1}. {device.get('name', 'Unknown device')}")
                else:
                    self.log_window.append_text("⚠️ No recording devices found. Check your microphone.")
            else:
                self.has_audio = False
                self.audio_error = stderr
                self.log_window.append_text(f"❌ Audio access error: {stderr}")
        except Exception as e:
            self.has_audio = False
            self.audio_error = str(e)
            self.log_window.append_text(f"❌ Exception during audio check: {str(e)}")

    def load_settings(self):
        """Loads settings from file or returns default values"""
        # Helper function for safe logging
        def log_message(message):
            if hasattr(self, 'log_window') and self.log_window:
                self.log_window.append_text(message)
            else:
                print(message)
        
        try:
            if os.path.exists(self.settings_path):
                try:
                    with open(self.settings_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        settings = json.loads(content)
                except UnicodeDecodeError:
                    # Try with alternative encoding if utf-8 failed
                    with open(self.settings_path, 'r', encoding='latin-1') as f:
                        content = f.read()
                        settings = json.loads(content)
                        log_message("⚠️ Settings file was read using alternative encoding")
                
                # Verify that all required keys are present
                for key, value in DEFAULT_SETTINGS.items():
                    if key not in settings:
                        settings[key] = value
                log_message(f"✅ Settings loaded from {self.settings_path}")
                log_message(f"   Temperature: {settings.get('temperature', 0.2)}")
                log_message(f"   Prompt length: {len(settings.get('prompt', ''))}")
                return settings
            else:
                log_message(f"⚠️ Settings file not found: {self.settings_path}")
        except json.JSONDecodeError as je:
            log_message(f"❌ JSON format error in settings file: {str(je)}")
            log_message(f"   Settings file will be renamed and a new one created")
            # If file is corrupted, rename it and create new one
            backup_path = f"{self.settings_path}.bak.{int(time.time())}"
            try:
                os.rename(self.settings_path, backup_path)
                log_message(f"✅ Backup saved: {backup_path}")
            except Exception as e:
                log_message(f"❌ Failed to create backup: {str(e)}")
        except Exception as e:
            log_message(f"❌ Error loading settings: {str(e)}")
            import traceback
            log_message(traceback.format_exc())
        
        # Return default settings in case of error
        log_message(f"ℹ️ Using default settings")
        return DEFAULT_SETTINGS.copy()
    
    def save_settings(self):
        """Saves settings to file"""
        # Helper function for safe logging
        def log_message(message):
            if hasattr(self, 'log_window') and self.log_window:
                self.log_window.append_text(message)
            else:
                print(message)
                
        try:
            # Check directory write permissions
            settings_dir = os.path.dirname(self.settings_path)
            if not os.access(settings_dir, os.W_OK):
                log_message(f"❌ No write permissions for directory: {settings_dir}")
                return False
                
            # First create temporary file for safe saving
            temp_path = f"{self.settings_path}.tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json_str = json.dumps(self.settings, ensure_ascii=False, indent=4)
                f.write(json_str)
            
            # If temp file was created successfully, rename it
            os.replace(temp_path, self.settings_path)
            
            log_message(f"✅ Settings saved to file: {self.settings_path}")
            log_message(f"   Temperature: {self.settings.get('temperature', 0.2)}")
            log_message(f"   Prompt length: {len(self.settings.get('prompt', ''))}")
            return True
        except Exception as e:
            log_message(f"❌ Error saving settings: {str(e)}")
            import traceback
            log_message(traceback.format_exc())
            return False
            
    def show_settings(self):
        """Shows settings dialog"""
        # Helper function for safe logging
        def log_message(message):
            if hasattr(self, 'log_window') and self.log_window:
                self.log_window.append_text(message)
            else:
                print(message)
                
        log_message("⚙️ Opening settings dialog...")
        settings_dialog = SettingsDialog(self.settings, self.parent_widget)
        if settings_dialog.exec_() == QtWidgets.QDialog.Accepted:
            # Update settings
            old_settings = self.settings.copy()
            self.settings = settings_dialog.get_settings()
            
            # Output information about new settings
            log_message(f"ℹ️ New settings:")
            log_message(f"   Temperature: {self.settings.get('temperature', 0.2)}")
            log_message(f"   Prompt length: {len(self.settings.get('prompt', ''))}")
            
            # Save to file
            if self.save_settings():
                log_message("✅ Settings saved successfully")
                
                # If process is already running, restart it with new settings
                if self.running:
                    log_message("🔄 Restarting process with new settings...")
                    # Stop current process
                    self.stop_whisper()
                    # Start process with new settings
                    self.start_remote_whisper()
            else:
                log_message("❌ Failed to save settings")

    def cleanup(self):
        """
        Clean up resources and terminate any running Whispex processes.
        """
        self.log_window.append_text("Cleaning up before exit...")
        
        try:
            # Find all Python processes related to our whispex.py script
            output = subprocess.run(
                ["pgrep", "-f", "whispex.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            if output.returncode == 0:
                leftover_pids = output.stdout.strip().split()
                self.log_window.append_text(f"Found remaining whispex.py processes: {leftover_pids}")
                
                for pid in leftover_pids:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                        self.log_window.append_text(f"Terminated process {pid}")
                    except ProcessLookupError:
                        self.log_window.append_text(f"Process {pid} already terminated")
                    except Exception as e:
                        self.log_window.append_text(f"Error terminating process {pid}: {e}")
            else:
                self.log_window.append_text("No Whispex processes found to clean up")
        except Exception as e:
            self.log_window.append_text(f"Error terminating remaining processes: {str(e)}")

class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings.copy()
        self.setWindowTitle("Whispex Settings")
        self.resize(700, 500)
        
        # Set icon for settings window
        script_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(script_dir, "whispex.png")
        
        if os.path.exists(icon_path):
            self.setWindowIcon(QtGui.QIcon(icon_path))
        
        # Create widgets
        layout = QtWidgets.QVBoxLayout()
        
        # Temperature
        temp_layout = QtWidgets.QHBoxLayout()
        temp_label = QtWidgets.QLabel("Temperature:")
        self.temp_spinbox = QtWidgets.QDoubleSpinBox()
        self.temp_spinbox.setMinimum(0.0)
        self.temp_spinbox.setMaximum(1.0)
        self.temp_spinbox.setSingleStep(0.1)
        self.temp_spinbox.setValue(settings.get('temperature', 0.2))
        self.temp_spinbox.setToolTip("Value from 0.0 to 1.0. Lower values make output more deterministic.")
        temp_layout.addWidget(temp_label)
        temp_layout.addWidget(self.temp_spinbox)
        layout.addLayout(temp_layout)
        
        # Prompt
        prompt_label = QtWidgets.QLabel("Prompt for Whisper:")
        layout.addWidget(prompt_label)
        
        self.prompt_text = QtWidgets.QTextEdit()
        self.prompt_text.setPlainText(settings.get('prompt', DEFAULT_SETTINGS['prompt']))
        layout.addWidget(self.prompt_text)
        
        # Buttons
        button_layout = QtWidgets.QHBoxLayout()
        
        reset_button = QtWidgets.QPushButton("Reset Settings")
        reset_button.clicked.connect(self.reset_settings)
        
        apply_button = QtWidgets.QPushButton("Apply")
        apply_button.clicked.connect(self.accept)
        
        cancel_button = QtWidgets.QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        
        button_layout.addWidget(reset_button)
        button_layout.addStretch()
        button_layout.addWidget(cancel_button)
        button_layout.addWidget(apply_button)
        
        layout.addLayout(button_layout)
        self.setLayout(layout)
    
    def reset_settings(self):
        """Resets settings to default values"""
        self.temp_spinbox.setValue(DEFAULT_SETTINGS.get('temperature', 0.2))
        self.prompt_text.setPlainText(DEFAULT_SETTINGS.get('prompt', ''))
    
    def get_settings(self):
        """Returns current settings from dialog"""
        settings = self.settings.copy()
        
        # Get and verify temperature
        temperature = self.temp_spinbox.value()
        settings['temperature'] = temperature
        
        # Get and verify prompt
        prompt = self.prompt_text.toPlainText()
        # Check if prompt is empty
        if not prompt.strip():
            prompt = DEFAULT_SETTINGS['prompt']
            print(f"WARNING: Prompt was empty, using default prompt")
        settings['prompt'] = prompt
        
        # Output debug information
        print(f"DEBUG: get_settings -> temperature={temperature}, prompt_length={len(prompt)}")
        
        return settings

class LogWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Whispex")
        self.resize(700, 500)
        
        # Set icon for log window
        script_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(script_dir, "whispex.png")
        
        if os.path.exists(icon_path):
            self.setWindowIcon(QtGui.QIcon(icon_path))
        
        # Initialize tray_icon reference
        self.tray_icon = None
        
        # Create text widget for displaying log
        self.log_text = QtWidgets.QTextEdit()
        self.log_text.setReadOnly(True)
        
        # Create buttons
        button_layout = QtWidgets.QHBoxLayout()
        
        clear_button = QtWidgets.QPushButton("Clear Log")
        clear_button.clicked.connect(self.clear_log)
        button_layout.addWidget(clear_button)
        
        check_audio_button = QtWidgets.QPushButton("Check Audio")
        check_audio_button.clicked.connect(self.parent_check_audio)
        button_layout.addWidget(check_audio_button)
        
        # Add settings button
        settings_button = QtWidgets.QPushButton("Settings")
        settings_button.clicked.connect(self.show_settings)
        button_layout.addWidget(settings_button)
        
        # Add control buttons
        start_button = QtWidgets.QPushButton("Start")
        start_button.clicked.connect(self.start_service)
        button_layout.addWidget(start_button)
        
        stop_button = QtWidgets.QPushButton("Stop")
        stop_button.clicked.connect(self.stop_service)
        button_layout.addWidget(stop_button)
        
        # Create layout
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.log_text)
        layout.addLayout(button_layout)
        self.setLayout(layout)
    
    def parent_check_audio(self):
        # Access tray_icon instead of parent
        if hasattr(self, 'tray_icon') and self.tray_icon and hasattr(self.tray_icon, 'check_audio_devices'):
            self.append_text("🔍 Re-checking audio devices...")
            self.tray_icon.check_audio_devices()
    
    def start_service(self):
        # Start service through tray_icon
        if hasattr(self, 'tray_icon') and self.tray_icon and hasattr(self.tray_icon, 'start_remote_whisper'):
            self.append_text("🚀 Starting recognition service...")
            self.tray_icon.start_remote_whisper()
    
    def stop_service(self):
        # Stop service through tray_icon
        if hasattr(self, 'tray_icon') and self.tray_icon and hasattr(self.tray_icon, 'stop_whisper'):
            self.append_text("🛑 Stopping recognition service...")
            self.tray_icon.stop_whisper()
    
    def show_settings(self):
        # Show settings through tray_icon
        if hasattr(self, 'tray_icon') and self.tray_icon and hasattr(self.tray_icon, 'show_settings'):
            self.append_text("⚙️ Opening settings...")
            self.tray_icon.show_settings()
    
    @QtCore.pyqtSlot(str)
    def append_text(self, text):
        self.log_text.append(text)
        # Scroll down
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def clear_log(self):
        self.log_text.clear()

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # Don't close app when windows are closed
    
    # Create invisible main window for tray support
    main_widget = QtWidgets.QWidget()
    
    tray_icon = WhisperTrayIcon(main_widget)
    tray_icon.show()
    
    # Show log window at startup to display status
    tray_icon.log_window.show()
    tray_icon.log_window.append_text("ℹ️ Using uv to run Python scripts")
    
    # Check audio devices
    tray_icon.check_audio_devices()
    
    # Show message at startup
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whispex.png")
    notification_icon = QtGui.QIcon(icon_path) if os.path.exists(icon_path) else QtGui.QIcon.fromTheme("audio-input-microphone")
    
    tray_icon.showMessage(
        "Whispex", 
        "Application running in system tray", 
        notification_icon, 
        3000
    )
    
    # Automatically start recognition service after app loads
    QtCore.QTimer.singleShot(1000, lambda: tray_icon.start_remote_whisper())
    
    sys.exit(app.exec_()) 
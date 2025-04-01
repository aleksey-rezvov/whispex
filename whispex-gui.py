import os
import signal
import subprocess
import sys
from pathlib import Path

import tomli
from PyQt5 import QtCore, QtGui, QtWidgets

# Import logger
from logger import log
# Import settings manager
from settings import (GeneralSettings, OpenAISettings, SettingsManager,
                      SettingsSection, WhisperSettings)

# Constants
UV_RUN_COMMAND = ["uv", "run"]
LOG_WINDOW_SIZE = (700, 500)
SETTINGS_DIALOG_SIZE = (700, 500)


class WhisperTrayIcon(QtWidgets.QSystemTrayIcon):
    def __init__(self, parent=None):
        super().__init__(parent)

        # Setup script directories and paths
        self.script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        self.script_path = self.script_dir / "whispex.py"

        # Store icon path as class member
        self.icon_path = self.script_dir / "whispex.png"

        if self.icon_path.exists():
            self.setIcon(QtGui.QIcon(str(self.icon_path)))
        else:
            # Fallback to system icon
            self.setIcon(QtGui.QIcon.fromTheme("audio-input-microphone"))
            log.warning(f"Icon not found at path {self.icon_path}")

        # Save parent widget
        self.parent_widget = parent

        # Create log window (before loading settings, so it's available for logging)
        self.log_window = LogWindow(self.parent_widget)
        # Set feedback reference
        self.log_window.tray_icon = self

        # Initialize settings manager
        self.settings_manager = SettingsManager()

        # Check OpenAI API status
        self.check_api_status()

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

    def check_api_status(self):
        """Check if the OpenAI API key is configured and valid"""
        api_key = self.settings_manager.get(SettingsSection.OPENAI, OpenAISettings.API_KEY, "")
        env_key = os.environ.get("OPENAI_API_KEY", "")

        # Check if we have an API key from either source
        if api_key or env_key:
            self.log_window.update_api_status(connected=True)
            self.log_window.append_text("✅ OpenAI API key configured")
        else:
            self.log_window.update_api_status(connected=False)
            self.log_window.append_text("❌ OpenAI API key not configured")
            self.log_window.append_text("Please configure API key in Settings → OpenAI tab")

    def start_remote_whisper(self):
        if not self.running:
            success = self.start_whisper("whispex.py")
            if success:
                self.running = True
                self.start_remote_action.setEnabled(False)
                self.stop_action.setEnabled(True)
                # Update log window
                self.log_window.update_status(running=True)
                # Show notification
                icon = (
                    QtGui.QIcon(str(self.icon_path))
                    if self.icon_path.exists()
                    else QtGui.QIcon.fromTheme("audio-input-microphone")
                )
                self.showMessage(
                    "Whispex", "Speech recognition service started", icon, 3000
                )

    def start_whisper(self, script_name):
        """
        Start the Whispex whisper process.
        """
        if not self.script_path.exists():
            self.log_window.append_text(f"Error: Script {self.script_path} not found")
            return False

        try:
            # Start the whisper script in a separate process
            subprocess.Popen(
                UV_RUN_COMMAND + [str(self.script_path)], cwd=str(self.script_dir)
            )

            self.log_window.append_text(f"Started {script_name}")
            return True
        except Exception as e:
            self.log_window.append_text(f"Error starting {script_name}: {e}")
            return False

    def stop_whisper(self):
        """
        Stop any running Whispex processes
        """
        self.log_window.append_text("Stopping all Whispex processes...")

        # Stop all whispex processes
        try:
            output = subprocess.run(
                ["pgrep", "-f", "whispex.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )

            if output.returncode == 0:
                pids = output.stdout.strip().split()
                if pids:
                    self.log_window.append_text(
                        f"Found {len(pids)} Whispex processes: {pids}"
                    )
                    for pid in pids:
                        try:
                            os.kill(int(pid), signal.SIGTERM)
                            self.log_window.append_text(
                                f"Sent termination signal to process {pid}"
                            )
                        except ProcessLookupError:
                            self.log_window.append_text(
                                f"Process {pid} already terminated"
                            )
                        except Exception as e:
                            self.log_window.append_text(
                                f"Error terminating process {pid}: {e}"
                            )
                else:
                    self.log_window.append_text("No Whispex processes found")
            else:
                self.log_window.append_text("No Whispex processes found")
        except Exception as e:
            self.log_window.append_text(f"Error stopping Whispex processes: {e}")
            import traceback

            self.log_window.append_text(traceback.format_exc())

        # Update GUI state
        self.running = False
        self.start_remote_action.setEnabled(True)
        self.stop_action.setEnabled(False)
        self.log_window.update_status(running=False)
        self.log_window.append_text("Speech recognition service stopped")

    def show_log(self):
        self.log_window.show()
        self.log_window.raise_()

    def exit_app(self):
        self.log_window.append_text("Exiting application...")

        # Stop process if running
        self.stop_whisper()

        # Terminate application
        QtWidgets.QApplication.quit()

    def show_settings(self):
        """Shows settings dialog"""

        # Helper function for safe logging
        def log_message(message):
            if hasattr(self, "log_window") and self.log_window:
                self.log_window.append_text(message)
            else:
                log.info(message)

        log_message("⚙️ Opening settings dialog...")
        settings_dialog = SettingsDialog(self.settings_manager, self.parent_widget)
        if settings_dialog.exec_() == QtWidgets.QDialog.Accepted:
            # Settings are automatically saved when changed now
            log_message("✅ Settings applied successfully")

            # Re-check API status after settings changes
            self.check_api_status()


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.setWindowTitle("Whispex Settings")
        self.resize(*SETTINGS_DIALOG_SIZE)

        # Set icon for settings window
        script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        icon_path = script_dir / "whispex.png"

        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        # Create main layout with tabs
        layout = QtWidgets.QVBoxLayout()
        self.tabs = QtWidgets.QTabWidget()

        # Create tabs for different setting categories
        self.create_general_tab()
        self.create_whisper_tab()
        self.create_openai_tab()

        layout.addWidget(self.tabs)

        # Buttons
        button_layout = QtWidgets.QHBoxLayout()

        reset_button = QtWidgets.QPushButton("Reset Settings")
        reset_button.clicked.connect(self.reset_settings)

        apply_button = QtWidgets.QPushButton("Apply")
        apply_button.clicked.connect(self.apply_settings)

        cancel_button = QtWidgets.QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(reset_button)
        button_layout.addStretch()
        button_layout.addWidget(cancel_button)
        button_layout.addWidget(apply_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def create_general_tab(self):
        """Create General Settings tab"""
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "General")

        layout = QtWidgets.QFormLayout()
        tab.setLayout(layout)

        # Language setting
        self.language_input = QtWidgets.QLineEdit()
        self.language_input.setText(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.LANGUAGE, "en"
            )
        )
        self.language_input.setToolTip("Language code (e.g. 'en', 'ru', 'fr')")
        layout.addRow("Language:", self.language_input)

        # Recording key
        self.rec_key_input = QtWidgets.QLineEdit()
        self.rec_key_input.setText(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.REC_KEY, "pynput.keyboard.Key.alt_r"
            )
        )
        self.rec_key_input.setToolTip("Key used for push-to-talk recording")
        layout.addRow("Recording Key:", self.rec_key_input)

        # Input method
        self.input_method_combo = QtWidgets.QComboBox()
        self.input_method_combo.addItems(["clipboard_ctrl_v", "clipboard_ctrl_shift_v", "direct"])
        current_method = self.settings_manager.get(
            SettingsSection.GENERAL, GeneralSettings.INPUT_METHOD, "clipboard_ctrl_shift_v"
        )
        self.input_method_combo.setCurrentText(current_method)
        self.input_method_combo.setToolTip("Method to insert transcribed text")
        layout.addRow("Input Method:", self.input_method_combo)

        # Input devices
        device_layout = QtWidgets.QHBoxLayout()

        # Default device checkbox
        self.default_device_checkbox = QtWidgets.QCheckBox("Use default device")
        self.default_device_checkbox.setChecked(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.DEFAULT_DEVICE, True
            )
        )
        self.default_device_checkbox.toggled.connect(self.on_default_device_toggled)

        # Input device selection
        self.input_device_combo = QtWidgets.QComboBox()
        self.populate_audio_devices()
        current_device = self.settings_manager.get(
            SettingsSection.GENERAL, GeneralSettings.INPUT_DEVICE, ""
        )

        if current_device:
            index = self.input_device_combo.findText(current_device)
            if index >= 0:
                self.input_device_combo.setCurrentIndex(index)

        # Enable/disable device selection based on default device setting
        self.input_device_combo.setEnabled(not self.default_device_checkbox.isChecked())

        device_layout.addWidget(self.default_device_checkbox)
        device_layout.addWidget(self.input_device_combo)

        layout.addRow("Audio Input:", device_layout)

        # Auto off time
        self.auto_off_spinbox = QtWidgets.QSpinBox()
        self.auto_off_spinbox.setMinimum(0)
        self.auto_off_spinbox.setMaximum(86400)  # 24 hours in seconds
        self.auto_off_spinbox.setValue(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.AUTO_OFF_TIME, 0
            )
        )
        self.auto_off_spinbox.setToolTip("Automatically exit after N seconds of inactivity (0 to disable)")
        layout.addRow("Auto Off Time (seconds):", self.auto_off_spinbox)

        # No type option
        self.no_type_checkbox = QtWidgets.QCheckBox()
        self.no_type_checkbox.setChecked(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.NO_TYPE, False
            )
        )
        self.no_type_checkbox.setToolTip("Don't type transcribed text when enabled")
        layout.addRow("Disable Text Input:", self.no_type_checkbox)

    def on_default_device_toggled(self, checked):
        """Enable/disable device selection based on default device setting"""
        self.input_device_combo.setEnabled(not checked)

    def populate_audio_devices(self):
        """Populate audio input devices dropdown"""
        try:
            import sounddevice as sd
            devices = sd.query_devices()

            # Clear combo box
            self.input_device_combo.clear()

            # Add empty option
            self.input_device_combo.addItem("Default", "")

            # Add input devices
            for i, device in enumerate(devices):
                if device.get('max_input_channels', 0) > 0:
                    name = device.get('name', f"Device {i}")
                    self.input_device_combo.addItem(name, i)
        except Exception as e:
            log.error(f"Error populating audio devices: {e}")
            self.input_device_combo.addItem("No devices found", "")

    def create_whisper_tab(self):
        """Create Whisper Settings tab"""
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "Whisper")

        layout = QtWidgets.QVBoxLayout()
        tab.setLayout(layout)

        form_layout = QtWidgets.QFormLayout()

        # Temperature
        self.temp_spinbox = QtWidgets.QDoubleSpinBox()
        self.temp_spinbox.setMinimum(0.0)
        self.temp_spinbox.setMaximum(1.0)
        self.temp_spinbox.setSingleStep(0.1)
        self.temp_spinbox.setValue(
            self.settings_manager.get(
                SettingsSection.WHISPER, WhisperSettings.TEMPERATURE, 0.2
            )
        )
        self.temp_spinbox.setToolTip(
            "Value from 0.0 to 1.0. Lower values make output more deterministic."
        )
        form_layout.addRow("Temperature:", self.temp_spinbox)

        # Sample rate
        self.sample_rate_combo = QtWidgets.QComboBox()
        self.sample_rate_combo.addItems(["16000", "8000", "24000", "44100", "48000"])
        current_sample_rate = str(self.settings_manager.get(
            SettingsSection.WHISPER, WhisperSettings.SAMPLE_RATE, 16000
        ))
        self.sample_rate_combo.setCurrentText(current_sample_rate)
        self.sample_rate_combo.setToolTip("Whisper sampling rate in Hz")
        form_layout.addRow("Sample Rate:", self.sample_rate_combo)

        # Recording sample rate
        self.recording_sample_rate_combo = QtWidgets.QComboBox()
        self.recording_sample_rate_combo.addItems(["16000", "44100", "48000", "96000"])
        current_recording_rate = str(self.settings_manager.get(
            SettingsSection.WHISPER, WhisperSettings.RECORDING_SAMPLE_RATE, 48000
        ))
        self.recording_sample_rate_combo.setCurrentText(current_recording_rate)
        self.recording_sample_rate_combo.setToolTip("Recording sampling rate in Hz (should be multiple of Sample Rate)")
        form_layout.addRow("Recording Sample Rate:", self.recording_sample_rate_combo)

        layout.addLayout(form_layout)

        # Prompt
        prompt_group = QtWidgets.QGroupBox("Prompt for Whisper")
        prompt_layout = QtWidgets.QVBoxLayout()
        prompt_group.setLayout(prompt_layout)

        self.prompt_text = QtWidgets.QTextEdit()
        default_prompt = self.settings_manager.get(
            SettingsSection.WHISPER, WhisperSettings.PROMPT, ""
        )
        self.prompt_text.setPlainText(default_prompt)
        self.prompt_text.setToolTip("Context to help improve transcription accuracy")

        prompt_layout.addWidget(self.prompt_text)
        layout.addWidget(prompt_group)

    def create_openai_tab(self):
        """Create OpenAI Settings tab"""
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "OpenAI")

        layout = QtWidgets.QFormLayout()
        tab.setLayout(layout)

        # API Key
        self.api_key_input = QtWidgets.QLineEdit()
        current_key = self.settings_manager.get(
            SettingsSection.OPENAI, OpenAISettings.API_KEY, ""
        )
        self.api_key_input.setText(current_key)
        self.api_key_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key_input.setToolTip("Your OpenAI API key (leave empty to use OPENAI_API_KEY environment variable)")
        layout.addRow("API Key:", self.api_key_input)

        # Information label
        info_label = QtWidgets.QLabel(
            "Leave API key empty to use the OPENAI_API_KEY environment variable.\n"
            "You need a valid OpenAI API key for the application to work."
        )
        info_label.setWordWrap(True)
        layout.addRow("", info_label)

    def reset_settings(self):
        """Resets settings to default values"""
        # Load default values from config file
        try:
            script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
            default_config_path = script_dir / "default_config.toml"

            if default_config_path.exists():
                with open(default_config_path, "rb") as f:
                    default_config = tomli.load(f)

                # Set values from default config for General tab
                self.language_input.setText(default_config["general"]["language"])
                self.rec_key_input.setText(default_config["general"]["rec_key"])
                self.input_method_combo.setCurrentText(default_config["general"]["input_method"])
                self.auto_off_spinbox.setValue(default_config["general"]["auto_off_time"])
                self.no_type_checkbox.setChecked(default_config["general"]["no_type"])

                # Audio device settings
                self.default_device_checkbox.setChecked(default_config["general"].get("default_device", True))
                self.input_device_combo.setEnabled(not self.default_device_checkbox.isChecked())
                self.input_device_combo.setCurrentIndex(0)  # Set to default option

                # Set values from default config for Whisper tab
                self.temp_spinbox.setValue(default_config["whisper"]["temperature"])
                self.sample_rate_combo.setCurrentText(str(default_config["whisper"]["sample_rate"]))
                self.recording_sample_rate_combo.setCurrentText(str(default_config["whisper"]["recording_sample_rate"]))
                self.prompt_text.setPlainText(default_config["whisper"]["prompt"])

                # OpenAI tab - API key is not set in defaults typically
                self.api_key_input.clear()
            else:
                log.warning("Default config file not found")
        except Exception as e:
            log.error(f"Error loading default settings: {e}")
            # Use safe fallback values
            self.language_input.setText("en")
            self.rec_key_input.setText("pynput.keyboard.Key.alt_r")
            self.input_method_combo.setCurrentText("clipboard_ctrl_shift_v")
            self.auto_off_spinbox.setValue(0)
            self.no_type_checkbox.setChecked(False)
            self.default_device_checkbox.setChecked(True)
            self.input_device_combo.setEnabled(False)
            self.temp_spinbox.setValue(0.2)
            self.sample_rate_combo.setCurrentText("16000")
            self.recording_sample_rate_combo.setCurrentText("48000")
            self.prompt_text.clear()
            self.api_key_input.clear()

    def apply_settings(self):
        """Apply settings and close dialog"""
        # General settings
        lang = self.language_input.text().strip()
        rec_key = self.rec_key_input.text().strip()
        input_method = self.input_method_combo.currentText()
        auto_off_time = self.auto_off_spinbox.value()
        no_type = self.no_type_checkbox.isChecked()
        default_device = self.default_device_checkbox.isChecked()

        # Get selected input device
        input_device = ""
        if not default_device:
            index = self.input_device_combo.currentIndex()
            if index > 0:  # Skip the "Default" option
                input_device = self.input_device_combo.currentText()

        # Whisper settings
        temperature = self.temp_spinbox.value()
        sample_rate = int(self.sample_rate_combo.currentText())
        recording_sample_rate = int(self.recording_sample_rate_combo.currentText())
        prompt = self.prompt_text.toPlainText()

        # OpenAI settings
        api_key = self.api_key_input.text().strip()

        # Update settings
        success = True

        # General settings
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.LANGUAGE, lang)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.REC_KEY, rec_key)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.INPUT_METHOD, input_method)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.AUTO_OFF_TIME, auto_off_time)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.NO_TYPE, no_type)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.DEFAULT_DEVICE, default_device)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.INPUT_DEVICE, input_device)

        # Whisper settings
        success &= self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.TEMPERATURE, temperature)
        success &= self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.SAMPLE_RATE, sample_rate)
        success &= self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.RECORDING_SAMPLE_RATE, recording_sample_rate)
        success &= self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.PROMPT, prompt)

        # OpenAI settings - only set if not empty
        if api_key:
            success &= self.settings_manager.set(SettingsSection.OPENAI, OpenAISettings.API_KEY, api_key)

        if success:
            log.info("Settings updated successfully")
        else:
            log.error("Error saving settings")

        # Accept dialog
        self.accept()


class LogWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Whispex")
        self.resize(*LOG_WINDOW_SIZE)

        # Set icon for log window
        script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        icon_path = script_dir / "whispex.png"

        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        # Initialize tray_icon reference
        self.tray_icon = None

        # Create text widget for displaying log
        self.log_text = QtWidgets.QTextEdit()
        self.log_text.setReadOnly(True)

        # Create status bar
        status_layout = QtWidgets.QHBoxLayout()

        # Service status label
        self.status_label = QtWidgets.QLabel("Service: Stopped")
        status_layout.addWidget(self.status_label)

        # API status
        self.api_status = QtWidgets.QLabel("API: Unknown")
        self.api_status.setStyleSheet("color: gray;")
        status_layout.addWidget(self.api_status)

        # Add spacer to push everything to the left
        status_layout.addStretch()

        # Create buttons
        button_layout = QtWidgets.QHBoxLayout()

        clear_button = QtWidgets.QPushButton("Clear Log")
        clear_button.clicked.connect(self.clear_log)
        button_layout.addWidget(clear_button)

        # Add audio devices button
        audio_devices_button = QtWidgets.QPushButton("Audio Devices")
        audio_devices_button.clicked.connect(self.show_audio_devices)
        button_layout.addWidget(audio_devices_button)

        # Add settings button
        settings_button = QtWidgets.QPushButton("Settings")
        settings_button.clicked.connect(self.show_settings)
        button_layout.addWidget(settings_button)

        # Add control buttons
        self.start_button = QtWidgets.QPushButton("Start")
        self.start_button.clicked.connect(self.start_service)
        button_layout.addWidget(self.start_button)

        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_service)
        self.stop_button.setEnabled(False)
        button_layout.addWidget(self.stop_button)

        # Create layout
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.log_text)
        layout.addLayout(status_layout)
        layout.addLayout(button_layout)
        self.setLayout(layout)

    def show_audio_devices(self):
        """Show information about available audio input devices"""
        self.append_text("🎤 Checking audio input devices...")
        try:
            import sounddevice as sd
            devices = sd.query_devices()

            self.append_text(f"Found {len(devices)} audio devices:")

            # Show input devices
            input_devices = []
            for i, device in enumerate(devices):
                max_input = device.get('max_input_channels', 0)
                if max_input > 0:
                    name = device.get('name', f"Device {i}")
                    input_devices.append((i, name, max_input))

            if input_devices:
                self.append_text("Input devices:")
                for i, name, channels in input_devices:
                    self.append_text(f"  [{i}] {name} ({channels} channels)")
            else:
                self.append_text("⚠️ No input devices found!")

            # Show current settings
            settings_manager = self.tray_icon.settings_manager if self.tray_icon else None
            if settings_manager:
                use_default = settings_manager.get(
                    SettingsSection.GENERAL, GeneralSettings.DEFAULT_DEVICE, True
                )
                device_name = settings_manager.get(
                    SettingsSection.GENERAL, GeneralSettings.INPUT_DEVICE, ""
                )

                if use_default:
                    self.append_text("\nCurrent setting: Using system default device")
                elif device_name:
                    self.append_text(f"\nCurrent setting: Using specific device '{device_name}'")
                else:
                    self.append_text("\nCurrent setting: Default (no device specified)")

        except Exception as e:
            self.append_text(f"❌ Error checking audio devices: {str(e)}")

    def update_status(self, running=False):
        """Update display status based on service state"""
        if running:
            self.status_label.setText("Service: Running")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
        else:
            self.status_label.setText("Service: Stopped")
            self.status_label.setStyleSheet("color: red;")
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)

    def update_api_status(self, connected=False):
        """Update API connection status"""
        if connected:
            self.api_status.setText("API: Connected")
            self.api_status.setStyleSheet("color: green;")
        else:
            self.api_status.setText("API: Not Connected")
            self.api_status.setStyleSheet("color: red;")

    def start_service(self):
        # Start service through tray_icon
        if (
            hasattr(self, "tray_icon")
            and self.tray_icon
            and hasattr(self.tray_icon, "start_remote_whisper")
        ):
            self.append_text("🚀 Starting recognition service...")
            self.tray_icon.start_remote_whisper()
            self.update_status(running=True)

    def stop_service(self):
        # Stop service through tray_icon
        if (
            hasattr(self, "tray_icon")
            and self.tray_icon
            and hasattr(self.tray_icon, "stop_whisper")
        ):
            self.append_text("🛑 Stopping recognition service...")
            self.tray_icon.stop_whisper()
            self.update_status(running=False)

    def show_settings(self):
        # Show settings through tray_icon
        if (
            hasattr(self, "tray_icon")
            and self.tray_icon
            and hasattr(self.tray_icon, "show_settings")
        ):
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


def main():
    # Create Qt application
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # Don't close app when all windows closed

    # Create main widget (needed to parent the tray icon)
    main_widget = QtWidgets.QWidget()

    # Create tray icon
    tray_icon = WhisperTrayIcon(main_widget)
    tray_icon.show()

    # Show startup message
    tray_icon.showMessage(
        "Whispex",
        "Speech recognition service is ready. Click the tray icon to start.",
        tray_icon.icon(),
        3000,
    )

    # Execute application
    sys.exit(app.exec_())


def check_dependencies():
    """Check if all required dependencies are installed"""
    try:
        import numpy
        import openai
        import PyQt5
        import sounddevice
        import soundfile
        return True
    except ImportError as e:
        print(f"Missing dependency: {e}")
        return False


def check_microphone_access():
    """Check if application has access to microphone"""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        input_devices = [d for d in devices if d['max_input_channels'] > 0]

        if not input_devices:
            print("No input devices found!")
            return False

        print(f"Available input devices: {len(input_devices)}")
        for device in input_devices:
            print(f" - {device['name']}")

        return True
    except Exception as e:
        print(f"Error checking microphone access: {e}")
        return False


if __name__ == "__main__":
    # Check dependencies first
    if not check_dependencies():
        print("Missing dependencies. Please run 'uv pip install -e .'")
        sys.exit(1)

    # Check microphone access
    if not check_microphone_access():
        print("No microphone access. Please check your system settings.")
        # We'll still launch the app, but it won't be able to record

    # Start main application
    main()

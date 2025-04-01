import os
import signal
import sys
import subprocess
import time
from pathlib import Path
from PyQt5 import QtWidgets, QtGui, QtCore
import tomli

# Import settings manager
from settings import SettingsManager, SettingsSection, WhisperSettings
# Import logger
from logger import log

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
            success = self.start_whisper("whispex.py")
            if success:
                self.running = True
                self.start_remote_action.setEnabled(False)
                self.stop_action.setEnabled(True)
                # Show notification
                icon = QtGui.QIcon(str(self.icon_path)) if self.icon_path.exists() else QtGui.QIcon.fromTheme("audio-input-microphone")
                self.showMessage(
                    "Whispex", 
                    "Speech recognition service started", 
                    icon, 
                    3000
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
                UV_RUN_COMMAND + [str(self.script_path)], 
                cwd=str(self.script_dir)
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
                universal_newlines=True
            )
            
            if output.returncode == 0:
                pids = output.stdout.strip().split()
                if pids:
                    self.log_window.append_text(f"Found {len(pids)} Whispex processes: {pids}")
                    for pid in pids:
                        try:
                            os.kill(int(pid), signal.SIGTERM)
                            self.log_window.append_text(f"Sent termination signal to process {pid}")
                        except ProcessLookupError:
                            self.log_window.append_text(f"Process {pid} already terminated")
                        except Exception as e:
                            self.log_window.append_text(f"Error terminating process {pid}: {e}")
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
            if hasattr(self, 'log_window') and self.log_window:
                self.log_window.append_text(message)
            else:
                log.info(message)
                
        log_message("⚙️ Opening settings dialog...")
        settings_dialog = SettingsDialog(self.settings_manager, self.parent_widget)
        if settings_dialog.exec_() == QtWidgets.QDialog.Accepted:
            # Settings are automatically saved when changed now
            log_message("✅ Settings applied successfully")
                
            # If process is already running, restart it with new settings
            if self.running:
                log_message("🔄 Restarting process with new settings...")
                # Stop current process
                self.stop_whisper()
                # Start process with new settings
                self.start_remote_whisper()

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
        
        # Create widgets
        layout = QtWidgets.QVBoxLayout()
        
        # Temperature
        temp_layout = QtWidgets.QHBoxLayout()
        temp_label = QtWidgets.QLabel("Temperature:")
        self.temp_spinbox = QtWidgets.QDoubleSpinBox()
        self.temp_spinbox.setMinimum(0.0)
        self.temp_spinbox.setMaximum(1.0)
        self.temp_spinbox.setSingleStep(0.1)
        self.temp_spinbox.setValue(settings_manager.get(SettingsSection.WHISPER, WhisperSettings.TEMPERATURE, 0.2))
        self.temp_spinbox.setToolTip("Value from 0.0 to 1.0. Lower values make output more deterministic.")
        temp_layout.addWidget(temp_label)
        temp_layout.addWidget(self.temp_spinbox)
        layout.addLayout(temp_layout)
        
        # Prompt
        prompt_label = QtWidgets.QLabel("Prompt for Whisper:")
        layout.addWidget(prompt_label)
        
        self.prompt_text = QtWidgets.QTextEdit()
        default_prompt = settings_manager.get(SettingsSection.WHISPER, WhisperSettings.PROMPT, "")
        self.prompt_text.setPlainText(default_prompt)
        layout.addWidget(self.prompt_text)
        
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
    
    def reset_settings(self):
        """Resets settings to default values"""
        # Load default values from config file
        try:
            script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
            default_config_path = script_dir / "default_config.toml"
            
            if default_config_path.exists():
                with open(default_config_path, "rb") as f:
                    default_config = tomli.load(f)
                    
                # Set values from default config
                self.temp_spinbox.setValue(default_config["whisper"]["temperature"])
                self.prompt_text.setPlainText(default_config["whisper"]["prompt"])
            else:
                log.warning("Default config file not found")
        except Exception as e:
            log.error(f"Error loading default settings: {e}")
            # Use safe fallback values
            self.temp_spinbox.setValue(0.2)
            self.prompt_text.clear()
    
    def apply_settings(self):
        """Apply settings and close dialog"""
        # Get temperature
        temperature = self.temp_spinbox.value()
        
        # Get prompt
        prompt = self.prompt_text.toPlainText()
        if not prompt.strip():
            log.warning("Prompt was empty, using empty string")
            prompt = ""
        
        # Update settings directly
        success1 = self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.TEMPERATURE, temperature)
        success2 = self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.PROMPT, prompt)
        
        if success1 and success2:
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
        
        # Create buttons
        button_layout = QtWidgets.QHBoxLayout()
        
        clear_button = QtWidgets.QPushButton("Clear Log")
        clear_button.clicked.connect(self.clear_log)
        button_layout.addWidget(clear_button)
        
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
    
    # Show message at startup
    notification_icon = QtGui.QIcon(str(tray_icon.icon_path)) if tray_icon.icon_path.exists() else QtGui.QIcon.fromTheme("audio-input-microphone")
    
    tray_icon.showMessage(
        "Whispex", 
        "Application running in system tray", 
        notification_icon, 
        3000
    )
    
    # Automatically start recognition service after app loads
    QtCore.QTimer.singleShot(1000, lambda: tray_icon.start_remote_whisper())
    
    sys.exit(app.exec_()) 
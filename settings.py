import os
import shutil
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Tuple, Union

import tomli
import tomli_w

# Import common logger
from logger import log


# Define strict setting keys
class GeneralSettings(Enum):
    LANGUAGE = "language"
    REC_KEY = "rec_key"
    INPUT_METHOD = "input_method"
    AUTO_OFF_TIME = "auto_off_time"
    NO_TYPE = "no_type"
    INPUT_DEVICE = "input_device"
    DEFAULT_DEVICE = "default_device"


class WhisperSettings(Enum):
    TEMPERATURE = "temperature"
    PROMPT = "prompt"
    SAMPLE_RATE = "sample_rate"
    RECORDING_SAMPLE_RATE = "recording_sample_rate"


class OpenAISettings(Enum):
    API_KEY = "api_key"


class DataPathSettings(Enum):
    BASE_DIR = "base_dir"
    AUDIO_DIR = "audio_dir"
    TRANSCRIPTION_DIR = "transcription_dir"


# Define settings sections
class SettingsSection(Enum):
    GENERAL = "general"
    WHISPER = "whisper"
    OPENAI = "openai"
    DATA_PATHS = "data_paths"


class SettingsManager:
    """
    Unified settings manager for whispex application.
    All settings are stored in a single TOML config file.
    """

    def __init__(self):
        """
        Initialize settings manager
        """
        self.script_dir = Path(os.path.dirname(os.path.abspath(__file__)))

        # Initialize config path
        self.config_path = self.ensure_config_path()

        # Initialize settings container
        self.settings = {}

        # Load settings
        self.load_settings()

        # Initialize data directories
        self._initialize_data_directories()

    def ensure_config_path(self):
        """
        Get path to configuration file.
        If user config doesn't exist, creates it from the default template.

        Returns:
            Path: Path to configuration file
        """
        # Path to user config
        user_config_dir = Path.home() / ".config" / "whispex"
        user_config_path = user_config_dir / "config.toml"

        # Path to default config in application directory
        default_config_path = self.script_dir / "default_config.toml"

        # Check if default config exists
        if not default_config_path.exists():
            log.error(f"Default configuration file not found at {default_config_path}")
            return None

        # If user config doesn't exist, create directory and copy default config
        if not user_config_path.exists():
            try:
                # Create config directory if it doesn't exist
                user_config_dir.mkdir(parents=True, exist_ok=True)

                # Copy default config to user location
                shutil.copy2(default_config_path, user_config_path)
                log.info(f"Created user configuration at {user_config_path}")
            except Exception as e:
                log.error(f"Failed to create user configuration: {e}")
                log.info(f"Using default configuration from {default_config_path}")
                return default_config_path

        return user_config_path

    def _initialize_data_directories(self):
        """
        Initialize and create data directories for the application.
        """
        # Base directory is ~/.whispex
        base_dir = Path.home() / ".whispex"
        audio_dir = base_dir / "audio"

        # Create directories if they don't exist
        base_dir.mkdir(exist_ok=True)
        audio_dir.mkdir(exist_ok=True)

        # Add to settings
        if SettingsSection.DATA_PATHS.value not in self.settings:
            self.settings[SettingsSection.DATA_PATHS.value] = {}

        self.settings[SettingsSection.DATA_PATHS.value][DataPathSettings.BASE_DIR.value] = str(base_dir)
        self.settings[SettingsSection.DATA_PATHS.value][DataPathSettings.AUDIO_DIR.value] = str(audio_dir)

        log.info(f"Data directories initialized: {base_dir}")

    def get_data_dirs(self) -> Tuple[Path, Path]:
        """
        Get application data directories.

        Returns:
            Tuple[Path, Path]: Tuple of (base_dir, audio_dir)
        """
        # Get paths from settings
        base_dir_str = self.get(SettingsSection.DATA_PATHS, DataPathSettings.BASE_DIR)
        audio_dir_str = self.get(SettingsSection.DATA_PATHS, DataPathSettings.AUDIO_DIR)

        # If not set in settings, use default
        if not base_dir_str:
            base_dir = Path.home() / ".whispex"
        else:
            base_dir = Path(base_dir_str)

        if not audio_dir_str:
            audio_dir = base_dir / "audio"
        else:
            audio_dir = Path(audio_dir_str)

        # Ensure directories exist
        base_dir.mkdir(exist_ok=True)
        audio_dir.mkdir(exist_ok=True)

        return base_dir, audio_dir

    def load_settings(self):
        """
        Load settings from TOML config.
        """
        if not self.config_path:
            log.error("No configuration path available")
            raise ValueError("Configuration file not found")

        try:
            with open(self.config_path, "rb") as f:
                config = tomli.load(f)

            # Handle OpenAI API key from environment if not in config
            if not config.get(SettingsSection.OPENAI.value, {}).get(
                OpenAISettings.API_KEY.value
            ):
                env_api_key = os.environ.get("OPENAI_API_KEY")
                if env_api_key:
                    if SettingsSection.OPENAI.value not in config:
                        config[SettingsSection.OPENAI.value] = {}
                    config[SettingsSection.OPENAI.value][
                        OpenAISettings.API_KEY.value
                    ] = env_api_key
                    log.info("Using OpenAI API key from environment variables")
                else:
                    log.error(
                        "OpenAI API key is not specified either in the configuration or in OPENAI_API_KEY environment variable"
                    )
                    log.error(
                        "Working with OpenAI API is not possible without a valid key"
                    )
                    log.error(
                        "Add the key to ~/.config/whispex/config.toml or set the OPENAI_API_KEY environment variable"
                    )
                    raise ValueError("OpenAI API key is not specified")

            # Required sections in the config
            required_sections = [
                section.value for section in [
                    SettingsSection.GENERAL,
                    SettingsSection.WHISPER,
                    SettingsSection.OPENAI
                ]
            ]

            # Verify the config has all required sections
            for section in required_sections:
                if section not in config:
                    log.error(f"Missing required section '{section}' in configuration")
                    raise ValueError(
                        f"Missing required section '{section}' in configuration"
                    )

            self.settings = config
            log.info(f"Loaded settings from {self.config_path}")
        except Exception as e:
            log.error(f"Error loading configuration: {e}")
            log.error("Cannot proceed without valid configuration")
            raise e

    def save_settings(self):
        """
        Save settings to TOML config file.

        Returns:
            bool: True if successful, False otherwise
        """
        if not self.config_path:
            log.error("No configuration path available")
            return False

        try:
            # Write settings to TOML file
            with open(self.config_path, "wb") as f:
                tomli_w.dump(self.settings, f)

            log.info(f"Settings saved to file: {self.config_path}")
            return True
        except Exception as e:
            log.error(f"Error saving settings: {e}")
            return False

    def get(
        self,
        section: Optional[Union[SettingsSection, str]],
        key: Union[GeneralSettings, WhisperSettings, OpenAISettings, DataPathSettings, str],
        default: Any = None,
    ) -> Any:
        """
        Get setting value with fallback to default.

        Args:
            section: Section name or enum
            key: Setting key or enum
            default: Default value if not found

        Returns:
            Setting value or default
        """
        # Convert enums to their string values
        section_str = section.value if isinstance(section, SettingsSection) else section
        key_str = (
            key.value
            if isinstance(key, (GeneralSettings, WhisperSettings, OpenAISettings, DataPathSettings))
            else key
        )

        # Handle None values for section or key
        if section_str is None or key_str is None:
            return default

        return self.settings.get(section_str, {}).get(key_str, default)

    def set(
        self,
        section: Union[SettingsSection, str],
        key: Union[GeneralSettings, WhisperSettings, OpenAISettings, DataPathSettings, str],
        value: Any,
    ) -> bool:
        """
        Set setting value and save settings to file.

        Args:
            section: Section name or enum
            key: Setting key or enum
            value: Setting value

        Returns:
            bool: True if successful, False otherwise
        """
        # Convert enums to their string values
        section_str = section.value if isinstance(section, SettingsSection) else section
        key_str = (
            key.value
            if isinstance(key, (GeneralSettings, WhisperSettings, OpenAISettings, DataPathSettings))
            else key
        )

        if section_str not in self.settings:
            self.settings[section_str] = {}

        # Store old value to check if changed
        old_value = self.settings[section_str].get(key_str)

        # Set new value
        self.settings[section_str][key_str] = value

        # Only save if value changed
        if old_value != value:
            log.info(f"Setting changed: [{section_str}] {key_str} = {value}")
            return self.save_settings()

        return True

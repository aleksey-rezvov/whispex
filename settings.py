#!/usr/bin/env python3
import os
import shutil
import logging
from pathlib import Path
import tomli
import tomli_w

# Setup logging
logger = logging.getLogger(__name__)

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
            logger.error(f"Default configuration file not found at {default_config_path}")
            return None
            
        # If user config doesn't exist, create directory and copy default config
        if not user_config_path.exists():
            try:
                # Create config directory if it doesn't exist
                user_config_dir.mkdir(parents=True, exist_ok=True)
                
                # Copy default config to user location
                shutil.copy2(default_config_path, user_config_path)
                logger.info(f"Created user configuration at {user_config_path}")
            except Exception as e:
                logger.error(f"Failed to create user configuration: {e}")
                logger.info(f"Using default configuration from {default_config_path}")
                return default_config_path
        
        return user_config_path
    
    def load_settings(self):
        """
        Load settings from TOML config.
        """
        if not self.config_path:
            logger.error("No configuration path available")
            raise ValueError("Configuration file not found")
        
        try:
            with open(self.config_path, "rb") as f:
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
                    logger.error("OpenAI API key is not specified either in the configuration or in OPENAI_API_KEY environment variable")
                    logger.error("Working with OpenAI API is not possible without a valid key")
                    logger.error("Add the key to ~/.config/whispex/config.toml or set the OPENAI_API_KEY environment variable")
                    raise ValueError("OpenAI API key is not specified")
            
            # Required sections in the config
            required_sections = ["general", "whisper", "openai"]
            
            # Verify the config has all required sections
            for section in required_sections:
                if section not in config:
                    logger.error(f"Missing required section '{section}' in configuration")
                    raise ValueError(f"Missing required section '{section}' in configuration")
            
            self.settings = config
            logger.info(f"Loaded settings from {self.config_path}")
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            logger.error("Cannot proceed without valid configuration")
            raise e
    
    def save_settings(self):
        """
        Save settings to TOML config file.
        
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.config_path:
            logger.error("No configuration path available")
            return False
            
        try:
            # Write settings to TOML file
            with open(self.config_path, "wb") as f:
                tomli_w.dump(self.settings, f)
            
            logger.info(f"Settings saved to file: {self.config_path}")
            return True
        except Exception as e:
            logger.error(f"Error saving settings: {e}")
            return False
    
    def get(self, section, key, default=None):
        """
        Get setting value with fallback to default.
        
        Args:
            section (str): Section name
            key (str): Setting key
            default: Default value if not found
            
        Returns:
            Setting value or default
        """
        return self.settings.get(section, {}).get(key, default)
    
    def set(self, section, key, value):
        """
        Set setting value and save settings to file.
        
        Args:
            section (str): Section name
            key (str): Setting key
            value: Setting value
            
        Returns:
            bool: True if successful, False otherwise
        """
        if section not in self.settings:
            self.settings[section] = {}
        
        # Store old value to check if changed
        old_value = self.settings[section].get(key)
        
        # Set new value
        self.settings[section][key] = value
        
        # Only save if value changed
        if old_value != value:
            logger.info(f"Setting changed: [{section}] {key} = {value}")
            return self.save_settings()
        
        return True 
# Whispex

A voice dictation tool that uses OpenAI's Whisper model for transcription, especially optimized for technical speech.

## Installation

```bash
# Install uv (Python package manager)
curl -fsSL https://astral.sh/uv/install.sh | bash

# Clone repository (if you haven't already)
git clone https://github.com/your-username/whispex.git
cd whispex
```

## Configuration

Whispex uses TOML format for configuration. The configuration is managed through the GUI application in the Settings dialog. The configuration file is automatically created when you first run the application.

Configuration is stored in the following locations:

1. User configuration: `~/.config/whispex/config.toml`
2. Default configuration: `default_config.toml` in the application directory

### Configuration Options

```toml
[general]
# Language code for transcription (e.g. 'ru', 'en')
language = "en"
# Key used for push-to-talk recording
rec_key = "alt_r"
# Input method: "clipboard_ctrl_v", "clipboard_ctrl_shift_v", or "direct"
input_method = "clipboard_ctrl_shift_v"

[whisper]
# Temperature parameter for model (0.0 to 1.0)
temperature = 0.2
# Prompt for Whisper model to improve transcription accuracy
prompt = "..."

[openai]
# Your OpenAI API key
api_key = "your-api-key-here"
```

## Usage

### Running the GUI Application (Recommended)

The easiest way to use Whispex is through its GUI application:

```bash
# Run from project directory using uv
uv run whispex-gui.py
```

The GUI will start in the system tray. Click the tray icon to show/hide the main window.

> **Note**: `uv run` automatically installs the required dependencies from `pyproject.toml` when launching the application, so there's no need to manually install dependencies.

### Desktop Integration

To integrate Whispex with your desktop environment:

1. Create a .desktop file:

```bash
# Create the desktop file in your applications directory
mkdir -p ~/.local/share/applications
cp Whispex.desktop ~/.local/share/applications/
```

2. Edit the desktop file to use the correct paths:

```bash
# Edit the desktop file to match your installation directory
nano ~/.local/share/applications/Whispex.desktop
```

Update the `Exec=` and `Icon=` paths to point to your installation directory. For example:

```
[Desktop Entry]
Type=Application
Name=Whispex
Comment=Voice-to-text dictation with OpenAI Whisper
Exec=/path/to/your/uv run /path/to/your/whispex/whispex-gui.py
Icon=/path/to/your/whispex/whispex.png
Terminal=false
Categories=Utility;Accessibility;
```

After this, Whispex should appear in your application menu.

### Command Line Usage

For command line usage, Whispex provides a shell script:

```bash
# Run the command line version in interactive mode
./whispex-cli.sh

# Transcribe an audio file directly and print to console
./whispex-cli.sh -f /path/to/your/audio.wav

# Transcribe an audio file and save to text file
./whispex-cli.sh -f /path/to/your/audio.wav -o transcript.txt
```

All settings are configured through the configuration file, as described in the Configuration section above.

### How to Use

1. **Interactive Mode**
   - Make sure your microphone is working
   - Run the application
   - Press and hold the configured key (default: right Alt) to start recording
   - Speak while holding the key
   - Release the key to finish recording and get the transcription

2. **File Transcription Mode**
   - Prepare an audio file in a supported format (WAV, MP3, etc.)
   - Run the application with the `-f` or `--file` parameter
   - The transcription will be printed to console or saved to the specified output file

## Troubleshooting

### Microphone Access

Ensure your system allows microphone access. You can check available audio devices in the GUI by clicking on the "Audio Devices" button.

### API Key

You need a valid OpenAI API key. You can set it in one of two ways:

1. In the Settings dialog of the GUI application
2. As an environment variable: `export OPENAI_API_KEY="your-key-here"`

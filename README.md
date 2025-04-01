# Whispex

A voice dictation tool that uses OpenAI's Whisper model for transcription, especially optimized for technical speech.

## Installation

```bash
# Install uv
curl -fsSL https://astral.sh/uv/install.sh | bash

# Install dependencies using uv
uv add numpy pynput pyperclip sounddevice soundfile openai tomli
```

## Development

### Code Linting

To maintain code quality, this project uses the `ruff` linter. First install development dependencies:

```bash
# Install development dependencies
uv add --dev ruff
```

Run linting:

```bash
# Check for linting issues
uv run --group dev ruff check . --exclude .venv

# Check formatting issues
uv run --group dev ruff format --check . --exclude .venv

# Apply format fixes
uv run --group dev ruff format . --exclude .venv
```

## Configuration

Whispex uses TOML format for configuration. The configuration is searched in the following order:

1. User configuration: `~/.config/whispex/config.toml`
2. Default configuration: `default_config.toml` in the application directory

To create your own configuration, run:

```bash
mkdir -p ~/.config/whispex
cp default_config.toml ~/.config/whispex/config.toml
```

Then edit `~/.config/whispex/config.toml` with your preferred settings.

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

```bash
python whispex.py [options]
```

Or use the GUI application:

```
python whispex-gui.py
```

### Options

- `language` - Optional language code (overrides config file)
- `--no-type` - Don't type the transcribed text
- `--temperature VALUE` - Set temperature parameter (0.0-1.0)
- `--prompt TEXT` - Custom prompt for the model
- `--prompt @FILE` - Load prompt from a file
- `--auto-off-time SECONDS` - Automatically exit after inactivity

### How to Use

1. Make sure your microphone is working
2. Run the script
3. Press and hold the configured key (default: right Alt) to start recording
4. Speak while holding the key
5. Release the key to finish recording and get the transcription

## Dependencies

- Python 3.6+
- OpenAI API key (set in config file or as environment variable OPENAI_API_KEY)
- sounddevice
- numpy
- pynput
- pyperclip
- tomli
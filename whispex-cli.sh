#!/usr/bin/env bash

script_path="$(realpath "$0")"
script_dir="$(dirname "$script_path")"

# Disable Python buffering
export PYTHONUNBUFFERED=1

show_help() {
    echo "Usage: $0 [options] [audio_file]"
    echo ""
    echo "Whispex is configured using the configuration file at:"
    echo "  ~/.config/whispex/config.toml (user config)"
    echo "  ./default_config.toml (default application config)"
    echo ""
    echo "Options:"
    echo "  --help, -h              Show this help message"
    echo "  --file, -f AUDIO_FILE   Transcribe the specified audio file"
    echo "  --output, -o FILE       Save transcription to file (default: print to console)"
    echo ""
    echo "Examples:"
    echo "  $0                      Run in interactive mode with push-to-talk"
    echo "  $0 -f recording.wav     Transcribe 'recording.wav' and print to console"
    echo "  $0 -f recording.wav -o transcript.txt    Transcribe to a file"
    echo ""
    echo "For configuration options, edit the TOML configuration file."
    echo ""
}

if [ "$1" == "--help" ] || [ "$1" == "-h" ]; then
    show_help
    exit 0
fi

echo "Starting whispex.py..."

# Clear VIRTUAL_ENV to avoid warnings about environment mismatch
# unset VIRTUAL_ENV

SCRIPT_PATH="${script_dir}/whispex.py"
# Pass all command line arguments to the Python script
exec uv run "${SCRIPT_PATH}" "$@"

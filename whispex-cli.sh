#!/usr/bin/env bash

script_path="$(realpath "$0")"
script_dir="$(dirname "$script_path")"
cd $script_dir

# Disable Python buffering
export PYTHONUNBUFFERED=1

# Help function
show_help() {
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  --help                  Show this help"
    echo "  --language LANG         Language code for transcription (e.g. 'ru', 'en')"
    echo "  --temperature VALUE     Temperature parameter (0.0-1.0)"
    echo "  --no-type               Don't type transcribed text"
    echo "  --auto-off-time SECONDS Automatically exit after inactivity"
    echo ""
}

# Check if help is requested
if [ "$1" == "--help" ] || [ "$1" == "-h" ]; then
    show_help
    exit 0
fi

# Direct execution of dictation.py in blocking mode
echo "Starting dictation.py..."
exec uv run @uv dictation.py "$@"
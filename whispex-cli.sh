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
    echo "  --gui                   Start graphical interface (whispex-tray.py)"
    echo "  --nogui                 Start without graphical interface (dictation.py)"
    echo "  --help                  Show this help"
    echo ""
}

# Signal handling function for clean termination
cleanup() {
    echo "Termination signal received, stopping process..."
    # Send SIGTERM to our process
    kill -TERM $PYTHON_PID 2>/dev/null
    
    # Give some time for clean termination
    sleep 0.5
    
    # If the process didn't terminate, force kill it
    if kill -0 $PYTHON_PID 2>/dev/null; then
        echo "Process didn't terminate, force killing..."
        kill -KILL $PYTHON_PID 2>/dev/null
    fi
    
    exit 0
}

# Check arguments
RUN_GUI=false

# Check first argument
if [ $# -eq 0 ]; then
    # If no arguments, run without GUI by default
    RUN_GUI=false
elif [ "$1" == "--help" ]; then
    show_help
    exit 0
elif [ "$1" == "--gui" ]; then
    RUN_GUI=true
    shift  # Remove first argument
elif [ "$1" == "--nogui" ]; then
    RUN_GUI=false
    shift  # Remove first argument
else
    # If first argument is not --gui flag, run without GUI
    RUN_GUI=false
fi

# Catch termination signals for clean cleanup
trap cleanup SIGINT SIGTERM

if [ "$RUN_GUI" = true ]; then
    echo "Starting graphical interface..."
    # Use uv run instead of direct Python execution
    stdbuf -o0 -e0 uv run @uv whispex-tray.py &
    PYTHON_PID=$!
else
    echo "Starting dictation.py..."
    # Use uv run instead of direct Python execution
    stdbuf -o0 -e0 uv run @uv dictation.py &
    PYTHON_PID=$!
fi

# Wait for Python process to complete
wait $PYTHON_PID
EXIT_CODE=$?

# Exit with the same code as Python
exit $EXIT_CODE
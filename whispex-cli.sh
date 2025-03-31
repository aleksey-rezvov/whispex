#!/usr/bin/env bash

script_path="$(realpath "$0")"
script_dir="$(dirname "$script_path")"
cd $script_dir

# Отключаем буферизацию Python
export PYTHONUNBUFFERED=1

# Функция для показа справки
show_help() {
    echo "Использование: $0 [опции]"
    echo ""
    echo "Опции:"
    echo "  --gui                   Запустить графический интерфейс (whispex-tray.py)"
    echo "  --nogui                 Запустить без графического интерфейса (dictation.py)"
    echo "  --help                  Показать эту справку"
    echo ""
}

# Функция для обработки сигналов завершения
cleanup() {
    echo "Получен сигнал завершения, останавливаем процесс..."
    # Отправляем SIGTERM нашему процессу
    kill -TERM $PYTHON_PID 2>/dev/null
    
    # Даем немного времени на корректное завершение
    sleep 0.5
    
    # Если процесс не завершился, принудительно его завершаем
    if kill -0 $PYTHON_PID 2>/dev/null; then
        echo "Процесс не завершился, принудительная остановка..."
        kill -KILL $PYTHON_PID 2>/dev/null
    fi
    
    exit 0
}

# Проверяем аргументы
RUN_GUI=false

# Проверяем первый аргумент
if [ $# -eq 0 ]; then
    # Если нет аргументов, по умолчанию запускаем без GUI
    RUN_GUI=false
elif [ "$1" == "--help" ]; then
    show_help
    exit 0
elif [ "$1" == "--gui" ]; then
    RUN_GUI=true
    shift  # Удаляем первый аргумент
elif [ "$1" == "--nogui" ]; then
    RUN_GUI=false
    shift  # Удаляем первый аргумент
else
    # Если первый аргумент не является флагом --gui, запускаем без GUI
    RUN_GUI=false
fi

# Перехватываем сигналы завершения для корректной очистки
trap cleanup SIGINT SIGTERM

if [ "$RUN_GUI" = true ]; then
    echo "Запускаем графический интерфейс..."
    # Используем uv run вместо прямого запуска Python
    stdbuf -o0 -e0 uv run @uv whispex-tray.py &
    PYTHON_PID=$!
else
    echo "Запускаем dictation.py..."
    # Используем uv run вместо прямого запуска Python
    stdbuf -o0 -e0 uv run @uv dictation.py &
    PYTHON_PID=$!
fi

# Ждем завершения Python процесса
wait $PYTHON_PID
EXIT_CODE=$?

# Выходим с тем же кодом, что и Python
exit $EXIT_CODE
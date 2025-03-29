#!/usr/bin/env bash

script_path="$(realpath "$0")"
script_dir="$(dirname "$script_path")"
cd $script_dir
export OPENAI_API_KEY=$(cat ~/.config/openai.token)
# Отключаем буферизацию Python
export PYTHONUNBUFFERED=1

# Запускаем Python скрипт и сохраняем его PID
# Используем stdbuf для отключения буферизации stdout и stderr
stdbuf -o0 -e0 venv/bin/python3 dictation.py remote "$@" &
PYTHON_PID=$!

# Функция для обработки сигналов завершения
cleanup() {
    echo "Получен сигнал завершения, останавливаем Python процесс..."
    # Отправляем SIGTERM нашему Python процессу
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

# Перехватываем сигналы завершения для корректной очистки
trap cleanup SIGINT SIGTERM

# Ждем завершения Python процесса
wait $PYTHON_PID

# Выходим с тем же кодом, что и Python
exit $?
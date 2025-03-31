#!/usr/bin/env bash

script_path="$(realpath "$0")"
script_dir="$(dirname "$script_path")"
cd $script_dir

# Пути к виртуальному окружению
VENV_DIR="$script_dir/venv"
VENV_PYTHON="$VENV_DIR/bin/python3"
VENV_PIP="$VENV_DIR/bin/pip"

# Проверяем наличие виртуального окружения
if [ ! -f "$VENV_PYTHON" ]; then
    echo "Ошибка: Виртуальное окружение не найдено в $VENV_DIR"
    echo "Установите виртуальное окружение и зависимости перед запуском скрипта."
    exit 1
fi

export OPENAI_API_KEY=$(cat ~/.config/openai.token)
# Отключаем буферизацию Python
export PYTHONUNBUFFERED=1

# Функция для показа справки
show_help() {
    echo "Использование: $0 [опции]"
    echo ""
    echo "Опции:"
    echo "  --gui                   Запустить графический интерфейс (whisper_tray.py)"
    echo "  --nogui                 Запустить без графического интерфейса (dictation.py)"
    echo "  --help                  Показать эту справку"
    echo ""
    echo "Параметры для dictation.py (используются только при --nogui):"
    echo "  language                Код языка для транскрипции (напр. 'ru', 'en')"
    echo "  --temperature=N         Параметр температуры для модели Whisper (по умолчанию: 0.2)"
    echo "  --prompt=@file          Пользовательский промпт для модели Whisper"
    echo "  --no-type-using-clipboard Не использовать буфер обмена для ввода"
    echo "  --auto-off-time=N       Автоматически выключаться после N секунд бездействия"
    echo ""
    echo "Примеры:"
    echo "  $0 --gui                # Запустить с графическим интерфейсом"
    echo "  $0 ru                   # Запустить без GUI с языком русский"
    echo "  $0 ru --temperature=0.3 # Запустить без GUI с языком русский и температурой 0.3"
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

# Проверка на наличие PyQt5 в виртуальном окружении при запуске GUI
check_pyqt5() {
    if $VENV_PYTHON -c "import PyQt5" 2>/dev/null; then
        return 0
    else
        echo "PyQt5 не установлен в виртуальном окружении. Устанавливаем..."
        $VENV_PIP install PyQt5
        return $?
    fi
}

# Проверяем аргументы
RUN_GUI=false

# Проверяем первый аргумент
if [ $# -eq 0 ]; then
    # Если нет аргументов, по умолчанию запускаем без GUI с русским языком
    ARGS=("ru")
elif [ "$1" == "--help" ]; then
    show_help
    exit 0
elif [ "$1" == "--gui" ]; then
    RUN_GUI=true
    shift  # Удаляем первый аргумент, чтобы остальные передать приложению
elif [ "$1" == "--nogui" ]; then
    RUN_GUI=false
    shift  # Удаляем первый аргумент, чтобы остальные передать приложению
else
    # Если первый аргумент не является флагом --gui, запускаем без GUI
    RUN_GUI=false
fi

# Перехватываем сигналы завершения для корректной очистки
trap cleanup SIGINT SIGTERM

if [ "$RUN_GUI" = true ]; then
    echo "Запускаем графический интерфейс..."
    # Проверяем наличие PyQt5 перед запуском GUI
    if ! check_pyqt5; then
        echo "Ошибка: Не удалось установить PyQt5. GUI не может быть запущен."
        exit 1
    fi
    # Используем stdbuf для отключения буферизации stdout и stderr
    stdbuf -o0 -e0 $VENV_PYTHON whisper_tray.py "$@" &
    PYTHON_PID=$!
else
    echo "Запускаем dictation.py с параметрами: $@"
    # Используем stdbuf для отключения буферизации stdout и stderr
    stdbuf -o0 -e0 $VENV_PYTHON dictation.py "$@" &
    PYTHON_PID=$!
fi

# Ждем завершения Python процесса
wait $PYTHON_PID
EXIT_CODE=$?

# Выходим с тем же кодом, что и Python
exit $EXIT_CODE
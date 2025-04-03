import datetime
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

import tomli
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import QAbstractTableModel, QModelIndex, QSize, Qt

# Import logger
from logger import log
# Import settings manager
from settings import (DataPathSettings, GeneralSettings, OpenAISettings,
                      SettingsManager, SettingsSection, WhisperSettings)

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    # Создаем заглушку для класса FileSystemEventHandler
    class FileSystemEventHandler:
        pass
    log.warning("watchdog module not found, file monitoring will not be available")

# Constants
UV_RUN_COMMAND = ["uv", "run"]
LOG_WINDOW_SIZE = (900, 600)
LOGS_WINDOW_SIZE = (800, 500)
SETTINGS_DIALOG_SIZE = (700, 500)
TRANSCRIPT_WINDOW_SIZE = (1000, 600)

# Log entry types
LOG_TYPE_SYSTEM = "system"
LOG_TYPE_TRANSCRIPTION = "transcription"
LOG_TYPE_ERROR = "error"
LOG_TYPE_INFO = "info"
LOG_TYPE_DEBUG = "debug"


class LogEntry:
    """Представляет запись в логе для отображения в таблице"""

    def __init__(self,
                 text: str,
                 entry_type: str = LOG_TYPE_SYSTEM,
                 timestamp: Optional[float] = None,
                 audio_file: Optional[str] = None):
        self.text = text
        self.entry_type = entry_type
        self.timestamp = timestamp or time.time()
        self.audio_file = audio_file

    @property
    def time_str(self) -> str:
        """Возвращает отформатированное время"""
        return datetime.datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")

    @property
    def has_audio(self) -> bool:
        """Имеется ли аудиофайл для записи"""
        return self.audio_file is not None and os.path.exists(self.audio_file)


class TranscriptionWatcher(QtCore.QObject):
    """Наблюдатель за новыми транскрипциями в директории"""

    # Сигнал о новой транскрипции
    new_transcription = QtCore.pyqtSignal(dict)

    def __init__(self, base_dir: Path, parent=None):
        super().__init__(parent)
        self.base_dir = base_dir
        self.running = False
        self.observer = None
        self.event_handler = None

        log.info(f"Инициализирован наблюдатель за транскрипциями в {base_dir}")

    def start(self):
        """Запуск наблюдения за директорией"""
        if not WATCHDOG_AVAILABLE:
            log.error("Модуль watchdog не установлен. Невозможно наблюдать за директорией.")
            return False

        if self.running:
            log.warning("Наблюдатель уже запущен")
            return True

        try:
            if not self.base_dir.exists():
                log.warning(f"Директория {self.base_dir} не существует, создаём...")
                self.base_dir.mkdir(parents=True, exist_ok=True)

            # Создаем обработчик событий
            self.event_handler = TranscriptionEventHandler(self)

            # Создаем и запускаем наблюдателя
            self.observer = Observer()
            self.observer.schedule(self.event_handler, str(self.base_dir), recursive=False)
            self.observer.start()

            self.running = True
            log.info(f"Запущено наблюдение за транскрипциями в директории {self.base_dir}")

            # Загружаем существующие транскрипции
            self.load_existing_transcriptions()

            return True
        except Exception as e:
            log.error(f"Ошибка при запуске наблюдения за транскрипциями: {e}")
            return False

    def stop(self):
        """Остановка наблюдения"""
        if not self.running or not self.observer:
            return

        try:
            self.observer.stop()
            self.observer.join()
            self.running = False
            log.info("Наблюдение за транскрипциями остановлено")
        except Exception as e:
            log.error(f"Ошибка при остановке наблюдения за транскрипциями: {e}")

    def load_existing_transcriptions(self):
        """Загрузка существующих транскрипций из директории"""
        if not self.base_dir.exists():
            log.warning(f"Директория {self.base_dir} не существует")
            return

        log.info(f"Загрузка существующих транскрипций из {self.base_dir}")

        # Получаем список файлов и сортируем по дате создания (от новых к старым)
        json_files = sorted(
            self.base_dir.glob("transcription_*.json"),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

        for file_path in json_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.new_transcription.emit(data)
                    log.debug(f"Загружена транскрипция: {file_path.name}")
            except Exception as e:
                log.error(f"Ошибка при загрузке транскрипции {file_path}: {e}")


class TranscriptionEventHandler(FileSystemEventHandler):
    """Обработчик событий файловой системы для транскрипций"""

    def __init__(self, watcher):
        super().__init__()
        self.watcher = watcher

    def on_created(self, event):
        """Обработка события создания файла"""
        if event.is_directory:
            return

        if event.src_path.endswith('.json') and os.path.basename(event.src_path).startswith('transcription_'):
            self._process_transcription_file(event.src_path)

    def on_modified(self, event):
        """Обработка события изменения файла"""
        if event.is_directory:
            return

        if event.src_path.endswith('.json') and os.path.basename(event.src_path).startswith('transcription_'):
            self._process_transcription_file(event.src_path)

    def _process_transcription_file(self, file_path):
        """Обработка файла транскрипции"""
        try:
            # Небольшая задержка для полного завершения записи файла
            time.sleep(0.1)

            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Отправляем данные через сигнал
            self.watcher.new_transcription.emit(data)
            log.debug(f"Обнаружена новая транскрипция: {os.path.basename(file_path)}")
        except Exception as e:
            log.error(f"Ошибка при обработке транскрипции {file_path}: {e}")


class TranscriptionTableModel(QAbstractTableModel):
    """Модель данных для таблицы транскрипций"""

    # Определение колонок
    COL_TIME = 0
    COL_TEXT = 1
    COL_LANG = 2
    COL_AUDIO = 3
    COL_PLAY = 4
    COL_COPY = 5
    COL_RECOG = 6

    # Заголовки колонок
    HEADERS = ["Время", "Текст", "Язык", "Аудиофайл", "🔊", "📋", "🔄"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.transcriptions = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self.transcriptions)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self.transcriptions):
            return None

        transcription = self.transcriptions[index.row()]

        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == self.COL_TIME:
                # Форматируем время в более читаемый вид
                timestamp = transcription.get('timestamp', '')
                if timestamp:
                    try:
                        dt = datetime.datetime.strptime(timestamp, "%Y%m%d_%H%M%S")
                        return dt.strftime("%d-%m-%Y %H:%M:%S")
                    except ValueError:
                        return timestamp
                return timestamp
            elif index.column() == self.COL_TEXT:
                return transcription.get('text', '')
            elif index.column() == self.COL_LANG:
                return transcription.get('whisper_parameters', {}).get('language', '')
            elif index.column() == self.COL_AUDIO:
                return transcription.get('audio_file', '')
            # Кнопки не отображают текст
            elif index.column() in [self.COL_PLAY, self.COL_COPY, self.COL_RECOG]:
                return ""

        elif role == Qt.ItemDataRole.ToolTipRole:
            if index.column() == self.COL_AUDIO:
                audio_file = transcription.get('audio_file', '')
                return f"Аудиофайл: {audio_file}"
            elif index.column() == self.COL_TEXT:
                return transcription.get('text', '')
            elif index.column() == self.COL_PLAY:
                return "Воспроизвести аудио"
            elif index.column() == self.COL_COPY:
                return "Копировать текст"
            elif index.column() == self.COL_RECOG:
                return "Перераспознать"

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if index.column() == self.COL_TIME:
                return int(Qt.AlignmentFlag.AlignCenter)
            elif index.column() == self.COL_LANG:
                return int(Qt.AlignmentFlag.AlignCenter)

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def addTranscription(self, data: dict) -> bool:
        """Добавляет транскрипцию в таблицу"""
        # Проверяем на дубликаты
        timestamp = data.get('timestamp', '')
        for existing in self.transcriptions:
            if existing.get('timestamp') == timestamp:
                return False

        # Добавляем новую запись в начало списка
        self.beginInsertRows(QModelIndex(), 0, 0)
        self.transcriptions.insert(0, data)
        self.endInsertRows()
        return True

    def clear(self) -> None:
        """Очищает все транскрипции"""
        self.beginResetModel()
        self.transcriptions.clear()
        self.endResetModel()

    def getTranscription(self, row: int) -> Optional[dict]:
        """Возвращает транскрипцию по индексу строки"""
        if 0 <= row < len(self.transcriptions):
            return self.transcriptions[row]
        return None


class TranscriptionButtonDelegate(QtWidgets.QStyledItemDelegate):
    """Делегат для отображения кнопок в таблице транскрипций"""

    # Сигналы для обработки нажатий
    playClicked = QtCore.pyqtSignal(int)
    copyClicked = QtCore.pyqtSignal(int)
    recognizeClicked = QtCore.pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

    def paint(self, painter: QtGui.QPainter, option: QtWidgets.QStyleOptionViewItem, index: QModelIndex) -> None:
        """Отрисовка кнопок в ячейках"""
        if not index.isValid():
            return super().paint(painter, option, index)

        model = index.model()
        if not isinstance(model, TranscriptionTableModel) or index.row() >= model.rowCount():
            return super().paint(painter, option, index)

        transcription = model.getTranscription(index.row())
        if not transcription:
            return super().paint(painter, option, index)

        # Центрируем содержимое ячейки
        option.displayAlignment = Qt.AlignmentFlag.AlignCenter

        # Готовим кисть и перо
        painter.save()

        # Определяем цвет и состояние кнопки
        enabled = False
        text = ""

        if index.column() == TranscriptionTableModel.COL_PLAY:
            # Кнопка воспроизведения
            text = "🔊"
            audio_file = transcription.get('audio_file', '')
            enabled = audio_file != ''
        elif index.column() == TranscriptionTableModel.COL_COPY:
            # Кнопка копирования
            text = "📋"
            enabled = transcription.get('text', '') != ''
        elif index.column() == TranscriptionTableModel.COL_RECOG:
            # Кнопка перераспознавания
            text = "🔄"
            audio_file = transcription.get('audio_file', '')
            enabled = audio_file != ''
        else:
            # Для других колонок используем стандартную отрисовку
            painter.restore()
            return super().paint(painter, option, index)

        # Фон кнопки
        is_selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        is_hover = bool(option.state & QtWidgets.QStyle.State_MouseOver)

        if is_selected:
            # Если строка выбрана, используем цвет выделения
            painter.setBrush(option.palette.highlight())
        else:
            # Иначе используем обычный фон или чуть темнее для наведения
            if is_hover and enabled:
                painter.setBrush(option.palette.mid())
            else:
                painter.setBrush(option.palette.button())

        # Настраиваем цвет текста
        if enabled:
            painter.setPen(option.palette.buttonText().color())
        else:
            painter.setPen(option.palette.mid().color())

        # Рисуем фон кнопки (скругленный прямоугольник)
        button_rect = option.rect.adjusted(4, 4, -4, -4)
        painter.drawRoundedRect(button_rect, 5, 5)

        # Рисуем текст кнопки
        painter.drawText(option.rect, int(Qt.AlignmentFlag.AlignCenter), text)

        painter.restore()

    def editorEvent(self, event: QtCore.QEvent, model: QAbstractTableModel,
                   option: QtWidgets.QStyleOptionViewItem, index: QModelIndex) -> bool:
        """Обработка событий нажатия на кнопки"""
        if not isinstance(model, TranscriptionTableModel) or not index.isValid():
            return super().editorEvent(event, model, option, index)

        if (event.type() == QtCore.QEvent.Type.MouseButtonRelease and
            isinstance(event, QtGui.QMouseEvent) and
            event.button() == Qt.MouseButton.LeftButton):

            transcription = model.getTranscription(index.row())
            if not transcription:
                return False

            if index.column() == TranscriptionTableModel.COL_PLAY:
                audio_file = transcription.get('audio_file', '')
                if audio_file:
                    self.playClicked.emit(index.row())
                    return True

            elif index.column() == TranscriptionTableModel.COL_COPY:
                text = transcription.get('text', '')
                if text:
                    self.copyClicked.emit(index.row())
                    return True

            elif index.column() == TranscriptionTableModel.COL_RECOG:
                audio_file = transcription.get('audio_file', '')
                if audio_file:
                    self.recognizeClicked.emit(index.row())
                    return True

        return super().editorEvent(event, model, option, index)

    def sizeHint(self, option: QtWidgets.QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """Определяет размер ячейки"""
        size = super().sizeHint(option, index)

        # Увеличиваем высоту ячеек для удобства нажатия на кнопки
        if index.column() in [TranscriptionTableModel.COL_PLAY,
                             TranscriptionTableModel.COL_COPY,
                             TranscriptionTableModel.COL_RECOG]:
            size.setHeight(max(size.height(), 30))
            size.setWidth(max(size.width(), 40))

        return size


class TextViewDialog(QtWidgets.QDialog):
    """Диалоговое окно для просмотра и копирования текста"""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Просмотр текста")
        self.resize(600, 400)

        # Создаем текстовое поле
        self.text_edit = QtWidgets.QTextEdit(self)
        self.text_edit.setText(text)

        # Кнопки
        copy_button = QtWidgets.QPushButton("Копировать выделенное")
        copy_button.clicked.connect(self.copy_selected)

        copy_all_button = QtWidgets.QPushButton("Копировать всё")
        copy_all_button.clicked.connect(self.copy_all)

        close_button = QtWidgets.QPushButton("Закрыть")
        close_button.clicked.connect(self.accept)

        # Создаем компоновку для кнопок
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addWidget(copy_button)
        button_layout.addWidget(copy_all_button)
        button_layout.addStretch()
        button_layout.addWidget(close_button)

        # Создаем общую компоновку
        main_layout = QtWidgets.QVBoxLayout()
        main_layout.addWidget(self.text_edit)
        main_layout.addLayout(button_layout)

        self.setLayout(main_layout)

    def copy_selected(self):
        """Копирует выделенный текст в буфер обмена"""
        selected_text = self.text_edit.textCursor().selectedText()
        if selected_text:
            clipboard = QtWidgets.QApplication.clipboard()
            if clipboard:
                clipboard.setText(selected_text)
                log.info(f"Скопирован выделенный текст: '{selected_text[:30]}...'")

    def copy_all(self):
        """Копирует весь текст в буфер обмена"""
        text = self.text_edit.toPlainText()
        if text:
            clipboard = QtWidgets.QApplication.clipboard()
            if clipboard:
                clipboard.setText(text)
                log.info(f"Скопирован весь текст: '{text[:30]}...'")


class LogViewWindow(QtWidgets.QDialog):
    """Окно для отображения логов программы"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Whispex - Logs")
        self.resize(*LOGS_WINDOW_SIZE)

        # Установка значка
        script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        icon_path = script_dir / "whispex.png"

        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        # Создаем текстовое поле для логов
        self.log_text = QtWidgets.QTextEdit(self)
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QtGui.QFont("Courier New", 10))

        # Кнопка очистки логов
        clear_button = QtWidgets.QPushButton("Очистить логи")
        clear_button.clicked.connect(self.clear_logs)

        # Создаем компоновку
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.log_text)
        layout.addWidget(clear_button)
        self.setLayout(layout)

    def append_text(self, text: str, entry_type: str = LOG_TYPE_INFO):
        """Добавляет сообщение в лог"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")

        # Форматирование в зависимости от типа лога
        if entry_type == LOG_TYPE_ERROR:
            formatted_text = f'<span style="color:red">[{timestamp}] {text}</span>'
        elif entry_type == LOG_TYPE_SYSTEM:
            formatted_text = f'<span style="color:blue">[{timestamp}] {text}</span>'
        elif entry_type == LOG_TYPE_DEBUG:
            formatted_text = f'<span style="color:gray">[{timestamp}] {text}</span>'
        else:
            formatted_text = f'[{timestamp}] {text}'

        # Добавляем текст и прокручиваем вниз
        self.log_text.append(formatted_text)
        scrollbar = self.log_text.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())

    def clear_logs(self):
        """Очищает все логи"""
        self.log_text.clear()


class LogHandler(logging.Handler):
    def __init__(self, log_window):
        super().__init__()
        self.log_window = log_window
        self.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))

    def emit(self, record):
        msg = self.format(record)

        # Определяем тип сообщения на основе уровня логирования
        if record.levelno >= logging.ERROR:
            entry_type = LOG_TYPE_ERROR
        elif record.levelno >= logging.WARNING:
            entry_type = LOG_TYPE_INFO
        elif record.levelno >= logging.DEBUG:
            entry_type = LOG_TYPE_DEBUG
        else:
            entry_type = LOG_TYPE_SYSTEM

        # Safely add log through append_text method which handles thread safety
        self.log_window.append_text(msg, entry_type=entry_type)


class LogWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Whispex Voice Recognition")
        self.resize(*LOG_WINDOW_SIZE)

        # Set icon for log window
        script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        icon_path = script_dir / "whispex.png"

        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        # Initialize tray_icon reference
        self.tray_icon = None

        # Окно для отображения логов
        self.log_view_window = LogViewWindow(self)

        # Получение директорий из настроек
        self.settings_manager = SettingsManager()
        base_dir, _ = self.settings_manager.get_data_dirs()

        # Создаем аудио плеер
        self.audio_player = AudioPlayer(self)

        # Создаем модель данных для таблицы транскрипций
        self.transcription_model = TranscriptionTableModel(self)

        # Создаем таблицу для отображения транскрипций
        self.transcription_table = QtWidgets.QTableView(self)
        self.transcription_table.setModel(self.transcription_model)

        # Настраиваем таблицу
        self.transcription_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.transcription_table.setAlternatingRowColors(True)
        vertical_header = self.transcription_table.verticalHeader()
        if vertical_header:
            vertical_header.setVisible(False)
        self.transcription_table.setShowGrid(True)

        # Настраиваем ширину колонок
        self.transcription_table.setColumnWidth(TranscriptionTableModel.COL_TIME, 150)  # Время
        self.transcription_table.setColumnWidth(TranscriptionTableModel.COL_LANG, 80)   # Язык
        self.transcription_table.setColumnWidth(TranscriptionTableModel.COL_AUDIO, 200) # Аудиофайл
        self.transcription_table.setColumnWidth(TranscriptionTableModel.COL_PLAY, 40)   # Кнопка воспроизведения
        self.transcription_table.setColumnWidth(TranscriptionTableModel.COL_COPY, 40)   # Кнопка копирования
        self.transcription_table.setColumnWidth(TranscriptionTableModel.COL_RECOG, 40)  # Кнопка перераспознавания

        # Растягиваем колонку с текстом
        header = self.transcription_table.horizontalHeader()
        if header:
            header.setSectionResizeMode(
                TranscriptionTableModel.COL_TEXT,
                QtWidgets.QHeaderView.ResizeMode.Stretch
            )

        # Создаем и устанавливаем делегат для кнопок
        self.button_delegate = TranscriptionButtonDelegate(self)
        self.transcription_table.setItemDelegateForColumn(TranscriptionTableModel.COL_PLAY, self.button_delegate)
        self.transcription_table.setItemDelegateForColumn(TranscriptionTableModel.COL_COPY, self.button_delegate)
        self.transcription_table.setItemDelegateForColumn(TranscriptionTableModel.COL_RECOG, self.button_delegate)

        # Подключаем сигналы делегата
        self.button_delegate.playClicked.connect(self._on_play_clicked)
        self.button_delegate.copyClicked.connect(self._on_copy_clicked)
        self.button_delegate.recognizeClicked.connect(self._on_recognize_clicked)

        # Включаем контекстное меню для таблицы
        self.transcription_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.transcription_table.customContextMenuRequested.connect(self._show_context_menu)

        # Подключаем двойной клик к действию воспроизведения
        self.transcription_table.doubleClicked.connect(self._on_table_double_clicked)

        # Создаем наблюдателя за транскрипциями
        self.watcher = TranscriptionWatcher(base_dir, self)
        self.watcher.new_transcription.connect(self._on_new_transcription)

        # Create status bar
        status_layout = QtWidgets.QHBoxLayout()

        # Service status label
        self.status_label = QtWidgets.QLabel("Service: Stopped")
        status_layout.addWidget(self.status_label)

        # API status
        self.api_status = QtWidgets.QLabel("API: Unknown")
        self.api_status.setStyleSheet("color: gray;")
        status_layout.addWidget(self.api_status)

        # Add spacer to push everything to the left
        status_layout.addStretch()

        # Create buttons
        button_layout = QtWidgets.QHBoxLayout()

        # Кнопки для транскрипций
        clear_button = QtWidgets.QPushButton("Очистить")
        clear_button.setToolTip("Очистить таблицу транскрипций")
        clear_button.clicked.connect(self._clear_transcriptions)
        button_layout.addWidget(clear_button)

        refresh_button = QtWidgets.QPushButton("Обновить")
        refresh_button.setToolTip("Перезагрузить транскрипции из файлов")
        refresh_button.clicked.connect(self._reload_transcriptions)
        button_layout.addWidget(refresh_button)

        # Кнопка показа/скрытия логов
        logs_button = QtWidgets.QPushButton("Logs")
        logs_button.setToolTip("Показать/скрыть окно логов")
        logs_button.clicked.connect(self.toggle_logs)
        button_layout.addWidget(logs_button)

        # Add audio devices button
        audio_devices_button = QtWidgets.QPushButton("Audio Devices")
        audio_devices_button.clicked.connect(self.show_audio_devices)
        button_layout.addWidget(audio_devices_button)

        # Add settings button
        settings_button = QtWidgets.QPushButton("Settings")
        settings_button.clicked.connect(self.show_settings)
        button_layout.addWidget(settings_button)

        # Add control buttons
        self.start_button = QtWidgets.QPushButton("Start")
        self.start_button.clicked.connect(self.start_service)
        button_layout.addWidget(self.start_button)

        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_service)
        self.stop_button.setEnabled(False)
        button_layout.addWidget(self.stop_button)

        # Create layout
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.transcription_table)
        layout.addLayout(status_layout)
        layout.addLayout(button_layout)
        self.setLayout(layout)

        # Добавляем информационное сообщение
        log.info("Главное окно приложения инициализировано")

        # Запускаем наблюдателя за транскрипциями
        self.watcher.start()

    def _on_play_clicked(self, row):
        """Обработка нажатия на кнопку воспроизведения"""
        transcription = self.transcription_model.getTranscription(row)
        if transcription:
            audio_file = transcription.get('audio_file', '')
            if audio_file:
                base_dir, _ = self.settings_manager.get_data_dirs()
                full_path = base_dir / audio_file
                if full_path.exists():
                    log.info(f"Воспроизведение аудио: {full_path}")
                    self.audio_player.play(str(full_path))
                else:
                    log.error(f"Аудиофайл не найден: {full_path}")

    def _on_copy_clicked(self, row):
        """Обработка нажатия на кнопку копирования"""
        transcription = self.transcription_model.getTranscription(row)
        if transcription:
            text = transcription.get('text', '')
            if text:
                clipboard = QtWidgets.QApplication.clipboard()
                if clipboard:
                    clipboard.setText(text)
                    log.info(f"Текст скопирован в буфер обмена: '{text[:30]}...'")

    def _on_recognize_clicked(self, row):
        """Обработка нажатия на кнопку перераспознавания"""
        transcription = self.transcription_model.getTranscription(row)
        if transcription:
            audio_file = transcription.get('audio_file', '')
            if audio_file:
                # TODO: Реализовать перераспознавание
                log.info(f"Перераспознавание аудио: {audio_file} (пока не реализовано)")

    def _on_table_double_clicked(self, index: QModelIndex):
        """Обработка двойного клика по таблице"""
        row = index.row()
        transcription = self.transcription_model.getTranscription(row)

        if transcription:
            if index.column() == TranscriptionTableModel.COL_AUDIO:
                # Воспроизводим аудио при двойном клике на аудиофайл
                audio_file = transcription.get('audio_file', '')
                if audio_file:
                    base_dir, _ = self.settings_manager.get_data_dirs()
                    full_path = base_dir / audio_file
                    if full_path.exists():
                        log.info(f"Воспроизведение аудио: {full_path}")
                        self.audio_player.play(str(full_path))
                    else:
                        log.error(f"Аудиофайл не найден: {full_path}")
            elif index.column() == TranscriptionTableModel.COL_TEXT:
                # Показываем диалог для просмотра и частичного копирования текста
                text = transcription.get('text', '')
                if text:
                    dialog = TextViewDialog(text, self)
                    dialog.exec_()

    def _show_context_menu(self, position):
        """Показывает контекстное меню для таблицы"""
        index = self.transcription_table.indexAt(position)
        if not index.isValid():
            return

        row = index.row()
        transcription = self.transcription_model.getTranscription(row)
        if not transcription:
            return

        menu = QtWidgets.QMenu(self)

        # Пункт "Копировать текст"
        copy_action = menu.addAction("Копировать текст")

        # Пункт "Воспроизвести аудио"
        play_action = menu.addAction("Воспроизвести аудио")

        # Пункт "Открыть JSON"
        open_json_action = menu.addAction("Открыть JSON файл")

        # Пункт "Перераспознать"
        recognize_action = menu.addAction("Перераспознать")

        # Показываем меню
        viewport = self.transcription_table.viewport()
        if viewport:
            action = menu.exec_(viewport.mapToGlobal(position))
        else:
            action = menu.exec_(QtGui.QCursor.pos())

        # Обрабатываем выбранное действие
        if action == copy_action:
            text = transcription.get('text', '')
            if text:
                clipboard = QtWidgets.QApplication.clipboard()
                if clipboard:
                    clipboard.setText(text)
                    log.info(f"Текст скопирован в буфер обмена: '{text[:30]}...'")

        elif action == play_action:
            audio_file = transcription.get('audio_file', '')
            if audio_file:
                base_dir, _ = self.settings_manager.get_data_dirs()
                full_path = base_dir / audio_file
                if full_path.exists():
                    log.info(f"Воспроизведение аудио: {full_path}")
                    self.audio_player.play(str(full_path))
                else:
                    log.error(f"Аудиофайл не найден: {full_path}")

        elif action == open_json_action:
            timestamp = transcription.get('timestamp', '')
            if timestamp:
                base_dir, _ = self.settings_manager.get_data_dirs()
                json_file = base_dir / f"transcription_{timestamp}.json"
                if json_file.exists():
                    # Открываем JSON файл в текстовом редакторе
                    try:
                        if sys.platform == "win32":
                            os.startfile(json_file)
                        elif sys.platform == "darwin":  # macOS
                            subprocess.run(["open", str(json_file)])
                        else:  # Linux
                            subprocess.run(["xdg-open", str(json_file)])
                        log.info(f"Открыт JSON файл: {json_file}")
                    except Exception as e:
                        log.error(f"Ошибка при открытии JSON файла: {e}")
                else:
                    log.error(f"JSON файл не найден: {json_file}")

        elif action == recognize_action:
            # TODO: Реализовать перераспознавание
            log.info("Перераспознавание пока не реализовано")

    def update_status(self, running=False):
        """Update display status based on service state"""
        if running:
            self.status_label.setText("Service: Running")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
        else:
            self.status_label.setText("Service: Stopped")
            self.status_label.setStyleSheet("color: red;")
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)

    def update_api_status(self, connected=False):
        """Update API connection status"""
        if connected:
            self.api_status.setText("API: Connected")
            self.api_status.setStyleSheet("color: green;")
        else:
            self.api_status.setText("API: Not Connected")
            self.api_status.setStyleSheet("color: red;")

    def show_audio_devices(self):
        """Show information about available audio input devices"""
        log.info("🎤 Проверка аудио устройств...")
        try:
            import sounddevice as sd
            devices = sd.query_devices()

            log.info(f"Найдено {len(devices)} аудио устройств:")

            # Show input devices
            input_devices = []
            for i, device in enumerate(devices):
                if isinstance(device, dict):
                    max_input = device.get('max_input_channels', 0)
                    if max_input > 0:
                        name = device.get('name', f"Device {i}")
                        input_devices.append((i, name, max_input))

            if input_devices:
                log.info("Входные устройства:")
                for i, name, channels in input_devices:
                    log.info(f"  [{i}] {name} ({channels} channels)")
            else:
                log.error("⚠️ Входных устройств не найдено!")

            # Show current settings
            settings_manager = self.settings_manager
            if settings_manager:
                use_default = settings_manager.get(
                    SettingsSection.GENERAL, GeneralSettings.DEFAULT_DEVICE, True
                )
                device_name = settings_manager.get(
                    SettingsSection.GENERAL, GeneralSettings.INPUT_DEVICE, ""
                )

                if use_default:
                    log.info("Текущая настройка: используется системное устройство по умолчанию")
                elif device_name:
                    log.info(f"Текущая настройка: используется устройство '{device_name}'")
                else:
                    log.info("Текущая настройка: по умолчанию (устройство не указано)")

        except Exception as e:
            log.error(f"❌ Ошибка при проверке аудио устройств: {str(e)}")

    def start_service(self):
        # Start service through tray_icon
        if (
            hasattr(self, "tray_icon")
            and self.tray_icon
            and hasattr(self.tray_icon, "start_remote_whisper")
        ):
            log.info("🚀 Запуск сервиса распознавания...")
            self.tray_icon.start_remote_whisper()
            self.update_status(running=True)

    def stop_service(self):
        # Stop service through tray_icon
        if (
            hasattr(self, "tray_icon")
            and self.tray_icon
            and hasattr(self.tray_icon, "stop_whisper")
        ):
            log.info("🛑 Остановка сервиса распознавания...")
            self.tray_icon.stop_whisper()
            self.update_status(running=False)

    def show_settings(self):
        """Shows settings dialog"""
        # Helper function for safe logging
        def log_message(message):
            log.info(message)

        log_message("⚙️ Opening settings dialog...")
        settings_dialog = SettingsDialog(self.settings_manager, self)
        if settings_dialog.exec_() == QtWidgets.QDialog.Accepted:
            # Settings are automatically saved when changed now
            log_message("✅ Settings applied successfully")

            # Re-check API status
            api_key = self.settings_manager.get(SettingsSection.OPENAI, OpenAISettings.API_KEY, "")
            env_key = os.environ.get("OPENAI_API_KEY", "")
            self.update_api_status(connected=(api_key or env_key))

    def on_tray_activated(self, reason):
        """Handle tray icon activation (clicks)"""
        # ActivationReason.Trigger == left click
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            if self.log_window.isVisible():
                self.log_window.hide()
            else:
                self.log_window.show()
                self.log_window.raise_()
                self.log_window.activateWindow()

    def toggle_logs(self):
        """Показать или скрыть окно логов"""
        if self.log_view_window.isVisible():
            self.log_view_window.hide()
        else:
            self.log_view_window.show()
            self.log_view_window.raise_()
            self.log_view_window.activateWindow()

    def _on_new_transcription(self, data: dict):
        """Обработка новой транскрипции"""
        success = self.transcription_model.addTranscription(data)
        if success:
            log.debug(f"Добавлена новая транскрипция: {data.get('timestamp', '')}")

    def _clear_transcriptions(self):
        """Очистка таблицы транскрипций"""
        self.transcription_model.clear()
        log.info("Таблица транскрипций очищена")

    def _reload_transcriptions(self):
        """Перезагрузка транскрипций из файлов"""
        self.transcription_model.clear()
        self.watcher.load_existing_transcriptions()
        log.info("Транскрипции перезагружены")


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.setWindowTitle("Whispex Settings")
        self.resize(*SETTINGS_DIALOG_SIZE)

        # Set icon for settings window
        script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        icon_path = script_dir / "whispex.png"

        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        # Create main layout with tabs
        layout = QtWidgets.QVBoxLayout()
        self.tabs = QtWidgets.QTabWidget()

        # Create tabs for different setting categories
        self.create_general_tab()
        self.create_whisper_tab()
        self.create_openai_tab()

        layout.addWidget(self.tabs)

        # Buttons
        button_layout = QtWidgets.QHBoxLayout()

        reset_button = QtWidgets.QPushButton("Reset Settings")
        reset_button.clicked.connect(self.reset_settings)

        apply_button = QtWidgets.QPushButton("Apply")
        apply_button.clicked.connect(self.apply_settings)

        cancel_button = QtWidgets.QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(reset_button)
        button_layout.addStretch()
        button_layout.addWidget(cancel_button)
        button_layout.addWidget(apply_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def create_general_tab(self):
        """Create General Settings tab"""
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "General")

        layout = QtWidgets.QFormLayout()
        tab.setLayout(layout)

        # Language setting
        self.language_input = QtWidgets.QLineEdit()
        self.language_input.setText(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.LANGUAGE, "en"
            )
        )
        self.language_input.setToolTip("Language code (e.g. 'en', 'ru', 'fr')")
        layout.addRow("Language:", self.language_input)

        # Recording key
        self.rec_key_input = QtWidgets.QLineEdit()
        self.rec_key_input.setText(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.REC_KEY, "pynput.keyboard.Key.alt_r"
            )
        )
        self.rec_key_input.setToolTip("Key used for push-to-talk recording")
        layout.addRow("Recording Key:", self.rec_key_input)

        # Input method
        self.input_method_combo = QtWidgets.QComboBox()
        self.input_method_combo.addItems(["clipboard_ctrl_v", "clipboard_ctrl_shift_v", "direct"])
        current_method = self.settings_manager.get(
            SettingsSection.GENERAL, GeneralSettings.INPUT_METHOD, "clipboard_ctrl_shift_v"
        )
        self.input_method_combo.setCurrentText(current_method)
        self.input_method_combo.setToolTip("Method to insert transcribed text")
        layout.addRow("Input Method:", self.input_method_combo)

        # Input devices
        device_layout = QtWidgets.QHBoxLayout()

        # Default device checkbox
        self.default_device_checkbox = QtWidgets.QCheckBox("Use default device")
        self.default_device_checkbox.setChecked(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.DEFAULT_DEVICE, True
            )
        )
        self.default_device_checkbox.toggled.connect(self.on_default_device_toggled)

        # Input device selection
        self.input_device_combo = QtWidgets.QComboBox()
        self.populate_audio_devices()
        current_device = self.settings_manager.get(
            SettingsSection.GENERAL, GeneralSettings.INPUT_DEVICE, ""
        )

        if current_device:
            index = self.input_device_combo.findText(current_device)
            if index >= 0:
                self.input_device_combo.setCurrentIndex(index)

        # Enable/disable device selection based on default device setting
        self.input_device_combo.setEnabled(not self.default_device_checkbox.isChecked())

        device_layout.addWidget(self.default_device_checkbox)
        device_layout.addWidget(self.input_device_combo)

        layout.addRow("Audio Input:", device_layout)

        # Auto off time
        self.auto_off_spinbox = QtWidgets.QSpinBox()
        self.auto_off_spinbox.setMinimum(0)
        self.auto_off_spinbox.setMaximum(86400)  # 24 hours in seconds
        self.auto_off_spinbox.setValue(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.AUTO_OFF_TIME, 0
            )
        )
        self.auto_off_spinbox.setToolTip("Automatically exit after N seconds of inactivity (0 to disable)")
        layout.addRow("Auto Off Time (seconds):", self.auto_off_spinbox)

        # No type option
        self.no_type_checkbox = QtWidgets.QCheckBox()
        self.no_type_checkbox.setChecked(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.NO_TYPE, False
            )
        )
        self.no_type_checkbox.setToolTip("Don't type transcribed text when enabled")
        layout.addRow("Disable Text Input:", self.no_type_checkbox)

    def on_default_device_toggled(self, checked):
        """Enable/disable device selection based on default device setting"""
        self.input_device_combo.setEnabled(not checked)

    def populate_audio_devices(self):
        """Populate audio input devices dropdown"""
        try:
            import sounddevice as sd
            devices = sd.query_devices()

            # Clear combo box
            self.input_device_combo.clear()

            # Add empty option
            self.input_device_combo.addItem("Default", "")

            # Add input devices
            for i, device in enumerate(devices):
                if isinstance(device, dict) and device.get('max_input_channels', 0) > 0:
                    name = device.get('name', f"Device {i}")
                    self.input_device_combo.addItem(name, i)
        except Exception as e:
            log.error(f"Error populating audio devices: {e}")
            self.input_device_combo.addItem("No devices found", "")

    def create_whisper_tab(self):
        """Create Whisper Settings tab"""
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "Whisper")

        layout = QtWidgets.QVBoxLayout()
        tab.setLayout(layout)

        form_layout = QtWidgets.QFormLayout()

        # Temperature
        self.temp_spinbox = QtWidgets.QDoubleSpinBox()
        self.temp_spinbox.setMinimum(0.0)
        self.temp_spinbox.setMaximum(1.0)
        self.temp_spinbox.setSingleStep(0.1)
        self.temp_spinbox.setValue(
            self.settings_manager.get(
                SettingsSection.WHISPER, WhisperSettings.TEMPERATURE, 0.2
            )
        )
        self.temp_spinbox.setToolTip(
            "Value from 0.0 to 1.0. Lower values make output more deterministic."
        )
        form_layout.addRow("Temperature:", self.temp_spinbox)

        # Sample rate
        self.sample_rate_combo = QtWidgets.QComboBox()
        self.sample_rate_combo.addItems(["16000", "8000", "24000", "44100", "48000"])
        current_sample_rate = str(self.settings_manager.get(
            SettingsSection.WHISPER, WhisperSettings.SAMPLE_RATE, 16000
        ))
        self.sample_rate_combo.setCurrentText(current_sample_rate)
        self.sample_rate_combo.setToolTip("Whisper sampling rate in Hz")
        form_layout.addRow("Sample Rate:", self.sample_rate_combo)

        # Recording sample rate
        self.recording_sample_rate_combo = QtWidgets.QComboBox()
        self.recording_sample_rate_combo.addItems(["16000", "44100", "48000", "96000"])
        current_recording_rate = str(self.settings_manager.get(
            SettingsSection.WHISPER, WhisperSettings.RECORDING_SAMPLE_RATE, 48000
        ))
        self.recording_sample_rate_combo.setCurrentText(current_recording_rate)
        self.recording_sample_rate_combo.setToolTip("Recording sampling rate in Hz (should be multiple of Sample Rate)")
        form_layout.addRow("Recording Sample Rate:", self.recording_sample_rate_combo)

        layout.addLayout(form_layout)

        # Prompt
        prompt_group = QtWidgets.QGroupBox("Prompt for Whisper")
        prompt_layout = QtWidgets.QVBoxLayout()
        prompt_group.setLayout(prompt_layout)

        self.prompt_text = QtWidgets.QTextEdit()
        default_prompt = self.settings_manager.get(
            SettingsSection.WHISPER, WhisperSettings.PROMPT, ""
        )
        self.prompt_text.setPlainText(default_prompt)
        self.prompt_text.setToolTip("Context to help improve transcription accuracy")

        prompt_layout.addWidget(self.prompt_text)
        layout.addWidget(prompt_group)

    def create_openai_tab(self):
        """Create OpenAI Settings tab"""
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "OpenAI")

        layout = QtWidgets.QFormLayout()
        tab.setLayout(layout)

        # API Key
        self.api_key_input = QtWidgets.QLineEdit()
        current_key = self.settings_manager.get(
            SettingsSection.OPENAI, OpenAISettings.API_KEY, ""
        )
        self.api_key_input.setText(current_key)
        self.api_key_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key_input.setToolTip("Your OpenAI API key (leave empty to use OPENAI_API_KEY environment variable)")
        layout.addRow("API Key:", self.api_key_input)

        # Information label
        info_label = QtWidgets.QLabel(
            "Leave API key empty to use the OPENAI_API_KEY environment variable.\n"
            "You need a valid OpenAI API key for the application to work."
        )
        info_label.setWordWrap(True)
        layout.addRow("", info_label)

    def reset_settings(self):
        """Resets settings to default values"""
        # Load default values from config file
        try:
            script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
            default_config_path = script_dir / "default_config.toml"

            if default_config_path.exists():
                with open(default_config_path, "rb") as f:
                    default_config = tomli.load(f)

                # Set values from default config for General tab
                self.language_input.setText(default_config["general"]["language"])
                self.rec_key_input.setText(default_config["general"]["rec_key"])
                self.input_method_combo.setCurrentText(default_config["general"]["input_method"])
                self.auto_off_spinbox.setValue(default_config["general"]["auto_off_time"])
                self.no_type_checkbox.setChecked(default_config["general"]["no_type"])

                # Audio device settings
                self.default_device_checkbox.setChecked(default_config["general"].get("default_device", True))
                self.input_device_combo.setEnabled(not self.default_device_checkbox.isChecked())
                self.input_device_combo.setCurrentIndex(0)  # Set to default option

                # Set values from default config for Whisper tab
                self.temp_spinbox.setValue(default_config["whisper"]["temperature"])
                self.sample_rate_combo.setCurrentText(str(default_config["whisper"]["sample_rate"]))
                self.recording_sample_rate_combo.setCurrentText(str(default_config["whisper"]["recording_sample_rate"]))
                self.prompt_text.setPlainText(default_config["whisper"]["prompt"])

                # OpenAI tab - API key is not set in defaults typically
                self.api_key_input.clear()
            else:
                log.warning("Default config file not found")
        except Exception as e:
            log.error(f"Error loading default settings: {e}")
            # Use safe fallback values
            self.language_input.setText("en")
            self.rec_key_input.setText("pynput.keyboard.Key.alt_r")
            self.input_method_combo.setCurrentText("clipboard_ctrl_shift_v")
            self.auto_off_spinbox.setValue(0)
            self.no_type_checkbox.setChecked(False)
            self.default_device_checkbox.setChecked(True)
            self.input_device_combo.setEnabled(False)
            self.temp_spinbox.setValue(0.2)
            self.sample_rate_combo.setCurrentText("16000")
            self.recording_sample_rate_combo.setCurrentText("48000")
            self.prompt_text.clear()
            self.api_key_input.clear()

    def apply_settings(self):
        """Apply settings and close dialog"""
        # General settings
        lang = self.language_input.text().strip()
        rec_key = self.rec_key_input.text().strip()
        input_method = self.input_method_combo.currentText()
        auto_off_time = self.auto_off_spinbox.value()
        no_type = self.no_type_checkbox.isChecked()
        default_device = self.default_device_checkbox.isChecked()

        # Get selected input device
        input_device = ""
        if not default_device:
            index = self.input_device_combo.currentIndex()
            if index > 0:  # Skip the "Default" option
                input_device = self.input_device_combo.currentText()

        # Whisper settings
        temperature = self.temp_spinbox.value()
        sample_rate = int(self.sample_rate_combo.currentText())
        recording_sample_rate = int(self.recording_sample_rate_combo.currentText())
        prompt = self.prompt_text.toPlainText()

        # OpenAI settings
        api_key = self.api_key_input.text().strip()

        # Update settings
        success = True

        # General settings
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.LANGUAGE, lang)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.REC_KEY, rec_key)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.INPUT_METHOD, input_method)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.AUTO_OFF_TIME, auto_off_time)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.NO_TYPE, no_type)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.DEFAULT_DEVICE, default_device)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.INPUT_DEVICE, input_device)

        # Whisper settings
        success &= self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.TEMPERATURE, temperature)
        success &= self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.SAMPLE_RATE, sample_rate)
        success &= self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.RECORDING_SAMPLE_RATE, recording_sample_rate)
        success &= self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.PROMPT, prompt)

        # OpenAI settings - only set if not empty
        if api_key:
            success &= self.settings_manager.set(SettingsSection.OPENAI, OpenAISettings.API_KEY, api_key)

        if success:
            log.info("Settings updated successfully")
        else:
            log.error("Error saving settings")

        # Accept dialog
        self.accept()


class LogTableModel(QAbstractTableModel):
    """Модель данных для таблицы логов"""

    # Определение колонок
    COL_TIME = 0
    COL_MESSAGE = 1
    COL_PLAY = 2
    COL_COPY = 3
    COL_RECOG = 4

    # Заголовки колонок
    HEADERS = ["Время", "Сообщение", "Аудио", "Копировать", "Перераспознать"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries: List[LogEntry] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self.entries)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self.entries):
            return None

        entry = self.entries[index.row()]

        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == self.COL_TIME:
                return entry.time_str
            elif index.column() == self.COL_MESSAGE:
                return entry.text
            # Остальные колонки не отображают текст

        elif role == Qt.ItemDataRole.ToolTipRole:
            if index.column() == self.COL_PLAY and entry.has_audio:
                return "Воспроизвести аудио"
            elif index.column() == self.COL_COPY:
                return "Копировать текст в буфер обмена"
            elif index.column() == self.COL_RECOG and entry.has_audio:
                return "Выполнить повторное распознавание"

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if index.column() == self.COL_TIME:
                return int(Qt.AlignmentFlag.AlignCenter)

        elif role == Qt.ItemDataRole.BackgroundRole:
            if entry.entry_type == LOG_TYPE_ERROR:
                return QtGui.QColor(255, 220, 220)  # Светло-красный для ошибок
            elif entry.entry_type == LOG_TYPE_TRANSCRIPTION:
                return QtGui.QColor(220, 255, 220)  # Светло-зеленый для распознанного текста
            elif entry.entry_type == LOG_TYPE_DEBUG:
                return QtGui.QColor(240, 240, 240)  # Серый для отладочных сообщений

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def addEntry(self, entry: LogEntry) -> None:
        """Добавляет запись в таблицу"""
        self.beginInsertRows(QModelIndex(), len(self.entries), len(self.entries))
        self.entries.append(entry)
        self.endInsertRows()

    def clear(self) -> None:
        """Очищает все записи в таблице"""
        self.beginResetModel()
        self.entries.clear()
        self.endResetModel()

    def getEntry(self, row: int) -> Optional[LogEntry]:
        """Возвращает запись по индексу строки"""
        if 0 <= row < len(self.entries):
            return self.entries[row]
        return None


class LogTableButtonDelegate(QtWidgets.QStyledItemDelegate):
    """Делегат для отображения кнопок в таблице"""

    # Сигналы для обработки нажатий
    playClicked = QtCore.pyqtSignal(int)
    copyClicked = QtCore.pyqtSignal(int)
    recognizeClicked = QtCore.pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

    def paint(self, painter: QtGui.QPainter, option: QtWidgets.QStyleOptionViewItem, index: QModelIndex) -> None:
        """Отрисовка кнопок в ячейках"""
        if not index.isValid():
            return super().paint(painter, option, index)

        model = index.model()
        if not isinstance(model, LogTableModel) or index.row() >= model.rowCount():
            return super().paint(painter, option, index)

        entry = model.getEntry(index.row())
        if not entry:
            return super().paint(painter, option, index)

        # Центрируем содержимое ячейки
        option.displayAlignment = Qt.AlignmentFlag.AlignCenter

        # Готовим кисть и перо
        painter.save()

        # Определяем цвет и состояние кнопки
        enabled = False
        text = ""

        if index.column() == LogTableModel.COL_PLAY:
            # Кнопка воспроизведения
            text = "🔊"
            enabled = entry.has_audio
        elif index.column() == LogTableModel.COL_COPY:
            # Кнопка копирования
            text = "📋"
            enabled = True
        elif index.column() == LogTableModel.COL_RECOG:
            # Кнопка перераспознавания
            text = "🔄"
            enabled = entry.has_audio
        else:
            # Для других колонок используем стандартную отрисовку
            painter.restore()
            return super().paint(painter, option, index)

        # Фон кнопки
        is_selected = (option.state & QtWidgets.QStyle.State_Selected) != 0
        is_hover = (option.state & QtWidgets.QStyle.State_MouseOver) != 0

        if is_selected:
            # Если строка выбрана, используем цвет выделения
            painter.setBrush(option.palette.highlight())
        else:
            # Иначе используем обычный фон или чуть темнее для наведения
            if is_hover and enabled:
                painter.setBrush(option.palette.mid())
            else:
                painter.setBrush(option.palette.button())

        # Настраиваем цвет текста
        if enabled:
            painter.setPen(option.palette.buttonText().color())
        else:
            painter.setPen(option.palette.mid().color())

        # Рисуем фон кнопки (скругленный прямоугольник)
        button_rect = option.rect.adjusted(4, 4, -4, -4)
        painter.drawRoundedRect(button_rect, 5, 5)

        # Рисуем текст кнопки
        painter.drawText(option.rect, int(Qt.AlignmentFlag.AlignCenter), text)

        painter.restore()

    def editorEvent(self, event: QtCore.QEvent, model: QAbstractTableModel,
                   option: QtWidgets.QStyleOptionViewItem, index: QModelIndex) -> bool:
        """Обработка событий нажатия на кнопки"""
        if not isinstance(model, LogTableModel) or not index.isValid():
            return super().editorEvent(event, model, option, index)

        if (event.type() == QtCore.QEvent.Type.MouseButtonRelease and
            isinstance(event, QtGui.QMouseEvent) and
            event.button() == Qt.MouseButton.LeftButton):

            entry = model.getEntry(index.row())
            if not entry:
                return False

            if index.column() == LogTableModel.COL_PLAY and entry.has_audio and entry.audio_file:
                self.playClicked.emit(index.row())
                return True

            elif index.column() == LogTableModel.COL_COPY:
                self.copyClicked.emit(index.row())
                return True

            elif index.column() == LogTableModel.COL_RECOG and entry.has_audio:
                self.recognizeClicked.emit(index.row())
                return True

        return super().editorEvent(event, model, option, index)

    def sizeHint(self, option: QtWidgets.QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """Определяет размер ячейки"""
        size = super().sizeHint(option, index)

        # Увеличиваем высоту ячеек для удобства нажатия на кнопки
        if index.column() in [LogTableModel.COL_PLAY, LogTableModel.COL_COPY, LogTableModel.COL_RECOG]:
            size.setHeight(max(size.height(), 30))

        return size


class AudioPlayer(QtCore.QObject):
    """Класс для воспроизведения аудиофайлов"""

    playbackFinished = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None

    def play(self, audio_file: str) -> bool:
        """Воспроизводит аудиофайл"""
        if not os.path.exists(audio_file):
            return False

        if self.process and self.process.poll() is None:
            # Останавливаем текущий процесс воспроизведения
            self.stop()

        try:
            # Используем subprocess для запуска плеера
            # aplay для Linux, afplay для macOS, или можно использовать библиотеку PyAudio
            if sys.platform == "linux":
                self.process = subprocess.Popen(["aplay", audio_file])
            elif sys.platform == "darwin":
                self.process = subprocess.Popen(["afplay", audio_file])
            else:
                # Для Windows и других платформ можно использовать PyAudio
                # или другие методы воспроизведения
                log.warning(f"Audio playback not supported on platform: {sys.platform}")
                return False

            # Запускаем таймер для проверки завершения воспроизведения
            self.timer = QtCore.QTimer()
            self.timer.timeout.connect(self._check_playback)
            self.timer.start(100)  # Проверяем каждые 100 мс

            return True
        except Exception as e:
            log.error(f"Error playing audio: {e}")
            return False

    def stop(self) -> None:
        """Останавливает воспроизведение"""
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()

        if hasattr(self, 'timer') and self.timer.isActive():
            self.timer.stop()

    def _check_playback(self) -> None:
        """Проверяет, завершилось ли воспроизведение"""
        if self.process and self.process.poll() is not None:
            self.timer.stop()
            self.playbackFinished.emit()


class LogViewWindow(QtWidgets.QDialog):
    """Окно для отображения логов программы"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Whispex - Logs")
        self.resize(*LOGS_WINDOW_SIZE)

        # Установка значка
        script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        icon_path = script_dir / "whispex.png"

        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        # Создаем текстовое поле для логов
        self.log_text = QtWidgets.QTextEdit(self)
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QtGui.QFont("Courier New", 10))

        # Кнопка очистки логов
        clear_button = QtWidgets.QPushButton("Очистить логи")
        clear_button.clicked.connect(self.clear_logs)

        # Создаем компоновку
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.log_text)
        layout.addWidget(clear_button)
        self.setLayout(layout)

    def append_text(self, text: str, entry_type: str = LOG_TYPE_INFO):
        """Добавляет сообщение в лог"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")

        # Форматирование в зависимости от типа лога
        if entry_type == LOG_TYPE_ERROR:
            formatted_text = f'<span style="color:red">[{timestamp}] {text}</span>'
        elif entry_type == LOG_TYPE_SYSTEM:
            formatted_text = f'<span style="color:blue">[{timestamp}] {text}</span>'
        elif entry_type == LOG_TYPE_DEBUG:
            formatted_text = f'<span style="color:gray">[{timestamp}] {text}</span>'
        else:
            formatted_text = f'[{timestamp}] {text}'

        # Добавляем текст и прокручиваем вниз
        self.log_text.append(formatted_text)
        scrollbar = self.log_text.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())

    def clear_logs(self):
        """Очищает все логи"""
        self.log_text.clear()


class LogHandler(logging.Handler):
    def __init__(self, log_window):
        super().__init__()
        self.log_window = log_window
        self.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))

    def emit(self, record):
        msg = self.format(record)

        # Определяем тип сообщения на основе уровня логирования
        if record.levelno >= logging.ERROR:
            entry_type = LOG_TYPE_ERROR
        elif record.levelno >= logging.WARNING:
            entry_type = LOG_TYPE_INFO
        elif record.levelno >= logging.DEBUG:
            entry_type = LOG_TYPE_DEBUG
        else:
            entry_type = LOG_TYPE_SYSTEM

        # Safely add log through append_text method which handles thread safety
        self.log_window.append_text(msg, entry_type=entry_type)


class LogWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Whispex Voice Recognition")
        self.resize(*LOG_WINDOW_SIZE)

        # Set icon for log window
        script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        icon_path = script_dir / "whispex.png"

        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        # Initialize tray_icon reference
        self.tray_icon = None

        # Окно для отображения логов
        self.log_view_window = LogViewWindow(self)

        # Получение директорий из настроек
        self.settings_manager = SettingsManager()
        base_dir, _ = self.settings_manager.get_data_dirs()

        # Создаем аудио плеер
        self.audio_player = AudioPlayer(self)

        # Создаем модель данных для таблицы транскрипций
        self.transcription_model = TranscriptionTableModel(self)

        # Создаем таблицу для отображения транскрипций
        self.transcription_table = QtWidgets.QTableView(self)
        self.transcription_table.setModel(self.transcription_model)

        # Настраиваем таблицу
        self.transcription_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.transcription_table.setAlternatingRowColors(True)
        vertical_header = self.transcription_table.verticalHeader()
        if vertical_header:
            vertical_header.setVisible(False)
        self.transcription_table.setShowGrid(True)

        # Настраиваем ширину колонок
        self.transcription_table.setColumnWidth(TranscriptionTableModel.COL_TIME, 150)  # Время
        self.transcription_table.setColumnWidth(TranscriptionTableModel.COL_LANG, 80)   # Язык
        self.transcription_table.setColumnWidth(TranscriptionTableModel.COL_AUDIO, 200) # Аудиофайл
        self.transcription_table.setColumnWidth(TranscriptionTableModel.COL_PLAY, 40)   # Кнопка воспроизведения
        self.transcription_table.setColumnWidth(TranscriptionTableModel.COL_COPY, 40)   # Кнопка копирования
        self.transcription_table.setColumnWidth(TranscriptionTableModel.COL_RECOG, 40)  # Кнопка перераспознавания

        # Растягиваем колонку с текстом
        header = self.transcription_table.horizontalHeader()
        if header:
            header.setSectionResizeMode(
                TranscriptionTableModel.COL_TEXT,
                QtWidgets.QHeaderView.ResizeMode.Stretch
            )

        # Создаем и устанавливаем делегат для кнопок
        self.button_delegate = TranscriptionButtonDelegate(self)
        self.transcription_table.setItemDelegateForColumn(TranscriptionTableModel.COL_PLAY, self.button_delegate)
        self.transcription_table.setItemDelegateForColumn(TranscriptionTableModel.COL_COPY, self.button_delegate)
        self.transcription_table.setItemDelegateForColumn(TranscriptionTableModel.COL_RECOG, self.button_delegate)

        # Подключаем сигналы делегата
        self.button_delegate.playClicked.connect(self._on_play_clicked)
        self.button_delegate.copyClicked.connect(self._on_copy_clicked)
        self.button_delegate.recognizeClicked.connect(self._on_recognize_clicked)

        # Включаем контекстное меню для таблицы
        self.transcription_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.transcription_table.customContextMenuRequested.connect(self._show_context_menu)

        # Подключаем двойной клик к действию воспроизведения
        self.transcription_table.doubleClicked.connect(self._on_table_double_clicked)

        # Создаем наблюдателя за транскрипциями
        self.watcher = TranscriptionWatcher(base_dir, self)
        self.watcher.new_transcription.connect(self._on_new_transcription)

        # Create status bar
        status_layout = QtWidgets.QHBoxLayout()

        # Service status label
        self.status_label = QtWidgets.QLabel("Service: Stopped")
        status_layout.addWidget(self.status_label)

        # API status
        self.api_status = QtWidgets.QLabel("API: Unknown")
        self.api_status.setStyleSheet("color: gray;")
        status_layout.addWidget(self.api_status)

        # Add spacer to push everything to the left
        status_layout.addStretch()

        # Create buttons
        button_layout = QtWidgets.QHBoxLayout()

        # Кнопки для транскрипций
        clear_button = QtWidgets.QPushButton("Очистить")
        clear_button.setToolTip("Очистить таблицу транскрипций")
        clear_button.clicked.connect(self._clear_transcriptions)
        button_layout.addWidget(clear_button)

        refresh_button = QtWidgets.QPushButton("Обновить")
        refresh_button.setToolTip("Перезагрузить транскрипции из файлов")
        refresh_button.clicked.connect(self._reload_transcriptions)
        button_layout.addWidget(refresh_button)

        # Кнопка показа/скрытия логов
        logs_button = QtWidgets.QPushButton("Logs")
        logs_button.setToolTip("Показать/скрыть окно логов")
        logs_button.clicked.connect(self.toggle_logs)
        button_layout.addWidget(logs_button)

        # Add audio devices button
        audio_devices_button = QtWidgets.QPushButton("Audio Devices")
        audio_devices_button.clicked.connect(self.show_audio_devices)
        button_layout.addWidget(audio_devices_button)

        # Add settings button
        settings_button = QtWidgets.QPushButton("Settings")
        settings_button.clicked.connect(self.show_settings)
        button_layout.addWidget(settings_button)

        # Add control buttons
        self.start_button = QtWidgets.QPushButton("Start")
        self.start_button.clicked.connect(self.start_service)
        button_layout.addWidget(self.start_button)

        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_service)
        self.stop_button.setEnabled(False)
        button_layout.addWidget(self.stop_button)

        # Create layout
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.transcription_table)
        layout.addLayout(status_layout)
        layout.addLayout(button_layout)
        self.setLayout(layout)

        # Добавляем информационное сообщение
        log.info("Главное окно приложения инициализировано")

        # Запускаем наблюдателя за транскрипциями
        self.watcher.start()

    def _on_play_clicked(self, row):
        """Обработка нажатия на кнопку воспроизведения"""
        transcription = self.transcription_model.getTranscription(row)
        if transcription:
            audio_file = transcription.get('audio_file', '')
            if audio_file:
                base_dir, _ = self.settings_manager.get_data_dirs()
                full_path = base_dir / audio_file
                if full_path.exists():
                    log.info(f"Воспроизведение аудио: {full_path}")
                    self.audio_player.play(str(full_path))
                else:
                    log.error(f"Аудиофайл не найден: {full_path}")

    def _on_copy_clicked(self, row):
        """Обработка нажатия на кнопку копирования"""
        transcription = self.transcription_model.getTranscription(row)
        if transcription:
            text = transcription.get('text', '')
            if text:
                clipboard = QtWidgets.QApplication.clipboard()
                if clipboard:
                    clipboard.setText(text)
                    log.info(f"Текст скопирован в буфер обмена: '{text[:30]}...'")

    def _on_recognize_clicked(self, row):
        """Обработка нажатия на кнопку перераспознавания"""
        transcription = self.transcription_model.getTranscription(row)
        if transcription:
            audio_file = transcription.get('audio_file', '')
            if audio_file:
                # TODO: Реализовать перераспознавание
                log.info(f"Перераспознавание аудио: {audio_file} (пока не реализовано)")

    def _on_table_double_clicked(self, index: QModelIndex):
        """Обработка двойного клика по таблице"""
        row = index.row()
        transcription = self.transcription_model.getTranscription(row)

        if transcription:
            if index.column() == TranscriptionTableModel.COL_AUDIO:
                # Воспроизводим аудио при двойном клике на аудиофайл
                audio_file = transcription.get('audio_file', '')
                if audio_file:
                    base_dir, _ = self.settings_manager.get_data_dirs()
                    full_path = base_dir / audio_file
                    if full_path.exists():
                        log.info(f"Воспроизведение аудио: {full_path}")
                        self.audio_player.play(str(full_path))
                    else:
                        log.error(f"Аудиофайл не найден: {full_path}")
            elif index.column() == TranscriptionTableModel.COL_TEXT:
                # Показываем диалог для просмотра и частичного копирования текста
                text = transcription.get('text', '')
                if text:
                    dialog = TextViewDialog(text, self)
                    dialog.exec_()

    def _show_context_menu(self, position):
        """Показывает контекстное меню для таблицы"""
        index = self.transcription_table.indexAt(position)
        if not index.isValid():
            return

        row = index.row()
        transcription = self.transcription_model.getTranscription(row)
        if not transcription:
            return

        menu = QtWidgets.QMenu(self)

        # Пункт "Копировать текст"
        copy_action = menu.addAction("Копировать текст")

        # Пункт "Воспроизвести аудио"
        play_action = menu.addAction("Воспроизвести аудио")

        # Пункт "Открыть JSON"
        open_json_action = menu.addAction("Открыть JSON файл")

        # Пункт "Перераспознать"
        recognize_action = menu.addAction("Перераспознать")

        # Показываем меню
        viewport = self.transcription_table.viewport()
        if viewport:
            action = menu.exec_(viewport.mapToGlobal(position))
        else:
            action = menu.exec_(QtGui.QCursor.pos())

        # Обрабатываем выбранное действие
        if action == copy_action:
            text = transcription.get('text', '')
            if text:
                clipboard = QtWidgets.QApplication.clipboard()
                if clipboard:
                    clipboard.setText(text)
                    log.info(f"Текст скопирован в буфер обмена: '{text[:30]}...'")

        elif action == play_action:
            audio_file = transcription.get('audio_file', '')
            if audio_file:
                base_dir, _ = self.settings_manager.get_data_dirs()
                full_path = base_dir / audio_file
                if full_path.exists():
                    log.info(f"Воспроизведение аудио: {full_path}")
                    self.audio_player.play(str(full_path))
                else:
                    log.error(f"Аудиофайл не найден: {full_path}")

        elif action == open_json_action:
            timestamp = transcription.get('timestamp', '')
            if timestamp:
                base_dir, _ = self.settings_manager.get_data_dirs()
                json_file = base_dir / f"transcription_{timestamp}.json"
                if json_file.exists():
                    # Открываем JSON файл в текстовом редакторе
                    try:
                        if sys.platform == "win32":
                            os.startfile(json_file)
                        elif sys.platform == "darwin":  # macOS
                            subprocess.run(["open", str(json_file)])
                        else:  # Linux
                            subprocess.run(["xdg-open", str(json_file)])
                        log.info(f"Открыт JSON файл: {json_file}")
                    except Exception as e:
                        log.error(f"Ошибка при открытии JSON файла: {e}")
                else:
                    log.error(f"JSON файл не найден: {json_file}")

        elif action == recognize_action:
            # TODO: Реализовать перераспознавание
            log.info("Перераспознавание пока не реализовано")

    def update_status(self, running=False):
        """Update display status based on service state"""
        if running:
            self.status_label.setText("Service: Running")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
        else:
            self.status_label.setText("Service: Stopped")
            self.status_label.setStyleSheet("color: red;")
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)

    def update_api_status(self, connected=False):
        """Update API connection status"""
        if connected:
            self.api_status.setText("API: Connected")
            self.api_status.setStyleSheet("color: green;")
        else:
            self.api_status.setText("API: Not Connected")
            self.api_status.setStyleSheet("color: red;")

    def show_audio_devices(self):
        """Show information about available audio input devices"""
        log.info("🎤 Проверка аудио устройств...")
        try:
            import sounddevice as sd
            devices = sd.query_devices()

            log.info(f"Найдено {len(devices)} аудио устройств:")

            # Show input devices
            input_devices = []
            for i, device in enumerate(devices):
                if isinstance(device, dict):
                    max_input = device.get('max_input_channels', 0)
                    if max_input > 0:
                        name = device.get('name', f"Device {i}")
                        input_devices.append((i, name, max_input))

            if input_devices:
                log.info("Входные устройства:")
                for i, name, channels in input_devices:
                    log.info(f"  [{i}] {name} ({channels} channels)")
            else:
                log.error("⚠️ Входных устройств не найдено!")

            # Show current settings
            settings_manager = self.settings_manager
            if settings_manager:
                use_default = settings_manager.get(
                    SettingsSection.GENERAL, GeneralSettings.DEFAULT_DEVICE, True
                )
                device_name = settings_manager.get(
                    SettingsSection.GENERAL, GeneralSettings.INPUT_DEVICE, ""
                )

                if use_default:
                    log.info("Текущая настройка: используется системное устройство по умолчанию")
                elif device_name:
                    log.info(f"Текущая настройка: используется устройство '{device_name}'")
                else:
                    log.info("Текущая настройка: по умолчанию (устройство не указано)")

        except Exception as e:
            log.error(f"❌ Ошибка при проверке аудио устройств: {str(e)}")

    def start_service(self):
        # Start service through tray_icon
        if (
            hasattr(self, "tray_icon")
            and self.tray_icon
            and hasattr(self.tray_icon, "start_remote_whisper")
        ):
            log.info("🚀 Запуск сервиса распознавания...")
            self.tray_icon.start_remote_whisper()
            self.update_status(running=True)

    def stop_service(self):
        # Stop service through tray_icon
        if (
            hasattr(self, "tray_icon")
            and self.tray_icon
            and hasattr(self.tray_icon, "stop_whisper")
        ):
            log.info("🛑 Остановка сервиса распознавания...")
            self.tray_icon.stop_whisper()
            self.update_status(running=False)

    def show_settings(self):
        """Shows settings dialog"""
        # Helper function for safe logging
        def log_message(message):
            log.info(message)

        log_message("⚙️ Opening settings dialog...")
        settings_dialog = SettingsDialog(self.settings_manager, self)
        if settings_dialog.exec_() == QtWidgets.QDialog.Accepted:
            # Settings are automatically saved when changed now
            log_message("✅ Settings applied successfully")

            # Re-check API status
            api_key = self.settings_manager.get(SettingsSection.OPENAI, OpenAISettings.API_KEY, "")
            env_key = os.environ.get("OPENAI_API_KEY", "")
            self.update_api_status(connected=(api_key or env_key))

    def on_tray_activated(self, reason):
        """Handle tray icon activation (clicks)"""
        # ActivationReason.Trigger == left click
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            if self.log_window.isVisible():
                self.log_window.hide()
            else:
                self.log_window.show()
                self.log_window.raise_()
                self.log_window.activateWindow()

    def toggle_logs(self):
        """Показать или скрыть окно логов"""
        if self.log_view_window.isVisible():
            self.log_view_window.hide()
        else:
            self.log_view_window.show()
            self.log_view_window.raise_()
            self.log_view_window.activateWindow()

    def _on_new_transcription(self, data: dict):
        """Обработка новой транскрипции"""
        success = self.transcription_model.addTranscription(data)
        if success:
            log.debug(f"Добавлена новая транскрипция: {data.get('timestamp', '')}")

    def _clear_transcriptions(self):
        """Очистка таблицы транскрипций"""
        self.transcription_model.clear()
        log.info("Таблица транскрипций очищена")

    def _reload_transcriptions(self):
        """Перезагрузка транскрипций из файлов"""
        self.transcription_model.clear()
        self.watcher.load_existing_transcriptions()
        log.info("Транскрипции перезагружены")


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.setWindowTitle("Whispex Settings")
        self.resize(*SETTINGS_DIALOG_SIZE)

        # Set icon for settings window
        script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        icon_path = script_dir / "whispex.png"

        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        # Create main layout with tabs
        layout = QtWidgets.QVBoxLayout()
        self.tabs = QtWidgets.QTabWidget()

        # Create tabs for different setting categories
        self.create_general_tab()
        self.create_whisper_tab()
        self.create_openai_tab()

        layout.addWidget(self.tabs)

        # Buttons
        button_layout = QtWidgets.QHBoxLayout()

        reset_button = QtWidgets.QPushButton("Reset Settings")
        reset_button.clicked.connect(self.reset_settings)

        apply_button = QtWidgets.QPushButton("Apply")
        apply_button.clicked.connect(self.apply_settings)

        cancel_button = QtWidgets.QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(reset_button)
        button_layout.addStretch()
        button_layout.addWidget(cancel_button)
        button_layout.addWidget(apply_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def create_general_tab(self):
        """Create General Settings tab"""
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "General")

        layout = QtWidgets.QFormLayout()
        tab.setLayout(layout)

        # Language setting
        self.language_input = QtWidgets.QLineEdit()
        self.language_input.setText(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.LANGUAGE, "en"
            )
        )
        self.language_input.setToolTip("Language code (e.g. 'en', 'ru', 'fr')")
        layout.addRow("Language:", self.language_input)

        # Recording key
        self.rec_key_input = QtWidgets.QLineEdit()
        self.rec_key_input.setText(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.REC_KEY, "pynput.keyboard.Key.alt_r"
            )
        )
        self.rec_key_input.setToolTip("Key used for push-to-talk recording")
        layout.addRow("Recording Key:", self.rec_key_input)

        # Input method
        self.input_method_combo = QtWidgets.QComboBox()
        self.input_method_combo.addItems(["clipboard_ctrl_v", "clipboard_ctrl_shift_v", "direct"])
        current_method = self.settings_manager.get(
            SettingsSection.GENERAL, GeneralSettings.INPUT_METHOD, "clipboard_ctrl_shift_v"
        )
        self.input_method_combo.setCurrentText(current_method)
        self.input_method_combo.setToolTip("Method to insert transcribed text")
        layout.addRow("Input Method:", self.input_method_combo)

        # Input devices
        device_layout = QtWidgets.QHBoxLayout()

        # Default device checkbox
        self.default_device_checkbox = QtWidgets.QCheckBox("Use default device")
        self.default_device_checkbox.setChecked(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.DEFAULT_DEVICE, True
            )
        )
        self.default_device_checkbox.toggled.connect(self.on_default_device_toggled)

        # Input device selection
        self.input_device_combo = QtWidgets.QComboBox()
        self.populate_audio_devices()
        current_device = self.settings_manager.get(
            SettingsSection.GENERAL, GeneralSettings.INPUT_DEVICE, ""
        )

        if current_device:
            index = self.input_device_combo.findText(current_device)
            if index >= 0:
                self.input_device_combo.setCurrentIndex(index)

        # Enable/disable device selection based on default device setting
        self.input_device_combo.setEnabled(not self.default_device_checkbox.isChecked())

        device_layout.addWidget(self.default_device_checkbox)
        device_layout.addWidget(self.input_device_combo)

        layout.addRow("Audio Input:", device_layout)

        # Auto off time
        self.auto_off_spinbox = QtWidgets.QSpinBox()
        self.auto_off_spinbox.setMinimum(0)
        self.auto_off_spinbox.setMaximum(86400)  # 24 hours in seconds
        self.auto_off_spinbox.setValue(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.AUTO_OFF_TIME, 0
            )
        )
        self.auto_off_spinbox.setToolTip("Automatically exit after N seconds of inactivity (0 to disable)")
        layout.addRow("Auto Off Time (seconds):", self.auto_off_spinbox)

        # No type option
        self.no_type_checkbox = QtWidgets.QCheckBox()
        self.no_type_checkbox.setChecked(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.NO_TYPE, False
            )
        )
        self.no_type_checkbox.setToolTip("Don't type transcribed text when enabled")
        layout.addRow("Disable Text Input:", self.no_type_checkbox)

    def on_default_device_toggled(self, checked):
        """Enable/disable device selection based on default device setting"""
        self.input_device_combo.setEnabled(not checked)

    def populate_audio_devices(self):
        """Populate audio input devices dropdown"""
        try:
            import sounddevice as sd
            devices = sd.query_devices()

            # Clear combo box
            self.input_device_combo.clear()

            # Add empty option
            self.input_device_combo.addItem("Default", "")

            # Add input devices
            for i, device in enumerate(devices):
                if isinstance(device, dict) and device.get('max_input_channels', 0) > 0:
                    name = device.get('name', f"Device {i}")
                    self.input_device_combo.addItem(name, i)
        except Exception as e:
            log.error(f"Error populating audio devices: {e}")
            self.input_device_combo.addItem("No devices found", "")

    def create_whisper_tab(self):
        """Create Whisper Settings tab"""
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "Whisper")

        layout = QtWidgets.QVBoxLayout()
        tab.setLayout(layout)

        form_layout = QtWidgets.QFormLayout()

        # Temperature
        self.temp_spinbox = QtWidgets.QDoubleSpinBox()
        self.temp_spinbox.setMinimum(0.0)
        self.temp_spinbox.setMaximum(1.0)
        self.temp_spinbox.setSingleStep(0.1)
        self.temp_spinbox.setValue(
            self.settings_manager.get(
                SettingsSection.WHISPER, WhisperSettings.TEMPERATURE, 0.2
            )
        )
        self.temp_spinbox.setToolTip(
            "Value from 0.0 to 1.0. Lower values make output more deterministic."
        )
        form_layout.addRow("Temperature:", self.temp_spinbox)

        # Sample rate
        self.sample_rate_combo = QtWidgets.QComboBox()
        self.sample_rate_combo.addItems(["16000", "8000", "24000", "44100", "48000"])
        current_sample_rate = str(self.settings_manager.get(
            SettingsSection.WHISPER, WhisperSettings.SAMPLE_RATE, 16000
        ))
        self.sample_rate_combo.setCurrentText(current_sample_rate)
        self.sample_rate_combo.setToolTip("Whisper sampling rate in Hz")
        form_layout.addRow("Sample Rate:", self.sample_rate_combo)

        # Recording sample rate
        self.recording_sample_rate_combo = QtWidgets.QComboBox()
        self.recording_sample_rate_combo.addItems(["16000", "44100", "48000", "96000"])
        current_recording_rate = str(self.settings_manager.get(
            SettingsSection.WHISPER, WhisperSettings.RECORDING_SAMPLE_RATE, 48000
        ))
        self.recording_sample_rate_combo.setCurrentText(current_recording_rate)
        self.recording_sample_rate_combo.setToolTip("Recording sampling rate in Hz (should be multiple of Sample Rate)")
        form_layout.addRow("Recording Sample Rate:", self.recording_sample_rate_combo)

        layout.addLayout(form_layout)

        # Prompt
        prompt_group = QtWidgets.QGroupBox("Prompt for Whisper")
        prompt_layout = QtWidgets.QVBoxLayout()
        prompt_group.setLayout(prompt_layout)

        self.prompt_text = QtWidgets.QTextEdit()
        default_prompt = self.settings_manager.get(
            SettingsSection.WHISPER, WhisperSettings.PROMPT, ""
        )
        self.prompt_text.setPlainText(default_prompt)
        self.prompt_text.setToolTip("Context to help improve transcription accuracy")

        prompt_layout.addWidget(self.prompt_text)
        layout.addWidget(prompt_group)

    def create_openai_tab(self):
        """Create OpenAI Settings tab"""
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "OpenAI")

        layout = QtWidgets.QFormLayout()
        tab.setLayout(layout)

        # API Key
        self.api_key_input = QtWidgets.QLineEdit()
        current_key = self.settings_manager.get(
            SettingsSection.OPENAI, OpenAISettings.API_KEY, ""
        )
        self.api_key_input.setText(current_key)
        self.api_key_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key_input.setToolTip("Your OpenAI API key (leave empty to use OPENAI_API_KEY environment variable)")
        layout.addRow("API Key:", self.api_key_input)

        # Information label
        info_label = QtWidgets.QLabel(
            "Leave API key empty to use the OPENAI_API_KEY environment variable.\n"
            "You need a valid OpenAI API key for the application to work."
        )
        info_label.setWordWrap(True)
        layout.addRow("", info_label)

    def reset_settings(self):
        """Resets settings to default values"""
        # Load default values from config file
        try:
            script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
            default_config_path = script_dir / "default_config.toml"

            if default_config_path.exists():
                with open(default_config_path, "rb") as f:
                    default_config = tomli.load(f)

                # Set values from default config for General tab
                self.language_input.setText(default_config["general"]["language"])
                self.rec_key_input.setText(default_config["general"]["rec_key"])
                self.input_method_combo.setCurrentText(default_config["general"]["input_method"])
                self.auto_off_spinbox.setValue(default_config["general"]["auto_off_time"])
                self.no_type_checkbox.setChecked(default_config["general"]["no_type"])

                # Audio device settings
                self.default_device_checkbox.setChecked(default_config["general"].get("default_device", True))
                self.input_device_combo.setEnabled(not self.default_device_checkbox.isChecked())
                self.input_device_combo.setCurrentIndex(0)  # Set to default option

                # Set values from default config for Whisper tab
                self.temp_spinbox.setValue(default_config["whisper"]["temperature"])
                self.sample_rate_combo.setCurrentText(str(default_config["whisper"]["sample_rate"]))
                self.recording_sample_rate_combo.setCurrentText(str(default_config["whisper"]["recording_sample_rate"]))
                self.prompt_text.setPlainText(default_config["whisper"]["prompt"])

                # OpenAI tab - API key is not set in defaults typically
                self.api_key_input.clear()
            else:
                log.warning("Default config file not found")
        except Exception as e:
            log.error(f"Error loading default settings: {e}")
            # Use safe fallback values
            self.language_input.setText("en")
            self.rec_key_input.setText("pynput.keyboard.Key.alt_r")
            self.input_method_combo.setCurrentText("clipboard_ctrl_shift_v")
            self.auto_off_spinbox.setValue(0)
            self.no_type_checkbox.setChecked(False)
            self.default_device_checkbox.setChecked(True)
            self.input_device_combo.setEnabled(False)
            self.temp_spinbox.setValue(0.2)
            self.sample_rate_combo.setCurrentText("16000")
            self.recording_sample_rate_combo.setCurrentText("48000")
            self.prompt_text.clear()
            self.api_key_input.clear()

    def apply_settings(self):
        """Apply settings and close dialog"""
        # General settings
        lang = self.language_input.text().strip()
        rec_key = self.rec_key_input.text().strip()
        input_method = self.input_method_combo.currentText()
        auto_off_time = self.auto_off_spinbox.value()
        no_type = self.no_type_checkbox.isChecked()
        default_device = self.default_device_checkbox.isChecked()

        # Get selected input device
        input_device = ""
        if not default_device:
            index = self.input_device_combo.currentIndex()
            if index > 0:  # Skip the "Default" option
                input_device = self.input_device_combo.currentText()

        # Whisper settings
        temperature = self.temp_spinbox.value()
        sample_rate = int(self.sample_rate_combo.currentText())
        recording_sample_rate = int(self.recording_sample_rate_combo.currentText())
        prompt = self.prompt_text.toPlainText()

        # OpenAI settings
        api_key = self.api_key_input.text().strip()

        # Update settings
        success = True

        # General settings
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.LANGUAGE, lang)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.REC_KEY, rec_key)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.INPUT_METHOD, input_method)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.AUTO_OFF_TIME, auto_off_time)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.NO_TYPE, no_type)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.DEFAULT_DEVICE, default_device)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.INPUT_DEVICE, input_device)

        # Whisper settings
        success &= self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.TEMPERATURE, temperature)
        success &= self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.SAMPLE_RATE, sample_rate)
        success &= self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.RECORDING_SAMPLE_RATE, recording_sample_rate)
        success &= self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.PROMPT, prompt)

        # OpenAI settings - only set if not empty
        if api_key:
            success &= self.settings_manager.set(SettingsSection.OPENAI, OpenAISettings.API_KEY, api_key)

        if success:
            log.info("Settings updated successfully")
        else:
            log.error("Error saving settings")

        # Accept dialog
        self.accept()


def setup_gui_logging(log_window):
    """Set up logging to display in the GUI log window"""
    class LogHandler(logging.Handler):
        def __init__(self, log_window):
            super().__init__()
            self.log_window = log_window
            self.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))

        def emit(self, record):
            msg = self.format(record)

            # Определяем тип сообщения на основе уровня логирования
            if record.levelno >= logging.ERROR:
                entry_type = LOG_TYPE_ERROR
            elif record.levelno >= logging.WARNING:
                entry_type = LOG_TYPE_INFO
            elif record.levelno >= logging.DEBUG:
                entry_type = LOG_TYPE_DEBUG
            else:
                entry_type = LOG_TYPE_SYSTEM

            # Safely add log through append_text method which handles thread safety
            self.log_window.append_text(msg, entry_type=entry_type)

    # Create and add the custom handler
    gui_handler = LogHandler(log_window)
    gui_handler.setLevel(logging.INFO)  # Set level to INFO for the GUI

    # Add handler to root logger
    root_logger = logging.getLogger()
    root_logger.addHandler(gui_handler)


def main():
    """Main application entry point"""
    # Enable unbuffered output
    os.environ['PYTHONUNBUFFERED'] = '1'

    # Create Qt application
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # Don't close app when all windows closed

    # Create main widget (needed to parent the tray icon)
    main_widget = QtWidgets.QWidget()

    # Create tray icon
    tray_icon = WhisperTrayIcon(main_widget)
    tray_icon.show()

    # Настраиваем перенаправление логов в окно логов
    log_view = tray_icon.log_window.log_view_window
    log_handler = LogHandler(log_view)
    log_handler.setLevel(logging.INFO)

    # Добавляем обработчик к корневому логгеру
    root_logger = logging.getLogger()
    root_logger.addHandler(log_handler)

    # Show the main window at start
    tray_icon.log_window.show()
    log.info("✨ Приложение запущено")

    # Show startup message
    tray_icon.showMessage(
        "Whispex",
        "Speech recognition service is ready. Use Alt-Right key to start recording.",
        tray_icon.icon(),
        3000,
    )

    # Автоматический запуск сервиса распознавания
    QtCore.QTimer.singleShot(1000, lambda: tray_icon.start_remote_whisper())

    # Execute application
    sys.exit(app.exec_())


class OutputReaderThread(QtCore.QThread):
    """Thread for reading process output without blocking the GUI"""
    output_received = QtCore.pyqtSignal(str)

    def __init__(self, process):
        super().__init__()
        self.process = process
        self._stop_flag = False

    def run(self):
        import select
        import time

        # Get file descriptors for select
        stdout_fd = self.process.stdout.fileno()
        stderr_fd = self.process.stderr.fileno()

        # Create list for select
        read_list = [stdout_fd, stderr_fd]

        while not self._stop_flag and self.process.poll() is None:
            # Use select for non-blocking read
            readable, _, _ = select.select(read_list, [], [], 0.1)

            for fd in readable:
                if fd == stdout_fd:
                    line = self.process.stdout.readline().strip()
                    if line:
                        self.output_received.emit(line)
                elif fd == stderr_fd:
                    line = self.process.stderr.readline().strip()
                    if line:
                        self.output_received.emit(f"Error: {line}")

            # Small pause to reduce CPU load
            time.sleep(0.01)

        # Read remaining output after process completion
        try:
            for line in self.process.stdout:
                if line.strip():
                    self.output_received.emit(line.strip())
            for line in self.process.stderr:
                if line.strip():
                    self.output_received.emit(f"Error: {line.strip()}")
        except (IOError, OSError) as e:
            self.output_received.emit(f"Error reading remaining output: {e}")

        self.output_received.emit("Process completed.")

    def stop(self):
        self._stop_flag = True


class WhisperTrayIcon(QtWidgets.QSystemTrayIcon):
    def __init__(self, parent=None):
        super().__init__(parent)

        # Setup script directories and paths
        self.script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        self.script_path = self.script_dir / "whispex.py"

        # Store icon path as class member
        self.icon_path = self.script_dir / "whispex.png"

        if self.icon_path.exists():
            self.setIcon(QtGui.QIcon(str(self.icon_path)))
        else:
            # Fallback to system icon
            self.setIcon(QtGui.QIcon.fromTheme("audio-input-microphone"))
            log.warning(f"Icon not found at path {self.icon_path}")

        # Save parent widget
        self.parent_widget = parent

        # Initialize settings manager
        self.settings_manager = SettingsManager()

        # Create main window (with transcriptions)
        self.log_window = LogWindow(self.parent_widget)
        # Set feedback reference
        self.log_window.tray_icon = self

        # Connect activated signal to handle tray icon clicks
        self.activated.connect(self.on_tray_activated)

        # Check OpenAI API status
        self.check_api_status()

        # Create menu
        self.menu = QtWidgets.QMenu()

        # Add start action
        self.start_remote_action = self.menu.addAction("Start")
        if self.start_remote_action:
            self.start_remote_action.triggered.connect(self.start_remote_whisper)

        # Add stop action
        self.stop_action = self.menu.addAction("Stop")
        if self.stop_action:
            self.stop_action.triggered.connect(self.stop_whisper)
            self.stop_action.setEnabled(False)

        # Add log view action
        self.log_action = self.menu.addAction("Open Main Window")
        if self.log_action:
            self.log_action.triggered.connect(self.show_log)

        # Add logs view action
        self.logs_view_action = self.menu.addAction("Show Logs")
        if self.logs_view_action:
            self.logs_view_action.triggered.connect(self.show_logs)

        # Add settings action
        self.settings_action = self.menu.addAction("Settings")
        if self.settings_action:
            self.settings_action.triggered.connect(self.show_settings)

        # Add separator
        self.menu.addSeparator()

        # Add exit action
        exit_action = self.menu.addAction("Exit")
        if exit_action:
            exit_action.triggered.connect(self.exit_app)

        # Set menu
        self.setContextMenu(self.menu)

        # Initialize process variables
        self.process = None
        self.output_reader = None
        self.running = False

    def show_logs(self):
        """Показать окно логов"""
        self.log_window.toggle_logs()

    def check_api_status(self):
        """Check if the OpenAI API key is configured and valid"""
        api_key = self.settings_manager.get(SettingsSection.OPENAI, OpenAISettings.API_KEY, "")
        env_key = os.environ.get("OPENAI_API_KEY", "")

        # Check if we have an API key from either source
        if api_key or env_key:
            self.log_window.update_api_status(connected=True)
            log.info("✅ OpenAI API ключ настроен")
        else:
            self.log_window.update_api_status(connected=False)
            log.error("❌ OpenAI API ключ не настроен")
            log.info("Настройте API ключ в Settings → OpenAI")

    def start_remote_whisper(self):
        if not self.running:
            success = self.start_whisper("whispex.py")
            if success:
                self.running = True
                self.start_remote_action.setEnabled(False)
                self.stop_action.setEnabled(True)
                # Update log window
                self.log_window.update_status(running=True)
                # Show notification
                icon = (
                    QtGui.QIcon(str(self.icon_path))
                    if self.icon_path.exists()
                    else QtGui.QIcon.fromTheme("audio-input-microphone")
                )
                self.showMessage(
                    "Whispex", "Speech recognition service started", icon, 3000
                )

    def start_whisper(self, script_name):
        """
        Start the Whispex whisper process.
        """
        if not self.script_path.exists():
            log.error(f"Error: Script {self.script_path} not found")
            return False

        try:
            # Set environment variables
            os.environ['PYTHONUNBUFFERED'] = '1'
            os.environ['WHISPEX_LOG_LEVEL'] = 'DEBUG'

            # Run process with redirected output that we'll capture in the log window
            cmd = " ".join(UV_RUN_COMMAND + [str(self.script_path)])
            log.info(f"Running command: {cmd}")
            log.info(f"Working directory: {self.script_dir}")

            # Use subprocess.Popen with redirected stdout and stderr in binary mode
            self.process = subprocess.Popen(
                UV_RUN_COMMAND + [str(self.script_path)],
                cwd=str(self.script_dir),
                env=os.environ,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,  # Unbuffered
                universal_newlines=True  # Use universal newlines mode
            )

            # Create and start output reader thread
            self.output_reader = OutputReaderThread(self.process)
            # Изменение: теперь логи идут в окно логов
            self.output_reader.output_received.connect(
                lambda text: log.info(text) if not text.startswith("Error:") else log.error(text[7:])
            )
            self.output_reader.start()

            # Add a debug message in the log window
            log.info(f"Started {script_name} with DEBUG logging (PID: {self.process.pid})")
            log.info("Process output will be displayed in logs")
            return True
        except Exception as e:
            log.error(f"Error starting {script_name}: {e}")
            import traceback
            log.error(traceback.format_exc())
            return False

    def stop_whisper(self):
        """
        Stop any running Whispex processes
        """
        log.info("Stopping all Whispex processes...")

        # Stop output reader thread if running
        if self.output_reader:
            log.info("Stopping output reader thread...")
            self.output_reader.stop()
            # Don't block GUI by waiting for thread completion
            # self.output_reader.wait()
            self.output_reader = None

        # Clean up process reference
        process_to_terminate = self.process  # Store reference to process
        if process_to_terminate:
            try:
                log.info(f"Terminating process {process_to_terminate.pid}")
                process_to_terminate.terminate()

                # Wait with timeout without blocking GUI
                import threading
                def wait_for_process():
                    try:
                        retcode = process_to_terminate.wait(timeout=2)
                        log.info(f"Process terminated with code {retcode}")
                    except subprocess.TimeoutExpired:
                        log.warning("Process termination timeout, sending SIGKILL")
                        try:
                            process_to_terminate.kill()
                            process_to_terminate.wait(timeout=1)
                        except (ProcessLookupError, OSError) as e:
                            log.error(f"Error force killing process: {e}")
                    except (ProcessLookupError, OSError) as e:
                        log.error(f"Error waiting for process: {e}")

                # Start waiting in a separate thread
                wait_thread = threading.Thread(target=wait_for_process)
                wait_thread.daemon = True
                wait_thread.start()
            except (ProcessLookupError, OSError) as e:
                log.error(f"Error terminating process: {e}")

            # Mark as closed after initiating termination
            self.process = None

        # Find and stop all whispex processes
        try:
            log.info("Searching for whispex.py processes...")
            output = subprocess.run(
                ["pgrep", "-f", "whispex.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            if output.returncode == 0:
                pids = output.stdout.strip().split()
                if pids:
                    log.info(
                        f"Found {len(pids)} Whispex processes: {pids}"
                    )
                    for pid in pids:
                        try:
                            pid_int = int(pid)
                            # Skip if it's our already terminated process
                            if process_to_terminate and pid_int == process_to_terminate.pid:
                                continue
                            os.kill(pid_int, signal.SIGTERM)
                            log.info(
                                f"Sent termination signal to process {pid}"
                            )
                        except ProcessLookupError:
                            log.info(
                                f"Process {pid} already terminated"
                            )
                        except OSError as e:
                            log.error(
                                f"Error terminating process {pid}: {e}"
                            )
                else:
                    log.info("No Whispex processes found")
            else:
                log.info("No Whispex processes found")
        except OSError as e:
            log.error(f"Error stopping Whispex processes: {e}")
            import traceback
            log.error(traceback.format_exc())

        # Update GUI state
        self.running = False
        self.start_remote_action.setEnabled(True)
        self.stop_action.setEnabled(False)
        self.log_window.update_status(running=False)
        log.info("Speech recognition service stopped")

    def show_log(self):
        self.log_window.show()
        self.log_window.raise_()

    def exit_app(self):
        log.info("Exiting application...")

        # Stop process if running
        self.stop_whisper()

        # Terminate application
        QtWidgets.QApplication.quit()

    def show_settings(self):
        """Shows settings dialog"""
        # Helper function for safe logging
        def log_message(message):
            log.info(message)

        log_message("⚙️ Opening settings dialog...")
        settings_dialog = SettingsDialog(self.settings_manager, self)
        if settings_dialog.exec_() == QtWidgets.QDialog.Accepted:
            # Settings are automatically saved when changed now
            log_message("✅ Settings applied successfully")

            # Re-check API status
            api_key = self.settings_manager.get(SettingsSection.OPENAI, OpenAISettings.API_KEY, "")
            env_key = os.environ.get("OPENAI_API_KEY", "")
            self.update_api_status(connected=(api_key or env_key))

    def on_tray_activated(self, reason):
        """Handle tray icon activation (clicks)"""
        # ActivationReason.Trigger == left click
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            if self.log_window.isVisible():
                self.log_window.hide()
            else:
                self.log_window.show()
                self.log_window.raise_()
                self.log_window.activateWindow()


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.setWindowTitle("Whispex Settings")
        self.resize(*SETTINGS_DIALOG_SIZE)

        # Set icon for settings window
        script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        icon_path = script_dir / "whispex.png"

        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))

        # Create main layout with tabs
        layout = QtWidgets.QVBoxLayout()
        self.tabs = QtWidgets.QTabWidget()

        # Create tabs for different setting categories
        self.create_general_tab()
        self.create_whisper_tab()
        self.create_openai_tab()

        layout.addWidget(self.tabs)

        # Buttons
        button_layout = QtWidgets.QHBoxLayout()

        reset_button = QtWidgets.QPushButton("Reset Settings")
        reset_button.clicked.connect(self.reset_settings)

        apply_button = QtWidgets.QPushButton("Apply")
        apply_button.clicked.connect(self.apply_settings)

        cancel_button = QtWidgets.QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        button_layout.addWidget(reset_button)
        button_layout.addStretch()
        button_layout.addWidget(cancel_button)
        button_layout.addWidget(apply_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def create_general_tab(self):
        """Create General Settings tab"""
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "General")

        layout = QtWidgets.QFormLayout()
        tab.setLayout(layout)

        # Language setting
        self.language_input = QtWidgets.QLineEdit()
        self.language_input.setText(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.LANGUAGE, "en"
            )
        )
        self.language_input.setToolTip("Language code (e.g. 'en', 'ru', 'fr')")
        layout.addRow("Language:", self.language_input)

        # Recording key
        self.rec_key_input = QtWidgets.QLineEdit()
        self.rec_key_input.setText(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.REC_KEY, "pynput.keyboard.Key.alt_r"
            )
        )
        self.rec_key_input.setToolTip("Key used for push-to-talk recording")
        layout.addRow("Recording Key:", self.rec_key_input)

        # Input method
        self.input_method_combo = QtWidgets.QComboBox()
        self.input_method_combo.addItems(["clipboard_ctrl_v", "clipboard_ctrl_shift_v", "direct"])
        current_method = self.settings_manager.get(
            SettingsSection.GENERAL, GeneralSettings.INPUT_METHOD, "clipboard_ctrl_shift_v"
        )
        self.input_method_combo.setCurrentText(current_method)
        self.input_method_combo.setToolTip("Method to insert transcribed text")
        layout.addRow("Input Method:", self.input_method_combo)

        # Input devices
        device_layout = QtWidgets.QHBoxLayout()

        # Default device checkbox
        self.default_device_checkbox = QtWidgets.QCheckBox("Use default device")
        self.default_device_checkbox.setChecked(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.DEFAULT_DEVICE, True
            )
        )
        self.default_device_checkbox.toggled.connect(self.on_default_device_toggled)

        # Input device selection
        self.input_device_combo = QtWidgets.QComboBox()
        self.populate_audio_devices()
        current_device = self.settings_manager.get(
            SettingsSection.GENERAL, GeneralSettings.INPUT_DEVICE, ""
        )

        if current_device:
            index = self.input_device_combo.findText(current_device)
            if index >= 0:
                self.input_device_combo.setCurrentIndex(index)

        # Enable/disable device selection based on default device setting
        self.input_device_combo.setEnabled(not self.default_device_checkbox.isChecked())

        device_layout.addWidget(self.default_device_checkbox)
        device_layout.addWidget(self.input_device_combo)

        layout.addRow("Audio Input:", device_layout)

        # Auto off time
        self.auto_off_spinbox = QtWidgets.QSpinBox()
        self.auto_off_spinbox.setMinimum(0)
        self.auto_off_spinbox.setMaximum(86400)  # 24 hours in seconds
        self.auto_off_spinbox.setValue(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.AUTO_OFF_TIME, 0
            )
        )
        self.auto_off_spinbox.setToolTip("Automatically exit after N seconds of inactivity (0 to disable)")
        layout.addRow("Auto Off Time (seconds):", self.auto_off_spinbox)

        # No type option
        self.no_type_checkbox = QtWidgets.QCheckBox()
        self.no_type_checkbox.setChecked(
            self.settings_manager.get(
                SettingsSection.GENERAL, GeneralSettings.NO_TYPE, False
            )
        )
        self.no_type_checkbox.setToolTip("Don't type transcribed text when enabled")
        layout.addRow("Disable Text Input:", self.no_type_checkbox)

    def on_default_device_toggled(self, checked):
        """Enable/disable device selection based on default device setting"""
        self.input_device_combo.setEnabled(not checked)

    def populate_audio_devices(self):
        """Populate audio input devices dropdown"""
        try:
            import sounddevice as sd
            devices = sd.query_devices()

            # Clear combo box
            self.input_device_combo.clear()

            # Add empty option
            self.input_device_combo.addItem("Default", "")

            # Add input devices
            for i, device in enumerate(devices):
                if isinstance(device, dict) and device.get('max_input_channels', 0) > 0:
                    name = device.get('name', f"Device {i}")
                    self.input_device_combo.addItem(name, i)
        except Exception as e:
            log.error(f"Error populating audio devices: {e}")
            self.input_device_combo.addItem("No devices found", "")

    def create_whisper_tab(self):
        """Create Whisper Settings tab"""
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "Whisper")

        layout = QtWidgets.QVBoxLayout()
        tab.setLayout(layout)

        form_layout = QtWidgets.QFormLayout()

        # Temperature
        self.temp_spinbox = QtWidgets.QDoubleSpinBox()
        self.temp_spinbox.setMinimum(0.0)
        self.temp_spinbox.setMaximum(1.0)
        self.temp_spinbox.setSingleStep(0.1)
        self.temp_spinbox.setValue(
            self.settings_manager.get(
                SettingsSection.WHISPER, WhisperSettings.TEMPERATURE, 0.2
            )
        )
        self.temp_spinbox.setToolTip(
            "Value from 0.0 to 1.0. Lower values make output more deterministic."
        )
        form_layout.addRow("Temperature:", self.temp_spinbox)

        # Sample rate
        self.sample_rate_combo = QtWidgets.QComboBox()
        self.sample_rate_combo.addItems(["16000", "8000", "24000", "44100", "48000"])
        current_sample_rate = str(self.settings_manager.get(
            SettingsSection.WHISPER, WhisperSettings.SAMPLE_RATE, 16000
        ))
        self.sample_rate_combo.setCurrentText(current_sample_rate)
        self.sample_rate_combo.setToolTip("Whisper sampling rate in Hz")
        form_layout.addRow("Sample Rate:", self.sample_rate_combo)

        # Recording sample rate
        self.recording_sample_rate_combo = QtWidgets.QComboBox()
        self.recording_sample_rate_combo.addItems(["16000", "44100", "48000", "96000"])
        current_recording_rate = str(self.settings_manager.get(
            SettingsSection.WHISPER, WhisperSettings.RECORDING_SAMPLE_RATE, 48000
        ))
        self.recording_sample_rate_combo.setCurrentText(current_recording_rate)
        self.recording_sample_rate_combo.setToolTip("Recording sampling rate in Hz (should be multiple of Sample Rate)")
        form_layout.addRow("Recording Sample Rate:", self.recording_sample_rate_combo)

        layout.addLayout(form_layout)

        # Prompt
        prompt_group = QtWidgets.QGroupBox("Prompt for Whisper")
        prompt_layout = QtWidgets.QVBoxLayout()
        prompt_group.setLayout(prompt_layout)

        self.prompt_text = QtWidgets.QTextEdit()
        default_prompt = self.settings_manager.get(
            SettingsSection.WHISPER, WhisperSettings.PROMPT, ""
        )
        self.prompt_text.setPlainText(default_prompt)
        self.prompt_text.setToolTip("Context to help improve transcription accuracy")

        prompt_layout.addWidget(self.prompt_text)
        layout.addWidget(prompt_group)

    def create_openai_tab(self):
        """Create OpenAI Settings tab"""
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "OpenAI")

        layout = QtWidgets.QFormLayout()
        tab.setLayout(layout)

        # API Key
        self.api_key_input = QtWidgets.QLineEdit()
        current_key = self.settings_manager.get(
            SettingsSection.OPENAI, OpenAISettings.API_KEY, ""
        )
        self.api_key_input.setText(current_key)
        self.api_key_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.api_key_input.setToolTip("Your OpenAI API key (leave empty to use OPENAI_API_KEY environment variable)")
        layout.addRow("API Key:", self.api_key_input)

        # Information label
        info_label = QtWidgets.QLabel(
            "Leave API key empty to use the OPENAI_API_KEY environment variable.\n"
            "You need a valid OpenAI API key for the application to work."
        )
        info_label.setWordWrap(True)
        layout.addRow("", info_label)

    def reset_settings(self):
        """Resets settings to default values"""
        # Load default values from config file
        try:
            script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
            default_config_path = script_dir / "default_config.toml"

            if default_config_path.exists():
                with open(default_config_path, "rb") as f:
                    default_config = tomli.load(f)

                # Set values from default config for General tab
                self.language_input.setText(default_config["general"]["language"])
                self.rec_key_input.setText(default_config["general"]["rec_key"])
                self.input_method_combo.setCurrentText(default_config["general"]["input_method"])
                self.auto_off_spinbox.setValue(default_config["general"]["auto_off_time"])
                self.no_type_checkbox.setChecked(default_config["general"]["no_type"])

                # Audio device settings
                self.default_device_checkbox.setChecked(default_config["general"].get("default_device", True))
                self.input_device_combo.setEnabled(not self.default_device_checkbox.isChecked())
                self.input_device_combo.setCurrentIndex(0)  # Set to default option

                # Set values from default config for Whisper tab
                self.temp_spinbox.setValue(default_config["whisper"]["temperature"])
                self.sample_rate_combo.setCurrentText(str(default_config["whisper"]["sample_rate"]))
                self.recording_sample_rate_combo.setCurrentText(str(default_config["whisper"]["recording_sample_rate"]))
                self.prompt_text.setPlainText(default_config["whisper"]["prompt"])

                # OpenAI tab - API key is not set in defaults typically
                self.api_key_input.clear()
            else:
                log.warning("Default config file not found")
        except Exception as e:
            log.error(f"Error loading default settings: {e}")
            # Use safe fallback values
            self.language_input.setText("en")
            self.rec_key_input.setText("pynput.keyboard.Key.alt_r")
            self.input_method_combo.setCurrentText("clipboard_ctrl_shift_v")
            self.auto_off_spinbox.setValue(0)
            self.no_type_checkbox.setChecked(False)
            self.default_device_checkbox.setChecked(True)
            self.input_device_combo.setEnabled(False)
            self.temp_spinbox.setValue(0.2)
            self.sample_rate_combo.setCurrentText("16000")
            self.recording_sample_rate_combo.setCurrentText("48000")
            self.prompt_text.clear()
            self.api_key_input.clear()

    def apply_settings(self):
        """Apply settings and close dialog"""
        # General settings
        lang = self.language_input.text().strip()
        rec_key = self.rec_key_input.text().strip()
        input_method = self.input_method_combo.currentText()
        auto_off_time = self.auto_off_spinbox.value()
        no_type = self.no_type_checkbox.isChecked()
        default_device = self.default_device_checkbox.isChecked()

        # Get selected input device
        input_device = ""
        if not default_device:
            index = self.input_device_combo.currentIndex()
            if index > 0:  # Skip the "Default" option
                input_device = self.input_device_combo.currentText()

        # Whisper settings
        temperature = self.temp_spinbox.value()
        sample_rate = int(self.sample_rate_combo.currentText())
        recording_sample_rate = int(self.recording_sample_rate_combo.currentText())
        prompt = self.prompt_text.toPlainText()

        # OpenAI settings
        api_key = self.api_key_input.text().strip()

        # Update settings
        success = True

        # General settings
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.LANGUAGE, lang)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.REC_KEY, rec_key)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.INPUT_METHOD, input_method)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.AUTO_OFF_TIME, auto_off_time)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.NO_TYPE, no_type)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.DEFAULT_DEVICE, default_device)
        success &= self.settings_manager.set(SettingsSection.GENERAL, GeneralSettings.INPUT_DEVICE, input_device)

        # Whisper settings
        success &= self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.TEMPERATURE, temperature)
        success &= self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.SAMPLE_RATE, sample_rate)
        success &= self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.RECORDING_SAMPLE_RATE, recording_sample_rate)
        success &= self.settings_manager.set(SettingsSection.WHISPER, WhisperSettings.PROMPT, prompt)

        # OpenAI settings - only set if not empty
        if api_key:
            success &= self.settings_manager.set(SettingsSection.OPENAI, OpenAISettings.API_KEY, api_key)

        if success:
            log.info("Settings updated successfully")
        else:
            log.error("Error saving settings")

        # Accept dialog
        self.accept()


if __name__ == "__main__":
    # Start main application
    main()

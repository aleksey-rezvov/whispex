import datetime
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
from settings import (GeneralSettings, OpenAISettings, SettingsManager,
                      SettingsSection, WhisperSettings)

# Constants
UV_RUN_COMMAND = ["uv", "run"]
LOG_WINDOW_SIZE = (900, 600)
SETTINGS_DIALOG_SIZE = (700, 500)

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

        mouse_event = cast(QtGui.QMouseEvent, event)
        if (event.type() == QtCore.QEvent.Type.MouseButtonRelease and
            hasattr(mouse_event, 'button') and
            mouse_event.button() == Qt.MouseButton.LeftButton):

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

        # Создаем аудио плеер
        self.audio_player = AudioPlayer(self)

        # Создаем модель данных для таблицы
        self.log_model = LogTableModel(self)

        # Создаем таблицу для отображения логов
        self.log_table = QtWidgets.QTableView(self)
        self.log_table.setModel(self.log_model)

        # Настраиваем таблицу
        self.log_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.log_table.setAlternatingRowColors(True)
        vertical_header = self.log_table.verticalHeader()
        if vertical_header:
            vertical_header.setVisible(False)
        self.log_table.setShowGrid(True)

        # Настраиваем ширину колонок
        self.log_table.setColumnWidth(LogTableModel.COL_TIME, 100)  # Время
        self.log_table.setColumnWidth(LogTableModel.COL_PLAY, 60)   # Кнопка воспроизведения
        self.log_table.setColumnWidth(LogTableModel.COL_COPY, 60)   # Кнопка копирования
        self.log_table.setColumnWidth(LogTableModel.COL_RECOG, 60)  # Кнопка перераспознавания

        # Растягиваем колонку с сообщением
        header = self.log_table.horizontalHeader()
        if header:
            header.setSectionResizeMode(
                LogTableModel.COL_MESSAGE,
                QtWidgets.QHeaderView.ResizeMode.Stretch
            )

        # Создаем и настраиваем делегат для кнопок
        self.button_delegate = LogTableButtonDelegate(self)
        self.log_table.setItemDelegateForColumn(LogTableModel.COL_PLAY, self.button_delegate)
        self.log_table.setItemDelegateForColumn(LogTableModel.COL_COPY, self.button_delegate)
        self.log_table.setItemDelegateForColumn(LogTableModel.COL_RECOG, self.button_delegate)

        # Подключаем сигналы делегата
        self.button_delegate.playClicked.connect(self._on_play_clicked)
        self.button_delegate.copyClicked.connect(self._on_copy_clicked)
        self.button_delegate.recognizeClicked.connect(self._on_recognize_clicked)

        # Добавляем приветственные сообщения
        self.append_text("✨ Welcome to Whispex Voice Recognition ✨")
        self.append_text("This application allows you to speak and have your voice transcribed to text.")
        self.append_text("The text will be inserted at your cursor position.")
        self.append_text("Status and log messages will appear here.")

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

        # Кнопка очистки лога
        clear_button = QtWidgets.QPushButton("Clear Log")
        clear_button.clicked.connect(self.clear_log)
        button_layout.addWidget(clear_button)

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
        layout.addWidget(self.log_table)
        layout.addLayout(status_layout)
        layout.addLayout(button_layout)
        self.setLayout(layout)

    def _on_play_clicked(self, row: int) -> None:
        """Обрабатывает нажатие на кнопку воспроизведения"""
        entry = self.log_model.getEntry(row)
        if entry and entry.has_audio and entry.audio_file:
            self.audio_player.play(entry.audio_file)

    def _on_copy_clicked(self, row: int) -> None:
        """Обрабатывает нажатие на кнопку копирования"""
        entry = self.log_model.getEntry(row)
        if entry:
            clipboard = QtWidgets.QApplication.clipboard()
            if clipboard:
                clipboard.setText(entry.text)
                # Показываем кратковременное сообщение об успешном копировании
                self.append_text(f"✅ Текст скопирован в буфер обмена: '{entry.text[:30]}...'",
                              entry_type=LOG_TYPE_INFO)

    def _on_recognize_clicked(self, row: int) -> None:
        """Обрабатывает нажатие на кнопку перераспознавания"""
        entry = self.log_model.getEntry(row)
        if entry and entry.has_audio:
            # Здесь будет код для повторного распознавания
            # Пока просто добавляем сообщение в лог
            self.append_text(f"🔄 Повторное распознавание для аудио: {entry.audio_file}",
                          entry_type=LOG_TYPE_INFO)
            # TODO: Реализовать повторное распознавание

    def show_audio_devices(self):
        """Show information about available audio input devices"""
        self.append_text("🎤 Checking audio input devices...", entry_type=LOG_TYPE_INFO)
        try:
            import sounddevice as sd
            devices = sd.query_devices()

            self.append_text(f"Found {len(devices)} audio devices:", entry_type=LOG_TYPE_INFO)

            # Show input devices
            input_devices = []
            for i, device in enumerate(devices):
                if isinstance(device, dict):
                    max_input = device.get('max_input_channels', 0)
                    if max_input > 0:
                        name = device.get('name', f"Device {i}")
                        input_devices.append((i, name, max_input))

            if input_devices:
                self.append_text("Input devices:", entry_type=LOG_TYPE_INFO)
                for i, name, channels in input_devices:
                    self.append_text(f"  [{i}] {name} ({channels} channels)", entry_type=LOG_TYPE_INFO)
            else:
                self.append_text("⚠️ No input devices found!", entry_type=LOG_TYPE_ERROR)

            # Show current settings
            settings_manager = self.tray_icon.settings_manager if self.tray_icon else None
            if settings_manager:
                use_default = settings_manager.get(
                    SettingsSection.GENERAL, GeneralSettings.DEFAULT_DEVICE, True
                )
                device_name = settings_manager.get(
                    SettingsSection.GENERAL, GeneralSettings.INPUT_DEVICE, ""
                )

                if use_default:
                    self.append_text("Current setting: Using system default device", entry_type=LOG_TYPE_INFO)
                elif device_name:
                    self.append_text(f"Current setting: Using specific device '{device_name}'", entry_type=LOG_TYPE_INFO)
                else:
                    self.append_text("Current setting: Default (no device specified)", entry_type=LOG_TYPE_INFO)

        except Exception as e:
            self.append_text(f"❌ Error checking audio devices: {str(e)}", entry_type=LOG_TYPE_ERROR)

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

    def start_service(self):
        # Start service through tray_icon
        if (
            hasattr(self, "tray_icon")
            and self.tray_icon
            and hasattr(self.tray_icon, "start_remote_whisper")
        ):
            self.append_text("🚀 Starting recognition service...", entry_type=LOG_TYPE_SYSTEM)
            self.tray_icon.start_remote_whisper()
            self.update_status(running=True)

    def stop_service(self):
        # Stop service through tray_icon
        if (
            hasattr(self, "tray_icon")
            and self.tray_icon
            and hasattr(self.tray_icon, "stop_whisper")
        ):
            self.append_text("🛑 Stopping recognition service...", entry_type=LOG_TYPE_SYSTEM)
            self.tray_icon.stop_whisper()
            self.update_status(running=False)

    def show_settings(self):
        # Show settings through tray_icon
        if (
            hasattr(self, "tray_icon")
            and self.tray_icon
            and hasattr(self.tray_icon, "show_settings")
        ):
            self.append_text("⚙️ Opening settings...", entry_type=LOG_TYPE_SYSTEM)
            self.tray_icon.show_settings()

    @QtCore.pyqtSlot(str)
    def append_text(self, text, entry_type=LOG_TYPE_SYSTEM, audio_file=None):
        # Wrap append method call in invokeMethod for thread-safe calls
        if QtCore.QThread.currentThread() == self.thread():
            # If we're in the main thread, call directly
            self._append_text_direct(text, entry_type, audio_file)
        else:
            # If we're in another thread, use invokeMethod
            QtCore.QMetaObject.invokeMethod(
                self,
                "_append_text_direct",
                QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, text),
                QtCore.Q_ARG(str, entry_type),
                QtCore.Q_ARG(str, audio_file if audio_file else "")
            )

    @QtCore.pyqtSlot(str, str, str)
    def _append_text_direct(self, text, entry_type=LOG_TYPE_SYSTEM, audio_file=None):
        """Direct text addition to log (must be called from main GUI thread only)"""
        entry = LogEntry(text, entry_type, time.time(), audio_file)
        self.log_model.addEntry(entry)

        # Прокручиваем к последней записи
        self.log_table.scrollToBottom()

        # Force GUI update
        QtWidgets.QApplication.processEvents()

    def clear_log(self):
        self.log_model.clear()


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

    # Set up log handler to display logs in the GUI
    setup_gui_logging(tray_icon.log_window)

    # Show the main window (log window) at start
    tray_icon.log_window.show()
    tray_icon.log_window.append_text("ℹ️ Application started")

    # Show startup message
    tray_icon.showMessage(
        "Whispex",
        "Speech recognition service is ready. Use Alt-Right key to start recording.",
        tray_icon.icon(),
        3000,
    )

    # Automatically start the speech recognition service
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

        # Create log window (before loading settings, so it's available for logging)
        self.log_window = LogWindow(self.parent_widget)
        # Set feedback reference
        self.log_window.tray_icon = self

        # Connect activated signal to handle tray icon clicks
        self.activated.connect(self.on_tray_activated)

        # Initialize settings manager
        self.settings_manager = SettingsManager()

        # Check OpenAI API status
        self.check_api_status()

        # Create menu
        self.menu = QtWidgets.QMenu()

        # Add start action
        self.start_remote_action = self.menu.addAction("Start")
        self.start_remote_action.triggered.connect(self.start_remote_whisper)

        # Add stop action
        self.stop_action = self.menu.addAction("Stop")
        self.stop_action.triggered.connect(self.stop_whisper)
        self.stop_action.setEnabled(False)

        # Add log view action
        self.log_action = self.menu.addAction("Open Main Window")
        self.log_action.triggered.connect(self.show_log)

        # Add settings action
        self.settings_action = self.menu.addAction("Settings")
        self.settings_action.triggered.connect(self.show_settings)

        # Add separator
        self.menu.addSeparator()

        # Add exit action
        exit_action = self.menu.addAction("Exit")
        exit_action.triggered.connect(self.exit_app)

        # Set menu
        self.setContextMenu(self.menu)

        # Initialize process variables
        self.process = None
        self.output_reader = None
        self.running = False

    def check_api_status(self):
        """Check if the OpenAI API key is configured and valid"""
        api_key = self.settings_manager.get(SettingsSection.OPENAI, OpenAISettings.API_KEY, "")
        env_key = os.environ.get("OPENAI_API_KEY", "")

        # Check if we have an API key from either source
        if api_key or env_key:
            self.log_window.update_api_status(connected=True)
            self.log_window.append_text("✅ OpenAI API key configured")
        else:
            self.log_window.update_api_status(connected=False)
            self.log_window.append_text("❌ OpenAI API key not configured")
            self.log_window.append_text("Please configure API key in Settings → OpenAI tab")

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
            self.log_window.append_text(f"Error: Script {self.script_path} not found")
            return False

        try:
            # Set environment variables
            os.environ['PYTHONUNBUFFERED'] = '1'
            os.environ['WHISPEX_LOG_LEVEL'] = 'DEBUG'

            # Run process with redirected output that we'll capture in the log window
            cmd = " ".join(UV_RUN_COMMAND + [str(self.script_path)])
            self.log_window.append_text(f"Running command: {cmd}")
            self.log_window.append_text(f"Working directory: {self.script_dir}")

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
            self.output_reader.output_received.connect(self.log_window.append_text)
            self.output_reader.start()

            # Add a debug message in the log window
            self.log_window.append_text(f"Started {script_name} with DEBUG logging (PID: {self.process.pid})")
            self.log_window.append_text("Process output will be displayed below:")
            return True
        except Exception as e:
            self.log_window.append_text(f"Error starting {script_name}: {e}")
            import traceback
            self.log_window.append_text(traceback.format_exc())
            return False

    def stop_whisper(self):
        """
        Stop any running Whispex processes
        """
        self.log_window.append_text("Stopping all Whispex processes...")

        # Stop output reader thread if running
        if self.output_reader:
            self.log_window.append_text("Stopping output reader thread...")
            self.output_reader.stop()
            # Don't block GUI by waiting for thread completion
            # self.output_reader.wait()
            self.output_reader = None

        # Clean up process reference
        process_to_terminate = self.process  # Store reference to process
        if process_to_terminate:
            try:
                self.log_window.append_text(f"Terminating process {process_to_terminate.pid}")
                process_to_terminate.terminate()

                # Wait with timeout without blocking GUI
                import threading
                def wait_for_process():
                    try:
                        retcode = process_to_terminate.wait(timeout=2)
                        self.log_window.append_text(f"Process terminated with code {retcode}")
                    except subprocess.TimeoutExpired:
                        self.log_window.append_text("Process termination timeout, sending SIGKILL")
                        try:
                            process_to_terminate.kill()
                            process_to_terminate.wait(timeout=1)
                        except (ProcessLookupError, OSError) as e:
                            self.log_window.append_text(f"Error force killing process: {e}")
                    except (ProcessLookupError, OSError) as e:
                        self.log_window.append_text(f"Error waiting for process: {e}")

                # Start waiting in a separate thread
                wait_thread = threading.Thread(target=wait_for_process)
                wait_thread.daemon = True
                wait_thread.start()
            except (ProcessLookupError, OSError) as e:
                self.log_window.append_text(f"Error terminating process: {e}")

            # Mark as closed after initiating termination
            self.process = None

        # Find and stop all whispex processes
        try:
            self.log_window.append_text("Searching for whispex.py processes...")
            output = subprocess.run(
                ["pgrep", "-f", "whispex.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            if output.returncode == 0:
                pids = output.stdout.strip().split()
                if pids:
                    self.log_window.append_text(
                        f"Found {len(pids)} Whispex processes: {pids}"
                    )
                    for pid in pids:
                        try:
                            pid_int = int(pid)
                            # Skip if it's our already terminated process
                            if process_to_terminate and pid_int == process_to_terminate.pid:
                                continue
                            os.kill(pid_int, signal.SIGTERM)
                            self.log_window.append_text(
                                f"Sent termination signal to process {pid}"
                            )
                        except ProcessLookupError:
                            self.log_window.append_text(
                                f"Process {pid} already terminated"
                            )
                        except OSError as e:
                            self.log_window.append_text(
                                f"Error terminating process {pid}: {e}"
                            )
                else:
                    self.log_window.append_text("No Whispex processes found")
            else:
                self.log_window.append_text("No Whispex processes found")
        except OSError as e:
            self.log_window.append_text(f"Error stopping Whispex processes: {e}")
            import traceback
            self.log_window.append_text(traceback.format_exc())

        # Update GUI state
        self.running = False
        self.start_remote_action.setEnabled(True)
        self.stop_action.setEnabled(False)
        self.log_window.update_status(running=False)
        self.log_window.append_text("Speech recognition service stopped")

    def show_log(self):
        self.log_window.show()
        self.log_window.raise_()

    def exit_app(self):
        self.log_window.append_text("Exiting application...")

        # Stop process if running
        self.stop_whisper()

        # Terminate application
        QtWidgets.QApplication.quit()

    def show_settings(self):
        """Shows settings dialog"""

        # Helper function for safe logging
        def log_message(message):
            if hasattr(self, "log_window") and self.log_window:
                self.log_window.append_text(message)
            else:
                log.info(message)

        log_message("⚙️ Opening settings dialog...")
        settings_dialog = SettingsDialog(self.settings_manager, self.parent_widget)
        if settings_dialog.exec_() == QtWidgets.QDialog.Accepted:
            # Settings are automatically saved when changed now
            log_message("✅ Settings applied successfully")

            # Re-check API status after settings changes
            self.check_api_status()

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
                if device.get('max_input_channels', 0) > 0:
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

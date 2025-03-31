#!/usr/bin/env python3
import os
import signal
import sys
import subprocess
import threading
import time
import json
from pathlib import Path
import tomli
from PyQt5 import QtWidgets, QtGui, QtCore

# Загрузка конфигурации из TOML
def get_config_path():
    # Путь к пользовательскому конфигу
    user_config_dir = Path.home() / ".config" / "whispex"
    user_config_path = user_config_dir / "config.toml"
    
    # Путь к конфигу по умолчанию в директории приложения
    script_dir = Path(__file__).parent
    default_config_path = script_dir / "default_config.toml"
    
    # Проверяем, существует ли пользовательский конфиг
    if user_config_path.exists():
        return user_config_path
    else:
        # Если нет пользовательского конфига, используем дефолтный
        if default_config_path.exists():
            return default_config_path
        else:
            print(f"Ошибка: Не найден файл конфигурации. Ни {user_config_path}, ни {default_config_path} не существуют.")
            return None

def load_config():
    config_path = get_config_path()
    if not config_path:
        return {}
    
    print(f"Загрузка конфигурации из: {config_path}")
    
    try:
        with open(config_path, "rb") as f:
            return tomli.load(f)
    except Exception as e:
        print(f"Ошибка при чтении конфигурации: {e}")
        return {}

# Загружаем настройки из TOML
config = load_config()

# Константы
DEFAULT_SETTINGS = {
    "temperature": 0.2,
    "prompt": """This is a transcription of a software developer speaking primarily in Russian, but frequently using English technical terms and phrases. The speaker is knowledgeable in computer science, software development, DevOps, and project management. They use technical jargon and industry terminology related to:
- Software development and programming
- System administration and DevOps
- Software architecture and design patterns
- Project management and requirements engineering
- Databases and data structures
- Algorithms and computational complexity
- Cloud technologies and infrastructure

When uncertain about a word or phrase, prioritize technical meaning over common usage. Preserve English technical terms even within Russian sentences. The speaker may switch between Russian and English mid-sentence when discussing technical concepts."""
}

class WhisperTrayIcon(QtWidgets.QSystemTrayIcon):
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Используем пользовательскую иконку
        script_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(script_dir, "whispex.png")
        
        if os.path.exists(icon_path):
            self.setIcon(QtGui.QIcon(icon_path))
        else:
            # Запасной вариант - системная иконка
            self.setIcon(QtGui.QIcon.fromTheme("audio-input-microphone"))
            print(f"Предупреждение: иконка не найдена по пути {icon_path}")
        
        # Сохраняем родительский виджет
        self.parent_widget = parent
        
        # Проверка аудио устройств будет после меню
        self.has_audio = False
        self.audio_devices = []
        
        # Создаем окно лога (до загрузки настроек, чтобы оно было доступно для логирования)
        self.log_window = LogWindow(self.parent_widget)
        # Устанавливаем обратную связь
        self.log_window.tray_icon = self
        
        # Создаем меню
        self.menu = QtWidgets.QMenu()
        
        # Добавляем действия для запуска
        self.start_remote_action = self.menu.addAction("Запустить")
        self.start_remote_action.triggered.connect(self.start_remote_whisper)
        
        # Добавляем действие для остановки
        self.stop_action = self.menu.addAction("Остановить")
        self.stop_action.triggered.connect(self.stop_whisper)
        self.stop_action.setEnabled(False)
        
        # Добавляем действие для просмотра лога
        self.log_action = self.menu.addAction("Показать лог")
        self.log_action.triggered.connect(self.show_log)
        
        # Добавляем действие для настроек
        self.settings_action = self.menu.addAction("Настройки")
        self.settings_action.triggered.connect(self.show_settings)
        
        # Добавляем разделитель
        self.menu.addSeparator()
        
        # Добавляем действие для выхода
        exit_action = self.menu.addAction("Выход")
        exit_action.triggered.connect(self.exit_app)
        
        # Устанавливаем меню
        self.setContextMenu(self.menu)
        
        # Инициализируем переменные для процесса
        self.process = None
        self.output_reader = None
        self.running = False
        
    def start_remote_whisper(self):
        if not self.running:
            self.start_whisper("dictation.py")
    
    def start_whisper(self, script_name):
        self.log_window.append_text(f"Запускаю {script_name}...")
        
        script_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Настраиваем окружение для запуска скрипта
        env = os.environ.copy()
        
        # Отключаем буферизацию Python вывода
        env['PYTHONUNBUFFERED'] = '1'
        
        try:
            # Выполняем скрипт через uv run
            self.log_window.append_text(f"Выполняю скрипт через uv: {script_name}")
            
            # Запускаем скрипт с помощью uv run @uv
            command = ["uv", "run", "@uv", script_name]
            
            self.log_window.append_text(f"Команда запуска: {' '.join(command)}")
            
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                universal_newlines=True,
                bufsize=1,
                env=env,
                cwd=script_dir  # Важно! Запускаем в директории проекта
            )
            
            # Обновляем состояние GUI
            self.running = True
            self.start_remote_action.setEnabled(False)
            self.stop_action.setEnabled(True)
            
            # Запускаем поток для чтения вывода
            self.output_reader = threading.Thread(target=self.read_output)
            self.output_reader.daemon = True
            self.output_reader.start()
            
            # Показываем уведомление
            self.showMessage(
                "Whispex", 
                "Сервис распознавания речи запущен", 
                QtGui.QIcon(os.path.join(script_dir, "whispex.png")) if os.path.exists(os.path.join(script_dir, "whispex.png")) else QtGui.QIcon.fromTheme("audio-input-microphone"), 
                3000
            )
            
        except Exception as e:
            self.log_window.append_text(f"Ошибка запуска: {str(e)}")
            # Печатаем стек-трейс для отладки
            import traceback
            self.log_window.append_text(traceback.format_exc())
    
    def read_output(self):
        try:
            if self.process:
                # Создаем потоки для чтения stdout и stderr
                stdout_thread = threading.Thread(target=self.read_stream, 
                                               args=(self.process.stdout, "STDOUT"))
                stderr_thread = threading.Thread(target=self.read_stream, 
                                               args=(self.process.stderr, "STDERR"))
                
                # Запускаем потоки
                stdout_thread.daemon = True
                stderr_thread.daemon = True
                stdout_thread.start()
                stderr_thread.start()
                
                # Явно сообщаем пользователю, что ожидаем ввода
                QtCore.QMetaObject.invokeMethod(
                    self.log_window, 
                    "append_text", 
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, "\n🎤 Процесс запущен. Нажмите и удерживайте Alt_R для записи речи.")
                )
                
                # Отслеживаем поток выполнения не блокируя основной поток
                monitor_thread = threading.Thread(target=self.monitor_process)
                monitor_thread.daemon = True
                monitor_thread.start()
                
                return
            else:
                QtCore.QMetaObject.invokeMethod(
                    self.log_window, 
                    "append_text", 
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, "ОШИБКА: Процесс недоступен")
                )
        except Exception as e:
            QtCore.QMetaObject.invokeMethod(
                self.log_window, 
                "append_text", 
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, f"ОШИБКА чтения вывода: {str(e)}")
            )
            import traceback
            QtCore.QMetaObject.invokeMethod(
                self.log_window, 
                "append_text", 
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, traceback.format_exc())
            )
            
        # В случае ошибки, мы все равно ждем завершения процесса
        QtCore.QMetaObject.invokeMethod(
            self, 
            "process_finished", 
            QtCore.Qt.QueuedConnection
        )
            
    def monitor_process(self):
        """Отслеживает процесс и вызывает обновление GUI при его завершении."""
        if self.process:
            exit_code = self.process.wait()
            QtCore.QMetaObject.invokeMethod(
                self.log_window, 
                "append_text", 
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, f"Процесс завершился с кодом: {exit_code}")
            )
            
            # Обновляем GUI в главном потоке
            QtCore.QMetaObject.invokeMethod(
                self, 
                "process_finished", 
                QtCore.Qt.QueuedConnection
            )
    
    def read_stream(self, stream, name):
        """Читает поток (stdout или stderr) и отправляет данные в окно лога."""
        try:
            # Установим небуферизованное чтение для потока
            os.set_blocking(stream.fileno(), False)
            
            while self.process and self.process.poll() is None:
                # Читаем доступные данные без блокировки
                line = stream.readline()
                if line:
                    # Добавляем префикс к строке в зависимости от потока
                    prefix = "[ERR] " if name == "STDERR" else ""
                    # Отправляем строку в GUI поток
                    line_text = f"{prefix}{line.strip()}"
                    QtCore.QMetaObject.invokeMethod(
                        self.log_window, 
                        "append_text", 
                        QtCore.Qt.QueuedConnection,
                        QtCore.Q_ARG(str, line_text)
                    )
                else:
                    # Если нет новых данных, даем процессору отдохнуть
                    QtCore.QThread.msleep(50)
            
            # Вычитываем оставшиеся данные после завершения процесса
            for line in stream:
                if line:
                    prefix = "[ERR] " if name == "STDERR" else ""
                    line_text = f"{prefix}{line.strip()}"
                    QtCore.QMetaObject.invokeMethod(
                        self.log_window, 
                        "append_text", 
                        QtCore.Qt.QueuedConnection,
                        QtCore.Q_ARG(str, line_text)
                    )
        except Exception as e:
            QtCore.QMetaObject.invokeMethod(
                self.log_window, 
                "append_text", 
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, f"ОШИБКА чтения потока {name}: {str(e)}")
            )
    
    @QtCore.pyqtSlot()
    def process_finished(self):
        self.running = False
        self.start_remote_action.setEnabled(True)
        self.stop_action.setEnabled(False)
        self.log_window.append_text("Процесс завершен.")
    
    def stop_whisper(self):
        if self.process and self.running:
            try:
                self.log_window.append_text("Останавливаю процесс...")
                
                # Получаем ID всех дочерних процессов перед завершением основного
                try:
                    # Находим все дочерние процессы
                    child_pids = []
                    parent_pid = self.process.pid
                    ps_command = subprocess.run(
                        ["pgrep", "-P", str(parent_pid)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True
                    )
                    if ps_command.returncode == 0:
                        child_pids = [int(pid) for pid in ps_command.stdout.strip().split()]
                        self.log_window.append_text(f"Найдены дочерние процессы: {child_pids}")
                except Exception as e:
                    self.log_window.append_text(f"Ошибка при поиске дочерних процессов: {str(e)}")
                
                # Отправляем SIGTERM главному процессу и даем ему шанс корректно завершиться
                os.kill(self.process.pid, signal.SIGTERM)
                
                # Ждем небольшое время для корректного завершения
                max_wait = 3  # максимальное время ожидания в секундах
                for _ in range(max_wait * 10):  # проверяем каждые 100 мс
                    if self.process.poll() is not None:  # процесс завершился
                        self.log_window.append_text(f"Процесс успешно завершен с кодом: {self.process.returncode}")
                        break
                    time.sleep(0.1)
                
                # Если процесс не завершился, принудительно завершаем его
                if self.process.poll() is None:
                    self.log_window.append_text("Процесс не завершился корректно, принудительное завершение...")
                    os.kill(self.process.pid, signal.SIGKILL)
                    self.log_window.append_text("Процесс принудительно завершен")
                
                # Проверяем и убиваем все дочерние процессы, если они остались
                for pid in child_pids:
                    try:
                        # Проверяем, существует ли процесс
                        os.kill(pid, 0)  # 0 - просто проверка наличия процесса
                        # Если процесс существует, принудительно завершаем его
                        self.log_window.append_text(f"Принудительно завершаем дочерний процесс {pid}")
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        # Процесс уже не существует
                        pass
                
                # GUI обновляется в process_finished после завершения процесса
                # Принудительно вызываем обработку завершения, если функция process_finished еще не сработала
                if self.running:
                    self.process_finished()
                
            except Exception as e:
                self.log_window.append_text(f"Ошибка при остановке: {str(e)}")
                import traceback
                self.log_window.append_text(traceback.format_exc())
    
    def show_log(self):
        self.log_window.show()
        self.log_window.raise_()
    
    def exit_app(self):
        self.log_window.append_text("Завершение приложения...")
        
        # Останавливаем процесс, если он запущен
        self.stop_whisper()
        
        # Дополнительная проверка и завершение оставшихся процессов Python
        try:
            # Находим все процессы Python, связанные с нашим скриптом dictation.py
            ps_command = subprocess.run(
                ["pgrep", "-f", "dictation.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            if ps_command.returncode == 0:
                leftover_pids = [int(pid) for pid in ps_command.stdout.strip().split()]
                self.log_window.append_text(f"Найдены оставшиеся процессы dictation.py: {leftover_pids}")
                
                # Принудительно завершаем оставшиеся процессы
                for pid in leftover_pids:
                    try:
                        if pid != os.getpid():  # Не убиваем наш собственный процесс
                            self.log_window.append_text(f"Принудительно завершаем процесс {pid}")
                            os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
        except Exception as e:
            self.log_window.append_text(f"Ошибка при завершении оставшихся процессов: {str(e)}")
        
        # Завершаем приложение
        QtWidgets.QApplication.quit()

    def check_audio_devices(self):
        """Проверяет доступность аудио устройств используя uv run"""
        try:
            # Запускаем скрипт для получения аудио устройств
            check_script = """
import json
import sounddevice as sd
print(json.dumps(sd.query_devices()))
"""
            proc = subprocess.Popen(
                ["uv", "run", "@uv", "-c", check_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            stdout, stderr = proc.communicate()
            
            if proc.returncode == 0:
                import json
                self.audio_devices = json.loads(stdout)
                self.has_audio = True
                self.log_window.append_text("✅ Доступ к аудио устройствам получен")
                
                # Отображаем информацию об устройствах
                input_devices = [d for d in self.audio_devices if d.get('max_input_channels', 0) > 0]
                if input_devices:
                    self.log_window.append_text(f"✅ Найдено {len(input_devices)} аудио устройств для записи")
                    for i, device in enumerate(input_devices):
                        self.log_window.append_text(f"    {i+1}. {device.get('name', 'Неизвестное устройство')}")
                else:
                    self.log_window.append_text("⚠️ Не найдено устройств для записи аудио. Проверьте микрофон.")
            else:
                self.has_audio = False
                self.audio_error = stderr
                self.log_window.append_text(f"❌ Ошибка доступа к аудио: {stderr}")
        except Exception as e:
            self.has_audio = False
            self.audio_error = str(e)
            self.log_window.append_text(f"❌ Исключение при проверке аудио: {str(e)}")

    def load_settings(self):
        """Загружает настройки из файла или возвращает значения по умолчанию"""
        # Вспомогательная функция для безопасного логирования
        def log_message(message):
            if hasattr(self, 'log_window') and self.log_window:
                self.log_window.append_text(message)
            else:
                print(message)
        
        try:
            if os.path.exists(self.settings_path):
                try:
                    with open(self.settings_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        settings = json.loads(content)
                except UnicodeDecodeError:
                    # Пробуем с другой кодировкой, если utf-8 не сработал
                    with open(self.settings_path, 'r', encoding='latin-1') as f:
                        content = f.read()
                        settings = json.loads(content)
                        log_message("⚠️ Файл настроек был прочитан с использованием альтернативной кодировки")
                
                # Проверяем, что все необходимые ключи присутствуют
                for key, value in DEFAULT_SETTINGS.items():
                    if key not in settings:
                        settings[key] = value
                log_message(f"✅ Настройки загружены из {self.settings_path}")
                log_message(f"   Температура: {settings.get('temperature', 0.2)}")
                log_message(f"   Длина промпта: {len(settings.get('prompt', ''))}")
                return settings
            else:
                log_message(f"⚠️ Файл настроек не найден: {self.settings_path}")
        except json.JSONDecodeError as je:
            log_message(f"❌ Ошибка формата JSON в файле настроек: {str(je)}")
            log_message(f"   Файл настроек будет переименован и создан новый")
            # Если файл поврежден, переименовываем его и создаем новый
            backup_path = f"{self.settings_path}.bak.{int(time.time())}"
            try:
                os.rename(self.settings_path, backup_path)
                log_message(f"✅ Резервная копия сохранена: {backup_path}")
            except Exception as e:
                log_message(f"❌ Не удалось создать резервную копию: {str(e)}")
        except Exception as e:
            log_message(f"❌ Ошибка при загрузке настроек: {str(e)}")
            import traceback
            log_message(traceback.format_exc())
        
        # Возвращаем настройки по умолчанию в случае ошибки
        log_message(f"ℹ️ Используются настройки по умолчанию")
        return DEFAULT_SETTINGS.copy()
    
    def save_settings(self):
        """Сохраняет настройки в файл"""
        # Вспомогательная функция для безопасного логирования
        def log_message(message):
            if hasattr(self, 'log_window') and self.log_window:
                self.log_window.append_text(message)
            else:
                print(message)
                
        try:
            # Проверяем права доступа к директории
            settings_dir = os.path.dirname(self.settings_path)
            if not os.access(settings_dir, os.W_OK):
                log_message(f"❌ Нет прав на запись в директорию: {settings_dir}")
                return False
                
            # Сначала создаем временный файл для безопасного сохранения
            temp_path = f"{self.settings_path}.tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json_str = json.dumps(self.settings, ensure_ascii=False, indent=4)
                f.write(json_str)
            
            # Если временный файл успешно создан, переименовываем его
            os.replace(temp_path, self.settings_path)
            
            log_message(f"✅ Настройки сохранены в файл: {self.settings_path}")
            log_message(f"   Температура: {self.settings.get('temperature', 0.2)}")
            log_message(f"   Длина промпта: {len(self.settings.get('prompt', ''))}")
            return True
        except Exception as e:
            log_message(f"❌ Ошибка при сохранении настроек: {str(e)}")
            import traceback
            log_message(traceback.format_exc())
            return False
            
    def show_settings(self):
        """Показывает диалог настроек"""
        # Вспомогательная функция для безопасного логирования
        def log_message(message):
            if hasattr(self, 'log_window') and self.log_window:
                self.log_window.append_text(message)
            else:
                print(message)
                
        log_message("⚙️ Открываю диалог настроек...")
        settings_dialog = SettingsDialog(self.settings, self.parent_widget)
        if settings_dialog.exec_() == QtWidgets.QDialog.Accepted:
            # Обновляем настройки
            old_settings = self.settings.copy()
            self.settings = settings_dialog.get_settings()
            
            # Выводим информацию о новых настройках
            log_message(f"ℹ️ Новые настройки:")
            log_message(f"   Температура: {self.settings.get('temperature', 0.2)}")
            log_message(f"   Длина промпта: {len(self.settings.get('prompt', ''))}")
            
            # Сохраняем в файл
            if self.save_settings():
                log_message("✅ Настройки сохранены успешно")
                
                # Если процесс уже запущен, перезапускаем его с новыми настройками
                if self.running:
                    log_message("🔄 Перезапуск процесса с новыми настройками...")
                    # Останавливаем текущий процесс
                    self.stop_whisper()
                    # Запускаем процесс с новыми настройками
                    self.start_remote_whisper()
            else:
                log_message("❌ Не удалось сохранить настройки")

class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings.copy()
        self.setWindowTitle("Настройки Whispex")
        self.resize(700, 500)
        
        # Устанавливаем иконку для окна настроек
        script_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(script_dir, "whispex.png")
        
        if os.path.exists(icon_path):
            self.setWindowIcon(QtGui.QIcon(icon_path))
        
        # Создаем виджеты
        layout = QtWidgets.QVBoxLayout()
        
        # Температура
        temp_layout = QtWidgets.QHBoxLayout()
        temp_label = QtWidgets.QLabel("Температура:")
        self.temp_spinbox = QtWidgets.QDoubleSpinBox()
        self.temp_spinbox.setMinimum(0.0)
        self.temp_spinbox.setMaximum(1.0)
        self.temp_spinbox.setSingleStep(0.1)
        self.temp_spinbox.setValue(settings.get('temperature', 0.2))
        self.temp_spinbox.setToolTip("Значение от 0.0 до 1.0. Меньшие значения делают вывод более детерминированным.")
        temp_layout.addWidget(temp_label)
        temp_layout.addWidget(self.temp_spinbox)
        layout.addLayout(temp_layout)
        
        # Промпт
        prompt_label = QtWidgets.QLabel("Промпт для Whisper:")
        layout.addWidget(prompt_label)
        
        self.prompt_text = QtWidgets.QTextEdit()
        self.prompt_text.setPlainText(settings.get('prompt', DEFAULT_SETTINGS['prompt']))
        layout.addWidget(self.prompt_text)
        
        # Кнопки
        button_layout = QtWidgets.QHBoxLayout()
        
        reset_button = QtWidgets.QPushButton("Сбросить настройки")
        reset_button.clicked.connect(self.reset_settings)
        
        apply_button = QtWidgets.QPushButton("Применить")
        apply_button.clicked.connect(self.accept)
        
        cancel_button = QtWidgets.QPushButton("Отмена")
        cancel_button.clicked.connect(self.reject)
        
        button_layout.addWidget(reset_button)
        button_layout.addStretch()
        button_layout.addWidget(cancel_button)
        button_layout.addWidget(apply_button)
        
        layout.addLayout(button_layout)
        self.setLayout(layout)
    
    def reset_settings(self):
        """Сбрасывает настройки к значениям по умолчанию"""
        self.temp_spinbox.setValue(DEFAULT_SETTINGS.get('temperature', 0.2))
        self.prompt_text.setPlainText(DEFAULT_SETTINGS.get('prompt', ''))
    
    def get_settings(self):
        """Возвращает текущие настройки из диалога"""
        settings = self.settings.copy()
        
        # Получаем и проверяем температуру
        temperature = self.temp_spinbox.value()
        settings['temperature'] = temperature
        
        # Получаем и проверяем промпт
        prompt = self.prompt_text.toPlainText()
        # Проверяем, не пустой ли промпт
        if not prompt.strip():
            prompt = DEFAULT_SETTINGS['prompt']
            print(f"ВНИМАНИЕ: Промпт был пустым, использован промпт по умолчанию")
        settings['prompt'] = prompt
        
        # Выводим отладочную информацию
        print(f"DEBUG: get_settings -> temperature={temperature}, prompt_length={len(prompt)}")
        
        return settings

class LogWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Whispex")
        self.resize(700, 500)
        
        # Устанавливаем иконку для окна лога
        script_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(script_dir, "whispex.png")
        
        if os.path.exists(icon_path):
            self.setWindowIcon(QtGui.QIcon(icon_path))
        
        # Инициализируем ссылку на tray_icon
        self.tray_icon = None
        
        # Создаем текстовый виджет для отображения лога
        self.log_text = QtWidgets.QTextEdit()
        self.log_text.setReadOnly(True)
        
        # Создаем кнопки
        button_layout = QtWidgets.QHBoxLayout()
        
        clear_button = QtWidgets.QPushButton("Очистить лог")
        clear_button.clicked.connect(self.clear_log)
        button_layout.addWidget(clear_button)
        
        check_audio_button = QtWidgets.QPushButton("Проверить аудио")
        check_audio_button.clicked.connect(self.parent_check_audio)
        button_layout.addWidget(check_audio_button)
        
        # Добавляем кнопку настроек
        settings_button = QtWidgets.QPushButton("Настройки")
        settings_button.clicked.connect(self.show_settings)
        button_layout.addWidget(settings_button)
        
        # Добавляем кнопки управления
        start_button = QtWidgets.QPushButton("Запустить")
        start_button.clicked.connect(self.start_service)
        button_layout.addWidget(start_button)
        
        stop_button = QtWidgets.QPushButton("Остановить")
        stop_button.clicked.connect(self.stop_service)
        button_layout.addWidget(stop_button)
        
        # Создаем компоновку
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.log_text)
        layout.addLayout(button_layout)
        self.setLayout(layout)
    
    def parent_check_audio(self):
        # Обращаемся к tray_icon вместо parent
        if hasattr(self, 'tray_icon') and self.tray_icon and hasattr(self.tray_icon, 'check_audio_devices'):
            self.append_text("🔍 Повторная проверка аудио устройств...")
            self.tray_icon.check_audio_devices()
    
    def start_service(self):
        # Запускаем сервис через tray_icon
        if hasattr(self, 'tray_icon') and self.tray_icon and hasattr(self.tray_icon, 'start_remote_whisper'):
            self.append_text("🚀 Запуск службы распознавания...")
            self.tray_icon.start_remote_whisper()
    
    def stop_service(self):
        # Останавливаем сервис через tray_icon
        if hasattr(self, 'tray_icon') and self.tray_icon and hasattr(self.tray_icon, 'stop_whisper'):
            self.append_text("🛑 Остановка службы распознавания...")
            self.tray_icon.stop_whisper()
    
    def show_settings(self):
        # Показываем настройки через tray_icon
        if hasattr(self, 'tray_icon') and self.tray_icon and hasattr(self.tray_icon, 'show_settings'):
            self.append_text("⚙️ Открываю настройки...")
            self.tray_icon.show_settings()
    
    @QtCore.pyqtSlot(str)
    def append_text(self, text):
        self.log_text.append(text)
        # Прокручиваем вниз
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def clear_log(self):
        self.log_text.clear()

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # Не закрывать приложение при закрытии окон
    
    # Создаем невидимое главное окно для поддержки работы в трее
    main_widget = QtWidgets.QWidget()
    
    tray_icon = WhisperTrayIcon(main_widget)
    tray_icon.show()
    
    # Показываем окно лога при запуске для отображения статуса
    tray_icon.log_window.show()
    tray_icon.log_window.append_text("ℹ️ Используется uv для запуска Python-скриптов")
    
    # Проверяем аудио устройства
    tray_icon.check_audio_devices()
    
    # Показываем сообщение при запуске
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whispex.png")
    notification_icon = QtGui.QIcon(icon_path) if os.path.exists(icon_path) else QtGui.QIcon.fromTheme("audio-input-microphone")
    
    tray_icon.showMessage(
        "Whispex", 
        "Приложение запущено в системном трее", 
        notification_icon, 
        3000
    )
    
    # Автоматически запускаем службу распознавания после загрузки приложения
    QtCore.QTimer.singleShot(1000, lambda: tray_icon.start_remote_whisper())
    
    sys.exit(app.exec_()) 
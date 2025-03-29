#!/usr/bin/env python3
import os
import signal
import sys
import subprocess
import threading
import time
from PyQt5 import QtWidgets, QtGui, QtCore

class WhisperTrayIcon(QtWidgets.QSystemTrayIcon):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIcon(QtGui.QIcon.fromTheme("audio-input-microphone"))
        
        # Сохраняем родительский виджет
        self.parent_widget = parent
        
        # Проверяем наличие виртуального окружения
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.venv_python = os.path.join(script_dir, "venv/bin/python3")
        self.venv_pip = os.path.join(script_dir, "venv/bin/pip")
        self.has_venv = os.path.exists(self.venv_python)
        
        # Проверка аудио устройств будет после меню
        self.has_audio = False
        self.audio_devices = []
        
        # Создаем окно лога (до меню, чтобы оно было доступно)
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
            self.start_whisper("run_dictation_remote.sh")
    
    def start_whisper(self, script_name):
        self.log_window.append_text(f"Запускаю {script_name}...")
        
        script_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(script_dir, script_name)
        
        # Настраиваем окружение для запуска скрипта
        env = os.environ.copy()
        
        # Отключаем буферизацию Python вывода
        env['PYTHONUNBUFFERED'] = '1'
        
        # Если это удаленный скрипт, добавляем OPENAI_API_KEY из файла
        if 'remote' in script_name:
            try:
                openai_token_path = os.path.expanduser("~/.config/openai.token")
                if os.path.exists(openai_token_path):
                    with open(openai_token_path, 'r') as token_file:
                        env['OPENAI_API_KEY'] = token_file.read().strip()
                        self.log_window.append_text(f"OpenAI API ключ загружен")
                else:
                    self.log_window.append_text(f"ОШИБКА: Файл OpenAI API ключа не найден: {openai_token_path}")
            except Exception as e:
                self.log_window.append_text(f"ОШИБКА при чтении OpenAI API ключа: {str(e)}")
        
        try:
            # Запускаем через bash для правильной обработки переменных окружения и shell-специфичных команд
            self.log_window.append_text(f"Выполняю bash скрипт: {script_path}")
            

            
            # Добавляем язык напрямую в аргументы скрипта
            command = ["/bin/bash", script_path, "ru"]
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
            self.showMessage("Whisper Dictation", "Сервис распознавания речи запущен", QtGui.QIcon.fromTheme("audio-input-microphone"), 3000)
            
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

    def check_and_install_dependencies(self):
        """Проверяет и устанавливает недостающие зависимости в venv."""
        
        if not self.has_venv:
            self.log_window.append_text("❌ Виртуальное окружение не найдено, не могу установить зависимости")
            return False
        
        required_packages = ['sounddevice', 'openai', 'numpy']
        missing_packages = []
        
        # Проверяем каждый пакет
        for package in required_packages:
            try:
                # Пытаемся импортировать пакет через venv python
                proc = subprocess.Popen(
                    [self.venv_python, "-c", f"import {package}"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True
                )
                _, stderr = proc.communicate()
                if proc.returncode != 0:
                    self.log_window.append_text(f"⚠️ Пакет {package} не найден в venv")
                    missing_packages.append(package)
                else:
                    self.log_window.append_text(f"✅ Пакет {package} найден")
            except Exception as e:
                self.log_window.append_text(f"❌ Ошибка при проверке пакета {package}: {str(e)}")
                missing_packages.append(package)
        
        # Устанавливаем недостающие пакеты
        if missing_packages:
            self.log_window.append_text(f"Устанавливаю недостающие пакеты: {', '.join(missing_packages)}")
            for package in missing_packages:
                try:
                    self.log_window.append_text(f"📦 Установка {package}...")
                    proc = subprocess.Popen(
                        [self.venv_pip, "install", package],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True
                    )
                    stdout, stderr = proc.communicate()
                    if proc.returncode == 0:
                        self.log_window.append_text(f"✅ Пакет {package} успешно установлен")
                    else:
                        self.log_window.append_text(f"❌ Ошибка установки {package}: {stderr}")
                except Exception as e:
                    self.log_window.append_text(f"❌ Исключение при установке {package}: {str(e)}")
        
        # Проверяем аудио после всех установок
        self.check_audio_devices()
        return len(missing_packages) == 0
    
    def check_audio_devices(self):
        """Проверяет доступность аудио устройств используя venv."""
        if not self.has_venv:
            self.log_window.append_text("❌ Виртуальное окружение не найдено, не могу проверить аудио")
            self.has_audio = False
            return
        
        try:
            # Запускаем скрипт для получения аудио устройств
            check_script = """
import json
import sounddevice as sd
print(json.dumps(sd.query_devices()))
"""
            proc = subprocess.Popen(
                [self.venv_python, "-c", check_script],
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
            else:
                self.has_audio = False
                self.audio_error = stderr
                self.log_window.append_text(f"❌ Ошибка доступа к аудио: {stderr}")
        except Exception as e:
            self.has_audio = False
            self.audio_error = str(e)
            self.log_window.append_text(f"❌ Исключение при проверке аудио: {str(e)}")

class LogWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Лог Whisper Dictation")
        self.resize(700, 500)
        
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
        
        check_deps_button = QtWidgets.QPushButton("Проверить зависимости")
        check_deps_button.clicked.connect(self.parent_check_deps)
        button_layout.addWidget(check_deps_button)
        
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
    
    def parent_check_deps(self):
        # Обращаемся к tray_icon вместо parent
        if hasattr(self, 'tray_icon') and self.tray_icon and hasattr(self.tray_icon, 'check_and_install_dependencies'):
            self.append_text("🔍 Повторная проверка зависимостей...")
            self.tray_icon.check_and_install_dependencies()
    
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
    if tray_icon.has_venv:
        tray_icon.log_window.append_text("✅ Виртуальное окружение найдено: " + tray_icon.venv_python)
        # Проверяем и устанавливаем зависимости
        tray_icon.log_window.append_text("🔍 Проверка необходимых зависимостей...")
        tray_icon.check_and_install_dependencies()
    else:
        tray_icon.log_window.append_text("❌ ОШИБКА: Виртуальное окружение не найдено!")
        tray_icon.log_window.append_text("    Необходимо для работы: " + os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv/bin/python3"))
    
    # Проверяем аудио устройства
    if tray_icon.has_audio:
        input_devices = [d for d in tray_icon.audio_devices if d.get('max_input_channels', 0) > 0]
        if input_devices:
            tray_icon.log_window.append_text(f"✅ Найдено {len(input_devices)} аудио устройств для записи")
            for i, device in enumerate(input_devices):
                tray_icon.log_window.append_text(f"    {i+1}. {device.get('name', 'Неизвестное устройство')}")
        else:
            tray_icon.log_window.append_text("⚠️ Не найдено устройств для записи аудио. Проверьте микрофон.")
    else:
        tray_icon.log_window.append_text("⚠️ Аудио устройства не обнаружены. Это нормально если они будут доступны скрипту dictation.py.")
        if hasattr(tray_icon, 'audio_error'):
            tray_icon.log_window.append_text(f"    Детали ошибки: {tray_icon.audio_error}")
    
    # Показываем сообщение при запуске
    tray_icon.showMessage(
        "Whisper Dictation", 
        "Приложение запущено в системном трее", 
        QtGui.QIcon.fromTheme("audio-input-microphone"), 
        3000
    )
    
    # Автоматически запускаем службу распознавания после загрузки приложения
    QtCore.QTimer.singleShot(1000, lambda: tray_icon.start_remote_whisper())
    
    sys.exit(app.exec_()) 
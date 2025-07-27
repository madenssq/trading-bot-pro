# Plik: main.py (WERSJA FINALNA I POPRAWIONA)

import os
import sys
import platform
import asyncio
import logging
import logging.handlers
import traceback

os.environ['QTWEBENGINE_REMOTE_DEBUGGING'] = "9222"

# --- GLOBALNA FLAGA LOGOWANIA ---
_logging_configured = False

def configure_qt_environment():
    """Konfiguruje ścieżki DLL dla środowiska Qt w systemie Windows."""
    if platform.system() != "Windows":
        return
    try:
        if hasattr(sys, 'prefix'):
            site_packages_path = os.path.join(sys.prefix, 'Lib', 'site-packages')
            pyqt6_qt6_path = os.path.join(site_packages_path, 'PyQt6', 'Qt6', 'bin')
            if os.path.isdir(pyqt6_qt6_path) and hasattr(os, 'add_dll_directory'):
                os.add_dll_directory(pyqt6_qt6_path)
    except Exception as e:
        print(f"Ostrzeżenie podczas konfiguracji środowiska Qt: {e}")

configure_qt_environment()

from PyQt6.QtWidgets import QApplication
from qasync import QEventLoop

from app_config import DATA_DIR
from core.settings_manager import SettingsManager
from ui.main_window import MainWindow, QtLogHandler

LOGS_DIR = os.path.join(DATA_DIR, "logs")
CONFIG_DIR = os.path.join(DATA_DIR, "config")
LOG_FILE = os.path.join(LOGS_DIR, "trading_bot.log")

def create_directories():
    """Tworzy niezbędne foldery, jeśli nie istnieją."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    os.makedirs(CONFIG_DIR, exist_ok=True)

def configure_logging(settings_manager: SettingsManager):
    """Konfiguruje podstawowe logowanie do pliku i konsoli."""
    global _logging_configured
    if _logging_configured:
        return

    create_directories()
    log_config = settings_manager.get('logging', {})
    app_log_level = log_config.get("level", "INFO").upper()
    formatter = logging.Formatter(log_config.get("format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    
    app_logger = logging.getLogger()
    if app_logger.hasHandlers():
        app_logger.handlers.clear()
    app_logger.setLevel(app_log_level)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=log_config.get("max_size_mb", 10) * 1024 * 1024,
        backupCount=log_config.get("backup_count", 3),
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    app_logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    app_logger.addHandler(stream_handler)
    
    logging.info("Podstawowe logowanie (plik, konsola) zostało skonfigurowane. Poziom logów: %s", app_log_level)
    _logging_configured = True

def add_ui_log_handler(window_instance):
    """Dodaje handler logowania do widgetu w UI."""
    if not window_instance: return
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    qt_handler = QtLogHandler(window_instance)
    qt_handler.setFormatter(formatter)
    logging.getLogger().addHandler(qt_handler)
    logging.info("Handler logowania dla UI został dodany.")

def handle_exception(exc_type, exc_value, exc_traceback):
    """Globalna obsługa nieprzechwyconych wyjątków."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.critical("Nieobsłużony wyjątek! Aplikacja zostanie zamknięta.", exc_info=(exc_type, exc_value, exc_traceback))

def main():
    """Główna funkcja uruchamiająca aplikację."""
    sys.excepthook = handle_exception

    try:
        settings_manager = SettingsManager()
        configure_logging(settings_manager)

        app = QApplication(sys.argv)
        loop = QEventLoop(app)
        asyncio.set_event_loop(loop)

        window = MainWindow(settings_manager)
        add_ui_log_handler(window)

        window.setWindowTitle("BotradingowyMADENSS")
        window.showMaximized()

        with loop:
            logging.info("Aplikacja pomyślnie uruchomiona. Pętla zdarzeń aktywna.")
            loop.run_forever()

    except Exception as e:
        logging.critical(f"Krytyczny błąd podczas uruchamiania aplikacji: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logging.info("Zamykanie aplikacji.")

if __name__ == "__main__":
    main()
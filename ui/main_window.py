# Plik: ui/main_window.py (WERSJA Z PRZYCISKAMI KONTROLI SKANERA)

import asyncio
import logging
import os
import sys
import re
import json
import pandas as pd
import pyqtgraph as pg
from app_config import PROJECT_ROOT
from typing import Dict, Any, Tuple, Optional, List
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QUrl
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QTextBrowser, QTabWidget,
                             QStatusBar, QMessageBox, QCheckBox, QComboBox, QPushButton, QTableWidget,
                             QHeaderView, QTableWidgetItem, QSizePolicy)
from PyQt6.QtGui import QPixmap, QMovie, QFont, QColor
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings

from core.settings_manager import SettingsManager
from core.core_services import CoreServices
from ui.styles import get_theme_stylesheet, THEMES

# Importy poszczególnych zakładek
from ui.analysis_tab import AnalysisTab
from ui.journal_tab import JournalTab
from ui.alerts_tab import AlertsTab
from ui.watched_tab import WatchedTab
from ui.backtester_tab import BacktesterTab
from ui.settings_tab import SettingsTab

logger = logging.getLogger(__name__)

# --- KLASY POMOCNICZE (bez zmian) ---
class StatusWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.animation_label = QLabel()
        self.animation_label.setMaximumHeight(60)
        gif_path = os.path.join(PROJECT_ROOT, 'assets', 'loading.gif')
        self.movie = QMovie(gif_path)
        self.animation_label.setMovie(self.movie)
        self.animation_label.setScaledContents(True)
        self.animation_label.setFixedSize(100, 100)
        self.layout.addWidget(self.animation_label)
        self.status_label = QLabel("Inicjalizacja...")
        font = QFont(); font.setPointSize(11)
        self.status_label.setFont(font)
        self.layout.addWidget(self.status_label)
        self.set_status("Czuwanie...", False)

    def set_status(self, text: str, is_busy: bool):
        self.status_label.setText(text)
        if is_busy:
            self.animation_label.show(); self.movie.start()
        else:
            self.movie.stop(); self.animation_label.hide()

class QtLogHandler(logging.Handler):
    def __init__(self, parent):
        super().__init__(); self.parent = parent
    def emit(self, record):
        self.parent.log_signal.emit(self.format(record))


class MainWindow(QMainWindow):
    log_signal = pyqtSignal(str)

    def __init__(self, settings_manager: SettingsManager):
        super().__init__()
        self.setObjectName("MainWindow")
        self.settings_manager = settings_manager
        self._analysis_lock = asyncio.Lock()
        self._is_shutting_down = False
        self.banner_pixmap = None

        try:
            self.services = CoreServices(settings_manager, self._analysis_lock)
        except Exception as e:
            QMessageBox.critical(self, "Błąd Krytyczny Inicjalizacji", f"Nie udało się zainicjalizować serwisów rdzenia: {e}")
            return

        self.services.ssnedam.status_update_callback = self.update_main_status
        self.services.ssnedam.queue_update_callback = self.update_queue_status_label

        self._init_tabs()
        self._setup_ui()
        self._connect_signals()
        self._init_timers()
        QTimer.singleShot(0, self.apply_styles)
        QTimer.singleShot(500, lambda: asyncio.create_task(self.app_startup_sequence()))

    def _init_tabs(self):
        """Tworzy instancje wszystkich zakładek, przekazując im potrzebne serwisy."""
        self.analysis_tab = AnalysisTab(
            settings_manager=self.settings_manager, 
            analyzer=self.services.analyzer, 
            db_manager=self.services.db_manager
        )
        self.settings_tab = SettingsTab(
            settings_manager=self.settings_manager, 
            coin_manager=self.services.coin_manager, 
            ai_client=self.services.ai_client
        )
        self.backtester_tab = BacktesterTab(
            settings_manager=self.settings_manager, 
            analyzer=self.services.analyzer, 
            main_window=self
        )
        self.alerts_tab = AlertsTab(
            settings_manager=self.settings_manager, 
            analyzer=self.services.analyzer, 
            db_manager=self.services.db_manager
        )
        self.journal_tab = JournalTab(
            db_manager=self.services.db_manager, 
            analyzer=self.services.analyzer, 
            settings_manager=self.settings_manager
        )
        self.watched_tab = WatchedTab(
            db_manager=self.services.db_manager, 
            settings_manager=self.settings_manager, 
            analyzer=self.services.analyzer
        )


    def _setup_ui(self):
        """Buduje główny interfejs użytkownika."""
        self.setCentralWidget(QWidget())
        main_layout = QVBoxLayout(self.centralWidget())
        main_layout.setContentsMargins(10, 10, 10, 10); main_layout.setSpacing(10)

        # --- Górny panel (NOWY UKŁAD) ---
        top_layout = QHBoxLayout()
        self.banner_label = QLabel(); self.banner_label.setMaximumSize(500, 100)
        self.banner_label.setScaledContents(True) 
        self.status_widget = StatusWidget()
        self.log_widget_container = QWidget()

        log_layout = QVBoxLayout(self.log_widget_container); log_layout.setContentsMargins(0,0,0,0)
        log_layout.addWidget(QLabel("Logi Systemowe:"))
        self.log_widget = QTextBrowser(); self.log_widget.setReadOnly(True); self.log_widget.setMaximumHeight(100)
        log_layout.addWidget(self.log_widget)

        # Panel kontrolny skanera (bez zmian w definicji)
        scanner_control_panel = QWidget()
        scanner_layout = QVBoxLayout(scanner_control_panel)
        scanner_layout.setContentsMargins(5, 0, 5, 0)
        scanner_label = QLabel("<b>Skaner:</b>"); scanner_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.start_scan_btn = QPushButton("▶️ Uruchom")
        self.stop_scan_btn = QPushButton("⏹️ Zatrzymaj")

        # --- ZMIANA: Ustawienie polityki rozmiaru, aby przyciski były węższe ---
        self.start_scan_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.stop_scan_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        scanner_layout.addWidget(scanner_label)
        scanner_layout.addWidget(self.start_scan_btn)
        scanner_layout.addWidget(self.stop_scan_btn)

        # --- ZMIANA: Nowa kolejność elementów ---
        top_layout.addWidget(scanner_control_panel, 0) # 1. Skaner na lewo, bez rozciągania
        top_layout.addWidget(self.banner_label, 1)      # 2. Banner
        top_layout.addWidget(self.status_widget, 2)     # 3. Status
        top_layout.addWidget(self.log_widget_container, 3) # 4. Logi
        top_layout.addStretch(1)                        # 5. Pusta przestrzeń po prawej

        main_layout.addLayout(top_layout)

        # Reszta metody _setup_ui bez zmian
        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_dashboard_tab(), "📊 Dashboard")
        self.tabs.addTab(self.analysis_tab, "🔬 Analiza")
        self.tabs.addTab(self.journal_tab, "📔 Dziennik")
        self.tabs.addTab(self._create_chart_tab(), "📈 Wykres TradingView")
        self.alerts_tab_index = self.tabs.addTab(self.alerts_tab, "🔔 Alerty")
        self.tabs.addTab(self.backtester_tab, "⚙️ Backtester")
        self.tabs.addTab(self.watched_tab, "📌 Obserwowane")
        self.tabs.addTab(self.settings_tab, "🛠 Ustawienia")
        main_layout.addWidget(self.tabs)

        self.status_bar = QStatusBar()
        self.queue_status_label = QLabel("Kolejka AI: 0")
        self.status_bar.addPermanentWidget(self.queue_status_label)
        self.setStatusBar(self.status_bar)

    def _connect_signals(self):
        """Łączy sygnały z UI z odpowiednimi slotami."""
        self.log_signal.connect(self._append_log_message)
        self.tabs.currentChanged.connect(self.on_tab_changed)

        # Sygnały z zakładek
        self.analysis_tab.analysis_requested.connect(self._on_analysis_requested)
        self.analysis_tab.group_action_requested.connect(self._on_group_action_requested)
        self.analysis_tab.coin_action_requested.connect(self._on_coin_action_requested)
        self.analysis_tab.chart_widget.status_message_changed.connect(self.update_status_message)
        self.settings_tab.settings_changed.connect(self._on_settings_changed)
        self.alerts_tab.alert_ready_for_dispatch.connect(self._dispatch_telegram_alert)
        self.journal_tab.chart_widget.status_message_changed.connect(self.update_status_message)
        
        # NOWE SYGNAŁY PRZYCISKÓW
        self.start_scan_btn.clicked.connect(self._start_ssnedam_timer)
        self.stop_scan_btn.clicked.connect(self._stop_ssnedam_timer)

    def _init_timers(self):
        """Inicjalizuje wszystkie timery aplikacji."""
        self.dashboard_timer = QTimer(self)
        self.dashboard_timer.setInterval(60000)
        self.dashboard_timer.timeout.connect(self.refresh_dashboard)

        self.ssnedam_timer = QTimer(self)
        self.ssnedam_timer.timeout.connect(self._ssnedam_scan_loop)

    async def app_startup_sequence(self):
        """Uruchamia wszystkie procesy startowe w zdefiniowanej kolejności."""
        self.analysis_tab.set_controls_enabled(False)
        try:
            self.update_main_status("Wczytywanie list monet...", True)
            await self.services.coin_manager.fetch_all_exchange_symbols()
            await self.services.coin_manager.set_user_id_and_load_data(None)
            self.settings_tab.refresh_group_list()
            self.analysis_tab.populate_coin_tree(self.services.coin_manager.get_user_coin_groups())
            groups = sorted(self.services.coin_manager.get_user_coin_groups().keys())
            if self.dash_group_combo:
                self.dash_group_combo.addItems(groups)
                if self.settings_tab:
                    self.settings_tab.update_group_list(groups)

            self.update_main_status("Wczytywanie Dashboardu...", True)
            first_group = self.dash_group_combo.itemText(0) if self.dash_group_combo.count() > 0 else None
            await self._execute_dashboard_refresh(group_to_load=first_group)

            self.update_main_status("Wczytywanie Dziennika...", True)
            self.journal_tab.populate_data()

            self.update_main_status("Uruchamianie zadań w tle...", True)
            self.services.ssnedam.start_worker()
            self._handle_ssnedam_state_changed() # Automatycznie uruchom skaner, jeśli jest włączony w ustawieniach
            asyncio.create_task(self.services.paper_trader.start())

        except Exception as e:
            logger.error(f"Błąd podczas sekwencji startowej: {e}", exc_info=True)
            QMessageBox.critical(self, "Błąd Inicjalizacji", f"Wystąpił krytyczny błąd podczas startu: {e}")
        finally:
            self.analysis_tab.set_controls_enabled(True)
            self.update_main_status("Gotowy.", False)

    # --- NOWE METODY DO KONTROLI SKANERA ---
    def _start_ssnedam_timer(self):
        """Uruchamia timer skanera i aktualizuje UI."""
        if self.ssnedam_timer.isActive():
            return
            
        interval_ms = self.settings_manager.get('ssnedam.interval_minutes', 15) * 60 * 1000
        self.ssnedam_timer.start(interval_ms)
        logger.info(f"Uruchomiono timer Ssnedam. Interwał: {interval_ms / 1000}s.")
        
        self._update_scanner_ui_state()
        # Uruchom skanowanie od razu po włączeniu
        QTimer.singleShot(0, self._ssnedam_scan_loop)

    def _update_scanner_ui_state(self):
        """Centralna funkcja do aktualizacji UI na podstawie stanu timera."""
        is_active = self.ssnedam_timer.isActive()
        
        self.start_scan_btn.setEnabled(not is_active)
        self.stop_scan_btn.setEnabled(is_active)
        
        if is_active:
            self.update_main_status("Skaner w tle AKTYWNY.", False)
        else:
            self.update_main_status("Skaner w tle ZATRZYMANY.", False)

    def _stop_ssnedam_timer(self):
        """Zatrzymuje timer skanera, czyści kolejkę i aktualizuje UI."""
        self.ssnedam_timer.stop()
        if self.services and self.services.ssnedam:
            self.services.ssnedam.clear_analysis_queue()
        
        logger.info("Zatrzymano timer Ssnedam.")
        self._update_scanner_ui_state()

    def _handle_ssnedam_state_changed(self):
        """Automatycznie zarządza stanem skanera na podstawie ustawień."""
        logger.info("Odczytuję ustawienia i dostosowuję stan skanera...")
        if self.settings_manager.get('ssnedam.enabled', False):
            self._start_ssnedam_timer()
        else:
            self._stop_ssnedam_timer()

    # --- ZMODYFIKOWANE METODY ---
    def update_main_status(self, text: str, is_busy: bool = False):
        if not hasattr(self, 'status_widget'):
            return
        
        match = re.match(r'\((.*?)\)\s*(.*)', text)
        if match:
            symbol = match.group(1)
            message = match.group(2)
            formatted_text = f"<b>{symbol}:</b> {message}"
        else:
            formatted_text = text
            
        self.status_widget.set_status(formatted_text, is_busy)

    def _on_analysis_requested(self, payload: dict):
        if self._analysis_lock.locked():
            return
        
        async def analysis_task():
            async with self._analysis_lock:
                self.analysis_tab.set_controls_enabled(False)
                try:
                    parsed_response, analysis_result, best_timeframe, _ = await self.services.ai_pipeline.run(
                        payload["symbol"], payload["interval"], payload["exchange"], self.update_main_status
                    )
                    if parsed_response and analysis_result:
                        # POPRAWKA: Dodajemy 'await' oraz brakujący argument payload["exchange"]
                        tactician_inputs = await self.services.analyzer.prepare_tactician_inputs(
                            analysis_result, 
                            best_timeframe, 
                            payload["symbol"], 
                            payload["exchange"]
                        )
                        self.analysis_tab.update_view(
                            ohlcv_df=analysis_result.all_ohlcv_dfs.get(best_timeframe),
                            all_timeframe_data=analysis_result.all_timeframe_data,
                            parsed_data=parsed_response.parsed_data,
                            fvgs=self.services.analyzer.find_fair_value_gaps(analysis_result.all_ohlcv_dfs.get(best_timeframe)),
                            fib_data=json.loads(tactician_inputs.get('fibonacci_data', '{}'))
                        )
                    else:
                        QMessageBox.warning(self, "Błąd Analizy", "Nie udało się uzyskać poprawnej odpowiedzi od AI.")
                except Exception as e:
                    logger.critical(f"Błąd krytyczny podczas analizy na żądanie: {e}", exc_info=True)
                    QMessageBox.critical(self, "Błąd Analizy", f"Wystąpił nieoczekiwany błąd:\n{e}")
                finally:
                    self.analysis_tab.set_controls_enabled(True)

        asyncio.create_task(analysis_task())
        
    def closeEvent(self, event):
        if not self._is_shutting_down:
            self._is_shutting_down = True
            self.update_main_status("Zamykanie...", True)
            asyncio.create_task(self._app_shutdown_sequence())
        event.ignore()

    async def _app_shutdown_sequence(self):
        logger.info("Rozpoczynanie sekwencji zamykania UI...")
        await self.services.shutdown()
        logger.info("Wszystkie zadania w tle zakończone. Zamykanie aplikacji.")
        logger.info("Zamykanie komponentu QWebEngineView...")
        self.tradingview_chart_view.close()
        logger.info("Wysyłanie sygnału zamknięcia do wszystkich okien Qt...")
        QApplication.closeAllWindows()
        QTimer.singleShot(100, lambda: sys.exit(0))

    # --- POZOSTAŁE METODY (BEZ ZMIAN) ---
    def _dispatch_telegram_alert(self, alert_data, images: list):
        asyncio.create_task(self.services.ssnedam.send_telegram_alert_with_album(alert_data, images))

    def _ssnedam_scan_loop(self):
        if self._analysis_lock.locked():
            logger.warning("[Ssnedam] Skanowanie pominięte, trwa inna analiza.")
            return

        user_groups = self.services.coin_manager.get_user_coin_groups()
        if not user_groups:
            logger.warning("[Ssnedam] Brak jakichkolwiek grup monet do przeskanowania.")
            return

        group_from_settings = self.settings_manager.get('ssnedam.group', '')
        
        if group_from_settings not in user_groups:
            target_group_name = sorted(user_groups.keys())[0]
            logger.warning(f"[Ssnedam] Grupa '{group_from_settings}' nie istnieje lub nie jest ustawiona. Używam pierwszej dostępnej: '{target_group_name}'.")
            self.settings_manager.set('ssnedam.group', target_group_name)
            self.settings_manager.save_settings()
            self.settings_tab.load_settings()
        else:
            target_group_name = group_from_settings

        coins = user_groups.get(target_group_name, [])
        if coins:
            asyncio.create_task(self.services.ssnedam.scan_for_alerts(coins, self.alerts_tab.add_alert_to_list))
        else:
            logger.warning(f"[Ssnedam] Brak coinów w grupie '{target_group_name}' do przeskanowania.")

    def _on_settings_changed(self):
        self.services.ai_client.update_config()
        self.apply_styles()
        self._handle_ssnedam_state_changed()

    async def _on_group_action_requested(self, action: str, data: dict):
        if action == 'add': await self.services.coin_manager.add_group(data['name'])
        elif action == 'remove': await self.services.coin_manager.remove_group(data['name'])
        groups = sorted(self.services.coin_manager.get_user_coin_groups().keys())
        self.analysis_tab.populate_coin_tree(self.services.coin_manager.get_user_coin_groups())
        self.settings_tab.update_group_list(groups)
        self.dash_group_combo.clear()
        self.dash_group_combo.addItems(groups)

    async def _on_coin_action_requested(self, action: str, data: dict):
        if action == 'show_add_dialog':
            from ui.history_dialog import AddCoinDialog
            dialog = AddCoinDialog(self, self.services.coin_manager.get_available_symbols(), self.services.coin_manager.get_user_coin_groups())
            if dialog.exec():
                await self.services.coin_manager.add_coin_to_group(dialog.selected_group, dialog.selected_symbol, dialog.selected_exchange)
        elif action == 'remove':
            await self.services.coin_manager.remove_coin_from_group(data['group'], data['symbol'], data['exchange'])
        groups = sorted(self.services.coin_manager.get_user_coin_groups().keys())
        self.analysis_tab.populate_coin_tree(self.services.coin_manager.get_user_coin_groups())
        self.settings_tab.update_group_list(groups)
        self.dash_group_combo.clear()
        self.dash_group_combo.addItems(groups)

    def update_status_message(self, message: str):
        if hasattr(self, 'status_bar'): self.status_bar.showMessage(message, 5000)

    def update_queue_status_label(self, size: int):
        if hasattr(self, 'queue_status_label'): self.queue_status_label.setText(f"Kolejka AI: {size}")

    def _append_log_message(self, message: str):
        self.log_widget.append(message)

    def apply_styles(self):
        theme_setting = self.settings_manager.get('app.theme', 'ciemny')
        theme_key = 'dark' if theme_setting == 'ciemny' else 'jasny'
        banner_theme_name = 'dark' if theme_setting == 'ciemny' else 'light'
        stylesheet = get_theme_stylesheet(theme_setting)
        QApplication.instance().setStyleSheet(stylesheet)
        pg.setConfigOption('background', THEMES[theme_key]['CHART_BG'])
        pg.setConfigOption('foreground', THEMES[theme_key]['CHART_FG'])
        
        banner_path = os.path.join(PROJECT_ROOT, 'assets', f'banner_{banner_theme_name}.png')
        if hasattr(self, 'banner_label') and os.path.exists(banner_path):
            try:
                pixmap = QPixmap(banner_path)
                if not pixmap.isNull():
                    self.banner_pixmap = pixmap
                    scaled_pixmap = self.banner_pixmap.scaled(
                        self.banner_label.maximumWidth(), self.banner_label.maximumHeight(),
                        Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                    )
                    self.banner_label.setPixmap(scaled_pixmap)
            except Exception as e:
                logger.error(f"Krytyczny błąd podczas ładowania grafiki bannera: {e}")

    def on_tab_changed(self, index):
        if self.tabs.tabText(index) == "📔 Dziennik": self.journal_tab.populate_data()
        elif self.tabs.tabText(index) == "📌 Obserwowane": self.watched_tab.populate_list()

    def refresh_dashboard(self):
        asyncio.create_task(self._execute_dashboard_refresh())

    async def _execute_dashboard_refresh(self, group_to_load: Optional[str] = None):
        user_groups = self.services.coin_manager.get_user_coin_groups()
        if not user_groups:
            self.dashboard_table.setRowCount(0)
            return
        group = group_to_load
        if not group:
            group = self.dash_group_combo.currentText()
            if not group:
                group = sorted(user_groups.keys())[0]
        self.dash_group_combo.setCurrentText(group)
        coins = user_groups.get(group, [])
        if coins:
            data = await self.services.dashboard_handler.get_market_summary(coins)
            self._populate_dashboard_table(data)
        else:
            self.dashboard_table.setRowCount(0)

    def _get_color_for_value(self, value, min_val, max_val, color_neg, color_pos):
        if value is None or pd.isna(value): return None
        value = max(min(value, max_val), min_val)
        if (max_val - min_val) == 0: normalized = 0.5
        else: normalized = (value - min_val) / (max_val - min_val)
        r = int(color_neg.red() + (color_pos.red() - color_neg.red()) * normalized)
        g = int(color_neg.green() + (color_pos.green() - color_neg.green()) * normalized)
        b = int(color_neg.blue() + (color_pos.blue() - color_neg.blue()) * normalized)
        return QColor(r, g, b)

    def _populate_dashboard_table(self, data: list):
        try:
            self.dashboard_table.setSortingEnabled(False)
            self.dashboard_table.clearContents()
            headers = ["Symbol", "Cena ($)", "Zmiana 24h", "Wolumen 24h", "Rekom. Bota", "Faza Rynku", "Zmienność", "Siła Wzgl. (BTC 7d)", "L/S Ratio"]
            self.dashboard_table.setColumnCount(len(headers))
            self.dashboard_table.setHorizontalHeaderLabels(headers)
            if not data:
                self.dashboard_table.setRowCount(0)
                return
            self.dashboard_table.setRowCount(len(data))
            df = pd.DataFrame(data)
            change_min, change_max = df['change_24h'].min(), df['change_24h'].max()
            faza_min, faza_max = df['dist_from_ema200'].min(), df['dist_from_ema200'].max()
            sila_min, sila_max = df['relative_strength_btc_7d'].min(), df['relative_strength_btc_7d'].max()
            atr_min, atr_max = df['atr_percent'].min(), df['atr_percent'].max()
            color_red, color_green = QColor("#d32f2f"), QColor("#388e3c")
            color_cold, color_hot = QColor("#e3f2fd"), QColor("#fff3e0")
            reco_colors = {"KUPUJ": QColor("#2ECC71"), "SPRZEDAJ": QColor("#E74C3C"), "NEUTRALNIE": QColor("#95A5A6")}
            column_map = {
                "Symbol": {"key": "symbol"}, "Cena ($)": {"key": "price", "format": "{:,.4f}"},
                "Zmiana 24h": {"key": "change_24h", "format": "{:+.2f}%", "heatmap": (change_min, change_max, color_red, color_green)},
                "Wolumen 24h": {"key": "volume_24h", "format": "{:,.0f}"},
                "Rekom. Bota": {"key": "bot_reco", "color_map": reco_colors},
                "Faza Rynku": {"key": "dist_from_ema200", "format": "{:+.2f}%", "heatmap": (faza_min, faza_max, color_red, color_green)},
                "Zmienność": {"key": "atr_percent", "format": "{:.2f}%", "heatmap": (atr_min, atr_max, color_cold, color_hot)},
                "Siła Wzgl. (BTC 7d)": {"key": "relative_strength_btc_7d", "format": "{:+.2f}%", "heatmap": (sila_min, sila_max, color_red, color_green)},
                "L/S Ratio": {"key": "long_short_ratio", "format": "{:.2f}"},
            }
            for row, coin in enumerate(data):
                for col, col_name in enumerate(headers):
                    config = column_map.get(col_name)
                    if not config: continue
                    raw_value = coin.get(config["key"])
                    value_str = "N/A"
                    item = QTableWidgetItem()
                    if isinstance(raw_value, (int, float)):
                        item.setData(Qt.ItemDataRole.EditRole, raw_value)
                    if raw_value is not None:
                        try: value_str = config.get("format", "{}").format(raw_value)
                        except (ValueError, TypeError): value_str = str(raw_value)
                    item.setText(value_str)
                    if "heatmap" in config and isinstance(raw_value, (int, float)):
                        min_v, max_v, c_neg, c_pos = config["heatmap"]
                        color = self._get_color_for_value(raw_value, min_v, max_v, c_neg, c_pos)
                        if color:
                            item.setBackground(color)
                            if color.lightness() > 180:
                                item.setForeground(QColor("black"))
                    elif "color_map" in config:
                        for keyword, color in config["color_map"].items():
                            if keyword in value_str:
                                item.setForeground(color); item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold)); break
                    self.dashboard_table.setItem(row, col, item)
            self.dashboard_table.setSortingEnabled(True)
            self.dashboard_table.resizeColumnsToContents()
            self.dashboard_table.horizontalHeader().setStretchLastSection(True)
        except Exception as e:
            logger.error(f"Błąd podczas wypełniania tabeli dashboardu: {e}", exc_info=True)

    def _create_dashboard_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        top_panel = QHBoxLayout()
        top_panel.addWidget(QLabel("Grupa do wyświetlenia:"))
        self.dash_group_combo = QComboBox()
        top_panel.addWidget(self.dash_group_combo)
        top_panel.addStretch()
        self.dash_refresh_btn = QPushButton("🔄 Odśwież")
        self.dash_refresh_btn.clicked.connect(self.refresh_dashboard)
        top_panel.addWidget(self.dash_refresh_btn)
        self.dash_auto_refresh_check = QCheckBox("Auto-odświeżanie (1 min)")
        top_panel.addWidget(self.dash_auto_refresh_check)
        layout.addLayout(top_panel)
        self.dashboard_table = QTableWidget()
        layout.addWidget(self.dashboard_table)
        return tab

    def _create_chart_tab(self):
        self.tradingview_chart_view = QWebEngineView()
        self.tradingview_chart_view.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        self.tradingview_chart_view.setUrl(QUrl("about:blank"))
        return self.tradingview_chart_view

    def pause_background_tasks(self):
        if self.ssnedam_timer.isActive(): self.ssnedam_timer.stop()
        if self.services.paper_trader.is_running: self.services.paper_trader.stop()

    def resume_background_tasks(self):
        self._handle_ssnedam_state_changed()
        if not self.services.paper_trader.is_running: asyncio.create_task(self.services.paper_trader.start())
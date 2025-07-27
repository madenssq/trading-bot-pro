# Plik: ui/main_window.py (WERSJA PO PEŁNEJ REFAKTORYZACJI ZAKŁADEK)

import asyncio
import io
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

# --- KROK 2: Biblioteki Zewnętrzne ---
import firebase_admin
import httpx
import numpy as np
import pandas as pd
import pyqtgraph as pg
import pyqtgraph.exporters
from ui.journal_tab import JournalTab
from ui.alerts_tab import AlertsTab
import requests
from firebase_admin import auth, credentials, firestore
from PyQt6.QtCore import QDate, QPoint, QPointF, Qt, QTimer, QUrl, pyqtSignal, QBuffer
from PyQt6.QtGui import (QColor, QFont, QIcon, QImage, QMovie, QPainter,
                         QPicture, QPixmap)
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                             QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                             QFrame, QGraphicsRectItem, QHBoxLayout,
                             QHeaderView, QInputDialog, QLabel, QListWidget,
                             QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                             QPushButton, QRadioButton, QScrollArea, QSpinBox,
                             QSplitter, QStatusBar, QTabWidget, QTableWidget,
                             QTableWidgetItem, QTextBrowser, QVBoxLayout,
                             QWidget, QTreeWidget, QTreeWidgetItem)

# --- KROK 3: Importy Wewnętrzne Aplikacji ---
from app_config import FIREBASE_ADMIN_SDK_KEY_PATH, RAMY_CZASOWE
from core.ai_client import AIClient, ParsedAIResponse
from core.analyzer import AnalysisResult, TechnicalAnalyzer
from core.coin_manager import CoinManager
from core.dashboard_handler import DashboardHandler
from core.settings_manager import SettingsManager
from core.paper_trader import PaperTrader
from core.ai_pipeline import AIPipeline
from core.performance_analyzer import PerformanceAnalyzer
from core.ssnedam import AlertData, Ssnedam
from core.database_manager import DatabaseManager
from ui.analysis_handler import AnalysisHandler
from ui.analysis_tab import AnalysisTab
from ui.watched_tab import WatchedTab
from ui.backtester_tab import BacktesterTab
from ui.history_dialog import AddCoinDialog, DateAxis, CandlestickItem
from ui.settings_tab import SettingsTab
from core.news_client import CryptoPanicClient
from ui.styles import get_theme_stylesheet, THEMES

logger = logging.getLogger(__name__)

# --- KLASY POMOCNICZE ---

def escape_markdown(text: str) -> str:
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)


class StatusWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.animation_label = QLabel()
        self.animation_label.setMaximumHeight(60)
        self.movie = QMovie("assets/loading.gif")
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




# --- GŁÓWNA KLASA OKNA ---

class MainWindow(QMainWindow):
    log_signal = pyqtSignal(str)

    def __init__(self, settings_manager: SettingsManager):
        super().__init__()
        self.setObjectName("MainWindow")
        self.settings_manager = settings_manager
        self._analysis_lock = asyncio.Lock()

        # KROK 1: Inicjalizacja podstawowych managerów i połączeń
        self.db_manager = DatabaseManager()
        self.db, self.auth_admin_client = None, None
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore, auth
            from app_config import FIREBASE_ADMIN_SDK_KEY_PATH
            if not firebase_admin._apps and os.path.exists(FIREBASE_ADMIN_SDK_KEY_PATH):
                cred = credentials.Certificate(FIREBASE_ADMIN_SDK_KEY_PATH)
                firebase_admin.initialize_app(cred)
                self.db, self.auth_admin_client = firestore.client(), auth
                logger.info("Pomyślnie zainicjowano połączenie z Firebase.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd Firebase", f"Nie udało się zainicjalizować Firebase: {e}")
            logger.critical(f"Błąd inicjalizacji Firebase: {e}", exc_info=True)
        
        # KROK 2: Inicjalizacja "silników" analitycznych ✅
        self.analyzer = TechnicalAnalyzer(self.settings_manager, self.db_manager)
        self.ai_client = AIClient(self.settings_manager)
        self.performance_analyzer = PerformanceAnalyzer(self.db_manager)
        try:
            cp_token = self.settings_manager.get('cryptopanic.api_token')
            self.news_client = CryptoPanicClient(cp_token) if cp_token else None
        except ValueError as e:
            logger.warning(f"Nie można zainicjalizować klienta wiadomości: {e}")
            self.news_client = None
        self.coin_manager = CoinManager(db_client=self.db, auth_admin_client=self.auth_admin_client, analyzer=self.analyzer)
        
        # KROK 2.5: Inicjalizacja scentralizowanego pipeline'u AI ➡️
        # TA LINIA MUSI BYĆ TUTAJ, PRZED KROKIEM 3
        self.ai_pipeline = AIPipeline(
            analyzer=self.analyzer,
            ai_client=self.ai_client,
            db_manager=self.db_manager,
            performance_analyzer=self.performance_analyzer
        )

        # KROK 3: Inicjalizacja handlerów, które korzystają z silników 👇
        self.analysis_handler = AnalysisHandler(
            analyzer=self.analyzer,
            ai_client=self.ai_client,
            performance_analyzer=self.performance_analyzer,
            news_client=self.news_client,
            db_manager=self.db_manager,
            status_callback=self.update_main_status,
            display_callback=self.display_analysis_results,
            ai_pipeline=self.ai_pipeline, # Teraz self.ai_pipeline już istnieje
            parent_widget=self
        )
        self.ssnedam = Ssnedam(
            analyzer=self.analyzer,
            ai_client=self.ai_client,
            performance_analyzer=self.performance_analyzer,
            news_client=self.news_client,
            db_manager=self.db_manager,
            queue_update_callback=self.update_queue_status_label,
            global_analysis_lock=self._analysis_lock,
            status_update_callback=self.update_main_status,
            ai_pipeline=self.ai_pipeline # Teraz self.ai_pipeline już istnieje
        )
        self.dashboard_handler = DashboardHandler(self.analyzer)
        self.paper_trader = PaperTrader(self.db_manager, self.analyzer, self._analysis_lock)

        # KROK 4: TWORZENIE INSTANCJI ZAKŁADEK
        self.analysis_tab = AnalysisTab(self.settings_manager, self.analyzer, self.db_manager)
        self.settings_tab = SettingsTab(self.settings_manager, self.coin_manager, self.ai_client)
        self.backtester_tab = BacktesterTab(self.settings_manager, self.analyzer, self)
        self.alerts_tab = AlertsTab(self.settings_manager, self.analyzer)
        self.journal_tab = JournalTab(self.db_manager, self.analyzer, self.settings_manager)
        self.watched_tab = WatchedTab(self.db_manager, self.settings_manager, self.analyzer)

        # KROK 5: ŁĄCZENIE SYGNAŁÓW Z ZAKŁADEK
        self.analysis_tab.analysis_requested.connect(self._on_analysis_requested)
        self.analysis_tab.group_action_requested.connect(self._on_group_action_requested) 
        self.analysis_tab.coin_action_requested.connect(self._on_coin_action_requested) 
        self.analysis_tab.status_message_changed.connect(self.update_status_message)
        self.settings_tab.settings_changed.connect(self._on_settings_changed)
        self.alerts_tab.alert_ready_for_dispatch.connect(self._dispatch_telegram_alert)
        self.journal_tab.status_message_changed.connect(self.update_status_message)

        # KROK 6: Uruchamianie zadań w tle i reszta inicjalizacji
        self.dashboard_loaded_once = False
        self.current_interval, self.current_symbol, self.current_exchange = "1h", "BTC/USDT", "BINANCE"
        self.ohlcv_df = pd.DataFrame()
        self.plotted_items = {}
        
        # Timery
        self.dashboard_timer = QTimer(self)
        self.dashboard_timer.setInterval(60000)
        self.dashboard_timer.timeout.connect(self.refresh_dashboard)
        
        self.ssnedam_timer = QTimer(self)
        self.ssnedam_timer.timeout.connect(self._ssnedam_scan_loop)

        # Uruchomienie UI i zadań startowych
        self.setup_ui()
        QTimer.singleShot(0, self.ssnedam.start_worker)
        QTimer.singleShot(1000, lambda: asyncio.create_task(self.initialize_app_data())) # Lekkie opóźnienie dla pewności
        QTimer.singleShot(5000, lambda: asyncio.create_task(self.paper_trader.start()))


    # --- SETUP UI I TWORZENIE ZAKŁADEK ---
    
    def setup_ui(self):
        self.setCentralWidget(QWidget())
        main_layout = QVBoxLayout(self.centralWidget())
        main_layout.setContentsMargins(10, 10, 10, 10); main_layout.setSpacing(10)
        
        top_layout = QHBoxLayout()
        self.banner_label = QLabel(); self.banner_label.setMaximumHeight(120)
        self.status_widget = StatusWidget()
        self.log_widget_container = QWidget()
        log_layout = QVBoxLayout(self.log_widget_container); log_layout.setContentsMargins(0,0,0,0)
        log_layout.addWidget(QLabel("Logi Systemowe:"))
        self.log_widget = QTextBrowser(); self.log_widget.setReadOnly(True); self.log_widget.setMaximumHeight(100)
        log_layout.addWidget(self.log_widget)
        top_layout.addWidget(self.banner_label, 1); top_layout.addStretch(1)
        top_layout.addWidget(self.status_widget, 2); top_layout.addStretch(1)
        top_layout.addWidget(self.log_widget_container, 3)
        main_layout.addLayout(top_layout)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_dashboard_tab(), "📊 Dashboard")
        self.tabs.addTab(self._create_analysis_tab(), "🔬 Analiza")
        self.tabs.addTab(self._create_journal_tab(), "📔 Dziennik")
        self.tabs.addTab(self._create_chart_tab(), "📈 Wykres TradingView")
        self.alerts_tab_index = self.tabs.addTab(self._create_alerts_tab(), "🔔 Alerty")
        self.tabs.addTab(self._create_backtester_tab(), "⚙️ Backtester")
        self.tabs.addTab(self.watched_tab, "📌 Obserwowane")
        self.tabs.addTab(self._create_settings_tab(), "🛠 Ustawienia")
        main_layout.addWidget(self.tabs)

        self.status_bar = QStatusBar()
        self.queue_status_label = QLabel("Kolejka AI: 0")
        self.status_bar.addPermanentWidget(self.queue_status_label)
    
        self.setStatusBar(self.status_bar)

        self.apply_styles()
        self.tabs.currentChanged.connect(self.on_tab_changed)
        self.log_signal.connect(self._append_log_message)

    def _create_journal_tab(self):
        """Zwraca instancję zakładki Dziennika."""
        return self.journal_tab

    def _create_analysis_tab(self):
        return self.analysis_tab

    def _create_alerts_tab(self):
        """Zwraca instancję zakładki Alerty."""
        return self.alerts_tab


    
    def _create_backtester_tab(self):
        """Zwraca instancję zakładki Backtestera."""
        return self.backtester_tab
    
    def _create_settings_tab(self):
        """Zwraca instancję zakładki Ustawień."""
        return self.settings_tab

    def _create_chart_tab(self):
        """Tworzy zakładkę z osadzonym wykresem TradingView."""
        self.tradingview_chart_view = QWebEngineView()
        self.tradingview_chart_view.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        self.tradingview_chart_view.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        
        # Początkowe załadowanie pustej strony, aby uniknąć błędów
        self.tradingview_chart_view.setUrl(QUrl("about:blank"))
        
        return self.tradingview_chart_view

    def _create_dashboard_tab(self):
        """Tworzy interfejs użytkownika dla zakładki Dashboard."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # --- Panel sterowania na górze ---
        top_panel = QHBoxLayout()
        top_panel.addWidget(QLabel("Grupa do wyświetlenia:"))
        
        self.dash_group_combo = QComboBox() # Tworzymy brakujący widget
        top_panel.addWidget(self.dash_group_combo)
        top_panel.addStretch()
        
        self.dash_refresh_btn = QPushButton("🔄 Odśwież")
        self.dash_refresh_btn.clicked.connect(self.refresh_dashboard)
        top_panel.addWidget(self.dash_refresh_btn)
        
        self.dash_auto_refresh_check = QCheckBox("Auto-odświeżanie (1 min)")
        self.dash_auto_refresh_check.stateChanged.connect(self._handle_dashboard_auto_refresh)
        top_panel.addWidget(self.dash_auto_refresh_check)
        
        layout.addLayout(top_panel)
        
        # --- Tabela z danymi ---
        self.dashboard_table = QTableWidget() # Tworzymy także brakującą tabelę
        self.dashboard_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.dashboard_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.dashboard_table.cellDoubleClicked.connect(self._on_dashboard_symbol_selected)
        layout.addWidget(self.dashboard_table)
        
        return tab

    # --- GŁÓWNA LOGIKA I OBSŁUGA DANYCH ---

    async def initialize_app_data(self):
        self.set_ui_state(False)
        try:
            await self.coin_manager.fetch_all_exchange_symbols()
            await self.coin_manager.set_user_id_and_load_data(None)
            print(f"DEBUG: Grupy w CoinManager tuż przed odświeżeniem: {self.coin_manager.get_user_coin_groups()}")
            self.settings_tab.refresh_group_list()
            self.user_id = self.coin_manager.user_id
            self.populate_coin_tree()
            # await self._execute_dashboard_refresh() # <-- LINIĘ USUWAMY
            self._handle_ssnedam_state_changed()
            self.on_tab_changed(self.tabs.currentIndex())
        except Exception as e:
            logger.error(f"Błąd inicjalizacji: {e}", exc_info=True)
            QMessageBox.critical(self, "Błąd inicjalizacji", f"Wystąpił krytyczny błąd: {e}")
        finally:
            self.set_ui_state(True)

    def display_analysis_results(self, ohlcv_df, all_timeframe_data, parsed_data, raw_ai_response, context_text, visualization_data, all_ohlcv_dfs, fvgs, fib_data=None):
        """
        Przekazuje kompletne dane do zakładki Analiza, aby zaktualizowała swój widok.
        Teraz akceptuje również dane Fibonacciego.
        """
        if not parsed_data:
            logger.error("Otrzymano puste dane po parsowaniu, nie można zaktualizować widoku.")
            # Możemy tu dodać logikę wyświetlania surowej odpowiedzi AI w UI, jeśli chcemy
            return

        # Przekazujemy wszystkie dane, w tym fib_data, do odpowiedniej zakładki
        self.analysis_tab.update_view(
            ohlcv_df=ohlcv_df,
            all_timeframe_data=all_timeframe_data,
            parsed_data=parsed_data,
            fvgs=fvgs,
            fib_data=fib_data # <-- Przekazujemy dane Fibo dalej
        )
    

    # --- METODY RYSUJĄCE ---

    def _draw_chart_with_features(self, plot_widget, df: pd.DataFrame, fvgs: list = None, setup: dict = None, sr_levels: dict = None, title: str = "Wykres Analizy", zoom_range: Dict = None):
        plot_widget.clear()
        theme_setting = self.settings_manager.get('app.theme', 'ciemny')
        theme_key = 'dark' if theme_setting == 'ciemny' else 'light'
        theme = THEMES[theme_key]
        
        plot_widget.setBackground(theme['CHART_BG'])
        
        if df is None or df.empty:
            plot_widget.addPlot().setTitle("Brak danych do wyświetlenia", color='red')
            return None

        plot_area = plot_widget.addPlot(row=0, col=0, axisItems={'bottom': DateAxis(orientation='bottom')})
        plot_area.setTitle(title)
        plot_area.showGrid(x=False, y=False)
        
        left_axis = plot_area.getAxis('left')
        left_axis.setPen(color=theme['CHART_FG'], width=1)
        left_axis.setTextPen(color=theme['CHART_FG'])

        self.plotted_items.clear()
        params = self.settings_manager.get('analysis.indicator_params', {})
        
        self._draw_candlesticks(plot_area, df)
        self._draw_emas(plot_area, df, params)
        self._draw_bbands(plot_area, df, params)
        
        if sr_levels: self._draw_support_resistance(plot_area, sr_levels)
        if fvgs: self._draw_fvgs(plot_area, fvgs)
        if setup: self._draw_setup_zones(plot_area, setup)

        # --- NOWA LOGIKA: Rysowanie Linii Pułapki ---
        if setup and setup.get("trigger_event"):
            event_data = setup["trigger_event"]
            trap_level = event_data.get("level")
            if trap_level:
                # Rysujemy specjalną, wyróżniającą się linię
                trap_line = pg.InfiniteLine(
                    pos=trap_level, 
                    angle=0, 
                    pen=pg.mkPen('yellow', style=Qt.PenStyle.DashLine, width=2), 
                    label='Poziom Pułapki {value:.4f}', 
                    labelOpts={'position': 0.5, 'color': 'y'} # Ustawiamy też kolor etykiety
                )
                plot_area.addItem(trap_line)
                
                # Dodajemy ją do słownika, aby można było ją ukrywać
                if 'trap_levels' not in self.plotted_items:
                    self.plotted_items['trap_levels'] = []
                self.plotted_items['trap_levels'].append(trap_line)
        
        # --- ZMIANA: Logika przybliżania wykresu ---
        if zoom_range:
            plot_area.setXRange(zoom_range['x_min'], zoom_range['x_max'])
            plot_area.setYRange(zoom_range['y_min'], zoom_range['y_max'])
        elif not df.empty:
            min_price, max_price = df['Low'].min(), df['High'].max()
            padding = (max_price - min_price) * 0.1
            plot_area.setYRange(min_price - padding, max_price + padding, padding=0)
            plot_area.enableAutoRange(axis='x')
            
        return plot_area

    def _draw_candlesticks(self, plot, df):
        timestamps = df.index.astype(np.int64) // 10**9
        self.plotted_items['candlesticks'] = [CandlestickItem(list(zip(timestamps, df['Open'], df['High'], df['Low'], df['Close'])))]
        plot.addItem(self.plotted_items['candlesticks'][0])

    def _draw_emas(self, plot, df, params):
        self.plotted_items['ema'] = []; ts = df.index.astype(np.int64) // 10**9
        f_len, s_len = params.get('ema_fast_length', 50), params.get('ema_slow_length', 200)
        if f'EMA_{f_len}' in df.columns: self.plotted_items['ema'].append(plot.plot(ts, df[f'EMA_{f_len}'], pen=pg.mkPen('#3498DB', width=2)))
        if f'EMA_{s_len}' in df.columns: self.plotted_items['ema'].append(plot.plot(ts, df[f'EMA_{s_len}'], pen=pg.mkPen('#F1C40F', width=2)))

    def _draw_bbands(self, plot, df, params):
        self.plotted_items['bb'] = []; ts = df.index.astype(np.int64) // 10**9
        up_key, low_key = f"BBU_{params.get('bbands_length', 20)}_{params.get('bbands_std', 2.0)}", f"BBL_{params.get('bbands_length', 20)}_{params.get('bbands_std', 2.0)}"
        if up_key in df and low_key in df:
            up_item, low_item = plot.plot(ts, df[up_key], pen=pg.mkPen('#95A5A6', style=Qt.PenStyle.DashLine)), plot.plot(ts, df[low_key], pen=pg.mkPen('#95A5A6', style=Qt.PenStyle.DashLine))
            fill = pg.FillBetweenItem(up_item, low_item, brush=(91, 99, 120, 50)); plot.addItem(fill); self.plotted_items['bb'].extend([up_item, low_item, fill])
    
    def _draw_support_resistance(self, plot, sr_data):
        self.plotted_items['sr_levels'] = []
        support_pen, resistance_pen = pg.mkPen('#2ECC71', width=2), pg.mkPen('#E74C3C', width=2)
        
        # ZMIANA: Dodajemy pozycjonowanie etykiet, aby się nie nakładały
        for level in sr_data.get('support', []):
            line = pg.InfiniteLine(pos=level, angle=0, pen=support_pen, label='Wsparcie {value:.2f}', labelOpts={'position': 0.85}) # Etykieta po prawej
            plot.addItem(line)
            self.plotted_items['sr_levels'].append(line)
            
        for level in sr_data.get('resistance', []):
            line = pg.InfiniteLine(pos=level, angle=0, pen=resistance_pen, label='Opór {value:.2f}', labelOpts={'position': 0.85}) # Etykieta po prawej
            plot.addItem(line)
            self.plotted_items['sr_levels'].append(line)

    def _draw_fvgs(self, plot, fvgs: List[Dict]):
        self.plotted_items['fvgs'] = []
        bullish_brush, bearish_brush = QColor(0, 150, 255, 40), QColor(255, 165, 0, 40)
        for gap in fvgs:
            brush = bullish_brush if gap['type'] == 'bullish' else bearish_brush
            rect = QGraphicsRectItem(gap['start_time'], gap['start_price'], gap['width_seconds'], gap['end_price'] - gap['start_price'])
            rect.setBrush(brush); rect.setPen(pg.mkPen(None)); plot.addItem(rect); self.plotted_items['fvgs'].append(rect)

    def _draw_setup_zones(self, plot, setup_data: dict):
        self.plotted_items['setup'] = []
        entry = setup_data.get('entry')
        stop_loss = setup_data.get('stop_loss')
        take_profit_levels = setup_data.get('take_profit')

        # Rysowanie strefy Stop Loss
        if entry and stop_loss:
            sl_region = pg.LinearRegionItem(values=[entry, stop_loss], orientation='horizontal', brush=QColor(231, 76, 60, 40), pen=pg.mkPen(None))
            plot.addItem(sl_region)
            self.plotted_items['setup'].append(sl_region)

        # ZMIANA: Rysowanie strefy Take Profit
        if entry and take_profit_levels:
            # Używamy TP2 jeśli istnieje, w przeciwnym razie wracamy do TP1
            target_tp = take_profit_levels[1] if len(take_profit_levels) > 1 else take_profit_levels[0]
            
            tp_region = pg.LinearRegionItem(values=[entry, target_tp], orientation='horizontal', brush=QColor(46, 204, 113, 40), pen=pg.mkPen(None))
            plot.addItem(tp_region)
            self.plotted_items['setup'].append(tp_region)
            
        self._draw_sl_tp_lines(plot, setup_data)

    def _draw_sl_tp_lines(self, plot, setup_data: dict):
        if 'setup' not in self.plotted_items: self.plotted_items['setup'] = []
        
        # ZMIANA: Pozycjonujemy etykiety SL/TP po lewej stronie, aby nie kolidowały z S/R
        if entry := setup_data.get('entry'):
            line = pg.InfiniteLine(pos=entry, angle=0, pen=pg.mkPen('cyan', style=Qt.PenStyle.DashLine), label='Wejście {value:.2f}', labelOpts={'position': 0.15})
            plot.addItem(line)
            self.plotted_items['setup'].append(line)
            
        if sl := setup_data.get('stop_loss'):
            line = pg.InfiniteLine(pos=sl, angle=0, pen=pg.mkPen('red', style=Qt.PenStyle.DashLine), label='Stop Loss {value:.2f}', labelOpts={'position': 0.15})
            plot.addItem(line)
            self.plotted_items['setup'].append(line)
            
        if tps := setup_data.get('take_profit'):
            for i, tp in enumerate(tps):
                line = pg.InfiniteLine(pos=tp, angle=0, pen=pg.mkPen('green', style=Qt.PenStyle.DashLine), label=f'TP {i+1} {{value:.2f}}', labelOpts={'position': 0.15})
                plot.addItem(line)
                self.plotted_items['setup'].append(line)
                
    # --- OBSŁUGA ZDARZEŃ ---

    def _on_settings_changed(self):
        """Slot reagujący na zmianę ustawień w SettingsTab."""
        logger.info("Wykryto zmianę ustawień. Stosowanie globalnych zmian...")
        self.ai_client.update_config()
        self.apply_styles()
        self._handle_ssnedam_state_changed()
        self.populate_coin_tree()




    def _reset_chart_view(self):
        if hasattr(self, 'plot_area') and not self.ohlcv_df.empty:
            if hasattr(self, 'lock_autorange_check') and self.lock_autorange_check.isChecked(): return
            self.plot_area.enableAutoRange(axis='x')
            visible_range = self.plot_area.vb.viewRange()
            min_x, max_x = visible_range[0]
            visible_df = self.ohlcv_df[(self.ohlcv_df.index.astype(np.int64) // 10**9 >= min_x) & (self.ohlcv_df.index.astype(np.int64) // 10**9 <= max_x)]
            if not visible_df.empty:
                min_price, max_price = visible_df['Low'].min(), visible_df['High'].max()
                padding = (max_price - min_price) * 0.1
                self.plot_area.setYRange(min_price - padding, max_price + padding, padding=0)

    def on_tab_changed(self, index):
        """
        Wersja z logowaniem diagnostycznym.
        """
        print("--- DEBUG: on_tab_changed called ---")
        print(f"Otrzymany index: {index}")
        

        try:
            tab_text = self.tabs.tabText(index)
            print(f"Tekst na zakładce: '{tab_text}'")
            print(f"Czy to Dashboard?: {tab_text == '📊 Dashboard'}")
            print(f"Czy dashboard był już ładowany?: {self.dashboard_loaded_once}")
        except Exception as e:
            print(f"Błąd podczas sprawdzania informacji o zakładce: {e}")

        # Oryginalna logika
        if self.tabs.tabText(index) == "📊 Dashboard" and not self.dashboard_loaded_once:
            self.dashboard_loaded_once = True 
            logger.info("Pierwsze wejście do dashboardu. Uruchamianie ładowania danych...")
            self.refresh_dashboard()
        elif self.tabs.tabText(index) == "📔 Dziennik":
            self.journal_tab.populate_data()

        elif self.tabs.tabText(index) == "📈 Wykres TradingView":
            self.load_chart(self.current_symbol, self.current_interval)

        elif self.tabs.tabText(index) == "📌 Obserwowane":
            self.watched_tab.populate_list()

        print("--- DEBUG: on_tab_changed finished ---")
    
    

    def _on_chart_interval_changed(self, new_interval: str):
        if not hasattr(self, 'all_ohlcv_dfs') or not self.all_ohlcv_dfs: return
        new_ohlcv_df = self.all_ohlcv_dfs.get(new_interval)
        if new_ohlcv_df is not None and not new_ohlcv_df.empty:
            self.update_status_message(f"Zmieniono interwał wykresu na: {new_interval}")
            self._update_chart_display(new_ohlcv_df, self.all_timeframe_data_cache, self.parsed_data_cache)
        else:
            self.update_status_message(f"Brak danych dla interwału {new_interval} do narysowania.")

    async def _execute_full_analysis_task(self, mode: str): # <--- ZMIANA
        async with self._analysis_lock:
            self.set_ui_state(False)
            # Usunięto odczytywanie trybu z radio buttonów, teraz jest przekazywany
            await self.analysis_handler.run_full_analysis(
                self.current_symbol, self.current_interval, self.current_exchange, mode
            )
            self.set_ui_state(True)




            
    # --- METODY POMOCNICZE ---

    def _dispatch_telegram_alert(self, alert_data: AlertData, images: list):
        """
        Slot, który odbiera gotowy alert z obrazami i zleca jego wysyłkę.
        """
        logger.info(f"Odebrano gotowy pakiet dla {alert_data.symbol}. Zlecanie wysyłki na Telegram...")
        # Używamy create_task, aby wysyłka nie blokowała interfejsu
        asyncio.create_task(
            self.ssnedam.send_telegram_alert_with_album(alert_data, images)
        )

    def load_chart(self, symbol, interval):
        try:
            sym = f"BINANCE:{symbol.replace('/', '')}"; int_map={'1m':'1','5m':'5','15m':'15','30m':'30','1h':'60','4h':'240','1d':'D','1w':'W'}
            theme = "dark" if self.settings_manager.get('app.theme','ciemny') == 'ciemny' else "light"
            html = f'<!DOCTYPE html><html><head><title>Chart</title><style>body,html{{margin:0;padding:0;width:100%;height:100%;overflow:hidden;}}</style></head><body><div id="c" style="width:100%;height:100%;"></div><script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script><script type="text/javascript">new TradingView.widget({{"autosize":true,"symbol":"{sym}","interval":"{int_map.get(interval, "60")}","theme":"{theme}","style":"1","locale":"pl","container_id":"c"}});</script></body></html>'
            if hasattr(self, 'tradingview_chart_view'): self.tradingview_chart_view.setHtml(html)
        except Exception as e:
            logger.error(f"Błąd podczas generowania wykresu TradingView: {e}", exc_info=True)

    def populate_coin_tree(self):
        groups = self.coin_manager.get_user_coin_groups()
        self.analysis_tab.populate_coin_tree(groups) # <--- ZMIANA
        
        # Aktualizacja list w innych zakładkach
        all_coins_list = sorted(list(self.coin_manager.get_all_symbols_from_groups()))
        if hasattr(self, 'backtester_tab'): self.backtester_tab.update_coin_list(all_coins_list)
        if hasattr(self, 'settings_tab'): self.settings_tab.update_group_list(sorted(groups.keys()))
        if hasattr(self, 'dash_group_combo'):
            current_selection = self.dash_group_combo.currentText()
            self.dash_group_combo.clear(); self.dash_group_combo.addItems(sorted(groups.keys()))
            if current_selection in groups: self.dash_group_combo.setCurrentText(current_selection)
        


    def set_ui_state(self, enabled: bool):
        self.analysis_tab.setEnabled(enabled) # <--- POPRAWIONA LINIA
        if hasattr(self, 'dash_refresh_btn'): self.dash_refresh_btn.setEnabled(enabled)
        if enabled: self.update_main_status("Czuwanie...", False)
        else: self.update_main_status("Pracuję...", True)

    def update_main_status(self, text: str, is_busy: bool = False):
        if hasattr(self, 'status_widget'): self.status_widget.set_status(text, is_busy)
        if not is_busy: self.status_bar.showMessage("Gotowy.", 3000)
    
    def update_status_message(self, message: str):
        if hasattr(self, 'status_bar'): self.status_bar.showMessage(message, 5000)

    def update_queue_status_label(self, size: int):
        if hasattr(self, 'queue_status_label'): self.queue_status_label.setText(f"Kolejka AI: {size}")

    def _append_log_message(self, message: str):
        self.log_widget.append(message)
        self.log_widget.verticalScrollBar().setValue(self.log_widget.verticalScrollBar().maximum())

    def apply_styles(self):
        theme, bg_path = self.settings_manager.get('app.theme','ciemny'), self.settings_manager.get('app.background_path')
        stylesheet = get_theme_stylesheet(theme, bg_path); QApplication.instance().setStyleSheet(stylesheet)
        
        theme_key = 'dark' if theme == 'ciemny' else theme
        
        pg.setConfigOption('background', THEMES[theme_key]['CHART_BG']); pg.setConfigOption('foreground', THEMES[theme_key]['CHART_FG'])
        banner = f"assets/banner_{theme}.png"
        if hasattr(self, 'banner_label') and os.path.exists(banner): self.banner_label.setPixmap(QPixmap(banner).scaled(self.banner_label.width(), self.banner_label.height(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    
    def show_definition_in_statusbar(self, url: QUrl):
        # ...
        pass

   
        
    
    async def _group_worker(self, action, name):
        if await action(name): self.populate_coin_tree()
    
    def add_coin_to_group(self):
        dialog=AddCoinDialog(self, self.coin_manager.get_available_symbols(), self.coin_manager.get_user_coin_groups())
        if dialog.exec()==QDialog.DialogCode.Accepted:
            asyncio.create_task(self._coin_worker(self.coin_manager.add_coin_to_group, dialog.selected_group, dialog.selected_symbol, dialog.selected_exchange))
    async def _coin_worker(self, action, group, symbol, exchange):
        if await action(group, symbol, exchange): self.populate_coin_tree()
    
    

    def refresh_dashboard(self): asyncio.create_task(self._execute_dashboard_refresh())
    async def _execute_dashboard_refresh(self):
        if self._analysis_lock.locked(): return
        async with self._analysis_lock:
            self.set_ui_state(False); group=self.dash_group_combo.currentText()
            if not group and self.coin_manager.get_user_coin_groups(): group=list(self.coin_manager.get_user_coin_groups().keys())[0]; self.dash_group_combo.setCurrentText(group)
            coins = self.coin_manager.get_user_coin_groups().get(group, [])
            if coins: data=await self.dashboard_handler.get_market_summary(coins); self._populate_dashboard_table(data)
            else: self.dashboard_table.setRowCount(0); self.status_bar.showMessage(f"Brak coinów w grupie '{group}'.", 3000)
            self.set_ui_state(True)
    def _populate_dashboard_table(self, data: list):
        self.dashboard_table.setSortingEnabled(False); 
        self.dashboard_table.setRowCount(0); 
        headers = ["Symbol", "Cena ($)", "Zmiana 24h", "Wolumen 24h", "Rekom. Bota", "Zgodność Sygn.", "Faza Rynku", "Zmienność", "Siła Wzgl. (BTC 7d)", "Potenc. Squeeze", "TV (1h)", "TV (4h)", "TV (1d)"]
        self.dashboard_table.setColumnCount(len(headers))
        self.dashboard_table.setHorizontalHeaderLabels(headers)
        self.dashboard_table.setRowCount(len(data))
        
        reco_colors = {"KUPUJ": QColor("#2ECC71"), "SPRZEDAJ": QColor("#E74C3C"), "NEUTRALNIE": QColor("#95A5A6")}
        conf_colors = {"WZROSTOWA": QColor("#2ECC71"), "SPADKOWA": QColor("#E74C3C"), "KONFLIKT": QColor("#E67E22"), "MIESZANE": QColor("#95A5A6")}
        squeeze_colors = {"Wysoki": QColor("#f39c12"), "Średni": QColor("#f1c40f")}                                                                                 
        column_map = {
        "Symbol": {"key": "symbol"}, 
        "Cena ($)": {"key": "price", "format": "{:,.4f}"}, 
        "Zmiana 24h": {"key": "change_24h", "format": "{:+.2f}%", "color_value": True}, 
        "Wolumen 24h": {"key": "volume_24h", "format": "{:,.0f}"}, 
        "Rekom. Bota": {"key": "bot_reco", "color_map": reco_colors}, 
        "Zgodność Sygn.": {"key": "confluence", "color_map": conf_colors}, 
        "Faza Rynku": {"key": "dist_from_ema200", "format": "{:+.2f}%", "color_value": True}, 
        "Zmienność": {"key": "atr_percent", "format": "{:.2f}%"},
        "Siła Wzgl. (BTC 7d)": {"key": "relative_strength_btc_7d", "format": "{:+.2f}%", "color_value": True}, # <--- NOWE
        "Potenc. Squeeze": {"key": "short_squeeze_potential", "color_map": squeeze_colors}, # <--- NOWE
        "TV (1h)": {"key": "tv_1h"}, 
        "TV (4h)": {"key": "tv_4h"}, 
        "TV (1d)": {"key": "tv_1d"}
    }
        for row, coin in enumerate(data):
            for col, col_name in enumerate(headers):
                config = column_map.get(col_name);
                if not config: continue
                raw_value = coin.get(config["key"]); value_str = "N/A"; item = QTableWidgetItem()
                if isinstance(raw_value, (int, float)): item.setData(Qt.ItemDataRole.EditRole, raw_value)
                if raw_value is not None:
                    try: value_str = config.get("format", "{}").format(raw_value)
                    except (ValueError, TypeError): value_str = str(raw_value)
                item.setText(value_str)
                if "color_map" in config:
                    for keyword, color in config["color_map"].items():
                        if keyword in value_str: item.setForeground(color); item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold)); break
                elif config.get("color_value") and isinstance(raw_value, (int, float)):
                    if raw_value > 0: item.setForeground(QColor("#2ECC71"))
                    elif raw_value < 0: item.setForeground(QColor("#E74C3C"))
                self.dashboard_table.setItem(row, col, item)
        self.dashboard_table.setSortingEnabled(True); self.dashboard_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents); self.dashboard_table.horizontalHeader().setStretchLastSection(True)
    
    def _handle_dashboard_auto_refresh(self, state):
        if state == Qt.CheckState.Checked.value: self.dashboard_timer.start()
        else: self.dashboard_timer.stop()

    def _on_dashboard_symbol_selected(self, row, col):
        """
        NOWA WERSJA: Deleguje zadanie znalezienia i przeanalizowania coina
        do zakładki AnalysisTab.
        """
        if self._analysis_lock.locked():
            self.update_status_message("Poczekaj, aż bieżąca analiza się zakończy!")
            return

        item = self.dashboard_table.item(row, 0) # Pobieramy item z symbolem (kolumna 0)
        if not item:
            return

        symbol = item.text()

        # Przełączamy się na zakładkę analizy (indeks 1)
        self.tabs.setCurrentIndex(1) 

        # Prosimy zakładkę analizy, aby znalazła i wybrała odpowiedni coin
        self.analysis_tab.select_and_analyze_coin(symbol)

    def _handle_ssnedam_state_changed(self):
        enabled = self.settings_manager.get('ssnedam.enabled', False)
        if self.ssnedam_timer.isActive(): self.ssnedam_timer.stop()
        if enabled:
            interval_minutes = self.settings_manager.get('ssnedam.interval_minutes', 15); interval_ms = interval_minutes * 60 * 1000
            self.ssnedam_timer.setInterval(interval_ms); self.ssnedam_timer.start(); QTimer.singleShot(0, self._ssnedam_scan_loop)
            logger.info(f"Ssnedam (RE)AKTYWOWANY. Interwał: {interval_minutes} min.")
        else: logger.info("Ssnedam pozostaje WYŁĄCZONY.")

    def _ssnedam_scan_loop(self):
        if self._analysis_lock.locked():
            logger.warning("[Ssnedam] Skanowanie pominięte, trwa inna analiza.")
            return
        
        if self.settings_manager.get('ssnedam.enabled', False):
            # --- NOWA, BARDZIEJ ODPORNA LOGIKA ---
            user_groups = self.coin_manager.get_user_coin_groups()
            if not user_groups:
                logger.warning("[Ssnedam] Brak jakichkolwiek grup monet do przeskanowania.")
                return

            # Sprawdzamy, czy grupa z ustawień jest poprawna
            group_from_settings = self.settings_manager.get('ssnedam.group', '')
            
            # Jeśli grupa z ustawień nie istnieje na liście, bierzemy pierwszą dostępną
            if group_from_settings not in user_groups:
                target_group_name = list(user_groups.keys())[0]
                logger.warning(f"[Ssnedam] Grupa '{group_from_settings}' nie istnieje. Używam pierwszej dostępnej: '{target_group_name}'.")
            else:
                target_group_name = group_from_settings

            coins = user_groups.get(target_group_name, [])
            # --- KONIEC NOWEJ LOGIKI ---
            
            if coins:
                asyncio.create_task(self.ssnedam.scan_for_alerts(coins, self.on_alert_triggered))
            else:
                logger.warning(f"[Ssnedam] Brak coinów w grupie '{target_group_name}' do przeskanowania.")
            
    def closeEvent(self, event):
        if (loop := asyncio.get_event_loop()).is_running():
            logger.info("Zamykanie aplikacji, zatrzymywanie zadań w tle...")

            # Zatrzymujemy pętlę PaperTradera
            if hasattr(self, 'paper_trader'):
                self.paper_trader.stop()

            # Zbieramy zadania do zamknięcia
            shutdown_tasks = [
                self.ssnedam.close(),
                self.analyzer.exchange_service.close_all_exchanges(), 
                self.coin_manager.close_exchanges()
            ]
            shutdown_task = asyncio.gather(*shutdown_tasks)
            loop.run_until_complete(shutdown_task)

            # --- DODAJ TĘ LINIĘ ---
            # Zamykamy połączenie z bazą danych na samym końcu
            self.db_manager.close()
            # --- KONIEC DODAWANIA ---

        event.accept()

    def _on_analysis_requested(self, payload: dict):
        """Slot reagujący na prośbę o analizę z AnalysisTab."""
        if self._analysis_lock.locked():
            return
        # Aktualizujemy stan MainWindow na podstawie danych z sygnału
        self.current_symbol = payload["symbol"]
        self.current_exchange = payload["exchange"]
        self.current_interval = payload["interval"]
        mode = payload["trade_mode"]
        
        # Uruchamiamy zadanie analizy
        asyncio.create_task(self._execute_full_analysis_task(mode))

    def _on_group_action_requested(self, action: str, data: dict):
        """Slot obsługujący akcje na grupach (dodawanie/usuwanie)."""
        if action == 'add':
            asyncio.create_task(self._group_worker(self.coin_manager.add_group, data['name']))
        elif action == 'remove':
            asyncio.create_task(self._group_worker(self.coin_manager.remove_group, data['name']))

    def _on_coin_action_requested(self, action: str, data: dict):
        """Slot obsługujący akcje na coinach."""
        if action == 'show_add_dialog':
            self.add_coin_to_group() # Używamy istniejącej metody z MainWindow
        elif action == 'remove':
            asyncio.create_task(self._coin_worker(self.coin_manager.remove_coin_from_group, data['group'], data['symbol'], data['exchange']))

    def on_alert_triggered(self, alert_data: AlertData):
        """
        Slot, który jest wywołyany przez Ssnedam. Przekazuje alert do zakładki Alerty.
        """
        # Przełączamy się na zakładkę z alertami
        if hasattr(self, 'alerts_tab_index'):
            self.tabs.setCurrentIndex(self.alerts_tab_index)

        # Delegujemy zadanie do odpowiedniej zakładki
        self.alerts_tab.add_alert_to_list(alert_data) 

    def pause_background_tasks(self):
        """Zatrzymuje timery Ssnedam i PaperTradera."""
        if self.ssnedam_timer.isActive():
            self.ssnedam_timer.stop()
            logger.info("Timer Ssnedam wstrzymany na czas backtestu.")
        if hasattr(self, 'paper_trader_timer') and self.paper_trader_timer.isActive():
            self.paper_trader_timer.stop()
            logger.info("Timer PaperTradera wstrzymany na czas backtestu.")

    def resume_background_tasks(self):
        """Wznawia timery Ssnedam i PaperTradera."""
        self._handle_ssnedam_state_changed() # Ta funkcja już poprawnie uruchamia Ssnedam
        if hasattr(self, 'paper_trader_timer'):
            self.paper_trader_timer.start()
            logger.info("Timer PaperTradera wznowiony.")
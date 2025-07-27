import logging
import asyncio
from typing import Dict, Any, List
from .analysis_tab_helpers import export_widget_to_image_bytes

import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QListWidget, QTableWidget,
    QTextBrowser, QSplitter, QListWidgetItem
)

# Te importy są kluczowe, bo przenosimy tu logikę rysowania
from core.analyzer import TechnicalAnalyzer
from core.settings_manager import SettingsManager
from core.ssnedam import AlertData
from .analysis_tab_helpers import (
    draw_chart_with_features, populate_indicator_summary_table, generate_html_from_analysis
)

logger = logging.getLogger(__name__)


class AlertsTab(QWidget):
    # Sygnał będzie przenosił obiekt AlertData oraz listę obrazków w formacie bytes
    alert_ready_for_dispatch = pyqtSignal(object, list) 

    def __init__(self, settings_manager: SettingsManager, analyzer: TechnicalAnalyzer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.analyzer = analyzer
        self.current_alert_data = None
        self._setup_ui()

    def _setup_ui(self):
        """Tworzy interfejs użytkownika dla zakładki Alerty w nowym, trzykolumnowym układzie."""
        layout = QHBoxLayout(self)
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Kolumna 1: Analiza tekstowa (po lewej) ---
        self.alert_details_text = QTextBrowser()
        self.alert_details_text.setMinimumWidth(300)

        # --- Kolumna 2: Wykres (na środku) ---
        self.alert_chart_widget = pg.GraphicsLayoutWidget()

        # --- Kolumna 3: Lista alertów i wnioski (po prawej) ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        right_layout.addWidget(QLabel("Wykryte Alerty:"))
        self.alerts_list_widget = QListWidget()
        self.alerts_list_widget.itemClicked.connect(self._on_alert_selected) # Podłączamy sygnał
        right_layout.addWidget(self.alerts_list_widget, 1)

        right_layout.addWidget(QLabel("Kluczowe Wnioski Techniczne (Alert):"))
        self.alert_summary_table = QTableWidget()
        right_layout.addWidget(self.alert_summary_table, 1)

        main_splitter.addWidget(self.alert_details_text)
        main_splitter.addWidget(self.alert_chart_widget)
        main_splitter.addWidget(right_panel)

        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 6)
        main_splitter.setStretchFactor(2, 2)

        layout.addWidget(main_splitter)

    def add_alert_to_list(self, alert_data: AlertData):
        """Dodaje nowy alert na górę listy i go zaznacza."""
        logger.info(f"Otrzymano nowy, potwierdzony alert dla {alert_data.symbol} w AlertsTab!")

        title = f"🔔 {alert_data.symbol} ({alert_data.interval}) - {alert_data.setup_data.get('type', 'N/A')}"

        list_item = QListWidgetItem(title)
        list_item.setData(Qt.ItemDataRole.UserRole, alert_data)

        self.alerts_list_widget.insertItem(0, list_item)
        self.alerts_list_widget.setCurrentRow(0)

        # Automatycznie wyświetl szczegóły nowego alertu
        self._display_alert_details(alert_data, dispatch_notification=True)

    def _on_alert_selected(self, item: QListWidgetItem):
        """Slot wywoływany po kliknięciu elementu na liście alertów."""
        alert_data: AlertData | None = item.data(Qt.ItemDataRole.UserRole)
        if alert_data:
            # Tutaj tylko wyświetlamy, więc nie ustawiamy flagi
            self._display_alert_details(alert_data) 

    def _display_alert_details(self, alert_data: AlertData, dispatch_notification: bool = False):
        """
        Główna metoda orkiestrująca wyświetlaniem szczegółów alertu.
        Uruchamiana asynchronicznie, aby nie blokować UI.
        """
        self.current_alert_data = alert_data
        asyncio.create_task(self._visualize_alert_task(alert_data, dispatch_notification))

    async def _visualize_alert_task(self, alert_data: AlertData, dispatch_notification: bool):
        """
        Zadanie asynchroniczne, które pobiera dane, rysuje wykres,
        GENERUJE OBRAZY i emituje sygnał gotowości do wysyłki.
        """
        self.alert_details_text.setHtml(generate_html_from_analysis(alert_data.parsed_data))

        # POPRAWKA: Używamy nowego exchange_service
        exchange_instance = await self.analyzer.exchange_service.get_exchange_instance(alert_data.exchange)
        if not exchange_instance:
            logger.error(f"Nie można utworzyć instancji giełdy dla alertu: {alert_data.exchange}")
            return

        # POPRAWKA: Używamy nowego exchange_service
        df = await self.analyzer.exchange_service.fetch_ohlcv(exchange_instance, alert_data.symbol, alert_data.interval)
        if df is None or df.empty:
            logger.error(f"Nie udało się pobrać danych OHLCV dla alertu: {alert_data.symbol}")
            return

        # POPRAWKA: Używamy nowego indicator_service
        df_with_indicators = self.analyzer.indicator_service.calculate_all(df.copy())

        populate_indicator_summary_table(
            {alert_data.interval: {"interpreted": self.analyzer.indicator_service.interpret_all(df_with_indicators)}}, 
            self.alert_summary_table
        )

        fvgs = self.analyzer.pattern_service.find_fair_value_gaps(df_with_indicators)
        sr_levels = alert_data.parsed_data.get('support_resistance')

        # --- KROK 2.1: Rysowanie i eksport WIDOKU OGÓLNEGO ---
        draw_chart_with_features(
            self, plot_widget=self.alert_chart_widget, df=df_with_indicators,
            setup=alert_data.setup_data, fvgs=fvgs, sr_levels=sr_levels,
            title=f"Alert dla {alert_data.symbol} ({alert_data.interval})",
            alert_data=alert_data
        )
        await asyncio.sleep(0.2)
        overview_image_bytes = export_widget_to_image_bytes(self.alert_chart_widget)

        # --- KROK 2.2: Przygotowanie i eksport WIDOKU PRZYBLIŻONEGO ---
        setup_data = alert_data.parsed_data.get('setup', {})
        prices_in_setup = [setup_data.get('entry', 0), setup_data.get('stop_loss', 0)] + setup_data.get('take_profit', [])
        valid_prices = [p for p in prices_in_setup if isinstance(p, (int, float)) and p > 0]
        
        if not valid_prices: 
            logger.warning("Brak poprawnych cen w setupie do wyznaczenia przybliżenia.")
            if dispatch_notification:
                self.alert_ready_for_dispatch.emit(alert_data, [overview_image_bytes])
            return

        min_price, max_price = min(valid_prices), max(valid_prices)
        price_padding = (max_price - min_price) * 0.2
        last_50_candles = df_with_indicators.iloc[-50:]

        zoom_range = {
            'x_min': last_50_candles.index[0].timestamp(), 
            'x_max': last_50_candles.index[-1].timestamp(),
            'y_min': min_price - price_padding, 
            'y_max': max_price + price_padding,
        }

        draw_chart_with_features(
            self, plot_widget=self.alert_chart_widget, df=df_with_indicators,
            setup=alert_data.setup_data, fvgs=fvgs, sr_levels=sr_levels,
            title=f"Przybliżenie na setup dla {alert_data.symbol}",
            zoom_range=zoom_range,
            alert_data=alert_data
        )
        await asyncio.sleep(0.2)
        zoomed_image_bytes = export_widget_to_image_bytes(self.alert_chart_widget)

        # --- KROK 2.3: EMISJA SYGNAŁU ---
        if dispatch_notification:
            self.alert_ready_for_dispatch.emit(alert_data, [zoomed_image_bytes, overview_image_bytes])
            logger.info(f"Nowy alert {alert_data.symbol} został przygotowany do wysyłki.")
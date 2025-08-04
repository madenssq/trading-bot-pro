import asyncio
import logging
from typing import Dict, Any, List

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QListWidget, QTableWidget,
    QTextBrowser, QSplitter, QListWidgetItem
)

from core.analyzer import TechnicalAnalyzer
from core.settings_manager import SettingsManager
from core.ssnedam import AlertData
from .analysis_tab_helpers import (
    populate_indicator_summary_table, generate_html_from_analysis
)

from .chart_widget import UniversalChartWidget
import pyqtgraph as pg # Potrzebne do eksportu
from core.database_manager import DatabaseManager

logger = logging.getLogger(__name__)


class AlertsTab(QWidget):
    alert_ready_for_dispatch = pyqtSignal(object, list)

    def __init__(self, settings_manager: SettingsManager, analyzer: TechnicalAnalyzer, db_manager: DatabaseManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.analyzer = analyzer
        self.db_manager = db_manager
        self.current_alert_data = None
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        self.alert_details_text = QTextBrowser(minimumWidth=300)
        self.chart_widget = UniversalChartWidget(analyzer=self.analyzer, settings_manager=self.settings_manager, db_manager=self.db_manager)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("Wykryte Alerty:"))
        self.alerts_list_widget = QListWidget()
        right_layout.addWidget(self.alerts_list_widget, 1)
        right_layout.addWidget(QLabel("Kluczowe Wnioski Techniczne (Alert):"))
        self.alert_summary_table = QTableWidget()
        right_layout.addWidget(self.alert_summary_table, 1)

        main_splitter.addWidget(self.alert_details_text)
        main_splitter.addWidget(self.chart_widget)
        main_splitter.addWidget(right_panel)

        main_splitter.setStretchFactor(0, 2); main_splitter.setStretchFactor(1, 6); main_splitter.setStretchFactor(2, 2)
        layout.addWidget(main_splitter)

    def _connect_signals(self):
        self.alerts_list_widget.itemClicked.connect(self._on_alert_selected)

    def add_alert_to_list(self, alert_data: AlertData):
        logger.info(f"Otrzymano nowy, potwierdzony alert dla {alert_data.symbol} w AlertsTab!")
        title = f"🔔 {alert_data.symbol} ({alert_data.interval}) - {alert_data.setup_data.get('type', 'N/A')}"
        list_item = QListWidgetItem(title)
        list_item.setData(Qt.ItemDataRole.UserRole, alert_data)
        self.alerts_list_widget.insertItem(0, list_item)
        self.alerts_list_widget.setCurrentRow(0)
        self._display_alert_details(alert_data, dispatch_notification=True)

    def _on_alert_selected(self, item: QListWidgetItem):
        if alert_data := item.data(Qt.ItemDataRole.UserRole):
            self._display_alert_details(alert_data)

    def _display_alert_details(self, alert_data: AlertData, dispatch_notification: bool = False):
        self.current_alert_data = alert_data
        asyncio.create_task(self._visualize_alert_task(alert_data, dispatch_notification))

    async def _visualize_alert_task(self, alert_data: AlertData, dispatch_notification: bool):
        self.alert_details_text.setHtml(generate_html_from_analysis(alert_data.parsed_data))
        
        exchange = await self.analyzer.get_exchange_instance(alert_data.exchange)
        df = await self.analyzer.fetch_ohlcv(exchange, alert_data.symbol, alert_data.interval)
        if df is not None:
             df_with_indicators = self.analyzer.calculate_all_indicators(df.copy())
             interpreted = self.analyzer._indicator_service.interpret_all(df_with_indicators)
             populate_indicator_summary_table({alert_data.interval: {"interpreted": interpreted}}, self.alert_summary_table)

        overlay_data = {
            "parsed_data": alert_data.parsed_data,
            "alert_timestamp": alert_data.alert_timestamp
        }
        
        await self.chart_widget.display_analysis(
            symbol=alert_data.symbol,
            exchange=alert_data.exchange,
            interval=alert_data.interval,
            overlay_data=overlay_data
        )

        if dispatch_notification:
            await asyncio.sleep(0.5)
            image_bytes = self.chart_widget.export_to_image_bytes()
            if image_bytes:
                self.alert_ready_for_dispatch.emit(alert_data, [image_bytes])
                logger.info(f"Nowy alert {alert_data.symbol} został przygotowany do wysyłki.")
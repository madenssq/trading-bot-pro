import asyncio
import logging
import json
from datetime import datetime
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QFrame, QGridLayout, QSplitter, QComboBox,
    QMessageBox, QRadioButton, QLineEdit, QDateEdit, QTextBrowser
)
from PyQt6.QtCore import Qt, QDate

from core.database_manager import DatabaseManager
from core.analyzer import TechnicalAnalyzer
from core.settings_manager import SettingsManager
from .chart_widget import UniversalChartWidget
from .analysis_tab_helpers import generate_html_from_analysis

logger = logging.getLogger(__name__)

STATUS_MAP = {
    'POTENTIAL': {'label': 'Potencjalny', 'color': QColor("#95A5A6")},
    'ACTIVE': {'label': 'Aktywny', 'color': QColor("#3498DB")},
    'PARTIAL_PROFIT': {'label': 'Częściowy Zysk', 'color': QColor("#5DADE2")},
    'CLOSED_TP': {'label': 'Zysk (TP)', 'color': QColor("#2ECC71")},
    'CLOSED_BE': {'label': 'Zysk (B/E)', 'color': QColor("#A0E6B4")},
    'CLOSED_SL': {'label': 'Strata (SL)', 'color': QColor("#E74C3C")},
    'EXPIRED': {'label': 'Wygasły', 'color': QColor("#707B7C")},
    'CANCELLED': {'label': 'Anulowany', 'color': QColor("#707B7C")},
}

class JournalTab(QWidget):
    def __init__(self, db_manager: DatabaseManager, analyzer: TechnicalAnalyzer, settings_manager: SettingsManager, parent=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.analyzer = analyzer
        self.settings_manager = settings_manager
        self.current_trade = None
        self.current_journal_entries = []
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        main_layout = QHBoxLayout(self)
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        left_panel = self._create_left_panel()
        right_panel = self._create_right_panel()
        main_splitter.addWidget(left_panel); main_splitter.addWidget(right_panel)
        main_splitter.setStretchFactor(0, 4); main_splitter.setStretchFactor(1, 6)
        main_layout.addWidget(main_splitter)

    def _create_left_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        summary_frame = QFrame(); summary_frame.setFrameShape(QFrame.Shape.StyledPanel); summary_layout = QGridLayout(summary_frame)
        self.pnl_label = self._create_summary_label("Całkowity PnL: N/A"); self.win_rate_label = self._create_summary_label("Win Rate: N/A"); self.total_trades_label = self._create_summary_label("Liczba Transakcji: N/A")
        summary_layout.addWidget(self.pnl_label, 0, 0); summary_layout.addWidget(self.win_rate_label, 0, 1); summary_layout.addWidget(self.total_trades_label, 0, 2)
        layout.addWidget(summary_frame)
        filter_layout = QHBoxLayout(); filter_layout.addWidget(QLabel("<b>Pokaż:</b>")); self.filter_all_rb = QRadioButton("Wszystko"); self.filter_all_rb.setChecked(True); self.filter_setup_rb = QRadioButton("Tylko Setupy"); self.filter_analysis_rb = QRadioButton("Tylko Analizy"); filter_layout.addWidget(self.filter_all_rb); filter_layout.addWidget(self.filter_setup_rb); filter_layout.addWidget(self.filter_analysis_rb); filter_layout.addStretch(); layout.addLayout(filter_layout)
        adv_filter_layout = QGridLayout(); self.symbol_filter = QLineEdit(placeholderText="Filtruj po symbolu..."); self.start_date_filter = QDateEdit(calendarPopup=True); self.start_date_filter.setDate(QDate.currentDate().addMonths(-3)); self.end_date_filter = QDateEdit(calendarPopup=True); self.end_date_filter.setDate(QDate.currentDate()); self.outcome_filter = QComboBox(); self.outcome_filter.addItems(["Status: Wszystkie", "Status: Otwarte (Aktywne + Częściowe)", "Status: Potencjalne", "Status: Zamknięte (Wszystkie)", "Status: Zamknięte (Zysk)", "Status: Zamknięte (Strata)", "Status: Zamknięte (B/E)", "Status: Anulowane / Wygasłe"]); adv_filter_layout.addWidget(self.symbol_filter, 0, 0, 1, 2); adv_filter_layout.addWidget(QLabel("Od:"), 1, 0); adv_filter_layout.addWidget(self.start_date_filter, 1, 1); adv_filter_layout.addWidget(QLabel("Do:"), 2, 0); adv_filter_layout.addWidget(self.end_date_filter, 2, 1); adv_filter_layout.addWidget(self.outcome_filter, 3, 0, 1, 2); layout.addLayout(adv_filter_layout)
        top_bar_layout = QHBoxLayout(); top_bar_layout.addStretch(); self.refresh_button = QPushButton("🔄 Odśwież Dane"); self.delete_button = QPushButton("❌ Usuń Zaznaczone"); top_bar_layout.addWidget(self.delete_button); top_bar_layout.addWidget(self.refresh_button); layout.addLayout(top_bar_layout)
        self.trades_table = QTableWidget(); self.trades_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); self.trades_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); layout.addWidget(self.trades_table)
        return panel

    def _create_right_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel); layout.setContentsMargins(0, 0, 0, 0)
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.chart_widget = UniversalChartWidget(self.analyzer, self.settings_manager, self.db_manager)
        self.context_browser = QTextBrowser(); self.context_browser.setPlaceholderText("Wybierz transakcję z listy..."); self.context_browser.setMinimumHeight(150)
        right_splitter.addWidget(self.chart_widget); right_splitter.addWidget(self.context_browser)
        right_splitter.setStretchFactor(0, 4); right_splitter.setStretchFactor(1, 1)
        layout.addWidget(right_splitter)
        return panel

    def _connect_signals(self):
        self.refresh_button.clicked.connect(self.populate_data); self.delete_button.clicked.connect(self._delete_selected_trades) 
        self.trades_table.itemClicked.connect(self._on_trade_selected); self.filter_all_rb.toggled.connect(self.populate_data)
        self.filter_setup_rb.toggled.connect(self.populate_data); self.symbol_filter.textChanged.connect(self.populate_data)
        self.start_date_filter.dateChanged.connect(self.populate_data); self.end_date_filter.dateChanged.connect(self.populate_data)
        self.outcome_filter.currentIndexChanged.connect(self.populate_data)

    def _on_trade_selected(self, item):
        row = item.row()
        if row >= len(self.current_journal_entries): return
        self.current_trade = self.current_journal_entries[row]
        self._display_trade_context(self.current_trade)
        if self.current_trade.get('entry_type') != 'SETUP':
            if self.chart_widget.price_plot_area:
                self.chart_widget.price_plot_area.clear(); self.chart_widget.price_plot_area.setTitle("Wybierz wpis typu 'SETUP', aby zobaczyć wykres.")
            if self.chart_widget.indicator_plot_area: self.chart_widget.indicator_plot_area.clear()
            return
        overlay_data = self._prepare_overlay_data(self.current_trade)
        asyncio.create_task(self.chart_widget.display_analysis(
            symbol=self.current_trade['symbol'], exchange=self.current_trade['exchange'],
            interval=self.current_trade['interval'], overlay_data=overlay_data
        ))
        
    def _prepare_overlay_data(self, trade: dict) -> dict:
        parsed_data = {}; trade_events = []
        if trade:
            if trade.get('full_ai_response_json'):
                try: parsed_data = json.loads(trade['full_ai_response_json'])
                except json.JSONDecodeError: pass
            if 'setup' not in parsed_data:
                parsed_data['setup'] = {'type': trade.get('type'), 'entry': trade.get('entry_price'), 'stop_loss': trade.get('stop_loss'), 'take_profit': [trade.get('take_profit')], 'take_profit_1': trade.get('take_profit_1')}
            trade_events = self.db_manager.get_events_for_trade(trade['id'])
        return {"parsed_data": parsed_data, "alert_timestamp": trade.get('timestamp'), "trade_events": trade_events}

    def _display_trade_context(self, trade: dict):
        if not trade: self.context_browser.setHtml(""); return
        if trade.get('entry_type') != 'SETUP': self.context_browser.setHtml("<h3>Wpis Analityczny</h3><p>To jest ogólny wpis analityczny, nie zawiera szczegółowego setupu.</p>"); return
        full_analysis_json = trade.get('full_ai_response_json')
        if full_analysis_json:
            try: self.context_browser.setHtml(generate_html_from_analysis(json.loads(full_analysis_json)))
            except json.JSONDecodeError: self.context_browser.setHtml("<p>Błąd w formacie zapisanej analizy.</p>")
        else: self.context_browser.setHtml("<p>Brak szczegółowej analizy AI dla tego wpisu.</p>")
        events = self.db_manager.get_events_for_trade(trade['id'])
        if events:
            event_html = "<h4>Historia Zdarzeń:</h4><ul>"
            for event in events:
                event_time = datetime.fromtimestamp(event['timestamp']).strftime('%Y-%m-%d %H:%M'); details = event.get('details', {}); price = details.get('price')
                if event['event_type'] == 'ACTIVATED': event_html += f"<li><b>{event_time}:</b> Pozycja aktywowana przy cenie {price:.4f}</li>"
                elif event['event_type'] == 'TP1_HIT': event_html += f"<li><b>{event_time}:</b> Osiągnięto TP1 przy cenie {price:.4f}</li>"
                elif event['event_type'] == 'SL_MOVED_TO_BE': event_html += f"<li><b>{event_time}:</b> Przesunięto SL na Break-Even ({price:.4f})</li>"
            event_html += "</ul>"; self.context_browser.append(event_html)

    def populate_data(self):
        filters = {}
        if self.filter_setup_rb.isChecked(): filters['entry_type'] = 'SETUP'
        elif self.filter_analysis_rb.isChecked(): filters['entry_type'] = 'ANALYSIS'
        if symbol_query := self.symbol_filter.text().strip().upper(): filters['symbol'] = symbol_query
        filters['start_date'] = self.start_date_filter.dateTime().toPyDateTime().timestamp(); filters['end_date'] = self.end_date_filter.dateTime().toPyDateTime().replace(hour=23, minute=59, second=59).timestamp()
        selected_index = self.outcome_filter.currentIndex()
        if selected_index == 1: filters['status_in'] = ['ACTIVE', 'PARTIAL_PROFIT']
        elif selected_index == 2: filters['status'] = 'POTENTIAL'
        elif selected_index == 3: filters['status_in'] = ['CLOSED_TP', 'CLOSED_SL', 'CLOSED_BE']
        elif selected_index == 4: filters['status_in'] = ['CLOSED_TP', 'CLOSED_BE']
        elif selected_index == 5: filters['status'] = 'CLOSED_SL'
        elif selected_index == 6: filters['status'] = 'CLOSED_BE'
        elif selected_index == 7: filters['status_in'] = ['CANCELLED', 'EXPIRED']
        all_entries = self.db_manager.get_all_trades(filters=filters); self.current_journal_entries = all_entries
        if not all_entries: self.trades_table.setRowCount(0); self.total_trades_label.setText("Liczba Wpisów: 0"); self._update_summary_stats([]); return
        setup_entries = [entry for entry in all_entries if entry['entry_type'] == 'SETUP']; self._update_summary_stats(setup_entries)
        self.total_trades_label.setText(f"Liczba Wpisów: {len(all_entries)}"); self._populate_table(all_entries)

    def _update_summary_stats(self, trades: list):
        closed_statuses = ['CLOSED_TP', 'CLOSED_SL', 'CLOSED_BE']; closed_trades = [t for t in trades if t.get('status') in closed_statuses]
        if not closed_trades: self.pnl_label.setText("Całkowity PnL: $0.00"); self.win_rate_label.setText("Win Rate: N/A"); return
        total_pnl = 0.0
        for trade in closed_trades:
            pnl = 0.0; entry = float(trade.get('entry_price', 0)); trade_type_mult = 1 if trade.get('type') == 'Long' else -1
            if trade['status'] == 'CLOSED_TP' and trade.get('take_profit'): pnl = (float(trade['take_profit']) - entry) * trade_type_mult
            elif trade['status'] == 'CLOSED_SL' and trade.get('stop_loss'): pnl = (float(trade['stop_loss']) - entry) * trade_type_mult
            elif trade['status'] == 'CLOSED_BE' and trade.get('take_profit_1'): pnl = ((float(trade.get('take_profit_1', entry)) - entry) * trade_type_mult) * 0.5
            total_pnl += pnl
        pnl_color = "#2ECC71" if total_pnl >= 0 else "#E74C3C"; self.pnl_label.setText(f"Całkowity PnL: <span style='color: {pnl_color};'>${total_pnl:,.4f}</span>")
        wins = sum(1 for t in closed_trades if t['status'] in ['CLOSED_TP', 'CLOSED_BE']); win_rate = (wins / len(closed_trades)) * 100 if closed_trades else 0
        self.win_rate_label.setText(f"Win Rate: {win_rate:.2f}%")

    def _populate_table(self, entries: list):
        headers = ["", "Data", "Symbol", "Typ", "Pewność", "Status"]; self.trades_table.setColumnCount(len(headers)); self.trades_table.setHorizontalHeaderLabels(headers); self.trades_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            checkbox_item = QTableWidgetItem(); checkbox_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled); checkbox_item.setCheckState(Qt.CheckState.Unchecked); checkbox_item.setData(Qt.ItemDataRole.UserRole, entry['id'])
            dt_object = datetime.fromtimestamp(entry['timestamp']); date_str = dt_object.strftime('%Y-%m-%d %H:%M')
            status_info = STATUS_MAP.get(entry.get('status'), {'label': 'N/A', 'color': QColor("white")}); status_item = QTableWidgetItem(status_info['label']); status_item.setForeground(status_info['color'])
            self.trades_table.setItem(row, 0, checkbox_item); self.trades_table.setItem(row, 1, QTableWidgetItem(date_str)); self.trades_table.setItem(row, 2, QTableWidgetItem(f"{entry['symbol']} ({entry.get('interval', 'N/A')})"))
            self.trades_table.setItem(row, 3, QTableWidgetItem(str(entry.get('type', 'Analiza')))); self.trades_table.setItem(row, 4, QTableWidgetItem(str(entry.get('confidence', 'N/A')))); self.trades_table.setItem(row, 5, status_item)
        self.trades_table.resizeColumnsToContents(); self.trades_table.setColumnWidth(0, 30)

    def _delete_selected_trades(self):
        ids_to_delete = []
        for row in range(self.trades_table.rowCount()):
            if (checkbox := self.trades_table.item(row, 0)) and checkbox.checkState() == Qt.CheckState.Checked: ids_to_delete.append(checkbox.data(Qt.ItemDataRole.UserRole))
        if not ids_to_delete: QMessageBox.information(self, "Informacja", "Nie zaznaczono nic do usunięcia."); return
        if QMessageBox.question(self, "Potwierdzenie", f"Czy na pewno chcesz usunąć {len(ids_to_delete)} wpisów?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.db_manager.delete_trades(ids_to_delete); self.populate_data()
    
    def _create_summary_label(self, text: str) -> QLabel:
        label = QLabel(text); font = label.font(); font.setPointSize(11); font.setBold(True); label.setFont(font)
        return label
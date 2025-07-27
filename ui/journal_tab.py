import asyncio
import logging
import json
import numpy as np
from datetime import datetime
from PyQt6.QtGui import QColor, QFont

import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QFrame, QGridLayout, QSplitter, QComboBox, 
    QMessageBox, QRadioButton, QLineEdit, QDateEdit, QTreeWidget, QTreeWidgetItem, 
    QCheckBox, QTextBrowser, QGroupBox
)
from PyQt6.QtCore import Qt, QDate, pyqtSignal

from core.database_manager import DatabaseManager
from core.analyzer import TechnicalAnalyzer
from core.settings_manager import SettingsManager
from .analysis_tab_helpers import draw_chart_with_features

from .analysis_tab_helpers import generate_html_from_analysis

logger = logging.getLogger(__name__)

class JournalTab(QWidget):
    status_message_changed = pyqtSignal(str)

    def __init__(self, db_manager: DatabaseManager, analyzer: TechnicalAnalyzer, settings_manager: SettingsManager, parent=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.analyzer = analyzer
        self.settings_manager = settings_manager
        self.current_trade = None
        self.current_journal_entries = []
        
        self.plotted_items = {}
        self.plot_area = None
        self.ohlcv_df = None 
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        main_layout = QHBoxLayout(self)
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Lewy panel (tabela i statystyki) - bez zmian
        self.left_panel = self._create_left_panel()

        # Prawy panel (wykres, kontrolki i NOWY panel kontekstu)
        right_panel = QWidget()
        right_layout = QHBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # --- GŁÓWNA CZĘŚĆ PRAWEGO PANELU (wykres i kontekst) ---
        center_content_widget = QWidget()
        center_content_layout = QVBoxLayout(center_content_widget)

        self.chart_widget = pg.GraphicsLayoutWidget()
        
        # --- NOWY WIDGET: Panel Kontekstu ---
        self.context_box = QGroupBox("Kontekst Decyzji")
        context_layout = QVBoxLayout(self.context_box)
        self.context_browser = QTextBrowser()
        self.context_browser.setPlaceholderText("Wybierz transakcję z listy, aby zobaczyć kontekst.")
        context_layout.addWidget(self.context_browser)
        
        center_content_layout.addWidget(self.chart_widget, 3) # Wykres zajmuje 3/4 miejsca
        center_content_layout.addWidget(self.context_box, 1)  # Kontekst zajmuje 1/4

        # Panel kontrolny wykresu (po prawej stronie)
        self.chart_control_panel = self._create_chart_control_panel()
        
        right_layout.addWidget(center_content_widget, 3) 
        right_layout.addWidget(self.chart_control_panel, 1) 

        main_splitter.addWidget(self.left_panel)
        main_splitter.addWidget(right_panel)
        main_splitter.setStretchFactor(0, 6)
        main_splitter.setStretchFactor(1, 4)

        main_layout.addWidget(main_splitter)
    
    # Metody _create_left_panel i _create_chart_control_panel pozostają bez zmian
    def _create_left_panel(self):
        panel = QWidget()
        left_layout = QVBoxLayout(panel)
        summary_frame = QFrame(); summary_frame.setFrameShape(QFrame.Shape.StyledPanel)
        summary_layout = QGridLayout(summary_frame)
        self.pnl_label = self._create_summary_label("Całkowity PnL: N/A")
        self.win_rate_label = self._create_summary_label("Win Rate: N/A")
        self.total_trades_label = self._create_summary_label("Liczba Transakcji: N/A")
        summary_layout.addWidget(self.pnl_label, 0, 0); summary_layout.addWidget(self.win_rate_label, 0, 1); summary_layout.addWidget(self.total_trades_label, 0, 2)
        left_layout.addWidget(summary_frame)
        filter_layout = QHBoxLayout(); filter_layout.addWidget(QLabel("<b>Pokaż:</b>"))
        self.filter_all_rb = QRadioButton("Wszystko"); self.filter_all_rb.setChecked(True)
        self.filter_setup_rb = QRadioButton("Tylko Setupy"); self.filter_analysis_rb = QRadioButton("Tylko Analizy")
        filter_layout.addWidget(self.filter_all_rb); filter_layout.addWidget(self.filter_setup_rb); filter_layout.addWidget(self.filter_analysis_rb)
        filter_layout.addStretch()
        left_layout.addLayout(filter_layout)
        adv_filter_layout = QGridLayout()
        self.symbol_filter = QLineEdit(placeholderText="Filtruj po symbolu, np. BTC/USDT")
        self.start_date_filter = QDateEdit(calendarPopup=True); self.start_date_filter.setDate(QDate.currentDate().addMonths(-3))
        self.end_date_filter = QDateEdit(calendarPopup=True); self.end_date_filter.setDate(QDate.currentDate())
        self.outcome_filter = QComboBox()
        self.outcome_filter.addItems(["Wynik: Wszystkie", "Wynik: Aktywne", "Wynik: Zysk (TP)", "Wynik: Strata (SL)", "Wynik: Anulowane", "Wynik: Wygasłe"])
        adv_filter_layout.addWidget(self.symbol_filter, 0, 0, 1, 2); adv_filter_layout.addWidget(QLabel("Od:"), 1, 0); adv_filter_layout.addWidget(self.start_date_filter, 1, 1)
        adv_filter_layout.addWidget(QLabel("Do:"), 2, 0); adv_filter_layout.addWidget(self.end_date_filter, 2, 1); adv_filter_layout.addWidget(self.outcome_filter, 3, 0, 1, 2)
        left_layout.addLayout(adv_filter_layout)
        top_bar_layout = QHBoxLayout(); top_bar_layout.addStretch()
        self.refresh_button = QPushButton("🔄 Odśwież Dane"); self.delete_button = QPushButton("❌ Usuń Zaznaczone")
        top_bar_layout.addWidget(self.delete_button); top_bar_layout.addWidget(self.refresh_button)
        left_layout.addLayout(top_bar_layout)
        self.trades_table = QTableWidget()
        self.trades_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.trades_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        left_layout.addWidget(self.trades_table)
        return panel

    def _create_chart_control_panel(self):
        panel = QFrame(maximumWidth=200)
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("<b>Widoczność Elementów</b>"))
        self.plot_items_tree = QTreeWidget(headerHidden=True)
        layout.addWidget(self.plot_items_tree)
        items = {"Wskaźniki": ["EMA", "Wstęgi Bollingera"], "Analiza": ["Poziomy Fibonacciego","Poziomy S/R", "Strefy FVG", "Setup (SL/TP)"]}
        for group, names in items.items():
            parent = QTreeWidgetItem(self.plot_items_tree, [group])
            parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
            parent.setCheckState(0, Qt.CheckState.Checked)
            for name in names:
                child = QTreeWidgetItem(parent, [name])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked)
        
        separator = QFrame(); separator.setFrameShape(QFrame.Shape.HLine); separator.setFrameShadow(QFrame.Shadow.Sunken); layout.addWidget(separator)
        layout.addWidget(QLabel("<b>Kontrola Widoku</b>"))
        self.lock_autorange_check = QCheckBox("Zablokuj auto-dopasowanie")
        layout.addWidget(self.lock_autorange_check)
        self.reset_view_btn = QPushButton("Resetuj Widok")
        layout.addWidget(self.reset_view_btn)
        
        layout.addStretch()
        return panel

    def _on_trade_selected(self, item):
        row = item.row()
        if row < len(self.current_journal_entries):
            self.current_trade = self.current_journal_entries[row]
            
            # --- ZMIANA: Przekazujemy ID transakcji ---
            self._display_trade_context(self.current_trade['id']) 
            
            if self.current_trade.get('entry_type') != 'SETUP':
                self.chart_widget.clear()
                self.chart_widget.addPlot().setTitle("Wybierz wpis typu 'SETUP', aby zobaczyć wykres.")
                return

            asyncio.create_task(self._display_trade_chart())
            
    def _display_trade_context(self, trade_id: int):
        if not self.current_trade:
            self.context_browser.setHtml(""); self.context_browser.setPlaceholderText("Wybierz transakcję.")
            return

        if self.current_trade.get('entry_type') != 'SETUP':
            self.context_browser.setHtml("<h3>Wpis Analityczny</h3><p>To jest ogólny wpis analityczny, nie zawiera szczegółowego setupu.</p>")
            return
        
        # --- NOWA LOGIKA: Wyświetlamy PEŁNĄ analizę AI ---
        full_analysis_json = self.current_trade.get('full_ai_response_json')
        if full_analysis_json:
            try:
                analysis_data = json.loads(full_analysis_json)
                # Używamy istniejącego pomocnika do wygenerowania pięknego raportu
                html_report = generate_html_from_analysis(analysis_data)
                self.context_browser.setHtml(html_report)
            except json.JSONDecodeError:
                self.context_browser.setHtml("<p>Błąd w formacie zapisanej analizy.</p>")
        else:
            # Fallback dla starych transakcji bez zapisanej analizy
            html = "<ul>"
            html += f"<li><b>Pewność (Confidence):</b> {self.current_trade.get('confidence', 'N/A')} / 10</li>"
            html += f"<li><b>Reżim Rynku:</b> {self.current_trade.get('market_regime', 'N/A')}</li>"
            html += "</ul>"
            self.context_browser.setHtml(html)

        # Pobieramy i wyświetlamy historię zdarzeń (TP1, BE)
        events = self.db_manager.get_events_for_trade(trade_id)
        if events:
            event_html = "<h4>Historia Zdarzeń:</h4><ul>"
            for event in events:
                event_time = datetime.fromtimestamp(event['timestamp']).strftime('%Y-%m-%d %H:%M')
                details = event.get('details', {}); price = details.get('price')
                
                if event['event_type'] == 'TP1_HIT':
                    event_html += f"<li><b>{event_time}:</b> Osiągnięto TP1 przy cenie {price:.4f}</li>"
                elif event['event_type'] == 'SL_MOVED_TO_BE':
                     event_html += f"<li><b>{event_time}:</b> Przesunięto SL na Break-Even ({price:.4f})</li>"
            event_html += "</ul>"
            # Dopisujemy historię zdarzeń do istniejącego raportu
            self.context_browser.append(event_html)

    def _connect_signals(self):
        self.refresh_button.clicked.connect(self.populate_data)
        self.delete_button.clicked.connect(self._delete_selected_trades) 
        self.trades_table.itemClicked.connect(self._on_trade_selected)
        self.filter_all_rb.toggled.connect(self.populate_data)
        self.filter_setup_rb.toggled.connect(self.populate_data)
        self.filter_analysis_rb.toggled.connect(self.populate_data)
        self.symbol_filter.textChanged.connect(self.populate_data)
        self.start_date_filter.dateChanged.connect(self.populate_data)
        self.end_date_filter.dateChanged.connect(self.populate_data)
        self.outcome_filter.currentIndexChanged.connect(self.populate_data)
        self.plot_items_tree.itemChanged.connect(self._on_plot_item_toggle)
        self.reset_view_btn.clicked.connect(self._reset_chart_view)

    def _create_summary_label(self, text: str) -> QLabel:
        label = QLabel(text)
        font = label.font(); font.setPointSize(11); font.setBold(True)
        label.setFont(font)
        return label

    def _setup_crosshair(self, plot_area, ohlcv_df, symbol):
        plot_area.setCursor(Qt.CursorShape.BlankCursor)
        if not hasattr(self, 'crosshair_v'):
            self.crosshair_v = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('grey', style=Qt.PenStyle.DashLine))
            self.crosshair_h = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen('grey', style=Qt.PenStyle.DashLine))
        if self.crosshair_v.scene() is not plot_area.scene():
            plot_area.addItem(self.crosshair_v, ignoreBounds=True)
            plot_area.addItem(self.crosshair_h, ignoreBounds=True)
        self.mouse_move_proxy = pg.SignalProxy(
            plot_area.scene().sigMouseMoved, rateLimit=60, 
            slot=lambda evt: self._on_mouse_moved(evt, plot_area, ohlcv_df, symbol)
        )

    def _on_mouse_moved(self, event, plot_area, ohlcv_df, symbol):
        if not plot_area or ohlcv_df.empty:
            self.crosshair_v.hide(); self.crosshair_h.hide()
            return

        pos = event[0]
        if plot_area.sceneBoundingRect().contains(pos):
            mouse_point = plot_area.vb.mapSceneToView(pos)
            self.crosshair_v.setPos(mouse_point.x()); self.crosshair_h.setPos(mouse_point.y())
            self.crosshair_v.show(); self.crosshair_h.show()
            
            numeric_index = ohlcv_df.index.astype(np.int64) // 10**9
            closest_idx = np.searchsorted(numeric_index, mouse_point.x())
            if closest_idx >= len(ohlcv_df): closest_idx = len(ohlcv_df) -1
            candle_data = ohlcv_df.iloc[closest_idx]
            date_str = candle_data.name.strftime('%Y-%m-%d %H:%M')
            status_text = (f"{symbol} | {date_str} | O: {candle_data['Open']:.4f} | H: {candle_data['High']:.4f} | "
                           f"L: {candle_data['Low']:.4f} | C: {candle_data['Close']:.4f} | Wol: {candle_data['Volume']:.2f}")
            self.status_message_changed.emit(status_text)
        else:
            self.crosshair_v.hide(); self.crosshair_h.hide()

    def _on_plot_item_toggle(self, item, column):
        name_map = {"Wskaźniki": "ema", "Wstęgi Bollingera": "bb", "Poziomy Fibonacciego": "fibonacci", "Poziomy S/R": "sr_levels", "Strefy FVG": "fvgs", "Setup (SL/TP)": "setup"}
        key = name_map.get(item.text(0))
        if key:
            for plot_object in self.plotted_items.get(key, []):
                plot_object.setVisible(item.checkState(0) == Qt.CheckState.Checked)

    def _reset_chart_view(self):
        if self.plot_area and not self.lock_autorange_check.isChecked():
            self.plot_area.enableAutoRange(axis='xy')

    def populate_data(self):
        filters = {}
        if self.filter_setup_rb.isChecked(): filters['entry_type'] = 'SETUP'
        elif self.filter_analysis_rb.isChecked(): filters['entry_type'] = 'ANALYSIS'
        if symbol_query := self.symbol_filter.text().strip().upper(): filters['symbol'] = symbol_query
        
        outcome_map = {1: 'ACTIVE', 2: 'TP_HIT', 3: 'SL_HIT', 4: 'ANULOWANY', 5: 'WYGASŁY'}
        if outcome_filter := outcome_map.get(self.outcome_filter.currentIndex()): filters['result'] = outcome_filter
            
        filters['start_date'] = self.start_date_filter.dateTime().toPyDateTime().timestamp()
        filters['end_date'] = self.end_date_filter.dateTime().toPyDateTime().replace(hour=23, minute=59, second=59).timestamp()

        all_entries = self.db_manager.get_all_trades(filters=filters)
        self.current_journal_entries = all_entries
        
        if not all_entries:
            self.trades_table.setRowCount(0); self.pnl_label.setText("Całkowity PnL: N/A")
            self.win_rate_label.setText("Win Rate: N/A"); self.total_trades_label.setText(f"Liczba Wpisów: 0")
            return

        setup_entries = [entry for entry in all_entries if entry['entry_type'] == 'SETUP']
        self._update_summary_stats(setup_entries)
        self.total_trades_label.setText(f"Liczba Wpisów: {len(all_entries)}")
        self._populate_table(all_entries)

    def _update_summary_stats(self, trades: list):
        closed_trades = [t for t in trades if t['result'] in ['TP_HIT', 'SL_HIT']]
        if not closed_trades:
            self.pnl_label.setText("Całkowity PnL: $0.00"); self.win_rate_label.setText("Win Rate: 0.00%")
            return

        total_pnl = 0
        for trade in closed_trades:
            pnl = 0; entry = float(trade['entry_price'])
            if trade['type'] == 'Long':
                if trade['result'] == 'TP_HIT': pnl = float(trade['take_profit']) - entry
                elif trade['result'] == 'SL_HIT': pnl = float(trade['stop_loss']) - entry
            elif trade['type'] == 'Short':
                if trade['result'] == 'TP_HIT': pnl = entry - float(trade['take_profit'])
                elif trade['result'] == 'SL_HIT': pnl = entry - float(trade['stop_loss'])
            total_pnl += pnl

        pnl_color = "green" if total_pnl >= 0 else "red"
        self.pnl_label.setText(f"Całkowity PnL: <span style='color: {pnl_color};'>${total_pnl:,.4f}</span>")
        wins = sum(1 for t in closed_trades if t['result'] == 'TP_HIT')
        win_rate = (wins / len(closed_trades)) * 100 if closed_trades else 0
        self.win_rate_label.setText(f"Win Rate: {win_rate:.2f}%")

    def _populate_table(self, entries: list):
        headers = ["", "Data", "Symbol", "Typ", "Confidence", "Reżim Rynku", "Momentum", "Entry", "SL", "TP", "Status"]
        self.trades_table.setColumnCount(len(headers)); self.trades_table.setHorizontalHeaderLabels(headers)
        self.trades_table.setRowCount(len(entries))

        for row, entry in enumerate(entries):
            checkbox_item = QTableWidgetItem()
            checkbox_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            checkbox_item.setCheckState(Qt.CheckState.Unchecked)
            checkbox_item.setData(Qt.ItemDataRole.UserRole, entry['id'])
            dt_object = datetime.fromtimestamp(entry['timestamp'])
            date_str = dt_object.strftime('%Y-%m-%d %H:%M')
            
            self.trades_table.setItem(row, 0, checkbox_item)
            self.trades_table.setItem(row, 1, QTableWidgetItem(date_str))
            self.trades_table.setItem(row, 2, QTableWidgetItem(f"{entry['symbol']} ({entry.get('interval', 'N/A')})"))

            if entry['entry_type'] == 'SETUP':
                status_text = entry['result']; is_active = entry.get('is_active', 0) == 1
                status_item = QTableWidgetItem()
                if is_active and status_text == 'PENDING': status_item.setText("Aktywny"); status_item.setForeground(QColor("#3498DB"))
                else:
                    status_item.setText(status_text)
                    if status_text == 'TP_HIT': status_item.setForeground(QColor("#2ECC71"))
                    elif status_text == 'SL_HIT': status_item.setForeground(QColor("#E74C3C"))
                    elif status_text in ['ANULOWANY', 'WYGASŁY']: status_item.setForeground(QColor("#95A5A6"))
                
                self.trades_table.setItem(row, 3, QTableWidgetItem(str(entry.get('type', 'N/A'))))
                self.trades_table.setItem(row, 4, QTableWidgetItem(str(entry.get('confidence', 'N/A'))))
                self.trades_table.setItem(row, 5, QTableWidgetItem(str(entry.get('market_regime', 'N/A'))))
                self.trades_table.setItem(row, 6, QTableWidgetItem(str(entry.get('momentum_status', 'N/A'))))
                self.trades_table.setItem(row, 7, QTableWidgetItem(f"{entry.get('entry_price', 0):.4f}"))
                self.trades_table.setItem(row, 8, QTableWidgetItem(f"{entry.get('stop_loss', 0):.4f}"))
                self.trades_table.setItem(row, 9, QTableWidgetItem(f"{entry.get('take_profit', 0):.4f}"))
                self.trades_table.setItem(row, 10, status_item)
            else:
                self.trades_table.setItem(row, 3, QTableWidgetItem("Analiza"))
                for col in range(4, 11): self.trades_table.setItem(row, col, QTableWidgetItem("N/A"))

        self.trades_table.resizeColumnsToContents()
        self.trades_table.setColumnWidth(0, 30)

    async def _display_trade_chart(self):
        if not self.current_trade: return
        trade = self.current_trade; symbol = trade['symbol']; interval = trade['interval']
        trade_id = trade['id']
        entry_timestamp_ms = trade['timestamp'] * 1000; exchange_id = trade.get('exchange', 'BINANCE')
        self.chart_widget.clear(); plot = self.chart_widget.addPlot(); plot.setTitle("Ładowanie danych...")
        
        exchange = await self.analyzer.exchange_service.get_exchange_instance(exchange_id)
        if not exchange: plot.setTitle(f"Błąd giełdy {exchange_id}"); return
        
        since_timestamp = int(entry_timestamp_ms - (300 * exchange.parse_timeframe(interval) * 1000))
        df = await self.analyzer.exchange_service.fetch_ohlcv(exchange, symbol, interval, limit=500, since=since_timestamp)
        if df is None or df.empty: plot.setTitle(f"Brak danych dla {symbol} ({interval})"); return
        
        self.ohlcv_df = self.analyzer.indicator_service.calculate_all(df.copy())
        sr_levels = self.analyzer.pattern_service.find_programmatic_sr_levels(self.ohlcv_df, self.analyzer.indicator_service)
        fib_data = self.analyzer.pattern_service.find_fibonacci_retracement(self.ohlcv_df)
        fvgs = self.analyzer.pattern_service.find_fair_value_gaps(self.ohlcv_df)
        
        trade_events = self.db_manager.get_events_for_trade(trade_id)

        from core.ssnedam import AlertData
        dummy_alert = AlertData(symbol="", interval="", setup_data={}, context="", exchange="", raw_ai_response="", parsed_data={}, alert_timestamp=trade['timestamp'])
        
        # --- POPRAWKA: take_profit jest teraz pojedynczą wartością, a nie listą ---
        setup_to_draw = {
            'type': trade['type'], 
            'entry': trade['entry_price'], 
            'stop_loss': trade['stop_loss'], 
            'take_profit': [trade['take_profit']] if trade['take_profit'] else [], # TP2
            'take_profit_1': trade.get('take_profit_1') # TP1
        }

        plot_area = draw_chart_with_features(
            self, plot_widget=self.chart_widget, df=self.ohlcv_df,
            setup=setup_to_draw, sr_levels=sr_levels, fib_data=fib_data, fvgs=fvgs,
            title=f"Podgląd dla {symbol} ({interval})",
            alert_data=dummy_alert,
            trade_events=trade_events
        )
        if plot_area:
            plot_area.enableAutoRange(axis='xy')
            self._setup_crosshair(plot_area, self.ohlcv_df, symbol)

    def _delete_selected_trades(self):
        ids_to_delete = []
        for row in range(self.trades_table.rowCount()):
            checkbox = self.trades_table.item(row, 0)
            if checkbox and checkbox.checkState() == Qt.CheckState.Checked:
                ids_to_delete.append(checkbox.data(Qt.ItemDataRole.UserRole))
        if not ids_to_delete:
            QMessageBox.information(self, "Informacja", "Nie zaznaczono nic do usunięcia.")
            return
        reply = QMessageBox.question(self, "Potwierdzenie", f"Czy na pewno chcesz usunąć {len(ids_to_delete)} wpisów?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.db_manager.delete_trades(ids_to_delete)
            self.populate_data()
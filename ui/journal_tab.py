import asyncio
import logging
import numpy as np
from datetime import datetime, timedelta
from PyQt6.QtGui import QColor, QFont

import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QFrame, QGridLayout, QSplitter, QComboBox, 
    QMessageBox, QRadioButton, QLineEdit, QDateEdit, QTreeWidget, QTreeWidgetItem, QCheckBox
)
from PyQt6.QtCore import Qt, QDate, pyqtSignal

from core.database_manager import DatabaseManager
from core.analyzer import TechnicalAnalyzer
from core.settings_manager import SettingsManager
from .analysis_tab_helpers import draw_chart_with_features

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
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Lewy panel (tabela i statystyki) ---
        self.left_panel = QWidget()
        left_layout = QVBoxLayout(self.left_panel)

        # ... (cały kod summary_frame i filtrów pozostaje bez zmian) ...
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
        self.outcome_filter.addItems([
            "Wynik: Wszystkie", 
            "Wynik: Aktywne",
            "Wynik: Zysk (TP)", 
            "Wynik: Strata (SL)",
            "Wynik: Anulowane", # Zamiast Unieważnione
            "Wynik: Wygasłe"   # NOWA OPCJA
        ])

        adv_filter_layout.addWidget(self.symbol_filter, 0, 0, 1, 2)
        self.outcome_filter = QComboBox(); self.outcome_filter.addItems(["Wynik: Wszystkie", "Wynik: Aktywne", "Wynik: Zysk (TP)", "Wynik: Strata (SL)", "Wynik: Unieważnione"])
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

        # --- Prawy panel (wykres i KONTROLKI) ---
        right_panel = QWidget()
        
        right_layout = QHBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.chart_widget = pg.GraphicsLayoutWidget()
        
        self.chart_control_panel = self._create_chart_control_panel()
        
        right_layout.addWidget(self.chart_widget, 3) 
        right_layout.addWidget(self.chart_control_panel, 2) 

        main_splitter.addWidget(self.left_panel)
        main_splitter.addWidget(right_panel)
        main_splitter.setStretchFactor(0, 7) # Lewy panel (tabela) dostaje 60% miejsca
        main_splitter.setStretchFactor(1, 3) # Prawy panel (wykres) dostaje 40%

        main_layout.addWidget(main_splitter)

    # NOWE: Metoda tworząca panel kontrolny, skopiowana z AnalysisTab
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
        
        # Usunęliśmy stary ComboBox z interwałami, bo jest zbędny
        layout.addStretch()
        return panel
    
    def _create_summary_label(self, text: str) -> QLabel:
        """Tworzy i stylizuje etykietę do panelu podsumowania."""
        label = QLabel(text)
        font = label.font()
        font.setPointSize(11)
        font.setBold(True)
        label.setFont(font)
        return label

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

    def _populate_table(self, entries: list):
        
        headers = ["", "Data", "Symbol", "Typ", "Confidence", "Reżim Rynku", "Momentum", "Entry", "SL", "TP", "Status"]
        self.trades_table.setColumnCount(len(headers)); self.trades_table.setHorizontalHeaderLabels(headers); self.trades_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            
            pass 

        # POPRAWKA: Automatyczne dopasowanie szerokości kolumn
        self.trades_table.resizeColumnsToContents()
        self.trades_table.horizontalHeader().setStretchLastSection(True)
        self.trades_table.setColumnWidth(0, 30)

            
    # --- NOWE METODY POMOCNICZE (skopiowane z AnalysisTab) ---
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
        name_map = {"EMA": "ema", "Wstęgi Bollingera": "bb", "Poziomy Fibonacciego": "fibonacci", "Poziomy S/R": "sr_levels", "Strefy FVG": "fvgs", "Setup (SL/TP)": "setup"}
        key = name_map.get(item.text(0))
        if key:
            for plot_object in self.plotted_items.get(key, []):
                plot_object.setVisible(item.checkState(0) == Qt.CheckState.Checked)

    def _reset_chart_view(self):
        if self.plot_area and not self.lock_autorange_check.isChecked():
            self.plot_area.enableAutoRange(axis='xy')

    def populate_data(self):
        """
        Zbiera dane ze wszystkich filtrów, pobiera wpisy z bazy 
        i aktualizuje cały widok dziennika.
        """
        logger.info("Odświeżanie danych w Dzienniku Aktywności z filtrami...")

        # Krok 1: Zbierz wartości ze wszystkich kontrolek filtrów
        filters = {}

        # Filtr typu (Radio)
        if self.filter_setup_rb.isChecked():
            filters['entry_type'] = 'SETUP'
        elif self.filter_analysis_rb.isChecked():
            filters['entry_type'] = 'ANALYSIS'

        # Filtr symbolu (LineEdit)
        symbol_query = self.symbol_filter.text().strip().upper()
        if symbol_query:
            filters['symbol'] = symbol_query

        # Filtr wyniku (ComboBox)
        outcome_idx = self.outcome_filter.currentIndex()
        if outcome_idx == 1: # Aktywne
            filters['result'] = 'ACTIVE' # Użyjemy specjalnego słowa kluczowego
        elif outcome_idx == 2: # Zysk (TP)
            filters['result'] = 'TP_HIT'
        elif outcome_idx == 3: # Strata (SL)
            filters['result'] = 'SL_HIT'
            
        # Filtr daty (DateEdit) - konwertujemy na timestampy
        start_dt = self.start_date_filter.dateTime().toPyDateTime()
        end_dt = self.end_date_filter.dateTime().toPyDateTime()
        filters['start_date'] = start_dt.timestamp()
        # Dodajemy 23:59:59 do daty końcowej, aby objąć cały dzień
        filters['end_date'] = end_dt.replace(hour=23, minute=59, second=59).timestamp()

        # Krok 2: Pobierz odfiltrowane dane z bazy
        all_entries = self.db_manager.get_all_trades(filters=filters)
        self.current_journal_entries = all_entries
        
        # Krok 3: Zaktualizuj UI (logika z poprzednich wersji)
        if not all_entries:
            self.trades_table.setRowCount(0)
            self.pnl_label.setText("Całkowity PnL: N/A")
            self.win_rate_label.setText("Win Rate: N/A")
            self.total_trades_label.setText(f"Liczba Wpisów: 0")
            return

        # Statystyki liczymy tylko dla odfiltrowanych setupów
        setup_entries = [entry for entry in all_entries if entry['entry_type'] == 'SETUP']
        self._update_summary_stats(setup_entries) # Ta metoda liczy statystyki tylko dla setupów
        
        self.total_trades_label.setText(f"Liczba Wpisów: {len(all_entries)}")
        
        self._populate_table(all_entries)

    def _update_summary_stats(self, trades: list):
        """Oblicza i aktualizuje statystyki podsumowujące z poprawioną logiką PnL."""
        closed_trades = [t for t in trades if t['result'] in ['TP_HIT', 'SL_HIT']]
        
        if not closed_trades:
            self.pnl_label.setText("Całkowity PnL: $0.00")
            self.win_rate_label.setText("Win Rate: 0.00%")
            self.total_trades_label.setText(f"Liczba Transakcji: {len(trades)}")
            return

        # --- POPRAWIONA LOGIKA OBLICZANIA PNL ---
        total_pnl = 0
        for trade in closed_trades:
            pnl = 0
            entry = float(trade['entry_price'])
            
            if trade['type'] == 'Long':
                if trade['result'] == 'TP_HIT':
                    pnl = float(trade['take_profit']) - entry
                elif trade['result'] == 'SL_HIT':
                    pnl = float(trade['stop_loss']) - entry
            elif trade['type'] == 'Short':
                if trade['result'] == 'TP_HIT':
                    pnl = entry - float(trade['take_profit'])
                elif trade['result'] == 'SL_HIT':
                    pnl = entry - float(trade['stop_loss'])
            
            total_pnl += pnl

        pnl_color = "green" if total_pnl >= 0 else "red"
        self.pnl_label.setText(f"Całkowity PnL: <span style='color: {pnl_color};'>${total_pnl:,.4f}</span>")

        # Obliczanie Win Rate (bez zmian, było poprawne)
        wins = sum(1 for t in closed_trades if t['result'] == 'TP_HIT')
        win_rate = (wins / len(closed_trades)) * 100 if closed_trades else 0
        self.win_rate_label.setText(f"Win Rate: {win_rate:.2f}%")

        self.total_trades_label.setText(f"Liczba Transakcji: {len(trades)} (Zamkniętych: {len(closed_trades)})")
    def _populate_table(self, entries: list):
        """Wypełnia tabelę danymi, uwzględniając nowe kolumny kontekstowe."""
        # KROK 1: Dodajemy nowe nagłówki
        headers = [
            "", "Data", "Symbol", "Typ", "Confidence", "Reżim Rynku", 
            "Momentum", "Entry", "SL", "TP", "Status"
        ]
        self.trades_table.setColumnCount(len(headers))
        self.trades_table.setHorizontalHeaderLabels(headers)
        self.trades_table.setRowCount(len(entries))

        for row, entry in enumerate(entries):
            # ... (istniejący kod tworzący checkbox i date_str bez zmian) ...
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
                status_text = entry['result']
                is_active = entry.get('is_active', 0) == 1

                status_item = QTableWidgetItem()

                if is_active and status_text == 'PENDING':
                    status_item.setText("Aktywny")
                    status_item.setForeground(QColor("#3498DB")) # Niebieski kolor dla aktywnych
                else:
                    status_item.setText(status_text)
                    if status_text == 'TP_HIT':
                        status_item.setForeground(QColor("#2ECC71")) # Zielony
                    elif status_text == 'SL_HIT':
                        status_item.setForeground(QColor("#E74C3C")) # Czerwony
                    elif status_text in ['ANULOWANY', 'WYGASŁY']: # NOWE STATUSY
                        status_item.setForeground(QColor("#95A5A6")) # Szary
                
                # KROK 2: Wypełniamy wszystkie kolumny dla setupu
                self.trades_table.setItem(row, 3, QTableWidgetItem(str(entry.get('type', 'N/A'))))
                self.trades_table.setItem(row, 4, QTableWidgetItem(str(entry.get('confidence', 'N/A'))))
                self.trades_table.setItem(row, 5, QTableWidgetItem(str(entry.get('market_regime', 'N/A')))) # NOWA
                self.trades_table.setItem(row, 6, QTableWidgetItem(str(entry.get('momentum_status', 'N/A')))) # NOWA
                self.trades_table.setItem(row, 7, QTableWidgetItem(f"{entry.get('entry_price', 0):.4f}"))
                self.trades_table.setItem(row, 8, QTableWidgetItem(f"{entry.get('stop_loss', 0):.4f}"))
                self.trades_table.setItem(row, 9, QTableWidgetItem(f"{entry.get('take_profit', 0):.4f}"))
                self.trades_table.setItem(row, 10, status_item)
            else: # Dla wpisu typu 'ANALYSIS'
                self.trades_table.setItem(row, 3, QTableWidgetItem("Analiza"))
                # Wypełniamy resztę kolumn pustymi danymi
                for col in range(4, 11):
                    self.trades_table.setItem(row, col, QTableWidgetItem("N/A"))

        self.trades_table.resizeColumnsToContents()
        self.trades_table.setColumnWidth(0, 30)

    def _on_trade_selected(self, item):
        """Reaguje na kliknięcie wpisu w tabeli i loguje każdy krok."""
        print("\n" + "="*20 + " DIAGNOSTYKA KLIKNIĘCIA W DZIENNIKU " + "="*20)
        try:
            row = item.row()
            all_entries = self.current_journal_entries

            if row < len(all_entries):
                self.current_trade = all_entries[row]
                if not self.current_trade:
                    print("BŁĄD: Nie udało się pobrać danych dla klikniętego wiersza.")
                    print("="*67 + "\n")
                    return

                entry_type = self.current_trade.get('entry_type', 'BRAK')
                print(f"Kliknięto wpis typu: '{entry_type}' dla symbolu: {self.current_trade.get('symbol')}")

                # Sprawdzamy, czy kliknięty wpis to setup, a nie ogólna analiza
                if entry_type != 'SETUP':
                    print("Wykryto typ inny niż 'SETUP'. Wykres nie zostanie narysowany. To jest poprawne zachowanie.")
                    self.chart_widget.clear()
                    self.chart_widget.addPlot().setTitle("Wybierz wpis typu 'SETUP', aby zobaczyć wykres transakcji.")
                    print("="*67 + "\n")
                    return

                print("Wpis to 'SETUP'. Uruchamiam zadanie rysowania wykresu...")
                print("="*67 + "\n")
                asyncio.create_task(self._display_trade_chart())
            else:
                print("BŁĄD: Kliknięty wiersz jest poza zakresem listy wpisów.")
                print("="*67 + "\n")

        except Exception as e:
            print(f"!!! WYSTĄPIŁ KRYTYCZNY BŁĄD W _on_trade_selected: {e}")

    def _on_interval_changed(self, interval: str):
        """Reaguje na zmianę interwału w ComboBox."""
        if self.current_trade:
            asyncio.create_task(self._display_trade_chart(interval))

    async def _display_trade_chart(self):
        try:
            if not self.current_trade: return

            trade = self.current_trade
            symbol = trade['symbol']
            interval = trade['interval']
            entry_timestamp_ms = trade['timestamp'] * 1000
            exchange_id = trade.get('exchange', 'BINANCE')

            self.chart_widget.clear()
            plot = self.chart_widget.addPlot(); plot.setTitle("Ładowanie danych...")
            
            exchange = await self.analyzer.exchange_service.get_exchange_instance(exchange_id)
            if not exchange: plot.setTitle(f"Błąd giełdy {exchange_id}"); return

            interval_duration_ms = exchange.parse_timeframe(interval) * 1000
            since_timestamp = int(entry_timestamp_ms - (300 * interval_duration_ms))
            df = await self.analyzer.exchange_service.fetch_ohlcv(exchange, symbol, interval, limit=500, since=since_timestamp)
            if df is None or df.empty: plot.setTitle(f"Brak danych dla {symbol} ({interval})"); return
            
            self.ohlcv_df = self.analyzer.indicator_service.calculate_all(df.copy())

            # POPRAWKA: Używamy nowego pattern_service dla wszystkich analiz wzorców
            sr_levels = self.analyzer.pattern_service.find_programmatic_sr_levels(self.ohlcv_df, self.analyzer.indicator_service)
            fib_data = self.analyzer.pattern_service.find_fibonacci_retracement(self.ohlcv_df)
            fvgs = self.analyzer.pattern_service.find_fair_value_gaps(self.ohlcv_df)

            from core.ssnedam import AlertData
            dummy_alert = AlertData(symbol="", interval="", setup_data={}, context="", exchange="", raw_ai_response="", parsed_data={}, alert_timestamp=trade['timestamp'])
            
            setup_to_draw = {'type': trade['type'], 'entry': trade['entry_price'], 'stop_loss': trade['stop_loss'], 'take_profit': [trade['take_profit']]}

            plot_area = draw_chart_with_features(
                self, plot_widget=self.chart_widget, df=self.ohlcv_df,
                setup=setup_to_draw, sr_levels=sr_levels, fib_data=fib_data, fvgs=fvgs,
                title=f"Podgląd dla {symbol} ({interval})", alert_data=dummy_alert
            )

            if plot_area:
                plot_area.enableAutoRange(axis='xy')
                self._setup_crosshair(plot_area, self.ohlcv_df, symbol)

        except Exception as e:
            logger.error(f"Krytyczny błąd podczas wyświetlania wykresu w dzienniku: {e}", exc_info=True)
            print(f"\n!!! ZŁAPANO KRYTYCZNY BŁĄD W _display_trade_chart: {e}\n")
            if 'plot' in locals(): 
                plot.setTitle(f"Wystąpił krytyczny błąd: {e}")
            else:
                self.chart_widget.clear()
                self.chart_widget.addPlot().setTitle(f"Krytyczny błąd: {e}")

        except Exception as e:
            logger.error(f"Krytyczny błąd podczas wyświetlania wykresu w dzienniku: {e}", exc_info=True)
            print(f"\n!!! ZŁAPANO KRYTYCZNY BŁĄD W _display_trade_chart: {e}\n")
            if 'plot' in locals(): 
                plot.setTitle(f"Wystąpił krytyczny błąd: {e}")
            else:
                self.chart_widget.clear()
                self.chart_widget.addPlot().setTitle(f"Krytyczny błąd: {e}")
    def _delete_selected_trades(self):
        """Zbiera ID zaznaczonych transakcji i zleca ich usunięcie."""
        ids_to_delete = []
        for row in range(self.trades_table.rowCount()):
            checkbox = self.trades_table.item(row, 0)
            if checkbox and checkbox.checkState() == Qt.CheckState.Checked:
                trade_id = checkbox.data(Qt.ItemDataRole.UserRole)
                ids_to_delete.append(trade_id)

        if not ids_to_delete:
            QMessageBox.information(self, "Informacja", "Nie zaznaczono żadnych transakcji do usunięcia.")
            return

        reply = QMessageBox.question(self, "Potwierdzenie", 
                                    f"Czy na pewno chcesz usunąć {len(ids_to_delete)} zaznaczonych transakcji?",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            self.db_manager.delete_trades(ids_to_delete)
            self.populate_data() # Odświeżamy widok po usunięciu
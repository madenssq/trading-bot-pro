import asyncio
import logging
import re
import json
import pandas as pd
import pyqtgraph as pg
import numpy as np
from datetime import datetime
from typing import Dict, Any, Optional, List

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QTreeWidget, QPushButton,
    QFrame, QComboBox, QCheckBox, QTableWidget, QHeaderView,
    QMenu, QInputDialog, QMessageBox, QDialog, QTreeWidgetItem, QTextBrowser,
    QSplitter, QGraphicsRectItem, QTableWidgetItem
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QPointF, QBuffer
from PyQt6.QtGui import QColor, QImage

from app_config import RAMY_CZASOWE
from .styles import THEMES
from .history_dialog import AddCoinDialog, DateAxis, CandlestickItem
from .analysis_tab_helpers import (
    draw_chart_with_features, populate_indicator_summary_table, generate_html_from_analysis
)

logger = logging.getLogger(__name__)

class AnalysisTab(QWidget):
    analysis_requested = pyqtSignal(dict)
    group_action_requested = pyqtSignal(str, dict)
    coin_action_requested = pyqtSignal(str, dict)
    status_message_changed = pyqtSignal(str)

    def __init__(self, settings_manager, analyzer, db_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.analyzer = analyzer
        self.db_manager = db_manager
        self.last_selected_coin_data = None
        self.plotted_items = {}
        self.ohlcv_df = pd.DataFrame()
        self.plot_area = None
        self.last_analysis_data = None
        self.last_ohlcv_df = None

        self._setup_ui()
        self._connect_signals()
        self._load_initial_settings()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        left_panel = self._create_left_panel()
        center_panel = self._create_center_panel()
        right_panel = self._create_right_panel()
        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(center_panel)
        main_splitter.addWidget(right_panel)
        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 3)
        main_splitter.setStretchFactor(2, 5)
        layout.addWidget(main_splitter)

    def _create_left_panel(self):
        panel = QFrame(minimumWidth=300, maximumWidth=350)
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("Dostępne Coiny"))
        self.coin_filter = QLineEdit(placeholderText="🔍 Filtruj...")
        layout.addWidget(self.coin_filter)
        self.coin_tree = QTreeWidget()
        self.coin_tree.setHeaderLabels(["Coin/Grupa", "Giełda"])
        self.coin_tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.coin_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        layout.addWidget(self.coin_tree)
        group_btns_layout = QHBoxLayout()
        self.add_group_btn = QPushButton("➕ Dodaj Grupę")
        self.remove_group_btn = QPushButton("➖ Usuń Grupę")
        group_btns_layout.addWidget(self.add_group_btn)
        group_btns_layout.addWidget(self.remove_group_btn)
        layout.addLayout(group_btns_layout)
        coin_btns_layout = QHBoxLayout()
        self.add_coin_btn = QPushButton("➕ Dodaj Coin")
        self.remove_coin_btn = QPushButton("➖ Usuń Coin")
        coin_btns_layout.addWidget(self.add_coin_btn)
        coin_btns_layout.addWidget(self.remove_coin_btn)
        layout.addLayout(coin_btns_layout)
        return panel

    def _create_center_panel(self):
        panel = QFrame()
        layout = QVBoxLayout(panel)
        controls_layout = QHBoxLayout()
        controls_layout.addWidget(QLabel("Interwał Analizy:"))
        self.timeframe_selector = QComboBox()
        self.timeframe_selector.addItems(RAMY_CZASOWE)
        controls_layout.addWidget(self.timeframe_selector)
        controls_layout.addStretch()
        layout.addLayout(controls_layout)
        self.analysis_output = QTextBrowser(readOnly=True)
        self.indicator_summary_table = QTableWidget()
        layout.addWidget(QLabel("Wyniki Analizy AI"))
        layout.addWidget(self.analysis_output, 2)
        layout.addWidget(QLabel("Kluczowe Wnioski Techniczne"))
        layout.addWidget(self.indicator_summary_table, 1)
        self.save_analysis_btn = QPushButton("📌 Zapisz do Obserwowanych")
        self.save_analysis_btn.setEnabled(False) # Domyślnie wyłączony
        layout.addWidget(self.save_analysis_btn)
        return panel

    def _create_right_panel(self):
        panel = QWidget()
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        self.graphical_analysis_plot = pg.GraphicsLayoutWidget()
        self.chart_control_panel = self._create_chart_control_panel()
        layout.addWidget(self.graphical_analysis_plot, 4)
        layout.addWidget(self.chart_control_panel, 1)
        return panel
        
    def _create_chart_control_panel(self):
        panel = QFrame(maximumWidth=200)
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("<b>Widoczność Elementów</b>"))
        self.plot_items_tree = QTreeWidget(headerHidden=True)
        layout.addWidget(self.plot_items_tree)
        items = {"Wskaźniki": ["EMA", "Wstęgi Bollingera"], "Analiza": ["Poziomy Fibonacciego","Poziomy S/R od AI", "Strefy FVG", "Setup (SL/TP)", "Poziom Pułapki"]}
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
        return panel

    def _connect_signals(self):
        self.coin_filter.textChanged.connect(self.filter_coin_tree)
        self.coin_tree.itemClicked.connect(self._on_coin_selected_for_analysis)
        self.coin_tree.customContextMenuRequested.connect(self._show_coin_tree_context_menu)
        self.add_group_btn.clicked.connect(self._add_group)
        self.remove_group_btn.clicked.connect(self._remove_group)
        self.add_coin_btn.clicked.connect(self._add_coin_to_group)
        self.remove_coin_btn.clicked.connect(self._remove_coin_from_group_selected_item)
        self.timeframe_selector.currentTextChanged.connect(self._on_coin_selected_for_analysis)
        self.plot_items_tree.itemChanged.connect(self._on_plot_item_toggle)
        self.reset_view_btn.clicked.connect(self._reset_chart_view)

    def _load_initial_settings(self):
        interval = self.settings_manager.get('analysis.default_interval', '1h')
        self.timeframe_selector.setCurrentText(interval)

    # --- Metody obsługujące sygnały (sloty) ---

    def filter_coin_tree(self):
        text = self.coin_filter.text().lower()
        root = self.coin_tree.invisibleRootItem()
        for i in range(root.childCount()):
            group = root.child(i)
            has_match = text in group.text(0).lower()
            for j in range(group.childCount()):
                coin = group.child(j)
                matches = text in coin.text(0).lower()
                coin.setHidden(not matches)
                if matches: has_match = True
            group.setHidden(not has_match)

    def _on_coin_selected_for_analysis(self, item=None, col=None):
        selected_item = self.coin_tree.currentItem()
        if not selected_item: return

        if data := selected_item.data(0, Qt.ItemDataRole.UserRole):
            if data.get("type") == "coin":
                self.last_selected_coin_data = selected_item.data(0, Qt.ItemDataRole.UserRole)
                request_payload = {
                    "symbol": data["symbol"], "exchange": data["exchange"],
                    "interval": self.timeframe_selector.currentText(), "trade_mode": "manual"
                }
                self.analysis_requested.emit(request_payload)

    def _show_coin_tree_context_menu(self, position: QPoint):
        item = self.coin_tree.itemAt(position)
        if not item or not item.parent(): return
        menu = QMenu()
        delete_action = menu.addAction("🗑️ Usuń ten coin")
        action = menu.exec(self.coin_tree.mapToGlobal(position))
        if action == delete_action: self._remove_coin_item(item)

    def _add_group(self):
        name, ok = QInputDialog.getText(self, "Dodaj Grupę", "Nazwa nowej grupy:")
        if ok and name: self.group_action_requested.emit('add', {'name': name})
            
    def _remove_group(self):
        item = self.coin_tree.currentItem()
        if item and not item.parent():
            group_name = item.data(0, Qt.ItemDataRole.UserRole)["name"]
            if group_name in ["Ulubione", "Do Obserwacji"]:
                QMessageBox.warning(self, "Błąd", "Nie można usunąć grup domyślnych.")
                return
            if QMessageBox.question(self, "Potwierdź", f"Usunąć grupę '{group_name}'?") == QMessageBox.StandardButton.Yes:
                self.group_action_requested.emit('remove', {'name': group_name})

    def _add_coin_to_group(self):
        self.coin_action_requested.emit('show_add_dialog', {})

    def _remove_coin_from_group_selected_item(self):
        if not self.last_selected_coin_item:
            QMessageBox.warning(self, "Błąd", "Najpierw kliknij na coina na liście, którego chcesz usunąć.")
            return
        self._remove_coin_item(self.last_selected_coin_item)
    
    def _remove_coin_item(self, item_to_delete):
        if item_to_delete and item_to_delete.parent():
            group_name = item_to_delete.parent().data(0, Qt.ItemDataRole.UserRole)["name"]
            coin_data = item_to_delete.data(0, Qt.ItemDataRole.UserRole)
            if QMessageBox.question(self, "Potwierdź", f"Usunąć {coin_data['symbol']} z grupy '{group_name}'?") == QMessageBox.StandardButton.Yes:
                self.coin_action_requested.emit('remove', {'group': group_name, 'symbol': coin_data['symbol'], 'exchange': coin_data['exchange']})
                if coin_data == self.last_selected_coin_data:
                    self.last_selected_coin_data = None
    
    def _on_plot_item_toggle(self, item, column):
        name_map = {
            "EMA": "ema", "Wstęgi Bollingera": "bb", "Poziomy Fibonacciego": "fibonacci", "Poziomy S/R od AI": "sr_levels",
            "Strefy FVG": "fvgs", "Setup (SL/TP)": "setup", "Poziom Pułapki": "trap_levels"
        }
        key = name_map.get(item.text(0))
        if key:
            for plot_object in self.plotted_items.get(key, []):
                plot_object.setVisible(item.checkState(0) == Qt.CheckState.Checked)

    def _reset_chart_view(self):
        if self.plot_area and not self.ohlcv_df.empty:
            if not self.lock_autorange_check.isChecked():
                self.plot_area.enableAutoRange(axis='xy', enable=True)

    # --- Metody publiczne i do rysowania ---

    def populate_coin_tree(self, groups: dict):
        
        self.coin_tree.clear()
        for name in sorted(groups.keys()):
            parent = QTreeWidgetItem(self.coin_tree, [name])
            parent.setData(0, Qt.ItemDataRole.UserRole, {"type": "group", "name": name})
            for coin in sorted(groups.get(name, []), key=lambda c: c['symbol']):
                child = QTreeWidgetItem(parent, [coin['symbol'].replace('/USDT', ''), coin['exchange']])
                child.setData(0, Qt.ItemDataRole.UserRole, {**coin, "type": "coin"})
            parent.setExpanded(True)

    def update_view(self, ohlcv_df, all_timeframe_data, parsed_data, fvgs, fib_data=None):
        """
        NOWA WERSJA: Przyjmuje i przekazuje do wykresu dane Fibonacciego.
        """
        self.ohlcv_df = self.analyzer.indicator_service.calculate_all(ohlcv_df.copy())
        self.last_analysis_data = parsed_data
        self.last_ohlcv_df = self.ohlcv_df # self.ohlcv_df jest już obliczone z wskaźnikami
        self.save_analysis_btn.setEnabled(True)
        
        final_html_report = generate_html_from_analysis(parsed_data)
        self.analysis_output.setHtml(final_html_report)
        
        populate_indicator_summary_table(all_timeframe_data, self.indicator_summary_table)
        
        self.plot_area = draw_chart_with_features(
            self,
            plot_widget=self.graphical_analysis_plot,
            df=self.ohlcv_df,
            fvgs=fvgs,
            setup=parsed_data.get('setup'),
            sr_levels=parsed_data.get('support_resistance'),
            fib_data=fib_data 
        )
        
        if self.plot_area:
            symbol = ""
            if self.last_selected_coin_data: 
                symbol = self.last_selected_coin_data.get('symbol', '')
            
            self._setup_crosshair(self.plot_area, self.ohlcv_df, symbol)

    

    def _setup_crosshair(self, plot_area, ohlcv_df, symbol):
        """Dodaje i konfiguruje kursor (crosshair) dla danego obszaru wykresu."""
        plot_area.setCursor(Qt.CursorShape.BlankCursor)
        if not hasattr(self, 'crosshair_v'):
            self.crosshair_v = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('grey', style=Qt.PenStyle.DashLine))
            self.crosshair_h = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen('grey', style=Qt.PenStyle.DashLine))
        
        
        if self.crosshair_v.scene() is not plot_area.scene():
            plot_area.addItem(self.crosshair_v, ignoreBounds=True)
            plot_area.addItem(self.crosshair_h, ignoreBounds=True)
        
        # --- KLUCZOWA ZMIANA ---
        # Tworzymy tzw. "proxy" dla sygnału, aby ograniczyć liczbę wywołań
        # i łączymy go z funkcją lambda, która przekazuje wszystkie potrzebne argumenty
        self.mouse_move_proxy = pg.SignalProxy(
            plot_area.scene().sigMouseMoved, 
            rateLimit=60, 
            slot=lambda evt: self._on_mouse_moved(evt, plot_area, ohlcv_df, symbol)
        )

    def _on_mouse_moved(self, event, plot_area, ohlcv_df, symbol):
        if not plot_area or ohlcv_df.empty:
            self.crosshair_v.hide()
            self.crosshair_h.hide()
            return

        pos = event[0]
        if plot_area.sceneBoundingRect().contains(pos):
            mouse_point = plot_area.vb.mapSceneToView(pos)
            self.crosshair_v.setPos(mouse_point.x())
            self.crosshair_h.setPos(mouse_point.y())
            self.crosshair_v.show()
            self.crosshair_h.show()

            # ... (logika aktualizacji paska statusu bez zmian) ...
            numeric_index = ohlcv_df.index.astype(np.int64) // 10**9
            closest_idx = np.searchsorted(numeric_index, mouse_point.x())
            if closest_idx >= len(ohlcv_df): closest_idx = len(ohlcv_df) -1

            candle_data = ohlcv_df.iloc[closest_idx]
            date_str = candle_data.name.strftime('%Y-%m-%d %H:%M')

            status_text = (f"{symbol} | {date_str} | O: {candle_data['Open']:.4f} | H: {candle_data['High']:.4f} | "
                        f"L: {candle_data['Low']:.4f} | C: {candle_data['Close']:.4f} | Wol: {candle_data['Volume']:.2f}")
            self.status_message_changed.emit(status_text)
        else:
          
            self.crosshair_v.hide()
            self.crosshair_h.hide()


    def _reset_chart_view(self):
        if hasattr(self, 'plot_area') and not self.ohlcv_df.empty:
            if hasattr(self, 'lock_autorange_check') and self.lock_autorange_check.isChecked(): return
            self.plot_area.enableAutoRange(axis='xy', enable=True)

    def _on_plot_item_toggle(self, item, column):
        name_map = {
            "EMA": "ema", "Wstęgi Bollingera": "bb", 
            "Poziomy S/R od AI": "sr_levels", "Strefy FVG": "fvgs", 
            "Setup (SL/TP)": "setup",
            "Poziom Pułapki": "trap_levels"
        }
        if not hasattr(self, 'plot_items_tree'): return
        
        key = name_map.get(item.text(0))
        if key:
            for plot_object in self.plotted_items.get(key, []):
                plot_object.setVisible(item.checkState(0) == Qt.CheckState.Checked)

    def _create_left_panel(self):
        """Tworzy lewą kolumnę z listą monet i przyciskami do zarządzania."""
        panel = QFrame(minimumWidth=300, maximumWidth=350)
        layout = QVBoxLayout(panel)
        
        layout.addWidget(QLabel("Dostępne Coiny"))
        self.coin_filter = QLineEdit(placeholderText="🔍 Filtruj...")
        layout.addWidget(self.coin_filter)
        
        self.coin_tree = QTreeWidget()
        self.coin_tree.setHeaderLabels(["Coin/Grupa", "Giełda"])
        self.coin_tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.coin_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        layout.addWidget(self.coin_tree)

        # Przyciski do zarządzania grupami
        group_btns_layout = QHBoxLayout()
        self.add_group_btn = QPushButton("➕ Dodaj Grupę")
        self.remove_group_btn = QPushButton("➖ Usuń Grupę")
        group_btns_layout.addWidget(self.add_group_btn)
        group_btns_layout.addWidget(self.remove_group_btn)
        layout.addLayout(group_btns_layout)

        # Przyciski do zarządzania monetami
        coin_btns_layout = QHBoxLayout()
        self.add_coin_btn = QPushButton("➕ Dodaj Coin")
        self.remove_coin_btn = QPushButton("➖ Usuń Coin")
        coin_btns_layout.addWidget(self.add_coin_btn)
        coin_btns_layout.addWidget(self.remove_coin_btn)
        layout.addLayout(coin_btns_layout)
        
        return panel
    
    def _connect_signals(self):
        """Łączy sygnały z widgetów ze slotami."""
        # Sygnały z lewej kolumny
        self.coin_filter.textChanged.connect(self.filter_coin_tree)
        self.coin_tree.itemClicked.connect(self._on_coin_selected_for_analysis)
        self.coin_tree.customContextMenuRequested.connect(self._show_coin_tree_context_menu)
        self.save_analysis_btn.clicked.connect(self._save_analysis_snapshot)
        
        self.add_group_btn.clicked.connect(self._add_group)
        self.remove_group_btn.clicked.connect(self._remove_group)
        self.add_coin_btn.clicked.connect(self._add_coin_to_group)
        self.remove_coin_btn.clicked.connect(self._remove_coin_from_group_selected_item)

        # Sygnały ze środkowej kolumny
        self.timeframe_selector.currentTextChanged.connect(self._on_coin_selected_for_analysis)

        # Sygnały z prawej kolumny (panel kontrolny wykresu)
        self.plot_items_tree.itemChanged.connect(self._on_plot_item_toggle)
        self.reset_view_btn.clicked.connect(self._reset_chart_view)

    def select_and_analyze_coin(self, symbol_to_find: str):
        """Wyszukuje symbol na drzewie, zaznacza go i uruchamia analizę."""
        # .replace() jest potrzebny, bo nazwy na drzewku nie zawierają '/USDT'
        clean_symbol = symbol_to_find.replace('/USDT', '')
        tree_items = self.coin_tree.findItems(clean_symbol, Qt.MatchFlag.MatchContains | Qt.MatchFlag.MatchRecursive, 0)

        if tree_items:
            target_item = tree_items[0]
            self.coin_tree.setCurrentItem(target_item)
            # Wywołujemy istniejący slot, który zajmie się resztą (emisją sygnału itp.)
            self._on_coin_selected_for_analysis(target_item)
            logger.info(f"Przełączono na analizę {symbol_to_find} z poziomu dashboardu.")
            return True
        else:
            logger.warning(f"Nie znaleziono coina '{symbol_to_find}' na drzewie analizy.")
            return False
        
    def _save_analysis_snapshot(self):
        if self.last_analysis_data is None or self.last_ohlcv_df is None:
            QMessageBox.warning(self, "Brak Danych", "Nie ma analizy do zapisania.")
            return

        try:
            
            data_to_save = self.last_analysis_data.copy()

            if self.last_selected_coin_data:
                data_to_save['symbol'] = self.last_selected_coin_data.get('symbol')
                data_to_save['exchange'] = self.last_selected_coin_data.get('exchange')
                data_to_save['interval'] = self.timeframe_selector.currentText()

            # Konwertujemy WZBOGACONE dane do formatu JSON
            analysis_json = json.dumps(data_to_save)
            ohlcv_json = self.last_ohlcv_df.to_json(orient='split')
            new_id = self.db_manager.save_analysis_snapshot(analysis_json, ohlcv_json)

            if new_id:
                QMessageBox.information(self, "Sukces", f"Analiza została zapisana z ID: {new_id}")
                # Tutaj w przyszłości dodamy sygnał do przełączenia na nową zakładkę
            else:
                QMessageBox.critical(self, "Błąd", "Nie udało się zapisać analizy do bazy danych.")

        except Exception as e:
            logger.error(f"Błąd podczas przygotowywania danych do zapisu: {e}")
            QMessageBox.critical(self, "Błąd", f"Wystąpił błąd: {e}")

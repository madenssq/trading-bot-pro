import asyncio
import logging
import json
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QTreeWidget, QPushButton,
    QFrame, QComboBox, QHeaderView, QMessageBox, QInputDialog, QTreeWidgetItem, QTextBrowser,
    QSplitter, QTableWidget, QMenu 
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint

from app_config import RAMY_CZASOWE
from .analysis_tab_helpers import populate_indicator_summary_table, generate_html_from_analysis
from .chart_widget import UniversalChartWidget
from core.database_manager import DatabaseManager # Potrzebny do konstruktora

logger = logging.getLogger(__name__)

class AnalysisTab(QWidget):
    analysis_requested = pyqtSignal(dict)
    group_action_requested = pyqtSignal(str, dict)
    coin_action_requested = pyqtSignal(str, dict)
    save_snapshot_requested = pyqtSignal(dict, object)

    def __init__(self, settings_manager, analyzer, db_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.analyzer = analyzer
        self.db_manager = db_manager # <-- Poprawiony konstruktor
        
        self.last_selected_coin_data = None
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
        self.chart_widget = UniversalChartWidget(analyzer=self.analyzer, settings_manager=self.settings_manager, db_manager=self.db_manager)

        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(center_panel)
        main_splitter.addWidget(self.chart_widget)
        
        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 2)
        main_splitter.setStretchFactor(2, 6)
        layout.addWidget(main_splitter)

    def _create_left_panel(self):
        panel = QFrame(minimumWidth=300, maximumWidth=350)
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("Dostępne Coiny"))
        self.coin_filter = QLineEdit(placeholderText="🔍 Filtruj...")
        layout.addWidget(self.coin_filter)
        self.coin_tree = QTreeWidget(); self.coin_tree.setHeaderLabels(["Coin/Grupa", "Giełda"]); self.coin_tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); self.coin_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        layout.addWidget(self.coin_tree)
        group_btns_layout = QHBoxLayout(); self.add_group_btn = QPushButton("➕ Dodaj Grupę"); self.remove_group_btn = QPushButton("➖ Usuń Grupę"); group_btns_layout.addWidget(self.add_group_btn); group_btns_layout.addWidget(self.remove_group_btn); layout.addLayout(group_btns_layout)
        coin_btns_layout = QHBoxLayout(); self.add_coin_btn = QPushButton("➕ Dodaj Coin"); self.remove_coin_btn = QPushButton("➖ Usuń Coin"); coin_btns_layout.addWidget(self.add_coin_btn); coin_btns_layout.addWidget(self.remove_coin_btn); layout.addLayout(coin_btns_layout)
        return panel

    def _create_center_panel(self):
        panel = QFrame()
        layout = QVBoxLayout(panel)
        controls_layout = QHBoxLayout(); controls_layout.addWidget(QLabel("Interwał Analizy:")); self.timeframe_selector = QComboBox(); self.timeframe_selector.addItems(RAMY_CZASOWE); controls_layout.addWidget(self.timeframe_selector); controls_layout.addStretch(); layout.addLayout(controls_layout)
        self.analysis_output = QTextBrowser(readOnly=True); self.indicator_summary_table = QTableWidget()
        layout.addWidget(QLabel("Wyniki Analizy AI")); layout.addWidget(self.analysis_output, 2)
        layout.addWidget(QLabel("Kluczowe Wnioski Techniczne")); layout.addWidget(self.indicator_summary_table, 1)
        self.save_analysis_btn = QPushButton("📌 Zapisz do Obserwowanych"); self.save_analysis_btn.setEnabled(False)
        layout.addWidget(self.save_analysis_btn)
        return panel

    def _connect_signals(self):
        """Metoda UPROSZCZONA - łączy tylko sygnały należące do tej zakładki."""
        self.coin_filter.textChanged.connect(self.filter_coin_tree)
        self.coin_tree.itemClicked.connect(self._on_coin_selected_for_analysis)
        self.coin_tree.customContextMenuRequested.connect(self._show_coin_tree_context_menu)
        self.add_group_btn.clicked.connect(self._add_group)
        self.remove_group_btn.clicked.connect(self._remove_group)
        self.add_coin_btn.clicked.connect(self._add_coin_to_group)
        self.remove_coin_btn.clicked.connect(self._remove_coin_from_group_selected_item)
        self.timeframe_selector.currentTextChanged.connect(self._on_coin_selected_for_analysis)
        self.save_analysis_btn.clicked.connect(self._save_analysis_snapshot)

    def update_view(self, ohlcv_df, all_timeframe_data, parsed_data, fvgs, fib_data=None):
        self.last_analysis_data = parsed_data
        self.last_ohlcv_df = ohlcv_df
        self.save_analysis_btn.setEnabled(True)
        self.analysis_output.setHtml(generate_html_from_analysis(parsed_data))
        populate_indicator_summary_table(all_timeframe_data, self.indicator_summary_table)
        overlay_data = {"parsed_data": parsed_data, "fvgs": fvgs, "fib_data": fib_data}
        asyncio.create_task(self.chart_widget.display_analysis(
            symbol=self.last_selected_coin_data['symbol'],
            exchange=self.last_selected_coin_data['exchange'],
            interval=self.timeframe_selector.currentText(),
            overlay_data=overlay_data
        ))

    def set_controls_enabled(self, enabled: bool):
        self.coin_tree.setEnabled(enabled)
        self.timeframe_selector.setEnabled(enabled)
        
    def _save_analysis_snapshot(self):
        if self.last_analysis_data is None or self.last_ohlcv_df is None:
            QMessageBox.warning(self, "Brak Danych", "Nie ma analizy do zapisania.")
            return
        self.save_snapshot_requested.emit(self.last_analysis_data, self.last_ohlcv_df)

    def _load_initial_settings(self):
        interval = self.settings_manager.get('analysis.default_interval', '1h')
        self.timeframe_selector.setCurrentText(interval)

    def populate_coin_tree(self, groups: dict):
        self.coin_tree.clear()
        for name in sorted(groups.keys()):
            parent = QTreeWidgetItem(self.coin_tree, [name])
            parent.setData(0, Qt.ItemDataRole.UserRole, {"type": "group", "name": name})
            for coin in sorted(groups.get(name, []), key=lambda c: c['symbol']):
                child = QTreeWidgetItem(parent, [coin['symbol'].replace('/USDT', ''), coin['exchange']])
                child.setData(0, Qt.ItemDataRole.UserRole, {**coin, "type": "coin"})
            parent.setExpanded(True)

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

    def _on_coin_selected_for_analysis(self):
        selected_item = self.coin_tree.currentItem()
        if not (selected_item and selected_item.data(0, Qt.ItemDataRole.UserRole) and selected_item.data(0, Qt.ItemDataRole.UserRole).get("type") == "coin"):
            return
        self.last_selected_coin_data = selected_item.data(0, Qt.ItemDataRole.UserRole)
        self.analysis_requested.emit({
            "symbol": self.last_selected_coin_data["symbol"], "exchange": self.last_selected_coin_data["exchange"],
            "interval": self.timeframe_selector.currentText(), "trade_mode": "manual"
        })

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

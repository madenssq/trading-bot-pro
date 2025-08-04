import asyncio
import logging
import json
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QTextEdit, QPushButton, QComboBox, QSplitter, QMessageBox
)
from PyQt6.QtCore import Qt

from core.database_manager import DatabaseManager
from core.analyzer import TechnicalAnalyzer
from core.settings_manager import SettingsManager
from .chart_widget import UniversalChartWidget

logger = logging.getLogger(__name__)

class WatchedTab(QWidget):
    def __init__(self, db_manager, settings_manager, analyzer, parent=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.settings_manager = settings_manager
        self.analyzer = analyzer
        self.current_snapshot_data = None
        self._setup_ui()
        self._connect_signals()
        self.populate_list()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = QWidget(maximumWidth=400)
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(QLabel("<b>Zapisane Analizy:</b>"))
        self.snapshots_list = QListWidget()
        left_layout.addWidget(self.snapshots_list)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        self.chart_widget = UniversalChartWidget(self.analyzer, self.settings_manager, self.db_manager)
        
        notes_panel = QWidget(maximumHeight=250)
        notes_layout = QHBoxLayout(notes_panel)
        notes_box = QVBoxLayout()
        notes_box.addWidget(QLabel("<b>Notatki Użytkownika:</b>"))
        self.notes_editor = QTextEdit(placeholderText="Twoje notatki do tej analizy...")
        notes_box.addWidget(self.notes_editor)
        
        status_box = QVBoxLayout()
        status_box.addWidget(QLabel("<b>Status Analizy:</b>"))
        self.status_combo = QComboBox()
        self.status_combo.addItems(["Obserwowane", "Zagrano", "Anulowane", "Zakończona (Zysk)", "Zakończona (Strata)"])
        status_box.addWidget(self.status_combo)
        self.save_notes_btn = QPushButton("💾 Zapisz Zmiany")
        status_box.addWidget(self.save_notes_btn)
        status_box.addStretch()

        notes_layout.addLayout(notes_box, 3)
        notes_layout.addLayout(status_box, 1)

        right_layout.addWidget(self.chart_widget)
        right_layout.addWidget(notes_panel)

        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(right_panel)
        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 8)
        layout.addWidget(main_splitter)

    def _connect_signals(self):
        self.snapshots_list.itemClicked.connect(self._on_snapshot_selected)
        self.save_notes_btn.clicked.connect(self._save_changes)

    def populate_list(self):
        self.snapshots_list.clear()
        saved_analyses = self.db_manager.get_all_saved_analyses()
        for analysis_row in saved_analyses:
            try:
                analysis_data = json.loads(analysis_row['analysis_data_json'])
                symbol = analysis_data.get('symbol', 'B/D')
                interval = analysis_data.get('interval', 'B/D')
                item_text = f"📌 {symbol} ({interval}) - {analysis_row['status']}"
            except (json.JSONDecodeError, KeyError):
                item_text = f"Błędny wpis ID: {analysis_row['id']}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, analysis_row)
            self.snapshots_list.addItem(item)
    
    def _on_snapshot_selected(self, item: QListWidgetItem):
        self.current_snapshot_data = item.data(Qt.ItemDataRole.UserRole)
        if not self.current_snapshot_data: return

        try:
            parsed_data = json.loads(self.current_snapshot_data['analysis_data_json'])
            self.notes_editor.setText(self.current_snapshot_data.get('user_notes', ''))
            self.status_combo.setCurrentText(self.current_snapshot_data.get('status', 'Obserwowane'))

            overlay_data = {
                "parsed_data": parsed_data,
                "analysis_id": self.current_snapshot_data['id']
            }

            asyncio.create_task(self.chart_widget.display_analysis(
                symbol=parsed_data.get('symbol'),
                exchange=parsed_data.get('exchange'),
                interval=parsed_data.get('interval'),
                overlay_data=overlay_data
            ))
        except Exception as e:
            logger.error(f"Błąd podczas wyświetlania snapshotu: {e}", exc_info=True)

    def _save_changes(self):
        if not self.current_snapshot_data:
            QMessageBox.warning(self, "Brak danych", "Najpierw wybierz analizę z listy.")
            return

        analysis_id = self.current_snapshot_data['id']
        new_notes = self.notes_editor.toPlainText()
        new_status = self.status_combo.currentText()

        self.db_manager.update_snapshot_details(analysis_id, new_notes, new_status)
        self.populate_list()
        QMessageBox.information(self, "Sukces", "Zmiany zostały zapisane.")
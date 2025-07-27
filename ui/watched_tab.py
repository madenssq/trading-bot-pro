import logging
import json
import asyncio
from io import StringIO
import pandas as pd
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QLabel, QListWidget, 
    QListWidgetItem, QTextBrowser, QTextEdit, QPushButton, QComboBox, QMessageBox
)
from PyQt6.QtCore import Qt
from .analysis_tab_helpers import draw_chart_with_features, generate_html_from_analysis
from core.ssnedam import AlertData

logger = logging.getLogger(__name__)

class WatchedTab(QWidget):
    def __init__(self, db_manager, settings_manager, analyzer, parent=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.settings_manager = settings_manager
        self.analyzer = analyzer
        self.current_snapshot_data = None

        self.drawing_mode = None
        self.first_click_pos = None
        self.drawn_annotations = []
        self._setup_ui()
        self._connect_signals()
        self.populate_list()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- 1. LEWY PANEL: Lista analiz ---
        self.snapshots_list = QListWidget()

        # --- 2. ŚRODKOWY PANEL: Wykres i Analiza AI ---
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        chart_controls_layout = QHBoxLayout()
        chart_controls_layout.addWidget(QLabel("Interwał podglądu:"))
        self.interval_combo = QComboBox()
        self.interval_combo.addItems(['1h', '4h', '1d'])
        chart_controls_layout.addWidget(self.interval_combo)
        chart_controls_layout.addStretch()
        center_layout.addLayout(chart_controls_layout)
        center_splitter = QSplitter(Qt.Orientation.Vertical)
        
        self.chart_widget = pg.GraphicsLayoutWidget()
        self.analysis_browser = QTextBrowser() # <-- TWORZYMY BRAKUJĄCY WIDGET
        
        center_splitter.addWidget(self.chart_widget)
        center_splitter.addWidget(self.analysis_browser)
        center_splitter.setStretchFactor(0, 3) # Wykres jest 3x większy
        center_splitter.setStretchFactor(1, 1)
        center_layout.addWidget(center_splitter)

        # --- 3. PRAWY PANEL: Narzędzia i Notatki ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        right_layout.addWidget(QLabel("<b>Narzędzia:</b>"))
        tools_layout = QHBoxLayout()
        self.draw_hline_btn = QPushButton("─ Linia")
        self.draw_rect_btn = QPushButton("▭ Prostokąt")
        self.draw_trendline_btn = QPushButton("╱ Trendu")
        self.delete_last_btn = QPushButton("↩️ Usuń")
        tools_layout.addWidget(self.draw_hline_btn)
        tools_layout.addWidget(self.draw_rect_btn)
        tools_layout.addWidget(self.draw_trendline_btn)
        tools_layout.addWidget(self.delete_last_btn)
        right_layout.addLayout(tools_layout)

        right_layout.addWidget(QLabel("<b>Notatki Użytkownika:</b>"))
        self.notes_editor = QTextEdit(placeholderText="Twoje notatki do tej analizy...")
        right_layout.addWidget(self.notes_editor)
        
        right_layout.addWidget(QLabel("<b>Status Analizy:</b>"))
        self.status_combo = QComboBox()
        self.status_combo.addItems(["Obserwowane", "Zagrano", "Anulowane", "Zakończona (Zysk)", "Zakończona (Strata)"])
        right_layout.addWidget(self.status_combo)

        self.save_notes_btn = QPushButton("💾 Zapisz Zmiany")
        right_layout.addWidget(self.save_notes_btn)
        right_layout.addStretch()

        # --- SKŁADANIE CAŁOŚCI ---
        main_splitter.addWidget(self.snapshots_list)
        main_splitter.addWidget(center_panel)
        main_splitter.addWidget(right_panel)

        main_splitter.setStretchFactor(0, 2) 
        main_splitter.setStretchFactor(1, 6)
        main_splitter.setStretchFactor(2, 2)
        
        layout.addWidget(main_splitter)

    def _connect_signals(self):
        self.snapshots_list.itemClicked.connect(self._on_snapshot_selected)
        self.save_notes_btn.clicked.connect(self._save_changes)
        self.draw_hline_btn.clicked.connect(self._activate_hline_draw_mode)
        self.draw_rect_btn.clicked.connect(self._activate_rect_draw_mode)
        self.draw_trendline_btn.clicked.connect(self._activate_trendline_draw_mode)
        self.delete_last_btn.clicked.connect(self._delete_last_annotation)
        self.interval_combo.currentTextChanged.connect(self._on_interval_changed)
        self.chart_widget.scene().sigMouseClicked.connect(self._on_chart_clicked)

    def populate_list(self):
        self.snapshots_list.clear()
        saved_analyses = self.db_manager.get_all_saved_analyses()

        for analysis_row in saved_analyses:
            # Deserializujemy JSON, aby dostać się do symbolu i interwału
            try:
                analysis_data = json.loads(analysis_row['analysis_data_json'])
                symbol = analysis_data.get('setup', {}).get('symbol') or analysis_data.get('symbol', 'B/D')
                interval = analysis_data.get('setup', {}).get('interval') or analysis_data.get('interval', 'B/D')
                item_text = f"📌 {symbol} ({interval}) - {analysis_row['status']}"
            except (json.JSONDecodeError, KeyError):
                item_text = f"Błędny wpis ID: {analysis_row['id']}"

            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, analysis_row)
            self.snapshots_list.addItem(item)
    
    def _on_snapshot_selected(self, item: QListWidgetItem):
        """
        Wyświetla szczegóły wybranej 'migawki' analizy, pobierając świeże dane
        dla aktualnie wybranego interwału.
        """
        self.current_snapshot_data = item.data(Qt.ItemDataRole.UserRole)
        if not self.current_snapshot_data:
            return

        try:
            # Krok 1: Wczytaj zapisane dane, aby uzyskać kontekst (symbol, dane AI itp.)
            parsed_data = json.loads(self.current_snapshot_data['analysis_data_json'])
            save_timestamp = self.current_snapshot_data['save_timestamp']
            self.interval_combo.setCurrentText('1h')
            
            # Krok 2: Zbierz parametry do pobrania nowych danych
            symbol = parsed_data.get('symbol', 'B/D')
            exchange = parsed_data.get('exchange', 'BINANCE') # Używamy zapisanej giełdy lub domyślnej
            selected_interval = self.interval_combo.currentText()
            
            # Krok 3: Zleć pobranie danych i przerysowanie wykresu zadaniu w tle
            asyncio.create_task(self._redraw_chart_with_new_data(
                symbol, exchange, selected_interval, parsed_data, save_timestamp
            ))

        except Exception as e:
            logger.error(f"Błąd podczas przygotowywania do wyświetlenia snapshotu: {e}", exc_info=True)

    
    def _on_interval_changed(self):
        """Reaguje na zmianę interwału i odświeża wykres."""
        if self.current_snapshot_data:
            # Po prostu ponownie wywołujemy metodę wyświetlającą,
            # która odczyta nową wartość z ComboBoxa.
            self._on_snapshot_selected(self.snapshots_list.currentItem())
    
    def _save_changes(self):
        """Zapisuje zmiany w notatkach i statusie do bazy danych."""
        if not self.current_snapshot_data:
            QMessageBox.warning(self, "Brak danych", "Najpierw wybierz analizę z listy.")
            return

        analysis_id = self.current_snapshot_data['id']
        new_notes = self.notes_editor.toPlainText()
        new_status = self.status_combo.currentText()

        self.db_manager.update_snapshot_details(analysis_id, new_notes, new_status)

        # Szybkie odświeżenie listy, aby zobaczyć nowy status
        self.populate_list()
        QMessageBox.information(self, "Sukces", "Zmiany zostały zapisane.")

    def _activate_draw_mode(self, mode: str):
        """Uniwersalna funkcja do aktywacji trybu rysowania."""
        self.drawing_mode = mode
        self.first_click_pos = None # Resetujemy pozycję pierwszego kliknięcia
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _activate_hline_draw_mode(self):
        self._activate_draw_mode('hline')

    def _activate_rect_draw_mode(self):
        self._activate_draw_mode('rect')

    def _activate_trendline_draw_mode(self):
        self._activate_draw_mode('trendline')

    def _on_chart_clicked(self, event):
        """Obsługuje kliknięcie na wykresie, rysując obiekty."""
        if not self.drawing_mode:
            return

        plot_item = self.chart_widget.getItem(0, 0)
        if not plot_item:
            self._reset_draw_mode()
            return

        mouse_point = plot_item.vb.mapSceneToView(event.scenePos())

        if self.drawing_mode == 'hline':
            price_level = mouse_point.y()
            line = pg.InfiniteLine(pos=price_level, angle=0, movable=True, pen=pg.mkPen('yellow'))
            plot_item.addItem(line)

            if self.current_snapshot_data:
                analysis_id = self.current_snapshot_data['id']
                properties = {'pos': price_level}
                new_id = self.db_manager.add_annotation(analysis_id, 'hline', json.dumps(properties))
                if new_id:
                    self.drawn_annotations.append({'id': new_id, 'item': line})

            self._reset_draw_mode()

        elif self.drawing_mode == 'rect':
            if not self.first_click_pos:
                # To jest pierwsze kliknięcie - zapisujemy pozycję
                self.first_click_pos = mouse_point
            else:
                # To jest drugie kliknięcie - rysujemy prostokąt
                p1 = self.first_click_pos
                p2 = mouse_point

                rect = pg.RectROI(
                    pos=(min(p1.x(), p2.x()), min(p1.y(), p2.y())),
                    size=(abs(p1.x() - p2.x()), abs(p1.y() - p2.y())),
                    pen=pg.mkPen('cyan'),
                    movable=True,
                    resizable=True
                )
                plot_item.addItem(rect)

                if self.current_snapshot_data:
                    analysis_id = self.current_snapshot_data['id']
                    properties = {
                        'pos': (min(p1.x(), p2.x()), min(p1.y(), p2.y())),
                        'size': (abs(p1.x() - p2.x()), abs(p1.y() - p2.y()))
                    }
                    new_id = self.db_manager.add_annotation(analysis_id, 'rect', json.dumps(properties))
                    if new_id:
                        self.drawn_annotations.append({'id': new_id, 'item': rect})
                
        elif self.drawing_mode == 'trendline':
            if not self.first_click_pos:
                # Pierwsze kliknięcie
                self.first_click_pos = mouse_point
            else:
                # Drugie kliknięcie - rysujemy linię
                p1 = self.first_click_pos
                p2 = mouse_point

                # Używamy LineSegmentROI, który jest interaktywny
                line = pg.LineSegmentROI(positions=[(p1.x(), p1.y()), (p2.x(), p2.y())], pen=pg.mkPen('magenta'))
                plot_item.addItem(line)

                if self.current_snapshot_data:
                    analysis_id = self.current_snapshot_data['id']
                    properties = {'pos1': (p1.x(), p1.y()), 'pos2': (p2.x(), p2.y())}
                    new_id = self.db_manager.add_annotation(analysis_id, 'trendline', json.dumps(properties))
                    if new_id:
                        self.drawn_annotations.append({'id': new_id, 'item': line})


                self._reset_draw_mode()

    def _reset_draw_mode(self):
        """Resetuje tryb rysowania i kursor."""
        self.drawing_mode = None
        self.first_click_pos = None
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def _delete_last_annotation(self):
        """Usuwa ostatnio dodany element z wykresu i bazy danych."""
        if not self.drawn_annotations:
            return

        plot_item = self.chart_widget.getItem(0, 0)
        if not plot_item: return

        # Usuwamy ostatni element z naszej listy
        last_annotation = self.drawn_annotations.pop()
        annotation_id = last_annotation['id']
        item_to_remove = last_annotation['item']

        # Usuwamy z bazy danych
        self.db_manager.delete_annotation(annotation_id)
        # Usuwamy z wykresu
        plot_item.removeItem(item_to_remove)

    async def _redraw_chart_with_new_data(self, symbol, exchange, interval, parsed_data, save_timestamp):
    
        
        # Krok 1: Pobierz świeże dane OHLCV dla wybranego interwału
        # POPRAWKA: Używamy nowych serwisów
        exchange_instance = await self.analyzer.exchange_service.get_exchange_instance(exchange)
        if not exchange_instance: 
            self.chart_widget.clear()
            self.chart_widget.addPlot().setTitle(f"Błąd: Nie można utworzyć instancji giełdy {exchange}")
            return

        new_ohlcv_df = await self.analyzer.exchange_service.fetch_ohlcv(exchange_instance, symbol, interval, limit=1000)
        
        if new_ohlcv_df is None or new_ohlcv_df.empty:
            self.chart_widget.clear()
            self.chart_widget.addPlot().setTitle(f"Brak danych dla {symbol} ({interval})")
            return

        df_with_indicators = self.analyzer.indicator_service.calculate_all(new_ohlcv_df.copy())
        
        # Krok 2: Wypełnij pola tekstowe analizą i notatkami
        self.analysis_browser.setHtml(generate_html_from_analysis(parsed_data))
        self.notes_editor.setText(self.current_snapshot_data.get('user_notes', ''))
        self.status_combo.setCurrentText(self.current_snapshot_data.get('status', 'Obserwowane'))

        # Krok 3: Przygotuj dane i narysuj główny wykres (reszta bez zmian)
        from core.ssnedam import AlertData
        dummy_alert = AlertData(
            symbol=symbol, interval=interval, setup_data={}, context="", exchange="",
            raw_ai_response="", parsed_data={}, alert_timestamp=save_timestamp
        )

        plot_item = draw_chart_with_features(
            self,
            plot_widget=self.chart_widget,
            df=df_with_indicators,
            setup=parsed_data.get('setup'),
            sr_levels=parsed_data.get('support_resistance'),
            fib_data=parsed_data.get('fib_data'),
            fvgs=parsed_data.get('fvgs'),
            title=f"Podgląd dla {symbol} ({interval})",
            alert_data=dummy_alert
        )
        
        # Krok 4: Wczytaj i dorysuj zapisane adnotacje (rysunki)
        if plot_item and self.current_snapshot_data:
            self.drawn_annotations = [] 
            annotations = self.db_manager.get_annotations_for_analysis(self.current_snapshot_data['id'])
            
            for ann in annotations:
                try:
                    props = json.loads(ann['properties_json'])
                    item = None
                    
                    if ann['item_type'] == 'hline':
                        item = pg.InfiniteLine(pos=props.get('pos'), angle=0, movable=True, pen=pg.mkPen(props.get('pen', 'yellow')))
                    elif ann['item_type'] == 'rect':
                        item = pg.RectROI(pos=props.get('pos'), size=props.get('size'), pen=pg.mkPen('cyan'), movable=True, resizable=True)
                    elif ann['item_type'] == 'trendline':
                        item = pg.LineSegmentROI(positions=[props.get('pos1'), props.get('pos2')], pen=pg.mkPen('magenta'))
                    
                    if item:
                        plot_item.addItem(item)
                        self.drawn_annotations.append({'id': ann['id'], 'item': item})
                except Exception as e:
                    logger.error(f"Błąd podczas wczytywania adnotacji ID {ann.get('id', 'N/A')}: {e}")

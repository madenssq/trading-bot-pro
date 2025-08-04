import asyncio
import logging
import pandas as pd
import pyqtgraph as pg
import numpy as np
import json
from typing import Dict, Any, Optional

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel, QRadioButton,
    QButtonGroup, QCheckBox, QPushButton, QTreeWidget, QTreeWidgetItem, QGroupBox,
    QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor

from core.analyzer import TechnicalAnalyzer
from core.settings_manager import SettingsManager
from core.database_manager import DatabaseManager
from .history_dialog import CandlestickItem, DateAxis
from .styles import THEMES

logger = logging.getLogger(__name__)

class UniversalChartWidget(QWidget):
    status_message_changed = pyqtSignal(str)

    def __init__(self, analyzer: TechnicalAnalyzer, settings_manager: SettingsManager, db_manager: DatabaseManager, parent: QWidget | None = None):
        super().__init__(parent)
        self.analyzer = analyzer
        self.settings_manager = settings_manager
        self.db_manager = db_manager

        self.data_cache: Dict[str, pd.DataFrame] = {}
        self.plotted_items: Dict[str, list] = {}
        self.current_symbol: Optional[str] = None
        self.current_exchange: Optional[str] = None
        self.current_overlay_data: Dict[str, Any] = {}
        self.price_plot_area = None
        
        self.drawing_mode: Optional[str] = None
        self.first_click_pos = None
        self.drawn_annotations: list = []

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        self.charts_widget = pg.GraphicsLayoutWidget()
        theme_setting = self.settings_manager.get('app.theme', 'ciemny')
        theme_key = 'dark' if theme_setting == 'ciemny' else 'jasny'
        self.charts_widget.setBackground(THEMES[theme_key]['CHART_BG'])
        main_layout.addWidget(self.charts_widget, 5)

        self.price_plot_area = self.charts_widget.addPlot(row=0, col=0, axisItems={'bottom': DateAxis(orientation='bottom')})
        
        self.control_panel = self._create_control_panel()
        main_layout.addWidget(self.control_panel, 1)

    def _create_control_panel(self) -> QWidget:
        panel = QFrame(maximumWidth=200)
        layout = QVBoxLayout(panel)

        tf_box = QGroupBox("Interwał"); tf_layout = QHBoxLayout(tf_box)
        self.timeframe_group = QButtonGroup(self)
        for tf in ['15m', '1h', '4h', '1d']:
            rb = QRadioButton(tf); self.timeframe_group.addButton(rb); tf_layout.addWidget(rb)
        layout.addWidget(tf_box)
        
        view_box = QGroupBox("Widok"); view_layout = QVBoxLayout(view_box)
        self.log_scale_check = QCheckBox("Skala Logarytmiczna"); self.reset_view_btn = QPushButton("Resetuj Widok")
        view_layout.addWidget(self.log_scale_check); view_layout.addWidget(self.reset_view_btn)
        layout.addWidget(view_box)

        tools_box = QGroupBox("Narzędzia"); tools_layout = QVBoxLayout(tools_box)
        self.draw_hline_btn = QPushButton("─ Linia Pozioma"); self.draw_rect_btn = QPushButton("▭ Prostokąt")
        self.delete_last_btn = QPushButton("↩️ Usuń Ostatni")
        tools_layout.addWidget(self.draw_hline_btn); tools_layout.addWidget(self.draw_rect_btn); tools_layout.addWidget(self.delete_last_btn)
        layout.addWidget(tools_box)

        visibility_box = QGroupBox("Widoczność Elementów"); visibility_layout = QVBoxLayout(visibility_box)
        self.plot_items_tree = QTreeWidget(headerHidden=True)
        # Usunęliśmy wskaźniki dolne (MACD, RSI) z listy
        items = {"Wskaźniki": ["EMA", "Wstęgi Bollingera"], "Analiza": ["Poziomy S/R", "Strefy FVG", "Setup (SL/TP)", "Zdarzenia"]}
        for group, names in items.items():
            parent = QTreeWidgetItem(self.plot_items_tree, [group]); parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable); parent.setCheckState(0, Qt.CheckState.Checked)
            for name in names:
                child = QTreeWidgetItem(parent, [name]); child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable); child.setCheckState(0, Qt.CheckState.Checked)
        visibility_layout.addWidget(self.plot_items_tree); layout.addWidget(visibility_box)
        layout.addStretch()
        return panel

    def _connect_signals(self):
        self.timeframe_group.buttonClicked.connect(self._on_timeframe_changed)
        self.log_scale_check.toggled.connect(self._on_log_scale_toggled)
        self.reset_view_btn.clicked.connect(self._reset_view)
        self.plot_items_tree.itemChanged.connect(self._on_plot_item_toggle)
        self.charts_widget.scene().sigMouseClicked.connect(self._on_chart_clicked)
        self.draw_hline_btn.clicked.connect(lambda: self._activate_draw_mode('hline'))
        self.draw_rect_btn.clicked.connect(lambda: self._activate_draw_mode('rect'))
        self.delete_last_btn.clicked.connect(self._delete_last_annotation)

    async def display_analysis(self, symbol: str, exchange: str, interval: str, overlay_data: Dict[str, Any]):
        self.current_symbol = symbol; self.current_exchange = exchange; self.current_overlay_data = overlay_data
        self.data_cache.clear()
        for button in self.timeframe_group.buttons():
            if button.text() == interval: button.setChecked(True); break
        await self._load_and_draw_data(interval)

    def export_to_image_bytes(self) -> Optional[bytes]:
        if not self.price_plot_area: return None
        try:
            exporter = pg.exporters.ImageExporter(self.charts_widget.scene())
            image = exporter.export(toBytes=True)
            buffer = pg.QtCore.QBuffer(); buffer.open(pg.QtCore.QBuffer.OpenModeFlag.ReadWrite)
            image.save(buffer, "PNG"); return bytes(buffer.data())
        except Exception as e:
            logger.error(f"Błąd podczas eksportu wykresu do obrazu: {e}"); return None

    def _on_timeframe_changed(self, button: QRadioButton):
        selected_timeframe = button.text()
        asyncio.create_task(self._load_and_draw_data(selected_timeframe))
        
    def _on_log_scale_toggled(self, checked: bool):
        """Włącza/wyłącza skalę logarytmiczną i odświeża widok."""
        if self.price_plot_area:
            self.price_plot_area.setLogMode(y=checked)
            # Pobierz aktualny DataFrame i odśwież widok z nową skalą
            current_tf = self.timeframe_group.checkedButton().text()
            df = self.data_cache.get(current_tf)
            if df is not None:
                self._set_proportional_view(df)

    def _reset_view(self):
        if self.price_plot_area: self.price_plot_area.enableAutoRange(axis='xy')

    async def _load_and_draw_data(self, timeframe: str):
        if not self.current_symbol or not self.current_exchange: return
        df = self.data_cache.get(timeframe)
        if df is None:
            exchange = await self.analyzer.get_exchange_instance(self.current_exchange)
            if exchange:
                raw_df = await self.analyzer.fetch_ohlcv(exchange, self.current_symbol, timeframe, limit=1000)
                if raw_df is not None and not raw_df.empty:
                    df = self.analyzer.calculate_all_indicators(raw_df.copy())
                    self.data_cache[timeframe] = df
        
        if df is not None: self._draw_chart(df)
        else:
            if self.price_plot_area: self.price_plot_area.clear(); self.price_plot_area.setTitle(f"Brak danych dla {self.current_symbol} ({timeframe})", color='red')

    def _draw_chart(self, df: pd.DataFrame):
        if not self.price_plot_area: return
        self.price_plot_area.clear(); self.plotted_items.clear()
        timeframe_text = self.timeframe_group.checkedButton().text() if self.timeframe_group.checkedButton() else ""
        self.price_plot_area.setTitle(f"{self.current_symbol} ({timeframe_text})")
        
        self._draw_candlesticks(self.price_plot_area, df)
        self._draw_emas(self.price_plot_area, df)
        self._draw_bbands(self.price_plot_area, df)
        
        overlay = self.current_overlay_data
        if sr_data := overlay.get('parsed_data', {}).get('support_resistance'): self._draw_support_resistance(self.price_plot_area, sr_data)
        if fvgs := overlay.get('fvgs'): self._draw_fvgs(self.price_plot_area, fvgs)
        if setup := overlay.get('parsed_data', {}).get('setup'): self._draw_setup_zones(self.price_plot_area, df, setup)
        if events := overlay.get('trade_events'): self._draw_trade_events(self.price_plot_area, events)
        
        self._setup_crosshair(self.price_plot_area, df, self.current_symbol)
        self._set_proportional_view(df)

    def _on_plot_item_toggle(self, item, column):
        """Obsługuje przełączanie widoczności elementów na wykresie z poprawną logiką rodzic-dziecko."""
        name_map = {
            "EMA": "ema", "Wstęgi Bollingera": "bb", "MACD": "macd",
            "Poziomy S/R": "sr_levels", "Strefy FVG": "fvgs",
            "Setup (SL/TP)": "setup", "Zdarzenia": "trade_events"
        }

        # Pobierz klucz dla klikniętego elementu
        key = name_map.get(item.text(0))
        
        # --- PRZYPADEK 1: Kliknięto na DZIECKO (np. "Strefy FVG") ---
        if key:
            is_visible = item.checkState(0) == Qt.CheckState.Checked
            for plot_object in self.plotted_items.get(key, []):
                plot_object.setVisible(is_visible)
        
        # --- PRZYPADEK 2: Kliknięto na RODZICA (np. "Analiza") ---
        elif item.parent() is None:
            # Tymczasowo zablokuj sygnały, aby uniknąć pętli zwrotnej
            self.plot_items_tree.blockSignals(True)
            
            parent_state = item.checkState(0)
            # Zastosuj stan rodzica do wszystkich jego dzieci
            for i in range(item.childCount()):
                child = item.child(i)
                child.setCheckState(0, parent_state)
                
                # Zaktualizuj widoczność na wykresie dla każdego dziecka
                child_key = name_map.get(child.text(0))
                if child_key:
                    is_visible = child.checkState(0) == Qt.CheckState.Checked
                    for plot_object in self.plotted_items.get(child_key, []):
                        plot_object.setVisible(is_visible)
            
            # Włącz sygnały z powrotem
            self.plot_items_tree.blockSignals(False)
   

    def _draw_candlesticks(self, plot_area, df):
        timestamps = df.index.astype(np.int64) // 10**9
        alert_ts = self.current_overlay_data.get('alert_timestamp')
        item = CandlestickItem(list(zip(timestamps, df['Open'], df['High'], df['Low'], df['Close'])), alert_timestamp=alert_ts)
        plot_area.addItem(item); self.plotted_items['candlesticks'] = [item]

    def _draw_emas(self, plot_area, df):
        params = self.settings_manager.get('analysis.indicator_params', {}); items = []
        ts = df.index.astype(np.int64) // 10**9
        f_len, s_len = params.get('ema_fast_length', 50), params.get('ema_slow_length', 200)
        if f'EMA_{f_len}' in df.columns: items.append(plot_area.plot(ts, df[f'EMA_{f_len}'], pen=pg.mkPen('#3498DB', width=2)))
        if f'EMA_{s_len}' in df.columns: items.append(plot_area.plot(ts, df[f'EMA_{s_len}'], pen=pg.mkPen('#F1C40F', width=2)))
        self.plotted_items['ema'] = items

    def _draw_bbands(self, plot_area, df):
        params = self.settings_manager.get('analysis.indicator_params', {}); items = []
        ts = df.index.astype(np.int64) // 10**9
        keys = (f"BBU_{params.get('bbands_length', 20)}_{params.get('bbands_std', 2.0)}", f"BBL_{params.get('bbands_length', 20)}_{params.get('bbands_std', 2.0)}")
        if all(k in df for k in keys):
            up_item = plot_area.plot(ts, df[keys[0]], pen=pg.mkPen('#95A5A6', style=Qt.PenStyle.DashLine))
            low_item = plot_area.plot(ts, df[keys[1]], pen=pg.mkPen('#95A5A6', style=Qt.PenStyle.DashLine))
            fill = pg.FillBetweenItem(up_item, low_item, brush=(91, 99, 120, 50))
            plot_area.addItem(fill); items.extend([up_item, low_item, fill])
        self.plotted_items['bb'] = items
        
    def _draw_support_resistance(self, plot_area, sr_data):
        items = []; pens = {'support': pg.mkPen('#2ECC71', width=2), 'resistance': pg.mkPen('#E74C3C', width=2)}; labels = {'support': 'Wsparcie {value:.4f}', 'resistance': 'Opór {value:.4f}'}
        for sr_type in ['support', 'resistance']:
            for level in sr_data.get(sr_type, []):
                line = pg.InfiniteLine(pos=level, angle=0, pen=pens[sr_type], label=labels[sr_type], labelOpts={'position': 0.85})
                plot_area.addItem(line); items.append(line)
        self.plotted_items['sr_levels'] = items
            
    def _draw_fvgs(self, plot_area, fvgs: list):
        items = []; brushes = {'bullish': QColor(0, 150, 255, 40), 'bearish': QColor(255, 165, 0, 40)}
        for gap in fvgs:
            rect = pg.QtWidgets.QGraphicsRectItem(gap['start_time'], gap['start_price'], gap['width_seconds'], gap['end_price'] - gap['start_price'])
            rect.setBrush(brushes[gap['type']]); rect.setPen(pg.mkPen(None))
            plot_area.addItem(rect); items.append(rect)
        self.plotted_items['fvgs'] = items

    def _draw_setup_zones(self, plot_area, df: pd.DataFrame, setup_data: dict):
        items = []; entry = setup_data.get('entry'); stop_loss = setup_data.get('stop_loss'); tp1 = setup_data.get('take_profit_1'); tps = setup_data.get('take_profit')
        target_tp = (tps[0] if isinstance(tps, list) and tps else tps) if tps else None
        if entry: items.append(pg.InfiniteLine(pos=entry, angle=0, pen=pg.mkPen('cyan', style=Qt.PenStyle.DashLine), label='Wejście {value:.4f}', labelOpts={'position': 0.15}))
        if stop_loss: items.append(pg.InfiniteLine(pos=stop_loss, angle=0, pen=pg.mkPen('red', style=Qt.PenStyle.DashLine), label='Stop Loss {value:.4f}', labelOpts={'position': 0.15}))
        if tp1: items.append(pg.InfiniteLine(pos=tp1, angle=0, pen=pg.mkPen('#00A86B', style=Qt.PenStyle.DotLine, width=2), label='TP1 {value:.4f}', labelOpts={'position': 0.15, 'color': '#00A86B'}))
        if target_tp: items.append(pg.InfiniteLine(pos=target_tp, angle=0, pen=pg.mkPen('green', style=Qt.PenStyle.DashLine, width=2), label='TP2 {value:.4f}', labelOpts={'position': 0.15, 'color': 'green'}))
        for item in items: plot_area.addItem(item)
        alert_ts = self.current_overlay_data.get('alert_timestamp')
        if not alert_ts or df.empty or not entry: self.plotted_items['setup'] = items; return
        start_time = alert_ts; end_time = df.index[-1].timestamp(); width = end_time - start_time
        if width <= 0: self.plotted_items['setup'] = items; return
        if stop_loss:
            sl_rect = pg.QtWidgets.QGraphicsRectItem(start_time, stop_loss, width, entry - stop_loss)
            sl_rect.setBrush(QColor(231, 76, 60, 40)); sl_rect.setPen(pg.mkPen(None))
            plot_area.addItem(sl_rect); items.append(sl_rect)
        if target_tp:
            tp_rect = pg.QtWidgets.QGraphicsRectItem(start_time, entry, width, target_tp - entry)
            tp_rect.setBrush(QColor(46, 204, 113, 40)); tp_rect.setPen(pg.mkPen(None))
            plot_area.addItem(tp_rect); items.append(tp_rect)
        self.plotted_items['setup'] = items

    def _draw_trade_events(self, plot_area, events: list):
        items = []
        for event in events:
            if (event_time := event.get('timestamp')) and event.get('event_type') == 'SL_MOVED_TO_BE':
                line = pg.InfiniteLine(pos=event_time, angle=90, pen=pg.mkPen('#1E90FF', style=Qt.PenStyle.DotLine), label='SL to B/E', labelOpts={'position': 0.95, 'color': '#1E90FF', 'movable': True})
                plot_area.addItem(line); items.append(line)
        self.plotted_items['trade_events'] = items
    
    

    def _on_plot_item_toggle(self, item, column):
        """Obsługuje przełączanie widoczności elementów na wykresie z poprawną logiką rodzic-dziecko."""
        name_map = {
            "EMA": "ema", "Wstęgi Bollingera": "bb", "MACD": "macd",
            "Poziomy S/R": "sr_levels", "Strefy FVG": "fvgs",
            "Setup (SL/TP)": "setup", "Zdarzenia": "trade_events"
        }

        # Pobierz klucz dla klikniętego elementu
        key = name_map.get(item.text(0))
        
        # --- PRZYPADEK 1: Kliknięto na DZIECKO (np. "Strefy FVG") ---
        if key:
            is_visible = item.checkState(0) == Qt.CheckState.Checked
            for plot_object in self.plotted_items.get(key, []):
                plot_object.setVisible(is_visible)
        
        # --- PRZYPADEK 2: Kliknięto na RODZICA (np. "Analiza") ---
        elif item.parent() is None:
            # Tymczasowo zablokuj sygnały, aby uniknąć pętli zwrotnej
            self.plot_items_tree.blockSignals(True)
            
            parent_state = item.checkState(0)
            # Zastosuj stan rodzica do wszystkich jego dzieci
            for i in range(item.childCount()):
                child = item.child(i)
                child.setCheckState(0, parent_state)
                
                # Zaktualizuj widoczność na wykresie dla każdego dziecka
                child_key = name_map.get(child.text(0))
                if child_key:
                    is_visible = child.checkState(0) == Qt.CheckState.Checked
                    for plot_object in self.plotted_items.get(child_key, []):
                        plot_object.setVisible(is_visible)
            
            # Włącz sygnały z powrotem
            self.plot_items_tree.blockSignals(False)

    def _setup_crosshair(self, plot_area, ohlcv_df, symbol):
        if not hasattr(self, 'crosshair_v'):
            self.crosshair_v = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('grey', style=Qt.PenStyle.DashLine))
            self.crosshair_h = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen('grey', style=Qt.PenStyle.DashLine))
        if self.crosshair_v.scene() is not plot_area.scene():
            plot_area.addItem(self.crosshair_v, ignoreBounds=True); plot_area.addItem(self.crosshair_h, ignoreBounds=True)
        self.mouse_move_proxy = pg.SignalProxy(plot_area.scene().sigMouseMoved, rateLimit=60, slot=lambda evt: self._on_mouse_moved(evt, plot_area, ohlcv_df, symbol))

    def _on_mouse_moved(self, event, plot_area, ohlcv_df, symbol):
        if not (plot_area and not ohlcv_df.empty): self.crosshair_v.hide(); self.crosshair_h.hide(); return
        pos = event[0]
        if plot_area.sceneBoundingRect().contains(pos):
            mouse_point = plot_area.vb.mapSceneToView(pos)
            self.crosshair_v.setPos(mouse_point.x()); self.crosshair_h.setPos(mouse_point.y())
            self.crosshair_v.show(); self.crosshair_h.show()
            numeric_index = ohlcv_df.index.astype(np.int64) // 10**9
            closest_idx = np.searchsorted(numeric_index, mouse_point.x())
            if closest_idx >= len(ohlcv_df): closest_idx = len(ohlcv_df) - 1
            candle_data = ohlcv_df.iloc[closest_idx]; date_str = candle_data.name.strftime('%Y-%m-%d %H:%M')
            status_text = (f"{symbol} | {date_str} | O: {candle_data['Open']:.4f} | H: {candle_data['High']:.4f} | " f"L: {candle_data['Low']:.4f} | C: {candle_data['Close']:.4f} | Wol: {candle_data['Volume']:.2f}")
            self.status_message_changed.emit(status_text)
        else: self.crosshair_v.hide(); self.crosshair_h.hide()

    def _activate_draw_mode(self, mode: str):
        if self.current_overlay_data and self.current_overlay_data.get('analysis_id'):
            self.drawing_mode = mode; self.first_click_pos = None; self.setCursor(Qt.CursorShape.CrossCursor)
        else: QMessageBox.warning(self, "Brak analizy", "Najpierw wybierz zapisaną analizę z zakładki 'Obserwowane', aby móc dodawać adnotacje.")

    def _reset_draw_mode(self):
        self.drawing_mode = None; self.first_click_pos = None; self.setCursor(Qt.CursorShape.ArrowCursor)

    def _on_chart_clicked(self, event):
        if not self.drawing_mode or not self.price_plot_area: return
        mouse_point = self.price_plot_area.vb.mapSceneToView(event.scenePos()); analysis_id = self.current_overlay_data.get('analysis_id')
        if self.drawing_mode == 'hline':
            price_level = mouse_point.y(); line = pg.InfiniteLine(pos=price_level, angle=0, movable=True, pen=pg.mkPen('yellow'))
            self.price_plot_area.addItem(line); properties = {'pos': price_level}
            new_id = self.db_manager.add_annotation(analysis_id, 'hline', json.dumps(properties))
            if new_id: self.drawn_annotations.append({'id': new_id, 'item': line})
            self._reset_draw_mode()
        elif self.drawing_mode == 'rect':
            if not self.first_click_pos: self.first_click_pos = mouse_point
            else:
                p1, p2 = self.first_click_pos, mouse_point
                rect = pg.RectROI(pos=(min(p1.x(), p2.x()), min(p1.y(), p2.y())), size=(abs(p1.x() - p2.x()), abs(p1.y() - p2.y())), pen=pg.mkPen('cyan'), movable=True, resizable=True)
                self.price_plot_area.addItem(rect); properties = {'pos': (min(p1.x(), p2.x()), min(p1.y(), p2.y())), 'size': (abs(p1.x() - p2.x()), abs(p1.y() - p2.y()))}
                new_id = self.db_manager.add_annotation(analysis_id, 'rect', json.dumps(properties))
                if new_id: self.drawn_annotations.append({'id': new_id, 'item': rect})
                self._reset_draw_mode()

    def _delete_last_annotation(self):
        if not self.drawn_annotations or not self.price_plot_area: return
        last_annotation = self.drawn_annotations.pop(); annotation_id = last_annotation['id']; item_to_remove = last_annotation['item']
        self.db_manager.delete_annotation(annotation_id); self.price_plot_area.removeItem(item_to_remove)


    def _set_proportional_view(self, df: pd.DataFrame):
        """Inteligentnie ustawia zoom i zakres osi, aby setup był zawsze czytelny."""
        if not self.price_plot_area: return

        setup_data = self.current_overlay_data.get('parsed_data', {}).get('setup')
        
        # Jeśli nie ma danych o setupie, użyj standardowego auto-dopasowania
        if not setup_data:
            self._reset_view()
            return

        # --- Ustawianie osi Y (Cena) ---
        prices = [
            setup_data.get('entry'),
            setup_data.get('stop_loss'),
            setup_data.get('take_profit_1'),
            setup_data.get('take_profit')
        ]
        # Spłaszczamy listę na wypadek, gdyby take_profit był listą
        flat_prices = []
        for p in prices:
            if isinstance(p, list): flat_prices.extend(p)
            else: flat_prices.append(p)
        
        valid_prices = [p for p in flat_prices if p is not None and isinstance(p, (int, float))]
        
        if len(valid_prices) < 2:
            self._reset_view(); return

        min_y, max_y = min(valid_prices), max(valid_prices)
        padding = (max_y - min_y) * 0.15  # 15% marginesu z góry i z dołu
        self.price_plot_area.setYRange(min_y - padding, max_y + padding)

        # --- Ustawianie osi X (Czas) ---
        alert_ts = self.current_overlay_data.get('alert_timestamp')
        if not alert_ts:
            self._reset_view(); return
        
        candles_before = 50 # Ile świec kontekstu pokazać przed alertem
        alert_dt = pd.to_datetime(alert_ts, unit='s')
        
        try:
            idx_pos = df.index.get_indexer([alert_dt], method='nearest')[0]
            start_idx = max(0, idx_pos - candles_before)
            
            start_ts = df.index[start_idx].timestamp()
            end_ts = df.index[-1].timestamp()
            
            # Dodajemy mały margines w przyszłość, aby ostatnia świeca nie była przyklejona do krawędzi
            time_padding = (end_ts - start_ts) * 0.05
            self.price_plot_area.setXRange(start_ts, end_ts + time_padding)
        except IndexError:
            self._reset_view() # Fallback w razie problemów z danymi
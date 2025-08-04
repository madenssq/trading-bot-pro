import pandas as pd
import pyqtgraph as pg
import numpy as np
from typing import Dict, List
from datetime import datetime
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QTableWidget, QGraphicsRectItem

import pyqtgraph.exporters
from PyQt6.QtCore import QBuffer
from PyQt6.QtGui import QImage
from pyqtgraph import LinearRegionItem

from core.ssnedam import AlertData
from .history_dialog import CandlestickItem, DateAxis
from .styles import THEMES

def draw_chart_with_features(context, plot_widget, df: pd.DataFrame, fvgs: list = None, setup: dict = None, sr_levels: dict = None, title: str = "Wykres Analizy", zoom_range: Dict = None, alert_data: 'AlertData' = None, fib_data: dict = None, trade_events: List = None):
    plot_widget.clear()
    theme_setting = context.settings_manager.get('app.theme', 'ciemny')
    theme_key = 'dark' if theme_setting == 'ciemny' else theme_setting
    theme = THEMES[theme_key]

    plot_widget.setBackground(theme['CHART_BG'])
    if df is None or df.empty:
        plot_widget.addPlot().setTitle("Brak danych do wyświetlenia", color='red')
        return None

    plot_area = plot_widget.addPlot(row=0, col=0, axisItems={'bottom': DateAxis(orientation='bottom')})
    plot_area.setTitle(title)
    
    # Rysowanie wszystkich elementów (bez zmian)
    context.plotted_items = {}; params = context.settings_manager.get('analysis.indicator_params', {})
    _draw_candlesticks(context, plot_area, df, alert_data)
    _draw_emas(context, plot_area, df, params)
    _draw_bbands(context, plot_area, df, params)
    if fib_data: _draw_fibonacci_levels(context, plot_area, fib_data)
    if sr_levels: _draw_support_resistance(context, plot_area, sr_levels)
    if fvgs: _draw_fvgs(context, plot_area, fvgs)
    if setup: _draw_setup_zones(context, plot_area, setup)
    if trade_events: _draw_trade_events(context, plot_area, trade_events)
    
    # --- NOWA, ULEPSZONA LOGIKA USTAWIANIA WIDOKU ---
    if zoom_range:
        plot_area.setXRange(zoom_range['x_min'], zoom_range['x_max'])
        plot_area.setYRange(zoom_range['y_min'], zoom_range['y_max'])
    else:
        # 1. Ustaw oś czasu (X) na ostatnie 140 świec
        candles_to_show = 140
        visible_df = df.iloc[-candles_to_show:] if len(df) > candles_to_show else df
        plot_area.setXRange(visible_df.index[0].timestamp(), visible_df.index[-1].timestamp())
        
        # 2. Oblicz zakres cen (Y) na podstawie świec ORAZ setupu
        min_candle_price = visible_df['Low'].min()
        max_candle_price = visible_df['High'].max()
        
        # Zbierz wszystkie ceny z setupu, które nie są None
        setup_prices = []
        if setup:
            setup_prices.extend([
                setup.get('entry'), setup.get('stop_loss'), setup.get('take_profit_1')
            ])
            tp2_data = setup.get('take_profit')
            if isinstance(tp2_data, list) and tp2_data:
                setup_prices.append(tp2_data[0])
            elif isinstance(tp2_data, (int, float)):
                setup_prices.append(tp2_data)
        
        valid_setup_prices = [p for p in setup_prices if p is not None]
        
        # Wyznacz ostateczny zakres Y
        final_min_price = min(min_candle_price, *valid_setup_prices) if valid_setup_prices else min_candle_price
        final_max_price = max(max_candle_price, *valid_setup_prices) if valid_setup_prices else max_candle_price
        
        # Dodaj margines (padding)
        padding = (final_max_price - final_min_price) * 0.05
        plot_area.setYRange(final_min_price - padding, final_max_price + padding)

    context.plot_area = plot_area
    return plot_area

# ... Reszta funkcji pomocniczych ...
def _draw_candlesticks(context, plot, df, alert_data=None):
    timestamps = df.index.astype(np.int64) // 10**9
    alert_ts = alert_data.alert_timestamp if alert_data else None
    context.plotted_items['candlesticks'] = [CandlestickItem(list(zip(timestamps, df['Open'], df['High'], df['Low'], df['Close'])), alert_timestamp=alert_ts)]
    plot.addItem(context.plotted_items['candlesticks'][0])

def _draw_emas(context, plot, df, params):
    context.plotted_items['ema'] = []
    ts = df.index.astype(np.int64) // 10**9
    f_len, s_len = params.get('ema_fast_length', 50), params.get('ema_slow_length', 200)
    if f'EMA_{f_len}' in df.columns:
        context.plotted_items['ema'].append(plot.plot(ts, df[f'EMA_{f_len}'], pen=pg.mkPen('#3498DB', width=2)))
    if f'EMA_{s_len}' in df.columns:
        context.plotted_items['ema'].append(plot.plot(ts, df[f'EMA_{s_len}'], pen=pg.mkPen('#F1C40F', width=2)))

def _draw_bbands(context, plot, df, params):
    context.plotted_items['bb'] = []
    ts = df.index.astype(np.int64) // 10**9
    up_key = f"BBU_{params.get('bbands_length', 20)}_{params.get('bbands_std', 2.0)}"
    low_key = f"BBL_{params.get('bbands_length', 20)}_{params.get('bbands_std', 2.0)}"
    if up_key in df and low_key in df:
        up_item, low_item = plot.plot(ts, df[up_key], pen=pg.mkPen('#95A5A6', style=Qt.PenStyle.DashLine)), plot.plot(ts, df[low_key], pen=pg.mkPen('#95A5A6', style=Qt.PenStyle.DashLine))
        fill = pg.FillBetweenItem(up_item, low_item, brush=(91, 99, 120, 50))
        plot.addItem(fill)
        context.plotted_items['bb'].extend([up_item, low_item, fill])

def _draw_support_resistance(context, plot, sr_data):
    context.plotted_items['sr_levels'] = []
    support_pen, resistance_pen = pg.mkPen('#2ECC71', width=2), pg.mkPen('#E74C3C', width=2)
    for level in sr_data.get('support', []):
        line = pg.InfiniteLine(pos=level, angle=0, pen=support_pen, label='Wsparcie {value:.4f}', labelOpts={'position': 0.85})
        plot.addItem(line)
        context.plotted_items['sr_levels'].append(line)
    for level in sr_data.get('resistance', []):
        line = pg.InfiniteLine(pos=level, angle=0, pen=resistance_pen, label='Opór {value:.4f}', labelOpts={'position': 0.85})
        plot.addItem(line)
        context.plotted_items['sr_levels'].append(line)

def _draw_fvgs(context, plot, fvgs: List[Dict]):
    context.plotted_items['fvgs'] = []
    bullish_brush, bearish_brush = QColor(0, 150, 255, 40), QColor(255, 165, 0, 40)
    for gap in fvgs:
        brush = bullish_brush if gap['type'] == 'bullish' else bearish_brush
        rect = pg.QtWidgets.QGraphicsRectItem(gap['start_time'], gap['start_price'], gap['width_seconds'], gap['end_price'] - gap['start_price'])
        rect.setBrush(brush)
        rect.setPen(pg.mkPen(None))
        plot.addItem(rect)
        context.plotted_items['fvgs'].append(rect)

def _draw_setup_zones(context, plot, setup_data: dict):
    context.plotted_items['setup'] = []
    entry = setup_data.get('entry')
    stop_loss = setup_data.get('stop_loss')
    take_profit_levels = setup_data.get('take_profit')

    if entry and stop_loss:
        sl_region = pg.LinearRegionItem(values=[entry, stop_loss], orientation='horizontal', brush=QColor(231, 76, 60, 40), pen=pg.mkPen(None))
        plot.addItem(sl_region)
        context.plotted_items['setup'].append(sl_region)

    if entry and take_profit_levels:
        # --- NOWA, ODPORNA LOGIKA ---
        # Sprawdzamy, czy take_profit jest listą. Jeśli nie, tworzymy z niego listę.
        if not isinstance(take_profit_levels, list):
            take_profit_levels = [take_profit_levels]
        
        # Rysujemy strefę tylko jeśli lista nie jest pusta
        if take_profit_levels:
            target_tp = take_profit_levels[-1]
            tp_region = pg.LinearRegionItem(values=[entry, target_tp], orientation='horizontal', brush=QColor(46, 204, 113, 40), pen=pg.mkPen(None))
            plot.addItem(tp_region)
            context.plotted_items['setup'].append(tp_region)

    _draw_sl_tp_lines(context, plot, setup_data)
    
def _draw_sl_tp_lines(context, plot, setup_data: dict):
    """NOWA WERSJA: Rysuje wszystkie poziomy (Entry, SL, TP1, TP2) z odpowiednimi etykietami."""
    if 'setup' not in context.plotted_items:
        context.plotted_items['setup'] = []
    
    # Rysowanie ceny wejścia
    if entry := setup_data.get('entry'):
        line = pg.InfiniteLine(pos=entry, angle=0, pen=pg.mkPen('cyan', style=Qt.PenStyle.DashLine), 
                               label='Wejście {value:.4f}', labelOpts={'position': 0.15})
        plot.addItem(line)
        context.plotted_items['setup'].append(line)
        
    # Rysowanie Stop Lossa
    if sl := setup_data.get('stop_loss'):
        line = pg.InfiniteLine(pos=sl, angle=0, pen=pg.mkPen('red', style=Qt.PenStyle.DashLine), 
                               label='Stop Loss {value:.4f}', labelOpts={'position': 0.15})
        plot.addItem(line)
        context.plotted_items['setup'].append(line)
        
    # --- NOWA, INTELIGENTNA LOGIKA DLA TAKE PROFIT ---
    
    # Rysowanie TP1 (jeśli istnieje)
    if tp1 := setup_data.get('take_profit_1'):
        line = pg.InfiniteLine(pos=tp1, angle=0, pen=pg.mkPen('#00A86B', style=Qt.PenStyle.DotLine, width=2), 
                               label='TP1 (częściowy) {value:.4f}', labelOpts={'position': 0.15, 'color': '#00A86B'})
        plot.addItem(line)
        context.plotted_items['setup'].append(line)

    # Rysowanie TP2 (ostatecznego)
    tps = setup_data.get('take_profit')
    if tps:
        # Upewniamy się, że tps to lista
        if not isinstance(tps, list):
            tps = [tps]
            
        if tps: # Sprawdzamy, czy lista nie jest pusta
            tp2 = tps[0]
            line = pg.InfiniteLine(pos=tp2, angle=0, pen=pg.mkPen('green', style=Qt.PenStyle.DashLine, width=2), 
                                   label='TP2 (końcowy) {value:.4f}', labelOpts={'position': 0.15, 'color': 'green'})
            plot.addItem(line)
            context.plotted_items['setup'].append(line)

def populate_indicator_summary_table(all_timeframe_data: dict, table_widget: QTableWidget):
    table_widget.clear()
    table_widget.setColumnCount(0)
    table_widget.setRowCount(0)
    if not all_timeframe_data: return

    timeframes = sorted(all_timeframe_data.keys(), key=lambda tf: (int(tf[:-1]) if tf[:-1].isdigit() else 99, tf[-1]))
    indicators_of_interest = ["EMA_Trend", "RSI", "MACD", "Bollinger_Bands", "RSI_Divergence"]
    colors = {'bullish': QColor(26, 115, 232, 30), 'bearish': QColor(217, 48, 37, 30), 'neutral': QColor(128, 128, 128, 20)}
    icons = {'bullish': '▲', 'bearish': '▼', 'neutral': '●'}

    table_widget.setRowCount(len(indicators_of_interest))
    table_widget.setColumnCount(len(timeframes))
    table_widget.setHorizontalHeaderLabels(timeframes)
    table_widget.setVerticalHeaderLabels(indicators_of_interest)

    for row, indicator_name in enumerate(indicators_of_interest):
        for col, tf in enumerate(timeframes):
            indicator_data = all_timeframe_data.get(tf, {}).get('interpreted', {}).get(indicator_name)
            item = pg.QtWidgets.QTableWidgetItem("B/D")
            if indicator_data and isinstance(indicator_data, dict):
                text, sentiment = indicator_data.get('text', 'Błąd'), indicator_data.get('sentiment', 'neutral')
                icon = icons.get(sentiment, '●')
                item.setText(f"{icon} {text}")
                item.setBackground(colors.get(sentiment, colors['neutral']))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table_widget.setItem(row, col, item)

    table_widget.resizeColumnsToContents()
    table_widget.resizeRowsToContents()

def generate_html_from_analysis(parsed_data: dict) -> str:
    if not parsed_data:
        return "<h3>Błąd</h3><p>Otrzymano puste dane do wygenerowania raportu.</p>"

    def format_price(p):
        if p is None: return "N/A"
        try:
            price = float(p)
            if price >= 1.0: return f"${price:,.2f}" 
            elif price < 0.0001: return f"${price:,.8g}"
            else: return f"${price:,.4g}"
        except (ValueError, TypeError): return str(p)

    # --- NOWA, INTELIGENTNA LOGIKA ---
    # Jeśli AI nie dało wniosków, informujemy o tym.
    conclusions = parsed_data.get('key_conclusions', '').strip()
    if not conclusions or conclusions.lower() in ["brak wniosków.", "brak wniosków"]:
        conclusions = "<i>AI nie dostarczyło szczegółowego uzasadnienia.</i>"
    html = f"<h3>Kluczowe Wnioski Techniczne</h3><p>{conclusions}</p>"

    sr_data = parsed_data.get('support_resistance', {})
    supports = sr_data.get('support', [])
    resistances = sr_data.get('resistance', [])
    html += "<h3>Analiza Kontekstowa (Price Action)</h3>"
    if supports:
        html += "<p><b>Poziomy Wsparcia:</b> " + ", ".join([f"{format_price(s)}" for s in supports]) + "</p>"
    if resistances:
        html += "<p><b>Poziomy Oporu:</b> " + ", ".join([f"{format_price(r)}" for r in resistances]) + "</p>"

    html += "<h3>Najlepszy Setup (do obserwacji)</h3>"
    setup = parsed_data.get('setup')

    if setup and isinstance(setup, dict):
        # ... (logika statusu bez zmian) ...
        status = setup.get('status')
        if status == 'immediate': html += "<p><b>Status:</b> <span style='color:#2ECC71;'>OKAZJA NATYCHMIASTOWA</span></p>"
        elif status == 'potential': html += "<p><b>Status:</b> <span style='color:#F1C40F;'>SETUP POTENCJALNY (do obserwacji)</span></p>"
        
        # --- NOWA, INTELIGENTNA LOGIKA ---
        # Jeśli AI nie dało triggera, tworzymy go sami na podstawie key_level
        trigger = setup.get('trigger_text', '').strip()
        if not trigger or trigger.lower() == 'brak opisu':
            trigger = f"Obserwuj reakcję ceny w pobliżu kluczowego poziomu {format_price(setup.get('entry'))}"

        # ... (reszta logiki bez zmian, ale używamy nowych zmiennych) ...
        scenario = setup.get('type', 'N/A')
        stop_loss = setup.get('stop_loss')
        tp1 = setup.get('take_profit_1')
        tp2_data = setup.get('take_profit')
        tp2 = (tp2_data[0] if isinstance(tp2_data, list) and tp2_data else tp2_data) if tp2_data else None
        confidence = setup.get('confidence')
        entry = setup.get('entry')
        
        rr_ratio_tp1, rr_ratio_tp2 = None, None
        if entry and stop_loss and (risk := abs(entry - stop_loss)) > 0:
            if tp1: rr_ratio_tp1 = abs(tp1 - entry) / risk
            if tp2: rr_ratio_tp2 = abs(tp2 - entry) / risk
        
        html += f"<p><b>Scenariusz:</b> {scenario}</p>"
        html += "<ul>"
        html += f"<li><b>Trigger wejścia:</b> {trigger}</li>"
        html += f"<li><b>Stop Loss:</b> {format_price(stop_loss)}</li>"
        if tp1: html += f"<li><b>Take Profit 1 (częściowy):</b> {format_price(tp1)}" + (f" (R:R {rr_ratio_tp1:.2f}:1)" if rr_ratio_tp1 else "") + "</li>"
        if tp2: html += f"<li><b>Take Profit 2 (końcowy):</b> {format_price(tp2)}" + (f" (R:R {rr_ratio_tp2:.2f}:1)" if rr_ratio_tp2 else "") + "</li>"
        if confidence is not None: html += f"<li><b>Wiarygodność setupu:</b> {confidence}/10</li>"
        html += "</ul>"
    else:
        # ... (logika dla braku setupu bez zmian) ...
        reasoning = "AI nie znalazło setupu spełniającego wszystkie kryteria."
        if isinstance(parsed_data.get('setup'), dict): reasoning = parsed_data.get('setup').get('reasoning') or reasoning
        html += f"<p>{reasoning}</p>"

    return html

def export_widget_to_image_bytes(widget: pg.GraphicsLayoutWidget) -> bytes:
    """Eksportuje zawartość widgetu do obiektu bytes w formacie PNG."""
    exporter = pg.exporters.ImageExporter(widget.scene())
    # Ustawiamy parametry, aby obrazek był dobrej jakości
    params = exporter.parameters()
    params['width'] = 1080
    params['height'] = 1080
    image: QImage = exporter.export(toBytes=True)

    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.ReadWrite)
    image.save(buffer, "PNG")
    return bytes(buffer.data())

def _draw_fibonacci_levels(context, plot, fib_data: dict):
    """Rysuje na wykresie poziomy zniesienia Fibonacciego i strefę Golden Pocket."""
    if not fib_data or 'golden_pocket' not in fib_data:
        return

    context.plotted_items['fibonacci'] = []

    # Rysujemy strefę Golden Pocket
    gp = fib_data['golden_pocket']
    golden_pocket_region = LinearRegionItem(
        values=[gp['start'], gp['end']],
        orientation='horizontal',
        brush=pg.mkBrush(218, 165, 32, 40), # Złoty kolor z przezroczystością
        pen=pg.mkPen(None)
    )
    plot.addItem(golden_pocket_region)
    context.plotted_items['fibonacci'].append(golden_pocket_region)

    # Rysujemy pozostałe poziomy Fibo
    pen = pg.mkPen(color=(100, 100, 150), style=Qt.PenStyle.DashLine)
    for level_val, price in fib_data.get('levels', {}).items():
        line = pg.InfiniteLine(pos=price, angle=0, pen=pen, label=f'Fibo {level_val}', 
                               labelOpts={'position': 0.95, 'color': (100, 100, 150)})
        plot.addItem(line)
        context.plotted_items['fibonacci'].append(line)

def _draw_trade_events(context, plot, events: List[Dict]):
    """Rysuje na wykresie zdarzenia z życia transakcji (TP1, SL to BE)."""
    if 'trade_events' not in context.plotted_items:
        context.plotted_items['trade_events'] = []
    
    for event in events:
        event_time = event.get('timestamp')
        event_type = event.get('event_type')
        details = event.get('details', {})
        price_level = details.get('price')

        if not event_time or not event_type:
            continue
            
        if event_type == 'TP1_HIT' and price_level:
            # Rysujemy linię dla TP1
            tp1_line = pg.InfiniteLine(pos=price_level, angle=0, pen=pg.mkPen('#00A86B', style=Qt.PenStyle.DashLine, width=2), 
                                       label='TP1 {value:.4f}', labelOpts={'position': 0.85, 'color': '#00A86B'})
            plot.addItem(tp1_line)
            context.plotted_items['trade_events'].append(tp1_line)

        if event_type == 'SL_MOVED_TO_BE':
            # Rysujemy pionową linię w miejscu, gdzie SL został przesunięty
            event_line = pg.InfiniteLine(pos=event_time, angle=90, pen=pg.mkPen('#1E90FF', style=Qt.PenStyle.DotLine),
                                         label='SL to B/E', labelOpts={'position': 0.95, 'color': '#1E90FF', 'movable': True})
            plot.addItem(event_line)
            context.plotted_items['trade_events'].append(event_line)
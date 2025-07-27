# Plik: ui/history_dialog.py (NOWA, ROZBUDOWANA WERSJA)
import re
import logging
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QDialogButtonBox, QHBoxLayout, QLabel,
    QMessageBox, QComboBox, QLineEdit
)
# <<< ZMIANA: Dodajemy import sygnału >>>
from PyQt6.QtCore import pyqtSignal, Qt, QPointF
from PyQt6.QtGui import QPainter, QPicture
from datetime import datetime


logger = logging.getLogger(__name__)



class AddCoinDialog(QDialog):
    def __init__(self, parent=None, available_symbols: dict=None, user_coin_groups: dict=None):
        super().__init__(parent)
        self.setWindowTitle("Dodaj Coin do Grupy"); self.setFixedSize(400, 250)
        self.available_symbols = available_symbols or {}; self.user_coin_groups = user_coin_groups or {}
        self.selected_symbol, self.selected_exchange, self.selected_group = None, None, None
        layout=QVBoxLayout(self); layout.addWidget(QLabel("Wybierz Grupę:"))
        self.group_combo=QComboBox(); self.group_combo.addItems(sorted(self.user_coin_groups.keys())); layout.addWidget(self.group_combo)
        layout.addWidget(QLabel("Wyszukaj Symbol:"))
        self.symbol_filter=QLineEdit(placeholderText="Np. BTC/USDT"); self.symbol_filter.textChanged.connect(self.filter_symbols); layout.addWidget(self.symbol_filter)
        self.symbol_list=QListWidget(); self.symbol_list.itemClicked.connect(self.on_symbol_selected); layout.addWidget(self.symbol_list)
        self.exchange_label=QLabel("Wybrana Giełda: N/A"); layout.addWidget(self.exchange_label)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.filter_symbols()
    def filter_symbols(self):
        filter_text=self.symbol_filter.text().upper(); self.symbol_list.clear()
        matching_symbols=[]
        if self.available_symbols:
            for symbol, exchanges in self.available_symbols.items():
                if filter_text in symbol:
                    ex_str=f" ({', '.join(exchanges)})" if isinstance(exchanges, list) and exchanges else ""
                    matching_symbols.append(f"{symbol}{ex_str}")
        self.symbol_list.addItems(sorted(matching_symbols) if matching_symbols else ["Brak wyników."])
    def on_symbol_selected(self, item):
        full_text=item.text(); self.selected_symbol, self.selected_exchange=None, None; self.exchange_label.setText("Wybrana Giełda: N/A")
        if "Brak wyników" in full_text: return
        match=re.match(r"^(.*?)\s*\((.*)\)$", full_text)
        if match: s, e=match.group(1).strip(), match.group(2).strip().split(',')[0]
        else: s=full_text.split('(')[0].strip(); exchanges=self.available_symbols.get(s); e=exchanges[0] if isinstance(exchanges, list) and exchanges else "BINANCE"
        self.selected_symbol, self.selected_exchange=s, e; self.exchange_label.setText(f"Wybrana Giełda: {e}")
    def accept(self):
        self.selected_group=self.group_combo.currentText()
        if self.selected_symbol and self.selected_group and self.selected_exchange: super().accept()
        else: QMessageBox.warning(self, "Błąd", "Wybierz symbol z listy.")

class DateAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        strings = []
        for value in values:
            try:
                # Próbujemy konwertować wartość na datę
                s = datetime.fromtimestamp(value).strftime('%Y-%m-%d')
            except (OSError, ValueError):
                # Jeśli się nie uda (np. wartość jest poza zakresem), zwracamy pusty string
                s = ""
            strings.append(s)
        return strings
        
class CandlestickItem(pg.GraphicsObject):
    def __init__(self, data, alert_timestamp=None):
        pg.GraphicsObject.__init__(self)
        self.data = data if data is not None and len(data) >= 2 else []
        self.alert_timestamp = alert_timestamp
        if self.data: self.generatePicture()
        else: self.picture = pg.QtGui.QPicture()
    def generatePicture(self):
        self.picture = pg.QtGui.QPicture()
        p = pg.QtGui.QPainter(self.picture)

        try:
            timestamps = [item[0] for item in self.data]
            avg_diff = (timestamps[-1] - timestamps[0]) / (len(timestamps) - 1) if len(timestamps) > 1 else 1.0
            w = avg_diff / 3.0
        except (ZeroDivisionError, IndexError): 
            w = 1.0

        for t, open_price, high, low, close in self.data:
            # Sprawdzamy, czy świeca jest "nowa" w stosunku do czasu alertu
            is_new_candle = self.alert_timestamp and t > self.alert_timestamp

            if is_new_candle:
                # Kolory szarości dla nowych świec
                p.setPen(pg.mkPen(color=(180, 180, 180))) # Kijek
                candle_color = pg.mkColor(120, 120, 120) if open_price < close else pg.mkColor(80, 80, 80)
            else:
                # Oryginalne, kolorowe świece
                p.setPen(pg.mkPen(color=(220, 220, 220))) # Kijek
                candle_color = pg.mkColor('#26a69a') if open_price < close else pg.mkColor('#ef5350')

            p.drawLine(pg.QtCore.QPointF(t, low), pg.QtCore.QPointF(t, high))
            if open_price is not None and close is not None:
                p.setBrush(pg.mkBrush(candle_color))
                p.setPen(pg.mkPen(candle_color))
                p.drawRect(pg.QtCore.QRectF(t - w, open_price, w * 2, close - open_price))
        p.end()
    def paint(self, p, *args):
        if self.picture: p.drawPicture(0, 0, self.picture)
    def boundingRect(self):
        return pg.QtCore.QRectF(self.picture.boundingRect()) if self.picture else pg.QtCore.QRectF()



# Plik: ui/settings_tab.py (WERSJA PO PRZEBUDOWIE)

import asyncio
import logging
from typing import Dict, Any

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QCheckBox, QScrollArea, QSpinBox,
    QDoubleSpinBox, QMessageBox, QGroupBox, QFrame
)
from PyQt6.QtCore import pyqtSignal

from app_config import DEFAULT_SETTINGS

logger = logging.getLogger(__name__)

# Definiujemy strukturę, opisy i typy wszystkich ustawień
# To jest nasz nowy "mózg" tej zakładki
SETTINGS_STRUCTURE = {
    "Wygląd i AI": [
        {'key': 'app.theme', 'label': 'Motyw graficzny:', 'widget': QComboBox, 'params': {'items': ["Ciemny", "Jasny"]}, 'tooltip': 'Zmienia schemat kolorów aplikacji.'},
        {'key': 'ai.url', 'label': 'URL API AI:', 'widget': QLineEdit, 'params': {}, 'tooltip': 'Adres URL serwera AI zgodnego z API OpenAI.'},
        {'key': 'ai.model', 'label': 'Model AI:', 'widget': QLineEdit, 'params': {}, 'tooltip': 'Nazwa modelu, który ma być używany do analizy.'},
        {'key': 'ai.temperature', 'label': 'Temperatura:', 'widget': QDoubleSpinBox, 'params': {'minimum': 0.0, 'maximum': 2.0, 'singleStep': 0.1}, 'tooltip': 'Kreatywność modelu AI (wyższa = bardziej zróżnicowane odpowiedzi).'},
        {'key': 'ai.timeout', 'label': 'Timeout zapytania (s):', 'widget': QSpinBox, 'params': {'minimum': 10, 'maximum': 900}, 'tooltip': 'Maksymalny czas oczekiwania na odpowiedź od serwera AI.'},
    ],
    "Moduły Kontekstowe AI": [
        {'key': 'ai_context_modules.use_market_regime', 'label': 'Używaj Reżimu Rynku', 'widget': QCheckBox, 'params': {}, 'tooltip': 'Przekazuje do AI informację o ogólnym stanie rynku (hossa/bessa/konsolidacja) na podstawie BTC i ETH.'},
        {'key': 'ai_context_modules.use_order_flow', 'label': 'Używaj Order Flow', 'widget': QCheckBox, 'params': {}, 'tooltip': 'Przekazuje do AI analizę arkusza zleceń i ostatnich transakcji (presja popytu/podaży).'},
        {'key': 'ai_context_modules.use_onchain_data', 'label': 'Używaj Danych On-Chain', 'widget': QCheckBox, 'params': {}, 'tooltip': 'Przekazuje do AI dane o Funding Rate i Open Interest.'},
        {'key': 'ai_context_modules.use_performance_insights', 'label': 'Używaj Wniosków z Historii', 'widget': QCheckBox, 'params': {}, 'tooltip': 'Przekazuje do AI statystyki o historycznej skuteczności strategii Long/Short.'},
    ],
    "Skaner Ssnedam i Walidacja": [
        {'key': 'ssnedam.enabled', 'label': 'Włącz skaner w tle', 'widget': QCheckBox, 'params': {}, 'tooltip': 'Czy autonomiczny skaner ma wyszukiwać alerty w tle.'},
        {'key': 'ssnedam.group', 'label': 'Skanuj grupę:', 'widget': QComboBox, 'params': {'items': []}, 'tooltip': 'Grupa monet, która będzie monitorowana przez skaner.'},
        {'key': 'ssnedam.interval_minutes', 'label': 'Interwał skanowania (min):', 'widget': QSpinBox, 'params': {'minimum': 1, 'maximum': 120}, 'tooltip': 'Jak często skaner ma uruchamiać pełne skanowanie (w minutach).'},
        {'key': 'ssnedam.alert_interval', 'label': 'Interwał analizy (setupów):', 'widget': QComboBox, 'params': {'items': ['15m', '30m', '1h', '2h', '4h', '1d']}, 'tooltip': 'Na jakim interwale czasowym skaner ma szukać wstępnych "pułapek".'},
        {'key': 'ssnedam.cooldown_minutes', 'label': 'Cooldown dla alertów (min):', 'widget': QSpinBox, 'params': {'minimum': 1, 'maximum': 240}, 'tooltip': 'Ile minut musi minąć, zanim dla tego samego coina zostanie wygenerowany nowy alert.'},
        {'key': 'ssnedam.scanner_prominence', 'label': 'Czułość skanera (Prominencja):', 'widget': QDoubleSpinBox, 'params': {'minimum': 0.1, 'maximum': 2.0, 'singleStep': 0.1}, 'tooltip': 'Jak "wydatny" musi być szczyt/dołek, aby skaner go zauważył. Mniejsza wartość = więcej sygnałów.'},
        {'key': 'ssnedam.scanner_distance', 'label': 'Czułość skanera (Dystans):', 'widget': QSpinBox, 'params': {'minimum': 2, 'maximum': 50}, 'tooltip': 'Minimalna odległość między dwoma szczytami/dołkami. Większa wartość = sygnały z dłuższych struktur.'},
        {'key': 'ai.min_rr_ratio', 'label': 'Minimalny akceptowalny R:R:', 'widget': QDoubleSpinBox, 'params': {'minimum': 1.0, 'maximum': 10.0, 'singleStep': 0.1}, 'tooltip': 'Globalna zasada: setupy z niższym R:R zostaną odrzucone, nawet jeśli AI je zaproponuje.'},
    ],
    "Parametry Strategii (Backtester)": [
        {'key': 'strategies.ai_clone.ema_fast_len', 'label': 'Szybka EMA:', 'widget': QSpinBox, 'params': {'minimum': 2, 'maximum': 100}, 'tooltip': 'Długość szybkiej średniej kroczącej w strategii AIClone.'},
        {'key': 'strategies.ai_clone.ema_slow_len', 'label': 'Wolna EMA:', 'widget': QSpinBox, 'params': {'minimum': 10, 'maximum': 500}, 'tooltip': 'Długość wolnej średniej kroczącej (filtr trendu).'},
        {'key': 'strategies.ai_clone.rsi_len', 'label': 'Długość RSI:', 'widget': QSpinBox, 'params': {'minimum': 2, 'maximum': 50}, 'tooltip': 'Długość wskaźnika RSI.'},
        {'key': 'strategies.ai_clone.rsi_overbought', 'label': 'Poziom wykupienia RSI:', 'widget': QSpinBox, 'params': {'minimum': 50, 'maximum': 100}, 'tooltip': 'Poziom RSI, powyżej którego strategia nie będzie szukać wejść w pozycje długie.'},
        {'key': 'strategies.ai_clone.atr_len', 'label': 'Długość ATR:', 'widget': QSpinBox, 'params': {'minimum': 2, 'maximum': 50}, 'tooltip': 'Długość wskaźnika ATR, używanego do obliczania Stop Lossa.'},
        {'key': 'strategies.ai_clone.atr_multiplier_sl', 'label': 'Mnożnik ATR dla SL:', 'widget': QDoubleSpinBox, 'params': {'minimum': 0.1, 'maximum': 10.0, 'singleStep': 0.1}, 'tooltip': 'Jak daleko od ceny wejścia (w wielokrotnościach ATR) ma być ustawiony Stop Loss.'},
        {'key': 'strategies.ai_clone.risk_reward_ratio', 'label': 'Docelowy R:R strategii:', 'widget': QDoubleSpinBox, 'params': {'minimum': 0.5, 'maximum': 10.0, 'singleStep': 0.1}, 'tooltip': 'Docelowy stosunek zysku do ryzyka dla tej strategii.'},
        {'key': 'ai.validation.max_tp_to_atr_ratio', 'label': 'Maks. stosunek TP do ATR:', 'widget': QDoubleSpinBox, 'params': {'minimum': 1.0, 'maximum': 10.0, 'singleStep': 0.1}, 'tooltip': 'Walidacja: Odrzuca setupy, w których TP jest nierealistycznie daleko w stosunku do zmienności.'},
        {'key': 'ai.validation.golden_setup_min_confidence', 'label': 'Min. pewność złotego setupu:', 'widget': QSpinBox, 'params': {'minimum': 1, 'maximum': 10}, 'tooltip': 'Minimalna pewność (confidence), aby setup został uznany za "złoty przykład" do nauki AI.'},
        {'key': 'ssnedam.sr_scanner_prominence_multiplier', 'label': 'Czułość S/R (Prominencja):', 'widget': QDoubleSpinBox, 'params': {'minimum': 0.1, 'maximum': 5.0, 'singleStep': 0.1}, 'tooltip': 'Mnożnik odchylenia standardowego dla czułości skanera poziomów S/R.'},
        {'key': 'ssnedam.sr_scanner_distance', 'label': 'Czułość S/R (Dystans):', 'widget': QSpinBox, 'params': {'minimum': 2, 'maximum': 50}, 'tooltip': 'Minimalna odległość w świecach między szczytami/dołkami dla skanera S/R.'}
    ]
}

class SettingsTab(QWidget):
    settings_changed = pyqtSignal()

    def __init__(self, settings_manager, coin_manager, ai_client, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.coin_manager = coin_manager
        self.ai_client = ai_client
        self.controls_map: Dict[str, QWidget] = {}

        self._setup_ui()
        self._connect_signals()
        self.load_settings()

    def _setup_ui(self):
        """Automatycznie generuje UI na podstawie struktury SETTINGS_STRUCTURE."""
        main_layout = QVBoxLayout(self)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        content_widget = QWidget(); 
        
        # Używamy QHBoxLayout do stworzenia kolumn
        columns_layout = QHBoxLayout(content_widget)

        for group_title, settings_list in SETTINGS_STRUCTURE.items():
            group_box = QGroupBox(group_title)
            form_layout = QFormLayout(group_box)
            
            for setting in settings_list:
                key = setting['key']
                label_text = setting['label']
                WidgetClass = setting['widget']
                params = setting['params']
                tooltip = setting['tooltip']

                if WidgetClass == QCheckBox:
                    control = QCheckBox(label_text)
                    form_layout.addRow(control)
                else:
                    control = WidgetClass()
                    form_layout.addRow(label_text, control)
                
                # Ustawianie parametrów specyficznych dla widgetu
                if 'items' in params:
                    control.addItems(params['items'])
                for param, value in params.items():
                    if param != 'items':
                        setter = getattr(control, f"set{param.capitalize()}", None)
                        if setter: setter(value)
                
                control.setToolTip(tooltip)
                self.controls_map[key] = control
            
            # Dodajemy gotową grupę do layoutu kolumnowego
            columns_layout.addWidget(group_box)

        scroll.setWidget(content_widget)
        main_layout.addWidget(scroll)

        # Przyciski ZAPISZ / PRZYWRÓĆ
        btn_layout = QHBoxLayout()
        self.test_ai_connection_btn = QPushButton("Testuj Połączenie AI")
        self.settings_restore_btn = QPushButton("Przywróć Domyślne")
        self.settings_save_btn = QPushButton("Zapisz i Zastosuj")
        btn_layout.addWidget(self.test_ai_connection_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.settings_restore_btn)
        btn_layout.addWidget(self.settings_save_btn)
        main_layout.addLayout(btn_layout)

    # Metody _connect_signals, update_group_list, load_settings, save_settings i restore_settings
    # POZOSTAJĄ BEZ ZMIAN, ponieważ operują na `self.controls_map`, który jest teraz
    # wypełniany przez naszą nową, automatyczną pętlę.
    
    def _connect_signals(self):
        self.settings_save_btn.clicked.connect(self.save_settings)
        self.settings_restore_btn.clicked.connect(self.restore_settings)
        self.test_ai_connection_btn.clicked.connect(self._on_test_ai_connection_clicked)
    
    def update_group_list(self, groups: list):
        control = self.controls_map.get('ssnedam.group')
        if not control: return
        current_group = control.currentText()
        control.clear()
        control.addItems(groups)
        if current_group in groups:
            control.setCurrentText(current_group)
        elif groups:
            control.setCurrentIndex(0)

    def refresh_group_list(self):
        """Pobiera aktualną listę grup z CoinManagera i odświeża ComboBox."""
        groups = sorted(self.coin_manager.get_user_coin_groups().keys())
        self.update_group_list(groups)

    def load_settings(self):
        
        for key, control in self.controls_map.items():
            value = self.settings_manager.get(key)
            if value is None: continue
            if isinstance(control, QComboBox):
                if key == 'app.theme':
                    control.setCurrentText(str(value).capitalize())
                else:
                    control.setCurrentText(str(value))
            elif isinstance(control, QCheckBox):
                control.setChecked(bool(value))
            elif isinstance(control, QSpinBox): # Osobna obsługa dla liczb całkowitych
                control.setValue(int(float(value))) # Konwertujemy na int
            elif isinstance(control, QDoubleSpinBox): # Osobna obsługa dla liczb zmiennoprzecinkowych
                control.setValue(float(value)) # Konwertujemy na float
            elif isinstance(control, QLineEdit):
                control.setText(str(value))

    def save_settings(self):
        for key, control in self.controls_map.items():
            value = None
            if isinstance(control, QComboBox):
                value = control.currentText()
                if key == 'app.theme':
                    value = value.lower()
            elif isinstance(control, QCheckBox):
                value = control.isChecked()
            elif isinstance(control, (QSpinBox, QDoubleSpinBox)):
                value = control.value()
            elif isinstance(control, QLineEdit):
                value = control.text()
            if value is not None:
                self.settings_manager.set(key, value)
        if self.settings_manager.save_settings():
            QMessageBox.information(self, "Sukces", "Ustawienia zostały zapisane.")
            self.settings_changed.emit()

    def restore_settings(self):
        confirm = QMessageBox.question(self, "Potwierdź",
                                       "Czy na pewno chcesz przywrócić ustawienia fabryczne?",
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm == QMessageBox.StandardButton.Yes:
            # Używamy głębokiej kopii, aby uniknąć problemów z referencjami
            import copy
            self.settings_manager.settings = copy.deepcopy(DEFAULT_SETTINGS)
            if self.settings_manager.save_settings():
                self.load_settings()
                QMessageBox.information(self, "Sukces", "Przywrócono ustawienia domyślne.")
                self.settings_changed.emit()

    def _on_test_ai_connection_clicked(self):
        url_to_test = self.controls_map['ai.url'].text()
        if not url_to_test:
            QMessageBox.warning(self, "Brak URL", "Proszę wpisać URL serwera AI do przetestowania.")
            return
        asyncio.create_task(self._execute_connection_test(url_to_test))

    async def _execute_connection_test(self, url: str):
        original_text = self.test_ai_connection_btn.text()
        self.test_ai_connection_btn.setText("Testuję...")
        self.test_ai_connection_btn.setEnabled(False)
        try:
            # Zmieniamy, aby testowało połączenie z nowym URL, a nie starym
            await self.ai_client.test_connection_async(url)
            QMessageBox.information(self, "Sukces", f"Pomyślnie połączono z serwerem:\n{url}")
        except Exception as e:
            QMessageBox.critical(self, "Błąd Połączenia", f"Nie udało się połączyć z serwerem.\n\nBłąd: {e}")
        finally:
            self.test_ai_connection_btn.setText(original_text)
            self.test_ai_connection_btn.setEnabled(True)
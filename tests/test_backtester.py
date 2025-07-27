# Plik: tests/test_backtester.py

import pandas as pd
import pytest
from datetime import datetime
import asyncio

from core.backtester import Backtester
from core.strategy import Strategy
from core.settings_manager import SettingsManager

# Ta klasa pozostaje bez zmian
class BuyAndHoldUntilSLExecuted(Strategy):
    def init(self):
        pass

    def next(self):
        if not self._broker.in_position:
            entry_price = self.data.Close.iloc[self.i]
            sl_price = entry_price * 0.90
            tp_price = entry_price * 1.20
            self._broker.buy(sl=sl_price, tp=tp_price)

# ZMIANA: Dodajemy 'monkeypatch' jako argument do funkcji testowej
async def test_long_trade_closes_on_stop_loss(monkeypatch):
    """
    Testuje, czy długa pozycja jest poprawnie zamykana po osiągnięciu Stop Lossa.
    """
    # 1. Skonfiguruj (Arrange)
    settings_manager = SettingsManager()
    backtester = Backtester(settings_manager)

    entry_price = 100.0
    sl_price = 90.0
    
    data = {
        'Open':   [99, 101, 95],
        'High':   [102, 103, 96],
        'Low':    [98, 99, 89],
        'Close':  [entry_price, 102, 91]
    }
    index = pd.to_datetime([datetime(2025, 1, 1), datetime(2025, 1, 2), datetime(2025, 1, 3)])
    test_df = pd.DataFrame(data, index=index)
    
    # --- KLUCZOWA ZMIANA: MOCKOWANIE ---
    # Tworzymy fałszywą funkcję `_fetch_data`, która nie łączy się z internetem
    async def mock_fetch_data(*args, **kwargs):
        # Zamiast pobierać dane, przypisuje nasze przygotowane dane
        backtester._data = test_df
        return True # Udajemy, że pobieranie się udało

    # Używamy monkeypatch, aby tymczasowo podmienić prawdziwą funkcję _fetch_data na naszą fałszywą
    monkeypatch.setattr(backtester, '_fetch_data', mock_fetch_data)

    # 2. Stymuluj (Act)
    # Teraz `backtester.run` wywoła naszą fałszywą funkcję zamiast prawdziwej
    results, trades_df, equity_curve = await backtester.run(
        strategy_class=BuyAndHoldUntilSLExecuted,
        symbol="TEST/USDT",
        timeframe="1d",
        start_date="2025-01-01",
        end_date="2025-01-03",
        initial_capital=10000
    )

    # 3. Sprawdź (Assert)
    assert not trades_df.empty
    assert len(trades_df) == 1

    trade = trades_df.iloc[0]
    assert trade['entry_price'] == test_df['Close'].iloc[1] 
    assert trade['exit_price'] == pytest.approx(102.0 * 0.90)
    assert trade['profit_usd'] < 0

@pytest.mark.asyncio
async def test_long_trade_closes_on_take_profit(monkeypatch):
    """
    Testuje, czy długa pozycja jest poprawnie zamykana po osiągnięciu Take Profit.
    """
    # 1. Skonfiguruj (Arrange)
    settings_manager = SettingsManager()
    backtester = Backtester(settings_manager)

    # Przygotowujemy dane, które wymuszą trafienie w TP
    entry_price = 100.0
    tp_price = 120.0 # Oczekiwany TP dla wejścia 100 (strategia liczy 102 * 1.2 = 122.4)
    
    data = {
        'Open':   [99, 101, 105],
        'High':   [102, 103, 123],
        'Low':    [98, 99, 104],
        'Close':  [entry_price, 102, 118]
    }
    index = pd.to_datetime([datetime(2025, 1, 1), datetime(2025, 1, 2), datetime(2025, 1, 3)])
    test_df = pd.DataFrame(data, index=index)
    
    # Mockujemy funkcję pobierania danych
    async def mock_fetch_data(*args, **kwargs):
        backtester._data = test_df
        return True

    monkeypatch.setattr(backtester, '_fetch_data', mock_fetch_data)

    # 2. Stymuluj (Act)
    results, trades_df, equity_curve = await backtester.run(
        strategy_class=BuyAndHoldUntilSLExecuted,
        symbol="TEST/USDT",
        timeframe="1d",
        start_date="2025-01-01",
        end_date="2025-01-03",
        initial_capital=10000
    )

    # 3. Sprawdź (Assert)
    assert not trades_df.empty
    assert len(trades_df) == 1

    trade = trades_df.iloc[0]
    # Cena wejścia to wciąż Close drugiej świecy
    assert trade['entry_price'] == 102.0
    # Sprawdzamy, czy cena wyjścia jest DOKŁADNIE ceną TP obliczoną przez strategię
    assert trade['exit_price'] == pytest.approx(102.0 * 1.20)
    # Sprawdzamy, czy zanotowaliśmy zysk
    assert trade['profit_usd'] > 0

class SellAndHoldStrategy(Strategy):
    def init(self):
        pass

    def next(self):
        if not self._broker.in_position:
            entry_price = self.data.Close.iloc[self.i]
            # Dla shorta SL jest powyżej ceny wejścia, a TP poniżej
            sl_price = entry_price * 1.10  # 10% powyżej
            tp_price = entry_price * 0.80  # 20% poniżej
            self._broker.sell(sl=sl_price, tp=tp_price)


@pytest.mark.asyncio
async def test_short_trade_closes_on_stop_loss(monkeypatch):
    """Testuje, czy krótka pozycja jest poprawnie zamykana po osiągnięciu Stop Lossa."""
    # 1. Skonfiguruj (Arrange)
    settings_manager = SettingsManager()
    backtester = Backtester(settings_manager)

    # Dane, które wymuszą wejście na 100 i trafienie w SL na 110
    data = {
        'Open':   [102, 99, 108],
        'High':   [103, 101, 111], # <-- Szczyt tej świecy uderza w SL
        'Low':    [101, 98, 107],
        'Close':  [101, 100, 109]
    }
    index = pd.to_datetime([datetime(2025, 1, 1), datetime(2025, 1, 2), datetime(2025, 1, 3)])
    test_df = pd.DataFrame(data, index=index)
    
    async def mock_fetch_data(*args, **kwargs):
        backtester._data = test_df
        return True

    monkeypatch.setattr(backtester, '_fetch_data', mock_fetch_data)

    # 2. Stymuluj (Act)
    results, trades_df, equity_curve = await backtester.run(
        strategy_class=SellAndHoldStrategy,
        symbol="TEST/USDT", timeframe="1d", start_date="2025-01-01", end_date="2025-01-03"
    )

    # 3. Sprawdź (Assert)
    assert len(trades_df) == 1
    trade = trades_df.iloc[0]
    
    assert trade['type'] == 'SHORT'
    assert trade['entry_price'] == 100.0 # Wejście na zamknięciu drugiej świecy
    assert trade['exit_price'] == pytest.approx(100.0 * 1.10) # Wyjście na poziomie SL
    assert trade['profit_usd'] < 0 # To musi być strata


@pytest.mark.asyncio
async def test_short_trade_closes_on_take_profit(monkeypatch):
    """Testuje, czy krótka pozycja jest poprawnie zamykana po osiągnięciu Take Profit."""
    # 1. Skonfiguruj (Arrange)
    settings_manager = SettingsManager()
    backtester = Backtester(settings_manager)

    # Dane, które wymuszą wejście na 100 i trafienie w TP na 80
    data = {
        'Open':   [102, 99, 85],
        'High':   [103, 101, 86],
        'Low':    [101, 98, 79], # <-- Dołek tej świecy uderza w TP
        'Close':  [101, 100, 81]
    }
    index = pd.to_datetime([datetime(2025, 1, 1), datetime(2025, 1, 2), datetime(2025, 1, 3)])
    test_df = pd.DataFrame(data, index=index)
    
    async def mock_fetch_data(*args, **kwargs):
        backtester._data = test_df
        return True

    monkeypatch.setattr(backtester, '_fetch_data', mock_fetch_data)

    # 2. Stymuluj (Act)
    results, trades_df, equity_curve = await backtester.run(
        strategy_class=SellAndHoldStrategy,
        symbol="TEST/USDT", timeframe="1d", start_date="2025-01-01", end_date="2025-01-03"
    )

    # 3. Sprawdź (Assert)
    assert len(trades_df) == 1
    trade = trades_df.iloc[0]
    
    assert trade['type'] == 'SHORT'
    assert trade['entry_price'] == 100.0
    assert trade['exit_price'] == pytest.approx(100.0 * 0.80) # Wyjście na poziomie TP
    assert trade['profit_usd'] > 0 # To musi być zysk
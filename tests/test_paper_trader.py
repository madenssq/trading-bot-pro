

import pandas as pd
import pytest
import time
import asyncio
from datetime import datetime, timedelta

from core.paper_trader import PaperTrader
from core.analyzer import TechnicalAnalyzer
from core.settings_manager import SettingsManager
from core.database_manager import DatabaseManager # Upewnij się, że ten import jest

@pytest.mark.asyncio
async def test_pending_trade_is_invalidated_when_sl_hits_first(db_manager, monkeypatch):
    settings_manager = SettingsManager()
    analyzer = TechnicalAnalyzer(settings_manager, db_manager, None)
    paper_trader = PaperTrader(db_manager, analyzer, asyncio.Lock())

    trade_data = {
        "timestamp": time.time(), "symbol": "TEST/USDT", "interval": "1h",
        "type": "Long", "entry_price": 100.0, "stop_loss": 90.0, "take_profit": 120.0,
        "exchange": "BINANCE"
    }
    db_manager.log_trade(trade_data)

    mock_df = pd.DataFrame({
        'Open': [92], 'High': [95], 'Low': [89], 'Close': [91]
    }, index=[pd.to_datetime(datetime.now())])

    async def mock_fetch_ohlcv(*args, **kwargs): return mock_df
    monkeypatch.setattr(analyzer.exchange_service, 'fetch_ohlcv', mock_fetch_ohlcv)
    
    await paper_trader.check_pending_trades()

    trades = db_manager.get_all_trades(filters={'symbol': 'TEST/USDT'})
    assert len(trades) == 1
    assert trades[0]['result'] == 'ANULOWANY'

@pytest.mark.asyncio
async def test_pending_trade_expires_if_not_triggered(db_manager, monkeypatch):
    settings_manager = SettingsManager()
    analyzer = TechnicalAnalyzer(settings_manager, db_manager, None)
    paper_trader = PaperTrader(db_manager, analyzer, asyncio.Lock())
    paper_trader.expiration_limit = 5

    trade_timestamp = time.time() - (6 * 3600)
    trade_data = {
        "timestamp": trade_timestamp, "symbol": "EXPIRE/USDT", "interval": "1h",
        "type": "Long", "entry_price": 100.0, "stop_loss": 90.0, "take_profit": 120.0,
        "exchange": "BINANCE"
    }
    db_manager.log_trade(trade_data)

    # POPRAWKA: Dodajemy wszystkie potrzebne kolumny OHLC
    mock_df = pd.DataFrame({
        'Open': [105, 106, 105, 107, 106, 108],
        'High': [106, 107, 106, 108, 107, 109],
        'Low': [104, 105, 104, 106, 105, 107],
        'Close': [105, 106, 105, 107, 106, 108]
    }, index=pd.to_datetime([datetime.fromtimestamp(trade_timestamp + (i+1)*3600) for i in range(6)]))
    
    async def mock_fetch_ohlcv(*args, **kwargs): return mock_df
    monkeypatch.setattr(analyzer.exchange_service, 'fetch_ohlcv', mock_fetch_ohlcv)
    
    await paper_trader.check_pending_trades()

    trades = db_manager.get_all_trades(filters={'symbol': 'EXPIRE/USDT'})
    assert len(trades) == 1
    assert trades[0]['result'] == 'WYGASŁY'

@pytest.mark.asyncio
async def test_pending_short_trade_is_activated(db_manager, monkeypatch):
    settings_manager = SettingsManager()
    analyzer = TechnicalAnalyzer(settings_manager, db_manager, None)
    paper_trader = PaperTrader(db_manager, analyzer, asyncio.Lock())
    
    # POPRAWKA: Dane muszą pasować do tego, co testujemy (Short)
    trade_data = {
        "timestamp": time.time(), "symbol": "SHORT_TEST/USDT", "interval": "1h",
        "type": "Short", "entry_price": 100.0, "stop_loss": 110.0, "take_profit": 90.0,
        "exchange": "BINANCE"
    }
    db_manager.log_trade(trade_data)

    mock_df = pd.DataFrame({
        'Open': [98], 'High': [101], 'Low': [97], 'Close': [99]
    }, index=[pd.to_datetime(datetime.now())])
    
    async def mock_fetch_ohlcv(*args, **kwargs): return mock_df
    monkeypatch.setattr(analyzer.exchange_service, 'fetch_ohlcv', mock_fetch_ohlcv)
    
    await paper_trader.check_pending_trades()
    
    # POPRAWKA: Szukamy poprawnego symbolu
    trades = db_manager.get_all_trades(filters={'symbol': 'SHORT_TEST/USDT'})
    assert len(trades) == 1
    assert trades[0]['is_active'] == 1

@pytest.mark.asyncio
async def test_partially_closed_trade_is_marked_as_break_even(db_manager, monkeypatch):
    settings_manager = SettingsManager()
    analyzer = TechnicalAnalyzer(settings_manager, db_manager, None)
    paper_trader = PaperTrader(db_manager, analyzer, asyncio.Lock())
    
    # POPRAWKA: Symbol musi być unikalny dla tego testu
    symbol = "BE_TEST/USDT"
    entry_price = 100.0
    tp1_price = 105.0
    initial_sl = 95.0
    
    trade_data = {
        "timestamp": time.time(), "symbol": symbol, "interval": "1h",
        "type": "Long", "entry_price": entry_price, "stop_loss": initial_sl,
        "take_profit_1": tp1_price, "take_profit": 110.0, "exchange": "BINANCE"
    }
    db_manager.log_trade(trade_data)
    trade_id = db_manager.get_all_trades(filters={'symbol': symbol})[0]['id']

    candles_data = {
        'Open': [99, 102, 100], 'High': [101, 106, 101],
        'Low': [98, 99, 99], 'Close': [100, 104, 100]
    }
    index = pd.to_datetime([datetime.now() + timedelta(hours=i+1) for i in range(3)])
    mock_df = pd.DataFrame(candles_data, index=index)
    
    async def mock_fetch_ohlcv(*args, **kwargs): return mock_df
    monkeypatch.setattr(analyzer.exchange_service, 'fetch_ohlcv', mock_fetch_ohlcv)
    
    # Symulujemy kolejne cykle sprawdzania
    await paper_trader.check_pending_trades() # Aktywacja
    await paper_trader.check_pending_trades() # Trafienie w TP1 i BE
    await paper_trader.check_pending_trades() # Trafienie w SL na BE
    
    final_trade_state = db_manager.get_trade_by_id(trade_id)
    assert final_trade_state is not None
    assert final_trade_state['result'] == 'BREAK_EVEN'
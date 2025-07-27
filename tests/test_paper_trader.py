import pandas as pd
import pytest
import time
import asyncio
from datetime import datetime, timedelta

from core.paper_trader import PaperTrader
from core.analyzer import TechnicalAnalyzer
from core.settings_manager import SettingsManager

@pytest.mark.asyncio
async def test_pending_trade_is_invalidated_when_sl_hits_first(db_manager, monkeypatch):
    """
    Testuje scenariusz, w którym SL jest osiągany ZANIM cena wejścia zostanie aktywowana.
    Oczekiwany wynik: transakcja oznaczona jako 'ANULOWANY'.
    """
    # 1. Skonfiguruj (Arrange)
    settings_manager = SettingsManager()
    analyzer = TechnicalAnalyzer(settings_manager, db_manager)
    lock = asyncio.Lock()
    paper_trader = PaperTrader(db_manager, analyzer, lock)

    # Zapisujemy do testowej bazy danych setup, który będziemy monitorować
    trade_data = {
        "timestamp": time.time(), "symbol": "TEST/USDT", "interval": "1h",
        "type": "Long", "entry_price": 100.0, "stop_loss": 90.0, "take_profit": 120.0
    }
    db_manager.log_trade(trade_data)

    # Przygotowujemy fałszywe dane OHLCV, które symulują uderzenie w SL PRZED wejściem
    # Szczyt świecy (High) jest poniżej ceny wejścia, ale dołek (Low) jest poniżej SL
    mock_ohlcv_data = {
        'Open': [92], 'High': [95], 'Low': [89], 'Close': [91]
    }
    mock_df = pd.DataFrame(mock_ohlcv_data, index=[pd.to_datetime(datetime.now())])

    # Mockujemy funkcję pobierającą dane z giełdy
    async def mock_fetch_ohlcv(*args, **kwargs):
        return mock_df
    
    monkeypatch.setattr(analyzer.exchange_service, 'fetch_ohlcv', mock_fetch_ohlcv)

    # 2. Stymuluj (Act)
    # Uruchamiamy logikę PaperTradera, która powinna znaleźć i przetworzyć nasz setup
    await paper_trader.check_pending_trades()

    # 3. Sprawdź (Assert)
    # Pobieramy transakcję z bazy i sprawdzamy jej status
    trades = db_manager.get_all_trades(filters={'symbol': 'TEST/USDT'})
    assert len(trades) == 1
    assert trades[0]['result'] == 'ANULOWANY'

@pytest.mark.asyncio
async def test_pending_trade_expires_if_not_triggered(db_manager, monkeypatch):
    """
    Testuje scenariusz, w którym setup nie zostaje aktywowany przez zadaną liczbę świec.
    Oczekiwany wynik: transakcja oznaczona jako 'WYGASŁY'.
    """
    # 1. Skonfiguruj (Arrange)
    settings_manager = SettingsManager()
    analyzer = TechnicalAnalyzer(settings_manager, db_manager)
    lock = asyncio.Lock()
    paper_trader = PaperTrader(db_manager, analyzer, lock)
    # Ustawiamy krótki limit wygaśnięcia na potrzeby testu
    paper_trader.expiration_limit = 5 

    # Zapisujemy setup, który został utworzony "dawno temu" (6 świec temu)
    trade_timestamp = time.time() - (6 * 3600) # 6 godzin temu dla interwału 1h
    trade_data = {
        "timestamp": trade_timestamp, "symbol": "EXPIRE/USDT", "interval": "1h",
        "type": "Long", "entry_price": 100.0, "stop_loss": 90.0, "take_profit": 120.0
    }
    db_manager.log_trade(trade_data)

    # Przygotowujemy dane OHLCV, które zawierają 6 świec PO utworzeniu setupu
    # Ceny w tych świecach nigdy nie osiągają ani wejścia, ani SL
    mock_ohlcv_data = {
        'Close': [105, 106, 105, 107, 106, 108]
    }
    index = pd.to_datetime([datetime.fromtimestamp(trade_timestamp + (i+1)*3600) for i in range(6)])
    mock_df = pd.DataFrame(mock_ohlcv_data, index=index)
    
    # Mockujemy funkcję pobierającą dane z giełdy
    async def mock_fetch_ohlcv(*args, **kwargs):
        return mock_df

    monkeypatch.setattr(analyzer.exchange_service, 'fetch_ohlcv', mock_fetch_ohlcv)

    # 2. Stymuluj (Act)
    await paper_trader.check_pending_trades()

    # 3. Sprawdź (Assert)
    trades = db_manager.get_all_trades(filters={'symbol': 'EXPIRE/USDT'})
    assert len(trades) == 1
    assert trades[0]['result'] == 'WYGASŁY'

@pytest.mark.asyncio
async def test_pending_short_trade_is_activated(db_manager, monkeypatch):
        """
        Testuje, czy oczekujący setup typu Short jest poprawnie aktywowany,
        gdy cena osiągnie poziom wejścia.
        """
        # 1. Skonfiguruj (Arrange)
        settings_manager = SettingsManager()
        analyzer = TechnicalAnalyzer(settings_manager, db_manager)
        lock = asyncio.Lock()
        paper_trader = PaperTrader(db_manager, analyzer, lock)

        # Zapisujemy do bazy setup typu Short
        trade_data = {
            "timestamp": time.time(), "symbol": "SHORT/USDT", "interval": "1h",
            "type": "Short", "entry_price": 100.0, "stop_loss": 110.0, "take_profit": 90.0
        }
        db_manager.log_trade(trade_data)

        # Przygotowujemy dane OHLCV, które symulują dotknięcie ceny wejścia od dołu
        # Szczyt świecy (High) jest powyżej ceny wejścia
        mock_ohlcv_data = {
            'Open': [98], 'High': [101], 'Low': [97], 'Close': [99]
        }
        mock_df = pd.DataFrame(mock_ohlcv_data, index=[pd.to_datetime(datetime.now())])

        # Mockujemy funkcję pobierającą dane
        async def mock_fetch_ohlcv(*args, **kwargs):
            # Dla tego testu wystarczy zwrócić ostatnią świecę
            return mock_df

        monkeypatch.setattr(analyzer.exchange_service, 'fetch_ohlcv', mock_fetch_ohlcv)
        
        # 2. Stymuluj (Act)
        await paper_trader.check_pending_trades()

        # 3. Sprawdź (Assert)
        trades = db_manager.get_all_trades(filters={'symbol': 'SHORT/USDT'})
        assert len(trades) == 1
        
        # Najważniejsze: sprawdzamy, czy transakcja została oznaczona jako aktywna
        assert trades[0]['is_active'] == 1
        # Wynik wciąż powinien być 'PENDING', bo nie trafiliśmy ani w SL, ani w TP
        assert trades[0]['result'] == 'PENDING'
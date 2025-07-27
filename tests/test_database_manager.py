import time

def test_log_and_get_trade(db_manager):
    """
    Testuje podstawowy cykl: zapisanie setupu do dziennika i jego odczytanie.
    Zwróć uwagę, że 'db_manager' to nazwa naszej fixtury z conftest.py!
    """
    # 1. Skonfiguruj (Arrange)
    trade_data = {
        "timestamp": time.time(),
        "symbol": "TEST/USDT",
        "interval": "1h",
        "type": "Long",
        "confidence": 8,
        "market_regime": "RYNEK_BYKA",
        "momentum_status": "SILNY_TREND",
        "entry_price": 100.0,
        "stop_loss": 90.0,
        "take_profit": 120.0,
        "exchange": "BINANCE"
    }

    # 2. Stymuluj (Act)
    # Zapisujemy transakcję do tymczasowej bazy danych
    db_manager.log_trade(trade_data)
    # Pobieramy wszystkie transakcje dla naszego symbolu
    retrieved_trades = db_manager.get_all_trades(filters={'symbol': 'TEST/USDT'})

    # 3. Sprawdź (Assert)
    assert retrieved_trades is not None
    assert len(retrieved_trades) == 1

    retrieved_trade = retrieved_trades[0]
    assert retrieved_trade['symbol'] == "TEST/USDT"
    assert retrieved_trade['type'] == "Long"
    assert retrieved_trade['confidence'] == 8
    assert retrieved_trade['entry_price'] == 100.0
    assert retrieved_trade['result'] == 'PENDING' # Sprawdzamy domyślny status
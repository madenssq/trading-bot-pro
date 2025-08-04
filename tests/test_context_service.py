import pytest
import pandas as pd
from datetime import datetime

from core.context_service import ContextService
from core.settings_manager import SettingsManager
from core.exchange_service import ExchangeService
from core.indicator_service import IndicatorService
from core.database_manager import DatabaseManager
from core.analyzer import TechnicalAnalyzer

@pytest.fixture
def context_service(db_manager):
    """Fixtura tworząca instancję ContextService na potrzeby testów."""
    settings_manager = SettingsManager()
    # Tworzymy pełen obiekt Analyzer, ponieważ jest on potrzebny w IndicatorService
    analyzer = TechnicalAnalyzer(settings_manager, db_manager, None)
    exchange_service = analyzer._exchange_service
    indicator_service = analyzer._indicator_service
    return ContextService(settings_manager, exchange_service, indicator_service, db_manager)

def create_mock_df(trend_type: str) -> pd.DataFrame:
    """Tworzy fałszywy DataFrame symulujący określony trend."""
    base_price = 100
    if trend_type == "BULL":
        prices = [base_price + i for i in range(60)] # Trend wzrostowy
    elif trend_type == "BEAR":
        prices = [base_price - i for i in range(60)] # Trend spadkowy
    else: # CONSOLIDATION
        prices = [base_price + (i % 5) for i in range(60)] # Konsolidacja

    index = pd.to_datetime([datetime(2025, 1, 1) + pd.Timedelta(days=i) for i in range(60)])
    return pd.DataFrame({
        'Open': prices, 'High': [p + 2 for p in prices],
        'Low': [p - 2 for p in prices], 'Close': prices, 'Volume': [1000] * 60
    }, index=index)

@pytest.mark.asyncio
@pytest.mark.parametrize("btc_trend, eth_trend, expected_regime", [
    ("BULL", "BULL", "RYNEK_BYKA"),
    ("BEAR", "BEAR", "RYNEK_NIEDZWIEDZIA"),
    ("BULL", "BEAR", "KONSOLIDACJA"),
    ("CONSOLIDATION", "BULL", "KONSOLIDACJA"),
    ("CONSOLIDATION", "CONSOLIDATION", "KONSOLIDACJA"),
])
async def test_get_market_regime(context_service, monkeypatch, btc_trend, eth_trend, expected_regime):
    """Testuje, czy logika scoringu dla reżimu rynkowego działa poprawnie."""
    # 1. Arrange
    mock_btc_df = create_mock_df(btc_trend)
    mock_eth_df = create_mock_df(eth_trend)

    async def mock_fetch_ohlcv(exchange, symbol, *args, **kwargs):
        if symbol == 'BTC/USDT':
            return mock_btc_df
        elif symbol == 'ETH/USDT':
            return mock_eth_df
        return None
        
    monkeypatch.setattr(context_service.exchange_service, 'fetch_ohlcv', mock_fetch_ohlcv)

    # 2. Act
    result = await context_service.get_market_regime()

    # 3. Assert
    assert result == expected_regime

@pytest.mark.asyncio
@pytest.mark.parametrize("order_book_data, trades_data, expected_result", [
    # Scenariusz 1: Silna presja kupujących (dużo w bidach, dużo kupujących)
    (
        {'bids': [[100, 20]], 'asks': [[101, 5]]}, # 4x więcej w bidach
        [{'side': 'buy', 'cost': 1500}, {'side': 'sell', 'cost': 500}], # 3x więcej kupna
        "SILNA_PRESJA_KUPUJĄCYCH"
    ),
    # Scenariusz 2: Silna presja sprzedających (dużo w askach, dużo sprzedających)
    (
        {'bids': [[100, 5]], 'asks': [[101, 20]]}, # 4x więcej w askach
        [{'side': 'buy', 'cost': 500}, {'side': 'sell', 'cost': 1500}], # 3x więcej sprzedaży
        "SILNA_PRESJA_SPRZEDAJĄCYCH"
    ),
    # Scenariusz 3: Brak dominacji (arkusz i transakcje zrównoważone)
    (
        {'bids': [[100, 10]], 'asks': [[101, 11]]}, # Równowaga
        [{'side': 'buy', 'cost': 1000}, {'side': 'sell', 'cost': 1100}], # Równowaga
        "BRAK_DOMINACJI"
    ),
])
async def test_analyze_order_flow_strength(context_service, monkeypatch, order_book_data, trades_data, expected_result):
    """Testuje logikę scoringu dla analizy przepływu zleceń (order flow)."""
    # 1. Arrange
    # Mockujemy metody ccxt, które są wywoływane wewnątrz serwisu
    async def mock_fetch_l2_order_book(*args, **kwargs):
        return order_book_data
        
    async def mock_fetch_trades(*args, **kwargs):
        return trades_data

    # Tworzymy fałszywą instancję giełdy i podmieniamy jej metody
    mock_exchange = await context_service.exchange_service.get_exchange_instance("BINANCE")
    monkeypatch.setattr(mock_exchange, 'fetch_l2_order_book', mock_fetch_l2_order_book)
    monkeypatch.setattr(mock_exchange, 'fetch_trades', mock_fetch_trades)

    # 2. Act
    result = await context_service.analyze_order_flow_strength("TEST/USDT", "BINANCE")

    # 3. Assert
    assert result == expected_result
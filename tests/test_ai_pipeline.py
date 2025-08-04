import pytest
import pandas as pd
import json
import asyncio
from datetime import datetime

# Importujemy wszystkie potrzebne komponenty
from core.ai_pipeline import AIPipeline
from core.analyzer import TechnicalAnalyzer
from core.ai_client import AIClient
from core.database_manager import DatabaseManager
from core.settings_manager import SettingsManager
from core.performance_analyzer import PerformanceAnalyzer

@pytest.mark.asyncio
async def test_pipeline_produces_and_logs_valid_long_trade(db_manager, monkeypatch):
    """
    Testuje pełen przepływ AIPipeline dla scenariusza "Long".
    Sprawdza, czy po serii zamockowanych odpowiedzi AI, w bazie danych
    pojawi się poprawnie skonstruowana i zwalidowana transakcja.
    """
    # --- 1. ARRANGE (Skonfiguruj) ---

    # Inicjalizacja wszystkich potrzebnych serwisów
    settings_manager = SettingsManager()
    ai_client = AIClient(settings_manager)
    analyzer = TechnicalAnalyzer(settings_manager, db_manager, ai_client)
    performance_analyzer = PerformanceAnalyzer(db_manager)
    pipeline = AIPipeline(analyzer, ai_client, db_manager, performance_analyzer)

    # Przygotowanie fałszywych danych rynkowych
    mock_df = pd.DataFrame({
        'Open': [98], 'High': [101], 'Low': [97], 'Close': [100.0]
    }, index=[pd.to_datetime(datetime.now())])

    # Przygotowanie sekwencji fałszywych odpowiedzi od AI, symulujących każdy krok pipeline'u
    ai_responses = [
        # 1. Odpowiedź Agenta Obserwatora (wybiera interwał)
        "1h", 
        # 2. Odpowiedź Adwokata Diabła (daje kontrargument)
        "Rynek może być wykupiony.",
        # 3. Odpowiedź Taktyka (ustala BIAS)
        '```json\n{"key_conclusions": "Struktura jest wzrostowa.", "bias": "Bullish", "key_level": 100.5, "confidence": 8}\n```',
        # 4. Odpowiedź Recenzenta TP (ocenia poziomy S/R)
        '```json\n{"105.0": 8, "110.0": 9, "115.0": 7}\n```'
    ]
    
    # Mockowanie funkcji pobierającej dane z giełdy
    async def mock_fetch_ohlcv(*args, **kwargs):
        return mock_df
    monkeypatch.setattr(analyzer._exchange_service, 'fetch_ohlcv', mock_fetch_ohlcv)

    # Mockowanie funkcji pobierającej S/R (aby zwróciła kandydatów na TP)
    def mock_find_sr(*args, **kwargs):
        return {"support": [95.0], "resistance": [105.0, 110.0, 115.0]}
    monkeypatch.setattr(analyzer._pattern_service, 'find_programmatic_sr_levels', mock_find_sr)

    # Mockowanie kluczowej funkcji - odpowiedzi od AI
    call_count = 0
    async def mock_get_completion(*args, **kwargs):
        nonlocal call_count
        response = ai_responses[call_count]
        call_count += 1
        return response
    monkeypatch.setattr(ai_client, 'get_chat_completion_async', mock_get_completion)

    # --- 2. ACT (Stymuluj) ---
    
    # Uruchamiamy cały pipeline
    await pipeline.run(
        symbol="TEST/USDT", 
        interval="1h", 
        exchange="BINANCE", 
        status_callback=lambda text, busy: None # pusta funkcja na potrzeby testu
    )

    # --- 3. ASSERT (Sprawdź) ---

    # Sprawdzamy, czy w bazie danych pojawiła się oczekiwana transakcja
    trades = db_manager.get_all_trades(filters={'symbol': 'TEST/USDT'})
    
    assert len(trades) == 1
    trade = trades[0]

    assert trade['type'] == 'Long'
    assert trade['entry_price'] == 100.5 # Z odpowiedzi Taktyka
    assert trade['confidence'] == 8
    
    # Sprawdzamy, czy TP zostały poprawnie wybrane i obliczone
    # TP1 to najbliższy z kandydatów, TP2 to najdalszy
    assert trade['take_profit_1'] == 105.0
    assert trade['take_profit'] == 110.0 # Wybrany jako najlepszy (ocena 9), a 115 jest odrzucony jako zbyt daleki
    
    # Sprawdzamy, czy SL został poprawnie obliczony na podstawie ATR
    # (w tym teście mock_df nie ma ATR, więc SL będzie blisko wejścia)
    assert 'stop_loss' in trade
import pytest
import pandas as pd
from datetime import datetime

from core.pattern_service import PatternService
from core.settings_manager import SettingsManager
from core.indicator_service import IndicatorService
from core.analyzer import TechnicalAnalyzer
from core.database_manager import DatabaseManager

@pytest.fixture
def pattern_service(db_manager):
    """Fixtura tworząca instancję PatternService na potrzeby testów."""
    settings_manager = SettingsManager()
    analyzer = TechnicalAnalyzer(settings_manager, db_manager, None)
    # Pobieramy serwisy z już zainicjalizowanego analizatora
    indicator_service = analyzer._indicator_service
    exchange_service = analyzer._exchange_service
    return PatternService(settings_manager, indicator_service, exchange_service)

def test_find_programmatic_sr_levels(pattern_service):
    """
    Testuje, czy serwis poprawnie identyfikuje szczyty (opory) i dołki (wsparcia)
    na podstawie przygotowanego DataFrame.
    """
    # 1. Arrange
    # Tworzymy dane z wyraźnym dołkiem na 90 i szczytem na 110
    data = {
        'Open':  [100, 95, 91, 95, 100, 105, 110, 105, 101],
        'High':  [102, 98, 92, 96, 102, 108, 110, 108, 102],
        'Low':   [98,  92, 90, 91, 98,  102, 108, 104, 100],
        'Close': [101, 93, 91, 98, 101, 107, 109, 106, 101],
        'Volume':[100] * 9
    }
    index = pd.to_datetime([datetime(2025, 1, i+1) for i in range(9)])
    mock_df = pd.DataFrame(data, index=index)
    
    # Ustawiamy parametry skanera na bardzo czułe, aby na pewno znalazł nasze ekstrema
    pattern_service.settings.set('ssnedam.sr_scanner_prominence_multiplier', 0.1)
    pattern_service.settings.set('ssnedam.sr_scanner_distance', 2)

    # 2. Act
    result = pattern_service.find_programmatic_sr_levels(mock_df, pattern_service.indicator_service)

    # 3. Assert
    # Sprawdzamy, czy w wynikach znalazły się oczekiwane wartości
    assert 90.0 in result['support']
    assert 110.0 in result['resistance']
    
    # Sprawdzamy, czy poziomy są poprawnie posortowane
    assert result['support'] == sorted(result['support'], reverse=True)
    assert result['resistance'] == sorted(result['resistance'])

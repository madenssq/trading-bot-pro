import pandas as pd
import numpy as np
from core.indicator_service import IndicatorService
from core.settings_manager import SettingsManager

def test_calculate_all_adds_indicator_columns():
    """
    Testuje, czy serwis poprawnie oblicza i dodaje kolumny ze wskaźnikami.
    """
    # 1. Skonfiguruj (Arrange)
    # Tworzymy fałszywy SettingsManager, który dostarczy domyślne parametry
    settings_manager = SettingsManager() 
    # Dla tego testu nie potrzebujemy pełnego analizatora, więc przekazujemy None
    indicator_service = IndicatorService(settings_manager=settings_manager, analyzer=None)

    # Tworzymy przykładową ramkę danych OHLCV (wystarczająco długą dla wskaźników)
    data = {
        'Open': np.random.uniform(90, 100, size=50),
        'High': np.random.uniform(100, 110, size=50),
        'Low': np.random.uniform(80, 90, size=50),
        'Close': np.random.uniform(95, 105, size=50),
        'Volume': np.random.uniform(1000, 5000, size=50),
    }
    sample_df = pd.DataFrame(data)

    # 2. Stymuluj (Act)
    # Uruchamiamy główną metodę obliczającą
    result_df = indicator_service.calculate_all(sample_df)

    # 3. Sprawdź (Assert)
    # Sprawdzamy, czy w wyniku znajdują się kolumny, których oczekujemy
    assert 'RSI_14' in result_df.columns
    assert 'EMA_50' in result_df.columns
    assert 'BBL_20_2.0' in result_df.columns # Dolna wstęga Bollingera
    assert 'MACD_12_26_9' in result_df.columns
    
    # Sprawdzamy, czy wskaźniki faktycznie zostały obliczone (nie są puste)
    assert not result_df['RSI_14'].isnull().all()
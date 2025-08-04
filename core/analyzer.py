import pandas as pd
import numpy as np
import asyncio
import logging
import json
from typing import Dict, Any, Tuple, Optional, List
from dataclasses import dataclass, field

from core.settings_manager import SettingsManager
from core.database_manager import DatabaseManager
from core.exchange_service import ExchangeService
from core.indicator_service import IndicatorService
from core.pattern_service import PatternService
from core.context_service import ContextService
from core.ai_client import AIClient, ParsedAIResponse
from core.data_models import ContextData
import ccxt.async_support as ccxt
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class AnalysisResult:
    """Przechowuje kompletny wynik analizy technicznej."""
    exchange_id: Optional[str] = None
    current_price: Optional[float] = None
    all_timeframe_data: Dict = field(default_factory=dict)
    main_df_with_indicators: Optional[pd.DataFrame] = None
    all_ohlcv_dfs: Dict[str, pd.DataFrame] = field(default_factory=dict)
    fvgs: List[Dict[str, float]] = field(default_factory=list)
    is_successful: bool = False

class TechnicalAnalyzer:
    """Orkiestruje zaawansowaną analizą techniczną, delegując zadania do wyspecjalizowanych serwisów."""
    
    def __init__(self, settings_manager: SettingsManager, db_manager: DatabaseManager, ai_client: AIClient):
        self.settings = settings_manager
        self.db_manager = db_manager
        self.ai_client = ai_client
        
        # ZMIANA: Wszystkie serwisy są teraz "prywatne" (zaczynają się od _)
        # i nie powinny być wywoływane bezpośrednio spoza tej klasy.
        self._exchange_service = ExchangeService() 
        self._indicator_service = IndicatorService(settings_manager, self)
        self._pattern_service = PatternService(settings_manager, self._indicator_service, self._exchange_service)
        self._context_service = ContextService(settings_manager, self._exchange_service, self._indicator_service, db_manager)

    async def get_analysis_data(self, symbol: str, main_interval: str, exchange_id: str = "BINANCE") -> AnalysisResult:
        # ZMIANA: Używamy wewnętrznego serwisu
        exchange = await self._exchange_service.get_exchange_instance(exchange_id)
        if not exchange:
            return AnalysisResult(exchange_id=exchange_id)
        
        intervals_to_analyze = self.settings.get('analysis.multi_timeframe_intervals', ["1h", "4h", "1d"])
        
        intervals_to_fetch = list(set(intervals_to_analyze + [main_interval]))
        # ZMIANA: Używamy wewnętrznego serwisu
        tasks = {interval: self._exchange_service.fetch_ohlcv(exchange, symbol, interval) for interval in intervals_to_fetch}
        all_ohlcv_data = await asyncio.gather(*tasks.values(), return_exceptions=True)
        ohlcv_results = dict(zip(intervals_to_fetch, all_ohlcv_data))

        main_ohlcv_df = ohlcv_results.get(main_interval)
        if isinstance(main_ohlcv_df, Exception) or main_ohlcv_df is None or main_ohlcv_df.empty:
            logger.error(f"Nie udało się pobrać kluczowych danych dla {symbol} na interwale {main_interval}.")
            return AnalysisResult(exchange_id=exchange_id, all_ohlcv_dfs=ohlcv_results)

        current_price = main_ohlcv_df['Close'].iloc[-1]
        # ZMIANA: Używamy wewnętrznego serwisu
        main_df_with_indicators = self._indicator_service.calculate_all(main_ohlcv_df.copy())
        found_fvgs = self._pattern_service.find_fair_value_gaps(main_df_with_indicators)

        all_timeframe_data = {}
        for interval in intervals_to_analyze: 
            df = ohlcv_results.get(interval)
            if not isinstance(df, pd.DataFrame) or df.empty or len(df) < 2:
                continue
            
            # ZMIANA: Używamy wewnętrznego serwisu
            indicators_df = self._indicator_service.calculate_all(df.copy())
            interpreted_data = self._indicator_service.interpret_all(indicators_df)
            all_timeframe_data[interval] = {"interpreted": interpreted_data}
        
        return AnalysisResult(
            exchange_id=exchange_id, current_price=current_price,
            all_timeframe_data=all_timeframe_data, main_df_with_indicators=main_df_with_indicators,
            all_ohlcv_dfs=ohlcv_results, fvgs=found_fvgs, is_successful=True
        )

    async def prepare_tactician_inputs(self, analysis_result: 'AnalysisResult', best_timeframe: str, symbol: str, exchange_id: str) -> dict:
        # ZMIANA: Metoda jest teraz asynchroniczna i przyjmuje 'exchange_id'
        inputs = {
            "fibonacci_data": "{}", "programmatic_sr_json": "{}", "volume_profile_json": "{}",
            "approach_momentum_status": "BRAK_DANYCH", "intermediate_trend": "BRAK_DANYCH"
        }
        best_df = analysis_result.all_ohlcv_dfs.get(best_timeframe)
        if best_df is not None and not best_df.empty:
            df_with_indicators = self._indicator_service.calculate_all(best_df.copy())
            
            inputs["approach_momentum_status"] = self._context_service.analyze_approach_momentum(df_with_indicators)
            inputs["intermediate_trend"] = self._context_service.get_intermediate_trend_status(df_with_indicators)
            
            # ZMIANA: Dodajemy 'await' i przekazujemy 'symbol' oraz 'exchange_id'
            sr_levels = await self._pattern_service.find_programmatic_sr_levels(df_with_indicators, symbol, exchange_id)
            inputs["programmatic_sr_json"] = json.dumps(sr_levels)
            
            inputs["volume_profile_json"] = json.dumps(self._pattern_service.get_volume_profile_levels(df_with_indicators))

        df_daily = analysis_result.all_ohlcv_dfs.get('1d')
        if df_daily is not None and not df_daily.empty:
            df_daily_with_indicators = self._indicator_service.calculate_all(df_daily.copy())
            fib_data = self._pattern_service.find_fibonacci_retracement(df_daily_with_indicators)
            inputs["fibonacci_data"] = json.dumps(fib_data)
        return inputs

    # --- NOWE METODY PUBLICZNE (FASADA) ---
    # Te metody tworzą czysty, publiczny interfejs dla innych klas.

    async def fetch_ohlcv(self, exchange: ccxt.Exchange, symbol: str, interval: str, limit: int = None, since: int = None) -> Optional[pd.DataFrame]:
        """Pobiera świece OHLCV z danej giełdy."""
        # Dodaj ten import na górze pliku: import pandas as pd
        return await self._exchange_service.fetch_ohlcv(exchange, symbol, interval, limit, since)

    async def get_exchange_instance(self, exchange_id: str) -> Optional[ccxt.Exchange]:
        """Pobiera lub tworzy instancję ccxt dla danej giełdy."""
        return await self._exchange_service.get_exchange_instance(exchange_id)
    

    async def get_long_short_ratio(self, symbol: str, exchange_id: str) -> Optional[float]:
        """Pobiera stosunek pozycji długich do krótkich dla danego symbolu."""
        return await self._context_service.get_long_short_ratio(symbol, exchange_id)


    def find_divergence(self, price_series: pd.Series, indicator_series: pd.Series) -> Optional[str]:
        """Sprawdza, czy występuje dywergencja między ceną a wskaźnikiem."""
        # Ten import może być potrzebny na górze pliku: from typing import Optional
        # Ten import może być potrzebny na górze pliku: import pandas as pd
        return self._pattern_service.find_divergence(price_series, indicator_series)
    
    def find_fair_value_gaps(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Znajduje i zwraca listę luk cenowych (Fair Value Gaps)."""
        return self._pattern_service.find_fair_value_gaps(df)

    async def get_full_context(self, symbol: str, exchange_id: str, df_with_indicators: pd.DataFrame) -> 'ContextData':
        """Zbiera wszystkie dane kontekstowe i zwraca je jako pojedynczy obiekt."""
        # Dodaj import na górze pliku analyzer.py: from core.data_models import ContextData
        return await self._context_service.get_full_context(symbol, exchange_id, df_with_indicators)

    async def get_simple_recommendation(self, symbol: str, exchange: str) -> str:
        """Pobiera prostą rekomendację (KUPUJ/SPRZEDAJ/NEUTRALNIE) dla dashboardu."""
        return await self._context_service.get_simple_recommendation(symbol, exchange)
    
    async def find_potential_setups(self, symbol: str, exchange: str, interval: str) -> List[Dict[str, Any]]:
        """Znajduje potencjalne setupy 'trap' dla skanera Ssnedam."""
        return await self._pattern_service.find_potential_setups(symbol, exchange, interval)

    async def get_daily_metrics(self, symbol: str, exchange: str) -> Dict[str, Any]:
        """Pobiera kluczowe metryki dzienne (ATR%, dystans od EMA200) dla dashboardu."""
        return await self._context_service.get_daily_metrics(symbol, exchange)

    async def get_relative_strength(self, symbol: str, exchange: str) -> Optional[float]:
        """Pobiera wskaźnik siły względnej w stosunku do BTC dla dashboardu."""
        return await self._context_service.get_relative_strength(symbol, exchange)

    async def get_short_squeeze_indicator(self, symbol: str, exchange: str) -> Optional[str]:
        """Pobiera wskaźnik potencjalnego short squeeze dla dashboardu."""
        return await self._context_service.get_short_squeeze_indicator(symbol, exchange)

    async def get_market_regime(self, exchange_id: str = "BINANCE") -> str:
        """Pobiera ogólny reżim rynkowy (RYNEK_BYKA/NIEDZWIEDZIA/KONSOLIDACJA)."""
        return await self._context_service.get_market_regime(exchange_id)

    async def analyze_order_flow_strength(self, symbol: str, exchange_id: str) -> str:
        """Analizuje siłę przepływu zleceń (order flow) dla danego symbolu."""
        return await self._context_service.analyze_order_flow_strength(symbol, exchange_id)

    async def get_market_momentum_status(self, symbol: str, exchange: str) -> str:
        """Pobiera status pędu rynkowego (np. SILNY_TREND, RYZYKO_PRZEGRZANIA)."""
        return await self._context_service.get_market_momentum_status(symbol, exchange)

    def get_mean_reversion_status(self, df_with_indicators: pd.DataFrame) -> str:
        """Analizuje potencjał do powrotu do średniej na podstawie RSI."""
        return self._context_service.get_mean_reversion_status(df_with_indicators)

    def calculate_all_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Oblicza pełen zestaw wskaźników technicznych dla danego DataFrame."""
        return self._indicator_service.calculate_all(df)

    
    async def find_programmatic_sr_levels(self, df: pd.DataFrame, symbol: str, exchange_id: str) -> dict:
        # ZMIANA: Metoda jest teraz asynchroniczna i przyjmuje 'symbol' oraz 'exchange_id'
        """Znajduje programistycznie poziomy S/R."""
        return await self._pattern_service.find_programmatic_sr_levels(df, symbol, exchange_id)
    
    async def close_all_exchanges(self):
        """Deleguje zadanie zamknięcia wszystkich połączeń do serwisu giełd."""
        await self._exchange_service.close_all_exchanges()
        
    # --- Pozostałe metody pomocnicze (bez zmian) ---

    def _round_price_for_ai(self, price: float) -> float:
        if price > 1000: return round(price) 
        elif price > 10: return round(price, 2)
        elif price > 0.1: return round(price, 4)
        else: return round(price, 8)

    

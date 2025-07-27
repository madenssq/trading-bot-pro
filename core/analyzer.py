import pandas as pd
import numpy as np
import asyncio
import logging
import json
from typing import Dict, Any, Tuple, Optional, List
from dataclasses import dataclass, field

# Potrzebujemy tylko importów do klas, których używamy
from core.settings_manager import SettingsManager
from core.database_manager import DatabaseManager
from core.exchange_service import ExchangeService
from core.indicator_service import IndicatorService
from core.pattern_service import PatternService
from core.context_service import ContextService

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
    
    def __init__(self, settings_manager: SettingsManager, db_manager: DatabaseManager):
        self.settings = settings_manager
        self.db_manager = db_manager
        self.exchange_service = ExchangeService() 
        self.indicator_service = IndicatorService(settings_manager, self)
        self.pattern_service = PatternService(settings_manager, self.indicator_service, self.exchange_service)
        self.context_service = ContextService(settings_manager, self.exchange_service, self.indicator_service, db_manager)
        self.intervals_to_analyze: List[str] = self.settings.get('analysis.multi_timeframe_intervals', ["1h", "4h", "1d"])

    # --- GŁÓWNE METODY ORKIESTRUJĄCE ---

    async def get_analysis_data(self, symbol: str, main_interval: str, exchange_id: str = "BINANCE") -> AnalysisResult:
        exchange = await self.exchange_service.get_exchange_instance(exchange_id)
        if not exchange:
            return AnalysisResult(exchange_id=exchange_id)

        intervals_to_fetch = list(set(self.intervals_to_analyze + [main_interval]))
        tasks = {interval: self.exchange_service.fetch_ohlcv(exchange, symbol, interval) for interval in intervals_to_fetch}
        all_ohlcv_data = await asyncio.gather(*tasks.values(), return_exceptions=True)
        ohlcv_results = dict(zip(intervals_to_fetch, all_ohlcv_data))

        main_ohlcv_df = ohlcv_results.get(main_interval)
        if isinstance(main_ohlcv_df, Exception) or main_ohlcv_df is None or main_ohlcv_df.empty:
            logger.error(f"Nie udało się pobrać kluczowych danych dla {symbol} na interwale {main_interval}.")
            return AnalysisResult(exchange_id=exchange_id, all_ohlcv_dfs=ohlcv_results)

        current_price = main_ohlcv_df['Close'].iloc[-1]
        main_df_with_indicators = self.indicator_service.calculate_all(main_ohlcv_df.copy())
        found_fvgs = self.pattern_service.find_fair_value_gaps(main_df_with_indicators)

        all_timeframe_data = {}
        for interval in self.intervals_to_analyze:
            df = ohlcv_results.get(interval)
            if not isinstance(df, pd.DataFrame) or df.empty or len(df) < 2:
                continue
            
            indicators_df = self.indicator_service.calculate_all(df.copy())
            interpreted_data = self.indicator_service.interpret_all(indicators_df)
            all_timeframe_data[interval] = {"interpreted": interpreted_data}
        
        return AnalysisResult(
            exchange_id=exchange_id, current_price=current_price,
            all_timeframe_data=all_timeframe_data, main_df_with_indicators=main_df_with_indicators,
            all_ohlcv_dfs=ohlcv_results, fvgs=found_fvgs, is_successful=True
        )

    def prepare_tactician_inputs(self, analysis_result: 'AnalysisResult', best_timeframe: str, symbol: str) -> dict:
        inputs = {
            "fibonacci_data": "{}", "programmatic_sr_json": "{}", "volume_profile_json": "{}",
            "approach_momentum_status": "BRAK_DANYCH", "intermediate_trend": "BRAK_DANYCH"
        }
        best_df = analysis_result.all_ohlcv_dfs.get(best_timeframe)
        if best_df is not None and not best_df.empty:
            df_with_indicators = self.indicator_service.calculate_all(best_df.copy())
            
            inputs["approach_momentum_status"] = self.context_service.analyze_approach_momentum(df_with_indicators)
            inputs["intermediate_trend"] = self.context_service.get_intermediate_trend_status(df_with_indicators)
            inputs["programmatic_sr_json"] = json.dumps(self.pattern_service.find_programmatic_sr_levels(df_with_indicators, self.indicator_service))
            inputs["volume_profile_json"] = json.dumps(self.pattern_service.get_volume_profile_levels(df_with_indicators))

        df_daily = analysis_result.all_ohlcv_dfs.get('1d')
        if df_daily is not None and not df_daily.empty:
            df_daily_with_indicators = self.indicator_service.calculate_all(df_daily.copy())
            fib_data = self.pattern_service.find_fibonacci_retracement(df_daily_with_indicators)
            inputs["fibonacci_data"] = json.dumps(fib_data)
        return inputs

    # --- PROSTE METODY POMOCNICZE, KTÓRE ZOSTAJĄ ---

    def _round_price_for_ai(self, price: float) -> float:
        if price > 1000: return round(price) 
        elif price > 10: return round(price, 2)
        elif price > 0.1: return round(price, 4)
        else: return round(price, 8)

    def _format_data_for_prompt(self, data_dict: Dict) -> str:
        parts = []
        for interval, data in data_dict.items():
            if not data or not data.get('interpreted'): continue
            parts.append(f"\n# Interwał: {interval}")
            for key, value in data['interpreted'].items():
                parts.append(f"- {key}: {value.get('text', 'Brak danych') if isinstance(value, dict) else value}")
        return "\n".join(parts) if parts else "Brak danych technicznych."

    def _format_golden_examples_for_prompt(self, examples: List[Dict]) -> str:
        if not examples: return "Brak historycznych przykładów do pokazania."
        formatted_examples = []
        for i, ex in enumerate(examples):
            input_conditions = [f"- Symbol: {ex.get('symbol')}", f"- Reżim Rynkowy: {ex.get('market_regime')}"]
            output_trade = [f"- Typ: {ex.get('type')}", f"- Wejście: {ex.get('entry_price')}"]
            example_str = (f"--- PRZYKŁAD #{i+1} ---\nWARUNKI:\n" + "\n".join(input_conditions) + "\n\nSETUP:\n" + "\n".join(output_trade))
            formatted_examples.append(example_str)
        return "\n\n".join(formatted_examples)
import pandas as pd
import re
from typing import Dict, Any, Optional, List
from scipy.signal import find_peaks

from core.utils import suppress_stdout
from core.settings_manager import SettingsManager
from core.indicator_service import IndicatorService
from core.exchange_service import ExchangeService

class PatternService:
    """Odpowiada za wyszukiwanie formacji i wzorców na wykresie."""

    def __init__(self, settings_manager: SettingsManager, indicator_service: IndicatorService, exchange_service: ExchangeService):
        self.settings = settings_manager
        self.indicator_service = indicator_service
        self.exchange_service = exchange_service

    async def find_potential_setups(self, symbol: str, exchange: str, interval: str) -> List[Dict[str, Any]]:
        exchange_instance = await self.exchange_service.get_exchange_instance(exchange)
        if not exchange_instance: return []

        df = await self.exchange_service.fetch_ohlcv(exchange_instance, symbol, interval)
        if df is None or df.empty or len(df) < 20: return []
        
        params = self.settings.get('ssnedam', {})
        prominence_val = df['High'].std() * params.get('scanner_prominence', 0.5)
        distance_val = params.get('scanner_distance', 10)

        high_peaks, _ = find_peaks(df['High'], distance=distance_val, prominence=prominence_val)
        low_peaks, _ = find_peaks(-df['Low'], distance=distance_val, prominence=prominence_val)
        
        if not high_peaks.any() and not low_peaks.any(): return []

        found_setups = []
        for peak_idx in high_peaks[-3:]:
            resistance_level = df['High'].iloc[peak_idx]
            if self._find_recent_breakout_and_reclaim(df, level=resistance_level, is_resistance=True):
                reason = f"Wykryto potencjalną pułapkę na byki (Bull Trap) na oporze ${resistance_level:,.4f}."
                found_setups.append({'type': 'Potencjalny Short', 'interval': interval, 'details': reason})
        
        for peak_idx in low_peaks[-3:]:
            support_level = df['Low'].iloc[peak_idx]
            if self._find_recent_breakout_and_reclaim(df, level=support_level, is_resistance=False):
                reason = f"Wykryto potencjalną pułapkę na niedźwiedzie (Bear Trap) na wsparciu ${support_level:,.4f}."
                found_setups.append({'type': 'Potencjalny Long', 'interval': interval, 'details': reason})
        return found_setups

    def _find_recent_breakout_and_reclaim(self, df: pd.DataFrame, level: float, lookback: int = 5, is_resistance: bool = False) -> bool:
        if len(df) < lookback: return False
        recent_candles = df.iloc[-lookback:]
        crossed_level, reclaimed_level = False, False
        for i in range(len(recent_candles)):
            candle, prev_candle = recent_candles.iloc[i], recent_candles.iloc[i-1] if i > 0 else recent_candles.iloc[i]
            if is_resistance:
                if not crossed_level and candle['High'] > level and prev_candle['Close'] < level: crossed_level = True
                if crossed_level and candle['Close'] < level: reclaimed_level = True; break
            else:
                if not crossed_level and candle['Low'] < level and prev_candle['Close'] > level: crossed_level = True
                if crossed_level and candle['Close'] > level: reclaimed_level = True; break
        return crossed_level and reclaimed_level

    def find_divergence(self, price_series: pd.Series, indicator_series: pd.Series, lookback: int = 60, dist: int = 5) -> Optional[str]:
        if price_series.isna().all() or indicator_series.isna().all() or len(price_series) < lookback: return None
        try:
            price, indicator = price_series.tail(lookback).dropna(), indicator_series.tail(lookback).dropna()
            if price.empty or indicator.empty: return None
            price_peaks, _ = find_peaks(price, distance=dist)
            indicator_peaks, _ = find_peaks(indicator, distance=dist)
            if len(price_peaks) >= 2 and len(indicator_peaks) >= 2:
                if price.iloc[price_peaks[-1]] > price.iloc[price_peaks[-2]] and indicator.iloc[indicator_peaks[-1]] < indicator.iloc[indicator_peaks[-2]]:
                    return "Niedźwiedzia (wyższy szczyt ceny, niższy szczyt wskaźnika)"
            price_troughs, _ = find_peaks(-price, distance=dist)
            indicator_troughs, _ = find_peaks(-indicator, distance=dist)
            if len(price_troughs) >= 2 and len(indicator_troughs) >= 2:
                if price.iloc[price_troughs[-1]] < price.iloc[price_troughs[-2]] and indicator.iloc[indicator_troughs[-1]] > indicator.iloc[indicator_troughs[-2]]:
                    return "Bycza (niższy dołek ceny, wyższy dołek wskaźnika)"
        except Exception:
            pass # Błędy są logowane w nadrzędnej funkcji
        return None

    def format_candlestick_patterns(self, df: pd.DataFrame) -> Optional[str]:
        """Znajduje i formatuje nazwy rozpoznanych formacji świecowych."""
        recognized_patterns = []
        with suppress_stdout():
            try:
                patterns = df.ta.cdl_pattern(name="all")
                if isinstance(patterns, pd.DataFrame) and not patterns.empty:
                    last_patterns = patterns.iloc[-1]
                    found = last_patterns[last_patterns != 0]
                    if not found.empty:
                        for name in found.index:
                            clean_name = re.sub(r'(?<!^)(?=[A-Z])', ' ', name.replace('CDL_', '')).strip()
                            recognized_patterns.append(clean_name)
            except Exception:
                return None
        return ", ".join(recognized_patterns) if recognized_patterns else None

    def find_fair_value_gaps(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        if df is None or len(df) < 3: return []
        gaps = []
        avg_interval_seconds = df.index.to_series().diff().dt.total_seconds().median()
        fvg_width_in_seconds = avg_interval_seconds * 10

        for i in range(1, len(df) - 1):
            prev_candle, curr_candle, next_candle = df.iloc[i-1], df.iloc[i], df.iloc[i+1]
            gap_info = None
            if prev_candle['High'] < next_candle['Low']:
                gap_info = {'type': 'bullish', 'start_price': prev_candle['High'], 'end_price': next_candle['Low'], 'start_time': curr_candle.name.timestamp() - (avg_interval_seconds / 2), 'width_seconds': fvg_width_in_seconds}
            elif prev_candle['Low'] > next_candle['High']:
                gap_info = {'type': 'bearish', 'start_price': next_candle['High'], 'end_price': prev_candle['Low'], 'start_time': curr_candle.name.timestamp() - (avg_interval_seconds / 2), 'width_seconds': fvg_width_in_seconds}
            if gap_info:
                gaps.append(gap_info)
        return gaps

    def find_programmatic_sr_levels(self, df: pd.DataFrame, indicator_service) -> dict:
        """Automatycznie znajduje poziomy S/R."""
        if df.empty: return {"support": [], "resistance": []}
        supports, resistances = set(), set()
        last_price = df['Close'].iloc[-1]
        
        pivots = indicator_service._calculate_pivot_points(df)
        for key, val in pivots.items():
            (supports if val < last_price else resistances).add(round(val, 4))

        high_peaks, _ = find_peaks(df['High'], distance=10, prominence=df['High'].std() * 0.5)
        low_peaks, _ = find_peaks(-df['Low'], distance=10, prominence=df['Low'].std() * 0.5)
        for idx in high_peaks[-3:]: resistances.add(round(df['High'].iloc[idx], 4))
        for idx in low_peaks[-3:]: supports.add(round(df['Low'].iloc[idx], 4))

        for key in ['EMA_50', 'EMA_200']:
            ema_key = next((col for col in df.columns if key in col), None)
            if ema_key and ema_key in df.columns:
                val = df[ema_key].iloc[-1]
                (supports if val < last_price else resistances).add(round(val, 4))
        
        return {"support": sorted(list(supports), reverse=True), "resistance": sorted(list(resistances))}
        
    def find_fibonacci_retracement(self, df: pd.DataFrame, lookback: int = 100) -> Dict[str, Any]:
        """Znajduje ostatni swing i oblicza poziomy Fibonacciego."""
        if len(df) < lookback: return {}
        recent_df = df.iloc[-lookback:]
        high_point_val, low_point_val = recent_df['High'].max(), recent_df['Low'].min()
        high_point_idx, low_point_idx = recent_df['High'].idxmax(), recent_df['Low'].idxmin()

        if high_point_idx > low_point_idx:
            start_price, end_price, direction = low_point_val, high_point_val, "UP"
        else:
            start_price, end_price, direction = high_point_val, low_point_val, "DOWN"

        price_range = abs(end_price - start_price)
        if price_range == 0: return {}

        fib_levels = [0.236, 0.382, 0.5, 0.618, 0.786]
        retracement_levels = {}
        for level in fib_levels:
            retracement_levels[level] = end_price - price_range * level if direction == "UP" else end_price + price_range * level

        gp_start = retracement_levels[0.618]
        gp_end = end_price - price_range * 0.65 if direction == "UP" else end_price + price_range * 0.65
        
        return {"direction": direction, "start_price": start_price, "end_price": end_price, "levels": retracement_levels, "golden_pocket": {"start": min(gp_start, gp_end), "end": max(gp_start, gp_end)}}

    def get_volume_profile_levels(self, df: pd.DataFrame, bins: int = 50) -> Optional[Dict[str, float]]:
        """Oblicza kluczowe poziomy z profilu wolumenowego (POC, VAH, VAL)."""
        if df is None or df.empty or 'Volume' not in df.columns or 'Close' not in df.columns: return None
        try:
            volume_by_price = df.groupby(pd.cut(df['Close'], bins=bins, observed=False))['Volume'].sum()
            poc_level = volume_by_price.idxmax()
            poc = poc_level.mid
            
            value_area_bins = volume_by_price.sort_values(ascending=False)[volume_by_price.sort_values(ascending=False).cumsum() <= volume_by_price.sum() * 0.7]
            val, vah = value_area_bins.index.min().left, value_area_bins.index.max().right
            
            return {"poc": round(poc, 4), "vah": round(vah, 4), "val": round(val, 4)}
        except Exception:
            return None
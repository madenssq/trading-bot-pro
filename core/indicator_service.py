import pandas as pd
import pandas_ta as ta
import logging
import re
import os
import sys
from contextlib import contextmanager
from typing import Dict, Any, Optional, TYPE_CHECKING
from scipy.signal import find_peaks
from core.utils import suppress_stdout

from core.settings_manager import SettingsManager

if TYPE_CHECKING:
    from core.analyzer import TechnicalAnalyzer

logger = logging.getLogger(__name__)

# Przenosimy tutaj wszystkie powiązane klasy i funkcje
@contextmanager
def suppress_stdout():
    """Tymczasowo przekierowuje standardowe wyjście (print) do kosza."""
    with open(os.devnull, "w", encoding='utf-8') as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout

class IndicatorKeyGenerator:
    """Pomocnik do spójnego generowania nazw kolumn dla wskaźników."""
    def __init__(self, params: dict):
        self.p = params

    def rsi(self) -> str: return f"RSI_{self.p.get('rsi_length', 14)}"
    def ema(self, fast=True) -> str:
        length = self.p.get('ema_fast_length', 50) if fast else self.p.get('ema_slow_length', 200)
        return f"EMA_{length}"
    def macd(self) -> str: return f"MACD_{self.p.get('macd_fast',12)}_{self.p.get('macd_slow',26)}_{self.p.get('macd_signal',9)}"
    def macd_signal(self) -> str: return f"MACDs_{self.p.get('macd_fast',12)}_{self.p.get('macd_slow',26)}_{self.p.get('macd_signal',9)}"
    def bbands_upper(self) -> str: return f"BBU_{self.p.get('bbands_length', 20)}_{self.p.get('bbands_std', 2.0)}"
    def bbands_lower(self) -> str: return f"BBL_{self.p.get('bbands_length', 20)}_{self.p.get('bbands_std', 2.0)}"
    def vwap(self) -> str: return "VWAP_D"
    def atr(self) -> str: return f"ATRR_{self.p.get('atr_length', 14)}"

class IndicatorService:
    """Oblicza i interpretuje wskaźniki techniczne."""
    def __init__(self, settings_manager: SettingsManager, analyzer: 'TechnicalAnalyzer'):
        self.settings = settings_manager
        self.analyzer = analyzer

    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty: return pd.DataFrame()
        rename_map = {col: col.lower() for col in df.columns}
        df.rename(columns=rename_map, inplace=True)
        try:
            params = self.settings.get('analysis.indicator_params', {})
            df.ta.atr(append=True, length=params.get('atr_length', 14))
            df.ta.obv(append=True)
            df.ta.rsi(append=True, length=params.get('rsi_length', 14))
            df.ta.ema(append=True, length=params.get('ema_fast_length', 50))
            df.ta.ema(append=True, length=params.get('ema_slow_length', 200))
            df.ta.macd(append=True, fast=params.get('macd_fast', 12), slow=params.get('macd_slow', 26), signal=params.get('macd_signal', 9))
            df.ta.bbands(append=True, length=params.get('bbands_length', 20), std=params.get('bbands_std', 2.0))
            df.ta.vwap(append=True)
        except Exception as e:
            logger.error(f"Błąd podczas obliczania wskaźników: {e}", exc_info=True)
        finally:
            df.columns = [col.upper() for col in df.columns]
            df.rename(columns={'OPEN': 'Open', 'HIGH': 'High', 'LOW': 'Low', 'CLOSE': 'Close', 'VOLUME': 'Volume'}, inplace=True)
        return df

    def interpret_all(self, df: pd.DataFrame) -> Dict[str, Any]:
        if len(df) < 2: return {}
        interpretations = {}
        keys = IndicatorKeyGenerator(self.settings.get('analysis.indicator_params', {}))
        pivots = self._calculate_pivot_points(df)
        
        interpretations.update(self._get_ema_interpretation(df, keys))
        interpretations.update(self._get_rsi_interpretation(df, keys))
        interpretations.update(self._get_macd_interpretation(df, keys))
        interpretations.update(self._get_bbands_interpretation(df, keys))
        interpretations.update(self._get_volume_interpretation(df, keys))
        if pivots:
            interpretations['Pivots'] = {'text': str(pivots), 'sentiment': 'neutral'}
        return interpretations

    def _calculate_pivot_points(self, df: pd.DataFrame) -> Dict[str, float]:
        if len(df) < 2: return {}
        try:
            prev = df.iloc[-2]
            pivot = (prev['High'] + prev['Low'] + prev['Close']) / 3
            return {'PP': pivot, 'S1': (2*pivot)-prev['High'], 'R1': (2*pivot)-prev['Low'], 'S2': pivot-(prev['High']-prev['Low']), 'R2': pivot+(prev['High']-prev['Low'])}
        except KeyError as e:
            logger.error(f"Błąd obliczania pivotów: Brak wymaganej kolumny - {e}."); return {}

    
    def _get_rsi_interpretation(self, df: pd.DataFrame, keys: IndicatorKeyGenerator) -> dict:
        result = {}
        last = df.iloc[-1]
        rsi_key = keys.rsi()
        
        if rsi_key in df.columns and pd.notna(last[rsi_key]):
            rsi_val = last[rsi_key]
            
            sentiment = 'neutral'
            status_text = f"Neutralny ({rsi_val:.2f})"
            if rsi_val > 70:
                sentiment = 'bearish'
                status_text = f"Wykupienie ({rsi_val:.2f})"
            elif rsi_val < 30:
                sentiment = 'bullish'
                status_text = f"Wyprzedanie ({rsi_val:.2f})"
            
            result['RSI'] = {'text': status_text, 'sentiment': sentiment}
            
            divergence = self.analyzer.pattern_service.find_divergence(df['Close'], df[rsi_key])
            if divergence:
                div_sentiment = 'bullish' if 'Bycza' in divergence else 'bearish'
                result['RSI_Divergence'] = {'text': 'Występuje', 'sentiment': div_sentiment}
        return result

    def _get_ema_interpretation(self, df: pd.DataFrame, keys: IndicatorKeyGenerator) -> dict:
        result = {}
        last = df.iloc[-1]
        price = last['Close']
        ema_f_key, ema_s_key = keys.ema(fast=True), keys.ema(fast=False)

        if ema_f_key in df.columns and ema_s_key in df.columns and pd.notna(last[ema_f_key]) and pd.notna(last[ema_s_key]):
            ema_fast, ema_slow = last[ema_f_key], last[ema_s_key]
            
            sentiment = 'neutral'
            trend_text = "Konsolidacja lub korekta"
            if ema_fast > ema_slow and price > ema_fast:
                sentiment = 'bullish'
                trend_text = "Silny trend wzrostowy"
            elif ema_fast < ema_slow and price < ema_fast:
                sentiment = 'bearish'
                trend_text = "Silny trend spadkowy"
                
            result['EMA_Trend'] = {
                'text': trend_text,
                'sentiment': sentiment
            }
        return result
    
    def _get_macd_interpretation(self, df: pd.DataFrame, keys: IndicatorKeyGenerator) -> dict:
        result = {}
        last, prev = df.iloc[-1], df.iloc[-2]
        macd_key, sig_key = keys.macd(), keys.macd_signal()
        
        if macd_key in df.columns and sig_key in df.columns and pd.notna(last[macd_key]) and pd.notna(last[sig_key]):
            text, sentiment = "Brak przecięcia", "neutral"
            if last[macd_key] > last[sig_key] and prev[macd_key] <= prev[sig_key]:
                text, sentiment = "Bycze przecięcie", "bullish"
            elif last[macd_key] < last[sig_key] and prev[macd_key] >= prev[sig_key]:
                text, sentiment = "Niedźwiedzie przecięcie", "bearish"
            
            if sentiment != 'neutral':
                result['MACD'] = {'text': text, 'sentiment': sentiment}
        return result
        
    def _get_bbands_interpretation(self, df: pd.DataFrame, keys: IndicatorKeyGenerator) -> dict:
        result = {}
        last = df.iloc[-1]
        bbu_key, bbl_key = keys.bbands_upper(), keys.bbands_lower()
        
        if bbu_key in df.columns and bbl_key in df.columns and pd.notna(last[bbu_key]):
            text, sentiment = None, None
            if last['Close'] > last[bbu_key]:
                text, sentiment = "Przebicie górnej wstęgi", "bearish" # Potencjalna korekta
            elif last['Close'] < last[bbl_key]:
                text, sentiment = "Przebicie dolnej wstęgi", "bullish" # Potencjalne odbicie
            
            if text:
                result['Bollinger_Bands'] = {'text': text, 'sentiment': sentiment}
        return result
        
    def _get_volume_interpretation(self, df: pd.DataFrame, keys: IndicatorKeyGenerator) -> dict:
        result = {}
        last, prev = df.iloc[-1], df.iloc[-2]

        if 'OBV' in df.columns and pd.notna(last['OBV']) and pd.notna(prev['OBV']):
            sentiment = 'bullish' if last['OBV'] > prev['OBV'] else 'bearish'
            result['Volume_Trend_OBV'] = {'text': f"Trend {sentiment}", 'sentiment': sentiment}
        
        vwap_key = keys.vwap()
        if vwap_key in df.columns and pd.notna(last[vwap_key]):
            sentiment = 'bullish' if last['Close'] > last[vwap_key] else 'bearish'
            result['VWAP_Position'] = {'text': f"Cena {('powyżej' if sentiment == 'bullish' else 'poniżej')} VWAP", 'sentiment': sentiment}
        return result
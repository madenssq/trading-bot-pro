import asyncio
import logging
import pandas as pd
import httpx
from datetime import datetime
from typing import Dict, Any, Optional, List

from core.settings_manager import SettingsManager
from core.exchange_service import ExchangeService
from core.indicator_service import IndicatorService, IndicatorKeyGenerator
from core.database_manager import DatabaseManager

logger = logging.getLogger(__name__)

class ContextService:
    """Odpowiada za analizę szerszego kontekstu rynkowego."""

    def __init__(self, settings_manager: SettingsManager, exchange_service: ExchangeService, indicator_service: IndicatorService, db_manager: DatabaseManager):
        self.settings = settings_manager
        self.exchange_service = exchange_service
        self.indicator_service = indicator_service
        self.db_manager = db_manager

    async def get_market_regime(self, exchange_id: str = "BINANCE") -> str:
        try:
            exchange = await self.exchange_service.get_exchange_instance(exchange_id)
            if not exchange: return "KONSOLIDACJA"
            
            assets = ['BTC/USDT', 'ETH/USDT']
            tasks = [self.exchange_service.fetch_ohlcv(exchange, asset, '1d', limit=51) for asset in assets]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            scores = []
            for df in results:
                if isinstance(df, Exception) or df is None or df.empty or len(df) < 50:
                    scores.append(0); continue
                
                df = self.indicator_service.calculate_all(df)
                df.dropna(inplace=True)
                if df.empty:
                    scores.append(0); continue

                last_candle = df.iloc[-1]
                price = last_candle['Close']
                ema_fast_key = next((col for col in df.columns if 'EMA_50' in col), None) # Używamy standardowej szybkiej
                ema_slow_key = next((col for col in df.columns if 'EMA_200' in col), None)# Używamy standardowej wolnej

                if not ema_fast_key or not ema_slow_key or ema_fast_key not in last_candle or ema_slow_key not in last_candle:
                    scores.append(0); continue
                
                ema_fast, ema_slow = last_candle[ema_fast_key], last_candle[ema_slow_key]
                
                if price > ema_fast and ema_fast > ema_slow: scores.append(1)
                elif price < ema_fast and ema_fast < ema_slow: scores.append(-1)
                else: scores.append(0)
            
            total_score = sum(scores)
            if total_score >= 2: return "RYNEK_BYKA"
            elif total_score <= -2: return "RYNEK_NIEDZWIEDZIA"
            else: return "KONSOLIDACJA"
        except Exception as e:
            logger.error(f"Błąd podczas analizy reżimu rynkowego: {e}")
            return "KONSOLIDACJA"

    async def get_market_momentum_status(self, symbol: str, exchange: str) -> str:
        try:
            exchange_instance = await self.exchange_service.get_exchange_instance(exchange)
            if not exchange_instance: return "NEUTRALNY"

            df_daily = await self.exchange_service.fetch_ohlcv(exchange_instance, symbol, '1d')
            if df_daily is None or df_daily.empty or len(df_daily) < 21: return "NEUTRALNY"

            df_daily = self.indicator_service.calculate_all(df_daily)
            df_daily.dropna(inplace=True)
            if df_daily.empty: return "NEUTRALNY"

            last_candle = df_daily.iloc[-1]
            price = last_candle['Close']
            params = self.settings.get('analysis.indicator_params', {})
            keys = IndicatorKeyGenerator(params)
            
            ema_fast_key = keys.ema(fast=True)
            if ema_fast_key not in last_candle: return "NEUTRALNY"
            ema_trend = last_candle[ema_fast_key]

            is_rsi_extreme = last_candle.get(keys.rsi(), 50) > 80
            is_far_from_ema = (price - ema_trend) / ema_trend > 0.25 if ema_trend > 0 else False
            
            upper_bb_key = keys.bbands_upper()
            if upper_bb_key not in df_daily.columns: return "NEUTRALNY"
            is_riding_bb = (df_daily['Close'].iloc[-3:] > df_daily[upper_bb_key].iloc[-3:]).all()

            if is_rsi_extreme and is_far_from_ema and is_riding_bb: return "RYZYKO_PRZEGRZANIA"
            elif price > ema_trend: return "SILNY_TREND"
            else: return "NEUTRALNY"
        except Exception as e:
            logger.warning(f"Nie udało się obliczyć statusu pędu dla {symbol}: {e}")
            return "NEUTRALNY"

    def get_intermediate_trend_status(self, df: pd.DataFrame) -> str:
        if df is None or df.empty or len(df) < 50: return "BRAK_DANYCH"
        try:
            keys = IndicatorKeyGenerator(self.settings.get('analysis.indicator_params', {}))
            ema_key = keys.ema(fast=True)
            macd_key, macds_key = keys.macd(), keys.macd_signal()
            if not all(k in df.columns for k in [ema_key, macd_key, macds_key]): return "BRAK_DANYCH"
            
            last_candle = df.iloc[-1]
            price = last_candle['Close']
            price_above_ema = price > last_candle[ema_key]
            macd_is_bullish = last_candle[macd_key] > last_candle[macds_key]

            if price_above_ema and macd_is_bullish: return "TREND_WZROSTOWY"
            elif not price_above_ema and not macd_is_bullish: return "TREND_SPADKOWY"
            else: return "KOREKTA_LUB_KONSOLIDACJA"
        except Exception as e:
            logger.error(f"Błąd podczas analizy trendu średnioterminowego: {e}")
            return "BRAK_DANYCH"

    def analyze_approach_momentum(self, df: pd.DataFrame, lookback: int = 5) -> str:
        if len(df) < lookback + 1: return "BRAK_DANYCH"
        recent_df = df.iloc[-lookback:].copy()
        
        atr_key = next((col for col in df.columns if 'ATR' in col.upper()), None)
        if not atr_key or atr_key not in recent_df.columns: return "BRAK_DANYCH"

        price_change_pct = (recent_df['Close'].iloc[-1] / recent_df['Close'].iloc[0] - 1) * 100
        recent_df['body_size'] = abs(recent_df['Close'] - recent_df['Open'])
        avg_body_vs_atr = (recent_df['body_size'] / recent_df[atr_key]).mean()

        if avg_body_vs_atr > 0.7:
            if price_change_pct < -1: return "MOCNY_IMPULS_SPADKOWY"
            elif price_change_pct > 1: return "MOCNY_IMPULS_WZROSTOWY"

        return "KOREKCYJNE_ZEJSCIE" if price_change_pct < 0 else "KOREKCYJNY_WZROST"


    async def analyze_order_flow_strength(self, symbol: str, exchange_id: str) -> str:
        try:
            exchange = await self.exchange_service.get_exchange_instance(exchange_id)
            if not (exchange.has.get('fetchL2OrderBook') and exchange.has.get('fetchTrades')):
                return "BRAK_DANYCH"
            
            order_book, trades = await asyncio.gather(
                exchange.fetch_l2_order_book(symbol, limit=100),
                exchange.fetch_trades(symbol, limit=100),
                return_exceptions=True
            )
            if isinstance(order_book, Exception) or isinstance(trades, Exception): return "BRAK_DANYCH"

            score = 0
            best_bid = order_book['bids'][0][0] if order_book['bids'] else 0
            best_ask = order_book['asks'][0][0] if order_book['asks'] else 0
            if best_bid > 0 and best_ask > 0:
                bid_volume = sum(price * amount for price, amount in order_book['bids'] if price > best_bid * 0.98)
                ask_volume = sum(price * amount for price, amount in order_book['asks'] if price < best_ask * 1.02)
                if bid_volume > ask_volume * 1.5: score += 1
                if ask_volume > bid_volume * 1.5: score -= 1
            
            aggressive_buys = sum(trade['cost'] for trade in trades if trade['side'] == 'buy')
            aggressive_sells = sum(trade['cost'] for trade in trades if trade['side'] == 'sell')
            if aggressive_buys > aggressive_sells * 1.5: score += 1
            if aggressive_sells > aggressive_buys * 1.5: score -= 1
            
            if score >= 2: return "SILNA_PRESJA_KUPUJĄCYCH"
            elif score <= -2: return "SILNA_PRESJA_SPRZEDAJĄCYCH"
            else: return "BRAK_DOMINACJI"
        except Exception as e:
            logger.error(f"Błąd w analizie Order Flow dla {symbol}: {e}", exc_info=True)
            return "BRAK_DANYCH"
        
    async def get_onchain_context(self, symbol: str, exchange_id: str) -> Dict[str, Any]:
        """
        Pobiera dane on-chain (Funding Rate, Open Interest), używając bazy danych jako cache.
        """
        today_str = datetime.now().strftime('%Y-%m-%d')
        cached_data = self.db_manager.get_onchain_metrics(symbol, today_str)
        if cached_data:
            logger.info(f"Pobrano dane on-chain dla {symbol} z lokalnej bazy (cache).")
            return {
                "funding_rate": cached_data.get('funding_rate'),
                "open_interest_usd": cached_data.get('open_interest_usd')
            }

        logger.info(f"Brak danych on-chain w cache dla {symbol}. Pobieranie z giełdy...")
        metrics = {"funding_rate": None, "open_interest_usd": None}
        try:
            # POPRAWKA: Używamy exchange_service
            exchange = await self.exchange_service.get_exchange_instance(exchange_id)
            if not exchange or not exchange.has.get('fetchOpenInterest') or not exchange.has.get('fetchFundingRate'):
                return metrics

            exchange.options['defaultType'] = 'swap'
            
            oi_task = exchange.fetch_open_interest(symbol)
            fr_task = exchange.fetch_funding_rate(symbol)
            results = await asyncio.gather(oi_task, fr_task, return_exceptions=True)

            if not isinstance(results[0], Exception):
                metrics["open_interest_usd"] = results[0].get('openInterestValue')
            if not isinstance(results[1], Exception):
                metrics["funding_rate"] = results[1].get('fundingRate')

            db_payload = {**metrics, "symbol": symbol, "date": today_str}
            self.db_manager.save_onchain_metrics(db_payload)

            return metrics
        except Exception as e:
            logger.warning(f"Nie udało się pobrać danych on-chain dla {symbol}: {e}")
            return metrics

    async def get_relative_strength(self, symbol: str, exchange: str, comparison_symbol: str = "BTC/USDT", lookback_days: int = 7) -> Optional[float]:
        try:
            exchange_instance = await self.exchange_service.get_exchange_instance(exchange)
            if not exchange_instance: return None

            tasks = {
                'symbol': self.exchange_service.fetch_ohlcv(exchange_instance, symbol, '1d'),
                'comparison': self.exchange_service.fetch_ohlcv(exchange_instance, comparison_symbol, '1d')
            }
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            df_symbol, df_comparison = results[0], results[1]
            
            if df_symbol is None or df_comparison is None or len(df_symbol) < lookback_days or len(df_comparison) < lookback_days: return None

            symbol_change = (df_symbol['Close'].iloc[-1] / df_symbol['Close'].iloc[-lookback_days] - 1) * 100
            comparison_change = (df_comparison['Close'].iloc[-1] / df_comparison['Close'].iloc[-lookback_days] - 1) * 100
            return symbol_change - comparison_change
        except Exception as e:
            logger.warning(f"Nie udało się obliczyć siły względnej dla {symbol}: {e}")
            return None

    async def get_short_squeeze_indicator(self, symbol: str, exchange: str) -> Optional[str]:
        try:
            exchange_instance = await self.exchange_service.get_exchange_instance(exchange)
            if not exchange_instance or not exchange_instance.has.get('fetchOpenInterest'): return "N/A"

            exchange_instance.options['defaultType'] = 'swap'
            oi_data, funding_data = await asyncio.gather(
                exchange_instance.fetch_open_interest(symbol),
                exchange_instance.fetch_funding_rate(symbol),
                return_exceptions=True
            )
            
            if isinstance(oi_data, Exception) or isinstance(funding_data, Exception): return "N/A"
            
            funding_rate = funding_data.get('fundingRate', 0)
            open_interest_value = oi_data.get('openInterestValue', 0)
            
            if funding_rate < 0 and open_interest_value > 5_000_000: return "Wysoki"
            elif funding_rate < -0.0005: return "Średni"
            else: return "Niski"
        except Exception as e:
            logger.warning(f"Nie udało się pobrać danych do wskaźnika Short Squeeze dla {symbol}: {e}")
            return "Błąd"
        
    async def get_fear_and_greed_index(self) -> str:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get("https://api.alternative.me/fng/?limit=1")
                response.raise_for_status()
                data = response.json()['data'][0]
                return f"{data['value']} ({data['value_classification']})"
        except Exception as e:
            logger.warning(f"Nie udało się pobrać Indeksu Strachu i Chciwości: {e}")
            return "Brak danych"
        
    def get_active_trading_sessions(self) -> str:
        now_utc = datetime.utcnow().time()
        sessions = []
        if now_utc.hour >= 0 and now_utc.hour < 9: sessions.append("Azja (Tokio)")
        if now_utc.hour >= 7 and now_utc.hour < 16: sessions.append("Europa (Londyn)")
        if now_utc.hour >= 12 and now_utc.hour < 21: sessions.append("USA (Nowy Jork)")
        return ", ".join(sessions) if sessions else "Brak głównych aktywnych sesji"
    
    async def get_simple_recommendation(self, symbol: str, exchange: str) -> str:
        try:
            exchange_instance = await self.exchange_service.get_exchange_instance(exchange)
            if not exchange_instance: return "Błąd"

            ohlcv_1h = await self.exchange_service.fetch_ohlcv(exchange_instance, symbol, '1h')
            if ohlcv_1h is None or ohlcv_1h.empty: return "B/D"

            ohlcv_4h = await self.exchange_service.fetch_ohlcv(exchange_instance, symbol, '4h')
            if ohlcv_4h is None or ohlcv_4h.empty: return "B/D"

            self.indicator_service.calculate_all(ohlcv_1h)
            self.indicator_service.calculate_all(ohlcv_4h)

            keys = IndicatorKeyGenerator(self.settings.get('analysis.indicator_params', {}))
            ema_key = keys.ema(fast=True)

            if ema_key not in ohlcv_1h.columns or ema_key not in ohlcv_4h.columns: return "Błąd"

            last_close_1h, last_ema_1h = ohlcv_1h['Close'].iloc[-1], ohlcv_1h[ema_key].iloc[-1]
            last_close_4h, last_ema_4h = ohlcv_4h['Close'].iloc[-1], ohlcv_4h[ema_key].iloc[-1]

            if last_close_1h > last_ema_1h and last_close_4h > last_ema_4h: return "KUPUJ"
            elif last_close_1h < last_ema_1h and last_close_4h < last_ema_4h: return "SPRZEDAJ"
            else: return "NEUTRALNIE"
        except Exception as e:
            logger.warning(f"Nie udało się wygenerować prostej rekomendacji dla {symbol}: {e}", exc_info=True)
            return "Błąd"
        
    async def get_daily_metrics(self, symbol: str, exchange: str) -> Dict[str, Any]:
        metrics = {'atr_percent': None, 'dist_from_ema200': None}
        try:
            exchange_instance = await self.exchange_service.get_exchange_instance(exchange)
            if not exchange_instance: return metrics

            df_daily = await self.exchange_service.fetch_ohlcv(exchange_instance, symbol, '1d')
            if df_daily is None or df_daily.empty or len(df_daily) < 200: return metrics

            df_daily = self.indicator_service.calculate_all(df_daily)
            last_candle = df_daily.iloc[-1]
            price = last_candle['Close']
            keys = IndicatorKeyGenerator(self.settings.get('analysis.indicator_params', {}))

            atr_key = keys.atr()
            if atr_key in last_candle and pd.notna(last_candle[atr_key]):
                atr_value = last_candle[atr_key]
                if price > 0: metrics['atr_percent'] = (atr_value / price) * 100

            ema_key = keys.ema(fast=False)
            if ema_key in last_candle and pd.notna(last_candle[ema_key]):
                ema200 = last_candle[ema_key]
                if ema200 > 0: metrics['dist_from_ema200'] = ((price - ema200) / ema200) * 100
            return metrics
        except Exception as e:
            logger.warning(f"Nie udało się obliczyć metryk dziennych dla {symbol}: {e}")
            return metrics
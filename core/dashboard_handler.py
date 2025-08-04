import asyncio
import logging
from typing import List, Dict, Any
import time
import json
from app_config import DASHBOARD_CACHE_LIFETIME_SECONDS
# Upewnij się, że biblioteka jest zainstalowana: pip install tradingview_ta
from tradingview_ta import TA_Handler, Interval
from core.analyzer import TechnicalAnalyzer
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

class DashboardHandler:
    """
    Zarządza pobieraniem i agregowaniem danych na potrzeby głównego dashboardu.
    """
    def __init__(self, analyzer: TechnicalAnalyzer, thread_pool: ThreadPoolExecutor):
        
        self.analyzer = analyzer
        self.thread_pool = thread_pool



    async def get_market_summary(self, coins: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """
        Główna metoda pobierająca pełne podsumowanie rynku dla listy coinów.
        Używa semafora, aby ograniczyć liczbę równoczesnych zapytań do API.
        """
        # Ustawiamy semafor na maksymalnie 5 równoczesnych zadań
        semaphore = asyncio.Semaphore(3)

        # Tworzymy funkcję pomocniczą, która "opakowuje" nasze zadanie w semafor
        async def fetch_with_semaphore(coin):
            async with semaphore:
                return await self._get_single_coin_summary(coin)

        tasks = [fetch_with_semaphore(coin) for coin in coins]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_results = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"Nie udało się pobrać danych dla {coins[i]['symbol']}: {res}")
            else:
                valid_results.append(res)
        return valid_results

    async def _get_single_coin_summary(self, coin: Dict[str, str]) -> Dict[str, Any]:
        """
        Orkiestruje pobieraniem danych dla coina, pomijając już dane z TradingView.
        """
        symbol = coin['symbol']
        exchange_id = coin['exchange']

        # --- Definicje zadań asynchronicznych ---

        async def fetch_ticker_task():
            exchange_instance = await self.analyzer.get_exchange_instance(exchange_id)
            if not exchange_instance: return None
            try:
                return await exchange_instance.fetch_ticker(symbol)
            except Exception as e:
                logger.warning(f"Nie udało się pobrać tickera dla {symbol}: {e}")
                return None

        # Tworzymy listę wszystkich zadań do równoległego uruchomienia (bez TradingView)
        tasks = [
            fetch_ticker_task(),
            self.analyzer.get_simple_recommendation(symbol, exchange_id),
            self.analyzer.get_daily_metrics(symbol, exchange_id),
            self.analyzer.get_relative_strength(symbol, exchange_id),
            self.analyzer.get_long_short_ratio(symbol, exchange_id),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Rozpakowujemy wyniki
        ticker = results[0] if not isinstance(results[0], Exception) else {}
        bot_reco = results[1] if not isinstance(results[1], Exception) else "Błąd"
        daily_metrics = results[2] if not isinstance(results[2], Exception) else {}
        rel_strength = results[3] if not isinstance(results[3], Exception) else None
        ls_ratio = results[4] if not isinstance(results[4], Exception) else None

        # Przetwarzanie i zwracanie wyników
        price = ticker.get('last')
        change_24h = ticker.get('percentage')
        volume_24h = ticker.get('baseVolume')

        return {
            "symbol": symbol, "price": price, "change_24h": change_24h,
            "volume_24h": volume_24h, "bot_reco": bot_reco,
            "dist_from_ema200": daily_metrics.get('dist_from_ema200'),
            "atr_percent": daily_metrics.get('atr_percent'),
            "relative_strength_btc_7d": rel_strength, 
            "long_short_ratio": ls_ratio,
        }

    async def close_session(self):
        """Metoda do zamykania zasobów (jeśli będą potrzebne w przyszłości)."""
        pass

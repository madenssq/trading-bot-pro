import asyncio
import logging
from typing import List, Dict, Any

# Upewnij się, że biblioteka jest zainstalowana: pip install tradingview_ta
from tradingview_ta import TA_Handler, Interval
from core.analyzer import TechnicalAnalyzer

logger = logging.getLogger(__name__)

class DashboardHandler:
    """
    Zarządza pobieraniem i agregowaniem danych na potrzeby głównego dashboardu.
    """
    def __init__(self, analyzer: TechnicalAnalyzer):
        """DashboardHandler wymaga instancji analizatora do zaawansowanych obliczeń."""
        self.analyzer = analyzer

    def _determine_confluence(self, bot_reco: str, tv_recos: List[str]) -> str:
        """
        Określa zgodność (konfluencję) sygnałów z różnych źródeł.
        Zwraca czytelny dla użytkownika opis statusu.
        """
        if not bot_reco or not tv_recos:
            return "Brak Danych"

        # Zliczamy sygnały kupna i sprzedaży z TradingView
        buys = sum(1 for r in tv_recos if r and "BUY" in r)
        sells = sum(1 for r in tv_recos if r and "SELL" in r)

        # Definiujemy logikę zgodności
        if bot_reco == "KUPUJ" and buys >= 1 and sells == 0:
            return "▲ ZGODNOŚĆ WZROSTOWA"
        elif bot_reco == "SPRZEDAJ" and sells >= 1 and buys == 0:
            return "▼ ZGODNOŚĆ SPADKOWA"
        elif (bot_reco == "KUPUJ" and sells > 0) or (bot_reco == "SPRZEDAJ" and buys > 0):
            return "◄► KONFLIKT SYGNAŁÓW"
        else:
            return "▬ SYGNAŁY MIESZANE"

    async def get_market_summary(self, coins: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """
        Główna metoda pobierająca pełne podsumowanie rynku dla listy coinów.
        Używa semafora, aby ograniczyć liczbę równoczesnych zapytań do API.
        """
        # Ustawiamy semafor na maksymalnie 5 równoczesnych zadań
        semaphore = asyncio.Semaphore(5)

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
        Orkiestruje pobieranie wszystkich potrzebnych danych dla pojedynczego coina.
        """
        symbol = coin['symbol']
        exchange_id = coin['exchange']
        
        loop = asyncio.get_event_loop()

        # --- Definicje zadań asynchronicznych ---

        # Zadanie 1: Pobranie podstawowych danych (cena, wolumen)
        async def fetch_ticker_task():
            exchange_instance = await self.analyzer.exchange_service.get_exchange_instance(exchange_id)
            if not exchange_instance: raise Exception("Brak instancji giełdy")
            return await exchange_instance.fetch_ticker(symbol)

        bot_reco_task = self.analyzer.context_service.get_simple_recommendation(symbol, exchange_id)

        # Zadanie 3: Pobranie rekomendacji z TradingView (uruchamiane w tle)
        def get_tv_reco_sync(interval: Interval):
            try:
                handler = TA_Handler(symbol=symbol.replace('/', ''), screener="crypto", exchange=exchange_id, interval=interval)
                return handler.get_analysis().summary.get("RECOMMENDATION", "Błąd TV")
            except Exception:
                return "Błąd TV"
        
        tv_tasks = [loop.run_in_executor(None, get_tv_reco_sync, iv) for iv in [Interval.INTERVAL_1_HOUR, Interval.INTERVAL_4_HOURS, Interval.INTERVAL_1_DAY]]

        # Zadanie 4: Pobranie zaawansowanych metryk dziennych (z nowego serwisu)
        daily_metrics_task = self.analyzer.context_service.get_daily_metrics(symbol, exchange_id)
        relative_strength_task = self.analyzer.context_service.get_relative_strength(symbol, exchange_id)
        squeeze_indicator_task = self.analyzer.context_service.get_short_squeeze_indicator(symbol, exchange_id)

        # --- Uruchomienie i zebranie wyników ---
        
        ticker, bot_reco, tv_recos, daily_metrics, rel_strength, squeeze_ind = await asyncio.gather(
            fetch_ticker_task(), 
            bot_reco_task, 
            asyncio.gather(*tv_tasks), 
            daily_metrics_task,
            relative_strength_task,
            squeeze_indicator_task
        )

        # Przetwarzanie i zwracanie wyników
        confluence = self._determine_confluence(bot_reco, tv_recos)
        price = ticker.get('last') if ticker else None
        
        # Upewniamy się, że formatujemy tylko liczby
        change_24h = ticker.get('percentage') * 100 if isinstance(ticker.get('percentage'), (int, float)) else None
        volume_24h = ticker.get('baseVolume') if ticker else None

        return {
            "symbol": symbol,
            "price": price,
            "change_24h": change_24h,
            "volume_24h": volume_24h,
            "bot_reco": bot_reco,
            "tv_1h": tv_recos[0].replace("_", " ") if tv_recos[0] else "N/A",
            "tv_4h": tv_recos[1].replace("_", " ") if tv_recos[1] else "N/A",
            "tv_1d": tv_recos[2].replace("_", " ") if tv_recos[2] else "N/A",
            "confluence": confluence,
            "dist_from_ema200": daily_metrics.get('dist_from_ema200'),
            "atr_percent": daily_metrics.get('atr_percent'),
            "relative_strength_btc_7d": rel_strength, 
            "short_squeeze_potential": squeeze_ind,
        }

    async def close_session(self):
        """Metoda do zamykania zasobów (jeśli będą potrzebne w przyszłości)."""
        pass

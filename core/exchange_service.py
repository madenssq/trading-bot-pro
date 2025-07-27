# Plik: core/exchange_service.py

import asyncio
import logging
import pandas as pd
from typing import Dict, Optional

import ccxt.async_support as ccxt

logger = logging.getLogger(__name__)

class ExchangeService:
    """Zarządza połączeniami z giełdami i pobieraniem danych OHLCV."""

    def __init__(self):
        self.exchange_instances: Dict[str, ccxt.Exchange] = {}
        self.max_candles = 500 # Możemy przenieść to do ustawień w przyszłości

    async def get_exchange_instance(self, exchange_id: str) -> Optional[ccxt.Exchange]:
        """Pobiera lub tworzy instancję ccxt dla danej giełdy."""
        if exchange_id not in self.exchange_instances:
            try:
                exchange_class = getattr(ccxt, exchange_id.lower())
                config = {'enableRateLimit': True, 'timeout': 40000}
                self.exchange_instances[exchange_id] = exchange_class(config)
                logger.info(f"Utworzono nową instancję dla giełdy: {exchange_id}")
            except AttributeError:
                logger.error(f"Nieznana giełda: {exchange_id}")
                return None
        return self.exchange_instances[exchange_id]

    async def fetch_ohlcv(self, exchange: ccxt.Exchange, symbol: str, interval: str, limit: int = None, since: int = None) -> Optional[pd.DataFrame]:
        """Pobiera świece OHLCV z danej giełdy."""
        try:
            fetch_limit = limit if limit is not None else self.max_candles
            raw_ohlcv = await exchange.fetch_ohlcv(symbol, interval, limit=fetch_limit, since=since)
            if not raw_ohlcv: 
                return None
            df = pd.DataFrame(raw_ohlcv, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df.set_index('timestamp').sort_index()
        except Exception as e:
            logger.error(f"Błąd podczas pobierania świec dla {symbol} ({interval}): {e}")
            raise

    async def close_all_exchanges(self):
        """Zamyka wszystkie aktywne połączenia z giełdami."""
        await asyncio.gather(*[ex.close() for ex in self.exchange_instances.values()], return_exceptions=True)
        logger.info("Połączenia ExchangeService z giełdami zostały zamknięte.")
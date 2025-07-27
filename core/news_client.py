import httpx
import logging
from typing import List, Dict, Optional, Tuple
from datetime import date # <--- DODANY IMPORT

logger = logging.getLogger(__name__)

class CryptoPanicClient:
    """
    Klient do komunikacji z API CryptoPanic, teraz z jednodniowym cache'em.
    """
    BASE_URL = "https://cryptopanic.com/api/v1/posts/"

    def __init__(self, api_token: str):
        if not api_token:
            raise ValueError("Token API dla CryptoPanic jest wymagany.")
        self.api_token = api_token
        # ZMIANA: Inicjalizujemy pusty słownik na cache
        # Format: { 'symbol': (data_pobrania, lista_wiadomosci) }
        self.news_cache: Dict[str, Tuple[date, List[Dict]]] = {}

    async def get_recent_news_for_symbol(self, symbol: str) -> Optional[List[Dict]]:
        """
        Pobiera ostatnie wiadomości dla symbolu, używając cache'a.
        """
        currency_symbol = symbol.split('/')[0]
        today = date.today()

        # Krok 1: Sprawdź, czy mamy aktualne dane w cache'u
        if currency_symbol in self.news_cache:
            fetch_date, cached_news = self.news_cache[currency_symbol]
            if fetch_date == today:
                logger.info(f"Pobrano {len(cached_news)} wiadomości dla {currency_symbol} z pamięci podręcznej (cache).")
                return cached_news

        # Krok 2: Jeśli nie, pobierz dane z API
        logger.info(f"Brak aktualnych danych w cache dla {currency_symbol}. Pobieranie z API CryptoPanic...")
        params = {
            "auth_token": self.api_token,
            "currencies": currency_symbol,
            "public": "true"
        }

        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(self.BASE_URL, params=params, timeout=15.0)
                response.raise_for_status()
                data = response.json()
                news_results = data.get("results")

                # Krok 3: Zapisz nowe dane w cache'u
                if news_results is not None:
                    self.news_cache[currency_symbol] = (today, news_results)
                    logger.info(f"Pobrano {len(news_results)} wiadomości dla {currency_symbol} i zapisano w cache.")
                
                return news_results

        except httpx.HTTPStatusError as e:
            # Jeśli przekroczymy limit, zapisujemy pustą listę, aby nie próbować ponownie tego dnia
            if e.response.status_code == 403:
                 self.news_cache[currency_symbol] = (today, [])
                 logger.warning(f"Przekroczono limit API dla {currency_symbol}. Nie będę próbować ponownie dzisiaj.")
            
            logger.error(f"Błąd API CryptoPanic ({e.response.status_code}): {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Nieoczekiwany błąd podczas pobierania wiadomości z CryptoPanic: {e}")
            return None
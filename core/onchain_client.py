import httpx
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class OnChainClient:
    """
    Klient do komunikacji z API dostarczającym dane on-chain.
    """
    # Zmienimy to na właściwy URL, gdy wybierzesz API
    BASE_URL = "https://api.example.com/v1/" 

    def __init__(self, api_key: Optional[str]):
        if not api_key:
            raise ValueError("Klucz API dla danych on-chain jest wymagany.")
        self.api_key = api_key

    async def get_metrics(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Pobiera kluczowe metryki on-chain dla danego symbolu.
        
        Zwraca słownik, np:
        {
            "nupl": 0.65,
            "funding_rate_avg": 0.01,
            "active_addresses": 500000
        }
        """
        currency = symbol.split('/')[0]
        headers = {"Authorization": f"Bearer {self.api_key}"}
        # Tutaj logika będzie zależeć od konkretnego API
        # np. endpointy mogą być różne dla różnych metryk.
        
        logger.info(f"Pobieranie danych on-chain dla {currency}...")
        
        # Przykładowa logika zapytania (do dostosowania)
        try:
            async with httpx.AsyncClient() as client:
                # To jest tylko przykład, trzeba będzie dostosować endpoint i parametry
                params = {'asset': currency, 'metrics': 'nupl,funding_rate'}
                response = await client.get(f"{self.BASE_URL}metrics", params=params, headers=headers)
                response.raise_for_status()
                
                data = response.json()
                # Tutaj trzeba będzie sparsować odpowiedź, aby pasowała do naszego formatu
                
                return data # Zwracamy przykładowe dane
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Błąd API on-chain ({e.response.status_code}): {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Nieoczekiwany błąd podczas pobierania danych on-chain: {e}")
            return None
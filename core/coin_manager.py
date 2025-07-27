# Plik: core/coin_manager.py (WERSJA PO REFAKTORYZACJI)

import os
import json
import time
import logging
import asyncio
import uuid
import ccxt.async_support as ccxt
from typing import Dict, List, Optional
from core.analyzer import TechnicalAnalyzer

# ZMIANA: Importujemy listę giełd z pliku konfiguracyjnego
from app_config import SYMBOLS_CACHE_FILE, CACHE_MAX_AGE_SECONDS, USER_ID_FILE, SUPPORTED_EXCHANGES

logger = logging.getLogger(__name__)

class CoinManager:
    def __init__(self, db_client=None, auth_admin_client=None, analyzer: TechnicalAnalyzer = None):
        self.db = db_client
        self.auth = auth_admin_client
        self.user_id: Optional[str] = None
        self.available_symbols: Dict[str, List[str]] = {}
        self.user_coin_groups: Dict[str, List[Dict[str, str]]] = {}
        
        self.analyzer = analyzer

    

    async def close_exchanges(self):
        tasks = [ex.close() for ex in self.exchanges.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Połączenia CoinManagera z giełdami zostały zamknięte.")

    def _is_cache_valid(self) -> bool:
        if not os.path.exists(SYMBOLS_CACHE_FILE): return False
        try:
            return (time.time() - os.path.getmtime(SYMBOLS_CACHE_FILE)) < CACHE_MAX_AGE_SECONDS
        except (IOError, FileNotFoundError): return False

    def _load_symbols_from_cache(self):
        try:
            with open(SYMBOLS_CACHE_FILE, 'r', encoding='utf-8') as f: self.available_symbols = json.load(f)
            logger.info(f"Załadowano {len(self.available_symbols)} symboli z pamięci podręcznej.")
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Nie udało się wczytać pliku cache symboli: {e}"); self.available_symbols = {}

    def _save_symbols_to_cache(self):
        try:
            os.makedirs(os.path.dirname(SYMBOLS_CACHE_FILE), exist_ok=True)
            with open(SYMBOLS_CACHE_FILE, 'w', encoding='utf-8') as f: json.dump(self.available_symbols, f, indent=2)
            logger.info(f"Zapisano {len(self.available_symbols)} symboli do pamięci podręcznej.")
        except IOError as e:
            logger.error(f"Nie udało się zapisać pliku cache symboli: {e}")

    async def fetch_all_exchange_symbols(self):
        if self._is_cache_valid():
            self._load_symbols_from_cache()
            return
        
        logger.info("Pamięć podręczna symboli jest przestarzała. Pobieranie z giełd...")
        
        all_symbols = {}

        # KROK 1: Upewnij się, że definicja funkcji przyjmuje tylko JEDEN argument
        async def fetch_from(exchange_id: str):
            try:
                # Pobieramy instancję giełdy z naszego centralnego Analyzera
                exchange = await self.analyzer.exchange_service.get_exchange_instance(exchange_id)
                if not exchange: return

                markets = await exchange.load_markets()
                for market in markets.values():
                    symbol = market['symbol']
                    is_spot_usdt = (market.get('spot', False) and market.get('quote') == 'USDT')
                    is_linear_swap_usdt = (market.get('swap', False) and market.get('linear', True) and market.get('settle') == 'USDT')

                    if is_spot_usdt or is_linear_swap_usdt:
                        if symbol not in all_symbols:
                            all_symbols[symbol] = []
                        all_symbols[symbol].append(exchange_id)
            except Exception as e:
                logger.error(f"Nie udało się pobrać symboli z {exchange_id}: {e}")

        # KROK 2: Upewnij się, że wywołanie przekazuje tylko JEDEN argument
        await asyncio.gather(*[fetch_from(eid) for eid in SUPPORTED_EXCHANGES])
        
        self.available_symbols = all_symbols
        self._save_symbols_to_cache()

        # KROK 2: Upewnij się, że wywołanie przekazuje tylko JEDEN argument
        await asyncio.gather(*[fetch_from(eid) for eid in SUPPORTED_EXCHANGES])
        
        self.available_symbols = all_symbols
        self._save_symbols_to_cache()

    async def set_user_id_and_load_data(self, user_id: Optional[str] = None):
        """Główna metoda inicjująca dane użytkownika."""
        self._ensure_user_id(provided_user_id=user_id)
        if self.db and self.user_id:
            await self._load_groups_from_firestore()

    def _ensure_user_id(self, provided_user_id: Optional[str] = None):
        """Zapewnia, że user_id jest ustawione - z argumentu, z pliku lub jako nowe UUID."""
        if provided_user_id:
            self.user_id = provided_user_id
            return

        try:
            if os.path.exists(USER_ID_FILE):
                with open(USER_ID_FILE, 'r') as f: self.user_id = json.load(f).get('user_id')
            else:
                self.user_id = str(uuid.uuid4())
                with open(USER_ID_FILE, 'w') as f: json.dump({'user_id': self.user_id}, f)
            logger.info(f"Używam lokalnego ID użytkownika: {self.user_id}")
        except Exception as e:
            logger.error(f"Nie udało się uzyskać lokalnego ID: {e}"); self.user_id = str(uuid.uuid4())

    async def _load_groups_from_firestore(self):
        """Wczytuje grupy coinów dla ustawionego user_id z Firestore."""
        logger.info(f"Ładowanie grup coinów dla użytkownika {self.user_id} z Firestore...")
        doc_ref = self.db.collection('user_coin_lists').document(self.user_id)
        
        try:
            loop = asyncio.get_event_loop()
            doc = await loop.run_in_executor(None, doc_ref.get)
            
            if doc.exists:
                self.user_coin_groups = doc.to_dict()
                logger.info("Pomyślnie wczytano grupy coinów z Firestore.")
            else:
                logger.info("Brak dokumentu w Firestore dla tego użytkownika. Tworzenie domyślnych grup.")
                self.user_coin_groups = {"Ulubione": [], "Do Obserwacji": []}
                await self._save_groups_to_firestore()
        except Exception as e:
            logger.critical(f"Krytyczny błąd podczas komunikacji z Firestore: {e}", exc_info=True)
            raise

    async def _save_groups_to_firestore(self) -> bool:
        if not (self.db and self.user_id):
            logger.warning("Próba zapisu do Firestore bez aktywnego połączenia lub user_id.")
            return False
            
        try:
            doc_ref = self.db.collection('user_coin_lists').document(self.user_id)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: doc_ref.set(self.user_coin_groups))
            logger.info("Pomyślnie zapisano grupy coinów w Firestore.")
            return True
        except Exception as e:
            logger.error(f"Błąd zapisu do Firestore: {e}"); return False

    # --- Metody Publiczne do Zarządzania Grupami ---

    def get_available_symbols(self) -> Dict[str, List[str]]: return self.available_symbols
    def get_user_coin_groups(self) -> Dict[str, List[Dict[str, str]]]: return self.user_coin_groups
    
    def get_all_symbols_from_groups(self) -> set:
        all_coins = set()
        for group in self.user_coin_groups.values():
            for coin in group: all_coins.add(coin['symbol'])
        return all_coins
        
    async def add_group(self, group_name: str) -> bool:
        if group_name not in self.user_coin_groups:
            self.user_coin_groups[group_name] = []
            return await self._save_groups_to_firestore()
        return False
        
    async def remove_group(self, group_name: str) -> bool:
        if group_name in self.user_coin_groups and group_name not in ["Ulubione", "Do Obserwacji"]:
            del self.user_coin_groups[group_name]
            return await self._save_groups_to_firestore()
        return False
        
    async def add_coin_to_group(self, group_name: str, symbol: str, exchange: str) -> bool:
        if group_name in self.user_coin_groups:
            coin_entry = {"symbol": symbol, "exchange": exchange}
            if coin_entry not in self.user_coin_groups[group_name]:
                self.user_coin_groups[group_name].append(coin_entry)
                return await self._save_groups_to_firestore()
        return False
        
    async def remove_coin_from_group(self, group_name: str, symbol: str, exchange: str) -> bool:
        if group_name in self.user_coin_groups:
            coin_entry = {"symbol": symbol, "exchange": exchange}
            if coin_entry in self.user_coin_groups[group_name]:
                self.user_coin_groups[group_name].remove(coin_entry)
                return await self._save_groups_to_firestore()
        return False
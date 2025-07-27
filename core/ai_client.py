# Plik: core/ai_client.py

import ast
import httpx
import logging
import json
import re
from typing import Tuple, Dict, Any, Optional, List
from dataclasses import dataclass, field
import pandas as pd

from core.settings_manager import SettingsManager
from core.prompt_templates import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

@dataclass
class ParsedAIResponse:
    html_content: str = ""
    parsed_data: Dict[str, Any] = field(default_factory=dict)
    is_valid: bool = False

class AIClient:
    def __init__(self, settings: SettingsManager):
        self.settings = settings
        self.chat_history = []
        self.update_config()
        self._system_prompt_content = SYSTEM_PROMPT

    def update_config(self):
        self.api_url = self.settings.get("ai.url")
        self.model = self.settings.get("ai.model")
        self.timeout = self.settings.get("ai.timeout", 120)
        self.max_tokens = self.settings.get("ai.max_tokens", 8192)
        self.temperature = self.settings.get("ai.temperature", 0.6)

    def clear_chat_history(self):
        self.chat_history = []

    def add_message(self, role: str, content: str):
        self.chat_history.append({"role": role, "content": content})

    async def get_chat_completion_async(self) -> Optional[str]:
        if not self.chat_history or not self.api_url: return None
        
        messages_with_system = [{"role": "system", "content": self._system_prompt_content}] + self.chat_history

        payload = {
            "model": self.model, 
            "messages": messages_with_system, 
            "max_tokens": self.max_tokens, 
            "temperature": self.temperature, 
            "stream": False
        }
        
        timeout_config = httpx.Timeout(float(self.timeout), connect=10.0) # Zmniejszamy connect timeout do 10s

        try:
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                response = await client.post(self.api_url, json=payload)
                response.raise_for_status()
                json_response = response.json()
                content = json_response["choices"][0]["message"]["content"]
                return content
        # --- NOWA, BARDZIEJ SZCZEGÓŁOWA OBSŁUGA BŁĘDÓW ---
        except httpx.ReadTimeout as e:
            logger.error(f"Błąd ReadTimeout podczas komunikacji z AI: {e}", exc_info=True)
            # Rzucamy nasz własny, zrozumiały wyjątek
            raise TimeoutError(f"Serwer AI ({self.api_url}) nie odpowiedział na czas ({self.timeout}s).") from e
        except httpx.ConnectError as e:
            logger.error(f"Błąd ConnectError podczas komunikacji z AI: {e}", exc_info=True)
            raise ConnectionError(f"Nie można połączyć się z serwerem AI pod adresem: {self.api_url}. Upewnij się, że serwer Ollama jest uruchomiony.") from e
        except httpx.HTTPStatusError as e:
            logger.error(f"Błąd HTTP ({e.response.status_code}) od serwera AI: {e.response.text}", exc_info=True)
            raise ConnectionError(f"Serwer AI zwrócił błąd HTTP {e.response.status_code}. Sprawdź, czy model '{self.model}' jest poprawnie załadowany.") from e
        except Exception as e: 
            logger.error(f"Nieoczekiwany błąd podczas komunikacji z AI: {e}", exc_info=True)
            raise

    def przetworz_odpowiedz(self, raw_response: str) -> ParsedAIResponse:
        """
        NOWA WERSJA: Parsuje i waliduje uproszczoną odpowiedź AI ('bias' i 'key_level').
        """
        if not raw_response:
            return ParsedAIResponse(is_valid=False)

        parsed_data = {}
        is_valid = False
        
        # Logika znajdowania i czyszczenia JSON pozostaje taka sama
        json_match = re.search(r"```json\s*([\s\S]*?)\s*```", raw_response, re.IGNORECASE)
        json_string = None
        if json_match:
            json_string = json_match.group(1).strip()
        else:
            first_brace = raw_response.find('{')
            last_brace = raw_response.rfind('}')
            if first_brace != -1 and last_brace != -1:
                json_string = raw_response[first_brace:last_brace+1]

        if json_string:
            try:
                # Parsujemy JSON
                parsed_data = json.loads(json_string)
                # Używamy nowej, prostszej metody walidacji
                if self._validate_simplified_response(parsed_data):
                    is_valid = True
                    logger.info(f"Sukces! Sparsowano i zwalidowano nową odpowiedź AI: {parsed_data}")
                else:
                    logger.warning(f"Odpowiedź AI nie przeszła walidacji. Otrzymano: {parsed_data}")

            except json.JSONDecodeError as e:
                logger.error(f"Błąd parsowania JSON: {e}")
                logger.error(f"SUROWA ODPOWIEDŹ: {raw_response}")

        return ParsedAIResponse(parsed_data=parsed_data, is_valid=is_valid)
    
    def _validate_simplified_response(self, data: Dict[str, Any]) -> bool:
        """
        Sprawdza, czy uproszczona odpowiedź AI zawiera poprawne klucze i wartości
        ('bias', 'key_level', 'confidence').
        """
        if not isinstance(data, dict):
            return False
            
        # Sprawdzamy, czy 'bias' jest poprawny
        if data.get('bias') not in ['Bullish', 'Bearish', 'Neutral']:
            return False
            
        # Sprawdzamy, czy 'key_level' jest poprawną liczbą
        key_level = data.get('key_level')
        if not isinstance(key_level, (int, float)):
            return False
            
        # Sprawdzamy, czy 'confidence' jest poprawną liczbą całkowitą w zakresie 0-10
        confidence = data.get('confidence')
        if not isinstance(confidence, int) or not (0 <= confidence <= 10):
            return False
            
        return True
    
    def _validate_setup(self, setup: Dict[str, Any], atr_value: Optional[float], support_resistance: Dict[str, List[float]]) -> Optional[Dict[str, Any]]:
        """
        WERSJA Z AUTOKOREKTĄ: Jeśli SL jest zbyt blisko S/R, próbuje go automatycznie
        poprawić, używając bufora opartego na ATR.
        """
        try:
            entry = float(setup.get('entry'))
            stop_loss = float(setup.get('stop_loss'))
            tp1 = float(setup.get('take_profit')[0])
        except (ValueError, TypeError, IndexError, AttributeError):
            # ... (ta część bez zmian)
            logger.warning("[Walidacja Setupu] Setup zawiera nieprawidłowe lub brakujące wartości liczbowe.")
            logger.error(f"BŁĘDNY SETUP OTRZYMANY OD AI: {setup}")
            return None

        # --- Logika normalizacji typu (bez zmian) ---
        # ...
        setup_type_raw = setup.get('type', '').lower()
        normalized_type = 'Long' if setup_type_raw in ['long', 'bullish', 'wzrostowy', 'kupno'] else 'Short' if setup_type_raw in ['short', 'bearish', 'spadkowy', 'sprzedaż'] else None
        if not normalized_type: return None
        setup['type'] = normalized_type

        # --- NOWA, INTELIGENTNA SEKCJA WALIDACJI SL i S/R ---
        if normalized_type == 'Long':
            support_levels = support_resistance.get('support', [])
            if not support_levels:
                logger.warning("Błąd logiczny [Long]: AI nie zdefiniowało żadnych poziomów wsparcia. Setup odrzucony.")
                return None
            
            lowest_support = min(support_levels)
            if stop_loss >= lowest_support:
                logger.warning(f"AUTOKOREKTA [Long]: SL ({stop_loss}) jest zbyt blisko wsparcia ({lowest_support}). Próbuję naprawić...")
                if atr_value and atr_value > 0:
                    # Przesuwamy SL o 25% ATR poniżej wsparcia
                    stop_loss = lowest_support - (atr_value * 0.25)
                    setup['stop_loss'] = round(stop_loss, 8) # Zapisujemy poprawiony SL
                    logger.info(f"Nowy, poprawiony SL [Long]: {setup['stop_loss']}")
                else:
                    return None # Nie możemy naprawić bez ATR
        
        elif normalized_type == 'Short':
            resistance_levels = support_resistance.get('resistance', [])
            if not resistance_levels:
                logger.warning("Błąd logiczny [Short]: AI nie zdefiniowało żadnych poziomów oporu. Setup odrzucony.")
                return None
                
            highest_resistance = max(resistance_levels)
            if stop_loss <= highest_resistance:
                logger.warning(f"AUTOKOREKTA [Short]: SL ({stop_loss}) jest zbyt blisko oporu ({highest_resistance}). Próbuję naprawić...")
                if atr_value and atr_value > 0:
                    # Przesuwamy SL o 25% ATR powyżej oporu
                    stop_loss = highest_resistance + (atr_value * 0.25)
                    setup['stop_loss'] = round(stop_loss, 8)
                    logger.info(f"Nowy, poprawiony SL [Short]: {setup['stop_loss']}")
                else:
                    return None

        # --- Walidacja R:R (teraz na potencjalnie poprawionym SL) ---
        try:
            risk = abs(entry - stop_loss)
            reward = abs(tp1 - entry)
            if risk == 0: return None
            r_r_ratio = reward / risk
            
            # --- ZMIANA: Używamy wartości z ustawień ---
            min_rr = self.settings.get('ai.min_rr_ratio', 1.5)
            if r_r_ratio < min_rr:
                logger.warning(f"Setup odrzucony. Po korekcie SL, R:R ({r_r_ratio:.2f}) jest < {min_rr}.")
                return None
            setup['r_r_ratio'] = round(r_r_ratio, 2)
        except Exception:
            return None
            
        # --- Walidacja ATR dla TP (bez zmian) ---
        if atr_value and atr_value > 0:
            # --- ZMIANA: Używamy wartości z ustawień ---
            max_tp_atr = self.settings.get('ai.validation.max_tp_to_atr_ratio', 3.0)
            if abs(tp1 - entry) > (max_tp_atr * atr_value):
                logger.warning(f"Setup odrzucony. Cel zysku jest nierealistyczny w stosunku do ATR (większy niż {max_tp_atr}x ATR).")
                return None

        return setup

    async def get_news_sentiment(self, news_titles: List[str]) -> str:
        if not news_titles:
            return "Brak wiadomości"
        
        temp_chat_history = [
            {"role": "system", "content": "Jesteś analitykiem sentymentu. Twoim zadaniem jest ocena wydźwięku nagłówków prasowych. Odpowiedz jednym słowem: Pozytywny, Negatywny lub Neutralny."},
            {"role": "user", "content": f"Oceń ogólny sentyment poniższych nagłówków:\n- " + "\n- ".join(news_titles)}
        ]
        
        payload = {"model": self.model, "messages": temp_chat_history, "max_tokens": 10, "temperature": 0.1}
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.api_url, json=payload)
                response.raise_for_status()
                json_response = response.json()
                sentiment = json_response["choices"][0]["message"]["content"].strip().capitalize()
                
                if sentiment not in ["Pozytywny", "Negatywny", "Neutralny"]:
                    return "Neutralny"
                return sentiment
        except Exception as e:
            logger.warning(f"Nie udało się uzyskać sentymentu z wiadomości: {e}")
            return "Błąd"

    async def test_connection_async(self, url: str):
        logger.info(f"Testowanie połączenia z {url}...")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url) 
                response.raise_for_status()
                logger.info(f"Test połączenia z {url} zakończony sukcesem (status: {response.status_code}).")
        except httpx.ConnectError as e:
            logger.error(f"Błąd połączenia (ConnectError) podczas testu {url}: {e}")
            raise ConnectionError(f"Błąd połączenia z serwerem: {e.request.url}. Sprawdź adres i firewall.") from e
        except httpx.Timeout as e:
            logger.error(f"Timeout podczas testu {url}: {e}")
            raise TimeoutError("Serwer nie odpowiedział na czas (10s).") from e
        except Exception as e:
            logger.error(f"Nieoczekiwany błąd podczas testu połączenia z {url}: {e}", exc_info=True)
            raise
import httpx
import logging
import json
import re
from typing import Tuple, Dict, Any, Optional, List
from dataclasses import dataclass, field

from core.settings_manager import SettingsManager
from core.prompt_templates import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

@dataclass
class ParsedAIResponse:
    parsed_data: Dict[str, Any] = field(default_factory=dict)
    is_valid: bool = False

class AIClient:
    def __init__(self, settings: SettingsManager):
        self.settings = settings; self.chat_history = []; self.update_config()
        self._system_prompt_content = SYSTEM_PROMPT

    def update_config(self):
        self.api_url = self.settings.get("ai.url"); self.model = self.settings.get("ai.model")
        self.timeout = self.settings.get("ai.timeout", 120)

    def clear_chat_history(self): self.chat_history = []
    def add_message(self, role: str, content: str): self.chat_history.append({"role": role, "content": content})

    async def get_chat_completion_async(self) -> Optional[str]:
        if not self.chat_history or not self.api_url: return None
        messages_with_system = [{"role": "system", "content": self._system_prompt_content}] + self.chat_history
        payload = {"model": self.model, "messages": messages_with_system, "max_tokens": self.settings.get("ai.max_tokens"), "temperature": self.settings.get("ai.temperature"), "stream": False}
        timeout_config = httpx.Timeout(float(self.timeout), connect=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                response = await client.post(self.api_url, json=payload); response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"]
        except httpx.ConnectError as e: raise ConnectionError(f"Błąd połączenia z serwerem AI: {e.request.url}.") from e
        except Exception as e: logger.error(f"Nieoczekiwany błąd podczas komunikacji z AI: {e}", exc_info=True); raise

    def przetworz_odpowiedz(self, raw_response: str, mode: str = 'tactician') -> ParsedAIResponse:
        if not raw_response: return ParsedAIResponse(is_valid=False)
        json_match = re.search(r"```json\s*([\s\S]*?)\s*```", raw_response, re.IGNORECASE)
        json_string = json_match.group(1).strip() if json_match else None
        if not json_string:
            first_brace = raw_response.find('{'); last_brace = raw_response.rfind('}')
            if first_brace != -1 and last_brace != -1: json_string = raw_response[first_brace:last_brace+1]
        if json_string:
            try:
                parsed_data = json.loads(json_string)
                is_valid = False
                if mode == 'tp_reviewer': 
                    is_valid = self._validate_tp_reviewer_response(parsed_data)
                elif mode == 'risk_validator': 
                    is_valid = self._validate_risk_response(parsed_data)
                else:
                    is_valid = self._validate_simplified_response(parsed_data)
                if is_valid: logger.info(f"Sukces! Sparsowano i zwalidowano ({mode}) odpowiedź AI: {parsed_data}")
                else: logger.warning(f"Odpowiedź AI ({mode}) nie przeszła walidacji. Otrzymano: {parsed_data}")
                return ParsedAIResponse(parsed_data=parsed_data, is_valid=is_valid)
            except json.JSONDecodeError as e:
                logger.error(f"Błąd parsowania JSON: {e} | SUROWA ODPOWIEDŹ: {raw_response}")
        return ParsedAIResponse(parsed_data={}, is_valid=False)
    
    def _validate_simplified_response(self, data: Dict[str, Any]) -> bool:
        if not isinstance(data, dict): return False
        if not isinstance(data.get('key_conclusions'), str) or not data.get('key_conclusions').strip():
            return False
        

        if not isinstance(data.get('key_level'), (int, float)): return False
        if not isinstance(data.get('confidence'), int) or not (0 <= data.get('confidence') <= 10): return False
        return True
        
    def _validate_tp_reviewer_response(self, data: Dict[str, Any]) -> bool:
        """Sprawdza, czy odpowiedź od Recenzenta TP (słownik ocen) jest poprawna."""
        if not isinstance(data, dict) or not data:
            return False
        # Sprawdzamy, czy wszystkie klucze to stringi, a wartości to liczby całkowite
        for key, value in data.items():
            if not isinstance(key, str) or not isinstance(value, int):
                return False
        return True
    
    async def test_connection_async(self, url: str):
        logger.info(f"Testowanie połączenia z {url}...")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url); response.raise_for_status()
                logger.info(f"Test połączenia z {url} zakończony sukcesem (status: {response.status_code}).")
        except httpx.ConnectError as e: raise ConnectionError(f"Błąd połączenia z serwerem: {e.request.url}.") from e
        except Exception as e: logger.error(f"Nieoczekiwany błąd podczas testu połączenia z {url}: {e}", exc_info=True); raise

    def _validate_risk_response(self, data: Dict[str, Any]) -> bool:
        if not isinstance(data, dict): return False
        if not isinstance(data.get('key_conclusions'), str) or not data.get('key_conclusions').strip(): return False
        if not isinstance(data.get('sl_percent_distance'), (int, float)) or not (0 < data.get('sl_percent_distance') < 20): return False
        if not isinstance(data.get('confidence'), int) or not (0 <= data.get('confidence') <= 10): return False
        return True
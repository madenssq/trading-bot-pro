# Plik: core/settings_manager.py (WERSJA ZE ZMIENNYMI ŚRODOWISKOWYMI)

import json
import os
import logging
from typing import Any, Dict
import collections.abc

from app_config import DEFAULT_SETTINGS, USER_SETTINGS_FILE

logger = logging.getLogger(__name__)

def deep_update(d: Dict, u: Dict) -> Dict:
    for k, v in u.items():
        if isinstance(v, collections.abc.Mapping):
            d[k] = deep_update(d.get(k, {}), v)
        else:
            d[k] = v
    return d

class SettingsManager:
    def __init__(self, settings_file: str = USER_SETTINGS_FILE):
        self.settings_file = settings_file
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        self.settings = self._load_settings()
        self._load_secrets_from_env() # <-- NOWY KROK

    def _load_settings(self) -> Dict:
        settings = DEFAULT_SETTINGS.copy()
        if not os.path.exists(self.settings_file):
            logger.info("Plik ustawień nie istnieje. Używam i zapisuję domyślne.")
            self.save_settings(settings)
            return settings
        try:
            with open(self.settings_file, 'r', encoding='utf-8') as f:
                saved_settings = json.load(f)
                settings = deep_update(settings, saved_settings)
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Nie udało się wczytać pliku ustawień ({self.settings_file}): {e}. Używam domyślnych.")
            return DEFAULT_SETTINGS.copy()
        return settings
    
    def _load_secrets_from_env(self):
       
        secrets_map = {
            "TELEGRAM_API_TOKEN": "telegram.api_token",
            "TELEGRAM_CHAT_ID": "telegram.chat_id",
            "CRYPTOPANIC_API_TOKEN": "cryptopanic.api_token" 
        }
        
        logger.info("Sprawdzanie zmiennych środowiskowych dla kluczy API...")
        for env_key, settings_path in secrets_map.items():
            value = os.getenv(env_key)
            if value:
                self.set(settings_path, value)
                logger.info(f"Załadowano '{settings_path}' ze zmiennej środowiskowej.")

    def save_settings(self, settings_to_save: Dict = None) -> bool:
        data_to_save = settings_to_save if settings_to_save is not None else self.settings
        try:
            # Usuwamy sekrety przed zapisem, aby nie trafiły do pliku
            if 'telegram' in data_to_save:
                data_to_save['telegram']['api_token'] = ""
                data_to_save['telegram']['chat_id'] = ""
            if 'cryptopanic' in data_to_save:
                 data_to_save['cryptopanic']['api_token'] = ""

            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, indent=4, ensure_ascii=False)
            logger.info(f"Ustawienia zostały zapisane w pliku: {self.settings_file}")
            return True
        except IOError as e:
            logger.error(f"Nie udało się zapisać ustawień do pliku: {e}")
            return False

    def get(self, key_path: str, default: Any = None) -> Any:
        try:
            keys = key_path.split('.')
            value = self.settings
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default

    def set(self, key_path: str, value: Any):
        keys = key_path.split('.')
        d = self.settings
        for key in keys[:-1]:
            d = d.setdefault(key, {})
        d[keys[-1]] = value
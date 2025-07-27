
from abc import ABC, abstractmethod
import pandas as pd
# --- NOWY IMPORT ---
from core.settings_manager import SettingsManager

class Strategy(ABC):
    """
    Abstrakcyjna klasa bazowa dla wszystkich strategii.
    """
    # --- ZMIANA: Dodajemy settings_manager do konstruktora ---
    def __init__(self, broker, data, settings_manager: SettingsManager):
        self._broker = broker
        self.data = data
        self.settings = settings_manager # Zapisujemy dostęp do ustawień
        self.i = 0

    def I(self, indicator_func, *args, **kwargs):
        """
        Metoda pomocnicza, która wywołuje funkcję wskaźnika,
        przekazując jej potrzebne kolumny z danych.
        """
        indicator_series = indicator_func(
            high=self.data.High,
            low=self.data.Low,
            close=self.data.Close,
            volume=self.data.Volume,
            *args, **kwargs
        )
        self.data[indicator_series.name] = indicator_series
        return self.data[indicator_series.name]

    @abstractmethod
    def init(self):
        """Metoda do inicjalizacji wskaźników."""
        pass

    @abstractmethod
    def next(self):
        """Główna metoda strategii, wywoływana dla każdej świecy."""
        pass
from abc import ABC, abstractmethod
import pandas as pd

class Strategy(ABC):
    """
    Abstrakcyjna klasa bazowa dla wszystkich strategii.
    """
    def __init__(self, broker, data):
        self._broker = broker
        self.data = data # Używamy publicznego atrybutu 'data'
        self.i = 0

    def I(self, indicator_func, *args, **kwargs):
        """
        Metoda pomocnicza, która wywołuje funkcję wskaźnika,
        przekazując jej potrzebne kolumny z danych.
        """
        # POPRAWKA: Używamy self.data (bez podkreślnika)
        indicator_series = indicator_func(
            high=self.data.High,
            low=self.data.Low,
            close=self.data.Close,
            volume=self.data.Volume,
            *args, **kwargs
        )
        # Zapisujemy wskaźnik z powrotem do głównej ramki danych
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

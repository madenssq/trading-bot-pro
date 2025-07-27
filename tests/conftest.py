import sys
import os
import pytest
from core.database_manager import DatabaseManager

# Dodaj główny katalog projektu do ścieżki Pythona
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

@pytest.fixture
def db_manager():
    """
    Fixtura, która tworzy i udostępnia instancję DatabaseManager
    działającą na tymczasowej bazie danych w pamięci RAM.
    """
    # Tworzymy managera połączonego z bazą w pamięci (:memory:)
    manager = DatabaseManager(db_name=":memory:")
    
    # 'yield' przekazuje managera do testu
    yield manager
    
    # Kod poniżej 'yield' jest wykonywany PO zakończeniu testu (sprzątanie)
    manager.close()
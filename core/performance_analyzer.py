import logging
import pandas as pd
from core.database_manager import DatabaseManager

logger = logging.getLogger(__name__)

class PerformanceAnalyzer:
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def get_performance_insights(self) -> str:
        """
        Analizuje dziennik, oblicza kluczowe statystyki i generuje wnioski dla AI.
        """
        # Pobieramy tylko wpisy, które są setupami transakcyjnymi
        all_setups = self.db_manager.get_all_trades(filters={'entry_type': 'SETUP'})
        
        # Potrzebujemy rozsądnej próbki, aby statystyki miały sens
        if len(all_setups) < 10: 
            return "Brak wystarczających danych historycznych do wyciągnięcia wniosków."

        # Konwertujemy na DataFrame - to najlepsze narzędzie do takich analiz
        df = pd.DataFrame(all_setups)
        
        # Bierzemy pod uwagę tylko zakończone transakcje
        closed_trades = df[df['result'].isin(['TP_HIT', 'SL_HIT'])].copy()
        if len(closed_trades) < 5:
            return "Brak wystarczających danych o zakończonych transakcjach."

        insights = []

        # --- Analiza #1: Skuteczność Long vs Short ---
        # Używamy apply, aby bezpiecznie obliczyć win rate dla każdej grupy
        def calculate_win_rate(group):
            wins = (group['result'] == 'TP_HIT').sum()
            total = len(group)
            return (wins / total) * 100 if total > 0 else 0

        win_rate_by_type = closed_trades.groupby('type').apply(calculate_win_rate)

        if 'Long' in win_rate_by_type:
            insights.append(f"Historyczna skuteczność setupów 'Long' wynosi {win_rate_by_type['Long']:.1f}%.")
        if 'Short' in win_rate_by_type:
            insights.append(f"Historyczna skuteczność setupów 'Short' wynosi {win_rate_by_type['Short']:.1f}%.")
            
        # --- Tutaj w przyszłości możemy dodać więcej analiz (np. po confidence, po reżimie rynku) ---

        if not insights:
            return "Nie udało się wygenerować żadnych wniosków z analizy."

        final_string = "Wnioski z Autorefleksji: " + " ".join(insights)
        logger.info(f"Wygenerowano wnioski z analizy performance: {final_string}")
        return final_string
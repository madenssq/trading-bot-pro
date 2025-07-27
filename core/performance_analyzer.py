import logging
import pandas as pd
from core.database_manager import DatabaseManager

logger = logging.getLogger(__name__)

class PerformanceAnalyzer:
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def get_performance_insights(self) -> str:
        """
        Analizuje dziennik, oblicza kluczowe statystyki w podgrupach
        i generuje szczegółowe wnioski dla AI.
        """
        all_setups = self.db_manager.get_all_trades(filters={'entry_type': 'SETUP'})
        
        if len(all_setups) < 10: 
            return "Brak wystarczających danych historycznych do wyciągnięcia wniosków."

        df = pd.DataFrame(all_setups)
        
        # Bierzemy pod uwagę tylko zakończone transakcje, które mają zdefiniowany reżim rynkowy
        closed_trades = df[
            df['result'].isin(['TP_HIT', 'SL_HIT']) & 
            df['market_regime'].notna()
        ].copy()
        
        if len(closed_trades) < 5:
            return "Brak wystarczających danych o zakończonych transakcjach z określonym reżimem rynkowym."

        insights = []

        # --- NOWA, ZAAWANSOWANA ANALIZA ---
        # Grupujemy transakcje po typie (Long/Short) ORAZ po reżimie rynkowym
        
        def calculate_win_rate(group):
            if len(group) < 3: # Ignorujemy grupy z małą próbką
                return None
            wins = (group['result'] == 'TP_HIT').sum()
            total = len(group)
            return (wins / total) * 100

        # Obliczamy skuteczność dla każdej podgrupy
        win_rate_by_group = closed_trades.groupby(['type', 'market_regime']).apply(calculate_win_rate)

        # Formatujemy wyniki w czytelny tekst
        for (trade_type, regime), win_rate in win_rate_by_group.items():
            if win_rate is not None:
                insight_text = (
                    f"Historyczna skuteczność dla typu '{trade_type}' "
                    f"w reżimie '{regime}' wynosi {win_rate:.1f}% "
                    f"(na podstawie {len(closed_trades.loc[(closed_trades.type == trade_type) & (closed_trades.market_regime == regime)])} transakcji)."
                )
                insights.append(insight_text)
        
        if not insights:
            return "Nie udało się wygenerować żadnych szczegółowych wniosków z analizy."

        final_string = "Wnioski z Autorefleksji: " + " ".join(insights)
        logger.info(f"Wygenerowano szczegółowe wnioski z analizy performance: {final_string}")
        return final_string

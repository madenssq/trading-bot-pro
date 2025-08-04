import logging
import pandas as pd
from core.database_manager import DatabaseManager

logger = logging.getLogger(__name__)

class PerformanceAnalyzer:
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def get_performance_insights(self) -> str:
        """Analizuje dziennik i generuje szczegółowe wnioski dla AI."""
        all_setups = self.db_manager.get_all_trades(filters={'entry_type': 'SETUP'})
        if len(all_setups) < 10: return "Brak wystarczających danych historycznych."

        df = pd.DataFrame(all_setups)
        closed_trades = df[
            df['result'].isin(['TP_HIT', 'SL_HIT', 'BREAK_EVEN']) & 
            df['market_regime'].notna()
        ].copy()
        
        if len(closed_trades) < 5: return "Brak wystarczających danych o zakończonych transakcjach."

        insights = []
        
        def calculate_win_rate(group):
            if len(group) < 3: return None
            # --- POPRAWKA: Zliczamy TP_HIT i BREAK_EVEN jako wygrane ---
            wins = group['result'].isin(['TP_HIT', 'BREAK_EVEN']).sum()
            total = len(group)
            return (wins / total) * 100

        win_rate_by_group = closed_trades.groupby(['type', 'market_regime']).apply(calculate_win_rate)

        for (trade_type, regime), win_rate in win_rate_by_group.items():
            if win_rate is not None:
                insight_text = (
                    f"Historyczna skuteczność dla typu '{trade_type}' "
                    f"w reżimie '{regime}' wynosi {win_rate:.1f}% "
                    f"(na podstawie {len(closed_trades.loc[(closed_trades.type == trade_type) & (closed_trades.market_regime == regime)])} transakcji)."
                )
                insights.append(insight_text)
        
        if not insights: return "Nie udało się wygenerować szczegółowych wniosków z analizy."

        return "Wnioski z Autorefleksji: " + " ".join(insights)
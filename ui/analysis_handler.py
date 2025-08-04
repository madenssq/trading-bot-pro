import asyncio
import logging
import pandas as pd
import json
from typing import Callable, Dict, List, Optional

from PyQt6.QtWidgets import QMessageBox

from core.ai_client import AIClient, ParsedAIResponse
from core.analyzer import AnalysisResult, TechnicalAnalyzer
from core.indicator_service import IndicatorKeyGenerator
from core.news_client import CryptoPanicClient
from core.performance_analyzer import PerformanceAnalyzer
from core.database_manager import DatabaseManager
from core.ai_pipeline import AIPipeline
from core.performance_analyzer import PerformanceAnalyzer
from core.database_manager import DatabaseManager
from core.prompt_templates import (OBSERVER_PROMPT_TEMPLATE,
                                   STRATEGIST_PROMPT_TEMPLATE,
                                   TACTICIAN_PROMPT_TEMPLATE)

logger = logging.getLogger(__name__)

class AnalysisHandler:
    def __init__(self,
                 analyzer: TechnicalAnalyzer,
                 ai_client: AIClient,
                 performance_analyzer: PerformanceAnalyzer,
                 news_client: Optional[CryptoPanicClient],
                 db_manager: DatabaseManager,
                 status_callback: Callable[[str], None],
                 display_callback: Callable,
                 ai_pipeline: 'AIPipeline',
                 parent_widget=None):

        self.analyzer = analyzer
        self.ai_client = ai_client
        self.performance_analyzer = performance_analyzer
        self.news_client = news_client
        self.db_manager = db_manager
        self.update_status = status_callback
        self.display_results = display_callback
        self.ai_pipeline = ai_pipeline
        self.parent_widget = parent_widget

    async def run_full_analysis(self, symbol: str, interval: str, exchange: str,):
        try:
            # --- NOWY BLOK TRY...EXCEPT WOKÓŁ GŁÓWNEGO WYWOŁANIA ---
            parsed_response, analysis_result, best_timeframe, context_data = await self.ai_pipeline.run(
                symbol, interval, exchange, self.update_status
            )

            if not parsed_response or not analysis_result:
                # Ten komunikat może się pojawić, jeśli AI nie zwróciło poprawnego JSONa
                QMessageBox.warning(self.parent_widget, "Błąd Analizy", "Nie udało się uzyskać poprawnej odpowiedzi od AI lub pobrać danych. Sprawdź logi po więcej informacji.")
                return

            # Zapisujemy tylko ogólną analizę, setup jest już zalogowany w pipeline
            analysis_log_data = {"symbol": symbol, "interval": best_timeframe, "exchange": exchange}
            self.db_manager.log_analysis(analysis_log_data)

            # Po prostu wyświetlamy wyniki, które otrzymaliśmy z pipeline
            tactician_inputs = self.analyzer.prepare_tactician_inputs(analysis_result, best_timeframe, symbol)
            self.display_results(
                ohlcv_df=analysis_result.all_ohlcv_dfs.get(best_timeframe),
                all_timeframe_data=analysis_result.all_timeframe_data,
                parsed_data=parsed_response.parsed_data,
                raw_ai_response="",
                context_text="",
                visualization_data=[],
                fvgs=self.analyzer.pattern_service.find_fair_value_gaps(analysis_result.all_ohlcv_dfs.get(best_timeframe)),
                all_ohlcv_dfs=analysis_result.all_ohlcv_dfs,
                fib_data=json.loads(tactician_inputs.get('fibonacci_data', '{}'))
            )

        # --- NOWA SEKCJA OBSŁUGI BŁĘDÓW ---
        except (ConnectionError, TimeoutError) as e:
            # Łapiemy błędy połączenia i timeoutu rzucone przez AIClient
            logger.critical(f"Błąd połączenia z AI podczas analizy: {e}")
            # Wyświetlamy CZYTELNY komunikat w UI
            QMessageBox.critical(self.parent_widget, "Błąd Połączenia z AI", str(e))
        except Exception as e:
            logger.critical(f"Krytyczny błąd w AnalysisHandler: {e}", exc_info=True)
            QMessageBox.critical(self.parent_widget, "Błąd Krytyczny Analizy", f"Wystąpił nieoczekiwany błąd:\n{e}")
        finally:
            # Ta linijka ZAWSZE się wykona, odblokowując UI
            self.update_status("Czuwanie...", False)

    # NOWA METODA POMOCNICZA
    def _format_data_for_prompt(self, data_dict: Dict) -> str:
        """Formatuje słownik z danymi technicznymi na czytelny tekst dla AI."""
        parts = []
        for interval, data in data_dict.items():
            if not data or not data.get('interpreted'): continue
            parts.append(f"\n# Interwał: {interval}")
            for key, value in data['interpreted'].items():
                parts.append(f"- {key}: {value.get('text', 'Brak danych') if isinstance(value, dict) else value}")
        return "\n".join(parts) if parts else "Brak danych technicznych."
    
    
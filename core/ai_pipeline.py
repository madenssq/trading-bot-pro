import asyncio
import logging
import re
import json
import time
import pandas as pd
from typing import Tuple, Optional, Dict

from core.analyzer import TechnicalAnalyzer, AnalysisResult
from core.indicator_service import IndicatorKeyGenerator
from core.ai_client import AIClient, ParsedAIResponse
from core.database_manager import DatabaseManager
from core.performance_analyzer import PerformanceAnalyzer
from core.prompt_templates import OBSERVER_PROMPT_TEMPLATE, TACTICIAN_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)

class AIPipeline:
    def __init__(self, analyzer: TechnicalAnalyzer, ai_client: AIClient, db_manager: DatabaseManager, performance_analyzer: PerformanceAnalyzer):
        self.analyzer = analyzer
        self.ai_client = ai_client
        self.db_manager = db_manager
        self.performance_analyzer = performance_analyzer
        self.analysis_locks: Dict[Tuple[str, str], asyncio.Lock] = {}

    async def run(self, symbol: str, interval: str, exchange: str, status_callback: callable) -> Tuple[Optional[ParsedAIResponse], Optional[AnalysisResult], str, Dict]:
        """
        Uruchamia pełną, scentralizowaną sekwencję analizy AI z blokadą per-symbol,
        aby zapobiec wyścigom i duplikatom.
        """
        lock_key = (symbol, interval)
        if lock_key not in self.analysis_locks:
            self.analysis_locks[lock_key] = asyncio.Lock()
        
        lock = self.analysis_locks[lock_key]

        if lock.locked():
            logger.warning(f"[{symbol}] Analiza jest już w toku. Pomijanie zduplikowanego żądania.")
            return None, None, interval, {}

        async with lock:
            context_data = {
                "market_regime": "N/A", "momentum_status": "N/A",
                "order_flow_status": "N/A", "onchain_text": "N/A",
                "performance_insights": "N/A", "golden_examples_text": "N/A"
            }
            
            try:
                # Etap 1-4: Zbieranie danych i analiza AI
                status_callback(f"Pobieranie danych dla {symbol}...", True)
                analysis_result: AnalysisResult = await self.analyzer.get_analysis_data(symbol, interval, exchange)
                if not analysis_result.is_successful: return None, None, interval, context_data
                
                status_callback("Agent 1 (Obserwator): Wybór interwału...", True)
                observer_prompt = OBSERVER_PROMPT_TEMPLATE.format(technical_data_section=self.analyzer._format_data_for_prompt(analysis_result.all_timeframe_data))
                self.ai_client.clear_chat_history()
                self.ai_client.add_message("user", observer_prompt)
                observer_response = (await self.ai_client.get_chat_completion_async() or interval).strip()
                timeframe_match = re.search(r'\b(\d{1,2}[hdwm])\b', observer_response, re.IGNORECASE)
                best_timeframe = timeframe_match.group(1).lower() if timeframe_match else interval
                
                if self.analyzer.settings.get('ai_context_modules.use_market_regime'):
                    context_data['market_regime'] = await self.analyzer.context_service.get_market_regime(exchange)
                    context_data['momentum_status'] = await self.analyzer.context_service.get_market_momentum_status(symbol, exchange)
                if self.analyzer.settings.get('ai_context_modules.use_order_flow'):
                    context_data['order_flow_status'] = await self.analyzer.context_service.analyze_order_flow_strength(symbol, exchange)
                if self.analyzer.settings.get('ai_context_modules.use_onchain_data'):
                    onchain_context = await self.analyzer.context_service.get_onchain_context(symbol, exchange)
                    context_data['onchain_text'] = f"Funding Rate: {onchain_context.get('funding_rate', 'N/A')}, Open Interest: {onchain_context.get('open_interest_usd', 'N/A')}"
                if self.analyzer.settings.get('ai_context_modules.use_performance_insights'):
                    context_data['performance_insights'] = self.performance_analyzer.get_performance_insights()
                
                golden_examples = self.db_manager.get_golden_setups(limit=3)
                context_data['golden_examples_text'] = self.analyzer._format_golden_examples_for_prompt(golden_examples)
                
                status_callback(f"Agent 3 (Taktyk): Szukanie setupu na {best_timeframe}...", True)
                tactician_inputs = self.analyzer.prepare_tactician_inputs(analysis_result, best_timeframe, symbol)
                tactician_inputs.update({
                    "timeframe": best_timeframe,
                    "single_tf_data_section": self.analyzer._format_data_for_prompt(analysis_result.all_timeframe_data.get(best_timeframe, {})),
                    "current_price": self.analyzer._round_price_for_ai(analysis_result.current_price),
                    "performance_insights_section": context_data['performance_insights'],
                    "onchain_data_section": context_data['onchain_text'],
                    "market_regime": context_data['market_regime'],
                    "momentum_status": context_data['momentum_status'],
                    "order_flow_status": context_data['order_flow_status'],
                    "golden_examples_section": context_data['golden_examples_text']
                })
                tactician_prompt = TACTICIAN_PROMPT_TEMPLATE.format(**tactician_inputs)
                
                self.ai_client.clear_chat_history()
                self.ai_client.add_message("user", tactician_prompt)
                final_response_text = await self.ai_client.get_chat_completion_async()
                parsed_response = self.ai_client.przetworz_odpowiedz(final_response_text)
                
                programmatic_sr_str = tactician_inputs.get("programmatic_sr_json", "{}")
                try:
                    parsed_response.parsed_data['support_resistance'] = json.loads(programmatic_sr_str)
                except json.JSONDecodeError:
                    parsed_response.parsed_data['support_resistance'] = {"support": [], "resistance": []}

                # Etap 5: Scentralizowany "Konstruktor Setupu" i logowanie
                if parsed_response.is_valid and parsed_response.parsed_data.get('bias') in ['Bullish', 'Bearish']:
                    ai_reco = parsed_response.parsed_data
                    strategy_params = self.analyzer.settings.get('strategies.ai_clone', {})
                    atr_multiplier = strategy_params.get('atr_multiplier_sl', 1.5)
                    
                    best_df = analysis_result.all_ohlcv_dfs.get(best_timeframe)
                    if best_df is None or best_df.empty:
                        logger.warning(f"Brak danych OHLCV dla wybranego interwału {best_timeframe}, nie można skonstruować setupu.")
                        return parsed_response, analysis_result, best_timeframe, context_data
                        
                    best_df_with_indicators = self.analyzer.indicator_service.calculate_all(best_df.copy())
                    atr_key_generator = IndicatorKeyGenerator(self.analyzer.settings.get('analysis.indicator_params'))
                    atr_key = atr_key_generator.atr()
                    
                    last_atr = 0
                    if atr_key in best_df_with_indicators.columns and pd.notna(best_df_with_indicators[atr_key].iloc[-1]):
                        last_atr = best_df_with_indicators[atr_key].iloc[-1]

                    if last_atr > 0:
                        entry_price = ai_reco['key_level']
                        trade_type = 'Long' if ai_reco['bias'] == 'Bullish' else 'Short'
                        stop_loss = entry_price - (last_atr * atr_multiplier) if trade_type == 'Long' else entry_price + (last_atr * atr_multiplier)
                        
                        risk_amount = abs(entry_price - stop_loss)
                        rr_ratio_tp1 = strategy_params.get('risk_reward_ratio_tp1', 1.5)
                        rr_ratio_tp2 = strategy_params.get('risk_reward_ratio_tp2', 3.0)
                        
                        take_profit_1 = entry_price + (risk_amount * rr_ratio_tp1) if trade_type == 'Long' else entry_price - (risk_amount * rr_ratio_tp1)
                        take_profit = entry_price + (risk_amount * rr_ratio_tp2) if trade_type == 'Long' else entry_price - (risk_amount * rr_ratio_tp2)
                        
                        calculated_rr_tp2 = (abs(take_profit - entry_price) / risk_amount) if risk_amount > 0 else 0
                        min_rr_ratio = self.ai_client.settings.get('ai.min_rr_ratio', 2.0)

                        if calculated_rr_tp2 >= min_rr_ratio:
                            setup_data = {
                                "status": "potential", "type": trade_type,
                                "trigger_text": f"Obserwuj reakcję ceny w pobliżu kluczowego poziomu {entry_price:.4f}",
                                "entry": entry_price, "stop_loss": stop_loss, "take_profit": [take_profit],
                                "take_profit_1": take_profit_1, "confidence": ai_reco['confidence'], "r_r_ratio": calculated_rr_tp2
                            }
                            parsed_response.parsed_data['setup'] = setup_data
                            
                            trade_log_data = {
                                "timestamp": time.time(), "symbol": symbol, "interval": best_timeframe,
                                "type": trade_type, "confidence": ai_reco['confidence'],
                                "market_regime": context_data.get('market_regime'),
                                "momentum_status": context_data.get('momentum_status'),
                                "entry_price": entry_price, "stop_loss": stop_loss,
                                "take_profit": take_profit, "take_profit_1": take_profit_1,
                                "exchange": exchange,
                                "full_ai_response_json": json.dumps(parsed_response.parsed_data)
                            }
                            
                            if not self.db_manager.does_trade_exist(trade_log_data):
                                self.db_manager.log_trade(trade_log_data)
                
                return parsed_response, analysis_result, best_timeframe, context_data

            except Exception as e:
                logger.critical(f"Krytyczny błąd w AIPipeline dla {symbol}: {e}", exc_info=True)
                return None, None, interval, context_data
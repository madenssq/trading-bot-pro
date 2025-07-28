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
from core.prompt_templates import OBSERVER_PROMPT_TEMPLATE, TACTICIAN_PROMPT_TEMPLATE, DEVILS_ADVOCATE_PROMPT_TEMPLATE, CONTRARIAN_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)

class AIPipeline:
    def __init__(self, analyzer: TechnicalAnalyzer, ai_client: AIClient, db_manager: DatabaseManager, performance_analyzer: PerformanceAnalyzer):
        self.analyzer = analyzer
        self.ai_client = ai_client
        self.db_manager = db_manager
        self.performance_analyzer = performance_analyzer
        self.analysis_locks: Dict[Tuple[str, str], asyncio.Lock] = {}

    async def run(self, symbol: str, interval: str, exchange: str, status_callback: callable) -> Tuple[Optional[ParsedAIResponse], Optional[AnalysisResult], str, Dict]:
        lock_key = (symbol, interval)
        if lock_key not in self.analysis_locks:
            self.analysis_locks[lock_key] = asyncio.Lock()
        
        lock = self.analysis_locks[lock_key]

        if lock.locked():
            return None, None, interval, {}

        async with lock:
            context_data = {"devils_advocate_argument": "Brak kontrargumentu."}
            
            try:
                # Etap 1: Zbieranie Danych
                status_callback(f"Pobieranie danych dla {symbol}...", True)
                analysis_result: AnalysisResult = await self.analyzer.get_analysis_data(symbol, interval, exchange)
                if not analysis_result.is_successful: return None, None, interval, context_data
                
                # Etap 2: Agent Obserwator
                status_callback("Agent 1 (Obserwator): Wybór interwału...", True)
                observer_prompt = OBSERVER_PROMPT_TEMPLATE.format(technical_data_section=self.analyzer._format_data_for_prompt(analysis_result.all_timeframe_data))
                self.ai_client.clear_chat_history(); self.ai_client.add_message("user", observer_prompt)
                observer_response = (await self.ai_client.get_chat_completion_async() or interval).strip()
                timeframe_match = re.search(r'\b(\d{1,2}[hdwm])\b', observer_response, re.IGNORECASE)
                best_timeframe = timeframe_match.group(1).lower() if timeframe_match else interval
                
                # Etap 3: Agent Adwokat Diabła
                status_callback("Agent 2 (Adwokat Diabła): Szukanie kontrargumentów...", True)
                advocate_inputs = self.analyzer.prepare_tactician_inputs(analysis_result, best_timeframe, symbol)
                market_regime = await self.analyzer.context_service.get_market_regime(exchange)
                advocate_inputs.update({
                    "timeframe": best_timeframe, "current_price": analysis_result.current_price,
                    "market_regime": market_regime,
                    "order_flow_status": await self.analyzer.context_service.analyze_order_flow_strength(symbol, exchange),
                    "bias_suggestion": "wzrostowy" if "RYNEK_BYKA" in market_regime else "spadkowy"
                })
                advocate_prompt = DEVILS_ADVOCATE_PROMPT_TEMPLATE.format(**advocate_inputs)
                self.ai_client.clear_chat_history(); self.ai_client.add_message("user", advocate_prompt)
                context_data["devils_advocate_argument"] = await self.ai_client.get_chat_completion_async() or "Brak."

                # Etap 4: Agent Taktyk
                status_callback(f"Agent 3 (Taktyk): Wydawanie werdyktu dla {best_timeframe}...", True)
                tactician_inputs = self.analyzer.prepare_tactician_inputs(analysis_result, best_timeframe, symbol)
                tactician_inputs.update({
                    "timeframe": best_timeframe,
                    "current_price": self.analyzer._round_price_for_ai(analysis_result.current_price),
                    "performance_insights_section": self.performance_analyzer.get_performance_insights(),
                    "onchain_data_section": "N/A", "market_regime": market_regime,
                    "momentum_status": await self.analyzer.context_service.get_market_momentum_status(symbol, exchange),
                    "order_flow_status": advocate_inputs['order_flow_status'],
                    "devils_advocate_argument": context_data["devils_advocate_argument"]
                })
                tactician_prompt = TACTICIAN_PROMPT_TEMPLATE.format(**tactician_inputs)
                self.ai_client.clear_chat_history(); self.ai_client.add_message("user", tactician_prompt)
                final_response_text = await self.ai_client.get_chat_completion_async()
                parsed_response = self.ai_client.przetworz_odpowiedz(final_response_text)

                # Etap 4.5: Agent Kontrarian (jeśli potrzebny)
                if parsed_response.is_valid and parsed_response.parsed_data.get('bias') == 'Neutral':
                    status_callback("Agent 4 (Kontrarian): Szukanie okazji 'pod prąd'...", True)
                    contrarian_inputs = advocate_inputs
                    contrarian_prompt = CONTRARIAN_PROMPT_TEMPLATE.format(**contrarian_inputs)
                    self.ai_client.clear_chat_history(); self.ai_client.add_message("user", contrarian_prompt)
                    contrarian_response_text = await self.ai_client.get_chat_completion_async()
                    
                    if contrarian_response_text and "BRAK" not in contrarian_response_text.upper():
                        logger.info(f"[{symbol}] Agent Kontrarian znalazł potencjalny setup!")
                        parsed_response = self.ai_client.przetworz_odpowiedz(contrarian_response_text)

                # --- UZUPEŁNIONY ELEMENT: Dodanie S/R do KAŻDEJ odpowiedzi ---
                programmatic_sr_str = tactician_inputs.get("programmatic_sr_json", "{}")
                try:
                    parsed_response.parsed_data['support_resistance'] = json.loads(programmatic_sr_str)
                except json.JSONDecodeError:
                    parsed_response.parsed_data['support_resistance'] = {"support": [], "resistance": []}

                # Etap 5: Konstruktor Setupu
                if parsed_response.is_valid and parsed_response.parsed_data.get('bias') in ['Bullish', 'Bearish']:
                    ai_reco = parsed_response.parsed_data
                    strategy_params = self.analyzer.settings.get('strategies.ai_clone', {})
                    atr_multiplier = strategy_params.get('atr_multiplier_sl', 1.5)
                    
                    best_df = analysis_result.all_ohlcv_dfs.get(best_timeframe)
                    if best_df is None or best_df.empty: return parsed_response, analysis_result, best_timeframe, context_data
                    
                    best_df_with_indicators = self.analyzer.indicator_service.calculate_all(best_df.copy())
                    atr_key = IndicatorKeyGenerator(self.analyzer.settings.get('analysis.indicator_params')).atr()
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
                        
                        min_rr_ratio = self.ai_client.settings.get('ai.min_rr_ratio', 2.0)
                        if (abs(take_profit - entry_price) / risk_amount if risk_amount > 0 else 0) >= min_rr_ratio:
                            parsed_response.parsed_data['setup'] = {"status": "potential", "type": trade_type, "entry": entry_price, "stop_loss": stop_loss, "take_profit": take_profit, "take_profit_1": take_profit_1, "confidence": ai_reco['confidence']}
                            trade_log_data = {"timestamp": time.time(), "symbol": symbol, "interval": best_timeframe, "type": trade_type, "confidence": ai_reco['confidence'], "market_regime": market_regime, "momentum_status": tactician_inputs['momentum_status'], "entry_price": entry_price, "stop_loss": stop_loss, "take_profit": take_profit, "take_profit_1": take_profit_1, "exchange": exchange, "full_ai_response_json": json.dumps(parsed_response.parsed_data)}
                            if not self.db_manager.does_trade_exist(trade_log_data): self.db_manager.log_trade(trade_log_data)
                
                return parsed_response, analysis_result, best_timeframe, context_data

            except Exception as e:
                logger.critical(f"Krytyczny błąd w AIPipeline dla {symbol}: {e}", exc_info=True)
                return None, None, interval, context_data
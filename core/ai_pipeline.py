# Plik: core/ai_pipeline.py (WERSJA FINALNA I KOMPLETNA)

import asyncio
import logging
import re
import json
import time
import pandas as pd
from typing import Tuple, Optional, Dict, List, Any

from core.analyzer import TechnicalAnalyzer, AnalysisResult
from core.ai_client import AIClient, ParsedAIResponse
from core.database_manager import DatabaseManager
from core.performance_analyzer import PerformanceAnalyzer
from core.data_models import TradeData, ContextData
from core.prompt_templates import (OBSERVER_PROMPT_TEMPLATE, BIAS_AGENT_PROMPT_TEMPLATE, 
                                     LEVEL_CONFIDENCE_AGENT_PROMPT_TEMPLATE, TP_REVIEWER_PROMPT_TEMPLATE)

logger = logging.getLogger(__name__)

class AIPipeline:
    def __init__(self, analyzer: TechnicalAnalyzer, ai_client: AIClient, db_manager: DatabaseManager, performance_analyzer: PerformanceAnalyzer):
        self.analyzer = analyzer
        self.ai_client = ai_client
        self.db_manager = db_manager
        self.performance_analyzer = performance_analyzer
        self.analysis_locks: Dict[Tuple[str, str], asyncio.Lock] = {}

    async def run(self, symbol: str, interval: str, exchange: str, sc: callable, trigger_pattern: str = "Brak") -> Tuple[Optional[ParsedAIResponse], Optional[AnalysisResult], str, Dict]:
        lock_key = (symbol, interval)
        if lock_key not in self.analysis_locks: self.analysis_locks[lock_key] = asyncio.Lock()
        lock = self.analysis_locks[lock_key]
        if lock.locked(): return None, None, interval, {}

        async with lock:
            try:
                analysis_result = await self._step_1_get_technical_analysis(symbol, interval, exchange, sc)
                if not analysis_result: return None, None, interval, {}

                best_timeframe = await self._step_2_run_observer(symbol, interval, analysis_result, sc)
                
                context, base_inputs = await self._step_3_get_full_context(symbol, exchange, analysis_result, best_timeframe)
                if not context: return None, None, best_timeframe, {}
                
                bias = await self._step_4a_get_bias(symbol, sc, best_timeframe, ar=analysis_result, context=context, base_inputs=base_inputs, trigger_pattern=trigger_pattern)
                if not bias or bias == 'Neutral':
                    sc(f"({symbol}) Agent Kierunku ocenił rynek jako Neutralny. Koniec analizy.", False)
                    return None, analysis_result, best_timeframe, context.__dict__
                
                parsed_response = await self._step_4b_get_level_and_confidence(symbol, sc, bias, base_inputs, trigger_pattern, ar=analysis_result)
                if not parsed_response or not parsed_response.is_valid:
                    return parsed_response, analysis_result, best_timeframe, context.__dict__
                
                parsed_response.parsed_data['bias'] = bias
                
                df_for_setup = self.analyzer.calculate_all_indicators(analysis_result.all_ohlcv_dfs[best_timeframe].copy())
                final_setup = await self._step_5_construct_and_validate_setup(...)
                # Nie musimy już nic dodawać, bo zostało to zrobione w kroku 5.
                
                return parsed_response, analysis_result, best_timeframe, context.__dict__
            except Exception as e:
                logger.critical(f"Krytyczny błąd w AIPipeline dla {symbol}: {e}", exc_info=True)
                return None, None, interval, {}

    async def _step_1_get_technical_analysis(self, symbol: str, interval: str, exchange: str, sc: callable) -> Optional[AnalysisResult]:
        sc(f"({symbol}) Krok 1: Pobieranie danych...", True); analysis_result = await self.analyzer.get_analysis_data(symbol, interval, exchange)
        if not analysis_result.is_successful: logger.error(f"({symbol}) Krok 1 nie powiódł się."); return None
        return analysis_result

    async def _step_2_run_observer(self, symbol: str, interval: str, ar: AnalysisResult, sc: callable) -> str:
        sc(f"({symbol}) Krok 2: Agent Obserwator...", True); prompt = OBSERVER_PROMPT_TEMPLATE.format(technical_data_section=self._format_data_for_prompt(ar.all_timeframe_data))
        self.ai_client.clear_chat_history(); self.ai_client.add_message("user", prompt); response = (await self.ai_client.get_chat_completion_async() or interval).strip()
        match = re.search(r'\b(\d{1,2}[hdwm])\b', response, re.IGNORECASE); return match.group(1).lower() if match else interval

    async def _step_3_get_full_context(self, symbol: str, exchange: str, ar: AnalysisResult, timeframe: str) -> Tuple[Optional[ContextData], Dict]:
        best_df = ar.all_ohlcv_dfs.get(timeframe)
        if best_df is None: return None, {}
        context = await self.analyzer.get_full_context(symbol, exchange, best_df)
        base_inputs = await self.analyzer.prepare_tactician_inputs(ar, timeframe, symbol, exchange)
        return context, base_inputs

    async def _step_4a_get_bias(self, symbol: str, sc: callable, timeframe: str, ar: AnalysisResult, context: ContextData, base_inputs: Dict, trigger_pattern: str) -> Optional[str]:
        sc(f"({symbol}) Krok 4a: Agent Kierunku...", True)
        bias_inputs = {**base_inputs, "timeframe": timeframe, "trigger_pattern_section": trigger_pattern, "current_price": self.analyzer._round_price_for_ai(ar.current_price), **context.__dict__}
        prompt = BIAS_AGENT_PROMPT_TEMPLATE.format(**bias_inputs); self.ai_client.clear_chat_history(); self.ai_client.add_message("user", prompt)
        response = await self.ai_client.get_chat_completion_async()
        if response and response.strip() in ['Bullish', 'Bearish', 'Neutral']:
            sc(f"({symbol}) Agent Kierunku zdecydował: {response.strip()}", True); return response.strip()
        sc(f"({symbol}) Agent Kierunku zwrócił niepoprawną odpowiedź: {response}", False); return None

    async def _step_4b_get_level_and_confidence(self, symbol: str, sc: callable, bias: str, base_inputs: Dict, trigger_pattern: str, ar: AnalysisResult) -> Optional[ParsedAIResponse]:
        sc(f"({symbol}) Krok 4b: Agent Ryzyka...", True)
        level_inputs = {"bias": bias, "trigger_pattern_section": trigger_pattern, "programmatic_sr_json": base_inputs.get("programmatic_sr_json"), "current_price": self.analyzer._round_price_for_ai(ar.current_price)}
        prompt = LEVEL_CONFIDENCE_AGENT_PROMPT_TEMPLATE.format(**level_inputs)
        # Zmieniamy walidator na nowy, który stworzymy w AIClient
        return await self.get_ai_response_with_retry(prompt, symbol, sc, mode='risk_validator')

    async def _step_5_construct_and_validate_setup(self, symbol: str, exchange: str, timeframe: str, resp: ParsedAIResponse, context: ContextData, base_inputs: Dict, ar: AnalysisResult, sc: callable, df_with_indicators: pd.DataFrame) -> Optional[Dict]:
        if not (resp.is_valid and resp.parsed_data.get('bias') in ['Bullish', 'Bearish']):
            sc(f"({symbol}) AI nie znalazło klarownego kierunku.", False)
            return None
        
        sc(f"({symbol}) Krok 5: Konstruktor setupu...", True)
        ai_reco = resp.parsed_data
        trade_type = 'Long' if ai_reco['bias'] == 'Bullish' else 'Short'
        
        # --- NOWA LOGIKA OBLICZEŃ BAZUJĄCA NA ODPOWIEDZI AI ---
        # Wejście to aktualna cena rynkowa w momencie analizy.
        entry_price = ar.current_price
        # Stop Loss jest obliczany na podstawie procentowej odległości zwróconej przez AI.
        sl_percent = ai_reco['sl_percent_distance'] / 100.0

        if trade_type == 'Long':
            stop_loss = entry_price * (1 - sl_percent)
        else: # Short
            stop_loss = entry_price * (1 + sl_percent)
        # --- KONIEC NOWEJ LOGIKI ---

        # Od tego momentu reszta logiki działa na nowo obliczonych wartościach
        tp1, tp2 = await self.get_hybrid_tps(symbol, sc, trade_type, entry_price, stop_loss, base_inputs, df_with_indicators)
        
        if tp1 is None or tp2 is None:
            logger.warning(f"[{symbol}] Hybrydowy TP nie znalazł celów. Używam zapasowej metody R:R.")
            rr_tp1 = self.analyzer.settings.get('strategies.ai_clone.risk_reward_ratio_tp1', 1.5)
            rr_tp2 = self.analyzer.settings.get('strategies.ai_clone.risk_reward_ratio_tp2', 2.0)
            risk = abs(entry_price - stop_loss)
            if risk == 0: return None
            tp1 = entry_price + (risk * rr_tp1) if trade_type == 'Long' else entry_price - (risk * rr_tp1)
            tp2 = entry_price + (risk * rr_tp2) if trade_type == 'Long' else entry_price - (risk * rr_tp2)

        risk = abs(entry_price - stop_loss)
        final_rr = (abs(tp2 - entry_price) / risk) if risk > 0 else 0
        
        required_rr = 0.0
        reason = ""

        # Sprawdzamy, czy tryb dynamiczny jest włączony
        if self.analyzer.settings.get('ai.dynamic_rr.enabled', False):
            daily_metrics = await self.analyzer.get_daily_metrics(symbol, exchange)
            current_atr_pct = daily_metrics.get('atr_percent')

            # --- NOWY BLOK ZABEZPIECZAJĄCY ---
            if current_atr_pct is None:
                logger.warning(f"[{symbol}] Nie można obliczyć dziennego ATR%. Przełączam na stały tryb R:R.")
                required_rr = self.analyzer.settings.get('ai.min_rr_ratio', 2.0)
                reason = "braku danych do oceny zmienności"
            # --- KONIEC BLOKU ZABEZPIECZAJĄCEGO ---
            else:
                atr_threshold = self.analyzer.settings.get('ai.dynamic_rr.atr_threshold_pct', 2.5)
                high_vol_target = self.analyzer.settings.get('ai.dynamic_rr.high_vol_rr', 1.5)
                low_vol_target = self.analyzer.settings.get('ai.dynamic_rr.low_vol_rr', 2.5)
                
                if current_atr_pct >= atr_threshold:
                    required_rr = high_vol_target
                    reason = f"wysokiej zmienności (ATR {current_atr_pct:.2f}% >= {atr_threshold}%)"
                else:
                    required_rr = low_vol_target
                    reason = f"niskiej zmienności (ATR {current_atr_pct:.2f}% < {atr_threshold}%)"
        else:
            required_rr = self.analyzer.settings.get('ai.min_rr_ratio', 2.0)
            reason = "trybu stałego"

        max_rr = self.analyzer.settings.get('ai.validation.max_rr_ratio', 8.0)

        if final_rr < required_rr:
            logger.info(f"[{symbol}] Setup odrzucony - finalne R:R ({final_rr:.2f}) jest niższe niż wymagane ({required_rr}) z powodu {reason}.")
            return None
        if final_rr > max_rr:
            logger.info(f"[{symbol}] Setup odrzucony - finalne R:R ({final_rr:.2f}) jest wyższe niż dozwolone maksimum ({max_rr}).")
            return None

        final_setup = {
            "status": "potential", "type": trade_type, "entry": entry_price, "stop_loss": stop_loss,
            "take_profit": tp2, "take_profit_1": tp1, "confidence": ai_reco['confidence']
        }

        # 2. Dodajemy go do obiektu z odpowiedzią AI PRZED zapisem do bazy
        resp.parsed_data['setup'] = final_setup
        
        # 3. Tworzymy obiekt do zapisu, używając już ZMODYFIKOWANEJ odpowiedzi
        trade_to_log = TradeData(
            timestamp=time.time(), symbol=symbol, interval=timeframe, exchange=exchange, type=trade_type,
            confidence=ai_reco.get('confidence'), market_regime=context.market_regime,
            momentum_status=context.market_momentum_status, entry_price=entry_price, stop_loss=stop_loss,
            take_profit=tp2, take_profit_1=tp1, full_ai_response_json=json.dumps(resp.parsed_data)
        )
        
        if not self.db_manager.does_trade_exist(trade_to_log.__dict__):
            self.db_manager.log_trade(trade_to_log)
            sc(f"({symbol}) Nowy setup '{trade_type}' zapisany!", False)
        
        return final_setup

    async def get_hybrid_tps(self, symbol: str, sc: callable, trade_type: str, entry_price: float, stop_loss: float, base_inputs: dict, df_with_indicators: pd.DataFrame) -> Tuple[Optional[float], Optional[float]]:
        sr_levels = json.loads(base_inputs.get("programmatic_sr_json", "{}")); candidates = []
        if trade_type == 'Long': candidates = sorted([r for r in sr_levels.get('resistance', []) if r > entry_price])
        else: candidates = sorted([s for s in sr_levels.get('support', []) if s < entry_price], reverse=True)
        if not candidates: return None, None
        sc(f"({symbol}) Agent Recenzent TP ocenia cele...", True); candidates_text = "\n".join([f"- {c:.4f}" for c in candidates]); tp_reviewer_prompt = TP_REVIEWER_PROMPT_TEMPLATE.format(trade_type=trade_type, entry_price=entry_price, tp_candidates_text=candidates_text)
        parsed_response = await self.get_ai_response_with_retry(tp_reviewer_prompt, symbol, sc, mode='tp_reviewer');
        if not parsed_response.is_valid: return None, None
        scored_candidates = {float(k): v for k, v in parsed_response.parsed_data.items() if float(k) in candidates};
        if not scored_candidates: return None, None
        risk_amount = abs(entry_price - stop_loss);
        if risk_amount == 0: return None, None 
        atr_key = next((col for col in df_with_indicators.columns if 'ATRR' in col), None); last_atr = df_with_indicators[atr_key].iloc[-1] if atr_key and pd.notna(df_with_indicators[atr_key].iloc[-1]) else 0
        if last_atr <= 0: return None, None
        min_rr_tp1 = self.analyzer.settings.get('strategies.ai_clone.min_risk_reward_ratio_tp1', 0.8); max_dist_multiplier = self.analyzer.settings.get('ai.validation.max_tp_to_atr_ratio', 10.0); max_distance = last_atr * max_dist_multiplier
        valid_and_realistic_candidates = []
        for c in candidates:
            if (abs(c - entry_price) / risk_amount) < min_rr_tp1: continue
            if abs(c - entry_price) > max_distance: logger.info(f"Odrzucono kandydata TP {c:.4f} - zbyt odległy."); continue
            valid_and_realistic_candidates.append(c)
        if not valid_and_realistic_candidates: logger.warning(f"Brak kandydatów na TP dla {trade_type} po filtracji."); return None, None
        valid_and_realistic_candidates.sort(key=lambda c: scored_candidates.get(c, 0), reverse=True)
        tp1 = min(valid_and_realistic_candidates) if trade_type == 'Long' else max(valid_and_realistic_candidates); tp2 = valid_and_realistic_candidates[0]
        if trade_type == 'Long' and tp2 < tp1: tp2 = max(valid_and_realistic_candidates)
        if trade_type == 'Short' and tp2 > tp1: tp2 = min(valid_and_realistic_candidates)
        return tp1, tp2

    async def get_ai_response_with_retry(self, prompt: str, symbol: str, sc: callable, mode: str = 'tactician', retries: int = 3) -> ParsedAIResponse:
        self.ai_client.clear_chat_history(); self.ai_client.add_message("user", prompt)
        for attempt in range(retries + 1):
            sc(f"({symbol}) Oczekiwanie na odpowiedź AI (próba {attempt + 1})...", True)
            raw_response = await self.ai_client.get_chat_completion_async()
            sc(f"({symbol}) Przetwarzanie odpowiedzi AI...", True)
            parsed = self.ai_client.przetworz_odpowiedz(raw_response, mode=mode)
            if parsed.is_valid: sc(f"({symbol}) Sukces! Odpowiedź AI poprawna.", True); return parsed
            else: sc(f"({symbol}) Błąd! Odpowiedź AI niepoprawna.", True)
            if attempt < retries: logger.warning(f"Odpowiedź AI była niepoprawna strukturalnie (próba {attempt + 1}/{retries + 1}). Ponawiam.")
            else: logger.error("AI nie dostarczyło żadnej poprawnej strukturalnie odpowiedzi po kilku próbach.")
        return ParsedAIResponse(is_valid=False)

    def _format_data_for_prompt(self, data_dict: Dict) -> str:
        parts = []
        for interval, data in data_dict.items():
            if not data or not data.get('interpreted'): continue
            parts.append(f"\n# Interwał: {interval}")
            for key, value in data['interpreted'].items():
                parts.append(f"- {key}: {value.get('text', 'Brak danych') if isinstance(value, dict) else value}")
        return "\n".join(parts) if parts else "Brak danych technicznych."
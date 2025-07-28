import asyncio
import logging
import pandas as pd
from typing import Dict, Any, Optional

from core.database_manager import DatabaseManager
from core.analyzer import TechnicalAnalyzer

logger = logging.getLogger(__name__)

class PaperTrader:
    def __init__(self, db_manager: DatabaseManager, analyzer: TechnicalAnalyzer, global_analysis_lock: asyncio.Lock):
        self.db_manager = db_manager
        self.analyzer = analyzer
        self.is_running = False
        self.global_analysis_lock = global_analysis_lock
        self.expiration_limit = self.analyzer.settings.get('ssnedam.setup_expiration_candles', 12)
        logger.info("PaperTrader zainicjalizowany.")

    async def start(self):
        if self.is_running: return
        self.is_running = True
        logger.info("[PaperTrader] Uruchamianie pętli monitorującej...")
        while self.is_running:
            try:
                await self.check_pending_trades()
            except Exception as e:
                logger.error(f"[PaperTrader] Niespodziewany błąd w pętli: {e}", exc_info=True)
            await asyncio.sleep(60)

    def stop(self):
        self.is_running = False
        logger.info("[PaperTrader] Zatrzymywanie pętli monitorującej...")

    async def check_pending_trades(self):
        if self.global_analysis_lock.locked():
            logger.info("[PaperTrader] Skanowanie pominięte, trwa inna analiza.")
            return

        pending_trades = self.db_manager.get_pending_trades()
        if not pending_trades: return

        trades_by_market = {}
        for trade in pending_trades:
            key = (trade['symbol'], trade['interval'])
            if key not in trades_by_market: trades_by_market[key] = []
            trades_by_market[key].append(trade)

        for (symbol, interval), trades in trades_by_market.items():
            try:
                oldest_trade_ts = min(t['timestamp'] for t in trades)
                exchange_id = trades[0].get('exchange', 'BINANCE')
                exchange_instance = await self.analyzer.exchange_service.get_exchange_instance(exchange_id)
                if not exchange_instance: continue
                
                ohlcv = await self.analyzer.exchange_service.fetch_ohlcv(exchange_instance, symbol, interval, since=int(oldest_trade_ts * 1000))
                if ohlcv is None or ohlcv.empty: continue
                
                for trade in trades:
                    candles_after_setup = ohlcv[ohlcv.index > pd.to_datetime(trade['timestamp'], unit='s')]
                    if candles_after_setup.empty: continue
                    
                    trade_id = trade['id']
                    
                    # --- SCENARIUSZ A: Transakcja AKTYWNA ---
                    if trade.get('is_active', 0) == 1:
                        self._handle_active_trade(trade, candles_after_setup)
                        continue

                    # --- SCENARIUSZ B: Transakcja OCZEKUJĄCA ---
                    self._handle_pending_trade(trade, candles_after_setup)

            except Exception as e:
                logger.warning(f"[PaperTrader] Błąd podczas sprawdzania {symbol}. Błąd: {e}", exc_info=True)
                continue
    
    def _handle_active_trade(self, trade: dict, candles: pd.DataFrame):
        """Przetwarza logikę dla już aktywnej transakcji."""
        trade_id = trade['id']
        # Sprawdzamy każdą nową świecę od momentu aktywacji
        for _, candle in candles.iterrows():
            current_trade_state = self.db_manager.get_trade_by_id(trade_id)
            if not current_trade_state or current_trade_state['result'] != 'PENDING':
                break # Transakcja została już zamknięta w innej iteracji

            is_partially_closed = current_trade_state.get('is_partially_closed', 0) == 1
            if not is_partially_closed and self._check_tp1_hit(trade, candle):
                tp1_price = float(trade['take_profit_1'])
                logger.info(f"TRANSAKCJA ID {trade_id}: Osiągnięto TP1 przy cenie {tp1_price}.")
                self.db_manager.log_trade_event(trade_id, 'TP1_HIT', {'price': tp1_price})
                self.db_manager.log_trade_event(trade_id, 'SL_MOVED_TO_BE', {'price': trade['entry_price']})
                self.db_manager.mark_trade_as_partially_closed(trade_id, trade['entry_price'])
            else:
                result = self._check_sl_tp(current_trade_state, candle)
                if result:
                    self.db_manager.update_trade_result(trade_id, result, trade['symbol'])
                    break
    
    def _handle_pending_trade(self, trade: dict, candles: pd.DataFrame):
        """Przetwarza logikę dla transakcji oczekującej na aktywację."""
        trade_id = trade['id']
        # 1. Sprawdź, czy setup wygasł
        if len(candles) > self.expiration_limit:
            logger.info(f"SETUP WYGASŁY: {trade['symbol']} (ID: {trade_id}) nie został aktywowany w ciągu {self.expiration_limit} świec.")
            self.db_manager.update_trade_result(trade_id, 'WYGASŁY', trade['symbol'])
            return

        # 2. Sprawdź każdą nową świecę w poszukiwaniu aktywacji lub anulowania
        for _, candle in candles.iterrows():
            entry = float(trade['entry_price']); sl = float(trade['stop_loss'])
            
            # --- NOWA, PRECYZYJNA LOGIKA ---
            if trade['type'] == 'Long':
                # ANULOWANIE: Dołek świecy uderza w SL, a jej szczyt NIGDY nie dotknął wejścia.
                if candle['Low'] <= sl and candle['High'] < entry:
                    logger.info(f"SETUP ANULOWANY [Long]: {trade['symbol']} (ID: {trade_id}). SL({sl}) trafiony przed wejściem({entry}).")
                    self.db_manager.update_trade_result(trade_id, 'ANULOWANY', trade['symbol'])
                    return # Zakończ przetwarzanie tej transakcji
                
                # AKTYWACJA: Dołek świecy dotknął wejścia.
                elif candle['Low'] <= entry:
                    self.db_manager.activate_trade(trade_id, trade['symbol'])
                    self._handle_active_trade(self.db_manager.get_trade_by_id(trade_id), pd.DataFrame([candle]))
                    return

            elif trade['type'] == 'Short':
                # ANULOWANIE: Szczyt świecy uderza w SL, a jej dołek NIGDY nie dotknął wejścia.
                if candle['High'] >= sl and candle['Low'] > entry:
                    logger.info(f"SETUP ANULOWANY [Short]: {trade['symbol']} (ID: {trade_id}). SL({sl}) trafiony przed wejściem({entry}).")
                    self.db_manager.update_trade_result(trade_id, 'ANULOWANY', trade['symbol'])
                    return
                
                # AKTYWACJA: Szczyt świecy dotknął wejścia.
                elif candle['High'] >= entry:
                    self.db_manager.activate_trade(trade_id, trade['symbol'])
                    self._handle_active_trade(self.db_manager.get_trade_by_id(trade_id), pd.DataFrame([candle]))
                    return

    def _check_tp1_hit(self, trade: dict, candle: pd.Series) -> bool:
        tp1_price = trade.get('take_profit_1')
        if not tp1_price: return False
        tp1_price = float(tp1_price)
        if trade['type'] == 'Long' and candle['High'] >= tp1_price: return True
        if trade['type'] == 'Short' and candle['Low'] <= tp1_price: return True
        return False

    def _check_sl_tp(self, trade: dict, candle: pd.Series) -> Optional[str]:
        if trade.get('stop_loss') is None or trade.get('take_profit') is None: return None
        sl_price = float(trade['stop_loss']); tp_price = float(trade['take_profit'])
        if trade['type'] == 'Long':
            if candle['Low'] <= sl_price: return 'SL_HIT'
            elif candle['High'] >= tp_price: return 'TP_HIT'
        elif trade['type'] == 'Short':
            if candle['High'] >= sl_price: return 'SL_HIT'
            elif candle['Low'] <= tp_price: return 'TP_HIT'
        return None
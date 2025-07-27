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
        """Uruchamia główną pętlę monitorującą."""
        if self.is_running:
            return
        
        self.is_running = True
        logger.info("[PaperTrader] Uruchamianie pętli monitorującej...")
        
        while self.is_running:
            try:
                await self.check_pending_trades()
            except Exception as e:
                logger.error(f"[PaperTrader] Niespodziewany błąd w pętli: {e}", exc_info=True)
            
            # Czekamy 60 sekund przed kolejnym sprawdzeniem
            await asyncio.sleep(60)

    def stop(self):
        """Zatrzymuje pętlę monitorującą."""
        self.is_running = False
        logger.info("[PaperTrader] Zatrzymywanie pętli monitorującej...")

    async def check_pending_trades(self):
        """Główna metoda sprawdzająca wszystkie oczekujące i aktywne transakcje."""
        if self.global_analysis_lock.locked():
            logger.info("[PaperTrader] Skanowanie pominięte, trwa inna analiza.")
            return

        pending_trades = self.db_manager.get_pending_trades()
        if not pending_trades:
            return

        logger.info(f"[PaperTrader] Sprawdzanie {len(pending_trades)} oczekujących transakcji...")
        
        trades_by_market = {}
        for trade in pending_trades:
            key = (trade['symbol'], trade['interval'])
            if key not in trades_by_market:
                trades_by_market[key] = []
            trades_by_market[key].append(trade)

        for (symbol, interval), trades in trades_by_market.items():
            try:
                oldest_trade_ts = min(t['timestamp'] for t in trades)
                exchange_id = trades[0].get('exchange', 'BINANCE')
                exchange_instance = await self.analyzer.exchange_service.get_exchange_instance(exchange_id)
                if not exchange_instance: continue
                
                ohlcv = await self.analyzer.exchange_service.fetch_ohlcv(
                    exchange_instance, symbol, interval, since=int(oldest_trade_ts * 1000)
                )
                if ohlcv is None or ohlcv.empty: continue
                
                last_candle = ohlcv.iloc[-1]

                for trade in trades:
                    trade_id = trade['id']
                    
                    # --- SCENARIUSZ A: Transakcja AKTYWNA ---
                    if trade.get('is_active', 0) == 1:
                        # --- NOWA LOGIKA DLA AKTYWNYCH TRANSAKCJI ---
                        # 1. Sprawdź, czy nie trafiono w TP1 (jeśli jeszcze nie był)
                        is_partially_closed = trade.get('is_partially_closed', 0) == 1
                        tp1_price = trade.get('take_profit_1')

                        if not is_partially_closed and tp1_price and self._check_tp1_hit(trade, last_candle):
                            # Jeśli trafiono w TP1, zapisz zdarzenie i zaktualizuj transakcję
                            logger.info(f"TRANSAKCJA ID {trade_id}: Osiągnięto TP1 przy cenie {tp1_price}.")
                            self.db_manager.log_trade_event(trade_id, 'TP1_HIT', {'price': tp1_price})
                            self.db_manager.log_trade_event(trade_id, 'SL_MOVED_TO_BE', {'price': trade['entry_price']})
                            self.db_manager.mark_trade_as_partially_closed(trade_id, trade['entry_price'])
                        else:
                            # Jeśli nie, sprawdź ostateczne SL/TP
                            result = self._check_sl_tp(trade, last_candle)
                            if result:
                                exit_price = float(trade['take_profit']) if result == 'TP_HIT' else float(trade['stop_loss'])
                                logger.info(f"TRANSAKCJA AKTYWNA ZAMKNIĘTA: {symbol} osiągnęła {result} przy cenie {exit_price}.")
                                self.db_manager.update_trade_result(trade_id, result, symbol)
                        continue

                    # --- SCENARIUSZ B: Transakcja OCZEKUJĄCA (logika bez zmian) ---
                    trade_timestamp = pd.to_datetime(trade['timestamp'], unit='s')
                    candles_since_setup = ohlcv[ohlcv.index > trade_timestamp]
                    if len(candles_since_setup) > self.expiration_limit:
                        self.db_manager.update_trade_result(trade_id, 'WYGASŁY', symbol); continue

                    entry, sl = float(trade['entry_price']), float(trade['stop_loss'])
                    entry_triggered = (trade['type'] == 'Long' and last_candle['Low'] <= entry) or \
                                      (trade['type'] == 'Short' and last_candle['High'] >= entry)
                    sl_hit_before_entry = (trade['type'] == 'Long' and last_candle['Low'] <= sl and last_candle['High'] < entry) or \
                                          (trade['type'] == 'Short' and last_candle['High'] >= sl and last_candle['Low'] > entry)

                    if sl_hit_before_entry:
                        self.db_manager.update_trade_result(trade_id, 'ANULOWANY', symbol)
                    elif entry_triggered:
                        self.db_manager.activate_trade(trade_id, symbol)
            
            except Exception as e:
                trade_ids_in_batch = [t['id'] for t in trades]
                logger.warning(f"[PaperTrader] Błąd podczas sprawdzania {symbol} (ID: {trade_ids_in_batch}). Błąd: {e}")
                continue
    
    # --- NOWA METODA POMOCNICZA ---
    def _check_tp1_hit(self, trade: dict, candle: pd.Series) -> bool:
        """Sprawdza, czy cena osiągnęła poziom TP1."""
        tp1_price = trade.get('take_profit_1')
        if not tp1_price: return False
        
        tp1_price = float(tp1_price)
        if trade['type'] == 'Long' and candle['High'] >= tp1_price:
            return True
        if trade['type'] == 'Short' and candle['Low'] <= tp1_price:
            return True
        return False

    def _check_sl_tp(self, trade: dict, candle: pd.Series) -> Optional[str]:
        """Sprawdza, czy aktywna transakcja osiągnęła SL lub TP."""
        if trade.get('stop_loss') is None or trade.get('take_profit') is None:
            return None
            
        trade_type = trade['type']
        sl_price = float(trade['stop_loss'])
        tp_price = float(trade['take_profit'])
        
        if trade_type == 'Long':
            if candle['Low'] <= sl_price: return 'SL_HIT'
            elif candle['High'] >= tp_price: return 'TP_HIT'
        elif trade_type == 'Short':
            if candle['High'] >= sl_price: return 'SL_HIT'
            elif candle['Low'] <= tp_price: return 'TP_HIT'
            
        return None
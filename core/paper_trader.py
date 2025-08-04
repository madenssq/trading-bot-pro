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
        logger.info("PaperTrader zainicjalizowany w nowym trybie 'status'.")

    async def start(self):
        if self.is_running: return
        self.is_running = True
        logger.info("[PaperTrader] Uruchamianie pętli monitorującej...")
        while self.is_running:
            try:
                # ZMIANA: Nazwa metody odzwierciedla teraz, że sprawdzamy wszystkie otwarte pozycje
                await self.check_open_trades()
            except Exception as e:
                logger.error(f"[PaperTrader] Niespodziewany błąd w pętli: {e}", exc_info=True)
            await asyncio.sleep(60)

    def stop(self):
        self.is_running = False
        logger.info("[PaperTrader] Zatrzymywanie pętli monitorującej...")

    async def check_open_trades(self):
        if self.global_analysis_lock.locked():
            logger.info("[PaperTrader] Skanowanie pominięte, trwa inna analiza.")
            return

        # ZMIANA: Używamy nowej metody, która pobiera wszystkie transakcje, które nie są w stanie końcowym
        open_trades = self.db_manager.get_open_trades()
        if not open_trades: return

        trades_by_market = {}
        for trade in open_trades:
            key = (trade['symbol'], trade['interval'], trade['exchange'])
            if key not in trades_by_market: trades_by_market[key] = []
            trades_by_market[key].append(trade)

        for (symbol, interval, exchange_id), trades in trades_by_market.items():
            try:
                oldest_trade_ts = min(t['timestamp'] for t in trades)
                exchange_instance = await self.analyzer.get_exchange_instance(exchange_id)
                if not exchange_instance: continue
                
                ohlcv = await self.analyzer.fetch_ohlcv(exchange_instance, symbol, interval, since=int(oldest_trade_ts * 1000))
                if ohlcv is None or ohlcv.empty: continue
                
                for trade in trades:
                    candles_after_setup = ohlcv[ohlcv.index > pd.to_datetime(trade['timestamp'], unit='s')]
                    if candles_after_setup.empty: continue
                    
                    # ZMIANA: Rozbudowana logika oparta na nowym, jednoznacznym statusie
                    current_status = trade.get('status')
                    if current_status == 'POTENTIAL':
                        self._handle_potential_trade(trade, candles_after_setup)
                    elif current_status in ['ACTIVE', 'PARTIAL_PROFIT']:
                        self._handle_active_trade(trade, candles_after_setup)
            
            except Exception as e:
                logger.warning(f"[PaperTrader] Błąd podczas sprawdzania {symbol} na giełdzie {exchange_id}. Błąd: {e}", exc_info=True)
                continue

    def _handle_potential_trade(self, trade: dict, candles: pd.DataFrame):
        """Obsługuje setupy, które jeszcze nie zostały aktywowane."""
        trade_id = trade['id']
        if len(candles) > self.expiration_limit:
            self.db_manager.update_trade_status(trade_id, 'EXPIRED'); return

        for _, candle in candles.iterrows():
            entry = float(trade['entry_price']); sl = float(trade['stop_loss'])
            
            if trade['type'] == 'Long':
                if candle['Low'] <= sl and candle['High'] < entry:
                    self.db_manager.update_trade_status(trade_id, 'CANCELLED'); return
                elif candle['Low'] <= entry:
                    self.db_manager.update_trade_status(trade_id, 'ACTIVE')
                    self.db_manager.log_trade_event(trade_id, 'ACTIVATED', {'price': entry})
                    self._handle_active_trade(self.db_manager.get_trade_by_id(trade_id), pd.DataFrame([candle], index=[candle.name]))
                    return
            elif trade['type'] == 'Short':
                if candle['High'] >= sl and candle['Low'] > entry:
                    self.db_manager.update_trade_status(trade_id, 'CANCELLED'); return
                elif candle['High'] >= entry:
                    self.db_manager.update_trade_status(trade_id, 'ACTIVE')
                    self.db_manager.log_trade_event(trade_id, 'ACTIVATED', {'price': entry})
                    self._handle_active_trade(self.db_manager.get_trade_by_id(trade_id), pd.DataFrame([candle], index=[candle.name]))
                    return

    def _handle_active_trade(self, trade: dict, candles: pd.DataFrame):
        """Obsługuje transakcje, które są aktywne lub częściowo zrealizowane."""
        trade_id = trade['id']
        
        for _, candle in candles.iterrows():
            current_trade_state = self.db_manager.get_trade_by_id(trade_id)
            if not current_trade_state or current_trade_state['status'] not in ['ACTIVE', 'PARTIAL_PROFIT']:
                return

            if current_trade_state['status'] == 'ACTIVE' and self._check_tp1_hit(current_trade_state, candle):
                tp1_price = float(current_trade_state['take_profit_1'])
                new_sl_price = float(current_trade_state['entry_price'])
                
                self.db_manager.update_trade_status(trade_id, 'PARTIAL_PROFIT')
                self.db_manager.update_trade_sl(trade_id, new_sl_price)
                self.db_manager.log_trade_event(trade_id, 'TP1_HIT', {'price': tp1_price})
                self.db_manager.log_trade_event(trade_id, 'SL_MOVED_TO_BE', {'price': new_sl_price})
                
                current_trade_state = self.db_manager.get_trade_by_id(trade_id)

            result = self._check_sl_tp(current_trade_state, candle)
            if result:
                final_status = None
                if result == 'SL_HIT':
                    final_status = 'CLOSED_BE' if current_trade_state['status'] == 'PARTIAL_PROFIT' else 'CLOSED_SL'
                elif result == 'TP_HIT':
                    final_status = 'CLOSED_TP'
                
                if final_status:
                    self.db_manager.update_trade_status(trade_id, final_status)
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
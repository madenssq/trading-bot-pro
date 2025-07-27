import logging
import pandas as pd
import numpy as np
import asyncio
import ccxt.async_support as ccxt

from core.settings_manager import SettingsManager
from core.database_manager import DatabaseManager
from core.strategy import Strategy

logger = logging.getLogger(__name__)

class Backtester:
    def __init__(self, settings_manager: SettingsManager):
        self.settings_manager = settings_manager
        self.settings = self.settings_manager.get('backtester', {})
        self.fee_pct = self.settings.get('default_fee_pct', 0.1) / 100
        self.db_manager = DatabaseManager()
        self.exchange = ccxt.binance({'enableRateLimit': True})
        self._reset_state()

    def _reset_state(self):
        self._data: pd.DataFrame = None
        self._strategy: Strategy = None
        self.trades = []
        self.equity_curve = []
        self.initial_capital = 10000.0
        self.equity = 10000.0
        self.position_size = 0.0
        self.entry_price = 0.0
        self.entry_date = None
        self.position_type = 0
        self.sl_price = 0.0
        self.tp_price = 0.0 # Będzie teraz używane jako TP2
        # --- NOWY STAN ---
        self.tp1_price = 0.0 # Cena dla pierwszego, częściowego TP
        self.is_partially_closed = False # Flaga, czy zamknęliśmy już część pozycji

    def _execute_loop(self):
        logger.info(f"Uruchamianie pętli symulacyjnej dla {len(self._data)} świec...")
        self.equity = self.initial_capital
        self.equity_curve.append(self.equity)
        
        for i in range(1, len(self._data)):
            self._strategy.i = i
            current_candle = self._data.iloc[i]

            if self.in_position:
                if self.position_type == 1: # Pozycja Long
                    # --- NOWA, ZAAWANSOWANA LOGIKA WYJŚCIA ---
                    
                    # 1. Sprawdź, czy cena uderzyła w ostateczny TP (TP2)
                    if current_candle['High'] >= self.tp_price:
                        self.close('TP')
                    
                    # 2. Sprawdź, czy cena uderzyła w SL
                    elif current_candle['Low'] <= self.sl_price:
                        self.close('SL')
                    
                    # 3. Sprawdź, czy cena uderzyła w pierwszy TP (TP1) i czy pozycja nie była jeszcze częściowo zamknięta
                    elif not self.is_partially_closed and current_candle['High'] >= self.tp1_price:
                        self._close_partial()
                        self._move_sl_to_breakeven()
                
                # Jeśli pozycja została całkowicie zamknięta, przejdź dalej
                if not self.in_position:
                    self.equity_curve.append(self.equity)
                    continue
            
            self._strategy.next()
            
            current_equity = self.equity
            if self.in_position:
                pnl = (current_candle['Close'] - self.entry_price) * self.position_size * self.position_type
                current_equity = self.equity + pnl
            self.equity_curve.append(current_equity)

    # --- NOWE METODY POMOCNICZE ---
    def _close_partial(self):
        """Zamyka połowę pozycji na TP1."""
        size_to_close = self.position_size / 2
        profit = (self.tp1_price - self.entry_price) * size_to_close * self.position_type
        
        self.equity += profit - (size_to_close * self.tp1_price * self.fee_pct)
        self.position_size -= size_to_close
        self.is_partially_closed = True
        logger.debug(f"Zamknięto połowę pozycji na TP1 ({self.tp1_price}). Zysk: {profit:.2f}")

    def _move_sl_to_breakeven(self):
        """Przesuwa Stop Loss na cenę wejścia."""
        self.sl_price = self.entry_price
        logger.debug(f"Przesunięto SL na Break-Even ({self.entry_price}).")

    def buy(self, sl: float, tp: float, tp1: float, size: float = None):
        if self.in_position: return
        current_candle = self._data.iloc[self._strategy.i]
        self.position_type = 1
        self.entry_price = current_candle['Close']
        self.entry_date = current_candle.name
        self.sl_price = sl
        self.tp_price = tp # TP2
        self.tp1_price = tp1 # TP1
        self.is_partially_closed = False # Resetujemy flagę
        self.position_size = size or (self.equity * 0.98) / self.entry_price
        self.equity -= self.position_size * self.entry_price * self.fee_pct

    def close(self, reason: str):
        if not self.in_position: return
        current_candle = self._data.iloc[self._strategy.i]
        
        exit_price = self.sl_price if reason == 'SL' else self.tp_price if reason == 'TP' else current_candle['Close']
        
        profit = (exit_price - self.entry_price) * self.position_size * self.position_type
        self.equity += profit - (self.position_size * exit_price * self.fee_pct)
        
        # Zapisujemy transakcję dopiero po jej CAŁKOWITYM zamknięciu
        self.trades.append({
            'entry_date': self.entry_date, 'type': 'LONG' if self.position_type == 1 else 'SHORT',
            'entry_price': self.entry_price, 'exit_date': current_candle.name,
            'exit_price': exit_price, 'size': self.position_size, 'profit_usd': profit
        })
        # Resetujemy stan pozycji
        self.position_type = 0
        self.is_partially_closed = False

    # Metody _fetch_data, _calculate_results, sell, in_position, run pozostają bez większych zmian
    # ... (skrócono dla czytelności, kod jest taki sam jak w poprzedniej wersji) ...
    async def _fetch_data(self, symbol, timeframe, start_date, end_date):
        logger.info(f"Rozpoczynanie pobierania danych dla {symbol} ({timeframe})...")
        local_data = self.db_manager.get_ohlcv(symbol, timeframe, start_date, end_date)
        if local_data is not None and not local_data.empty:
            start_dt, end_dt = pd.to_datetime(start_date), pd.to_datetime(end_date)
            if local_data.index.min() <= start_dt and local_data.index.max() >= end_dt:
                self._data = local_data; await self.exchange.close(); return True
        logger.info("Pobieranie danych z giełdy...")
        try:
            since = self.exchange.parse8601(f"{start_date}T00:00:00Z")
            end_ts = self.exchange.parse8601(f"{end_date}T23:59:59Z")
            all_ohlcv = []
            limit = 1000
            while since < end_ts:
                ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
                if not ohlcv: break
                all_ohlcv.extend(ohlcv); since = ohlcv[-1][0] + 1
                if len(ohlcv) < limit: break
            if not all_ohlcv: return False
            df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df.columns = [c.capitalize() for c in df.columns]; df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')
            df.set_index('Timestamp', inplace=True); df.drop_duplicates(inplace=True)
            self.db_manager.save_ohlcv(df, symbol, timeframe); self._data = df[start_date:end_date]
            return True
        except Exception as e:
            logger.error(f"Błąd pobierania danych z giełdy: {e}"); return False
        finally:
            await self.exchange.close()

    def _calculate_results(self):
        if not self.trades: return {"Wiadomość": "Strategia nie wygenerowała żadnych transakcji."}, pd.DataFrame(), pd.Series()
        trades_df = pd.DataFrame(self.trades); equity_s = pd.Series(self.equity_curve, index=self._data.index[:len(self.equity_curve)])
        returns = equity_s.pct_change().dropna(); final_capital = self.equity; total_trades = len(self.trades)
        profits_usd = [t['profit_usd'] for t in self.trades]; winning_trades = sum(1 for p in profits_usd if p > 0)
        win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
        total_return_pct = ((final_capital / self.initial_capital) - 1) * 100
        buy_and_hold_return = ((self._data['Close'].iloc[-1] / self._data['Close'].iloc[0]) - 1) * 100
        rolling_max = equity_s.cummax(); drawdown = (equity_s - rolling_max) / rolling_max
        max_drawdown_pct = drawdown.min() * 100 if not drawdown.empty else 0
        sharpe_ratio = (returns.mean() / returns.std()) * np.sqrt(365) if returns.std() > 0 and returns.mean() > 0 else 0.0
        return {"Kapitał początkowy": f"${self.initial_capital:,.2f}", "Kapitał końcowy": f"${final_capital:,.2f}", "Całkowity zwrot (%)": f"{total_return_pct:.2f}%", "Benchmark 'Kup i Trzymaj' (%)": f"{buy_and_hold_return:.2f}%", "Liczba transakcji": total_trades, "Zyskowne transakcje (%)": f"{win_rate:.2f}%", "Maks. obsunięcie kapitału (%)": f"{max_drawdown_pct:.2f}%", "Sharpe Ratio (roczne)": f"{sharpe_ratio:.2f}"}, trades_df, equity_s

    def sell(self, sl: float, tp: float, size: float = None):
        if self.in_position: return; current_candle = self._data.iloc[self._strategy.i]; self.position_type = -1
        self.entry_price = current_candle['Close']; self.entry_date = current_candle.name; self.sl_price = sl; self.tp_price = tp
        self.position_size = size or (self.equity * 0.98) / self.entry_price; self.equity -= self.position_size * self.entry_price * self.fee_pct

    @property
    def in_position(self): return self.position_type != 0

    async def run(self, strategy_class: type[Strategy], symbol, timeframe, start_date, end_date, initial_capital=10000.0):
        self._reset_state(); self.initial_capital = initial_capital
        if not await self._fetch_data(symbol, timeframe, start_date, end_date): return {"Wiadomość": "Nie udało się pobrać danych."}, pd.DataFrame(), pd.Series()
        self._strategy = strategy_class(broker=self, data=self._data, settings_manager=self.settings_manager); self._strategy.init()
        loop = asyncio.get_event_loop(); await loop.run_in_executor(None, self._execute_loop)
        return self._calculate_results()
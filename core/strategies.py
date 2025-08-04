import pandas_ta as ta
from core.strategy import Strategy
from core.settings_manager import SettingsManager

# ... (klasy RsiOscillator i EmaCross bez zmian) ...
class RsiOscillator(Strategy):
    def __init__(self, broker, data, settings_manager: SettingsManager):
        super().__init__(broker, data, settings_manager)
    def init(self):
        self.rsi = self.I(ta.rsi, length=14)
    def next(self):
        current_rsi = self.rsi.iloc[self.i] 
        if not self._broker.in_position:
            if current_rsi < 30: self._broker.buy(sl=self.data.Close.iloc[self.i] * 0.98, tp=self.data.Close.iloc[self.i] * 1.04, tp1=None)
        elif self._broker.in_position:
            if current_rsi > 70: self._broker.close(reason='Signal')

class EmaCross(Strategy):
    def __init__(self, broker, data, settings_manager: SettingsManager):
        super().__init__(broker, data, settings_manager)
        self.fast_ema_len = 50; self.slow_ema_len = 200
    def init(self):
        self.ema_fast = self.I(ta.ema, length=self.fast_ema_len)
        self.ema_slow = self.I(ta.ema, length=self.slow_ema_len)
    def next(self):
        if self._broker.in_position: return
        if self.ema_fast.iloc[self.i-1] < self.ema_slow.iloc[self.i-1] and self.ema_fast.iloc[self.i] > self.ema_slow.iloc[self.i]:
            price = self.data.Close.iloc[self.i]
            self._broker.buy(sl=price * 0.95, tp=price * 1.10, tp1=None)

class AICloneStrategy(Strategy):
    def __init__(self, broker, data, settings_manager: SettingsManager):
        super().__init__(broker, data, settings_manager)
        strategy_params = self.settings.get('strategies.ai_clone', {})
        self.ema_fast_len = strategy_params.get('ema_fast_len', 30)
        self.ema_slow_len = strategy_params.get('ema_slow_len', 170)
        self.rsi_len = strategy_params.get('rsi_len', 14)
        self.atr_len = strategy_params.get('atr_len', 14)
        self.rsi_overbought = strategy_params.get('rsi_overbought', 75)
        self.atr_multiplier_sl = strategy_params.get('atr_multiplier_sl', 2.0)
        # Usunęliśmy parametry R:R, bo nie są już tu potrzebne

    def init(self):
        self.ema_fast = self.I(ta.ema, length=self.ema_fast_len)
        self.ema_slow = self.I(ta.ema, length=self.ema_slow_len)
        self.rsi = self.I(ta.rsi, length=self.rsi_len)
        self.atr = self.I(ta.atr, length=self.atr_len)

    def next(self):
        if self._broker.in_position:
            return

        macro_trend_is_bullish = self.data.Close.iloc[self.i] > self.ema_slow.iloc[self.i]
        if not macro_trend_is_bullish:
            return

        is_overbought = self.rsi.iloc[self.i] > self.rsi_overbought
        if not is_overbought:
            rsi_confirms_momentum = self.rsi.iloc[self.i] > 50
            if self.data.Low.iloc[self.i] < self.ema_fast.iloc[self.i] and \
               self.data.Close.iloc[self.i] > self.ema_fast.iloc[self.i] and \
               rsi_confirms_momentum:
                
                price = self.data.Close.iloc[self.i]
                sl = price - (self.atr.iloc[self.i] * self.atr_multiplier_sl)
                
                # --- ZMIANA: Przekazujemy tylko SL. TP wyznaczy broker. ---
                self._broker.buy(sl=sl)

class MeanReversionRSI(Strategy):
    """
    Strategia powrotu do średniej oparta na wskaźniku RSI.
    Kupuje, gdy rynek jest skrajnie wyprzedany.
    Sprzedaje (zamyka pozycję), gdy rynek wraca do neutralnego poziomu.
    """
    def __init__(self, broker, data, settings_manager: SettingsManager):
        super().__init__(broker, data, settings_manager)
        
        # --- ZMIANA: Wczytujemy parametry z pliku ustawień ---
        strategy_params = self.settings.get('strategies.mean_reversion_rsi', {})
        
        self.rsi_len = strategy_params.get('rsi_len', 14)
        self.rsi_oversold = strategy_params.get('rsi_oversold', 25)
        self.rsi_exit_level = strategy_params.get('rsi_exit_level', 50)
        self.atr_len = strategy_params.get('atr_len', 14)
        self.atr_multiplier_sl = strategy_params.get('atr_multiplier_sl', 2.0)

    def init(self):
        """Inicjalizujemy wskaźniki potrzebne dla strategii."""
        self.rsi = self.I(ta.rsi, length=self.rsi_len)
        self.atr = self.I(ta.atr, length=self.atr_len)

    def next(self):
        """Logika decyzyjna dla każdej świecy."""
        current_rsi = self.rsi.iloc[self.i]

        if not self._broker.in_position and current_rsi < self.rsi_oversold:
            price = self.data.Close.iloc[self.i]
            sl = price - (self.atr.iloc[self.i] * self.atr_multiplier_sl)
            
            # W tej strategii nie używamy sztywnego TP, więc ustawiamy go na None
            self._broker.buy(sl=sl, tp=None, tp1=None)
            return

        if self._broker.in_position and current_rsi > self.rsi_exit_level:
            self._broker.close(reason='RSI_EXIT')
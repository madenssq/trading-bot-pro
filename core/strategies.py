import pandas_ta as ta
from core.strategy import Strategy

class RsiOscillator(Strategy):
    def init(self):
        self.rsi = self.I(ta.rsi, length=14)

    def next(self):
        # POPRAWKA: Używamy `self.i` do patrzenia na bieżącą wartość RSI
        current_rsi = self.rsi.iloc[self.i] 

        if not self._broker.in_position:
            if current_rsi < 30:
                self._broker.buy(sl=self.data.Close.iloc[self.i] * 0.98, tp=self.data.Close.iloc[self.i] * 1.04)
        elif self._broker.in_position:
            if current_rsi > 70:
                self._broker.close(reason='Signal')

class EmaCross(Strategy):
    """
    Klasyczna strategia przecięcia średnich kroczących.
    - Kupuje, gdy szybsza średnia (50) przecina wolniejszą (200) od dołu (Złoty Krzyż).
    - Sprzedaje, gdy szybsza średnia przecina wolniejszą od góry (Krzyż Śmierci).
    """
    # --- Parametry ---
    fast_ema_len = 50
    slow_ema_len = 200

    def init(self):
        # Definiujemy obie średnie
        self.ema_fast = self.I(ta.ema, length=self.fast_ema_len)
        self.ema_slow = self.I(ta.ema, length=self.slow_ema_len)

    def next(self):
        """
        Wersja V3: Uproszczona logika wejścia i BRAK logiki wyjścia.
        Decyzje o zamknięciu pozycji podejmuje broker na podstawie SL/TP.
        """
        # Jeśli już jesteśmy w pozycji, nie robimy nic. Czekamy na SL/TP.
        if self._broker.in_position:
            return

        # --- Logika wejścia (pozostaje ta sama) ---

        # Filtr Reżimu Rynkowego
        macro_trend_is_bullish = self.data.Close.iloc[self.i] > self.ema_slow.iloc[self.i]
        if not macro_trend_is_bullish:
            return

        # Filtr "Przegrzania"
        is_overbought = self.rsi.iloc[self.i] > self.rsi_overbought
        if is_overbought:
            return

        # Warunek Wejścia: Odbicie od dynamicznego wsparcia (szybka średnia EMA)
        if self.data.Low.iloc[self.i] < self.ema_fast.iloc[self.i] and self.data.Close.iloc[self.i] > self.ema_fast.iloc[self.i]:
            price = self.data.Close.iloc[self.i]
            sl = price - (self.atr.iloc[self.i] * 1.5)
            tp = price + ((price - sl) * self.risk_reward_ratio)
            self._broker.buy(sl=sl, tp=tp)


class AICloneStrategy(Strategy):
    """
    Wersja V2: Ta strategia zawiera uproszczony Filtr Reżimu Rynkowego,
    naśladując główną logikę bota AI.
    """
    ema_fast_len = 21
    ema_slow_len = 50
    rsi_len = 14
    atr_len = 14
    rsi_overbought = 75 # Lekko poluzowane kryterium
    risk_reward_ratio = 2.0

    def init(self):
        """Definiujemy wskaźniki dla naszego coina."""
        self.ema_fast = self.I(ta.ema, length=self.ema_fast_len)
        self.ema_slow = self.I(ta.ema, length=self.ema_slow_len)
        self.rsi = self.I(ta.rsi, length=self.rsi_len)
        self.atr = self.I(ta.atr, length=self.atr_len)

        # Pobieramy dane dla BTC jako wskaźnika reżimu rynkowego
        # UWAGA: Backtester musi zostać rozbudowany, aby to obsłużyć.
        # Na razie symulujemy to, używając po prostu wolniejszej średniej.

    def next(self):
        """Logika decyzyjna dla każdej świecy."""
        if self._broker.in_position:
            # Prosta logika wyjścia: zamknij, gdy cena przetnie szybką średnią od góry
            if self.data.Close.iloc[self.i] < self.ema_fast.iloc[self.i]:
                self._broker.close(reason='Signal')
            return

        # --- NOWY KROK: FILTR REŻIMU RYNKOWEGO ---
        # Uproszczona wersja: używamy wolnej średniej jako wskaźnika ogólnego trendu
        macro_trend_is_bullish = self.data.Close.iloc[self.i] > self.ema_slow.iloc[self.i]

        if not macro_trend_is_bullish:
            return # Jeśli nie ma rynku byka, nie szukamy longów. Koniec.

        # --- Stara logika (uruchamiana tylko, gdy reżim jest poprawny) ---
        is_overbought = self.rsi.iloc[self.i] > self.rsi_overbought

        if not is_overbought:
            # Szukamy odbicia od dynamicznego wsparcia (szybka średnia EMA)
            if self.data.Low.iloc[self.i] < self.ema_fast.iloc[self.i] and self.data.Close.iloc[self.i] > self.ema_fast.iloc[self.i]:
                price = self.data.Close.iloc[self.i]
                sl = price - (self.atr.iloc[self.i] * 1.5)
                tp = price + ((price - sl) * self.risk_reward_ratio)
                self._broker.buy(sl=sl, tp=tp)
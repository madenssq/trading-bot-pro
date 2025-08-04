import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = "data"
LOGS_DIR = os.path.join(DATA_DIR, "logs")
CONFIG_DIR = os.path.join(DATA_DIR, "config")

# Pliki konfiguracyjne i danych
USER_ID_FILE = os.path.join(DATA_DIR, "user_id.json")
SYMBOLS_CACHE_FILE = os.path.join(DATA_DIR, "symbols_cache.json")
LOG_FILE = os.path.join(LOGS_DIR, "trading_bot.log")
USER_SETTINGS_FILE = os.path.join(CONFIG_DIR, "user_settings.json")
COOLDOWN_CACHE_FILE = os.path.join(DATA_DIR, "cooldown_cache.json")
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# Klucz do uwierzytelniania z Firebase
FIREBASE_ADMIN_SDK_KEY_PATH = "firebase-adminsdk.json"


# --- USTAWIENIA GIEŁD I DANYCH ---

# Lista giełd, z których CoinManager będzie pobierał dane
SUPPORTED_EXCHANGES = ["BINANCE", "BYBIT", "KUCOIN"]

# Standardowe ramy czasowe (interwały) używane w całej aplikacji
RAMY_CZASOWE = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d', '1w']

# Czas ważności pamięci podręcznej dla symboli (w sekundach)
# 24 godziny * 60 minut * 60 sekund = 1 dzień
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60


# --- DOMYŚLNE USTAWIENIA APLIKACJI ---

DEFAULT_SETTINGS = {
    "app": {
        "theme": "jasny",
        "background_path": "" # Domyślnie puste
    },
    "ai": {
        "url": "http://localhost:11434/v1/chat/completions",
        "model": "dolphin-pro",
        "temperature": 0.6,
        "max_tokens": 16384,
        "timeout": 300,
        "min_rr_ratio": 2.0,
        # --- NOWA SEKCJA ---
        "validation": {
            "max_tp_to_atr_ratio": 3.0,
            "golden_setup_min_confidence": 7
        }
    },
    "ssnedam": {
        "enabled": True,
        "group": "",
        "interval_minutes": 15,
        "alert_interval": "4h",
        "cooldown_minutes": 90,
        "scanner_prominence": 0.5,
        "scanner_distance": 10,
        "setup_expiration_candles": 12,
        # --- NOWE USTAWIENIA DLA SKANERA S/R ---
        "sr_scanner_prominence_multiplier": 0.5,
        "sr_scanner_distance": 10
    },
    "ai_context_modules": {
        "use_market_regime": True,
        "use_order_flow": True,
        "use_onchain_data": True,
        "use_performance_insights": False
    },
    "strategies": {
        "ai_clone": {
            "ema_fast_len": 30,
            "ema_slow_len": 170,
            "rsi_len": 14,
            "rsi_overbought": 75,
            "atr_len": 14,
            "atr_multiplier_sl": 2.0,
            "risk_reward_ratio_tp1": 1.5,
            "risk_reward_ratio_tp2": 2.0,
            "partial_close_pct": 50,
            "min_risk_reward_ratio_tp1": 0.8
        },
        # --- NOWA SEKCJA PONIŻEJ ---
        "mean_reversion_rsi": {
            "rsi_len": 14,
            "rsi_oversold": 25,
            "rsi_exit_level": 50,
            "atr_len": 14,
            "atr_multiplier_sl": 2.0
        }
    },
    "logging": {
        "level": "INFO",
        "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        "max_size_mb": 10,
        "backup_count": 3
    },
    "telegram": {
        "api_token": "",
        "chat_id": ""
    },
    "analysis": {
        "default_interval": "1h",
        "multi_timeframe_intervals": [
            "30m",
            "1h",
            "4h"
        ],
        "default_mode": "Antycypacja",
        "indicator_params": {
            "rsi_length": 14,
            "ema_fast_length": 50,
            "ema_slow_length": 200,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "bbands_length": 20,
            "bbands_std": 2.0,
            "atr_length": 14
        }
    },
    "cryptopanic": {
        "api_token": ""
    }
}



# --- STATYCZNE DANE APLIKACJI ---

# Definicje terminów technicznych używane w podpowiedziach (tooltips)
DEFINITIONS = {
    "RSI": "Relative Strength Index (Wskaźnik Siły Względnej) - wskaźnik pędu mierzący szybkość i zmianę ruchów cen. Wartości > 70 oznaczają wykupienie, a < 30 wyprzedanie.",
    "MACD": "Moving Average Convergence/Divergence - wskaźnik trendu i pędu. Przecięcie linii MACD i linii sygnałowej generuje sygnały kupna/sprzedaży.",
    "EMA": "Exponential Moving Average (Wykładnicza Średnia Krocząca) - średnia cena z określonego okresu, która przykłada większą wagę do nowszych danych.",
    "BOLINGER BANDS": "Wstęgi Bollingera - wskaźnik zmienności składający się z środkowej średniej kroczącej oraz dwóch zewnętrznych wstęg (odchylenia standardowe).",
    "OBV": "On-Balance Volume - wskaźnik mierzący skumulowaną presję kupna i sprzedaży. Rosnący OBV potwierdza siłę trendu wzrostowego.",
    "ATR": "Average True Range - miara zmienności rynkowej. Wyższy ATR oznacza większą zmienność i potencjalnie większe ruchy cenowe.",
    "PIVOT POINTS": "Punkty Obrotu - matematycznie obliczone, obiektywne poziomy wsparcia (S1, S2, S3) i oporu (R1, R2, R3).",
    "PRICE ACTION": "Analiza samego ruchu ceny na wykresie, formacji świecowych oraz struktury rynku bez użycia wskaźników.",
    "DYWERGENCJA": "Rozbieżność między ruchem ceny a wskaźnikiem (np. cena robi wyższy szczyt, a RSI niższy). Często zapowiada odwrócenie trendu."
}

# Symbole używane do określania ogólnego reżimu rynkowego
MARKET_REGIME_SYMBOLS = ['BTC/USDT', 'ETH/USDT']

# --- PARAMETRY ALGORYTMÓW ANALITYCZNYCH ---

# Symbol bazowy do obliczania siły względnej
RELATIVE_STRENGTH_BASE_SYMBOL = "BTC/USDT"
# Okres (w dniach) do obliczania siły względnej
RELATIVE_STRENGTH_LOOKBACK_DAYS = 7

# Okres (w świecach) do wyszukiwania swingu dla zniesień Fibonacciego
FIBONACCI_LOOKBACK_PERIOD = 100

# Domyślna maksymalna liczba świec do pobrania przez Backtester
BACKTESTER_MAX_CANDLES = 500

# Czas ważności pamięci podręcznej dla danych dashboardu (w sekundach)
# 15 minut * 60 sekund = 900 sekund
DASHBOARD_CACHE_LIFETIME_SECONDS = 15 * 60
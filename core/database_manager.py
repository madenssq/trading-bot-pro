import sqlite3
import pandas as pd
import logging
import time
import json
from datetime import datetime
from core.data_models import TradeData
from typing import Optional, List, Dict, Any

from app_config import DATA_DIR

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, db_name: str = "crypto_data.db"):
        if db_name == ":memory:":
            self.db_path = db_name
        else:
            self.db_path = f"{DATA_DIR}/{db_name}"
        
        self.conn: Optional[sqlite3.Connection] = None
        self._connect()
        self._create_tables()
        self._migrate_status_column()
        
        logger.info(f"Połączono z bazą danych i zweryfikowano wszystkie tabele: {self.db_path}")

    def _connect(self):
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.execute("PRAGMA foreign_keys = ON;")
        except sqlite3.Error as e:
            logger.error(f"Błąd połączenia z bazą danych SQLite: {e}"); raise

    def _create_tables(self):
        """Tworzy lub aktualizuje wszystkie tabele w bazie danych."""
        try:
            cursor = self.conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, entry_type TEXT NOT NULL,
                    symbol TEXT NOT NULL, interval TEXT NOT NULL, exchange TEXT NOT NULL, type TEXT, confidence INTEGER,
                    market_regime TEXT, momentum_status TEXT, approach_momentum_status TEXT,
                    entry_price REAL, stop_loss REAL, take_profit REAL, take_profit_1 REAL,
                    full_ai_response_json TEXT,
                    status TEXT NOT NULL DEFAULT 'POTENTIAL',
                    result TEXT, 
                    is_active INTEGER,
                    is_partially_closed INTEGER
                )""")
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trade_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id INTEGER NOT NULL, timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL, details_json TEXT,
                    FOREIGN KEY (trade_id) REFERENCES trades_log (id) ON DELETE CASCADE
                )""")
            
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_cache (
                symbol TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                data_json TEXT NOT NULL
            )""")

            cursor.execute("""CREATE TABLE IF NOT EXISTS ohlcv (symbol TEXT NOT NULL, timeframe TEXT NOT NULL, timestamp INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL, PRIMARY KEY (symbol, timeframe, timestamp))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS onchain_metrics (symbol TEXT NOT NULL, date TEXT NOT NULL, funding_rate REAL, open_interest_usd REAL, PRIMARY KEY (symbol, date))""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS saved_analyses (id INTEGER PRIMARY KEY AUTOINCREMENT, user_notes TEXT, status TEXT DEFAULT 'Obserwowane', analysis_data_json TEXT NOT NULL, ohlcv_df_json TEXT NOT NULL, save_timestamp REAL NOT NULL)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS chart_annotations (id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id INTEGER NOT NULL, item_type TEXT NOT NULL, properties_json TEXT NOT NULL, FOREIGN KEY (analysis_id) REFERENCES saved_analyses (id) ON DELETE CASCADE)""")
            
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Błąd podczas tworzenia/weryfikacji tabel: {e}"); raise

    def _migrate_status_column(self):
        """
        Jednorazowa migracja danych ze starych kolumn (result, is_active)
        do nowej, jednoznacznej kolumny 'status'.
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("PRAGMA table_info(trades_log)")
            columns = [info[1] for info in cursor.fetchall()]
            
            if 'status' not in columns:
                cursor.execute("ALTER TABLE trades_log ADD COLUMN status TEXT NOT NULL DEFAULT 'POTENTIAL'")
                self.conn.commit()
                logger.info("Dodano brakującą kolumnę 'status' do tabeli trades_log.")

            status_map = {
                ('PENDING', 0): 'POTENTIAL',
                ('PENDING', 1): 'ACTIVE',
                ('TP1_HIT_BE', None): 'CLOSED_BE',
                ('SL_HIT', None): 'CLOSED_SL',
                ('TP_HIT', None): 'CLOSED_TP',
                ('WYGASŁY', None): 'EXPIRED',
                ('ANULOWANY', None): 'CANCELLED'
            }
            
            update_cases = []
            for (old_result, old_is_active), new_status in status_map.items():
                if old_is_active is not None:
                    condition = f"result = '{old_result}' AND is_active = {old_is_active}"
                else:
                    condition = f"result = '{old_result}'"
                
                full_condition = f"({condition}) AND (status IS NULL OR status = 'POTENTIAL' OR status = 'ACTIVE')"
                update_cases.append(f"UPDATE trades_log SET status = '{new_status}' WHERE {full_condition}")
            
            migrated_count = 0
            for case in update_cases:
                cursor.execute(case)
                migrated_count += cursor.rowcount

            if migrated_count > 0:
                self.conn.commit()
                logger.info(f"Migracja Statusów: Pomyślnie zaktualizowano {migrated_count} wpisów do nowego formatu.")
            else:
                logger.info("Migracja Statusów: Nie znaleziono wpisów do aktualizacji.")

        except sqlite3.Error as e:
            logger.error(f"Błąd podczas migracji danych do nowej kolumny 'status': {e}")
            self.conn.rollback()

    # --- Poniżej wklej wszystkie pozostałe metody z Twojego pliku database_manager.py ---
    # np. add_log_entry, log_trade, get_trade_by_id, itd.
    
    def add_log_entry(self, entry_type: str, data: dict):
        if self.conn is None: return
        allowed_keys = [
            'timestamp', 'symbol', 'interval', 'exchange', 'type', 'confidence', 'market_regime',
            'momentum_status', 'approach_momentum_status', 'entry_price',
            'stop_loss', 'take_profit', 'take_profit_1', 'full_ai_response_json'
        ]
        columns = ['entry_type'] + [key for key in allowed_keys if key in data]
        placeholders = ', '.join('?' for _ in columns)
        query = f"INSERT INTO trades_log ({', '.join(columns)}) VALUES ({placeholders})"
        values = [entry_type] + [data.get(key) for key in columns[1:]]
        try:
            cursor = self.conn.cursor(); cursor.execute(query, values); self.conn.commit()
            logger.info(f"Pomyślnie zapisano wpis typu '{entry_type}' dla {data.get('symbol')} do bazy.")
        except sqlite3.Error as e:
            logger.error(f"Błąd zapisu do 'trades_log': {e}")

    def log_trade(self, trade_data: TradeData):
        data_dict = {k: v for k, v in trade_data.__dict__.items() if v is not None}
        self.add_log_entry('SETUP', data_dict)
    
    def log_analysis(self, analysis_data: dict):
        data_to_save = {
            'timestamp': time.time(),
            'symbol': analysis_data.get('symbol'),
            'interval': analysis_data.get('interval'),
            'exchange': analysis_data.get('exchange', 'BINANCE')
        }
        self.add_log_entry('ANALYSIS', data_to_save)

    def get_trade_by_id(self, trade_id: int) -> Optional[Dict[str, Any]]:
        if not self.conn: return None
        query = "SELECT * FROM trades_log WHERE id = ?"
        try:
            self.conn.row_factory = sqlite3.Row; cursor = self.conn.cursor(); cursor.execute(query, (trade_id,)); row = cursor.fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"Błąd pobierania transakcji o ID {trade_id}: {e}"); return None
        finally:
            self.conn.row_factory = None
            
    def get_all_trades(self, filters: dict) -> list:
        if not self.conn: return []
        query = "SELECT * FROM trades_log"
        conditions = []
        params = []

        for key, value in filters.items():
            if key == "entry_type":
                conditions.append("entry_type = ?")
                params.append(value)
            elif key == "symbol":
                conditions.append("symbol LIKE ?")
                params.append(f"%{value}%")
            elif key == "status":
                conditions.append("status = ?")
                params.append(value)
            # --- NOWY BLOK LOGIKI DLA WIELU STATUSÓW ---
            elif key == "status_in":
                if isinstance(value, (list, tuple)) and value:
                    placeholders = ', '.join('?' for _ in value)
                    conditions.append(f"status IN ({placeholders})")
                    params.extend(value)
            # --- KONIEC NOWEGO BLOKU ---
            elif key == "start_date":
                conditions.append("timestamp >= ?")
                params.append(value)
            elif key == "end_date":
                conditions.append("timestamp <= ?")
                params.append(value)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
            
        query += " ORDER BY timestamp DESC"
        
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Błąd podczas filtrowania transakcji: {e}")
            return []
        finally:
            self.conn.row_factory = None

    def close(self):
        if self.conn: self.conn.close(); self.conn = None; logger.info("Połączenie z bazą danych zostało zamknięte.")

    def log_trade_event(self, trade_id: int, event_type: str, details: Dict[str, Any]):
        if self.conn is None: return
        query = "INSERT INTO trade_events (trade_id, timestamp, event_type, details_json) VALUES (?, ?, ?, ?)"
        params = (trade_id, time.time(), event_type, json.dumps(details))
        try:
            cursor = self.conn.cursor(); cursor.execute(query, params); self.conn.commit()
            logger.info(f"Zapisano zdarzenie '{event_type}' dla transakcji ID {trade_id}.")
        except sqlite3.Error as e:
            logger.error(f"Błąd zapisu zdarzenia dla transakcji ID {trade_id}: {e}")

    def get_events_for_trade(self, trade_id: int) -> List[Dict[str, Any]]:
        if not self.conn: return []
        query = "SELECT * FROM trade_events WHERE trade_id = ? ORDER BY timestamp ASC"
        try:
            self.conn.row_factory = sqlite3.Row; cursor = self.conn.cursor(); cursor.execute(query, (trade_id,)); rows = cursor.fetchall()
            events = []
            for row in rows:
                event = dict(row)
                if event.get('details_json'): event['details'] = json.loads(event['details_json'])
                del event['details_json']
                events.append(event)
            return events
        except sqlite3.Error as e:
            logger.error(f"Błąd pobierania zdarzeń dla transakcji ID {trade_id}: {e}"); return []
        finally: self.conn.row_factory = None
    
    # --- NOWA METODA do aktualizacji statusu ---
    def update_trade_status(self, trade_id: int, new_status: str):
        if not self.conn: return
        query = "UPDATE trades_log SET status = ? WHERE id = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(query, (new_status, trade_id))
            self.conn.commit()
            logger.info(f"Zaktualizowano status transakcji ID {trade_id} na {new_status}.")
        except sqlite3.Error as e:
            logger.error(f"Błąd aktualizacji statusu dla transakcji ID {trade_id}: {e}")

    # --- NOWA METODA do aktualizacji SL przy częściowym zamknięciu ---
    def update_trade_sl(self, trade_id: int, new_sl_price: float):
        if not self.conn: return
        query = "UPDATE trades_log SET stop_loss = ? WHERE id = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(query, (new_sl_price, trade_id))
            self.conn.commit()
            logger.info(f"Zaktualizowano SL dla transakcji ID {trade_id} na {new_sl_price}.")
        except sqlite3.Error as e:
            logger.error(f"Błąd aktualizacji SL dla transakcji ID {trade_id}: {e}")

    def save_ohlcv(self, df: pd.DataFrame, symbol: str, timeframe: str):
        if df.empty or self.conn is None: return
        df_to_save = df.copy(); df_to_save.rename(columns=str.lower, inplace=True); df_to_save['symbol'] = symbol; df_to_save['timeframe'] = timeframe
        df_to_save['timestamp'] = df_to_save.index.astype(int) // 10**9
        try:
            df_to_save.to_sql('ohlcv', self.conn, if_exists='append', index=False, method=lambda table, conn, keys, data_iter: conn.executemany(f"INSERT OR IGNORE INTO {table.name} ({', '.join(keys)}) VALUES ({', '.join(['?'] * len(keys))})", data_iter))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Błąd zapisu danych OHLCV do bazy: {e}")
            
    def get_ohlcv(self, symbol, timeframe, start_date, end_date):
        if self.conn is None: return None
        start_ts = int(pd.to_datetime(start_date).timestamp()); end_ts = int(pd.to_datetime(f"{end_date} 23:59:59").timestamp())
        query = "SELECT * FROM ohlcv WHERE symbol = ? AND timeframe = ? AND timestamp BETWEEN ? AND ? ORDER BY timestamp ASC"
        try:
            df = pd.read_sql_query(query, self.conn, params=(symbol, timeframe, start_ts, end_ts))
            if df.empty: return None
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s'); df.set_index('timestamp', inplace=True); df.rename(columns=str.capitalize, inplace=True)
            return df
        except Exception as e:
            logger.error(f"Błąd odczytu danych OHLCV z bazy: {e}"); return None
        
    def get_open_trades(self) -> list:
        """Pobiera wszystkie transakcje, które nie są w stanie końcowym (do monitorowania przez PaperTradera)."""
        if not self.conn: return []
        # ZMIANA: Pobieramy wszystkie transakcje, które wymagają aktywnego śledzenia
        open_statuses = ('POTENTIAL', 'ACTIVE', 'PARTIAL_PROFIT')
        query = f"SELECT * FROM trades_log WHERE status IN {open_statuses} AND entry_type = 'SETUP'"
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Błąd pobierania otwartych transakcji: {e}")
            return []
        finally:
            self.conn.row_factory = None

    def update_trade_result(self, trade_id: int, result: str, symbol: str):
        """Aktualizuje wynik transakcji i JEDNOCZEŚNIE ją dezaktywuje."""
        if not self.conn: return
        # POPRAWKA: Dodajemy `is_active = 0` do zapytania.
        query = "UPDATE trades_log SET result = ?, is_active = 0 WHERE id = ?"
        try:
            cursor = self.conn.cursor(); cursor.execute(query, (result, trade_id)); self.conn.commit()
            logger.info(f"Zaktualizowano wynik transakcji dla {symbol} (ID: {trade_id}) na {result} i zamknięto pozycję.")
        except sqlite3.Error as e:
            logger.error(f"Błąd aktualizacji wyniku transakcji: {e}")

    def activate_trade(self, trade_id: int, symbol: str):
        if not self.conn: return
        query = "UPDATE trades_log SET is_active = 1 WHERE id = ?"
        try:
            cursor = self.conn.cursor(); cursor.execute(query, (trade_id,)); self.conn.commit()
            logger.info(f"Transakcja dla {symbol} (ID: {trade_id}) została AKTYWOWANA.")
        except sqlite3.Error as e:
            logger.error(f"Błąd aktywacji transakcji: {e}")

    def delete_trades(self, trade_ids: list):
        if not self.conn or not trade_ids: return
        placeholders = ', '.join('?' for _ in trade_ids); query = f"DELETE FROM trades_log WHERE id IN ({placeholders})"
        try:
            cursor = self.conn.cursor(); cursor.execute(query, trade_ids); self.conn.commit()
            logger.info(f"Usunięto {len(trade_ids)} transakcji z dziennika.")
        except sqlite3.Error as e:
            logger.error(f"Błąd podczas usuwania transakcji z dziennika: {e}")
    def get_golden_setups(self, limit: int = 3) -> list:
        if not self.conn: return []
        query = "SELECT * FROM trades_log WHERE entry_type = 'SETUP' AND result = 'TP_HIT' AND confidence >= 7 ORDER BY timestamp DESC LIMIT ?"
        try:
            self.conn.row_factory = sqlite3.Row; cursor = self.conn.cursor(); cursor.execute(query, (limit,)); rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Błąd podczas wyszukiwania złotych setupów: {e}"); return []
        finally: self.conn.row_factory = None

    def save_onchain_metrics(self, data: dict):
        if not self.conn: return
        query = "INSERT OR REPLACE INTO onchain_metrics (symbol, date, funding_rate, open_interest_usd) VALUES (?, ?, ?, ?)"
        try:
            cursor = self.conn.cursor(); cursor.execute(query, (data['symbol'], data['date'], data.get('funding_rate'), data.get('open_interest_usd'))); self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Błąd zapisu danych on-chain: {e}")

    def get_onchain_metrics(self, symbol: str, date_str: str) -> Optional[dict]:
        if not self.conn: return None
        query = "SELECT * FROM onchain_metrics WHERE symbol = ? AND date = ?"
        try:
            self.conn.row_factory = sqlite3.Row; cursor = self.conn.cursor(); cursor.execute(query, (symbol, date_str)); row = cursor.fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"Błąd odczytu danych on-chain: {e}"); return None
        finally: self.conn.row_factory = None

    def save_analysis_snapshot(self, analysis_data_json: str, ohlcv_df_json: str) -> Optional[int]:
        if not self.conn: return None
        query = "INSERT INTO saved_analyses (analysis_data_json, ohlcv_df_json, save_timestamp) VALUES (?, ?, ?)"
        try:
            cursor = self.conn.cursor(); timestamp = datetime.now().timestamp(); cursor.execute(query, (analysis_data_json, ohlcv_df_json, timestamp)); self.conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            logger.error(f"Błąd zapisu migawki analizy: {e}"); return None
        
    def get_all_saved_analyses(self) -> list:
        if not self.conn: return []
        query = "SELECT * FROM saved_analyses ORDER BY save_timestamp DESC"
        try:
            self.conn.row_factory = sqlite3.Row; cursor = self.conn.cursor(); cursor.execute(query); rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Błąd pobierania zapisanych analiz: {e}"); return []
        finally: self.conn.row_factory = None

    def update_snapshot_details(self, analysis_id: int, notes: str, status: str):
        if not self.conn: return
        query = "UPDATE saved_analyses SET user_notes = ?, status = ? WHERE id = ?"
        try:
            cursor = self.conn.cursor(); cursor.execute(query, (notes, status, analysis_id)); self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Błąd aktualizacji snapshotu: {e}")
    def add_annotation(self, analysis_id: int, item_type: str, properties_json: str) -> Optional[int]:
        if not self.conn: return None
        query = "INSERT INTO chart_annotations (analysis_id, item_type, properties_json) VALUES (?, ?, ?)"
        try:
            cursor = self.conn.cursor(); cursor.execute(query, (analysis_id, item_type, properties_json)); self.conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            logger.error(f"Błąd zapisu adnotacji: {e}"); return None
    def get_annotations_for_analysis(self, analysis_id: int) -> list:
        if not self.conn: return []
        query = "SELECT * FROM chart_annotations WHERE analysis_id = ?"
        try:
            self.conn.row_factory = sqlite3.Row; cursor = self.conn.cursor(); cursor.execute(query, (analysis_id,)); rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Błąd pobierania adnotacji: {e}"); return []
        finally: self.conn.row_factory = None
    def delete_annotation(self, annotation_id: int):
        if not self.conn: return
        query = "DELETE FROM chart_annotations WHERE id = ?"
        try:
            cursor = self.conn.cursor(); cursor.execute(query, (annotation_id,)); self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Błąd usuwania adnotacji: {e}")
    def does_trade_exist(self, trade_data: dict, time_window_minutes: int = 5) -> bool:
        if not self.conn: return False
        time_window_seconds = time_window_minutes * 60; current_timestamp = trade_data.get('timestamp', time.time())
        query = "SELECT 1 FROM trades_log WHERE symbol = ? AND interval = ? AND type = ? AND timestamp > ? LIMIT 1"
        params = (trade_data.get('symbol'), trade_data.get('interval'), trade_data.get('type'), current_timestamp - time_window_seconds)
        try:
            cursor = self.conn.cursor(); cursor.execute(query, params)
            return cursor.fetchone() is not None
        except sqlite3.Error as e:
            logger.error(f"Błąd podczas sprawdzania duplikatów setupu: {e}"); return False
        
    def get_dashboard_cache(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Pobiera dane z cache'a dashboardu dla danego symbolu."""
        if not self.conn: return None
        query = "SELECT timestamp, data_json FROM dashboard_cache WHERE symbol = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(query, (symbol,))
            row = cursor.fetchone()
            if row:
                timestamp, data_json = row
                return {"timestamp": timestamp, "data": json.loads(data_json)}
            return None
        except sqlite3.Error as e:
            logger.error(f"Błąd odczytu z cache'a dashboardu: {e}")
            return None

    def save_dashboard_cache(self, symbol: str, data: Dict[str, Any]):
        """Zapisuje dane dla symbolu do cache'a dashboardu."""
        if not self.conn: return
        query = "INSERT OR REPLACE INTO dashboard_cache (symbol, timestamp, data_json) VALUES (?, ?, ?)"
        params = (symbol, time.time(), json.dumps(data))
        try:
            cursor = self.conn.cursor()
            cursor.execute(query, params)
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Błąd zapisu do cache'a dashboardu: {e}")
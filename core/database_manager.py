# Plik: core/database_manager.py (WERSJA OSTATECZNA, POPRAWIONA)

import sqlite3
import pandas as pd
import logging
import time
from datetime import datetime
from typing import Optional

from app_config import DATA_DIR

logger = logging.getLogger(__name__)

class DatabaseManager:
    """Zarządza lokalną bazą danych SQLite do przechowywania danych OHLCV i logów transakcji."""

    def __init__(self, db_name: str = "crypto_data.db"):
        # POPRAWKA: Poprawna obsługa bazy danych w pamięci RAM na potrzeby testów
        if db_name == ":memory:":
            self.db_path = db_name
        else:
            self.db_path = f"{DATA_DIR}/{db_name}"
        
        self.conn: Optional[sqlite3.Connection] = None
        self._connect()
        self._create_onchain_metrics_table()
        self._create_ohlcv_table()
        self._create_trades_log_table()
        self._create_saved_analyses_table()
        self._create_annotations_table()
        logger.info(f"Połączono z lokalną bazą danych i zweryfikowano tabele: {self.db_path}")

    def _connect(self):
        """Nawiązuje połączenie z bazą danych."""
        try:
            self.conn = sqlite3.connect(self.db_path)
        except sqlite3.Error as e:
            logger.error(f"Błąd połączenia z bazą danych SQLite: {e}")
            raise

    def _create_ohlcv_table(self):
        """Tworzy tabelę na dane OHLCV, jeśli nie istnieje."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ohlcv (
                    symbol TEXT NOT NULL, timeframe TEXT NOT NULL, timestamp INTEGER NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                    close REAL NOT NULL, volume REAL NOT NULL,
                    PRIMARY KEY (symbol, timeframe, timestamp)
                )
            """)
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Błąd tworzenia tabeli 'ohlcv': {e}")

    def _create_trades_log_table(self):
        """Tworzy tabelę na logi transakcji i analiz."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    entry_type TEXT NOT NULL DEFAULT 'SETUP',
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    type TEXT,
                    confidence INTEGER,
                    market_regime TEXT,
                    momentum_status TEXT,
                    approach_momentum_status TEXT,
                    entry_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    result TEXT DEFAULT 'PENDING',
                    is_active INTEGER DEFAULT 0 NOT NULL
                )
            """)
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Błąd tworzenia tabeli 'trades_log': {e}")
    
    def add_log_entry(self, entry_type: str, data: dict):
        """Zapisuje nowy wpis do dziennika transakcji o określonym typie."""
        if self.conn is None: return

        allowed_keys = [
            'timestamp', 'symbol', 'interval', 'type', 'confidence', 'market_regime',
            'momentum_status', 'approach_momentum_status', 'entry_price',
            'stop_loss', 'take_profit', 'result', 'is_active'
        ]
        
        columns = ['entry_type'] + [key for key in allowed_keys if key in data]
        placeholders = ', '.join('?' for _ in columns)
        query = f"INSERT INTO trades_log ({', '.join(columns)}) VALUES ({placeholders})"
        
        values = [entry_type] + [data.get(key) for key in columns[1:]]

        try:
            cursor = self.conn.cursor()
            cursor.execute(query, values)
            self.conn.commit()
            logger.info(f"Pomyślnie zapisano wpis typu '{entry_type}' dla {data.get('symbol')} do bazy.")
        except sqlite3.Error as e:
            logger.error(f"Błąd zapisu do 'trades_log': {e}")

    def log_trade(self, trade_data: dict):
        """Zapisuje pojedynczy setup transakcyjny do dziennika."""
        # POPRAWKA: Usunięto zduplikowany kod, teraz jest tylko jedno wywołanie
        self.add_log_entry('SETUP', trade_data)

    def log_analysis(self, analysis_data: dict):
        """Zapisuje ogólną analizę do dziennika."""
        data_to_save = {
            'timestamp': datetime.now().timestamp(),
            'symbol': analysis_data.get('symbol'),
            'interval': analysis_data.get('interval'),
        }
        self.add_log_entry('ANALYSIS', data_to_save)

    # ... reszta metod (get_pending_trades, update_trade_result, etc.) pozostaje bez zmian ...
    # ... Kopiuję je poniżej dla kompletności ...

    def save_ohlcv(self, df: pd.DataFrame, symbol: str, timeframe: str):
        if df.empty or self.conn is None: return
        logger.info(f"Zapisywanie {len(df)} świec dla {symbol} ({timeframe}) do lokalnej bazy...")
        df_to_save = df.copy()
        df_to_save.rename(columns=str.lower, inplace=True)
        df_to_save['symbol'] = symbol
        df_to_save['timeframe'] = timeframe
        df_to_save['timestamp'] = df_to_save.index.astype(int) // 10**9
        try:
            df_to_save.to_sql('ohlcv', self.conn, if_exists='append', index=False,
                              method=lambda table, conn, keys, data_iter:
                              conn.executemany(f"INSERT OR IGNORE INTO {table.name} ({', '.join(keys)}) VALUES ({', '.join(['?'] * len(keys))})", data_iter))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Błąd zapisu danych OHLCV do bazy: {e}")

    def get_ohlcv(self, symbol, timeframe, start_date, end_date):
        if self.conn is None: return None
        logger.info(f"Pobieranie danych dla {symbol} ({timeframe}) z lokalnej bazy...")
        start_ts = int(pd.to_datetime(start_date).timestamp())
        end_ts = int(pd.to_datetime(f"{end_date} 23:59:59").timestamp())
        query = "SELECT * FROM ohlcv WHERE symbol = ? AND timeframe = ? AND timestamp BETWEEN ? AND ? ORDER BY timestamp ASC"
        try:
            df = pd.read_sql_query(query, self.conn, params=(symbol, timeframe, start_ts, end_ts))
            if df.empty:
                logger.warning("Brak danych w lokalnej bazie dla zadanego okresu.")
                return None
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
            df.set_index('timestamp', inplace=True)
            df.rename(columns=str.capitalize, inplace=True)
            logger.info(f"Pobrano {len(df)} świec z lokalnej bazy.")
            return df
        except Exception as e:
            logger.error(f"Błąd odczytu danych OHLCV z bazy: {e}")
            return None
    
    def get_pending_trades(self) -> list:
        if not self.conn: return []
        query = "SELECT * FROM trades_log WHERE result = 'PENDING' AND entry_type = 'SETUP'"
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Błąd pobierania oczekujących transakcji: {e}")
            return []
        finally:
            self.conn.row_factory = None

    def get_all_trades(self, filters: dict) -> list:
        if not self.conn: return []
        query = "SELECT * FROM trades_log"
        conditions = []
        params = []
        if filters.get("entry_type") in ['SETUP', 'ANALYSIS']:
            conditions.append("entry_type = ?")
            params.append(filters["entry_type"])
        if filters.get("symbol"):
            conditions.append("symbol LIKE ?")
            params.append(f"%{filters['symbol']}%")
        outcome_filter = filters.get("result")
        if outcome_filter == 'ACTIVE':
            conditions.append("is_active = 1 AND result = 'PENDING'")
        elif outcome_filter in ['TP_HIT', 'SL_HIT']:
            conditions.append("result = ?")
            params.append(outcome_filter)
        if filters.get("start_date"):
            conditions.append("timestamp >= ?")
            params.append(filters["start_date"])
        if filters.get("end_date"):
            conditions.append("timestamp <= ?")
            params.append(filters["end_date"])
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

    def update_trade_result(self, trade_id: int, result: str, symbol: str):
        if not self.conn: return
        query = "UPDATE trades_log SET result = ? WHERE id = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(query, (result, trade_id))
            self.conn.commit()
            logger.info(f"Zaktualizowano wynik transakcji dla {symbol} (ID: {trade_id}) na {result}.")
        except sqlite3.Error as e:
            logger.error(f"Błąd aktualizacji wyniku transakcji: {e}")

    def activate_trade(self, trade_id: int, symbol: str):
        if not self.conn: return
        query = "UPDATE trades_log SET is_active = 1 WHERE id = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(query, (trade_id,))
            self.conn.commit()
            logger.info(f"Transakcja dla {symbol} (ID: {trade_id}) została AKTYWOWANA (cena dotknęła wejścia).")
        except sqlite3.Error as e:
            logger.error(f"Błąd aktywacji transakcji: {e}")

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.info("Połączenie z bazą danych zostało zamknięte.")

    def delete_trades(self, trade_ids: list):
        if not self.conn or not trade_ids: return
        placeholders = ', '.join('?' for _ in trade_ids)
        query = f"DELETE FROM trades_log WHERE id IN ({placeholders})"
        try:
            cursor = self.conn.cursor()
            cursor.execute(query, trade_ids)
            self.conn.commit()
            logger.info(f"Usunięto {len(trade_ids)} transakcji z dziennika.")
        except sqlite3.Error as e:
            logger.error(f"Błąd podczas usuwania transakcji z dziennika: {e}")
            
    def get_golden_setups(self, limit: int = 3) -> list:
        if not self.conn: return []
        query = """
            SELECT * FROM trades_log
            WHERE
                entry_type = 'SETUP'
                AND result = 'TP_HIT'
                AND confidence >= 7
            ORDER BY
                timestamp DESC
            LIMIT ?
        """
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            cursor.execute(query, (limit,))
            rows = cursor.fetchall()
            logger.info(f"Znaleziono {len(rows)} 'złotych' setupów w bazie danych.")
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Błąd podczas wyszukiwania złotych setupów: {e}")
            return []
        finally:
            self.conn.row_factory = None

    def _create_onchain_metrics_table(self):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS onchain_metrics (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    funding_rate REAL,
                    open_interest_usd REAL,
                    PRIMARY KEY (symbol, date)
                )
            """)
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Błąd tworzenia tabeli 'onchain_metrics': {e}")

    def save_onchain_metrics(self, data: dict):
        if not self.conn: return
        query = "INSERT OR REPLACE INTO onchain_metrics (symbol, date, funding_rate, open_interest_usd) VALUES (?, ?, ?, ?)"
        try:
            cursor = self.conn.cursor()
            cursor.execute(query, (
                data['symbol'], data['date'], data.get('funding_rate'), data.get('open_interest_usd')
            ))
            self.conn.commit()
            logger.info(f"Zapisano dane on-chain dla {data['symbol']} na dzień {data['date']} do bazy.")
        except sqlite3.Error as e:
            logger.error(f"Błąd zapisu danych on-chain: {e}")

    def get_onchain_metrics(self, symbol: str, date_str: str) -> Optional[dict]:
        if not self.conn: return None
        query = "SELECT * FROM onchain_metrics WHERE symbol = ? AND date = ?"
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            cursor.execute(query, (symbol, date_str))
            row = cursor.fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"Błąd odczytu danych on-chain: {e}")
            return None
        finally:
            self.conn.row_factory = None

    def _create_saved_analyses_table(self):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS saved_analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_notes TEXT,
                    status TEXT DEFAULT 'Obserwowane',
                    analysis_data_json TEXT NOT NULL,
                    ohlcv_df_json TEXT NOT NULL,
                    save_timestamp REAL NOT NULL
                )
            """)
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Błąd tworzenia tabeli 'saved_analyses': {e}")

    def _create_annotations_table(self):
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chart_annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_id INTEGER NOT NULL,
                    item_type TEXT NOT NULL,
                    properties_json TEXT NOT NULL,
                    FOREIGN KEY (analysis_id) REFERENCES saved_analyses (id) ON DELETE CASCADE
                )
            """)
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Błąd tworzenia tabeli 'chart_annotations': {e}")

    def save_analysis_snapshot(self, analysis_data_json: str, ohlcv_df_json: str) -> Optional[int]:
        if not self.conn: return None
        query = """
            INSERT INTO saved_analyses 
            (analysis_data_json, ohlcv_df_json, save_timestamp) 
            VALUES (?, ?, ?)
        """
        try:
            cursor = self.conn.cursor()
            timestamp = datetime.now().timestamp()
            cursor.execute(query, (analysis_data_json, ohlcv_df_json, timestamp))
            self.conn.commit()
            logger.info(f"Pomyślnie zapisano nową migawkę analizy do bazy. ID: {cursor.lastrowid}")
            return cursor.lastrowid
        except sqlite3.Error as e:
            logger.error(f"Błąd zapisu migawki analizy: {e}")
            return None
        
    def get_all_saved_analyses(self) -> list:
        if not self.conn: return []
        query = "SELECT * FROM saved_analyses ORDER BY save_timestamp DESC"
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Błąd pobierania zapisanych analiz: {e}")
            return []
        finally:
            self.conn.row_factory = None

    def update_snapshot_details(self, analysis_id: int, notes: str, status: str):
        if not self.conn: return
        query = "UPDATE saved_analyses SET user_notes = ?, status = ? WHERE id = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(query, (notes, status, analysis_id))
            self.conn.commit()
            logger.info(f"Zaktualizowano szczegóły dla snapshotu o ID: {analysis_id}")
        except sqlite3.Error as e:
            logger.error(f"Błąd aktualizacji snapshotu: {e}")

    def add_annotation(self, analysis_id: int, item_type: str, properties_json: str):
        if not self.conn: return None
        query = "INSERT INTO chart_annotations (analysis_id, item_type, properties_json) VALUES (?, ?, ?)"
        try:
            cursor = self.conn.cursor()
            cursor.execute(query, (analysis_id, item_type, properties_json))
            self.conn.commit()
            logger.info(f"Dodano nową adnotację typu '{item_type}' do analizy ID: {analysis_id}")
            return cursor.lastrowid
        except sqlite3.Error as e:
            logger.error(f"Błąd zapisu adnotacji: {e}")
            return None

    def get_annotations_for_analysis(self, analysis_id: int) -> list:
        if not self.conn: return []
        query = "SELECT * FROM chart_annotations WHERE analysis_id = ?"
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            cursor.execute(query, (analysis_id,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Błąd pobierania adnotacji: {e}")
            return []
        finally:
            self.conn.row_factory = None

    def delete_annotation(self, annotation_id: int):
        if not self.conn: return
        query = "DELETE FROM chart_annotations WHERE id = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(query, (annotation_id,))
            self.conn.commit()
            logger.info(f"Usunięto adnotację o ID: {annotation_id}")
        except sqlite3.Error as e:
            logger.error(f"Błąd usuwania adnotacji: {e}")
            
    def does_trade_exist(self, trade_data: dict, time_window_minutes: int = 5) -> bool:
        if not self.conn: return False
        
        time_window_seconds = time_window_minutes * 60
        current_timestamp = trade_data.get('timestamp', time.time())
        
        query = """
            SELECT 1 FROM trades_log 
            WHERE symbol = ? 
            AND interval = ? 
            AND type = ?
            AND timestamp > ?
            LIMIT 1
        """
        params = (
            trade_data.get('symbol'),
            trade_data.get('interval'),
            trade_data.get('type'),
            current_timestamp - time_window_seconds
        )
        
        try:
            cursor = self.conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchone() is not None
        except sqlite3.Error as e:
            logger.error(f"Błąd podczas sprawdzania duplikatów setupu: {e}")
            return False
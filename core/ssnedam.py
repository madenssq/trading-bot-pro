# Plik: core/ssnedam.py

import asyncio
import io
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
import re
import os
import json
import pandas as pd
from app_config import COOLDOWN_CACHE_FILE


import httpx
import pyqtgraph as pg
import pyqtgraph.exporters
from PyQt6.QtGui import QImage
from PyQt6.QtCore import QBuffer
from core.ai_pipeline import AIPipeline
from core.performance_analyzer import PerformanceAnalyzer
from core.database_manager import DatabaseManager
from core.prompt_templates import (OBSERVER_PROMPT_TEMPLATE,
                                   STRATEGIST_PROMPT_TEMPLATE,
                                   TACTICIAN_PROMPT_TEMPLATE)

try:
    from plyer import notification
    PLYER_AVAILABLE = True
except ImportError:
    PLYER_AVAILABLE = False
    logging.warning("Biblioteka 'plyer' nie została znaleziona. Powiadomienia na pulpicie nie będą działać.")

from core.ai_client import AIClient, ParsedAIResponse
from core.analyzer import AnalysisResult, TechnicalAnalyzer
from core.indicator_service import IndicatorKeyGenerator
from core.news_client import CryptoPanicClient

logger = logging.getLogger(__name__)

@dataclass
class AlertData:
    """Struktura danych przechowująca kompletne informacje o alercie."""
    symbol: str
    interval: str
    setup_data: Dict[str, Any]
    context: str
    exchange: str
    raw_ai_response: str
    parsed_data: Dict[str, Any]
    alert_timestamp: float = 0.0
    fib_data: Dict[str, Any] = field(default_factory=dict)

class Ssnedam:
    def __init__(self, analyzer: TechnicalAnalyzer, ai_client: AIClient, performance_analyzer: PerformanceAnalyzer, news_client: Optional[CryptoPanicClient], db_manager: DatabaseManager, queue_update_callback: Callable[[int], None], global_analysis_lock: asyncio.Lock, status_update_callback: Callable, ai_pipeline: AIPipeline,):
        self.analyzer = analyzer
        self.ai_client = ai_client
        self.performance_analyzer = performance_analyzer
        self.news_client = news_client
        self.db_manager = db_manager
        self.ai_pipeline = ai_pipeline
        self.update_status = status_update_callback

        # ZMIANA: Wczytujemy cooldowny z pliku przy starcie
        self.alert_timestamps: Dict[str, float] = self._load_cooldowns()

        self.analysis_queue = asyncio.Queue()
        self.worker_task: Optional[asyncio.Task] = None
        self.queue_update_callback = queue_update_callback
        self.global_analysis_lock = global_analysis_lock
        logger.info("Ssnedam (System Powiadomień) zainicjalizowany z pamięcią trwałą.")

    def start_worker(self):
        """Uruchamia w tle pracownika (consumer), który przetwarza zadania z kolejki AI."""
        if self.worker_task is None or self.worker_task.done():
            logger.info("[Ssnedam] Uruchamianie pracownika AI w tle...")
            self.worker_task = asyncio.create_task(self._analysis_worker())

    async def _analysis_worker(self):
        logger.info("[Pracownik AI] Uruchomiono. Oczekuję na zadania...")
        while True:
            try:
                task_data = await self.analysis_queue.get()
                
                if task_data is None: # Sygnał do zakończenia pracy
                    logger.info("[Pracownik AI] Otrzymano sygnał zakończenia. Zamykanie.")
                    self.analysis_queue.task_done()
                    break

                symbol, interval = task_data['symbol'], task_data['interval']
                logger.info(f"✅ [Pracownik AI] Pobrałem nowe zadanie: Analiza dla {symbol} ({interval})")
                self.queue_update_callback(self.analysis_queue.qsize())
                
                async with self.global_analysis_lock:
                    logger.info(f"[Pracownik AI] Zdobyto globalną blokadę dla {symbol}. Rozpoczynam analizę.")
                    await self._generate_and_trigger_alert(
                        symbol=symbol, exchange=task_data['exchange'],
                        interval=interval, on_alert_callback=task_data['on_alert_callback'],
                        status_callback=self.update_status
                    )

                self.update_status("W gotowości...", False)
                
                logger.info(f"[Pracownik AI] Zwolniono globalną blokadę dla {symbol}.")
                self.analysis_queue.task_done()
                logger.info(f"✅ [Pracownik AI] Zadanie dla {symbol} zakończone. Pozostało w kolejce: {self.analysis_queue.qsize()}")
                self.queue_update_callback(self.analysis_queue.qsize())
            except asyncio.CancelledError:
                logger.info("[Pracownik AI] Zadanie przerwane. Zamykanie.")
                break
            except Exception as e:
                logger.error(f"[Pracownik AI] Wystąpił błąd w pętli pracownika: {e}", exc_info=True)

    def _is_on_cooldown(self, symbol: str) -> bool:
        cooldown_seconds = self.analyzer.settings.get('ssnedam.cooldown_minutes', 20) * 60
        last_alert_time = self.alert_timestamps.get(symbol, 0)
        return (time.time() - last_alert_time) < cooldown_seconds

    async def scan_for_alerts(self, coins_to_scan: List[Dict[str, str]], on_alert_callback: Callable[[AlertData], None]):
        """Skanuje podaną listę monet jedna po drugiej, aby zapewnić maksymalną stabilność."""
        if not coins_to_scan: return
        
        alert_interval = self.analyzer.settings.get('ssnedam.alert_interval', '1h')
        logger.info(f"[Ssnedam] Rozpoczynanie sekwencyjnego skanowania {len(coins_to_scan)} coinów na interwale {alert_interval}...")
        
        # --- NOWA LOGIKA: Przetwarzanie sekwencyjne ---
        for i, coin in enumerate(coins_to_scan):
            symbol = coin['symbol']
            logger.info(f"[Ssnedam] Skanowanie ({i+1}/{len(coins_to_scan)}): {symbol}")

            # Sprawdzamy cooldown PRZED kosztowną operacją
            if self._is_on_cooldown(symbol):
                logger.debug(f"[Ssnedam] Pomijam {symbol} - jest na cooldownie.")
                continue

            try:
                coin_setups = await self.analyzer.pattern_service.find_potential_setups(coin['symbol'], coin['exchange'], alert_interval)
                
                if not coin_setups:
                    continue

                # Przetwarzamy wynik od razu
                best_setup = coin_setups[0]
                logger.info(f"!!! [Ssnedam] WYKRYTO INTERAKCJĘ: {best_setup['details']}. Dodawanie zadania do kolejki AI...")
                
                task_data = {
                    'symbol': symbol, 'exchange': coin['exchange'],
                    'interval': best_setup['interval'], 'on_alert_callback': on_alert_callback
                }
                await self.analysis_queue.put(task_data)
                self.queue_update_callback(self.analysis_queue.qsize())
                self.alert_timestamps[symbol] = time.time()
                self._save_cooldowns()

            except Exception as e:
                logger.error(f"[Ssnedam] Błąd podczas skanowania {symbol}: {e}", exc_info=True)
                # Kontynuujemy z następną monetą nawet w razie błędu
                continue
        # --- KONIEC NOWEJ LOGIKI ---
                
        logger.info(f"[Ssnedam] Skanowanie zakończone. Aktualny rozmiar kolejki AI: {self.analysis_queue.qsize()}")


    async def _generate_and_trigger_alert(self, symbol: str, exchange: str, interval: str, on_alert_callback: Callable, status_callback: Callable):
        try:
            parsed_response, analysis_result, best_timeframe, context_data = await self.ai_pipeline.run(
                symbol, interval, exchange, status_callback
            )

            if not parsed_response or not analysis_result:
                logger.error(f"Pipeline AI nie zwrócił wyników dla alertu {symbol}.")
                return

            # Sprawdzamy, czy pipeline utworzył i dodał setup do odpowiedzi
            if 'setup' in parsed_response.parsed_data and parsed_response.parsed_data['setup']:
                alert = AlertData(
                    symbol=symbol,
                    interval=best_timeframe,
                    setup_data=parsed_response.parsed_data.get('setup'),
                    context=parsed_response.parsed_data.get('key_conclusions'),
                    exchange=exchange,
                    raw_ai_response="",
                    parsed_data=parsed_response.parsed_data,
                    alert_timestamp=time.time()
                )
                on_alert_callback(alert)
            else:
                logger.info(f"[{symbol}] Mimo walidacji AI, finalny setup nie został skonstruowany. Alert odrzucony.")

        except Exception as e:
            logger.error(f"[Ssnedam] Błąd podczas generowania alertu dla {symbol}: {e}", exc_info=True)

    

    def _format_telegram_caption(self, alert_data: AlertData) -> str:
        """NOWA WERSJA: Formatuje podpis na Telegram, sama oblicza R:R i poprawnie escapuje znaki."""
        setup = alert_data.setup_data
        if not setup:
            return f"🔔 *Nowy Alert dla {self._escape_markdown_v2(alert_data.symbol)}* \nBrak szczegółów setupu."

        r_r_ratio_text = "N/A"
        try:
            entry = float(setup.get('entry', 0))
            sl = float(setup.get('stop_loss', 0))
            tp1 = float(setup.get('take_profit', [0])[0])
            if (entry - sl) != 0:
                r_r_ratio = abs(tp1 - entry) / abs(entry - sl)
                r_r_ratio_text = f"{r_r_ratio:.2f}"
        except (ValueError, TypeError, IndexError, ZeroDivisionError):
            pass

        # Escapujemy wszystkie dynamiczne dane, które mogą zawierać znaki specjalne
        alert_type = self._escape_markdown_v2(setup.get('type', 'N/A'))
        symbol = self._escape_markdown_v2(alert_data.symbol)
        interval = self._escape_markdown_v2(f"({alert_data.interval})")
        trigger = self._escape_markdown_v2(setup.get('trigger_text', 'Brak'))
        stop_loss = self._escape_markdown_v2(f"{setup.get('stop_loss', 0):.4f}")
        tp1 = self._escape_markdown_v2(f"{setup.get('take_profit', [0.0])[0]:.4f}")
        confidence = setup.get('confidence', 'N/A')
        
        # R:R jest już bezpieczny, bo to liczba i kropka, ale dla spójności też escapujemy
        r_r_display = self._escape_markdown_v2(f"{r_r_ratio_text}:1")

        # Wnioski z analizy tekstowej też muszą być escapowane
        conclusions = self._escape_markdown_v2(alert_data.context)
        
        caption_parts = [
            f"🔔 *Nowy Alert: {alert_type} na {symbol} {interval}*",
            f"Wiarygodność: *{confidence}/10*",
            r"\-\-\-",
            f"*Trigger:* {trigger}",
            f"*Stop Loss:* `{stop_loss}`",
            f"*Take Profit 1:* `{tp1}`",
            f"*R:R dla TP1:* {r_r_display}"
        ]

        if conclusions:
            caption_parts.append(f"\n*Kluczowe Wnioski:*\n{conclusions}")
        
        return "\n".join(caption_parts)

    def _send_desktop_notification(self, title: str, message: str):
        """Wysyła powiadomienie na pulpit."""
        if PLYER_AVAILABLE:
            try:
                notification.notify(title=title, message=message, app_name="Asystent Handlowy", timeout=20)
            except Exception as e:
                logger.error(f"Nie udało się wysłać powiadomienia na pulpit: {e}")

    async def close(self):
        """Zamyka workera i anuluje wszystkie zadania w kolejce."""
        if self.worker_task and not self.worker_task.done():
            logger.info("[Ssnedam] Wysyłanie sygnału zamknięcia do pracownika...")
            await self.analysis_queue.put(None)
            try:
                await asyncio.wait_for(self.worker_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("[Ssnedam] Zamknięcie workera przekroczyło limit czasu. Anulowanie zadania.")
                self.worker_task.cancel()
            logger.info("[Ssnedam] Pracownik AI został zamknięty.")

    async def send_telegram_alert_with_album(self, alert_data: AlertData, images: List[bytes]):
        """
        Otrzymuje gotowe obrazki i wysyła je jako album na Telegram.
        """
        token = self.analyzer.settings.get('telegram.api_token')
        chat_id = self.analyzer.settings.get('telegram.chat_id')
        if not token or not chat_id:
            logger.warning("[Telegram] Brak tokenu API lub Chat ID.")
            return

        caption = self._format_telegram_caption(alert_data)
        
        # Przygotowanie grupy mediów do wysłania jako album
        media = []
        files = {}
        for i, image_bytes in enumerate(images):
            filename = f'chart_{i}.png'
            files[filename] = image_bytes
            media_item = {'type': 'photo', 'media': f'attach://{filename}'}
            # Podpis i tryb formatowania dodajemy tylko do pierwszego zdjęcia w albumie
            if i == 0:
                media_item['caption'] = caption
                media_item['parse_mode'] = 'MarkdownV2'
            media.append(media_item)

        try:
            url = f"https://api.telegram.org/bot{token}/sendMediaGroup"
            async with httpx.AsyncClient() as client:
                # Przesyłamy dane jako multipart/form-data
                response = await client.post(url, data={'chat_id': chat_id, 'media': json.dumps(media)}, files=files, timeout=90.0)
                response.raise_for_status()
            
            logger.info(f"Pomyślnie wysłano album z alertem dla {alert_data.symbol} na Telegram.")
            self._send_desktop_notification(
                title=f"Nowy Alert: {alert_data.symbol}",
                message=f"Setup: {alert_data.setup_data.get('type', 'N/A')}, Wiarygodność: {alert_data.setup_data.get('confidence', 'N/A')}/10"
            )
        except httpx.HTTPStatusError as e:
            logger.error(f"Błąd HTTP od Telegrama: {e.response.status_code} - {e.response.text}", exc_info=True)
        except Exception as e:
            logger.error(f"Nie udało się wysłać albumu na Telegram: {e}", exc_info=True)

    def _escape_markdown_v2(self, text: str) -> str:
        """Zabezpiecza wszystkie znaki specjalne wymagane przez Telegram MarkdownV2."""
        # To jest kompletna lista znaków, które wymagają 'escapowania'
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return "".join(f'\\{char}' if char in escape_chars else char for char in str(text))
    
    def _load_cooldowns(self) -> Dict[str, float]:
        """NOWA METODA: Wczytuje zapisane timestampy cooldownu z pliku."""
        try:
            if os.path.exists(COOLDOWN_CACHE_FILE):
                with open(COOLDOWN_CACHE_FILE, 'r') as f:
                    return json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Nie udało się wczytać pliku cooldown: {e}")
        return {}

    def _save_cooldowns(self):
        """NOWA METODA: Zapisuje aktualny stan cooldownu do pliku."""
        try:
            with open(COOLDOWN_CACHE_FILE, 'w') as f:
                json.dump(self.alert_timestamps, f)
        except IOError as e:
            logger.error(f"Nie udało się zapisać pliku cooldown: {e}")

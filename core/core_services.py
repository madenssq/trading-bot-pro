import asyncio
import logging
import os
# ZMIANA: Dodajemy import
from concurrent.futures import ThreadPoolExecutor

import firebase_admin
from firebase_admin import credentials, firestore, auth
from app_config import FIREBASE_ADMIN_SDK_KEY_PATH

from core.settings_manager import SettingsManager
from core.database_manager import DatabaseManager
from core.ai_client import AIClient
from core.performance_analyzer import PerformanceAnalyzer
from core.news_client import CryptoPanicClient
from core.coin_manager import CoinManager
from core.analyzer import TechnicalAnalyzer
from core.ai_pipeline import AIPipeline
from core.ssnedam import Ssnedam
from core.dashboard_handler import DashboardHandler
from core.paper_trader import PaperTrader

logger = logging.getLogger(__name__)

class CoreServices:
    def __init__(self, settings_manager: SettingsManager, global_analysis_lock: asyncio.Lock):
        logger.info("Inicjalizacja CoreServices...")
        # ZMIANA: Tworzymy naszą własną, zarządzaną pulę wątków
        self.thread_pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix='AppWorker')
        
        self.settings_manager = settings_manager
        self.global_analysis_lock = global_analysis_lock

        self.db, self.auth_admin_client = None, None
        try:
            if not firebase_admin._apps and os.path.exists(FIREBASE_ADMIN_SDK_KEY_PATH):
                cred = credentials.Certificate(FIREBASE_ADMIN_SDK_KEY_PATH)
                self.firebase_app = firebase_admin.initialize_app(cred)
                self.db = firestore.client()
                self.auth_admin_client = auth
                logger.info("Połączenie z Firebase zainicjalizowane pomyślnie.")
        except Exception as e:
            logger.error(f"Nie udało się zainicjalizować Firebase: {e}")

        self.db_manager = DatabaseManager()
        self.ai_client = AIClient(self.settings_manager)
        
        try:
            cp_token = self.settings_manager.get('cryptopanic.api_token')
            self.news_client = CryptoPanicClient(cp_token) if cp_token else None
        except ValueError:
            self.news_client = None

        self.performance_analyzer = PerformanceAnalyzer(self.db_manager)
        self.analyzer = TechnicalAnalyzer(self.settings_manager, self.db_manager, self.ai_client)
        # ZMIANA: Przekazujemy pulę wątków do CoinManagera
        self.coin_manager = CoinManager(db_client=self.db, auth_admin_client=self.auth_admin_client, analyzer=self.analyzer, thread_pool=self.thread_pool)
        self.ai_pipeline = AIPipeline(analyzer=self.analyzer, ai_client=self.ai_client, db_manager=self.db_manager, performance_analyzer=self.performance_analyzer)
        self.ssnedam = Ssnedam(analyzer=self.analyzer, ai_client=self.ai_client, performance_analyzer=self.performance_analyzer, news_client=self.news_client, db_manager=self.db_manager, ai_pipeline=self.ai_pipeline, global_analysis_lock=self.global_analysis_lock, queue_update_callback=lambda size: None, status_update_callback=lambda text, busy: None)
        self.dashboard_handler = DashboardHandler(self.analyzer, self.thread_pool)
        self.paper_trader = PaperTrader(self.db_manager, self.analyzer, self.global_analysis_lock)
        
        logger.info("Wszystkie serwisy rdzenia zostały pomyślnie zainicjalizowane.")

    async def shutdown(self):
        logger.info("Rozpoczynanie sekwencji zamykania serwisów rdzenia...")
        self.paper_trader.stop()
        shutdown_tasks = [ self.ssnedam.close(), self.analyzer.close_all_exchanges() ]
        await asyncio.gather(*shutdown_tasks, return_exceptions=True)
        self.db_manager.close()
        
        if hasattr(self, 'firebase_app'):
            try:
                firebase_admin.delete_app(self.firebase_app)
                logger.info("Aplikacja Firebase została zamknięta.")
            except Exception as e:
                logger.error(f"Błąd podczas zamykania aplikacji Firebase: {e}")
        
        # ZMIANA: Jawnie zamykamy naszą pulę wątków
        if self.thread_pool:
            # wait=False oznacza, że nie czekamy na zakończenie zadań
            self.thread_pool.shutdown(wait=True)
            logger.info("Pula wątków została zamknięta.")
            
        logger.info("Wszystkie serwisy rdzenia zamknięte.")
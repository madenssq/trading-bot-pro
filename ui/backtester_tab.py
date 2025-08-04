import asyncio
import logging
import pandas as pd
import pyqtgraph as pg

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QFormLayout, QLabel, QComboBox, QDateEdit,
    QDoubleSpinBox, QPushButton, QFrame, QTableWidget, QTableWidgetItem,
    QHeaderView, QVBoxLayout, QMessageBox
)
from PyQt6.QtCore import QDate

from app_config import RAMY_CZASOWE
from core.backtester import Backtester
from core.settings_manager import SettingsManager
from core.analyzer import TechnicalAnalyzer

from core.strategies import RsiOscillator, EmaCross, AICloneStrategy, MeanReversionRSI

logger = logging.getLogger(__name__)

class BacktesterTab(QWidget):
    """
    Widget zakładki Backtestera, hermetyzujący całą jej logikę i UI.
    """
    def __init__(self, settings_manager, analyzer, main_window, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.analyzer = analyzer
        self.main_window = main_window
        self.backtester = None
        
        # Słownik mapujący nazwy przyjazne dla użytkownika na klasy strategii
        self.strategy_descriptions = {
            "Klon Strategii AI (zalecane)": AICloneStrategy,
            "RSI (Kup <30, Sprzedaj >70)": RsiOscillator,
            "Złoty/Śmierci Krzyż (EMA 50/200)": EmaCross,
            "Powrót do Średniej (RSI < 25)": MeanReversionRSI,
        }

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        """Tworzy interfejs użytkownika dla zakładki."""
        main_layout = QHBoxLayout(self)
        
        # Lewy panel sterowania
        control_panel = QFrame(minimumWidth=300, maximumWidth=400)
        control_panel.setFrameShape(QFrame.Shape.StyledPanel)
        control_layout = QFormLayout(control_panel)
        control_layout.setContentsMargins(15, 15, 15, 15)
        control_layout.setSpacing(10)
        
        control_layout.addRow(QLabel("<h3>Ustawienia Backtestu</h3>"))
        
        self.bt_strategy_combo = QComboBox()
        self.bt_strategy_combo.addItems(self.strategy_descriptions.keys())
        control_layout.addRow("Strategia:", self.bt_strategy_combo)
        
        self.bt_symbol_combo = QComboBox()
        self.bt_symbol_combo.setPlaceholderText("Wybierz z listy...")
        control_layout.addRow("Symbol:", self.bt_symbol_combo)
        
        self.bt_interval_combo = QComboBox()
        self.bt_interval_combo.addItems(RAMY_CZASOWE)
        self.bt_interval_combo.setCurrentText("1d")
        control_layout.addRow("Interwał:", self.bt_interval_combo)
        
        self.bt_start_date = QDateEdit(calendarPopup=True)
        self.bt_start_date.setDate(QDate.currentDate().addYears(-1))
        control_layout.addRow("Data początkowa:", self.bt_start_date)
        
        self.bt_end_date = QDateEdit(calendarPopup=True)
        self.bt_end_date.setDate(QDate.currentDate())
        control_layout.addRow("Data końcowa:", self.bt_end_date)
        
        self.bt_initial_capital = QDoubleSpinBox(maximum=1_000_000, value=10_000, prefix="$ ")
        control_layout.addRow("Kapitał początkowy:", self.bt_initial_capital)
        
        self.bt_run_button = QPushButton("🚀 Uruchom Backtest")
        self.bt_run_button.setFixedHeight(40)
        control_layout.addRow(self.bt_run_button)

        # Prawy panel wyników
        results_panel = QWidget()
        results_layout = QVBoxLayout(results_panel)
        
        self.bt_plot_widget = pg.GraphicsLayoutWidget()
        results_layout.addWidget(self.bt_plot_widget, 3)
        
        self.bt_results_table = QTableWidget()
        self.bt_results_table.setColumnCount(2)
        self.bt_results_table.verticalHeader().setVisible(False)
        self.bt_results_table.horizontalHeader().setVisible(False)
        self.bt_results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        results_layout.addWidget(self.bt_results_table, 1)
        
        main_layout.addWidget(control_panel)
        main_layout.addWidget(results_panel)

    def _connect_signals(self):
        """Łączy sygnały z widgetów z odpowiednimi slotami."""
        self.bt_run_button.clicked.connect(self.run_backtest)

    def update_coin_list(self, coins: list):
        """Aktualizuje listę dostępnych symboli w ComboBox."""
        current_symbol = self.bt_symbol_combo.currentText()
        self.bt_symbol_combo.clear()
        self.bt_symbol_combo.addItems(sorted(coins))
        if current_symbol in coins:
            self.bt_symbol_combo.setCurrentText(current_symbol)

    def set_busy_state(self, is_busy: bool):
        """Włącza/wyłącza przycisk uruchomienia i pokazuje status."""
        self.bt_run_button.setEnabled(not is_busy)
        self.bt_run_button.setText("Pracuję..." if is_busy else "🚀 Uruchom Backtest")

    def run_backtest(self):
        """
        Pobiera parametry z UI i uruchamia proces backtestu w tle,
        pauzując inne zadania.
        """
        # --- Tworzymy zadanie asynchroniczne wewnątrz tej metody ---
        async def _backtest_task_wrapper():
            self.set_busy_state(True)
            self.main_window.pause_background_tasks() # PAUZUJEMY TŁO
            try:
                # Pobieramy parametry wewnątrz zadania
                strategy_class = self.strategy_descriptions[self.bt_strategy_combo.currentText()]
                symbol = self.bt_symbol_combo.currentText()
                interval = self.bt_interval_combo.currentText()
                start_date = self.bt_start_date.date().toString("yyyy-MM-dd")
                end_date = self.bt_end_date.date().toString("yyyy-MM-dd")
                capital = self.bt_initial_capital.value()

                if not symbol:
                    QMessageBox.warning(self, "Brak Danych", "Proszę wybrać symbol do backtestu.")
                    return

                if self.backtester is None:
                    self.backtester = Backtester(self.settings_manager)

                # Uruchamiamy właściwy backtest
                results, trades_df, equity_curve_series = await self.backtester.run(
                    strategy_class=strategy_class,
                    symbol=symbol,
                    timeframe=interval,
                    start_date=start_date,
                    end_date=end_date,
                    initial_capital=capital
                )

                if trades_df.empty:
                    QMessageBox.information(self, "Wynik Backtestu", results.get("Wiadomość", "Strategia nie wygenerowała żadnych transakcji."))
                else:
                    self._display_backtest_results(results, trades_df, equity_curve_series)

            except Exception as e:
                logger.error(f"Błąd podczas backtestu: {e}", exc_info=True)
                QMessageBox.critical(self, "Błąd Backtestu", f"Wystąpił krytyczny błąd:\n{e}")
            finally:
                self.set_busy_state(False)
                self.main_window.resume_background_tasks() # WZNAWIAMY TŁO

        # Uruchamiamy nasze zadanie w tle
        asyncio.create_task(_backtest_task_wrapper())

    async def _execute_backtest_task(self, strategy_class, symbol, interval, start_date, end_date, capital):
        """Asynchroniczna metoda wykonująca backtest i aktualizująca UI."""
        self.set_busy_state(True)
        try:
            results, trades_df, equity_curve_series = await self.backtester.run(
                strategy_class=strategy_class,
                symbol=symbol,
                timeframe=interval,
                start_date=start_date,
                end_date=end_date,
                initial_capital=capital
            )
            
            # Sprawdzamy, czy backtest cokolwiek wygenerował
            if trades_df.empty:
                QMessageBox.information(self, "Wynik Backtestu", results.get("Wiadomość", "Strategia nie wygenerowała żadnych transakcji."))
            else:
                self._display_backtest_results(results, trades_df, equity_curve_series)

        except Exception as e:
            logger.error(f"Błąd podczas backtestu: {e}", exc_info=True)
            QMessageBox.critical(self, "Błąd Backtestu", f"Wystąpił krytyczny błąd:\n{e}")
        finally:
            self.set_busy_state(False)

    def _display_backtest_results(self, results: dict, trades_df: pd.DataFrame, equity_curve_series: pd.Series):
        """Wyświetla wyniki backtestu na wykresie i w tabeli."""
        logger.info(f"Wyświetlanie wyników backtestu...")
        
        self.bt_plot_widget.clear()
        self.bt_results_table.setRowCount(0)

        # Wykres krzywej kapitału
        plot = self.bt_plot_widget.addPlot(title="Krzywa kapitału (Equity Curve)")
        plot.plot(equity_curve_series.index.astype('int64') // 10**9, equity_curve_series.values, pen='c', name='Kapitał')
        plot.showGrid(x=True, y=True, alpha=0.3)
        plot.addLegend()

        # Tabela metryk
        self.bt_results_table.setRowCount(len(results))
        for row, (key, value) in enumerate(results.items()):
            self.bt_results_table.setItem(row, 0, QTableWidgetItem(str(key)))
            self.bt_results_table.setItem(row, 1, QTableWidgetItem(str(value)))
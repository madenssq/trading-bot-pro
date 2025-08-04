from dataclasses import dataclass, field
from typing import Optional, Any, Dict

@dataclass
class TradeData:
    """
    Ustrukturyzowany model danych dla pojedynczego wpisu w dzienniku transakcji.
    """
    # Kluczowe, zawsze wymagane pola
    timestamp: float
    symbol: str
    interval: str
    exchange: str
    
    # Pola specyficzne dla setupu, opcjonalne
    entry_type: str = "SETUP"
    type: Optional[str] = None # 'Long' or 'Short'
    confidence: Optional[int] = None
    market_regime: Optional[str] = None
    momentum_status: Optional[str] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    take_profit_1: Optional[float] = None
    
    # Pole na pełną odpowiedź AI, która doprowadziła do tego setupu
    full_ai_response_json: Optional[str] = None

@dataclass
class ContextData:
    """
    Ustrukturyzowany model danych dla pełnego kontekstu rynkowego
    przekazywanego do AI.
    """
    market_regime: str
    order_flow_status: str
    intermediate_trend: str
    approach_momentum_status: str
    mean_reversion_status: str
    market_momentum_status: str
    onchain_data: Dict[str, Any]
    performance_insights: str
    devils_advocate_argument: str
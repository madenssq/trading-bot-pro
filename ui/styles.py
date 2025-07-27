from typing import Dict

THEMES: Dict[str, Dict[str, str]] = {
    "dark": {
        # Istniejący, niebieski motyw ciemny pozostaje bez zmian
        "BG_BASE": "#282c34",
        "BG_SECONDARY": "#21252b",
        "TEXT_PRIMARY": "#abb2bf",
        "TEXT_SECONDARY": "#5c6370",
        "BORDER_PRIMARY": "#3b4048",
        "HIGHLIGHT_PRIMARY": "#61afef",
        "HIGHLIGHT_SECONDARY": "#c678dd",
        "SUCCESS": "#98c379",
        "ERROR": "#e06c75",
        "CHART_BG": "#282c34",
        "CHART_FG": "#abb2bf"
    },
    "jasny": {
        # NOWY MOTYW "MIEDZIANY ONYKS"
        "BG_BASE": "#261E16",              # Głęboka, niemal czarna szarość
        "BG_SECONDARY": "#2A2A33",          # Ciemnoszary dla tła elementów
        "TEXT_PRIMARY": "#EAEAEA",          # Jasnoszary, czytelny tekst
        "TEXT_SECONDARY": "#8A8A8E",        # Mniej ważny tekst
        "BORDER_PRIMARY": "#43403B",        # Subtelna, ciemna ramka
        "HIGHLIGHT_PRIMARY": "#D4AF37",      # Główny kolor: Stare złoto / Mosiądz
        "HIGHLIGHT_SECONDARY": "#B87333",    # Kolor kontrastowy: Miedź
        "SUCCESS": "#6A994E",              # Stonowana, leśna zieleń
        "ERROR": "#BC4749",                # Stonowana czerwień
        "CHART_BG": "#1B1B1B",
        "CHART_FG": "#EAEAEA"
    }
}

def get_theme_stylesheet(theme_name: str, background_path: str = None) -> str:
    """
    Generuje kompletny, zmodernizowany arkusz stylów CSS.
    """
    theme = THEMES.get(theme_name.lower(), THEMES["dark"])
    formatted_bg_path = background_path.replace("\\", "/") if background_path else ""

    stylesheet = f"""
        /* --- OGÓLNY WYGLĄD --- */
        QWidget {{
            background-color: {theme['BG_BASE']};
            color: {theme['TEXT_PRIMARY']};
            font-family: "Segoe UI", "Arial", sans-serif;
            font-size: 13px;
        }}
        #MainWindow {{
            {'background-image: url("' + formatted_bg_path + '");' if formatted_bg_path else ''}
            background-position: center;
            background-attachment: fixed;
            background-repeat: no-repeat;
        }}

        /* --- PANELE I RAMKI --- */
        QFrame, QGroupBox {{
            border: 1px solid {theme['BORDER_PRIMARY']};
            border-radius: 8px; /* Zwiększone zaokrąglenie */
            margin-top: 12px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top center;
            padding: 0 10px;
        }}
        QSplitter::handle {{
            background: {theme['BG_SECONDARY']};
        }}
        QSplitter::handle:hover {{
            background: {theme['BORDER_PRIMARY']};
        }}

        /* --- POLA TEKSTOWE --- */
        QTextBrowser, QTextEdit, QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox {{
            background-color: {theme['BG_SECONDARY']};
            color: {theme['TEXT_PRIMARY']};
            border: 1px solid {theme['BORDER_PRIMARY']};
            border-radius: 6px; /* Zwiększone zaokrąglenie */
            padding: 8px; /* Zwiększony padding */
            selection-background-color: {theme['HIGHLIGHT_PRIMARY']};
            selection-color: white;
        }}
        QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
            border: 2px solid {theme['HIGHLIGHT_PRIMARY']}; /* Grubsza ramka po zaznaczeniu */
        }}

        /* --- PRZYCISKI --- */
        QPushButton {{
            background-color: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                            stop: 0 {theme['BG_SECONDARY']}, stop: 1 {theme['BORDER_PRIMARY']});
            color: {theme['TEXT_PRIMARY']};
            border: 1px solid {theme['BORDER_PRIMARY']};
            padding: 8px 16px;
            border-radius: 6px;
            font-weight: bold;
        }}
        QPushButton:hover {{
            background-color: {theme['BORDER_PRIMARY']};
            border: 1px solid {theme['HIGHLIGHT_PRIMARY']};
        }}
        QPushButton:pressed {{
            background-color: {theme['BG_BASE']};
            border: 1px solid {theme['HIGHLIGHT_PRIMARY']};
            color: {theme['HIGHLIGHT_PRIMARY']};
        }}
        
        /* --- ZAKŁADKI --- */
        QTabWidget::pane {{
            border: 1px solid {theme['BORDER_PRIMARY']};
            border-radius: 8px;
        }}
        QTabBar::tab {{
            background-color: transparent;
            color: {theme['TEXT_SECONDARY']};
            padding: 10px 20px;
            border-bottom: 2px solid transparent; /* Niewidoczna dolna ramka */
            margin-right: 2px;
        }}
        QTabBar::tab:hover {{
            color: {theme['TEXT_PRIMARY']};
            border-bottom: 2px solid {theme['BORDER_PRIMARY']};
        }}
        QTabBar::tab:selected {{
            background-color: {theme['BG_BASE']};
            color: {theme['HIGHLIGHT_PRIMARY']};
            font-weight: bold;
            border-bottom: 2px solid {theme['HIGHLIGHT_PRIMARY']};
        }}

        /* --- LISTY I TABELE --- */
        QListWidget, QTreeWidget, QTableWidget {{
            background-color: {theme['BG_BASE']};
            border: 1px solid {theme['BORDER_PRIMARY']};
            border-radius: 8px;
            alternate-background-color: {theme['BG_SECONDARY']};
        }}
        QHeaderView::section {{
            background-color: {theme['BG_SECONDARY']};
            border: 1px solid {theme['BORDER_PRIMARY']};
            padding: 6px;
            font-weight: bold;
        }}
        QScrollBar:vertical {{
            border: none;
            background: {theme['BG_SECONDARY']};
            width: 10px;
            margin: 0px 0px 0px 0px;
        }}
        QScrollBar::handle:vertical {{
            background: {theme['BORDER_PRIMARY']};
            min-height: 20px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {theme['HIGHLIGHT_PRIMARY']};
        }}
    """
    return stylesheet
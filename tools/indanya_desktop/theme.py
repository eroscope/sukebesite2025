APP_STYLE = r"""
* { font-family: "Yu Gothic UI", "Meiryo"; font-size: 13px; color: #17191c; }
QMainWindow, QWidget#root { background: #f3f5f6; }
QFrame#sidebar { background: #181a1d; border: 0; }
QLabel#brandName { color: white; font-size: 17px; font-weight: 800; }
QLabel#brandSub, QLabel#sideLabel, QLabel#sideFoot { color: #818990; font-size: 10px; font-weight: 700; }
QPushButton#navButton { color: #c9ced2; background: transparent; border: 0; border-radius: 5px; padding: 10px 12px; text-align: left; font-weight: 700; }
QPushButton#navButton:hover { color: white; background: #26292d; }
QPushButton#navButton:checked { color: #17191c; background: white; border-left: 4px solid #c72d22; padding-left: 8px; }
QFrame#topbar { background: white; border: 0; border-bottom: 1px solid #dfe3e6; }
QLabel#eyebrow { color: #c72d22; font-size: 10px; font-weight: 900; }
QLabel#pageTitle { font-size: 21px; font-weight: 800; }
QPushButton#siteLink { color: #137f78; background: #e5f4f2; border: 1px solid #b8dcd8; border-radius: 4px; padding: 8px 11px; font-weight: 700; }
QPushButton#siteLink:hover { background: #d5eeeb; }
QFrame#panel { background: white; border: 1px solid #c5cbd0; border-radius: 5px; }
QFrame#accentPanel { background: white; border: 1px solid #c5cbd0; border-top: 3px solid #17191c; border-radius: 5px; }
QLabel#sectionTitle { font-size: 18px; font-weight: 800; }
QLabel#muted { color: #687078; }
QLabel#metric { font-size: 26px; font-weight: 800; }
QLabel#success { color: #137f78; font-weight: 800; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QTableWidget { background: white; border: 1px solid #c5cbd0; border-radius: 4px; padding: 8px; selection-background-color: #137f78; }
QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border: 2px solid #137f78; }
QComboBox { min-height: 22px; }
QPushButton#primary { color: white; background: #181a1d; border: 0; border-radius: 4px; padding: 10px 16px; font-weight: 800; }
QPushButton#primary:hover { background: #2b2f33; }
QPushButton#primary:disabled { background: #8b9094; }
QPushButton#danger { color: white; background: #c72d22; border: 0; border-radius: 4px; padding: 9px 14px; font-weight: 800; }
QPushButton#secondary { color: #17191c; background: white; border: 1px solid #c5cbd0; border-radius: 4px; padding: 9px 14px; font-weight: 700; }
QPushButton#secondary:hover { background: #f3f5f6; }
QProgressBar { background: #e7eaec; border: 0; border-radius: 3px; height: 8px; text-align: center; color: transparent; }
QProgressBar::chunk { background: #137f78; border-radius: 3px; }
QHeaderView::section { background: #f3f5f6; border: 0; border-bottom: 1px solid #dfe3e6; padding: 9px; font-weight: 800; }
QTableWidget { gridline-color: #e5e8ea; padding: 0; }
QTableWidget::item { padding: 7px; }
QTableWidget::item:selected { background: #e5f4f2; color: #17191c; }
QTabWidget::pane { border: 1px solid #c5cbd0; background: white; }
QTabBar::tab { background: #e9ecee; padding: 9px 16px; border: 1px solid #c5cbd0; }
QTabBar::tab:selected { background: white; color: #c72d22; font-weight: 800; }
QScrollArea { border: 0; background: transparent; }
"""


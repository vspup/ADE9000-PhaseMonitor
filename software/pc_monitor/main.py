import sys

import pyqtgraph as pg
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def _dark_palette(app: QApplication) -> None:
    app.setStyle('Fusion')
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(28, 28, 28))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Base,            QColor(18, 18, 18))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(35, 35, 35))
    p.setColor(QPalette.ColorRole.Text,            QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Button,          QColor(45, 45, 45))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(42, 130, 218))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    app.setPalette(p)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName('ADE9000 Phase Monitor')

    _dark_palette(app)

    # Must be set before any GraphicsView is created
    pg.setConfigOptions(background='#1c1c1c', foreground='#dcdcdc')

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()

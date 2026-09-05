"""Точка входа для PyInstaller.

Отдельный файл, а не `-m snapreel`: замороженному бинарнику нужен обычный
скрипт, который PyInstaller возьмёт корнем графа импортов.
"""

import sys

from snapreel.cli import main

if __name__ == "__main__":
    sys.exit(main())

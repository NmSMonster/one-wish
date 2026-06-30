"""Konfiguracja pytest — gwarantuje, że katalog projektu jest na sys.path,
żeby `import backend...` działał niezależnie od miejsca uruchomienia."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

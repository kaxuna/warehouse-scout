"""SQL dialect translation behind one interface, so backends can be compared."""

from warehouse_scout.translate.base import Translation, Translator
from warehouse_scout.translate.sqlglot_backend import SqlglotTranslator

__all__ = ["SqlglotTranslator", "Translation", "Translator"]

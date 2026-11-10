"""Classificação ABC de Pareto, cobertura e ponto de pedido com frações exatas e memória de cálculo."""

__version__ = "1.0.0"

from .core import InventoryResult, analisar
from .errors import EstoqueError
from .reports import analisar_json, build_report, write_report

__all__ = [
    "EstoqueError",
    "InventoryResult",
    "analisar",
    "analisar_json",
    "build_report",
    "write_report",
    "__version__",
]

"""Download public Russian business registry data without API credentials."""

from .service import parse_company
from .compact import compact_result

__all__ = ["parse_company", "compact_result"]

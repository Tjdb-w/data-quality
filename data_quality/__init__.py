"""data_quality: standalone data quality rule validation.

Lineage tracing is intentionally out of scope for this version.
"""

from .validator import (
    InvalidInputError,
    InvalidRuleError,
    DataQualityError,
    validate,
)

__all__ = [
    "validate",
    "DataQualityError",
    "InvalidInputError",
    "InvalidRuleError",
]

__version__ = "0.1.0"

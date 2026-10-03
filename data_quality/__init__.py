"""data_quality: standalone data quality rule validation and lineage tracing."""

from .correlation import (
    InvalidCorrelationInputError,
    UnknownCorrelationReferenceError,
    correlate_violations,
)
from .field_lineage import (
    InvalidFieldLineageInputError,
    InvalidFieldLineageQueryError,
    UnknownFieldLineageTargetError,
    trace_field_lineage,
)
from .lineage import (
    InvalidLineageInputError,
    InvalidLineageQueryError,
    UnknownLineageTargetError,
    trace_lineage,
)
from .validator import (
    InvalidInputError,
    InvalidRuleError,
    DataQualityError,
    validate,
)

__all__ = [
    "validate",
    "trace_lineage",
    "trace_field_lineage",
    "correlate_violations",
    "DataQualityError",
    "InvalidInputError",
    "InvalidRuleError",
    "InvalidLineageInputError",
    "InvalidLineageQueryError",
    "UnknownLineageTargetError",
    "InvalidFieldLineageInputError",
    "InvalidFieldLineageQueryError",
    "UnknownFieldLineageTargetError",
    "InvalidCorrelationInputError",
    "UnknownCorrelationReferenceError",
]

__version__ = "0.4.0"

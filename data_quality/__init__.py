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
from .lineage_paths import (
    InvalidLineagePathInputError,
    InvalidLineagePathQueryError,
    explain_lineage_paths,
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
    "explain_lineage_paths",
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
    "InvalidLineagePathInputError",
    "InvalidLineagePathQueryError",
]

__version__ = "0.5.0"

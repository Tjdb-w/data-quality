"""data_quality: standalone data quality rule validation and lineage tracing."""

from .correlation import (
    InvalidCorrelationInputError,
    UnknownCorrelationReferenceError,
    correlate_violations,
)
from .field_impact import (
    ImpactInputError,
    analyze_field_impacts,
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
    LineageNodeNotFoundError,
    explain_field_lineage_paths,
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
    "explain_lineage_paths",
    "explain_field_lineage_paths",
    "correlate_violations",
    "analyze_field_impacts",
    "DataQualityError",
    "InvalidInputError",
    "InvalidRuleError",
    "ImpactInputError",
    "InvalidLineageInputError",
    "InvalidLineageQueryError",
    "UnknownLineageTargetError",
    "InvalidFieldLineageInputError",
    "InvalidFieldLineageQueryError",
    "UnknownFieldLineageTargetError",
    "LineageNodeNotFoundError",
    "InvalidCorrelationInputError",
    "UnknownCorrelationReferenceError",
]

__version__ = "0.6.0"

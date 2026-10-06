"""data_quality: standalone data quality rule validation and lineage tracing."""

from .composite import (
    CompositeRuleSetError,
    DuplicateRuleIdError,
    InvalidCompositeRuleError,
    InvalidRecordReferenceError,
    InvalidSeverityError,
    UnsupportedCompositeConditionError,
    composite_results_to_impact_inputs,
    evaluate_composite_rules,
    query_composite_results,
    register_composite_rules,
    sample_id_for_record,
)
from .correlation import (
    InvalidCorrelationInputError,
    InvalidLinkedCorrelationInputError,
    UnknownCorrelationReferenceError,
    UnknownLinkedCorrelationReferenceError,
    correlate_linked_violations,
    correlate_violations,
)
from .exemptions import (
    InvalidExemptionError,
    apply_violation_exemptions,
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
from .origin_analysis import (
    InvalidOriginInputError,
    UnknownOriginReferenceError,
    UnknownOriginTargetError,
    analyze_violation_origins,
)
from .profiler import (
    InvalidProfileInputError,
    profile_records,
)
from .quality_gates import (
    InvalidQualityGateRuleError,
    UnknownQualityGateSourceError,
    evaluate_quality_gates,
)
from .reference_integrity import (
    InvalidReferenceInputError,
    InvalidReferenceRuleError,
    UnknownReferenceDatasetError,
    validate_references,
)
from .snapshot_diff import (
    InvalidSnapshotInputError,
    UnknownSnapshotReferenceError,
    compare_quality_snapshots,
)
from .validator import (
    InvalidInputError,
    InvalidRuleError,
    DataQualityError,
    validate,
)

__all__ = [
    "validate",
    "profile_records",
    "trace_lineage",
    "trace_field_lineage",
    "explain_lineage_paths",
    "explain_field_lineage_paths",
    "correlate_violations",
    "correlate_linked_violations",
    "analyze_violation_origins",
    "analyze_field_impacts",
    "compare_quality_snapshots",
    "validate_references",
    "evaluate_quality_gates",
    "apply_violation_exemptions",
    "register_composite_rules",
    "evaluate_composite_rules",
    "query_composite_results",
    "composite_results_to_impact_inputs",
    "sample_id_for_record",
    "DataQualityError",
    "InvalidInputError",
    "InvalidRuleError",
    "InvalidProfileInputError",
    "CompositeRuleSetError",
    "DuplicateRuleIdError",
    "InvalidSeverityError",
    "UnsupportedCompositeConditionError",
    "InvalidCompositeRuleError",
    "InvalidRecordReferenceError",
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
    "InvalidLinkedCorrelationInputError",
    "UnknownLinkedCorrelationReferenceError",
    "InvalidOriginInputError",
    "UnknownOriginReferenceError",
    "UnknownOriginTargetError",
    "InvalidSnapshotInputError",
    "UnknownSnapshotReferenceError",
    "InvalidReferenceInputError",
    "InvalidReferenceRuleError",
    "UnknownReferenceDatasetError",
    "InvalidQualityGateRuleError",
    "UnknownQualityGateSourceError",
    "InvalidExemptionError",
]

__version__ = "0.9.0"

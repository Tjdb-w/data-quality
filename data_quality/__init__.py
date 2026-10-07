"""data_quality: standalone data quality rule validation and lineage tracing."""

from .batch_rules import (
    InvalidBatchInputError,
    InvalidBatchRuleError,
    evaluate_batch_rules,
)
from .change_impact import (
    ChangeImpactDatasetNotFoundError,
    ChangeImpactDuplicateFieldError,
    ChangeImpactError,
    ChangeImpactFieldNotFoundError,
    ChangeImpactInputError,
    ChangeImpactInvalidFieldsError,
    ChangeImpactInvalidRenameError,
    analyze_change_impact,
)
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
from .lineage_diff import (
    InvalidLineageDiffQueryError,
    InvalidLineageSnapshotError,
    UnknownLineageDiffTargetError,
    compare_lineage_snapshots,
)
from .lineage_paths import (
    LineageNodeNotFoundError,
    explain_field_lineage_paths,
    explain_lineage_paths,
)
from .linked_origin_analysis import (
    LinkedOriginInputError,
    LinkedOriginReferenceError,
    LinkedOriginTargetError,
    analyze_linked_origins,
)
from .origin_analysis import (
    InvalidOriginInputError,
    UnknownOriginReferenceError,
    UnknownOriginTargetError,
    analyze_violation_origins,
)
from .profiling import InvalidProfileInputError, profile_records
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
from .sample_lineage import (
    InvalidSampleLineageInputError,
    InvalidSampleLineageQueryError,
    UnknownSampleLineageReferenceError,
    UnknownSampleLineageTargetError,
    trace_sample_lineage,
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
    "evaluate_batch_rules",
    "analyze_change_impact",
    "trace_lineage",
    "trace_field_lineage",
    "trace_sample_lineage",
    "explain_lineage_paths",
    "explain_field_lineage_paths",
    "correlate_violations",
    "correlate_linked_violations",
    "analyze_violation_origins",
    "analyze_linked_origins",
    "analyze_field_impacts",
    "compare_quality_snapshots",
    "compare_lineage_snapshots",
    "validate_references",
    "evaluate_quality_gates",
    "profile_records",
    "apply_violation_exemptions",
    "register_composite_rules",
    "evaluate_composite_rules",
    "query_composite_results",
    "composite_results_to_impact_inputs",
    "sample_id_for_record",
    "DataQualityError",
    "InvalidInputError",
    "InvalidRuleError",
    "InvalidBatchInputError",
    "InvalidBatchRuleError",
    "ChangeImpactError",
    "ChangeImpactInputError",
    "ChangeImpactDatasetNotFoundError",
    "ChangeImpactFieldNotFoundError",
    "ChangeImpactInvalidRenameError",
    "ChangeImpactDuplicateFieldError",
    "ChangeImpactInvalidFieldsError",
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
    "InvalidSampleLineageInputError",
    "InvalidSampleLineageQueryError",
    "UnknownSampleLineageTargetError",
    "UnknownSampleLineageReferenceError",
    "LineageNodeNotFoundError",
    "InvalidCorrelationInputError",
    "UnknownCorrelationReferenceError",
    "InvalidLinkedCorrelationInputError",
    "UnknownLinkedCorrelationReferenceError",
    "InvalidOriginInputError",
    "UnknownOriginReferenceError",
    "UnknownOriginTargetError",
    "LinkedOriginInputError",
    "LinkedOriginReferenceError",
    "LinkedOriginTargetError",
    "InvalidSnapshotInputError",
    "UnknownSnapshotReferenceError",
    "InvalidLineageSnapshotError",
    "InvalidLineageDiffQueryError",
    "UnknownLineageDiffTargetError",
    "InvalidReferenceInputError",
    "InvalidReferenceRuleError",
    "UnknownReferenceDatasetError",
    "InvalidQualityGateRuleError",
    "UnknownQualityGateSourceError",
    "InvalidProfileInputError",
    "InvalidExemptionError",
]

__version__ = "0.9.0"

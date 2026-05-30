"""The Dead-Letter Quarantine core: a threshold-gated row processor (ADR-0019).

This wraps a File's row stream when quarantine is enabled. A row that fails
Transform or Field Encryption is routed to the Quarantine sink and the stream
continues — instead of raising and failing the whole File (Strict Mode). At
end-of-stream the configured threshold is evaluated against the now-known totals;
if exceeded the processor **raises** ``QuarantineThresholdExceeded``, which
propagates out of the Connector write *before* it commits (Streaming Load commits
only at end-of-stream, ADR-0001) and rolls the whole File back — nothing
committed, sidecar discarded. Under the threshold, the stream completes and the
caller commits the good rows, then finalizes the sink.

The processor is independent of the Connector and Audit DB: it transforms rows,
captures the bad ones, and decides the threshold. It does not finalize the sink
(the caller does that only after a successful commit).
"""

from typing import Any, Callable, Dict, Iterator, List, Optional

from filedge.config import ColumnMapping, QuarantineConfig
from filedge.field_crypto import FieldCryptoError
from filedge.quarantine.sink import QuarantineSink
from filedge.transform import TransformError, transform_row

_ROW_ERRORS = (TransformError, FieldCryptoError)


class QuarantineThresholdExceeded(Exception):
    """Raised at end-of-stream when bad rows exceed the configured threshold.

    Propagates out of the Connector write before commit, so the whole File is
    rolled back (nothing committed) — preserving the Strict Mode signal for a
    systemically bad File.
    """


def quarantining_rows(
    rows: Iterator[Dict[str, Any]],
    columns: List[ColumnMapping],
    quarantine: QuarantineConfig,
    sink: QuarantineSink,
    *,
    post_transform: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Iterator[Dict[str, Any]]:
    """Yield good transformed rows; quarantine bad ones; raise at end if over threshold.

    ``post_transform`` is applied to each successfully-transformed row (e.g. Field
    Encryption); failures there are quarantined the same as Transform failures.
    """
    total = 0
    invalid = 0
    for raw in rows:
        total += 1
        try:
            transformed = transform_row(raw, columns)
            if post_transform is not None:
                transformed = post_transform(transformed)
        except _ROW_ERRORS as e:
            invalid += 1
            sink.record(total, _offending_column(raw, columns), str(e), raw)
            continue
        yield transformed

    if quarantine.is_over_threshold(invalid, total):
        sink.discard()
        raise QuarantineThresholdExceeded(
            f"{invalid} of {total} rows invalid — over the quarantine threshold; "
            "failing the whole File (nothing committed)."
        )


def _offending_column(raw: Dict[str, Any], columns: List[ColumnMapping]) -> str:
    """Best-effort: the first declared column whose own Transform fails on this row.

    Returns "" when no single column is to blame (e.g. a Field Encryption error),
    which still records the full error string on the quarantined row.
    """
    for col in columns:
        try:
            transform_row(raw, [col])
        except TransformError:
            return col.source
    return ""

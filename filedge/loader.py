import os
from dataclasses import dataclass
from typing import Optional, Tuple

from filedge.cdc import CdcError
from filedge.config import CdcConfig, PipelineConfig
from filedge.connectors import Connector
from filedge.field_crypto import FieldCryptoEngine, FieldCryptoError
from filedge.filesystem import open_file
from filedge.key_resolver import KeyResolutionError, resolve_key
from filedge.parser import get_parser, parser_kwargs_for
from filedge.progress import ProgressReporter, emit_progress
from filedge.quarantine.processor import QuarantineThresholdExceeded, quarantining_rows
from filedge.quarantine.sink import QuarantineSink
from filedge.transform import TransformError, transform_row


@dataclass
class QuarantineOutcome:
    """How many rows a File quarantined and where (populated only when quarantine ran)."""

    count: int = 0
    path: Optional[str] = None


def load_file(
    connector: Connector,
    config: PipelineConfig,
    path: str,
    file_hash: str,
    fs=None,
    progress: ProgressReporter | None = None,
    row_report_interval: int = 1000,
    quarantine_outcome: Optional[QuarantineOutcome] = None,
) -> Tuple[int, Optional[str]]:
    parser = get_parser(config.format, **_parser_kwargs_from_config(config))
    rows_loaded = [0]
    cdc = _destination_cdc_config(config)
    try:
        field_crypto = FieldCryptoEngine.for_pipeline(config.columns, resolve_key)
    except KeyResolutionError as e:
        return rows_loaded[0], str(e)

    # Dead-Letter Quarantine (ADR-0019) applies to the standard write path. It is
    # opt-in (config.quarantine) and deliberately not combined with CDC here.
    use_quarantine = config.quarantine is not None and config.write_mode != "cdc"
    sink = (
        QuarantineSink(config.quarantine.dir, os.path.basename(path), file_hash)
        if use_quarantine else None
    )

    def _count_and_report():
        rows_loaded[0] += 1
        if rows_loaded[0] % row_report_interval == 0:
            emit_progress(progress, "loading", "rows", path=path, rows=rows_loaded[0])

    def row_iter():
        with open_file(path, fs=fs, mode=parser.mode, encoding=config.encoding) as f:
            for raw_row in parser.parse(f):
                transformed = transform_row(raw_row, config.columns)
                transformed = field_crypto.apply_to_row(transformed)
                if config.write_mode == "cdc" and cdc is not None:
                    transformed[cdc.operation_column] = raw_row.get(cdc.operation_column)
                _count_and_report()
                yield transformed

    def quarantining_iter():
        def _raw():
            with open_file(path, fs=fs, mode=parser.mode, encoding=config.encoding) as f:
                yield from parser.parse(f)

        good = quarantining_rows(
            _raw(), config.columns, config.quarantine, sink,
            post_transform=field_crypto.apply_to_row,
        )
        for transformed in good:
            _count_and_report()
            yield transformed

    try:
        iterator = quarantining_iter() if use_quarantine else row_iter()
        if config.write_mode == "cdc" and cdc is not None:
            connector.write_cdc_rows(config.dest_table, iterator, file_hash, cdc)
        else:
            connector.write_rows(config.dest_table, iterator, file_hash)
        if sink is not None and quarantine_outcome is not None:
            quarantine_outcome.count = sink.count
            quarantine_outcome.path = sink.finalize() if sink.count > 0 else None
        return rows_loaded[0], None
    except QuarantineThresholdExceeded as e:
        # The processor already discarded the sink; the whole File fails.
        return rows_loaded[0], str(e)
    except TransformError as e:
        return rows_loaded[0], str(e)
    except FieldCryptoError as e:
        return rows_loaded[0], str(e)
    except CdcError as e:
        return rows_loaded[0], str(e)
    except Exception as e:
        return rows_loaded[0], f"Unexpected error: {e}"


def _parser_kwargs_from_config(config: PipelineConfig) -> dict:
    return parser_kwargs_for(
        config.format,
        columns=config.columns,
        sheet=config.excel.sheet if config.excel is not None else None,
    )


def _destination_cdc_config(config: PipelineConfig) -> CdcConfig | None:
    if config.cdc is None:
        return None

    dest_by_source = {column.source: column.dest for column in config.columns}
    return CdcConfig(
        keys=[dest_by_source.get(key, key) for key in config.cdc.keys],
        operation_column=config.cdc.operation_column,
        sequence_by=dest_by_source.get(config.cdc.sequence_by, config.cdc.sequence_by),
        operations=config.cdc.operations,
    )

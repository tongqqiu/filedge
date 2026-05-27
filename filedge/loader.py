from typing import Optional, Tuple

from filedge.cdc import CdcError
from filedge.config import CdcConfig, PipelineConfig
from filedge.connectors import Connector
from filedge.field_crypto import FieldCryptoEngine, FieldCryptoError
from filedge.filesystem import open_file
from filedge.key_resolver import KeyResolutionError, resolve_key
from filedge.parser import get_parser
from filedge.progress import ProgressReporter, emit_progress
from filedge.transform import TransformError, transform_row


def load_file(
    connector: Connector,
    config: PipelineConfig,
    path: str,
    file_hash: str,
    fs=None,
    progress: ProgressReporter | None = None,
    row_report_interval: int = 1000,
) -> Tuple[int, Optional[str]]:
    parser = get_parser(config.format)
    rows_loaded = [0]
    cdc = _destination_cdc_config(config)
    try:
        field_crypto = FieldCryptoEngine.for_pipeline(config.columns, resolve_key)
    except KeyResolutionError as e:
        return rows_loaded[0], str(e)

    def row_iter():
        with open_file(path, fs=fs, encoding=config.encoding) as f:
            for raw_row in parser.parse(f):
                transformed = transform_row(raw_row, config.columns)
                transformed = field_crypto.apply_to_row(transformed)
                if config.write_mode == "cdc" and cdc is not None:
                    transformed[cdc.operation_column] = raw_row.get(cdc.operation_column)
                rows_loaded[0] += 1
                if rows_loaded[0] % row_report_interval == 0:
                    emit_progress(
                        progress,
                        "loading",
                        "rows",
                        path=path,
                        rows=rows_loaded[0],
                    )
                yield transformed

    try:
        if config.write_mode == "cdc" and cdc is not None:
            connector.write_cdc_rows(config.dest_table, row_iter(), file_hash, cdc)
        else:
            connector.write_rows(config.dest_table, row_iter(), file_hash)
        return rows_loaded[0], None
    except TransformError as e:
        return rows_loaded[0], str(e)
    except FieldCryptoError as e:
        return rows_loaded[0], str(e)
    except CdcError as e:
        return rows_loaded[0], str(e)
    except Exception as e:
        return rows_loaded[0], f"Unexpected error: {e}"


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

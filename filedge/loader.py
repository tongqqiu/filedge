from typing import Optional, Tuple

from filedge.config import PipelineConfig
from filedge.connectors import Connector
from filedge.filesystem import open_file
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

    def row_iter():
        with open_file(path, fs=fs, encoding=config.encoding) as f:
            for raw_row in parser.parse(f):
                transformed = transform_row(raw_row, config.columns)
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
        connector.write_rows(config.dest_table, row_iter(), file_hash)
        return rows_loaded[0], None
    except TransformError as e:
        return rows_loaded[0], str(e)
    except Exception as e:
        return rows_loaded[0], f"Unexpected error: {e}"

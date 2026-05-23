from typing import Optional, Tuple

from etl.config import PipelineConfig
from etl.connectors import Connector
from etl.filesystem import open_file
from etl.parser import get_parser
from etl.transform import TransformError, transform_row


def load_file(
    connector: Connector,
    config: PipelineConfig,
    file_path: str,
    file_hash: str,
    fs=None,
) -> Tuple[int, Optional[str]]:
    parser = get_parser(config.format)
    rows_loaded = [0]

    def row_iter():
        with open_file(file_path, fs=fs) as f:
            for raw_row in parser.parse(f):
                transformed = transform_row(raw_row, config.columns)
                rows_loaded[0] += 1
                yield transformed

    try:
        connector.write_rows(config.dest_table, row_iter(), file_hash)
        return rows_loaded[0], None
    except TransformError as e:
        return rows_loaded[0], str(e)
    except Exception as e:
        return rows_loaded[0], f"Unexpected error: {e}"

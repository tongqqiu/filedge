from typing import Optional, Tuple

from filedge.config import PipelineConfig
from filedge.connectors import Connector
from filedge.load_stream import LoadStream, LoadStreamError, iter_transformed_rows


def load_file(
    connector: Connector,
    config: PipelineConfig,
    path: str,
    file_hash: str,
    fs=None,
) -> Tuple[int, Optional[str]]:
    stream = LoadStream()

    try:
        rows = iter_transformed_rows(config, path, fs=fs, stream=stream)
        connector.write_rows(config.dest_table, rows, file_hash)
        return stream.rows_loaded, None
    except LoadStreamError as e:
        return stream.rows_loaded, str(e)
    except Exception as e:
        return stream.rows_loaded, f"Unexpected error: {e}"

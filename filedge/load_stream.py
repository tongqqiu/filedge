from dataclasses import dataclass
from typing import Iterator, Optional

from filedge.config import PipelineConfig
from filedge.filesystem import open_file
from filedge.parser import get_parser
from filedge.transform import TransformError, transform_row


@dataclass
class LoadStream:
    rows_loaded: int = 0


class LoadStreamError(Exception):
    def __init__(self, row_number: Optional[int], message: str):
        self.row_number = row_number
        self.message = message
        super().__init__(message)


def iter_transformed_rows(
    config: PipelineConfig,
    path: str,
    *,
    fs=None,
    stream: Optional[LoadStream] = None,
) -> Iterator[dict]:
    parser = get_parser(config.format)
    state = stream or LoadStream()

    with open_file(path, fs=fs, mode=parser.mode, encoding=config.encoding) as f:
        raw_rows = parser.parse(f)
        while True:
            try:
                raw_row = next(raw_rows)
            except StopIteration:
                return
            except Exception as e:
                row_number = state.rows_loaded + 1
                raise LoadStreamError(
                    row_number,
                    f"Row {row_number}: parse error: {e}",
                ) from e

            row_number = state.rows_loaded + 1
            try:
                transformed = transform_row(raw_row, config.columns)
            except TransformError as e:
                raise LoadStreamError(
                    row_number,
                    f"Row {row_number}: {e}",
                ) from e

            state.rows_loaded += 1
            yield transformed

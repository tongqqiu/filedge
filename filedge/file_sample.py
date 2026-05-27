import os
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import islice
from typing import Iterator, Optional

from filedge.filesystem import get_filesystem, open_file
from filedge.parser import get_parser


_EXT_TO_FORMAT = {
    ".csv": "csv",
    ".ndjson": "ndjson",
    ".jsonl": "ndjson",
    ".parquet": "parquet",
    ".xlsx": "excel",
}


SUPPORTED_FORMATS = ("csv", "ndjson", "parquet", "fixed_width", "excel")


@dataclass(frozen=True)
class FormatNotDetected:
    """No `--format` was passed and the file extension is not recognized."""

    file: str
    extension: str


def resolve_format(
    file: str, explicit: Optional[str] = None
) -> "str | FormatNotDetected":
    """Use the explicit format if given, otherwise detect from the file extension."""
    if explicit is not None:
        return explicit
    _, ext = os.path.splitext(file)
    fmt = _EXT_TO_FORMAT.get(ext.lower())
    if fmt is None:
        return FormatNotDetected(file=file, extension=ext.lower())
    return fmt


@contextmanager
def open_sample(
    file: str,
    fmt: str,
    *,
    encoding: str = "utf-8",
    start_row: int = 1,
    num_rows: Optional[int] = None,
    **parser_kwargs,
) -> Iterator[Iterator[dict]]:
    """Open a File and yield a row stream optionally sliced to a window.

    Owns filesystem resolution, parser selection, file open mode, encoding, and
    the row-window slice. `start_row` is 1-indexed inclusive; `num_rows=None`
    yields the full remaining stream. Per-format parser configuration (e.g.
    `columns=` for fixed_width) is forwarded to the parser factory.
    """
    fs, path = get_filesystem(file)
    parser = get_parser(fmt, **parser_kwargs)
    with open_file(path, fs=fs, mode=parser.mode, encoding=encoding) as f:
        rows = parser.parse(f)
        if start_row > 1:
            rows = islice(rows, start_row - 1, None)
        if num_rows is not None:
            rows = islice(rows, num_rows)
        yield rows


def read_excel_sheet_names(file: str) -> list[str]:
    """Return the sheet names of an .xlsx workbook without parsing rows.

    Used by `filedge inspect` to record the actual sheet name in the inferred
    YAML header even when `--sheet` was not passed (ADR-0012).
    """
    try:
        import openpyxl
    except ImportError:
        raise ImportError(
            "Excel support requires openpyxl — run: uv sync --extra excel"
        )
    fs, path = get_filesystem(file)
    with open_file(path, fs=fs, mode="rb") as f:
        wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
        return list(wb.sheetnames)


def read_parquet_schema(file: str):
    """Read the Arrow schema of a parquet File without parsing rows."""
    import pyarrow.parquet as pq

    fs, path = get_filesystem(file)
    with open_file(path, fs=fs, mode="rb") as f:
        return pq.ParquetFile(f).schema_arrow

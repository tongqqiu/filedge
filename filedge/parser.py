import csv
import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional

if TYPE_CHECKING:
    from filedge.config import ColumnMapping
    from filedge.excel import SheetSelector


class Parser(ABC):
    mode: str = "r"

    @abstractmethod
    def parse(self, fileobj) -> Iterator[Dict[str, Any]]:
        ...


class CSVParser(Parser):
    def parse(self, fileobj) -> Iterator[Dict[str, Any]]:
        reader = csv.DictReader(fileobj)
        for row in reader:
            yield dict(row)


class NDJSONParser(Parser):
    def parse(self, fileobj) -> Iterator[Dict[str, Any]]:
        for line in fileobj:
            line = line.strip()
            if line:
                yield json.loads(line)


class ParquetParser(Parser):
    mode = "rb"

    def parse(self, fileobj) -> Iterator[Dict[str, Any]]:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            raise ImportError(
                "Parquet support requires pyarrow — run: uv sync --extra parquet"
            )

        pf = pq.ParquetFile(fileobj)
        schema = pf.schema_arrow

        nested = {
            schema.field(i).name
            for i in range(len(schema))
            if pa.types.is_nested(schema.field(i).type)
        }

        for batch in pf.iter_batches():
            for row in batch.to_pylist():
                for col in nested:
                    if row.get(col) is not None:
                        row[col] = str(row[col])
                yield row


_PARSERS: Dict[str, Parser] = {
    "csv": CSVParser(),
    "ndjson": NDJSONParser(),
    "parquet": ParquetParser(),
}


def get_parser(format: str, **kwargs) -> Parser:
    """Return a Parser instance for the given format.

    Most formats are stateless and return a cached instance. Fixed-width and
    Excel are factories: fixed-width requires `columns=` (the layout, see
    ADR-0013); Excel accepts an optional `sheet=` selector (ADR-0012).
    """
    if format == "fixed_width":
        columns = kwargs.get("columns")
        if columns is None:
            raise ValueError(
                "fixed_width parser requires columns= (a sorted, validated layout)."
            )
        from filedge.fixed_width import FixedWidthParser
        return FixedWidthParser(layout=columns)
    if format == "excel":
        from filedge.excel import ExcelParser
        return ExcelParser(sheet=kwargs.get("sheet"))
    if format not in _PARSERS:
        supported = sorted(list(_PARSERS) + ["fixed_width", "excel"])
        raise ValueError(f"Unknown format: {format!r}. Supported: {supported}")
    return _PARSERS[format]


def parser_kwargs_for(
    fmt: str,
    *,
    columns: "Optional[List[ColumnMapping]]" = None,
    sheet: "SheetSelector" = None,
) -> Dict[str, Any]:
    """Build `get_parser` kwargs from already-resolved Pipeline Config inputs.

    The single point of truth for which format needs which parser argument,
    shared by the Loader (a Run) and the Authoring Session so that adding a
    format — or a new per-format knob — is a one-file change. `columns` supplies
    the Fixed-Width Layout (ADR-0013); `sheet` is a resolved Excel sheet selector
    (ADR-0012). Stateless formats need neither and get an empty mapping.
    """
    if fmt == "fixed_width":
        if columns is None:
            raise ValueError(
                "fixed_width requires a Pipeline Config for its layout."
            )
        from filedge.fixed_width import layout_from_columns
        return {"columns": layout_from_columns(columns)}
    if fmt == "excel":
        return {"sheet": sheet}
    return {}

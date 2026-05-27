import csv
import json
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterator


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

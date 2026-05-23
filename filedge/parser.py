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


def get_parser(format: str) -> Parser:
    if format not in _PARSERS:
        raise ValueError(f"Unknown format: {format!r}. Supported: {sorted(_PARSERS)}")
    return _PARSERS[format]

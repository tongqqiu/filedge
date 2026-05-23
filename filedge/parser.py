import csv
import json
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterator


class Parser(ABC):
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


_PARSERS = {
    "csv": CSVParser(),
    "ndjson": NDJSONParser(),
}


def get_parser(format: str) -> Parser:
    if format not in _PARSERS:
        raise ValueError(f"Unknown format: {format!r}. Supported: {sorted(_PARSERS)}")
    return _PARSERS[format]

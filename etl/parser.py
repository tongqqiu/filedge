import csv
import json
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterator


class Parser(ABC):
    @abstractmethod
    def parse(self, file_path: str) -> Iterator[Dict[str, Any]]:
        ...


class CSVParser(Parser):
    def parse(self, file_path: str) -> Iterator[Dict[str, Any]]:
        with open(file_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield dict(row)


class NDJSONParser(Parser):
    def parse(self, file_path: str) -> Iterator[Dict[str, Any]]:
        with open(file_path, encoding="utf-8") as f:
            for line in f:
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

"""The executable core of the Authoring Workflow (CONTEXT.md: Authoring Session).

`AuthoringSession` binds a sample File, its resolved format, and an optional
Pipeline Config, then exposes the three non-mutating Authoring operations —
Schema Inference, file preview, and Authoring Validation — behind one small
interface. It owns format-specific parser binding (Excel sheet selection,
Fixed-Width Layout) so the Operator CLI and the future Authoring UI invoke the
same operations without re-implementing dispatch. It runs no Run and mutates no
Audit Records (ADR-0015, ADR-0016).
"""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, List, Optional

from filedge.config import PipelineConfig
from filedge.excel import SheetSelector
from filedge.file_sample import open_sample, read_excel_sheet_names, read_parquet_schema
from filedge.inferrer import (
    InferredColumn,
    infer_schema,
    infer_schema_from_parquet,
)
from filedge.validator import ValidationResult, validate_file


@dataclass
class AuthoringSession:
    """A sample File bound to a format (and optionally a Pipeline Config).

    `encoding=None` falls back to the Pipeline Config's encoding, then UTF-8.
    `sheet` is an Excel sheet selector (name or 0-based index); when absent the
    Config's `excel.sheet` is used if present, otherwise the first sheet.
    """

    file: str
    fmt: str
    config: Optional[PipelineConfig] = None
    encoding: Optional[str] = None
    sheet: SheetSelector = None

    def infer_schema(self, *, sample_rows: int) -> List[InferredColumn]:
        """Run Schema Inference over a sample of the File."""
        if self.fmt == "parquet":
            return infer_schema_from_parquet(read_parquet_schema(self.file))
        with self._rows() as rows:
            return infer_schema(rows, sample_rows=sample_rows)

    def preview(
        self, *, start_row: int = 1, num_rows: Optional[int] = None
    ) -> List[dict]:
        """Materialize a window of parsed rows for display."""
        with self._rows(start_row=start_row, num_rows=num_rows) as rows:
            return list(rows)

    def validate(self, *, sample_rows: Optional[int] = None) -> ValidationResult:
        """Run Authoring Validation of the File against the Pipeline Config."""
        if self.config is None:
            raise ValueError("Authoring Validation requires a Pipeline Config.")
        with self._rows(num_rows=sample_rows) as rows:
            return validate_file(rows, self.config.columns)

    @property
    def sheet_name(self) -> Optional[str]:
        """Concrete Excel sheet that will be read, or None for non-Excel formats.

        Used by `filedge inspect` so the inferred YAML records the exact sheet,
        keeping the suggested config reproducible (ADR-0012).
        """
        if self.fmt != "excel":
            return None
        return _select_sheet_name(read_excel_sheet_names(self.file), self._sheet_selector)

    @property
    def _sheet_selector(self) -> SheetSelector:
        if self.sheet is not None:
            return self.sheet
        if self.config is not None and self.config.excel is not None:
            return self.config.excel.sheet
        return None

    @property
    def _encoding(self) -> str:
        if self.encoding is not None:
            return self.encoding
        if self.config is not None:
            return self.config.encoding
        return "utf-8"

    @contextmanager
    def _rows(
        self, *, start_row: int = 1, num_rows: Optional[int] = None
    ) -> Iterator[Iterator[dict]]:
        with open_sample(
            self.file,
            self.fmt,
            encoding=self._encoding,
            start_row=start_row,
            num_rows=num_rows,
            **self._parser_kwargs(),
        ) as rows:
            yield rows

    def _parser_kwargs(self) -> dict:
        if self.fmt == "fixed_width":
            if self.config is None:
                raise ValueError(
                    "fixed_width requires a Pipeline Config for its layout."
                )
            from filedge.fixed_width import layout_from_columns
            return {"columns": layout_from_columns(self.config.columns)}
        if self.fmt == "excel":
            return {"sheet": self._sheet_selector}
        return {}


def _select_sheet_name(names: List[str], selector: SheetSelector) -> str:
    """Resolve a sheet selector to a concrete sheet name in the workbook."""
    if selector is None:
        return names[0]
    if isinstance(selector, int):
        if selector < 0 or selector >= len(names):
            raise ValueError(
                f"Sheet index {selector} out of range; workbook has "
                f"{len(names)} sheet(s): {names!r}"
            )
        return names[selector]
    if selector not in names:
        raise ValueError(f"Missing sheet {selector!r}; workbook has {names!r}")
    return selector

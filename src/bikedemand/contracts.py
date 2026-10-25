"""Load a YAML data contract and validate a Polars DataFrame against it."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import polars as pl
import yaml

from bikedemand import CONTRACTS_DIR

_TYPE_CHECKS = {
    "datetime": lambda dt: isinstance(dt, pl.Datetime),
    "int": lambda dt: dt.is_integer(),
    "float": lambda dt: dt.is_float(),
    "str": lambda dt: dt == pl.String,
    "date": lambda dt: dt == pl.Date,
}


class ContractViolation(Exception):
    """Raised when data does not satisfy its contract."""


@dataclass
class Contract:
    id: str
    version: int
    columns: dict[str, dict]
    raw: dict = field(repr=False)

    @classmethod
    def load(cls, contract_id: str, contracts_dir: Path = CONTRACTS_DIR) -> Contract:
        raw = yaml.safe_load((contracts_dir / f"{contract_id}.yaml").read_text())
        return cls(id=raw["id"], version=raw["version"], columns=raw["columns"], raw=raw)

    def validate(self, df: pl.DataFrame) -> list[str]:
        """Return a list of violations (empty if the frame satisfies the contract)."""
        errors: list[str] = []

        missing = set(self.columns) - set(df.columns)
        if missing:
            errors.append(f"missing columns: {sorted(missing)}")

        key = self.raw.get("primary_key")
        if key and not set(key) & missing:
            dupes = df.height - df.select(key).unique().height
            if dupes:
                errors.append(f"primary key {key}: {dupes} duplicate rows")

        for name, spec in self.columns.items():
            if name not in df.columns:
                continue
            col = df[name]
            check = _TYPE_CHECKS[spec["type"]]
            if not check(col.dtype):
                errors.append(f"{name}: expected {spec['type']}, got {col.dtype}")
                continue
            if not spec.get("nullable", True) and col.null_count():
                errors.append(f"{name}: {col.null_count()} nulls in non-nullable column")
            if "min" in spec and (col.drop_nulls() < spec["min"]).any():
                errors.append(f"{name}: values below {spec['min']}")
            if "max" in spec and (col.drop_nulls() > spec["max"]).any():
                errors.append(f"{name}: values above {spec['max']}")

        return errors

    def row_check(self) -> pl.Expr:
        """Expression that is True for rows meeting the per-column null and range rules."""
        expr = pl.lit(True)
        for name, spec in self.columns.items():
            col = pl.col(name)
            if not spec.get("nullable", True):
                expr = expr & col.is_not_null()
            if "min" in spec:
                expr = expr & (col.is_null() | (col >= spec["min"]))
            if "max" in spec:
                expr = expr & (col.is_null() | (col <= spec["max"]))
        return expr

    def split(self, df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
        """Split rows into (valid, quarantined) so one bad row doesn't fail a whole file."""
        ok = df.with_columns(_ok=self.row_check())
        valid = ok.filter(pl.col("_ok")).drop("_ok")
        quarantined = ok.filter(~pl.col("_ok")).drop("_ok")
        return valid, quarantined

    def enforce(self, df: pl.DataFrame) -> pl.DataFrame:
        errors = self.validate(df)
        if errors:
            raise ContractViolation(f"{self.id} v{self.version}: " + "; ".join(errors))
        return df

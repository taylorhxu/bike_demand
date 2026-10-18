"""Oslo bike demand: ingestion, contracts and modelling for city-bike demand."""

from pathlib import Path

__version__ = "0.1.0.dev0"

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "contracts"
DATA_DIR = REPO_ROOT / "data"

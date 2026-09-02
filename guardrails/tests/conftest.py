from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from guardrails.validator import SqlValidator

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# Matches api.config.Settings.max_rows. The safe fixtures encode expectations
# against this number, so keep the two in step.
MAX_ROWS = 1000


def load_cases(name: str) -> list[dict]:
    with (FIXTURES / name).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or []


@pytest.fixture(scope="session")
def validator() -> SqlValidator:
    return SqlValidator(max_rows=MAX_ROWS)

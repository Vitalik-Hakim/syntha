from pathlib import Path

import pytest

from check_engine_io import check


def _violations(tmp_path: Path, source: str) -> list[str]:
    f = tmp_path / "mod.py"
    f.write_text(source)
    return [v.message for v in check([tmp_path])]


@pytest.mark.parametrize(
    "source",
    [
        "import requests",
        "import sqlalchemy.orm",
        "from sqlalchemy import select",
        "from urllib.request import urlopen",
        "import os",
        "from pathlib import Path",
        "import syntha_storage",
        "import importlib",
        "open('x')",
        "__import__('socket')",
        "import polars as pl\npl.read_csv('x.csv')",
        "import polars as pl\npl.scan_parquet('x')",
        "df.write_parquet('x')",
        "import numpy as np\nnp.load('x.npy')",
    ],
)
def test_flags_io(tmp_path: Path, source: str) -> None:
    assert _violations(tmp_path, source), f"expected a violation for: {source!r}"


@pytest.mark.parametrize(
    "source",
    [
        "import math",
        "import numpy as np\nnp.cumsum([1, 2])",
        "import polars as pl\npl.DataFrame({'a': [1]})",
        "from decimal import Decimal, ROUND_HALF_EVEN",
        "from syntha_contracts import Side",
        "from . import sibling",
        "x.reader()",
    ],
)
def test_allows_pure_code(tmp_path: Path, source: str) -> None:
    assert _violations(tmp_path, source) == []

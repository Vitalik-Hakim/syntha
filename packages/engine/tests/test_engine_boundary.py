from pathlib import Path

import syntha_engine
from check_engine_io import check

ENGINE_SRC = Path(syntha_engine.__file__).parent


def test_engine_source_has_no_io() -> None:
    violations = check([ENGINE_SRC])
    assert violations == [], "\n".join(str(v) for v in violations)

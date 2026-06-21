"""Test isolation: each test session gets its own DuckDB file.

This also shields tests from a developer .env that points DUCKDB_PATH at the
Docker volume path (/data), which does not exist on the host.
"""

import os
import tempfile

_tmpdir = tempfile.mkdtemp(prefix="stratfile-tests-")
os.environ["DUCKDB_PATH"] = os.path.join(_tmpdir, "stratfile.duckdb")

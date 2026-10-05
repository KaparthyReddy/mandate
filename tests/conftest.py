import os
from pathlib import Path

TEST_DB = Path("test_mandate.db")
if TEST_DB.exists():
    TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///./{TEST_DB}"

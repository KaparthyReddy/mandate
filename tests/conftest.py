import os
from pathlib import Path

from mandate import crypto

TEST_DB = Path("test_mandate.db")
if TEST_DB.exists():
    TEST_DB.unlink()

_signing_key = crypto.generate_private_key()

os.environ["DATABASE_URL"] = f"sqlite:///./{TEST_DB}"
os.environ["PAYMENT_PROVIDER"] = "fake"
os.environ["COUNCIL_ENABLED"] = "false"
os.environ["MANDATE_PRIVATE_KEY"] = crypto.private_key_to_b64(_signing_key)
os.environ["MANDATE_PUBLIC_KEY"] = crypto.public_key_to_b64(_signing_key.public_key())

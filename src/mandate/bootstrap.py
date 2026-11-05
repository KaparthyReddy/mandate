import json
import os
import secrets
from pathlib import Path

from mandate import crypto
from mandate.config import get_settings


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes")


def ensure_runtime_secrets(data_dir: Path | None = None) -> list[str]:
    if not _flag("MANDATE_AUTO_SECRETS", "false"):
        return []
    directory = data_dir or Path(os.environ.get("MANDATE_DATA_DIR", "/data"))
    path = directory / "secrets.json"
    stored: dict[str, str] = json.loads(path.read_text()) if path.exists() else {}
    notes: list[str] = []
    changed = False

    if not os.environ.get("MANDATE_PRIVATE_KEY"):
        if "private_key" not in stored:
            key = crypto.generate_private_key()
            stored["private_key"] = crypto.private_key_to_b64(key)
            stored["public_key"] = crypto.public_key_to_b64(key.public_key())
            notes.append("Generated a mandate signing key.")
            changed = True
        os.environ["MANDATE_PRIVATE_KEY"] = stored["private_key"]
        os.environ["MANDATE_PUBLIC_KEY"] = stored["public_key"]
    elif not os.environ.get("MANDATE_PUBLIC_KEY"):
        private = crypto.private_key_from_b64(os.environ["MANDATE_PRIVATE_KEY"])
        os.environ["MANDATE_PUBLIC_KEY"] = crypto.public_key_to_b64(private.public_key())

    if _flag("AUTH_ENABLED", "true") and not os.environ.get("MANDATE_ADMIN_KEY"):
        if "admin_key" not in stored:
            stored["admin_key"] = secrets.token_urlsafe(32)
            notes.append(f"Generated the admin key: {stored['admin_key']}")
            changed = True
        else:
            notes.append(f"Using the admin key stored in {path}")
        os.environ["MANDATE_ADMIN_KEY"] = stored["admin_key"]

    if changed:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stored))
        path.chmod(0o600)
    get_settings.cache_clear()
    return notes

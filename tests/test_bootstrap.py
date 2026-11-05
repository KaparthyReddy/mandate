import json
import os
from pathlib import Path

import pytest

from mandate import crypto
from mandate.bootstrap import ensure_runtime_secrets

NAMES = [
    "MANDATE_AUTO_SECRETS",
    "MANDATE_PRIVATE_KEY",
    "MANDATE_PUBLIC_KEY",
    "MANDATE_ADMIN_KEY",
    "AUTH_ENABLED",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)


def test_nothing_happens_unless_auto_secrets_is_on(tmp_path: Path) -> None:
    assert ensure_runtime_secrets(tmp_path) == []
    assert not (tmp_path / "secrets.json").exists()
    assert "MANDATE_ADMIN_KEY" not in os.environ


def test_missing_secrets_are_generated_once_and_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MANDATE_AUTO_SECRETS", "true")
    notes = ensure_runtime_secrets(tmp_path)
    assert any("signing key" in note for note in notes)
    assert any("admin key" in note for note in notes)
    first = dict(json.loads((tmp_path / "secrets.json").read_text()))
    assert (tmp_path / "secrets.json").stat().st_mode & 0o777 == 0o600
    signing = crypto.private_key_from_b64(os.environ["MANDATE_PRIVATE_KEY"])
    assert crypto.public_key_to_b64(signing.public_key()) == os.environ["MANDATE_PUBLIC_KEY"]
    for name in NAMES[1:4]:
        monkeypatch.delenv(name)
    again = ensure_runtime_secrets(tmp_path)
    assert not any("Generated" in note for note in again)
    assert json.loads((tmp_path / "secrets.json").read_text()) == first
    assert os.environ["MANDATE_ADMIN_KEY"] == first["admin_key"]
    assert os.environ["MANDATE_PRIVATE_KEY"] == first["private_key"]


def test_secrets_the_operator_provided_are_never_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = crypto.generate_private_key()
    monkeypatch.setenv("MANDATE_AUTO_SECRETS", "true")
    monkeypatch.setenv("MANDATE_PRIVATE_KEY", crypto.private_key_to_b64(key))
    monkeypatch.setenv("MANDATE_ADMIN_KEY", "mine")
    assert ensure_runtime_secrets(tmp_path) == []
    assert os.environ["MANDATE_ADMIN_KEY"] == "mine"
    assert os.environ["MANDATE_PUBLIC_KEY"] == crypto.public_key_to_b64(key.public_key())
    assert not (tmp_path / "secrets.json").exists()


def test_no_admin_key_is_generated_when_auth_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MANDATE_AUTO_SECRETS", "true")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    ensure_runtime_secrets(tmp_path)
    assert "MANDATE_ADMIN_KEY" not in os.environ

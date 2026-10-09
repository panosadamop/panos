import os

import pytest

from panos_core.config import PanosSettings
from panos_core.config.runtime import (
    generate_token,
    read_runtime_file,
    remove_runtime_file,
    write_runtime_file,
)
from panos_core.platform.secure_file import create_owner_only_file, is_owner_only


def test_tokens_are_long_and_unique() -> None:
    tokens = {generate_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(t) >= 43 for t in tokens)  # 32 random bytes, base64url


@pytest.mark.req("NFR-SEC-001")
def test_runtime_file_round_trip_and_owner_only(settings: PanosSettings) -> None:
    info = write_runtime_file(
        settings.runtime_file, host="127.0.0.1", port=5555, token="t" * 43, version="0.1.0"
    )
    assert info.base_url == "http://127.0.0.1:5555"
    assert read_runtime_file(settings.runtime_file) == info
    assert is_owner_only(settings.runtime_file)


def test_runtime_file_replaces_previous_file(settings: PanosSettings) -> None:
    write_runtime_file(settings.runtime_file, host="127.0.0.1", port=1, token="a", version="v")
    write_runtime_file(settings.runtime_file, host="127.0.0.1", port=2, token="b", version="v")
    info = read_runtime_file(settings.runtime_file)
    assert info is not None and info.port == 2
    assert is_owner_only(settings.runtime_file)


def test_missing_or_corrupt_runtime_file_reads_as_none(settings: PanosSettings) -> None:
    assert read_runtime_file(settings.runtime_file) is None
    settings.runtime_file.write_text("{not json", encoding="utf-8")
    assert read_runtime_file(settings.runtime_file) is None


def test_remove_only_own_runtime_file(settings: PanosSettings) -> None:
    write_runtime_file(settings.runtime_file, host="127.0.0.1", port=1, token="a", version="v")
    remove_runtime_file(settings.runtime_file, expected_pid=os.getpid() + 1)
    assert settings.runtime_file.exists()
    remove_runtime_file(settings.runtime_file, expected_pid=os.getpid())
    assert not settings.runtime_file.exists()


def test_create_owner_only_file_refuses_existing_file(settings: PanosSettings) -> None:
    target = settings.runtime_dir / "x"
    target.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError):
        create_owner_only_file(target)


@pytest.mark.windows
def test_plain_file_is_not_reported_owner_only_on_windows(settings: PanosSettings) -> None:
    # A normal file inherits ACEs for SYSTEM/Administrators, so it must not pass the check.
    target = settings.runtime_dir / "plain.txt"
    target.write_text("x", encoding="utf-8")
    assert not is_owner_only(target)


@pytest.mark.posix
def test_world_readable_file_is_not_owner_only_on_posix(settings: PanosSettings) -> None:
    target = settings.runtime_dir / "plain.txt"
    target.write_text("x", encoding="utf-8")
    target.chmod(0o644)
    assert not is_owner_only(target)

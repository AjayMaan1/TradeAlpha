import os
import subprocess
import sys
import textwrap

import pytest

from tradealpha.platform.config.secrets import SecretBox, get_or_create_secret_key, mask


def test_secret_key_generated_and_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "dev_key_change_this")       # placeholder must be ignored
    k1 = get_or_create_secret_key(str(tmp_path))
    k2 = get_or_create_secret_key(str(tmp_path))
    assert k1 == k2 and k1 != "dev_key_change_this" and len(k1) >= 48
    if os.name == "posix":
        assert oct(os.stat(tmp_path / "secret_key").st_mode & 0o777) == "0o600"
    monkeypatch.setenv("SECRET_KEY", "a-real-operator-provided-key")
    assert get_or_create_secret_key(str(tmp_path)) == "a-real-operator-provided-key"


def test_secretbox_roundtrip_and_file_permissions(tmp_path):
    box = SecretBox(str(tmp_path), backend="file")
    token = box.encrypt("df746c00-my-api-key")
    assert "my-api-key" not in token
    assert SecretBox(str(tmp_path), backend="file").decrypt(token) == "df746c00-my-api-key"
    if os.name == "posix":
        assert oct(os.stat(tmp_path / "fernet.key").st_mode & 0o777) == "0o600"
    with pytest.raises(ValueError):
        SecretBox(str(tmp_path / "other"), backend="file").decrypt(token)


def test_mask():
    assert mask("abcdefgh1234") == "\u2022" * 8 + "1234"
    assert mask("") == ""


@pytest.mark.slow
def test_instance_lock_is_exclusive_across_processes(tmp_path):
    """SC-01: a second process cannot become the background-services owner."""
    # The owner holds the lock until the test tells it to exit (stdin closes) - no wall-clock sleep, so a slow
    # interpreter start-up of the second process under load can never outlive the owner (was intermittent).
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {os.getcwd()!r})
        from tradealpha.platform.runtime.singleton import acquire_instance_lock
        print(acquire_instance_lock({str(tmp_path)!r}), flush=True)
        if sys.argv[1] == "hold":
            sys.stdin.read()
    """)
    first = subprocess.Popen([sys.executable, "-c", code, "hold"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
    try:
        assert first.stdout.readline().strip() == "True"
        second = subprocess.run([sys.executable, "-c", code, "probe"], capture_output=True, text=True, timeout=60)
        assert second.stdout.strip() == "False", second.stderr
    finally:
        first.stdin.close()                                          # owner exits -> lock released
        first.wait(timeout=60)
        first.stdout.close()
    third = subprocess.run([sys.executable, "-c", code, "probe"], capture_output=True, text=True, timeout=60)
    assert third.stdout.strip() == "True"                          # released when the owner exits


def test_instance_lock_in_process(tmp_path):
    from tradealpha.platform.runtime.singleton import acquire_instance_lock, release_instance_lock
    assert acquire_instance_lock(str(tmp_path), "unit") is True
    assert acquire_instance_lock(str(tmp_path), "unit") is True          # re-entrant for the owner
    from filelock import FileLock, Timeout
    with pytest.raises(Timeout):
        FileLock(str(tmp_path / "unit.lock"), is_singleton=False).acquire(timeout=0)
    release_instance_lock(str(tmp_path), "unit")
    FileLock(str(tmp_path / "unit.lock"), is_singleton=False).acquire(timeout=0)   # free again

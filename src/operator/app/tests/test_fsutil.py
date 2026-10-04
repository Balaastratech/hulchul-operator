"""a transient PermissionError on os.replace must not crash the worker."""

import os

import pytest

from src.operator.app.fsutil import replace_with_retry, write_json_atomic


def test_retries_a_transient_permission_error_then_succeeds():
    calls, delays = [], []

    def flaky(source, destination):
        calls.append((source, destination))
        if len(calls) < 4:
            raise PermissionError(5, "Access is denied")

    replace_with_retry("a", "b", replace=flaky, sleep=delays.append)
    assert len(calls) == 4
    assert delays == [0.05, 0.05, 0.05]


def test_gives_up_after_forty_tries_and_raises_the_original_error():
    calls = []

    def locked(source, destination):
        calls.append(1)
        raise PermissionError(5, "Access is denied")

    with pytest.raises(PermissionError):
        replace_with_retry("a", "b", replace=locked, sleep=lambda _: None)
    assert len(calls) == 40


def test_other_errors_are_not_retried():
    calls = []

    def missing(source, destination):
        calls.append(1)
        raise FileNotFoundError("gone")

    with pytest.raises(FileNotFoundError):
        replace_with_retry("a", "b", replace=missing, sleep=lambda _: None)
    assert len(calls) == 1


def test_real_replace_overwrites_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")
    write_json_atomic(target, '{"terminal": false}')
    assert target.read_text(encoding="utf-8") == '{"terminal": false}'
    assert not (tmp_path / "state.json.tmp").exists()


def test_run_real_uses_the_helper_for_shared_state():
    source = open(os.path.join(os.path.dirname(__file__), "../../../../scripts/run_real.py"), encoding="utf-8").read()
    assert "os.replace(" not in source
    assert "replace_with_retry(temp, directory / \"state.json\")" in source

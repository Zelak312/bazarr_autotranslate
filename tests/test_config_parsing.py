import pytest

import main


def test_parse_bool_accepts_words_and_numbers(monkeypatch):
    monkeypatch.setenv("TEST_BOOL", "TRUE")
    assert main.parse_bool("TEST_BOOL", False) is True
    monkeypatch.setenv("TEST_BOOL", "0")
    assert main.parse_bool("TEST_BOOL", True) is False


def test_parse_bool_unset_returns_default(monkeypatch):
    monkeypatch.delenv("TEST_BOOL", raising=False)
    assert main.parse_bool("TEST_BOOL", True) is True
    assert main.parse_bool("TEST_BOOL", False) is False


def test_parse_bool_invalid_value_exits(monkeypatch):
    monkeypatch.setenv("TEST_BOOL", "maybe")
    with pytest.raises(SystemExit):
        main.parse_bool("TEST_BOOL", False)


def test_parse_int_reads_environment(monkeypatch):
    monkeypatch.setenv("TEST_INT", "42")
    assert main.parse_int("TEST_INT", 0) == 42


def test_parse_int_unset_returns_default(monkeypatch):
    monkeypatch.delenv("TEST_INT", raising=False)
    assert main.parse_int("TEST_INT", 99) == 99


def test_parse_int_invalid_value_exits(monkeypatch):
    monkeypatch.setenv("TEST_INT", "not-a-number")
    with pytest.raises(SystemExit):
        main.parse_int("TEST_INT", 0)


def test_task_key_distinguishes_every_component():
    base = main.task_key(True, 1, "nl", False, False)
    assert base != main.task_key(False, 1, "nl", False, False)  # episode vs movie
    assert base != main.task_key(True, 2, "nl", False, False)  # different video
    assert base != main.task_key(True, 1, "de", False, False)  # different language
    assert base != main.task_key(True, 1, "nl", True, False)  # forced variant
    assert base != main.task_key(True, 1, "nl", False, True)  # hearing-impaired variant


def test_scan_budget_remaining_and_consume():
    budget = main.ScanBudget(2)
    assert budget.remaining() == 2
    budget.consume(1)
    assert budget.remaining() == 1
    budget.consume(5)
    assert budget.remaining() == 0

    disabled = main.ScanBudget(0)
    assert disabled.remaining() is None


def test_normalize_base_url_strips_trailing_slashes():
    assert main.normalize_base_url("http://bazarr:6767") == "http://bazarr:6767"
    assert main.normalize_base_url("http://bazarr:6767/") == "http://bazarr:6767"
    assert main.normalize_base_url("http://bazarr:6767///") == "http://bazarr:6767"

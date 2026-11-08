import time

import main


def test_rate_limit_wait_enforces_global_spacing(monkeypatch):
    monkeypatch.setattr(main, "translation_delay", 10)
    waits = []

    class FakeEvent:
        def wait(self, seconds):
            waits.append(seconds)
            return False

    monkeypatch.setattr(main, "shutdown_thread_event", FakeEvent())
    # The last request was 4 seconds ago, so about 6 seconds of the delay remain
    monkeypatch.setattr(main, "last_request_time", time.monotonic() - 4)

    assert main.rate_limit_wait() is False
    assert len(waits) == 1
    assert waits[0] > 0

    # An immediate second request must wait again: the first call reserved
    # the slot, so requests from all workers are spaced globally
    assert main.rate_limit_wait() is False
    assert len(waits) == 2
    assert waits[1] > 0


def test_rate_limit_disabled_returns_immediately(monkeypatch):
    monkeypatch.setattr(main, "translation_delay", 0)
    assert main.rate_limit_wait() is False


def test_rate_limit_wait_aborts_on_shutdown(monkeypatch):
    monkeypatch.setattr(main, "translation_delay", 10)

    class ShutdownEvent:
        def wait(self, seconds):
            return True

    monkeypatch.setattr(main, "shutdown_thread_event", ShutdownEvent())
    monkeypatch.setattr(main, "last_request_time", time.monotonic())
    assert main.rate_limit_wait() is True

import threading
import time

import httpx

import main
from class_types import Subtitle, SubtitleTranslate


def _task(video_id=10):
    return SubtitleTranslate(
        base_subtitle=Subtitle(name="English", code2="en", code3="eng",
                               path="/media/movie.en.srt", forced=False, hi=False, file_size=1024),
        to_language="nl",
        to_forced=False,
        to_hi=False,
        video_id=video_id,
        is_serie=True,
    )


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


def test_translation_worker_success_clears_failures_and_records_completion(monkeypatch):
    monkeypatch.setattr(main, "translation_delay", 0)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    monkeypatch.setattr(main, "build_worker_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler)))

    task = _task()
    key = main.task_key(task.is_serie, task.video_id, task.to_language, task.to_forced, task.to_hi)
    with main.failure_lock:
        main.failure_counts[key] = 2

    main.task_queue.put(task)
    main.task_queue.put(None)

    worker = threading.Thread(target=main.translation_worker, args=(0, "http://bazarr", "api-key"))
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()

    assert len(requests) == 1
    assert requests[0].method == "PATCH"
    assert requests[0].url.path == "/api/subtitles"
    with main.failure_lock:
        assert key not in main.failure_counts
    with main.completion_lock:
        completed_at = main.recent_completions.pop(key, None)
    assert completed_at is not None


def test_translation_worker_counts_failures(monkeypatch):
    monkeypatch.setattr(main, "translation_delay", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    monkeypatch.setattr(main, "build_worker_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler)))

    task = _task()
    key = main.task_key(task.is_serie, task.video_id, task.to_language, task.to_forced, task.to_hi)

    main.task_queue.put(task)
    main.task_queue.put(None)

    worker = threading.Thread(target=main.translation_worker, args=(0, "http://bazarr", "api-key"))
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()

    with main.failure_lock:
        assert main.failure_counts.get(key) == 1
        main.failure_counts.pop(key, None)


def test_prune_recent_completions_removes_only_expired_entries(monkeypatch):
    monkeypatch.setattr(main, "requeue_cooldown", 300)
    now = time.monotonic()
    main.recent_completions["expired"] = now - 301
    main.recent_completions["fresh"] = now
    try:
        main.prune_recent_completions()
        assert "expired" not in main.recent_completions
        assert "fresh" in main.recent_completions
    finally:
        main.recent_completions.pop("fresh", None)
        main.recent_completions.pop("expired", None)


def test_prune_recent_completions_noop_when_cooldown_disabled(monkeypatch):
    monkeypatch.setattr(main, "requeue_cooldown", 0)
    main.recent_completions["kept"] = time.monotonic()
    try:
        main.prune_recent_completions()
        assert "kept" in main.recent_completions
    finally:
        main.recent_completions.pop("kept", None)

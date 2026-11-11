import time

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


def test_dry_run_does_not_queue(monkeypatch):
    monkeypatch.setattr(main, "dry_run", True)
    monkeypatch.setattr(main, "requeue_cooldown", 0)
    queued = main.queue_subtitles_for_translation([_task()])
    assert queued == 1
    assert len(main.task_queue.q) == 0


def test_queueing_enqueues_the_task(monkeypatch):
    monkeypatch.setattr(main, "dry_run", False)
    monkeypatch.setattr(main, "requeue_cooldown", 0)
    task = _task()
    key = main.task_key(task.is_serie, task.video_id, task.to_language, task.to_forced, task.to_hi)
    try:
        queued = main.queue_subtitles_for_translation([task])
        assert queued == 1
        assert main.task_queue.q[0] is task
        assert main.task_queue.check_key(key) is True
    finally:
        main.task_queue.done(task)
        # done() only releases the deduplication key so the task could be
        # queued again; removing the item from the queue is get()'s job,
        # performed by the worker. Remove the item here as well so that
        # later tests start from an empty queue.
        main.task_queue.q.popleft()
    assert main.task_queue.check_key(key) is False


def test_recently_completed_translation_is_not_requeued(monkeypatch):
    monkeypatch.setattr(main, "requeue_cooldown", 300)
    task = _task()
    key = main.task_key(task.is_serie, task.video_id, task.to_language, task.to_forced, task.to_hi)
    with main.completion_lock:
        main.recent_completions[key] = time.monotonic()
    try:
        before = len(main.task_queue.q)
        queued = main.queue_subtitles_for_translation([task])
        assert queued == 0
        assert len(main.task_queue.q) == before
    finally:
        with main.completion_lock:
            main.recent_completions.pop(key, None)


def test_exceeded_retries_skip_the_task(monkeypatch):
    monkeypatch.setattr(main, "requeue_cooldown", 0)
    task = _task()
    key = main.task_key(task.is_serie, task.video_id, task.to_language, task.to_forced, task.to_hi)
    with main.failure_lock:
        main.failure_counts[key] = 3
    try:
        before = len(main.task_queue.q)
        queued = main.queue_subtitles_for_translation([task])
        assert queued == 0
        assert len(main.task_queue.q) == before
    finally:
        with main.failure_lock:
            main.failure_counts.pop(key, None)

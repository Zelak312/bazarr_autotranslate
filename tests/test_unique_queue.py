from unique_queue import UniqueQueue


def test_put_deduplicates_until_done():
    queue = UniqueQueue(key_fn=lambda item: item)
    queue.put("a")
    queue.put("a")
    assert len(queue.q) == 1

    queue.done("a")
    queue.put("a")
    assert len(queue.q) == 2


def test_none_bypasses_deduplication():
    # None is the shutdown sentinel and must always be enqueued
    queue = UniqueQueue(key_fn=lambda item: str(item))
    queue.put(None)
    queue.put(None)
    assert len(queue.q) == 2


def test_done_is_idempotent_for_unknown_keys():
    queue = UniqueQueue(key_fn=lambda item: item)
    # Must not raise: an unknown key must never kill a worker
    queue.done("never-seen")


def test_check_key_reports_queued_and_in_progress_items():
    queue = UniqueQueue(key_fn=lambda item: item)
    assert queue.check_key("a") is False
    queue.put("a")
    assert queue.check_key("a") is True
    queue.done("a")
    assert queue.check_key("a") is False

import argparse
import os
import sys
import time
import httpx
import signal
import asyncio
import logging
import threading
from dataclasses import asdict
from typing import Callable, List, Optional
from dotenv import load_dotenv
from unique_queue import UniqueQueue
from class_types import Serie, Movie, SubtitleTranslate, MissingSubtitle
from logging.handlers import TimedRotatingFileHandler


def get_env_or_default(env, default):
    val = os.getenv(env)
    return val if val is not None else default


def parse_bool(env_name: str, default: bool) -> bool:
    """
    Parse a boolean environment variable.

    bool("false") evaluates to True, so plain bool() must never be used on
    environment variables: any non-empty string is truthy, which silently
    made SERIES_SCAN=false and MOVIES_SCAN=false impossible to set.
    """
    value = os.getenv(env_name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in ("true", "1", "yes", "on"):
        return True
    if normalized in ("false", "0", "no", "off"):
        return False
    print(f"Invalid boolean value for {env_name}: {value!r}, expected true or false")
    sys.exit(1)


def parse_int(env_name: str, default: int) -> int:
    """Parse an integer environment variable with a clear error message."""
    val = os.getenv(env_name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        print(f"Invalid integer value for {env_name}: {val!r}")
        sys.exit(1)


def normalize_base_url(url: str) -> str:
    """
    Strip trailing slashes from the base URL: a value like
    "http://host:6767/" would otherwise produce "//api/..." in every
    endpoint URL, which Bazarr rejects.
    """
    return url.rstrip("/")


def task_key(is_serie: bool, video_id: int, to_language: str, to_forced: bool, to_hi: bool) -> str:
    """
    Unique key for one video-to-language translation request.

    The forced and hearing-impaired flags are part of the key: a single
    video can be missing the regular, the forced and the hearing-impaired
    variant of the same language at the same time, and each of those is a
    separate translation request.
    """
    flags = f"{'f' if to_forced else '-'}{'h' if to_hi else '-'}"
    return f"{'s' if is_serie else 'm'}_{video_id}_{to_language}_{flags}"


def key_fn(task: SubtitleTranslate) -> str:
    """Queue key for a translation task."""
    return task_key(task.is_serie, task.video_id, task.to_language, task.to_forced, task.to_hi)


class ScanBudget:
    """
    Per-scan-cycle budget for MAX_TRANSLATIONS_PER_SCAN.

    The budget is shared between the series and the movies scan of one
    cycle, so the limit caps the whole cycle instead of each scan
    separately. A limit of 0 disables the cap.
    """

    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0

    def remaining(self) -> Optional[int]:
        """Remaining capacity, or None when the cap is disabled."""
        if self.limit <= 0:
            return None
        return max(self.limit - self.used, 0)

    def consume(self, count: int) -> None:
        self.used += count


# Get configuration and setup things
load_dotenv()

# Bazarr returns languages as lowercase code2, so normalize the input
# to make "EN" match "en" instead of silently never matching anything.
base_languages = [lang.strip().lower() for lang in os.getenv("BASE_LANGUAGES", "").split(",") if lang.strip()]
to_languages = [lang.strip().lower() for lang in os.getenv("TO_LANGUAGES", "").split(",") if lang.strip()]

translation_request_timeout = parse_int("TRANSLATION_REQUEST_TIMEOUT", 15 * 60)
num_workers = parse_int("NUM_WORKERS", 1)
interval_between_scans = parse_int("INTERVAL_BETWEEN_SCANS", 5 * 60)
# Minimum age (in seconds) a source subtitle file must have before it is
# queued for translation. Default: 24 hours. Gives Bazarr time to find and
# download a proper subtitle from providers first.
min_subtitle_age = parse_int("MIN_SUBTITLE_AGE_SECONDS", 24 * 60 * 60)
# Safety limits. A value of 0 disables the limit.
# MAX_RETRIES: how often a single failing translation may be retried before
#   it is skipped for the lifetime of the process.
# MAX_TRANSLATIONS_PER_SCAN: maximum number of translation requests queued
#   per scan, to protect a paid translation service from large bursts.
max_retries = parse_int("MAX_RETRIES", 3)
max_translations_per_scan = parse_int("MAX_TRANSLATIONS_PER_SCAN", 0)
# Minimum number of seconds to wait between two translation requests sent
# by this container. 0 disables the delay. This is a simple rate limiter
# that protects a (paid) translation service from bursts when a scan
# discovers a large backlog in one go.
translation_delay = parse_int("TRANSLATION_DELAY_SECONDS", 0)
# Suppress re-queueing a translation for this many seconds after it
# completed, so a scan cannot queue a duplicate while Bazarr is still
# re-indexing the newly written subtitle. 0 disables the cooldown.
requeue_cooldown = parse_int("REQUEUE_COOLDOWN_SECONDS", 300)
# When true, the script only logs which translations it would request and
# never sends anything to Bazarr. Useful to validate a new configuration
# (language lists, path mapping, age gate) before real translations start.
dry_run = parse_bool("DRY_RUN", False)

log_level = get_env_or_default("LOG_LEVEL", "INFO")
log_directory = get_env_or_default("LOG_DIRECTORY", "logs/")
# How many rotated log files to keep.
log_backup_count = parse_int("LOG_BACKUP_COUNT", 4)
series_scan = parse_bool("SERIES_SCAN", True)
movies_scan = parse_bool("MOVIES_SCAN", True)

task_queue = UniqueQueue(key_fn=key_fn)
shutdown_event = asyncio.Event()
# Set alongside shutdown_event when a termination signal arrives. Workers
# wait on this event instead of time.sleep() when spacing translation
# requests by TRANSLATION_DELAY_SECONDS, so setting it ends an in-progress
# delay immediately instead of holding up the shutdown.
shutdown_thread_event = threading.Event()
# Failed translation attempts per task key, used to enforce MAX_RETRIES
failure_counts: dict[str, int] = {}
failure_lock = threading.Lock()
# Translation task keys with the time they completed, used with
# REQUEUE_COOLDOWN_SECONDS to suppress re-queueing duplicates. Keyed by
# time.monotonic() so the entries stay valid across wall-clock changes.
recent_completions: dict[str, float] = {}
completion_lock = threading.Lock()
# Start time of the most recent translation request sent to Bazarr, used
# to enforce TRANSLATION_DELAY_SECONDS globally across all worker threads.
last_request_time = 0.0
rate_limit_lock = threading.Lock()
logger = logging.getLogger("bazarr_lingarr")


def is_subtitle_old_enough(path: str, age_cache: dict[str, bool]) -> bool:
    """
    Return True only if the subtitle file at `path` is at least
    `min_subtitle_age` seconds old, based on its modification time.
    The modification time corresponds to the moment Bazarr wrote the
    file to disk, so this effectively delays translation until Bazarr
    has had `min_subtitle_age` seconds to satisfy the language through
    its providers.

    If the file cannot be inspected (not mounted, permission issue),
    the subtitle is skipped and a warning is logged, so that a path
    mapping problem never silently disables the age gate.

    Results are cached in `age_cache` so a source file that is checked
    for several missing languages is only inspected (and warned about)
    once per scan.
    """
    cached = age_cache.get(path)
    if cached is not None:
        return cached

    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        logger.warning(f"Skipping translation, unable to determine age of subtitle file: {path}. "
                       f"Verify that the media path is mounted in this container at the same path Bazarr reports.")
        age_cache[path] = False
        return False

    old_enough = age >= min_subtitle_age
    if not old_enough:
        logger.debug(f"Skipping translation, subtitle is only {int(age // 3600)} hours old "
                     f"(minimum age: {int(min_subtitle_age // 3600)} hours): {path}")

    age_cache[path] = old_enough
    return old_enough


async def api_get_parsed(
    client: httpx.AsyncClient,
    endpoint: str,
    api_key: str,
    parse: Callable,
    params: Optional[dict] = None,
    error_context: str = "",
):
    """
    GET an API endpoint and parse its "data" payload with the given parse
    function. Returns None on any error, so callers can distinguish a
    failed request (None) from a genuinely empty result ([]).
    """
    headers = {"X-API-KEY": api_key}
    try:
        response = await client.get(endpoint, headers=headers, params=params)
        response.raise_for_status()
        payload = response.json()["data"]

        logger.debug(f"received: {payload}")
        return parse(payload)
    except Exception:
        logger.exception(f"Error while {error_context}")
        return None


async def get_episodes_metadata(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    series_ids: Optional[List[int]] = None,
    episode_ids: Optional[List[int]] = None,
) -> Optional[List[Serie]]:
    """
    Get metadata for episodes/series

    Args:
        client (httpx.AsyncClient): Shared HTTP client, reused for all requests
        base_url (str): Base URL of Bazarr API
        api_key (str): API key for authentication
        series_ids (list[int], optional): List of series IDs to get metadata for
        episode_ids (list[int], optional): List of episode IDs to get metadata for
    """
    logger.debug(f"Getting metadata for series: {series_ids}, episodes: {episode_ids}")
    params = {}
    if series_ids:
        params["seriesid[]"] = series_ids
    if episode_ids:
        params["episodeid[]"] = episode_ids
    return await api_get_parsed(
        client,
        f"{base_url}/api/episodes",
        api_key,
        parse=lambda data: [Serie.from_dict(obj) for obj in data],
        params=params,
        error_context=f"getting episode metadata for series: {series_ids}, episodes: {episode_ids}",
    )


async def get_wanted_episodes(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    start: int = 0,
    length: int = -1,
) -> Optional[List[Serie]]:
    """
    Get wanted subtitles for episodes

    Args:
        client (httpx.AsyncClient): Shared HTTP client, reused for all requests
        base_url (str): Base URL of Bazarr API
        api_key (str): API key for authentication
        start (int): Paging start integer (default: 0)
        length (int): Paging length (default: -1)
    """
    logger.debug("Getting wanted episodes")
    return await api_get_parsed(
        client,
        f"{base_url}/api/episodes/wanted",
        api_key,
        parse=lambda data: [Serie.from_dict(obj) for obj in data],
        params={"start": start, "length": length},
        error_context="getting wanted episodes",
    )


async def get_movies_metadata(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    movie_ids: Optional[List[int]] = None,
) -> Optional[List[Movie]]:
    """
    Get metadata for movies

    Args:
        client (httpx.AsyncClient): Shared HTTP client, reused for all requests
        base_url (str): Base URL of Bazarr API
        api_key (str): API key for authentication
        movie_ids (list[int], optional): List of movie IDs to get metadata for
    """
    logger.debug(f"Getting metadata for movies: {movie_ids}")
    params = {}
    if movie_ids:
        params["radarrid[]"] = movie_ids
    return await api_get_parsed(
        client,
        f"{base_url}/api/movies",
        api_key,
        parse=lambda data: [Movie.from_dict(obj) for obj in data],
        params=params,
        error_context=f"getting movie metadata for movies: {movie_ids}",
    )


async def get_wanted_movies(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    start: int = 0,
    length: int = -1,
) -> Optional[List[Movie]]:
    """
    Get wanted subtitles for movies

    Args:
        client (httpx.AsyncClient): Shared HTTP client, reused for all requests
        base_url (str): Base URL of Bazarr API
        api_key (str): API key for authentication
        start (int): Paging start integer (default: 0)
        length (int): Paging length (default: -1)
    """
    logger.debug("Getting wanted movies")
    return await api_get_parsed(
        client,
        f"{base_url}/api/movies/wanted",
        api_key,
        parse=lambda data: [Movie.from_dict(obj) for obj in data],
        params={"start": start, "length": length},
        error_context="getting wanted movies",
    )


async def verify_bazarr_connection(client: httpx.AsyncClient, base_url: str, api_key: str) -> None:
    """
    Verify Bazarr is reachable and the API key is valid, exiting with a
    clear message otherwise. Failing fast here turns a wrong URL or a
    wrong key into an immediate, actionable error instead of repeated
    scan failures; a 401 response means the key is wrong.
    """
    try:
        response = await client.get(f"{base_url}/api/system/status", headers={"X-API-KEY": api_key})
        response.raise_for_status()
    except Exception as e:
        print(f"Could not connect to Bazarr at {base_url}, or the API key is invalid: {e}")
        sys.exit(1)
    logger.info("Successfully connected to Bazarr")


async def find_base_language_subtitles_from_missing_subtitles(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    videos: List[Serie] | List[Movie],
    budget: Optional[ScanBudget] = None,
) -> Optional[List[SubtitleTranslate]]:
    # Making a video id to missing-subtitles map, useful later on.
    # The full MissingSubtitle entry is kept instead of only its code2:
    # the same language can be missing more than once (as regular, forced
    # and hearing-impaired variants), and each variant needs its own
    # translation request with its own flags.
    # Skip counters for the per-scan INFO summary, so the reason nothing
    # was queued stays visible without DEBUG logging
    skipped_already_queued = 0
    skipped_age_gate = 0
    skipped_no_source = 0

    video_id_missing_map: dict[int, List[MissingSubtitle]] = {}
    for video in videos:
        # Get video id from the correct property depending on the video instance
        video_id = video.sonarr_episode_id if isinstance(video, Serie) else video.radarr_id
        for missing_sub in video.missing_subtitles:
            # Check if the missing subtitle is in the list of languages to translate to
            if missing_sub.code2 in to_languages:
                # Check if that subtitle is already in the translation queue
                if task_queue.check_key(task_key(isinstance(video, Serie), video_id,
                                                 missing_sub.code2, missing_sub.forced, missing_sub.hi)):
                    skipped_already_queued += 1
                    logger.debug(f"Skipping subtitle, already in translation queue, {asdict(missing_sub)}")
                    continue

                video_id_missing_map.setdefault(video_id, []).append(missing_sub)

    if len(video_id_missing_map) == 0:
        logger.info("No missing subtitles found that are in the list of languages to be translated")
        return None

    if isinstance(videos[0], Serie):
        metadata = await get_episodes_metadata(
            client, base_url, api_key, episode_ids=list(video_id_missing_map.keys())
        )
    else:
        metadata = await get_movies_metadata(client, base_url, api_key, movie_ids=list(video_id_missing_map.keys()))

    if metadata is None:
        logger.error("Failed to retrieve metadata, couldn't find already existing subtitles")
        return None

    if len(metadata) == 0:
        logger.warning("No metadata was returned for the videos with missing subtitles")
        return None

    video_id_to_video_map: dict[int, Serie | Movie] = {}
    for video in metadata:
        video_id = video.sonarr_episode_id if isinstance(video, Serie) else video.radarr_id
        video_id_to_video_map[video_id] = video

    # Age-gate results are cached per scan so each source file is only
    # inspected (and warned about) once.
    age_cache: dict[str, bool] = {}

    # Check the metadata for already existing subtitles
    # Match the existing subtitles from the base language list to the missing ones for translation
    subtitles_to_translate: List[SubtitleTranslate] = []
    for video_id, missing_subs in video_id_missing_map.items():
        # Get the video associated
        video = video_id_to_video_map.get(video_id)
        if video is None:
            logger.warning(f"Skipping video: {video_id}, no metadata was returned for it")
            continue

        # Check if there are subtitles
        if video.subtitles is None:
            logger.debug(f"Skipping video: {video_id}, no currently existing subtitles found")
            continue

        for missing in missing_subs:
            # Usable sources: external files (with a path) in a base language
            # that satisfy the minimum age. Only external files qualify, and
            # a subtitle that already has the target language is never used
            # as a source (guards against overlapping base/target lists).
            source_too_fresh = False
            candidates = []
            for sub in video.subtitles:
                if sub.code2 not in base_languages or sub.code2 == missing.code2 or not sub.path:
                    continue
                if not is_subtitle_old_enough(sub.path, age_cache):
                    source_too_fresh = True
                    continue
                candidates.append(sub)

            if not candidates:
                if source_too_fresh:
                    skipped_age_gate += 1
                else:
                    skipped_no_source += 1
                logger.debug(f"No matching existing subtitle found for: {missing.code2} "
                             f"(forced: {missing.forced}, hi: {missing.hi}) for video: {video_id}")
                continue

            # Prefer a source whose forced and hearing-impaired flags match
            # the missing entry: a forced translation is best produced from
            # a forced source and a hearing-impaired one from a
            # hearing-impaired source. Fall back to the first candidate if
            # no matching variant exists, which mirrors what Bazarr's manual
            # translate dialog does.
            matching = [sub for sub in candidates
                        if sub.forced == missing.forced and sub.hi == missing.hi]
            source = matching[0] if matching else candidates[0]

            # The flags of the MISSING entry are carried along: Bazarr's
            # translate API expects the flags of the target translation,
            # not of the source file.
            subtitles_to_translate.append(SubtitleTranslate(source, missing.code2, missing.forced,
                                                            missing.hi, video_id, isinstance(video, Serie)))

    if len(subtitles_to_translate) == 0:
        logger.info("No already existing subtitles matched with requested translation subs "
                    f"(skipped: {skipped_already_queued} already in the queue, "
                    f"{skipped_age_gate} with sources below the minimum age, "
                    f"{skipped_no_source} without a usable source)")
        return None

    # The cap applies per scan cycle, to protect a (paid) translation
    # service from large bursts queued in one go.
    remaining = budget.remaining() if budget is not None else None
    if remaining is not None and len(subtitles_to_translate) > remaining:
        logger.warning(
            f"Found {len(subtitles_to_translate)} matching subtitles, "
            f"capping this scan cycle at MAX_TRANSLATIONS_PER_SCAN={max_translations_per_scan} "
            f"({remaining} remaining)"
        )
        subtitles_to_translate = subtitles_to_translate[:remaining]

    if len(subtitles_to_translate) == 0:
        # Only reachable when the per-cycle budget capped the list to zero:
        # all matches are deferred to the next cycle. None (not []) tells
        # the caller that this scan has nothing left to queue.
        logger.info("All matching subtitles deferred to the next scan cycle: the per-cycle budget is exhausted")
        return None

    logger.debug(f"Matching subtitles: {[asdict(w) for w in subtitles_to_translate]}")
    logger.info(f"Found {len(subtitles_to_translate)} matching subtitles to translate "
                f"(skipped: {skipped_already_queued} already in the queue, "
                f"{skipped_age_gate} with sources below the minimum age, "
                f"{skipped_no_source} without a usable source)")
    return subtitles_to_translate


def queue_subtitles_for_translation(subtitles: List[SubtitleTranslate]) -> int:
    queued = 0
    skipped_retries = 0
    skipped_cooldown = 0
    for sub in subtitles:
        key = task_key(sub.is_serie, sub.video_id, sub.to_language, sub.to_forced, sub.to_hi)
        # Skip items that have exceeded the retry limit, so a permanently
        # failing subtitle cannot hammer a (paid) translation service forever.
        # The counter is read under the lock because worker threads update it.
        with failure_lock:
            failures = failure_counts.get(key, 0)
        if max_retries > 0 and failures >= max_retries:
            logger.warning(
                f"Skipping translation of {sub.base_subtitle.path} to {sub.to_language}: "
                f"it already failed {failures} times (MAX_RETRIES={max_retries}). "
                f"Restart the process to retry it again."
            )
            skipped_retries += 1
            continue
        # Suppress re-queueing a translation for a short time after it
        # completed: Bazarr has not re-indexed the newly written subtitle
        # yet, so it would still appear missing and be queued twice.
        if requeue_cooldown > 0:
            with completion_lock:
                last_completed = recent_completions.get(key)
            if last_completed is not None and time.monotonic() - last_completed < requeue_cooldown:
                logger.debug(f"Skipping {sub.base_subtitle.path} to {sub.to_language}, "
                             f"it was translated less than {requeue_cooldown} seconds ago and Bazarr may not "
                             f"have re-indexed the new subtitle yet")
                skipped_cooldown += 1
                continue
        variant = f"{'.forced' if sub.to_forced else ''}{'.hi' if sub.to_hi else ''}"
        if dry_run:
            # DRY_RUN: log the request without queueing it, so a new setup
            # can be validated before real translations are triggered
            logger.info(f"[DRY RUN] Would translate {sub.base_subtitle.path} to: {sub.to_language}{variant}")
            queued += 1
            continue
        task_queue.put(sub)
        queued += 1
        logger.info(f"Queued: {sub.base_subtitle.path} to be translated to: {sub.to_language}{variant}")
    if skipped_retries:
        logger.info(f"Skipped {skipped_retries} translations that exceeded the retry limit")
    if skipped_cooldown:
        logger.info(f"Skipped {skipped_cooldown} translations that completed within the re-queue cooldown")
    return queued


def rate_limit_wait() -> bool:
    """
    Enforce TRANSLATION_DELAY_SECONDS globally: before a worker sends a
    translation request, wait until the configured delay has passed since
    the previous request of ANY worker, so the requests of multiple
    workers are spaced as well. Returns True when a shutdown was
    requested during the wait, in which case the caller must not send.
    Waiting on the shutdown thread event keeps a shutdown signal
    effective during the delay.
    """
    global last_request_time
    if translation_delay <= 0:
        return False
    with rate_limit_lock:
        wait_for = translation_delay - (time.monotonic() - last_request_time)
        if wait_for > 0:
            if shutdown_thread_event.wait(wait_for):
                return True
        last_request_time = time.monotonic()
    return False


def build_worker_client() -> httpx.Client:
    """
    HTTP client used by a translation worker. A module-level factory so
    tests can substitute a client with a mock transport.
    """
    return httpx.Client(timeout=translation_request_timeout)


def translation_worker(worker_id, base_url, api_key):
    endpoint = f"{base_url}/api/subtitles"
    headers = {"X-API-KEY": api_key}
    with build_worker_client() as client:
        while True:
            sub: SubtitleTranslate | None = task_queue.get()
            if sub is None:
                # Shutdown sentinel put by main() during a graceful shutdown
                logger.info(f"[Worker: {worker_id}] Shutting down")
                return
            key = task_key(sub.is_serie, sub.video_id, sub.to_language, sub.to_forced, sub.to_hi)
            logger.info(f"[Worker: {worker_id}] Translating: {sub.base_subtitle.path} to: {sub.to_language}")
            if rate_limit_wait():
                # A shutdown was requested during the rate-limit wait:
                # release the task without sending it, so a future run
                # queues it again
                task_queue.done(sub)
                continue
            try:
                params = {
                    "action": "translate",
                    "language": sub.to_language,
                    # Bazarr parses booleans by comparing the raw query string
                    # to "True"/"False" (capitalized). httpx serializes Python
                    # booleans as lowercase "true"/"false", which Bazarr would
                    # always read as False, so explicit strings are required.
                    "forced": "True" if sub.to_forced else "False",
                    "hi": "True" if sub.to_hi else "False",
                    "path": sub.base_subtitle.path,
                    "type": "episode" if sub.is_serie else "movie",
                    "id": sub.video_id,
                    "original_format": "True",
                }

                response = client.patch(endpoint, headers=headers, params=params)
                response.raise_for_status()

                logger.info(f"[Worker: {worker_id}] Translation finished")
                # A success clears the failure counter so a future re-translation
                # is not blocked by an old failure
                with failure_lock:
                    failure_counts.pop(key, None)
                # Record the completion so the scan does not queue a
                # duplicate while Bazarr is still re-indexing the new subtitle
                with completion_lock:
                    recent_completions[key] = time.monotonic()
            except Exception:
                logger.exception(f"[Worker: {worker_id}] Error while translating: {sub.base_subtitle.path} to: {sub.to_language}")
                with failure_lock:
                    failure_counts[key] = failure_counts.get(key, 0) + 1
            finally:
                task_queue.done(sub)


async def _scan_wanted(client, base_url, api_key, label, wanted_fetch, budget: Optional[ScanBudget] = None):
    """
    Shared scan logic for episodes and movies.

    Args:
        label (str): "episodes" or "movies", used in the log messages
        wanted_fetch: coroutine that lists the wanted videos
        budget (ScanBudget, optional): per-cycle translation cap, shared
            with the other scan
    """
    logger.info(f"Scanning for {label}")
    videos = await wanted_fetch
    if videos is None:
        logger.error(f"Failed to retrieve wanted {label}, skipping this scan")
        return
    if len(videos) == 0:
        logger.info(f"Found no missing subtitles for {label}")
        return

    logger.info(f"Found {len(videos)} missing subtitles for {label}")
    subtitles_to_translate = await find_base_language_subtitles_from_missing_subtitles(
        client, base_url, api_key, videos, budget
    )
    if subtitles_to_translate is None:
        return

    queued = queue_subtitles_for_translation(subtitles_to_translate)
    if budget is not None:
        budget.consume(queued)


async def scan_and_process_series(client, base_url, api_key, budget: Optional[ScanBudget] = None):
    """Scan wanted episodes and queue translations for them."""
    await _scan_wanted(client, base_url, api_key, "episodes",
                       get_wanted_episodes(client, base_url, api_key), budget)


async def scan_and_process_movies(client, base_url, api_key, budget: Optional[ScanBudget] = None):
    """Scan wanted movies and queue translations for them."""
    await _scan_wanted(client, base_url, api_key, "movies",
                       get_wanted_movies(client, base_url, api_key), budget)


def prune_recent_completions() -> None:
    """Remove expired entries from recent_completions so it cannot grow unbounded."""
    if requeue_cooldown <= 0:
        return
    cutoff = time.monotonic() - requeue_cooldown
    with completion_lock:
        expired = [key for key, completed_at in recent_completions.items() if completed_at <= cutoff]
        for key in expired:
            del recent_completions[key]


async def main(base_url, api_key, run_once: bool = False):
    worker_threads = []
    for i in range(num_workers):
        t = threading.Thread(target=translation_worker, args=(i, base_url, api_key), daemon=True)
        t.start()
        worker_threads.append(t)

    # One shared client for all API calls instead of a new client per request
    async with httpx.AsyncClient(timeout=30) as client:
        # Fail fast: verify Bazarr is reachable and the API key is valid
        # before entering the scan loop, so a wrong URL or key is reported
        # immediately instead of as repeated scan failures
        await verify_bazarr_connection(client, base_url, api_key)

        while not shutdown_event.is_set():
            prune_recent_completions()
            # A fresh budget per cycle, shared by the series and the movies
            # scan so MAX_TRANSLATIONS_PER_SCAN caps the whole cycle
            budget = ScanBudget(max_translations_per_scan)
            try:
                if series_scan:
                    await scan_and_process_series(client, base_url, api_key, budget)
                if movies_scan:
                    await scan_and_process_movies(client, base_url, api_key, budget)
            except Exception:
                logger.exception("Uncaught exception during scan")

            if run_once:
                # --once: perform a single cycle and shut down gracefully
                # instead of sleeping until the next scan interval
                break

            try:
                # Interruptible sleep: a shutdown signal is honored immediately
                # instead of only after the full scan interval
                await asyncio.wait_for(shutdown_event.wait(), timeout=interval_between_scans)
                break
            except asyncio.TimeoutError:
                pass

    # Wake all idle workers so they exit cleanly instead of being killed
    # mid-translation when the process exits
    shutdown_thread_event.set()
    for _ in range(num_workers):
        task_queue.put(None)
    for t in worker_threads:
        t.join(timeout=30)
    logger.info("Shutdown complete")


def handle_shutdown():
    logger.info("Received exit signal, shutting down after the current scan")
    shutdown_event.set()
    # Wake any worker that is currently waiting out TRANSLATION_DELAY_SECONDS
    shutdown_thread_event.set()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Automate subtitle translations by scanning Bazarr's wanted list "
                    "and queueing the translations through its API"
    )
    parser.add_argument("--once", action="store_true",
                        help="run a single scan cycle and exit instead of scanning in a loop")
    args = parser.parse_args()

    # Do verification on arguments
    base_url = os.getenv("BAZARR_BASE_URL")
    api_key = os.getenv("BAZARR_API_KEY")

    if base_url is None:
        print("BAZARR_BASE_URL is missing")
        sys.exit(1)

    if api_key is None:
        print("BAZARR_API_KEY is missing")
        sys.exit(1)

    # Normalize early: a trailing slash would otherwise produce "//api/..."
    # in every endpoint URL built from this value
    base_url = normalize_base_url(base_url)

    if len(base_languages) == 0:
        print("Missing BASE_LANGUAGES")
        sys.exit(1)

    wrong_languages = [lang for lang in base_languages if len(lang) != 2]
    if len(wrong_languages) > 0:
        print(f"Wrong languages given in BASE_LANGUAGES, wrong ones: {wrong_languages}, expected to be 2 characters long (code2)")
        sys.exit(1)

    if len(to_languages) == 0:
        print("Missing TO_LANGUAGES")
        sys.exit(1)

    wrong_languages = [lang for lang in to_languages if len(lang) != 2]
    if len(wrong_languages) > 0:
        print(f"Wrong languages given in TO_LANGUAGES, wrong ones: {wrong_languages}, expected to be 2 characters long (code2)")
        sys.exit(1)

    if not series_scan and not movies_scan:
        print("Both series and movies scan are disabled, nothing will be done")
        sys.exit(1)

    if num_workers < 1:
        print("NUM_WORKERS must be at least 1")
        sys.exit(1)

    if interval_between_scans < 1:
        print("INTERVAL_BETWEEN_SCANS must be at least 1 second")
        sys.exit(1)

    if min_subtitle_age < 0:
        print("MIN_SUBTITLE_AGE_SECONDS must be 0 or greater")
        sys.exit(1)

    if max_retries < 0:
        print("MAX_RETRIES must be 0 or greater (0 disables the limit)")
        sys.exit(1)

    if max_translations_per_scan < 0:
        print("MAX_TRANSLATIONS_PER_SCAN must be 0 or greater (0 disables the cap)")
        sys.exit(1)

    if translation_delay < 0:
        print("TRANSLATION_DELAY_SECONDS must be 0 or greater")
        sys.exit(1)

    if translation_request_timeout < 1:
        print("TRANSLATION_REQUEST_TIMEOUT must be at least 1 second")
        sys.exit(1)

    if requeue_cooldown < 0:
        print("REQUEUE_COOLDOWN_SECONDS must be 0 or greater (0 disables the cooldown)")
        sys.exit(1)

    # Setup logger
    logger.propagate = False
    trailing_slash = "/" if not log_directory.endswith("/") else ""
    os.makedirs(log_directory, exist_ok=True)
    handler = TimedRotatingFileHandler(
        f"{log_directory}{trailing_slash}bazarr_lingarr_autotranslate.log", when="midnight", interval=1,
        backupCount=log_backup_count
    )
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    # Also log to stdout so `docker logs` shows the activity
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    match log_level.lower():
        case "debug":
            logger.setLevel(logging.DEBUG)
            logger.debug("Configuration: --------------------")
            logger.debug(f"bazarr_base_url: {base_url}")
            logger.debug(f"base_languages: {base_languages}")
            logger.debug(f"to_languages: {to_languages}")
            logger.debug(f"translation_request_timeout: {translation_request_timeout}")
            logger.debug(f"num_workers: {num_workers}")
            logger.debug(f"interval_between_scans: {interval_between_scans}")
            logger.debug(f"min_subtitle_age: {min_subtitle_age}")
            logger.debug(f"max_retries: {max_retries}")
            logger.debug(f"max_translations_per_scan: {max_translations_per_scan}")
            logger.debug(f"translation_delay: {translation_delay}")
            logger.debug(f"requeue_cooldown: {requeue_cooldown}")
            logger.debug(f"dry_run: {dry_run}")
            logger.debug(f"log_level: {log_level}")
            logger.debug(f"log_directory: {log_directory}")
            logger.debug(f"log_backup_count: {log_backup_count}")
            logger.debug(f"series_scan: {series_scan}")
            logger.debug(f"movies_scan: {movies_scan}")
            logger.debug("End Configuration: ----------------")
        case "info":
            logger.setLevel(logging.INFO)
        case "warning" | "warn":
            logger.setLevel(logging.WARNING)
        case "error":
            logger.setLevel(logging.ERROR)
        case _:
            # Fail fast on a typo in LOG_LEVEL: silently falling back to
            # INFO would hide a requested DEBUG (or ERROR) configuration
            print(f"Unknown LOG_LEVEL: {log_level!r}, expected DEBUG, INFO, WARNING or ERROR")
            sys.exit(1)

    # Start running things
    loop = asyncio.new_event_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_shutdown)

        loop.run_until_complete(main(base_url, api_key, run_once=args.once))
    finally:
        loop.close()

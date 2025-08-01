import os
import sys
import httpx
import queue
import signal
import asyncio
import logging
import threading
from dotenv import load_dotenv
from typing import List, Optional
from unique_queue import UniqueQueue
from logging.handlers import TimedRotatingFileHandler
from class_types import Serie, Movie, SubtitleTranslate

def get_env_or_default(env, default):
    val = os.getenv(env)
    return val if val is not None else default

def get_attr_or_key(obj, name):
    if hasattr(obj, name):
        return getattr(obj, name)
    elif isinstance(obj, dict) and name in obj:
        return obj[name]
    else:
        raise AttributeError(f"Missing attribute or key '{name}'")

# Get configuration and setup things
load_dotenv()
base_languages_env = os.getenv("BASE_LANGUAGES")
if base_languages_env is not None:
    base_languages = [lang.strip() for lang in base_languages_env.split(",")]
else:
    base_languages = []

to_languges_env = os.getenv("TO_LANGUAGES")
if to_languges_env is not None:
    to_languges = [lang.strip() for lang in to_languges_env.split(",")]
else:
    to_languges = []

translation_request_timeout = int(get_env_or_default("TRANSLATION_REQUEST_TIMEOUT", 15 * 60))
num_workers = int(get_env_or_default("NUM_WORKERS", 1))
interval_between_scans = int(get_env_or_default("INTERVAL_BETWEEN_SCANS", 5 * 60))
log_level = get_env_or_default("LOG_LEVEL", "INFO")
log_directory = get_env_or_default("LOG_DIRECTORY", "logs/")
series_scan = bool(get_env_or_default("SERIES_SCAN", True))
movies_scan = bool(get_env_or_default("MOVIES_SCAN", True))

key_fn = lambda x: f" {"s" if get_attr_or_key(x, "is_serie") else "m"} {get_attr_or_key(x, "video_id")}_{get_attr_or_key(x, "to_language")}"
task_queue = UniqueQueue(key_fn=key_fn)
shutdown_event = asyncio.Event()
logger = logging.getLogger("bazarr_lingarr")

async def get_episodes_metadata(
    base_url: str,
    api_key: str,
    series_ids: Optional[List[int]] = None,
    episode_ids: Optional[List[int]] = None,
) -> List[Serie] | None:
    """
    Get metadata for episodes/series

    Args:
        base_url (str): Base URL of Bazarr API
        api_key (str): API key for authentication
        series_ids (list[int], optional): List of series IDs to get metadata for
        episode_ids (list[int], optional): List of episode IDs to get metadata for
    """

    logger.debug(f"Getting metadata for series: {series_ids}, episodes: {episode_ids}")
    endpoint = f"{base_url}/api/episodes"
    headers = {"X-API-KEY": api_key}

    params = {}
    if series_ids:
        params["seriesid[]"] = series_ids
    if episode_ids:
        params["episodeid[]"] = episode_ids

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(endpoint, headers=headers, params=params)
            response.raise_for_status()
            json = response.json()["data"]
            
            logger.debug(f"received: {json}")
            return [Serie.from_dict(obj) for obj in json]
    except Exception as e:
        logger.error(f"Error while getting metada: {e}")

async def get_wanted_episodes(
    base_url: str,
    api_key: str,
    start: int = 0,
    length: int = -1,
) -> List[Serie] | None:
    """
    Get wanted subtitles for episodes

    Args:
        base_url (str): Base URL of Bazarr API
        api_key (str): API key for authentication
        start (int): Paging start integer (default: 0)
        length (int): Paging length integer (default: -1)
        episode_ids (list[int], optional): List of specific episode IDs to check
    """

    logger.debug(f"Getting wanted episodes")
    endpoint = f"{base_url}/api/episodes/wanted"
    headers = {"X-API-KEY": api_key}
    params = {"start": start, "length": length}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(endpoint, headers=headers, params=params)
            response.raise_for_status()
            json = response.json()["data"]
            
            logger.debug(f"received: {json}")
            return [Serie.from_dict(obj) for obj in json]
    except Exception as e:
        logger.error(f"Error while getting wanted episodes: {e}")

async def get_movies_metadata(
    base_url: str,
    api_key: str,
    movie_ids: Optional[List[int]] = None,
) -> List[Movie] | None:
    """
    Get metadata for movies

    Args:
        base_url (str): Base URL of Bazarr API
        api_key (str): API key for authentication
        movie_ids (list[int], optional): List of movie IDs to get metadata for
    """

    logger.debug(f"Getting metada for moveis: {movie_ids}")
    endpoint = f"{base_url}/api/movies"
    headers = {"X-API-KEY": api_key}
    params = {}

    if movie_ids:
        params["radarrid[]"] = movie_ids

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(endpoint, headers=headers, params=params)
            response.raise_for_status()
            json = response.json()["data"]
            
            logger.debug(f"received: {json}")
            return [Movie.from_dict(obj) for obj in json]
    except Exception as e:
        logger.error(f"Error while getting movies metada: {e}")

async def get_wanted_movies(
    base_url: str,
    api_key: str,
    start: int = 0,
    length: int = -1,
) -> List[Movie] | None:
    """
    Get wanted subtitles for movies

    Args:
        base_url (str): Base URL of Bazarr API
        api_key (str): API key for authentication
        start (int): Paging start integer (default: 0)
        length (int): Paging length integer (default: -1)
    """

    logger.debug(f"Getting wanted movies")
    endpoint = f"{base_url}/api/movies/wanted"
    headers = {"X-API-KEY": api_key}
    params = {"start": start, "length": length}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(endpoint, headers=headers, params=params)
            response.raise_for_status()
            json = response.json()["data"]
            
            logger.debug(f"received: {json}")
            return [Movie.from_dict(obj) for obj in json]
    except Exception as e:
        logger.error(f"Error while getting metada for movies: {e}")

async def find_base_language_subtitles_from_missing_sutitles(base_url, api_key, videos: List[Serie] | List[Movie]) -> List[SubtitleTranslate] | None:
    # Making a video id to language map, useful later on
    video_id_language_map = {}
    for video in videos:
        # Get video id from correct property depending the video instance
        video_id = video.sonarr_episode_id if isinstance(video, Serie) else video.radarr_id
        for missing_sub in video.missing_subtitles:
            # Check if the missing subtitle is in the list for language to be translated in
            if missing_sub.code2 in to_languges:
                # Check if that subtitle is already in the translation list
                if task_queue.check({"is_serie": isinstance(video, Serie), "video_id": video_id, "to_language": missing_sub.code2}):
                    logger.debug(f"Skipping subtitle, already in translation queue, {missing_sub.to_dict()}")
                    continue

                video_id_language_map[video_id] = missing_sub.code2

    if len(video_id_language_map) == 0:
        logger.info("No missing subtitles found that is in list of languages to be translated")
        return

    metadata: List[Serie] | List[Movie] | None = None
    if isinstance(videos[0], Serie):
        metadata = await get_episodes_metadata(
            base_url, api_key, episode_ids=list(video_id_language_map.keys())
        )
    else:
        metadata = await get_movies_metadata(base_url, api_key, movie_ids=list(video_id_language_map.keys()))

    if metadata is None:
        logger.info("No metadata returned, couldn't find already existing subtitles")
        return
    
    video_id_to_video_map: dict[int, Serie | Movie] = {}
    for video in metadata:
        video_id = video.sonarr_episode_id if isinstance(video, Serie) else video.radarr_id
        video_id_to_video_map[video_id] = video
    
    # Check the metadata for already existing subtitles
    # Match the existing subtitles from base language list to the missing ones for translation
    subtitles_to_translate = []
    for video_id, language in video_id_language_map.items():
        # Get the video associated
        video = video_id_to_video_map[video_id]

        # Check if there is subtitles
        if video.subtitles is None:
            logger.debug(f"skipping video: {video_id} no current existing subtitles found")
            continue

        found = False
        for sub in video.subtitles:
            if language == sub.code2:
                continue # Skip metadata for subtitle if it's in the same language to for the translation
                # I don't think this should happen but better safe than sorry

            # If the subtitle is in the base language list, associate it with the language to translate in
            if sub.code2 in base_languages:
                subtitles_to_translate.append(SubtitleTranslate(sub, language, video_id, isinstance(video, Serie)))
                found = True
                break

        if not found:
            logger.debug(f"No matching existing subtitle found for: {language} for video: {video_id}")
    
    if len(subtitles_to_translate) == 0:
        logger.info("No already existing subtitles matched with requested translation subs")
        return

    logger.debug(f"Matching subtitles: {[w.to_dict() for w in subtitles_to_translate]}")
    logger.info(f"Found {len(subtitles_to_translate)} matching subtitles to translate")
    return subtitles_to_translate

def queue_subtitles_for_translation(subtitles: List[SubtitleTranslate]):
    for sub in subtitles:
        task_queue.put(sub)
        logger.info(f"Queued: {sub.base_subtitle.path} to be translated to in: {sub.to_language}")

def translation_worker(worker_id, base_url, api_key):
    endpoint = f"{base_url}/api/subtitles"
    headers = {"X-API-KEY": api_key}
    with httpx.Client(timeout=translation_request_timeout) as client:
        while True:
            sub: SubtitleTranslate | None = None
            try:
                sub = task_queue.get()
                if sub is None:
                    continue
                logger.info(f"[Worker: {worker_id}] Translating: {sub.base_subtitle.path} to: {sub.to_language}")

                params = {
                    "action": "translate",
                    "language": sub.to_language,
                    "path": sub.base_subtitle.path,
                    "type": "episode" if sub.is_serie else "movie",
                    "id": sub.video_id,
                    "forced": sub.base_subtitle.forced,
                    "hi": sub.base_subtitle.hi,
                    "original_format": True,
                }

                response = client.patch(endpoint, headers=headers, params=params)
                response.raise_for_status()

                logger.info(f"[Worker: {worker_id}] Translation finished")
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"[Worker: {worker_id}] Error while translating: {e}")
            
            task_queue.done(sub)

async def scan_and_process_series(base_url, api_key):
    logger.info("Scanning for episodes")
    series = await get_wanted_episodes(base_url, api_key)
    if series is None or len(series) == 0:
        logger.info("Found no missing subtitles for episodes")
        return
    
    logger.info(f"Found {len(series)} missing subtitles for episodes")
    subtitles_to_translate = await find_base_language_subtitles_from_missing_sutitles(base_url, api_key, series)
    if subtitles_to_translate is None:
        return
    
    queue_subtitles_for_translation(subtitles_to_translate)

async def scan_and_process_movies(base_url, api_key):
    logger.info("Scanning for movies")
    movies = await get_wanted_movies(base_url, api_key)
    if movies is None or len(movies) == 0:
        logger.info("Found no missing subtitles for movies")
        return
    
    logger.info(f"Found {len(movies)} missing subtitles for movies")
    subtitles_to_translate = await find_base_language_subtitles_from_missing_sutitles(base_url, api_key, movies)
    if subtitles_to_translate is None:
        return
    
    queue_subtitles_for_translation(subtitles_to_translate)

async def main(base_url, api_key):
    for i in range(num_workers):
        threading.Thread(target=translation_worker, args=(i, base_url, api_key), daemon=True).start()
    
    while not shutdown_event.is_set():
        try:
            if series_scan:
                await scan_and_process_series(base_url, api_key)
            if movies_scan:
                await scan_and_process_movies(base_url, api_key)
        except Exception as e:
            logger.error(f"Uncaugth exception: {e}")
        
        await asyncio.sleep(interval_between_scans)

def handle_shutdown():
    logger.info("Received exit signal")
    sys.exit(1)



if __name__ == "__main__":
    # Do verification on arguments
    base_url = os.getenv("BAZARR_BASE_URL")
    api_key = os.getenv("BAZARR_API_KEY")

    if base_url is None:
        print("BAZARR_BASE_URL is missing")
        sys.exit(1)

    if api_key is None:
        print("BAZARR_API_KEY is missing")
        sys.exit(1)

    if len(base_languages) == 0:
        print("Missing BASE_LANGUAGES")
        sys.exit(1)
    
    wrong_languages = [lang for lang in base_languages if len(lang) > 2 or len(lang) < 2]
    if len(wrong_languages) > 0:
        print(f"Wrong languages given in BASE_LANGUAGES, wrong ones: {wrong_languages}, expected to be 2 characters long (code2)")
        sys.exit(1)

    if len(to_languges) == 0:
        print("Missing TO_LANGUAGES")
        sys.exit(1)

    wrong_languages = [lang for lang in to_languges if len(lang) > 2 or len(lang) < 2]
    if len(wrong_languages) > 0:
        print(f"Wrong languages given in TO_LANGUAGES, wrong ones: {wrong_languages}, expected to be 2 characters long (code2)")
        sys.exit(1)

    if not series_scan and not movies_scan:
        print("Both series and movies scan are disabled, nothing will be done")
        sys.exit(1) 

    # Setup logger
    logger.propagate = False
    trailing_slash = "/" if not log_directory.endswith("/") else ""
    os.makedirs(log_directory, exist_ok=True)
    handler = TimedRotatingFileHandler(
        f"{log_directory}{trailing_slash}bazarr_lingarr_autotranslate.log", when="midnight", interval=1, backupCount=4
    )
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    match log_level.lower():
        case "info":
            logger.setLevel(logging.INFO)
        case "debug":
            logger.setLevel(logging.DEBUG)
            logger.debug("Configuration: --------------------")
            logger.debug(f"bazarr_base_url: {base_url}")
            logger.debug(f"base_languages: {base_languages}")
            logger.debug(f"to_languages: {to_languges}")
            logger.debug(f"translation_request_timeout: {translation_request_timeout}")
            logger.debug(f"num_workers: {num_workers}")
            logger.debug(f"interval_between_scans: {interval_between_scans}")
            logger.debug(f"log_level: {log_level}")
            logger.debug(f"log_directory: {log_directory}")
            logger.debug(f"series_scan: {series_scan}")
            logger.debug(f"movies_scan: {movies_scan}")
            logger.debug("End Configuration: ----------------")
        case "error":
            logger.setLevel(logging.ERROR)

    # Start running things
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_shutdown)

    loop.run_until_complete(main(base_url, api_key))
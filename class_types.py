"""
Typed models for the parts of the Bazarr API this tool consumes.

Every model provides a from_dict classmethod that validates that required
fields are present and have the expected type. A failure raises a ValueError
that names the offending field, instead of a bare AssertionError.

Instances are plain dataclasses, so dataclasses.asdict() serializes them
(for debug logging).
"""

from dataclasses import dataclass
from typing import Any, Callable, List, Optional


def _check_type(key: str, value: Any, expected: type) -> None:
    # isinstance(True, int) is True, so int fields must explicitly reject bools
    if expected is not bool and isinstance(value, bool):
        raise ValueError(f"Field {key!r} must be {expected.__name__}, got {value!r}")
    if not isinstance(value, expected):
        raise ValueError(f"Field {key!r} must be {expected.__name__}, got {value!r}")


def _required(obj: Any, key: str, expected: type) -> Any:
    """Fetch a required field and validate its type."""
    if not isinstance(obj, dict):
        raise ValueError(f"Expected an object, got {type(obj).__name__}")
    if key not in obj:
        raise ValueError(f"Missing required field {key!r}")
    value = obj[key]
    _check_type(key, value, expected)
    return value


def _optional(obj: Any, key: str, expected: type) -> Any:
    """Fetch an optional field; a missing key or None is returned as None."""
    value = obj.get(key) if isinstance(obj, dict) else None
    if value is None:
        return None
    _check_type(key, value, expected)
    return value


def _parse_list(obj: Any, key: str, parse_item: Callable[[Any], Any]) -> List[Any]:
    """Parse a required field that holds a list of objects."""
    return [parse_item(entry) for entry in _required(obj, key, list)]


def _parse_optional_list(obj: Any, key: str, parse_item: Callable[[Any], Any]) -> Optional[List[Any]]:
    """Parse a list field that may be absent or null."""
    value = obj.get(key) if isinstance(obj, dict) else None
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"Field {key!r} must be a list, got {type(value).__name__}")
    return [parse_item(entry) for entry in value]


@dataclass
class MissingSubtitle:
    name: str
    code2: str
    code3: str
    forced: bool
    hi: bool

    @classmethod
    def from_dict(cls, obj: Any) -> "MissingSubtitle":
        return cls(
            name=_required(obj, "name", str),
            code2=_required(obj, "code2", str),
            code3=_required(obj, "code3", str),
            forced=_required(obj, "forced", bool),
            hi=_required(obj, "hi", bool),
        )


@dataclass
class Subtitle:
    name: str
    code2: str
    code3: str
    # None for embedded subtitles
    path: Optional[str]
    forced: bool
    hi: bool
    file_size: Optional[int]

    @classmethod
    def from_dict(cls, obj: Any) -> "Subtitle":
        return cls(
            name=_required(obj, "name", str),
            code2=_required(obj, "code2", str),
            code3=_required(obj, "code3", str),
            # Embedded subtitles have no path and no file size
            path=_optional(obj, "path", str),
            forced=_required(obj, "forced", bool),
            hi=_required(obj, "hi", bool),
            file_size=_optional(obj, "file_size", int),
        )


@dataclass
class Serie:
    missing_subtitles: List[MissingSubtitle]
    monitored: Optional[bool]
    # Present on /api/episodes, absent on /api/episodes/wanted
    path: Optional[str]
    sonarr_episode_id: int
    sonarr_series_id: int
    subtitles: Optional[List[Subtitle]]
    title: Optional[str]
    series_title: Optional[str]
    # A string like "3x15" on /api/episodes/wanted
    episode_number: Optional[str]
    episode_title: Optional[str]

    @classmethod
    def from_dict(cls, obj: Any) -> "Serie":
        return cls(
            missing_subtitles=_parse_list(obj, "missing_subtitles", MissingSubtitle.from_dict),
            monitored=_optional(obj, "monitored", bool),
            path=_optional(obj, "path", str),
            sonarr_episode_id=_required(obj, "sonarrEpisodeId", int),
            sonarr_series_id=_required(obj, "sonarrSeriesId", int),
            subtitles=_parse_optional_list(obj, "subtitles", Subtitle.from_dict),
            title=_optional(obj, "title", str),
            series_title=_optional(obj, "seriesTitle", str),
            episode_number=_optional(obj, "episode_number", str),
            episode_title=_optional(obj, "episodeTitle", str),
        )


@dataclass
class Movie:
    title: str
    missing_subtitles: List[MissingSubtitle]
    radarr_id: int
    monitored: Optional[bool]
    path: Optional[str]
    subtitles: Optional[List[Subtitle]]

    @classmethod
    def from_dict(cls, obj: Any) -> "Movie":
        return cls(
            title=_required(obj, "title", str),
            missing_subtitles=_parse_list(obj, "missing_subtitles", MissingSubtitle.from_dict),
            radarr_id=_required(obj, "radarrId", int),
            monitored=_optional(obj, "monitored", bool),
            path=_optional(obj, "path", str),
            subtitles=_parse_optional_list(obj, "subtitles", Subtitle.from_dict),
        )


@dataclass
class SubtitleTranslate:
    base_subtitle: Subtitle
    to_language: str
    # Flags of the TARGET translation, taken from the missing-subtitles
    # entry. Bazarr's translate API uses these values to name and register
    # the resulting subtitle file (e.g. "movie.nl.forced.srt"), so they
    # must describe the wanted variant, not the source subtitle.
    to_forced: bool
    to_hi: bool
    video_id: int
    is_serie: bool

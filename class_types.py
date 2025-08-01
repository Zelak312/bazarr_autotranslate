from typing import Any, List, TypeVar, Callable, Type, cast, Optional

T = TypeVar("T")

def from_str(x: Any) -> str:
    assert isinstance(x, str)
    return x

def from_bool(x: Any) -> bool:
    assert isinstance(x, bool)
    return x

def from_list(f: Callable[[Any], T], x: Any) -> List[T]:
    assert isinstance(x, list)
    return [f(y) for y in x]

def from_int(x: Any) -> int:
    assert isinstance(x, int) and not isinstance(x, bool)
    return x

def from_none(x: Any) -> Any:
    assert x is None
    return x

def from_union(fs, x):
    for f in fs:
        try:
            return f(x)
        except:
            pass
    assert False

def to_class(c: Type[T], x: Any) -> dict:
    assert isinstance(x, c)
    return cast(Any, x).to_dict()

def is_type(t: Type[T], x: Any) -> T:
    assert isinstance(x, t)
    return x

class MissingSubtitle:
    name: str
    code2: str
    code3: str
    forced: bool
    hi: bool

    def __init__(self, name: str, code2: str, code3: str, forced: bool, hi: bool) -> None:
        self.name = name
        self.code2 = code2
        self.code3 = code3
        self.forced = forced
        self.hi = hi

    @staticmethod
    def from_dict(obj: Any) -> 'MissingSubtitle':
        assert isinstance(obj, dict)
        name = from_str(obj.get("name"))
        code2 = from_str(obj.get("code2"))
        code3 = from_str(obj.get("code3"))
        forced = from_bool(obj.get("forced"))
        hi = from_bool(obj.get("hi"))
        return MissingSubtitle(name, code2, code3, forced, hi)

    def to_dict(self) -> dict:
        result: dict = {}
        result["name"] = from_str(self.name)
        result["code2"] = from_str(self.code2)
        result["code3"] = from_str(self.code3)
        result["forced"] = from_bool(self.forced)
        result["hi"] = from_bool(self.hi)
        return result


class Subtitle:
    name: str
    code2: str
    code3: str
    path: str
    forced: bool
    hi: bool
    file_size: int

    def __init__(self, name: str, code2: str, code3: str, path: str, forced: bool, hi: bool, file_size: int) -> None:
        self.name = name
        self.code2 = code2
        self.code3 = code3
        self.path = path
        self.forced = forced
        self.hi = hi
        self.file_size = file_size

    @staticmethod
    def from_dict(obj: Any) -> 'Subtitle':
        assert isinstance(obj, dict)
        name = from_str(obj.get("name"))
        code2 = from_str(obj.get("code2"))
        code3 = from_str(obj.get("code3"))
        path = from_str(obj.get("path"))
        forced = from_bool(obj.get("forced"))
        hi = from_bool(obj.get("hi"))
        file_size = from_int(obj.get("file_size"))
        return Subtitle(name, code2, code3, path, forced, hi, file_size)

    def to_dict(self) -> dict:
        result: dict = {}
        result["name"] = from_str(self.name)
        result["code2"] = from_str(self.code2)
        result["code3"] = from_str(self.code3)
        result["path"] = from_str(self.path)
        result["forced"] = from_bool(self.forced)
        result["hi"] = from_bool(self.hi)
        result["file_size"] = from_int(self.file_size)
        return result


class Serie:
    missing_subtitles: List[MissingSubtitle]
    monitored: Optional[bool]
    path: Optional[str]
    sonarr_episode_id: int
    sonarr_series_id: int
    subtitles: Optional[List[Subtitle]]
    title: Optional[str]
    series_title: Optional[str]
    episode_number: Optional[str]
    episode_title: Optional[str]

    def __init__(self, missing_subtitles: List[MissingSubtitle], monitored: Optional[bool], sonarr_episode_id: int, sonarr_series_id: int, subtitles: Optional[List[Subtitle]], title: Optional[str], series_title: Optional[str], episode_number: Optional[str], episode_title: Optional[str]) -> None:
        self.missing_subtitles = missing_subtitles
        self.monitored = monitored
        self.sonarr_episode_id = sonarr_episode_id
        self.sonarr_series_id = sonarr_series_id
        self.subtitles = subtitles
        self.title = title
        self.series_title = series_title
        self.episode_number = episode_number
        self.episode_title = episode_title

    @staticmethod
    def from_dict(obj: Any) -> 'Serie':
        assert isinstance(obj, dict)
        missing_subtitles = from_list(MissingSubtitle.from_dict, obj.get("missing_subtitles"))
        monitored = from_union([from_bool, from_none], obj.get("monitored"))
        sonarr_episode_id = from_int(obj.get("sonarrEpisodeId"))
        sonarr_series_id = from_int(obj.get("sonarrSeriesId"))
        subtitles = from_union([lambda x: from_list(Subtitle.from_dict, x), from_none], obj.get("subtitles"))
        title = from_union([from_str, from_none], obj.get("title"))
        series_title = from_union([from_str, from_none], obj.get("seriesTitle"))
        episode_number = from_union([from_str, from_none], obj.get("episode_number"))
        episode_title = from_union([from_str, from_none], obj.get("episodeTitle"))
        return Serie(missing_subtitles, monitored, sonarr_episode_id, sonarr_series_id, subtitles, title, series_title, episode_number, episode_title)

    def to_dict(self) -> dict:
        result: dict = {}
        result["missing_subtitles"] = from_list(lambda x: to_class(MissingSubtitle, x), self.missing_subtitles)
        if self.monitored is not None:
            result["monitored"] = from_union([from_bool, from_none], self.monitored)
        if self.path is not None:
            result["path"] = from_union([from_str, from_none], self.path)
        result["sonarrEpisodeId"] = from_int(self.sonarr_episode_id)
        result["sonarrSeriesId"] = from_int(self.sonarr_series_id)
        if self.subtitles is not None:
            result["subtitles"] = from_union([lambda x: from_list(lambda x: to_class(Subtitle, x), x), from_none], self.subtitles)
        if self.title is not None:
            result["title"] = from_union([from_str, from_none], self.title)
        if self.series_title is not None:
            result["seriesTitle"] = from_union([from_str, from_none], self.series_title)
        if self.episode_number is not None:
            result["episode_number"] = from_union([from_str, from_none], self.episode_number)
        if self.episode_title is not None:
            result["episodeTitle"] = from_union([from_str, from_none], self.episode_title)
        return result


def serie_from_dict(s: Any) -> List[Serie]:
    return from_list(Serie.from_dict, s)


def serie_to_dict(x: List[Serie]) -> Any:
    return from_list(lambda x: to_class(Serie, x), x)

class Movie:
    title: str
    missing_subtitles: List[MissingSubtitle]
    radarr_id: int
    monitored: Optional[bool]
    path: Optional[str]
    subtitles: Optional[List[Subtitle]]

    def __init__(self, title: str, missing_subtitles: List[MissingSubtitle], radarr_id: int, monitored: Optional[bool], path: Optional[str], subtitles: Optional[List[Subtitle]]) -> None:
        self.title = title
        self.missing_subtitles = missing_subtitles
        self.radarr_id = radarr_id
        self.monitored = monitored
        self.path = path
        self.subtitles = subtitles

    @staticmethod
    def from_dict(obj: Any) -> 'Movie':
        assert isinstance(obj, dict)
        title = from_str(obj.get("title"))
        missing_subtitles = from_list(MissingSubtitle.from_dict, obj.get("missing_subtitles"))
        radarr_id = from_int(obj.get("radarrId"))
        monitored = from_union([from_bool, from_none], obj.get("monitored"))
        path = from_union([from_str, from_none], obj.get("path"))
        subtitles = from_union([lambda x: from_list(Subtitle.from_dict, x), from_none], obj.get("subtitles"))
        return Movie(title, missing_subtitles, radarr_id, monitored, path, subtitles)

    def to_dict(self) -> dict:
        result: dict = {}
        result["title"] = from_str(self.title)
        result["missing_subtitles"] = from_list(lambda x: to_class(MissingSubtitle, x), self.missing_subtitles)
        result["radarrId"] = from_int(self.radarr_id)
        if self.monitored is not None:
            result["monitored"] = from_union([from_bool, from_none], self.monitored)
        if self.path is not None:
            result["path"] = from_union([from_str, from_none], self.path)
        if self.subtitles is not None:
            result["subtitles"] = from_union([lambda x: from_list(lambda x: to_class(Subtitle, x), x), from_none], self.subtitles)
        return result


def movie_from_dict(s: Any) -> List[Movie]:
    return from_list(Movie.from_dict, s)


def movie_to_dict(x: List[Movie]) -> Any:
    return from_list(lambda x: to_class(Movie, x), x)

class SubtitleTranslate:
    base_subtitle: Subtitle
    to_language: str
    video_id: int
    is_serie: bool

    def __init__(self, base_subtitle: Subtitle, to_language: str, video_id: int, is_serie: bool) -> None:
        self.base_subtitle = base_subtitle
        self.to_language = to_language
        self.video_id = video_id
        self.is_serie = is_serie

    def to_dict(self):
        return {
            "base_subtitle": self.base_subtitle.to_dict(),
            "to_language": self.to_language,
            "video_id": self.video_id,
            "is_serie": self.is_serie
        }
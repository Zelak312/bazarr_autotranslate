import pytest

from class_types import MissingSubtitle, Movie, Serie, Subtitle


def _episode(**overrides):
    episode = {
        "sonarrEpisodeId": 10,
        "sonarrSeriesId": 1,
        "missing_subtitles": [],
        "subtitles": None,
    }
    episode.update(overrides)
    return episode


def test_missing_subtitle_parses_all_fields():
    parsed = MissingSubtitle.from_dict({
        "name": "Dutch", "code2": "nl", "code3": "dut", "forced": True, "hi": False,
    })
    assert parsed.code2 == "nl"
    assert parsed.forced is True
    assert parsed.hi is False


def test_missing_required_field_is_named_in_the_error():
    with pytest.raises(ValueError, match="sonarrEpisodeId"):
        Serie.from_dict(_episode(sonarrEpisodeId=None))
    with pytest.raises(ValueError, match="missing_subtitles"):
        Serie.from_dict({"sonarrEpisodeId": 10, "sonarrSeriesId": 1})


def test_wrong_type_is_named_in_the_error():
    with pytest.raises(ValueError, match="forced"):
        MissingSubtitle.from_dict({
            "name": "x", "code2": "en", "code3": "eng", "forced": "yes", "hi": False,
        })


def test_bool_is_rejected_for_int_fields():
    # isinstance(True, int) is True, so int fields must reject bools explicitly
    with pytest.raises(ValueError, match="sonarrEpisodeId"):
        Serie.from_dict(_episode(sonarrEpisodeId=True))


def test_optional_fields_accept_none():
    # Embedded subtitles have no path and no file size
    parsed = Subtitle.from_dict({
        "name": "English", "code2": "en", "code3": "eng",
        "path": None, "forced": False, "hi": False, "file_size": None,
    })
    assert parsed.path is None
    assert parsed.file_size is None


def test_series_metadata_parses_wanted_and_full_endpoints():
    # /api/episodes/wanted returns no path, no title keys and subtitles
    wanted = Serie.from_dict({
        "sonarrEpisodeId": 10,
        "sonarrSeriesId": 1,
        "missing_subtitles": [{"name": "Dutch", "code2": "nl", "code3": "dut", "forced": False, "hi": False}],
    })
    assert wanted.path is None
    assert wanted.subtitles is None

    # /api/episodes returns the full metadata
    full = Serie.from_dict(_episode(
        path="/tv/show/s01e01.mkv",
        subtitles=[{"name": "English", "code2": "en", "code3": "eng",
                    "path": "/tv/show/s01e01.en.srt", "forced": False, "hi": False, "file_size": 1024}],
    ))
    assert full.path == "/tv/show/s01e01.mkv"
    assert full.subtitles[0].file_size == 1024


def test_movie_parses_all_fields():
    parsed = Movie.from_dict({
        "title": "Movie",
        "radarrId": 7,
        "missing_subtitles": [],
        "subtitles": None,
    })
    assert parsed.title == "Movie"
    assert parsed.radarr_id == 7
    assert parsed.subtitles is None

import asyncio

import httpx

import main
from class_types import Serie


def _episode(video_id: int, missing: list, subtitles: list) -> dict:
    """Episode metadata as returned by Bazarr's /api/episodes endpoint."""
    return {
        "sonarrEpisodeId": video_id,
        "sonarrSeriesId": 1,
        "missing_subtitles": missing,
        "subtitles": subtitles,
    }


def _missing(code2: str, forced: bool = False, hi: bool = False) -> dict:
    return {"name": code2, "code2": code2, "code3": code2, "forced": forced, "hi": hi}


def _subtitle(path, code2: str = "en", forced: bool = False, hi: bool = False) -> dict:
    return {"name": code2, "code2": code2, "code3": code2, "path": path,
            "forced": forced, "hi": hi, "file_size": 1024}


def _setup(monkeypatch, base=("en",), to=("nl",), min_age=0):
    monkeypatch.setattr(main, "base_languages", list(base))
    monkeypatch.setattr(main, "to_languages", list(to))
    monkeypatch.setattr(main, "min_subtitle_age", min_age)


def _run_find_base(videos, metadata_response, budget=None):
    async def scenario():
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/episodes"
            return httpx.Response(200, json={"data": metadata_response})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await main.find_base_language_subtitles_from_missing_subtitles(
                client, "http://bazarr", "api-key", videos, budget
            )

    return asyncio.run(scenario())


def test_matching_prefers_source_with_matching_flags(tmp_path, monkeypatch):
    _setup(monkeypatch)
    source = tmp_path / "episode.en.srt"
    source.write_text("1
00:00:01,000 --> 00:00:02,000
hello
", encoding="utf-8")

    # The forced Dutch variant is missing; both a forced and a regular
    # English source exist, so the forced one must be preferred
    wanted = Serie.from_dict(_episode(10, missing=[_missing("nl", forced=True)], subtitles=[]))
    metadata = [_episode(10, missing=[_missing("nl", forced=True)],
                        subtitles=[_subtitle(str(source), forced=True), _subtitle(str(source))])]

    result = _run_find_base([wanted], metadata)

    assert result is not None
    assert len(result) == 1
    task = result[0]
    assert task.to_language == "nl"
    assert task.to_forced is True
    assert task.to_hi is False
    assert task.base_subtitle.forced is True
    assert task.video_id == 10
    assert task.is_serie is True


def test_matching_falls_back_to_first_candidate(tmp_path, monkeypatch):
    _setup(monkeypatch)
    source = tmp_path / "episode.en.srt"
    source.write_text("x", encoding="utf-8")

    # The hearing-impaired Dutch variant is missing but only a regular
    # English source exists, so the fallback is used
    wanted = Serie.from_dict(_episode(11, missing=[_missing("nl", hi=True)], subtitles=[]))
    metadata = [_episode(11, missing=[_missing("nl", hi=True)],
                        subtitles=[_subtitle(str(source), hi=False)])]

    result = _run_find_base([wanted], metadata)

    assert result is not None
    assert len(result) == 1
    assert result[0].to_hi is True
    assert result[0].base_subtitle.hi is False


def test_source_in_target_language_is_not_used(tmp_path, monkeypatch):
    # Base and target lists overlap, but a subtitle that already has the
    # target language must never be used as a source for it
    _setup(monkeypatch, base=("en", "nl"), to=("nl",))
    source = tmp_path / "episode.nl.srt"
    source.write_text("x", encoding="utf-8")

    wanted = Serie.from_dict(_episode(10, missing=[_missing("nl")], subtitles=[]))
    metadata = [_episode(10, missing=[_missing("nl")],
                        subtitles=[_subtitle(str(source), code2="nl")])]

    assert _run_find_base([wanted], metadata) is None


def test_age_gate_skips_fresh_subtitles(tmp_path, monkeypatch):
    _setup(monkeypatch, min_age=24 * 60 * 60)
    source = tmp_path / "episode.en.srt"
    source.write_text("x", encoding="utf-8")

    wanted = Serie.from_dict(_episode(10, missing=[_missing("nl")], subtitles=[]))
    metadata = [_episode(10, missing=[_missing("nl")], subtitles=[_subtitle(str(source))])]

    assert _run_find_base([wanted], metadata) is None


def test_age_gate_accepts_old_subtitles(tmp_path, monkeypatch):
    _setup(monkeypatch, min_age=0)
    source = tmp_path / "episode.en.srt"
    source.write_text("x", encoding="utf-8")

    wanted = Serie.from_dict(_episode(10, missing=[_missing("nl")], subtitles=[]))
    metadata = [_episode(10, missing=[_missing("nl")], subtitles=[_subtitle(str(source))])]

    result = _run_find_base([wanted], metadata)
    assert result is not None
    assert len(result) == 1


def test_budget_caps_the_number_of_translations(tmp_path, monkeypatch):
    _setup(monkeypatch)
    source_a = tmp_path / "episode_a.en.srt"
    source_a.write_text("x", encoding="utf-8")
    source_b = tmp_path / "episode_b.en.srt"
    source_b.write_text("x", encoding="utf-8")

    wanted = [Serie.from_dict(_episode(10, missing=[_missing("nl")], subtitles=[])),
              Serie.from_dict(_episode(11, missing=[_missing("nl")], subtitles=[]))]
    metadata = [_episode(10, missing=[_missing("nl")], subtitles=[_subtitle(str(source_a))]),
                _episode(11, missing=[_missing("nl")], subtitles=[_subtitle(str(source_b))])]

    result = _run_find_base(wanted, metadata, budget=main.ScanBudget(1))

    assert result is not None
    assert len(result) == 1

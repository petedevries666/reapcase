from pathlib import Path

from stadium_reaper_bridge.editor.display import song_header_metadata
from stadium_reaper_bridge.stadium import StadiumSong


def test_song_header_projects_native_initial_metadata():
    song = StadiumSong.from_json_text(
        '{"name":"CLOCKSICK","ppqn":240,"params":"",'
        '"flags":["001-01.001|START;;9;200;0;4;4;Off;true;SET;PRESET;SNAP",'
        '"002-01.001|TIME;;9;175;0;3;4"],"tracks":[]}'
    )

    metadata = song_header_metadata(song, Path("/sessions/workspace/453.json"))

    assert metadata.title == "CLOCKSICK"
    assert metadata.filename == "453.json"
    assert metadata.bpm == 200
    assert (metadata.numerator, metadata.denominator) == (4, 4)
    assert metadata.flag_count == 2
    assert metadata.ppqn == 240
    assert metadata.detail == "453.json  ·  200 BPM  ·  4/4  ·  2 flags  ·  PPQN 240"


def test_song_header_removes_windows_parent_path():
    song = StadiumSong.from_dict({"name": "Song", "ppqn": 96, "flags": [], "tracks": []})

    assert song_header_metadata(song, r"D:\\MUSIQUE\\SESSIONS\\453.json").filename == "453.json"

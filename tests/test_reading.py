"""CPS 阅读速度修复任务的 15 条验收测试。

每条测试都调用 ``cps.reading`` 中尚未实现的桩；初始状态应为
``15 failed``，不能出现 collection/import/syntax error。
"""

import pytest

from cps.reading import (
    CpsError,
    balance_lines,
    char_count,
    cps,
    cps_batch,
    enforce_min_duration,
    merge_close,
    split_cue,
    validate_cues,
)


def test_defect_01_spaces_are_not_counted():
    """缺陷 1：空格被计成字符会让 CPS 偏高。"""
    assert char_count("a b c") == 3
    assert cps({"start": 0, "end": 1000, "text": "a b c"}) == pytest.approx(3.0)


def test_defect_02_cjk_punctuation_counts_half():
    """缺陷 2：CJK 标点不能按一个完整字符计。"""
    text = "你好，。"
    assert char_count(text) == 3.0
    assert cps({"start": 0, "end": 1000, "text": text}) == pytest.approx(3.0)


def test_defect_03_cps_uses_seconds_not_minutes():
    """缺陷 3：CPS 的分母是秒，不是分钟。"""
    cue = {"start": 0, "end": 1000, "text": "字"}
    assert cps(cue) == pytest.approx(1.0, abs=1e-9)
    assert cps(cue) != pytest.approx(1.0 / 60.0, abs=1e-9)


def test_defect_04_split_allocates_time_by_character_share():
    """缺陷 4：拆分时间必须按字符占比分配，而不是等分。"""
    cue = {"start": 1000, "end": 2500, "text": "短，长长长"}
    parts = split_cue(cue, 1.5)

    assert [part["text"] for part in parts] == ["短，", "长长长"]
    assert parts[0]["start"] == 1000
    # 有效字符占比为 1.5 : 3，即 1/3 : 2/3；1500 ms 应分成 500/1000。
    assert parts[0]["end"] == pytest.approx(1500, abs=1)
    assert parts[1]["start"] == pytest.approx(parts[0]["end"], abs=1)
    assert parts[1]["end"] == 2500


def test_defect_05_merge_updates_start_and_end_without_overlap():
    """缺陷 5：合并后必须更新起止，不能让结果重叠。"""
    cues = [
        {"start": 0, "end": 1000, "text": "甲"},
        {"start": 1100, "end": 1800, "text": "乙"},
        {"start": 2500, "end": 3000, "text": "丙"},
    ]
    merged = merge_close(cues, 100)

    assert merged == [
        {"start": 0, "end": 1800, "text": "甲乙"},
        {"start": 2500, "end": 3000, "text": "丙"},
    ]
    assert merged[1]["start"] >= merged[0]["end"] - 1
    # 入参不能被就地改写。
    assert cues[0] == {"start": 0, "end": 1000, "text": "甲"}


def test_defect_06_min_duration_extends_current_and_pushes_later():
    """缺陷 6：补时长不能把前一条延长后压住后一条。"""
    cues = [
        {"start": 0, "end": 400, "text": "短"},
        {"start": 500, "end": 1200, "text": "也短"},
    ]
    result = enforce_min_duration(cues, 1000)

    assert result[0]["end"] == pytest.approx(result[0]["start"] + 1000, abs=1)
    assert result[1]["start"] >= result[0]["end"] - 1
    assert result[1]["end"] - result[1]["start"] >= 999
    assert [cue["text"] for cue in result] == ["短", "也短"]


def test_defect_07_balanced_lines_never_start_with_punctuation():
    """缺陷 7：均衡断行不能把标点放到第二行行首。"""
    lines = balance_lines("这是一个测试，用于检查断行", 7)

    assert lines == ["这是一个测试，", "用于检查断行"]
    assert all(len(line) <= 7 for line in lines)
    assert lines[1][0] not in "，。！？；：、,.!?;:"


def test_defect_08_validate_reports_overlap():
    """缺陷 8：重叠必须出现在问题列表中，不能静默通过。"""
    cues = [
        {"start": 0, "end": 1000, "text": "甲"},
        {"start": 900, "end": 1600, "text": "乙"},
    ]
    issues = validate_cues(cues, 10.0)

    assert issues
    assert any("overlap" in issue.lower() for issue in issues)
    # 首尾相接在 1 ms 容差内不应误报重叠。
    touching = [
        {"start": 0, "end": 1000, "text": "甲"},
        {"start": 1000, "end": 1600, "text": "乙"},
    ]
    assert validate_cues(touching, 10.0) == []


def test_defect_09_batch_isolates_tracks_and_rolls_back():
    """缺陷 9：批量处理不能共享字幕列表，且失败必须整体回滚。"""
    tracks = [
        {"id": "a", "cues": [{"start": 0, "end": 1000, "text": "甲"}]},
        {"id": "b", "cues": [{"start": 0, "end": 1000, "text": "乙"}]},
    ]
    config = {"max_cps": 5.0, "min_ms": 500, "max_gap_ms": 50}
    result = cps_batch(tracks, config)

    assert [track["id"] for track in result["tracks"]] == ["a", "b"]
    assert [track["cues"][0]["text"] for track in result["tracks"]] == ["甲", "乙"]
    assert result["tracks"][0]["cues"] is not result["tracks"][1]["cues"]

    bad = [
        {"id": "a", "cues": [{"start": 0, "end": 1000, "text": "甲"}]},
        {"id": "b", "cues": [{"start": 2000, "end": 2000, "text": "乙"}]},
    ]
    before = [
        {"id": track["id"], "cues": [dict(cue) for cue in track["cues"]]}
        for track in bad
    ]
    with pytest.raises(CpsError):
        cps_batch(bad, config)
    assert bad == before


def test_defect_10_negative_max_cps_is_rejected():
    """缺陷 10：max_cps 小于等于 0 必须抛 CpsError，不能除零。"""
    with pytest.raises(CpsError):
        split_cue({"start": 0, "end": 1000, "text": "甲"}, -1.0)


def test_single_character_cue_has_cps_one():
    """单字符、1 秒字幕的 CPS 应为 1。"""
    assert cps({"start": 0, "end": 1000, "text": "好"}) == pytest.approx(1.0)


def test_no_split_when_max_cps_is_large_enough():
    """max_cps 足够大时，split_cue 应原样返回（副本）。"""
    cue = {"start": 0, "end": 2000, "text": "你好世界"}
    parts = split_cue(cue, 10.0)

    assert parts == [cue]
    assert parts[0] is not cue


def test_empty_subtitle_returns_empty():
    """空字幕（无有效字符）拆分应返回空列表。"""
    assert char_count(" \t\n") == 0
    assert split_cue({"start": 0, "end": 1000, "text": " \t\n"}, 5.0) == []
    assert cps_batch([], {"max_cps": 5.0, "min_ms": 0, "max_gap_ms": 0}) == {
        "tracks": []
    }


def test_zero_max_cps_raises():
    """max_cps 为 0 时不能进入除法，必须抛 CpsError。"""
    with pytest.raises(CpsError):
        split_cue({"start": 0, "end": 1000, "text": "字"}, 0)


def test_batch_success_preserves_track_order():
    """批量全部成功时，轨的顺序和内容必须保持不变。"""
    tracks = [
        {"id": "first", "cues": [{"start": 0, "end": 1000, "text": "甲"}]},
        {"id": "second", "cues": [{"start": 0, "end": 1000, "text": "乙"}]},
    ]
    before = [
        {"id": track["id"], "cues": [dict(cue) for cue in track["cues"]]}
        for track in tracks
    ]
    result = cps_batch(
        tracks,
        {"max_cps": 5.0, "min_ms": 500, "max_gap_ms": 100},
    )

    assert result == {
        "tracks": [
            {"id": "first", "cues": [{"start": 0, "end": 1000, "text": "甲"}]},
            {"id": "second", "cues": [{"start": 0, "end": 1000, "text": "乙"}]},
        ]
    }
    assert tracks == before
    assert result["tracks"][0]["cues"] is not tracks[0]["cues"]
    assert result["tracks"][0]["cues"][0] is not tracks[0]["cues"][0]

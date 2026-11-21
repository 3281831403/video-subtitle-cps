"""视频字幕阅读速度（CPS）核心工具。

统一约定
--------
* 所有 ``start`` / ``end`` / 时长 / 间隙均为整数毫秒（ms）。
* CPS 表示“有效字符数 / 秒”，不是每分钟字符数。
* 最小显示时长由 ``min_ms`` 表示，单位同样是毫秒。
* 时间边界比较容差为 1 ms：两个时间值相差不超过 1 ms 视为相等，
  因此首尾相接的字幕不算重叠。
* 一条字幕是只含 ``start``、``end``、``text`` 三个键的普通 ``dict``。
"""

import math
import string
import unicodedata

_TOLERANCE_MS = 1


class CpsError(ValueError):
    """字幕、时间值或 CPS 限制非法时抛出的异常。"""


def _is_punctuation(ch) -> bool:
    """判断单个字符是否按标点计权（ASCII 标点以及 Unicode 标点类）。"""
    return ch in string.punctuation or unicodedata.category(ch).startswith("P")


def _is_space(ch) -> bool:
    return ch.isspace()


def _char_weight(ch) -> float:
    """单个字符的有效字符权重：空白 0，标点 0.5，其他 1。"""
    if _is_space(ch):
        return 0.0
    if _is_punctuation(ch):
        return 0.5
    return 1.0


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite_number(value, name, *, positive=False) -> float:
    """校验 ``value`` 是有限数（可选地要求为正数）。"""
    if not _is_number(value) or not math.isfinite(float(value)):
        raise CpsError(f"{name} must be a finite number")
    number = float(value)
    if positive and number <= 0:
        raise CpsError(f"{name} must be positive")
    return number


def _copy_cue(cue) -> dict:
    """返回只含 ``start`` / ``end`` / ``text`` 的新字典。"""
    return {
        "start": cue["start"],
        "end": cue["end"],
        "text": cue["text"],
    }


def char_count(text) -> int:
    """返回用于计算阅读速度的有效字符数。

    契约：
      * CJK 汉字、假名、谚文、拉丁字母、数字以及其他非标点、非空白
        字符，每个计 ``1.0``。
      * 标点每个计 ``0.5``，包括 ASCII 标点与全角/CJK 标点
        （如 ``，。！？；：、,.!?;:`` 及其同类字符）。
      * 所有空白字符（空格、制表符、换行、全角空格等）均计 ``0``。
      * ``None`` 按空字符串处理。

    加权和可能为半整数（出现奇数个标点时）；返回类型标注沿用题面
    的 ``int``，需要整数值的调用方应使用加权和为整数的文本或自行取整。
    """
    if text is None:
        return 0
    total = math.fsum(_char_weight(ch) for ch in text)
    if float(total).is_integer():
        return int(total)
    return total


def cps(cue) -> float:
    """计算单条字幕的阅读速度（字符/秒）。

    时长为 ``end - start`` 毫秒。计算公式为：
    ``char_count(text) / (duration_ms / 1000.0)``。

    时长小于等于 0 时抛出 ``CpsError``；空文本或纯空白文本返回
    ``0.0``。时间使用毫秒，结果使用秒，不得再除以 60。
    """
    duration_ms = cue["end"] - cue["start"]
    if duration_ms <= 0:
        raise CpsError("cue duration must be positive")
    return char_count(cue["text"]) / (duration_ms / 1000.0)


def _first_visible(text, start):
    """返回 ``text[start:]`` 中第一个非空白字符，没有则返回 ``None``。"""
    for ch in text[start:]:
        if not _is_space(ch):
            return ch
    return None


def _can_break(text, position) -> bool:
    """``position`` 处能否断开：后一段非空且不以标点开头。"""
    if position <= 0 or position >= len(text):
        return False
    first = _first_visible(text, position)
    return first is not None and not _is_punctuation(first)


def _is_natural_break(text, position) -> bool:
    """断点是否落在标点或空白之后。"""
    return position > 0 and (
        _is_punctuation(text[position - 1]) or _is_space(text[position - 1])
    )


def _cumulative_weights(text):
    cumulative = [0.0]
    for ch in text:
        cumulative.append(cumulative[-1] + _char_weight(ch))
    return cumulative


def _max_segments(text) -> int:
    """在不产生“标点开头”分段的前提下的最大分段数。"""
    return 1 + sum(
        1 for position in range(1, len(text)) if _can_break(text, position)
    )


def split_cue(cue, max_cps) -> list[dict]:
    """按阅读速度把一条字幕拆成多条，且不改变原字幕的总时间跨度。

    ``max_cps`` 必须是有限正数，否则抛出 ``CpsError``。若当前
    ``cps(cue)`` 不大于 ``max_cps``（以 1 ms 时间容差判断），返回只含
    原字幕副本的列表。空文本或纯空白文本返回 ``[]``。

    需要拆分时，令
    ``n = max(2, ceil(当前 CPS / max_cps))``，把文本切成 ``n`` 段连续
    文本：优先在标点或空白之后断开，并选择最接近“有效字符均分”的
    合法位置；不得让下一段以标点开头。原 ``[start, end]`` 时间跨度
    保持不变，每段时长按该段有效字符数占全文有效字符数的比例分配，
    而不是把总时长等分。边界取整到毫秒，最后一段的 ``end`` 必须等于
    原字幕的 ``end``；相邻边界误差不超过 1 ms。

    返回的每条字幕只含 ``start``、``end``、``text``，顺序与原文一致，
    且不得修改入参。
    """
    limit = _finite_number(max_cps, "max_cps", positive=True)

    text = cue["text"]
    if text is None:
        text = ""
    total_weight = char_count(text)
    if total_weight <= 0:
        return []

    start = cue["start"]
    end = cue["end"]
    current = cps(cue)
    # 1 ms 时间容差：把容差范围内的高估视为“未超限”。
    duration = end - start
    if total_weight <= limit * ((duration + _TOLERANCE_MS) / 1000.0):
        return [_copy_cue(cue)]

    segments = max(2, math.ceil(current / limit - 1e-9))
    segments = min(segments, _max_segments(text))
    if segments < 2:
        return [_copy_cue(cue)]

    cumulative = _cumulative_weights(text)
    total = cumulative[-1]

    cuts = []
    previous = 0
    for index in range(1, segments):
        target = total * index / segments
        lo = previous + 1
        hi = len(text) - (segments - index)
        legal = [k for k in range(lo, hi + 1) if _can_break(text, k)]
        if not legal:
            return [_copy_cue(cue)]
        preferred = [k for k in legal if _is_natural_break(text, k)]
        pool = preferred or legal
        chosen = min(pool, key=lambda k: (abs(cumulative[k] - target), k))
        cuts.append(chosen)
        previous = chosen
    cuts.append(len(text))

    duration = end - start
    boundaries = [start]
    for index in range(1, segments):
        offset = int(round(duration * cumulative[cuts[index - 1]] / total))
        timestamp = start + offset
        timestamp = max(timestamp, boundaries[-1])
        timestamp = min(timestamp, end)
        boundaries.append(timestamp)
    boundaries.append(end)

    parts = []
    segment_start = 0
    for index in range(segments):
        segment_end = cuts[index]
        parts.append(
            {
                "start": boundaries[index],
                "end": boundaries[index + 1],
                "text": text[segment_start:segment_end],
            }
        )
        segment_start = segment_end
    return parts


def merge_close(cues, max_gap_ms) -> list:
    """合并间隙过小的相邻字幕，并保证结果不重叠。

    输入按 ``start`` 升序排列。相邻字幕间隙为
    ``next["start"] - current["end"]`` 毫秒。间隙小于等于
    ``max_gap_ms`` 时合并（负间隙，即已经重叠，也合并）。合并后的字幕
    取 ``start=min(各段 start)``、``end=max(各段 end)``，文本按原顺序
    直接拼接，不额外插入分隔符。

    持续合并直到所有相邻间隙都大于 ``max_gap_ms``；因此结果中相邻字幕
    满足 ``next.start >= current.end``（允许 1 ms 容差），即不重叠。
    ``max_gap_ms`` 小于 0 时抛出 ``CpsError``。返回新字典，不修改入参。
    """
    gap_limit = _finite_number(max_gap_ms, "max_gap_ms")
    if gap_limit < 0:
        raise CpsError("max_gap_ms must be non-negative")

    merged = []
    for cue in cues:
        current = _copy_cue(cue)
        if merged:
            previous = merged[-1]
            gap = current["start"] - previous["end"]
            if gap <= gap_limit:
                previous["start"] = min(previous["start"], current["start"])
                previous["end"] = max(previous["end"], current["end"])
                previous["text"] = (previous["text"] or "") + (current["text"] or "")
                continue
        merged.append(current)
    return merged


def enforce_min_duration(cues, min_ms) -> list:
    """为每条字幕补足最小显示时长，并顺延后续字幕。

    对每条字幕，若 ``end - start < min_ms``，延长该条字幕自己的 ``end``
    到 ``start + min_ms``；不得通过延长前一条字幕来补后一条。若延长后
    与下一条重叠（1 ms 容差），把下一条及之后所有字幕整体右移所需
    毫秒数，保持它们的时长和原有间隙。顺序和文本不得改变。

    ``min_ms`` 小于 0 时抛出 ``CpsError``。返回新字典，不修改入参。
    """
    _finite_number(min_ms, "min_ms")
    if min_ms < 0:
        raise CpsError("min_ms must be non-negative")
    minimum = min_ms

    result = []
    shift = 0
    previous_end = None
    for cue in cues:
        start = cue["start"] + shift
        end = cue["end"] + shift
        if end - start < minimum:
            end = start + minimum
        if previous_end is not None and start < previous_end - _TOLERANCE_MS:
            delta = previous_end - start
            start += delta
            end += delta
            shift += delta
        result.append(
            {
                "start": start,
                "end": end,
                "text": cue["text"],
            }
        )
        previous_end = end
    return result


def balance_lines(text, max_chars) -> list[str]:
    """把一段文本均衡地拆成两行字幕。

    ``max_chars`` 是每行允许的最大显示宽度（按 ``len`` 计算），必须为
    正整数，否则抛出 ``CpsError``。文本能放进一行时返回 ``[text]``。
    否则只选择一个断点，使两行长度尽可能接近、两行都不超过
    ``max_chars``，并且第二行不得以标点开头（断点不得紧挨在标点
    之前，优先放在标点之后）。断点处的空白需去除。

    若不存在满足条件的断点，抛出 ``CpsError``，不得返回行首为标点的
    结果。
    """
    if (
        not isinstance(max_chars, int)
        or isinstance(max_chars, bool)
        or max_chars <= 0
    ):
        raise CpsError("max_chars must be a positive integer")

    if text is None:
        text = ""
    if len(text) <= max_chars:
        return [text]

    best = None
    for position in range(1, len(text)):
        first = text[:position].rstrip()
        second = text[position:].lstrip()
        if not first or not second:
            continue
        if _is_punctuation(second[0]):
            continue
        if len(first) > max_chars or len(second) > max_chars:
            continue
        natural = _is_natural_break(text, position)
        key = (abs(len(first) - len(second)), 0 if natural else 1, position)
        if best is None or key < best[0]:
            best = (key, first, second)

    if best is None:
        raise CpsError("cannot balance text without a punctuation-leading line")
    return [best[1], best[2]]


def validate_cues(cues, max_cps) -> list[str]:
    """检查一条字幕轨，返回问题文字列表；无问题时返回 ``[]``。

    按输入顺序检查：
      * 时长小于等于 0：``"cue {i}: duration must be positive"``；
      * 与上一条重叠（``start < previous.end - 1`` ms）：
        ``"cue {i}: overlaps previous cue"``；
      * CPS 超过 ``max_cps``：
        ``"cue {i}: cps {value:.2f} exceeds {max_cps:.2f}"``。

    时间比较使用 1 ms 容差，首尾刚好相接（差 1 ms 以内）不算重叠。
    ``max_cps`` 必须为有限正数，否则抛出 ``CpsError``。空输入返回
    ``[]``。
    """
    limit = _finite_number(max_cps, "max_cps", positive=True)

    issues = []
    previous = None
    for index, cue in enumerate(cues):
        start = cue["start"]
        end = cue["end"]
        duration = end - start
        if duration <= 0:
            issues.append(f"cue {index}: duration must be positive")
        if previous is not None and start < previous["end"] - _TOLERANCE_MS:
            issues.append(f"cue {index}: overlaps previous cue")
        if duration > 0:
            value = cps(cue)
            if value > limit:
                issues.append(
                    f"cue {index}: cps {value:.2f} exceeds {limit:.2f}"
                )
        previous = cue
    return issues


def _config_value(config, key):
    if not isinstance(config, dict) or key not in config:
        raise CpsError(f"config: missing {key}")
    return config[key]


def cps_batch(tracks, config) -> dict:
    """原子地批量处理多条字幕轨。

    ``tracks`` 是 ``[{"id": str, "cues": [cue, ...]}, ...]``，顺序即输出
    顺序。``config`` 必须包含正数 ``max_cps`` 以及非负的 ``min_ms``、
    ``max_gap_ms``。

    先校验全部轨和配置；任一轨非法或配置非法时抛出 ``CpsError``（尽量
    在消息中带上轨 ``id``），并且不得处理任何轨、不得修改入参，实现
    整体回滚。全部合法时，每条轨按
    ``merge_close -> split_cue -> enforce_min_duration -> validate_cues``
    独立处理（先合并再拆分，避免把刚拆出的连续片段重新合并），最终以
    ``{"tracks": [{"id": ..., "cues": [...]}, ...]}`` 返回。

    不同轨的字幕列表和字幕字典不得共享引用，也不得与入参共享引用。
    空 ``tracks`` 返回 ``{"tracks": []}``。
    """
    if not isinstance(tracks, (list, tuple)):
        raise CpsError("tracks must be a list")

    max_cps = _finite_number(
        _config_value(config, "max_cps"), "config.max_cps", positive=True
    )
    min_ms = _finite_number(_config_value(config, "min_ms"), "config.min_ms")
    max_gap_ms = _finite_number(
        _config_value(config, "max_gap_ms"), "config.max_gap_ms"
    )
    if min_ms < 0:
        raise CpsError("config.min_ms must be non-negative")
    if max_gap_ms < 0:
        raise CpsError("config.max_gap_ms must be non-negative")

    # 先完整校验所有轨，确保任何错误都发生在处理之前（整体回滚）。
    for track_index, track in enumerate(tracks):
        if not isinstance(track, dict) or "id" not in track:
            raise CpsError(f"track {track_index}: missing id")
        track_id = track["id"]
        cues = track.get("cues")
        if not isinstance(cues, list):
            raise CpsError(f"track {track_id!r}: cues must be a list")
        for cue_index, cue in enumerate(cues):
            if not isinstance(cue, dict) or not all(
                key in cue for key in ("start", "end", "text")
            ):
                raise CpsError(
                    f"track {track_id!r}: cue {cue_index} is malformed"
                )
            if cue["text"] is not None and not isinstance(cue["text"], str):
                raise CpsError(
                    f"track {track_id!r}: cue {cue_index} text must be a string"
                )
            try:
                duration = cue["end"] - cue["start"]
            except TypeError:
                raise CpsError(
                    f"track {track_id!r}: cue {cue_index} has invalid times"
                ) from None
            if duration <= 0:
                raise CpsError(
                    f"track {track_id!r}: cue {cue_index}: duration must be positive"
                )

    processed = []
    for track in tracks:
        cues = [_copy_cue(cue) for cue in track["cues"]]
        merged = merge_close(cues, max_gap_ms)
        split = []
        for cue in merged:
            split.extend(split_cue(cue, max_cps))
        adjusted = enforce_min_duration(split, min_ms)
        validate_cues(adjusted, max_cps)
        processed.append({"id": track["id"], "cues": adjusted})

    return {"tracks": processed}

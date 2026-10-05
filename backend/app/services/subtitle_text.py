"""Strict timed-text parsing and safe, portable SRT / WebVTT export."""

import html
import re

from pydantic import BaseModel, Field, model_validator


class Cue(BaseModel):
    start_ms: int = Field(ge=0, le=604800000)
    end_ms: int = Field(gt=0, le=604800000)
    text: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def valid_cue(self):
        self.text = clean_text(self.text)
        if not self.text or self.end_ms <= self.start_ms:
            raise ValueError("字幕文字不能为空，结束时间必须大于开始时间")
        return self


def clean_text(text):
    text = html.unescape(re.sub(r"<[^>]*>", "", text))
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text).strip()


def validate_cues(cues):
    if not cues or len(cues) > 30000:
        raise ValueError("字幕必须包含 1–30000 条有效片段")
    return sorted(
        [Cue.model_validate(c).model_dump() for c in cues],
        key=lambda c: (c["start_ms"], c["end_ms"]),
    )


def timestamp(value):
    parts = value.replace(",", ".").split(":")
    if len(parts) not in {2, 3}:
        raise ValueError("字幕时间格式无效")
    hours, minutes, seconds = parts if len(parts) == 3 else ["0", *parts]
    if not 0 <= int(minutes) < 60 or not 0 <= float(seconds) < 60:
        raise ValueError("字幕时间超出范围")
    return round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)


def parse_subtitles(content):
    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", content.strip()):
        lines = block.splitlines()
        if not lines or lines[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        timing = next((i for i, line in enumerate(lines[:2]) if "-->" in line), None)
        if timing is None:
            raise ValueError("无法读取字幕时间轴，请使用 UTF-8 的 SRT 或 VTT 文件")
        match = re.fullmatch(
            r"\s*(\d{1,3}:\d{2}(?::\d{2})?[.,]\d{3})\s+-->\s+"
            r"(\d{1,3}:\d{2}(?::\d{2})?[.,]\d{3})(?:\s+.*)?",
            lines[timing],
        )
        if not match:
            raise ValueError("字幕时间格式无效")
        cues.append(
            {
                "start_ms": timestamp(match[1]),
                "end_ms": timestamp(match[2]),
                "text": "\n".join(lines[timing + 1 :]),
            }
        )
    return validate_cues(cues)


def format_stamp(ms, vtt=False):
    seconds, millis = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}{'.' if vtt else ','}{millis:03}"


def export_subtitles(cues, fmt="vtt"):
    vtt = fmt == "vtt"
    output = ["WEBVTT\n"] if vtt else []
    for idx, c in enumerate(cues):
        text = html.escape(c["text"]) if vtt else c["text"]
        output.append(
            f"{idx + 1}\n{format_stamp(c['start_ms'], vtt)} --> "
            f"{format_stamp(c['end_ms'], vtt)}\n{text}\n"
        )
    return "\n".join(output) + "\n"


def bilingual_cues(translated, original):
    # Partition at every boundary, so overlaps and independently edited tracks stay aligned.
    events = {}
    for side, cues in enumerate((original, translated)):
        for index, c in enumerate(cues):
            events.setdefault(c["start_ms"], []).append((side, index, c["text"]))
            events.setdefault(c["end_ms"], []).append((side, index, None))
    active, result = [{}, {}], []
    times = sorted(events)
    for pos, start in enumerate(times[:-1]):
        for side, idx, text in events[start]:
            if text is None:
                active[side].pop(idx, None)
            else:
                active[side][idx] = text
        text = "\n".join(v for group in active for _, v in sorted(group.items()))
        if text:
            result.append({"start_ms": start, "end_ms": times[pos + 1], "text": text})
    return result

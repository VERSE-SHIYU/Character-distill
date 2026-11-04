# -*- coding: utf-8 -*-
"""宽网列候选行 → 调用方注入的判定器逐行判 → 由判为标题的行建 `Chapters`。

纯函数：不 import 模型、不碰存储。判定器形状照 `core.retro_regions.detect` 的 `call`，
多带一格 `function`（每批的 schema 随批大小变）：`judge(system, user, function) -> 对象或 JSON`。
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from core.chapters import Chapters
from core.quotes import MAX_CANDIDATES

_WIDE = re.compile(r"第.{1,12}?[章回卷节]")
_CONTEXT_WIDTH = 30

SYSTEM = ("下面是从一本小说原文里找出的、含「第…章/回/卷/节」字样的行，每条有编号、本行、上下文各一行。"
          "请判断每个编号的「本行」是不是这本书正文里的章节标题（一章或一回的开头那一行）。"
          "卷标题与章标题写在同一行的，算章节标题；只有卷名、没有章的行，不算。"
          "简介、目录、正文叙述或台词里提到「第几章 / 第几回」的，不算。"
          "每个编号填进 h1 到 h{k}：是章节标题填「章节标题」，否则填「不是」。")

Judge = Callable[[str, str, dict[str, Any]], Any]


def candidates(source_text: str) -> list[dict[str, Any]]:
    """宽网：每行 strip 后长度 1–40 且含「第…章/回/卷/节」→ 候选（行号、原文起点、本行、上下文各一行）。"""
    lines = source_text.split("\n")
    stripped = [ln.strip() for ln in lines]
    starts: list[int] = []
    pos = 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln) + 1
    out: list[dict[str, Any]] = []
    for i, line in enumerate(stripped):
        if not (1 <= len(line) <= 40) or not _WIDE.search(line):
            continue
        j = i - 1
        while j >= 0 and not stripped[j]:
            j -= 1
        prev = stripped[j][:_CONTEXT_WIDTH] if j >= 0 else ""
        j = i + 1
        while j < len(stripped) and not stripped[j]:
            j += 1
        nxt = stripped[j][:_CONTEXT_WIDTH] if j < len(stripped) else ""
        out.append({"idx": i, "start": starts[i], "line": line, "prev": prev, "next": nxt})
    return out


def detect(source_text: str, *, judge: Judge) -> Chapters:
    """宽网为空 → 空表、0 次调用；否则每 `MAX_CANDIDATES` 一批顺序判，判为标题的行编号 1..K。"""
    items = candidates(source_text)
    if not items:
        return Chapters()
    picked: list[int] = []
    for begin in range(0, len(items), MAX_CANDIDATES):
        batch = items[begin:begin + MAX_CANDIDATES]
        k = len(batch)
        reply = judge(SYSTEM.format(k=k), _render(batch), _schema(k))
        for item, is_heading in zip(batch, _parse(reply, k)):
            if is_heading:
                picked.append(item["start"])
    return Chapters.from_positions(picked)


def _schema(k: int) -> dict[str, Any]:
    properties = {f"h{i}": {"type": "string", "enum": ["章节标题", "不是"]}
                  for i in range(1, k + 1)}
    return {
        "name": "judge_headings",
        "description": "判断每个编号的行是不是章节标题",
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def _render(batch: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"[{i}] 上一行：{it['prev'] or '（无）'}\n    本行：{it['line']}\n    下一行：{it['next'] or '（无）'}"
        for i, it in enumerate(batch, 1))


def _parse(reply: Any, k: int) -> list[bool]:
    """回复不是对象、或缺任一格 → 抛异常（不建部分章节表）。"""
    data = json.loads(reply) if isinstance(reply, str) else reply
    if not isinstance(data, dict) or any(f"h{i}" not in data for i in range(1, k + 1)):
        raise ValueError(f"章节判定回复不是对象或缺格：{reply!r}"[:200])
    return [data[f"h{i}"] == "章节标题" for i in range(1, k + 1)]

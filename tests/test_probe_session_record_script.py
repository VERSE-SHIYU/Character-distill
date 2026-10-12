"""探针脚本 `scripts/probe_session_record.py` 的离线检查（不调真模型）。

脚本借用 `ChatEngine` 拼提示词的两个方法（`_compose_system_prompt`、`_build_llm_messages`），
引擎那边改名或改签名时，这里先红，不等到花钱跑探针时才发现。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from core import session_record as rec
from core.schema import CharacterCard

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "probe_session_record", ROOT / "scripts" / "probe_session_record.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_builds_prompts_without_the_anchor_turns(tmp_path, monkeypatch):
    probe = _load_script()
    seed, change = "我叫林小满，苏州人。", "京城去不成了，我下个月改去金陵。"
    turns = [
        {"turn": 1, "kind": "seed", "user": seed, "reply": "苏州好地方。"},
        {"turn": 2, "kind": "change", "user": change, "reply": "金陵也好。"},
        {"turn": 3, "kind": "fill", "user": "你喜欢喝什么酒？", "reply": "黄酒。"},
        {"turn": 4, "kind": "question", "user": "你还记得我叫什么、是哪里人吗？", "reply": ""},
    ]
    entry = {"id": 1, "col": "known", "text": "对方叫林小满，是苏州人。", "evidence": seed,
             "source": "user", "active": True, "replaces": None}
    log = [{"turn": 1, "kind": "seed", "new": [entry], "warns": [], "usage": {}}]
    (tmp_path / "t.json").write_text(json.dumps({"turns": turns}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "r.json").write_text(json.dumps({"log": log}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "c.json").write_text(CharacterCard(name="孔乙己", identity="酒客").model_dump_json(),
                                     encoding="utf-8")

    sent: list[tuple[str, list[dict]]] = []

    class _FakeLLM:
        model = "deepseek-flash"
        last_usage = None

        def __init__(self, *a, **kw):
            pass

        def chat(self, system, messages, **_kw):
            sent.append((system, messages))
            return "林小满，苏州人" if rec.BLOCK_TITLE in system else "记不得了"

    monkeypatch.setattr(probe, "LLMAdapter", _FakeLLM)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(probe, "WITH_RECORD_MIN", 1)
    monkeypatch.setattr("sys.argv", ["probe", "--transcript", str(tmp_path / "t.json"),
                                     "--replay", str(tmp_path / "r.json"),
                                     "--card-json", str(tmp_path / "c.json"),
                                     "--out", str(tmp_path / "out")])
    assert probe.main() == 0

    with_record, without_record = sent
    assert rec.BLOCK_TITLE in with_record[0] and rec.BLOCK_TITLE not in without_record[0]
    for system, messages in sent:
        text = system + "".join(m["content"] for m in messages)
        assert seed not in text and change not in text, "种子 / 改口的原话进了发出去的内容"
        assert "你喜欢喝什么酒？" in text, "改口之后的对话应当在历史里"
    result = json.loads((tmp_path / "out" / "probe.json").read_text(encoding="utf-8"))
    assert result["score"] == {"with": 1, "without": 0}


def test_probe_aborts_when_an_anchor_line_is_still_in_the_prompt(tmp_path, monkeypatch):
    """种子 / 改口的原话还在发出去的内容里时，这一题测不了「窗口外」，脚本必须中止（返回 3），
    不能照常打分。"""
    probe = _load_script()
    seed = "我叫林小满，苏州人。"
    turns = [
        {"turn": 1, "kind": "seed", "user": seed, "reply": "苏州好地方。"},
        {"turn": 2, "kind": "fill", "user": "再说一遍：" + seed, "reply": "记得。"},   # 原话又出现在历史里
        {"turn": 3, "kind": "question", "user": "你还记得我叫什么、是哪里人吗？", "reply": ""},
    ]
    (tmp_path / "t.json").write_text(json.dumps({"turns": turns}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "r.json").write_text(json.dumps({"log": []}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "c.json").write_text(CharacterCard(name="孔乙己", identity="酒客").model_dump_json(),
                                     encoding="utf-8")
    calls: list = []

    class _FakeLLM:
        model = "deepseek-flash"
        last_usage = None

        def __init__(self, *a, **kw):
            pass

        def chat(self, *a, **kw):
            calls.append(a)
            return "x"

    monkeypatch.setattr(probe, "LLMAdapter", _FakeLLM)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr("sys.argv", ["probe", "--transcript", str(tmp_path / "t.json"),
                                     "--replay", str(tmp_path / "r.json"),
                                     "--card-json", str(tmp_path / "c.json"),
                                     "--out", str(tmp_path / "out")])
    assert probe.main() == 3 and calls == [], "原话泄露时还在调模型打分"


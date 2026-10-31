"""目标检查：ContextEngine 不向 stdout 打配置/预算信息，改走模块 logger。

测的是「达成目标」，不是「符合 spec」：

1. 构造（已知模型）：stdout 为空，预算行是 INFO。
2. 构造（未知模型）：stdout 为空，回退告警是 WARNING（已兜底，不影响结果）。
3. ``build_ex`` 产出超预算 prompt：stdout 为空，告警是 WARNING。
4. ``scripts.eval_recall._production_character_hint`` 不再需要靠
   ``redirect_stdout`` 遮住 ContextEngine —— 去掉隔离后 stdout 仍为空。
"""
from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.arc_view import project_card  # noqa: E402
from core.context_engine import ContextEngine  # noqa: E402
from core.schema import CharacterCard  # noqa: E402

pytestmark = pytest.mark.xfail(
    strict=True, reason="ContextEngine 仍用 print 写 stdout（P8 目标检查）"
)

LOGGER = "core.context_engine"


def _engine(model: str = "deepseek-flash") -> ContextEngine:
    card = project_card(CharacterCard(name="祈灼"), None)[0]
    return ContextEngine(card=card, rag=None, storage=None, model=model)


def _records(caplog) -> list[tuple[int, str]]:
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == LOGGER]


def test_known_model_logs_budget_at_info_not_stdout(capsys, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        engine = _engine("deepseek-flash")
    out = capsys.readouterr().out
    assert out == "", f"构造 ContextEngine 向 stdout 打了：{out!r}"
    assert any(
        level == logging.INFO and f"TOTAL_BUDGET={engine.TOTAL_BUDGET}" in msg
        for level, msg in _records(caplog)
    ), f"预算信息不是 INFO：{_records(caplog)}"


def test_unknown_model_logs_warning_not_stdout(capsys, caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _engine("no-such-model")
    out = capsys.readouterr().out
    assert out == "", f"未知模型回退向 stdout 打了：{out!r}"
    assert any(
        level == logging.WARNING and "unknown model" in msg
        for level, msg in _records(caplog)
    ), f"回退告警不是 WARNING：{_records(caplog)}"


def test_over_budget_prompt_logs_warning_not_stdout(capsys, caplog):
    engine = _engine("deepseek-flash")
    engine.TOTAL_BUDGET = 1  # 1 token 预算 → 必然超过 90%，与生产超预算分支同一条路径
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        engine.build_ex("你好", include_dynamic=False)
    out = capsys.readouterr().out
    assert out == "", f"超预算告警向 stdout 打了：{out!r}"
    assert any(
        level == logging.WARNING and "prompt" in msg and "budget=1" in msg
        for level, msg in _records(caplog)
    ), f"超预算告警不是 WARNING：{_records(caplog)}"


class _StubLLM:
    """构造 ChatEngine 只需要 ``model``；本用例不调 LLM。"""

    model = "deepseek-flash"
    last_usage = None

    def chat(self, system_prompt, messages):
        return "好。"

    def chat_with_tools(self, system_prompt, messages, schemas):
        return types.SimpleNamespace(content="", tool_calls=None)


def test_production_hint_does_not_print_to_stdout(capsys):
    import scripts.eval_recall as ev

    hint = ev._production_character_hint(
        CharacterCard(name="祈灼"), None, "你还记得满月宴那天吗", _StubLLM()
    )
    out = capsys.readouterr().out
    assert out == "", f"_production_character_hint 向 stdout 打了：{out!r}"
    assert hint, "角色背景不该为空"

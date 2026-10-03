# -*- coding: utf-8 -*-
"""arc-behaviors-draft 变异矩阵驱动 —— spec `docs/specs/arc-behaviors-draft.md` §5 的 M1–M12。

不变量（脚本自检）：
  1. 先验基线：靶测试全绿才开跑，否则「变异后红」说不清红源。
  2. 锚点恰一命中：`src.count(old) == 1`，否则当场 assert —— 锚点漂移会变成「变异没生效」的假绿。
  3. 还原逐字节：每条跑完立刻还原，收尾核对 sha256。

用法：python tests/perf/card_draft_mutations.py        （需测试 PG：docker-compose.test.yml）
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parents[2]
DRAFT = "core/card_draft.py"
SCHEMA = "core/schema.py"
DIST = "core/distiller.py"
ROUTE = "web/routers/distill.py"

T_CARD = "tests/test_card_draft.py"
T_ROUTE = "tests/test_distill_task_api.py::TestDraftConversion"
T_G6 = "tests/test_card_arc_behaviors.py::test_g6_prompt_carries_arc_and_behaviors_together"

# (编号, 文件, 原文, 变异, 应红的测试 -k 表达式或节点)
MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    ("M1", DRAFT, "if len(valid) == count:", "if len(valid) >= 1:",
     [f"{T_CARD}::test_u2_tagged_in_some_phases_goes_under_them_in_order",
      f"{T_CARD}::TestEntries::test_distill_sync"]),
    ("M2", DRAFT, "if len(valid) != len(set(row.phases)) or not valid:",
     "if len(valid) != len(set(row.phases)):",
     [f"{T_CARD}::test_u4_no_valid_number_retracts_the_row_with_a_warning"]),
    ("M3", DRAFT, "if 1 <= p <= count", "if 0 <= p <= count",
     [f"{T_CARD}::test_u4_no_valid_number_retracts_the_row_with_a_warning"]),
    ("M4", DRAFT,
     '            logger.warning("[card_draft] 做法的阶段编号不合法（共 %d 个阶段）%s：%s",\n'
     "                           count, row.phases, row.situation)\n",
     "            pass\n",
     [f"{T_CARD}::test_u3_out_of_range_numbers_are_dropped_with_a_warning",
      f"{T_CARD}::test_u4_no_valid_number_retracts_the_row_with_a_warning"]),
    ("M5", ROUTE,
     "            # 蒸馏流交出的是模型输出契约（草稿），转成卡只经 card_from_draft 一处。\n"
     "            card = card_from_draft(data)\n        except Exception as exc:\n"
     "            # ValidationError",
     "            # 蒸馏流交出的是模型输出契约（草稿），转成卡只经 card_from_draft 一处。\n"
     "            card = CharacterCard.model_validate(data)\n        except Exception as exc:\n"
     "            # ValidationError",
     [f"{T_ROUTE}::test_bg_task_converts_draft",
      f"{T_CARD}::test_s1_model_output_becomes_a_card_only_through_card_from_draft"]),
    ("M6", ROUTE,
     "            card = card_from_draft(data)\n        except Exception as exc:\n"
     '            logger.error("Card validation failed: %s"',
     "            card = CharacterCard.model_validate(data)\n        except Exception as exc:\n"
     '            logger.error("Card validation failed: %s"',
     [f"{T_ROUTE}::test_run_stream_converts_draft",
      f"{T_CARD}::test_s1_model_output_becomes_a_card_only_through_card_from_draft"]),
    ("M7", DIST, "            return card_from_draft(data)\n        except ValidationError as exc:\n"
     '            print(f"Pydantic 校验 CharacterCard 失败：{exc}")\n'
     '            raise DistillError("蒸馏失败：LLM 返回格式不正确，请重试", str(exc)) from exc\n\n'
     "    def distill_stream",
     "            return CharacterCard.model_validate(data)\n        except ValidationError as exc:\n"
     '            print(f"Pydantic 校验 CharacterCard 失败：{exc}")\n'
     '            raise DistillError("蒸馏失败：LLM 返回格式不正确，请重试", str(exc)) from exc\n\n'
     "    def distill_stream",
     [f"{T_CARD}::TestEntries::test_distill_sync",
      f"{T_CARD}::test_s1_model_output_becomes_a_card_only_through_card_from_draft"]),
    ("M8", DIST, "        yield json.dumps(draft.model_dump(), ensure_ascii=False)",
     "        yield json.dumps(card_from_draft(merged).model_dump(), ensure_ascii=False)",
     [f"{T_CARD}::TestEntries::test_stream_grouped_yields_draft"]),
    ("M9", DIST,
     "    def distill(self, text: str, character_name: str) -> CharacterCard:",
     "    def distill(self, text: str, character_name: str) -> CharacterCard:\n"
     "        _unused = CharacterCard.model_json_schema()",
     [f"{T_CARD}::test_s1_schema_for_the_model_has_one_source"]),
    ("M10", SCHEMA,
     '        """旧卡的阶段是一句字符串 → 当作没有 label 的 state。"""\n'
     '        return {"state": value} if isinstance(value, str) else value',
     '        """旧卡的阶段是一句字符串 → 当作没有 label 的 state。"""\n'
     "        return value",
     [f"{T_CARD}::test_u6_legacy_arc_shapes_still_convert"]),
    ("M11", SCHEMA,
     '        return {"phases": value} if isinstance(value, list) else value',
     "        return value",
     [f"{T_CARD}::test_u6_legacy_arc_shapes_still_convert"]),
    ("M12", DIST, ', "phases": [1, 2]}\\n', "}\\n", [T_G6]),
]


def _pytest(nodes: list[str]) -> tuple[bool, str]:
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", *nodes],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = (proc.stdout.strip().splitlines() or [""])[-1]
    return proc.returncode == 0, tail


def _sha(paths: set[str]) -> dict[str, str]:
    return {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in sorted(paths)}


def main() -> int:
    targets = {f for _, f, *_ in MUTATIONS}
    before = _sha(targets)
    all_nodes = sorted({n for *_, nodes in MUTATIONS for n in nodes})
    ok, tail = _pytest(all_nodes)
    print(f"基线（靶测试）：{'绿' if ok else '红'}  {tail}")
    if not ok:
        print("基线不绿，拒跑。")
        return 2

    survived = []
    for mid, rel, old, new, nodes in MUTATIONS:
        path = ROOT / rel
        src = path.read_bytes()
        text = src.decode("utf-8")
        assert text.count(old) == 1, f"{mid}：锚点在 {rel} 命中 {text.count(old)} 次（应恰 1 次）"
        path.write_bytes(text.replace(old, new).encode("utf-8"))
        try:
            reds = []
            for node in nodes:
                green, tail = _pytest([node])
                reds.append((node.split("::")[-1], not green, tail))
        finally:
            path.write_bytes(src)
        killed = all(r for _, r, _ in reds)
        if not killed:
            survived.append(mid)
        print(f"{mid} {rel}: {'全红 ✓' if killed else '有存活 ✗'}")
        for name, red, tail in reds:
            print(f"    {'红' if red else '绿'}  {name}  ({tail})")

    after = _sha(targets)
    assert after == before, f"还原后 sha256 不一致：{[p for p in before if before[p] != after[p]]}"
    print(f"还原核对：{len(before)} 个文件 sha256 与开跑前一致")
    print("结论：" + ("全部变异被打红" if not survived else f"存活 {survived}"))
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())

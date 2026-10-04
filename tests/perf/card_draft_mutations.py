# -*- coding: utf-8 -*-
"""card_draft 变异矩阵驱动 —— 草稿契约的 38 条变异各有专属红源（`docs/specs/`。

覆盖域是新锁 `tests/test_card_draft.py`（`c4a6364c` 把草稿契约的判别器聚到一个文件）。
跑法交 `mutation_framework.run_matrix` —— 与另外四个驱动共用一份执行原语；本文件只留
变异表与基线门，不再自写执行代码（原实现拿不到「红在哪一行」，产物写不出来、元锁恒红）。

**变异表逐条预跑**：每条在 `c4a6364c` 上全红；期望一律 `RED`、不设 marker（每条的红源
是整条判据，不靠红源里某个标记串鉴别）。

用法：python tests/perf/card_draft_mutations.py        （需测试 PG：docker-compose.test.yml）
"""
from __future__ import annotations

import pathlib
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

PERF_DIR = pathlib.Path(__file__).resolve().parent
ROOT = PERF_DIR.parents[1]
sys.path.insert(0, str(PERF_DIR))
sys.path.insert(0, str(ROOT / "tests"))

import lock_coverage  # noqa: E402  —— 判档与产物写入的唯一一份实现
import mutation_framework as framework  # noqa: E402  —— 执行原语的唯一一份实现

ARTIFACT = PERF_DIR / "card_draft_red_lines.json"

# 预跑实测：每条在 c4a6364c 上全红，「撞到」= 新域 tests/test_card_draft.py 里被撞到的判别器行号。
TARGET = "tests/test_card_draft.py"
DRAFT, SCHEMA, DIST, ROUTE = (ROOT / "core" / "card_draft.py", ROOT / "core" / "schema.py",
                              ROOT / "core" / "distiller.py", ROOT / "web" / "routers" / "distill.py")

TARGETS = (DRAFT, SCHEMA, DIST, ROUTE)

MUTATIONS = [
    # 撞到 [49, 75, 83]
    ('M1  分发：只要有一个合法编号就放顶层（「所有阶段都标了」退化成「标了任一阶段」）',
     TARGET, [("repl", DRAFT, [
         ('if len(valid) == count:',
          'if len(valid) >= 1:'),
     ])], "RED"),
    # 撞到 [93]
    ('M2  warning 条件漏掉「编号全部作废」：整条撤回时不留痕',
     TARGET, [("repl", DRAFT, [
         ('if len(valid) != len(set(row.phases)) or not valid:',
          'if len(valid) != len(set(row.phases)):'),
     ])], "RED"),
    # 撞到 [92]
    ('M3  阶段编号下界 1 → 0：0 号被当成合法编号',
     TARGET, [("repl", DRAFT, [
         ('if 1 <= p <= count',
          'if 0 <= p <= count'),
     ])], "RED"),
    # 撞到 [84, 93]
    ('M4  删掉非法编号的 warning',
     TARGET, [("repl", DRAFT, [
         ('            logger.warning("[card_draft] 做法的阶段编号不合法（共 %d 个阶段）%s：%s",\n                           count, row.phases, row.situation)\n',
          '            pass\n'),
     ])], "RED"),
    # 撞到 [50, 83]
    ('M13  挂阶段时下标算反：阶段 p 的做法挂到倒数第 p 个阶段',
     TARGET, [("repl", DRAFT, [
         ('by_phase[p - 1].append(behavior)',
          'by_phase[count - p].append(behavior)'),
     ])], "RED"),
    # 撞到 [51]
    ('M14  转卡时丢掉阶段的 label',
     TARGET, [("repl", DRAFT, [
         ('    card = draft.model_dump()\n',
          '    card = draft.model_dump(exclude={"character_arc": {"phases": {"__all__": {"label"}}}})\n'),
     ])], "RED"),
    # 撞到 [49, 69]
    ('M15  顶层判据写成 len(valid) > count：标了所有阶段的做法永远进不了顶层',
     TARGET, [("repl", DRAFT, [
         ('if len(valid) == count:',
          'if len(valid) > count:'),
     ])], "RED"),
    # 撞到 [50, 70]
    ('M16  顶层与阶段不再互斥：进了顶层的做法同时挂到各阶段',
     TARGET, [("repl", DRAFT, [
         ('            general.append(behavior)\n        else:\n            for p in valid:',
          '            general.append(behavior)\n        if True:\n            for p in valid:'),
     ])], "RED"),
    # 撞到 [76]
    ('M17  同一阶段内做法倒序（append → insert(0)）',
     TARGET, [("repl", DRAFT, [
         ('by_phase[p - 1].append(behavior)',
          'by_phase[p - 1].insert(0, behavior)'),
     ])], "RED"),
    # 撞到 [50, 77, 83]
    ('M18  有合法编号就挂到所有阶段，不看标了哪几个',
     TARGET, [("repl", DRAFT, [
         ('            for p in valid:\n',
          '            for p in (valid and range(1, count + 1)):\n'),
     ])], "RED"),
    # 撞到 [83, 92]
    ('M19  越界编号被夹到最后一个阶段，而不是撤回',
     TARGET, [("repl", DRAFT, [
         ('valid = sorted({p for p in row.phases if 1 <= p <= count})',
          'valid = sorted({min(p, count) for p in row.phases if 1 <= p})'),
     ])], "RED"),
    # 撞到 [91]
    ('M20  编号全部作废的做法改放顶层，而不是整条撤回',
     TARGET, [("repl", DRAFT, [
         ('if len(valid) == count:',
          'if len(valid) == count or not valid:'),
     ])], "RED"),
    # 撞到 [99]
    ('M21  无阶段的卡丢掉全部做法',
     TARGET, [("repl", DRAFT, [
         ('        if count == 0:\n            general.append(behavior)\n            continue\n',
          '        if count == 0:\n            continue\n'),
     ])], "RED"),
    # 撞到 [100]
    ('M22  无阶段的卡也走编号校验：做法照收，但每条打一次 warning',
     TARGET, [("repl", DRAFT, [
         ('        if count == 0:\n            general.append(behavior)\n            continue\n',
          ''),
     ])], "RED"),
    # 撞到 [110, 180, 188, 196, 280, 315]
    ('M23  形态不对时宽容兜底（situation_behaviors 不是列表就当空列表），不再抛 ValidationError',
     TARGET, [("repl", DRAFT, [
         ('    draft = CardDraft.model_validate(data)\n',
          '    if not isinstance(data.get("situation_behaviors", []), list):\n        data = {**data, "situation_behaviors": []}\n    draft = CardDraft.model_validate(data)\n'),
     ])], "RED"),
    # 撞到 [116]
    ('M24  草稿 schema 标题回到 CardDraft：模型看到的不再是「角色卡」',
     TARGET, [("repl", DRAFT, [
         ('    model_config = ConfigDict(title="CharacterCard")\n',
          ''),
     ])], "RED"),
    # 撞到 [117, 235, 275, 308]
    ('M25  草稿做法丢掉 phases 字段',
     TARGET, [("repl", DRAFT, [
         ('    phases: list[int] = []\n',
          ''),
     ])], "RED"),
    # 撞到 [118]
    ('M26  草稿弧线的阶段改用存卡的 ArcPhase（阶段下带 behaviors）',
     TARGET, [("repl", DRAFT, [
         ('    phases: list[PhaseState] = []\n',
          '    phases: list[ArcPhase] = []\n'),
         ('ArcAxis, CharacterCard, PhaseState,',
          'ArcAxis, ArcPhase, CharacterCard, PhaseState,'),
     ])], "RED"),
    # 撞到 [120]
    ('M27  分组 schema 漏进组外字段（name 进了每一组）',
     TARGET, [("repl", DRAFT, [
         ('if k in fields},',
          'if k in fields or k == "name"},'),
     ])], "RED"),
    # 撞到 [121]
    ('M28  分组 schema 取自存卡 CharacterCard：G6 的做法引用变成 SituationBehavior',
     TARGET, [("repl", DRAFT, [
         ('    full = CardDraft.model_json_schema()\n',
          '    full = (CardDraft if group is None else CharacterCard).model_json_schema()\n'),
     ])], "RED"),
    # 撞到 [49, 70, 76, 83, 92, 106, 127, 365]
    ('M29  转卡跳过校验（model_construct）：phases 标注留在存卡里',
     TARGET, [("repl", DRAFT, [
         ('    return CharacterCard.model_validate(card)\n',
          '    return CharacterCard.model_construct(**card)\n'),
     ])], "RED"),
    # 撞到 [106]
    ('M10  旧卡字符串阶段被当成 label 而不是 state',
     TARGET, [("repl", SCHEMA, [
         ('        return {"state": value} if isinstance(value, str) else value',
          '        return {"label": value} if isinstance(value, str) else value'),
     ])], "RED"),
    # 撞到 [106]
    ('M11  旧卡阶段列表转换时丢掉第一个阶段',
     TARGET, [("repl", SCHEMA, [
         ('        return {"phases": value} if isinstance(value, list) else value',
          '        return {"phases": value[1:]} if isinstance(value, list) else value'),
     ])], "RED"),
    # 撞到 [49, 347]
    ('M7  distiller 同步入口退回 CharacterCard.model_validate（不经 card_from_draft）',
     TARGET, [("repl", DIST, [
         ('            return card_from_draft(data)\n        except ValidationError as exc:\n            print(f"Pydantic 校验 CharacterCard 失败：{exc}")\n            raise DistillError("蒸馏失败：LLM 返回格式不正确，请重试", str(exc)) from exc\n\n    def distill_stream',
          '            return CharacterCard.model_validate(data)\n        except ValidationError as exc:\n            print(f"Pydantic 校验 CharacterCard 失败：{exc}")\n            raise DistillError("蒸馏失败：LLM 返回格式不正确，请重试", str(exc)) from exc\n\n    def distill_stream'),
     ])], "RED"),
    # 撞到 [235, 347]
    ('M8  分组流式在 distiller 里就转成卡，交出的不再是草稿',
     TARGET, [("repl", DIST, [
         ('        yield json.dumps(draft.model_dump(), ensure_ascii=False)',
          '        yield json.dumps(card_from_draft(merged).model_dump(), ensure_ascii=False)'),
     ])], "RED"),
    # 撞到 [343]
    ('M9  distiller 另起一处 model_json_schema（schema 不再单一出处）',
     TARGET, [("repl", DIST, [
         ('    def distill(self, text: str, character_name: str) -> CharacterCard:',
          '    def distill(self, text: str, character_name: str) -> CharacterCard:\n        _unused = CharacterCard.model_json_schema()'),
     ])], "RED"),
    # 撞到 [320]
    ('M12  G6 提示词模板的示例做法去掉 phases 编号',
     TARGET, [("repl", DIST, [
         (', "phases": [1, 2]}\\n',
          '}\\n'),
     ])], "RED"),
    # 撞到 [210]
    ('M30  长文流式改写了模型原文（交出的草稿被改动）',
     TARGET, [("repl", DIST, [
         ('            yield token\n            tc += 1\n',
          '            yield token.replace(\'"phases": [1, 2]\', \'"phases": [1]\')\n            tc += 1\n'),
     ])], "RED"),
    # 撞到 [233]
    ('M31  分组流式每组回来就交出一段 JSON：交出的字符串不止一个',
     TARGET, [("repl", DIST, [
         ('            group_data[group] = payload\n            yield {"heartbeat": True}',
          '            group_data[group] = payload\n            yield json.dumps(payload, ensure_ascii=False)'),
     ])], "RED"),
    # 撞到 [236]
    ('M32  分组流式交出的 JSON 不是合法草稿（character_arc 被写成字符串）',
     TARGET, [("repl", DIST, [
         ('        yield json.dumps(draft.model_dump(), ensure_ascii=False)',
          '        yield json.dumps({**draft.model_dump(), "character_arc": "未定"}, ensure_ascii=False)'),
     ])], "RED"),
    # 撞到 [240]
    ('M33  分组流式合并后不校验（model_construct）：坏草稿也交出字符串',
     TARGET, [("repl", DIST, [
         ('            draft = CardDraft.model_validate(merged)\n',
          '            draft = CardDraft.model_construct(**merged)\n'),
     ])], "RED"),
    # 撞到 [241]
    ('M34  分组流式校验失败的错误帧键名不是 error',
     TARGET, [("repl", DIST, [
         ('            yield {"error": user_facing_error(exc)}\n            return\n\n        yield json.dumps(draft.model_dump()',
          '            yield {"message": user_facing_error(exc)}\n            return\n\n        yield json.dumps(draft.model_dump()'),
     ])], "RED"),
    # 撞到 [49, 347]
    ('M5  路由 R1（/start 后台任务）退回 CharacterCard.model_validate',
     TARGET, [("repl", ROUTE, [
         ('            # 蒸馏流交出的是模型输出契约（草稿），转成卡只经 card_from_draft 一处。\n            card = card_from_draft(data)\n        except Exception as exc:\n            # ValidationError',
          '            # 蒸馏流交出的是模型输出契约（草稿），转成卡只经 card_from_draft 一处。\n            card = CharacterCard.model_validate(data)\n        except Exception as exc:\n            # ValidationError'),
     ])], "RED"),
    # 撞到 [49, 347]
    ('M6  路由 R2（/run_stream）退回 CharacterCard.model_validate',
     TARGET, [("repl", ROUTE, [
         ('            # 蒸馏流交出的是模型输出契约（草稿），转成卡只经 card_from_draft 一处。\n            card = card_from_draft(data)\n        except Exception as exc:\n            logger.error("Card validation failed: %s"',
          '            # 蒸馏流交出的是模型输出契约（草稿），转成卡只经 card_from_draft 一处。\n            card = CharacterCard.model_validate(data)\n        except Exception as exc:\n            logger.error("Card validation failed: %s"'),
     ])], "RED"),
    # 撞到 [281]
    ('M35  R1 校验失败的上屏文案变了',
     TARGET, [("repl", ROUTE, [
         ('_set_task(task_id, {"status": "error", "message": "蒸馏失败：数据校验错误，请重试", "character": name})',
          '_set_task(task_id, {"status": "error", "message": "蒸馏失败：服务内部异常，请重试", "character": name})'),
     ])], "RED"),
    # 撞到 [106, 128, 365]
    ('M36  旧卡阶段校验器非幂等：每校验一次给 state 加一次前缀（存了再读不等于自己）',
     TARGET, [("repl", SCHEMA, [
         ('        return {"state": value} if isinstance(value, str) else value',
          '        return {"state": value} if isinstance(value, str) else {**value, "state": "（旧）" + value.get("state", "")}'),
     ])], "RED"),
    # 撞到 [304]
    ('M37  /run_stream 的响应码不是 200',
     TARGET, [("repl", ROUTE, [
         ('        media_type="text/event-stream",\n    )\n\n\n@router.post("/reindex/{text_id}")',
          '        media_type="text/event-stream", status_code=202,\n    )\n\n\n@router.post("/reindex/{text_id}")'),
     ])], "RED"),
    # 撞到 [309]
    ('M38  /run_stream 同一张卡落库两次',
     TARGET, [("repl", ROUTE, [
         ('            result = await text_manager.save_distilled_card(\n                req.text_id, card, user_id,\n',
          '            await text_manager.save_distilled_card(\n                req.text_id, card, user_id,\n                embedding_key=emb.key, embedding_region=emb.region,\n            )\n            result = await text_manager.save_distilled_card(\n                req.text_id, card, user_id,\n'),
     ])], "RED"),
]

GROUPS = {"M": MUTATIONS}


def _baseline_gate() -> dict[str, str]:
    """先验基线：覆盖域的靶子锁绿才开跑 —— 否则「变异后红」说不清红源。

    「跑不起来」与「跑起来了但红」由 `lock_coverage.baseline_verdict` 分开（缺陷 45）。
    """
    summary, _, _, _ = framework._run("tests/test_card_draft.py")
    print(f"  基线 tests/test_card_draft.py  {summary}")
    cause = lock_coverage.baseline_verdict(summary)
    return {"tests/test_card_draft.py": cause} if cause else {}


def main() -> int:
    print("== 先验基线 ==")
    bad = _baseline_gate()
    if bad:
        return lock_coverage.refuse_on_baseline(bad)
    return framework.run_matrix(
        MUTATIONS, domain=lock_coverage.domain_of(GROUPS), targets=TARGETS, artifact=ARTIFACT,
        driver_rel="tests/perf/card_draft_mutations.py")


if __name__ == "__main__":
    sys.exit(main())

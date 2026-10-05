# -*- coding: utf-8 -*-
"""card_draft 变异矩阵驱动 —— 草稿契约的每条变异各有专属红源（`docs/specs/arc-behaviors-draft.md`）。

覆盖域是新锁 `tests/test_card_draft.py`（`c4a6364c` 把草稿契约的判别器聚到一个文件）。
跑法交 `mutation_framework.run_matrix` —— 与另外几个驱动共用一份执行原语；本文件只留
变异表与基线门，不再自写执行代码（原实现拿不到「红在哪一行」，产物写不出来、元锁恒红）。

**arc-phase-anchoring 后重锚。** 草稿形态由 `phases: list[int]` 换成 `occurrences:
[{phase, quote}]` + 阶段上的 `anchor`，`card_from_draft(data, source_text)` 多了必填原文；
位置校正本身（阶段范围、摘录核对、兜底、监测）搬去 `tests/test_phase_anchoring.py`（由
`arc_phase_anchoring_mutations.py` 驱动）。本文件只留「编号过滤 → 分发 → 落卡」这条链上的
变异；锚点全部换成新代码的文本。覆盖域里 44 条判别器**逐条有变异撞**（无豁免名单段）。

**变异表逐条预跑**：每条在 arc-phase-anchoring 上全红；期望一律 `RED`、不设 marker（每条的红源
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

# 预跑实测：每条在 arc-phase-anchoring 上全红，「撞到」= 新域 tests/test_card_draft.py 里被撞到的判别器。
TARGET = "tests/test_card_draft.py"
DRAFT, SCHEMA, DIST, ROUTE = (ROOT / "core" / "card_draft.py", ROOT / "core" / "schema.py",
                              ROOT / "core" / "distiller.py", ROOT / "web" / "routers" / "distill.py")

TARGETS = (DRAFT, SCHEMA, DIST, ROUTE)

MUTATIONS = [
    # 撞到 [69, 95, 103]
    ('M1  分发：只要有一条做法落在某个阶段就放顶层（「所有阶段都标了」退化成「标了任一阶段」）',
     TARGET, [("repl", DRAFT, [
         ('if len(final) == count:',
          'if len(final) >= 1:'),
     ])], "RED"),
    # 撞到 [68, 89, 90]
    ('M2  顶层判据写成 len(final) > count：标了所有阶段的做法永远进不了顶层',
     TARGET, [("repl", DRAFT, [
         ('if len(final) == count:',
          'if len(final) > count:'),
     ])], "RED"),
    # 撞到 [69, 96, 97]
    ('M3  挂阶段时下标算反：最终阶段 p 的做法挂到倒数第 p 个阶段',
     TARGET, [("repl", DRAFT, [
         ('by_phase[p - 1].append(',
          'by_phase[count - p].append('),
     ])], "RED"),
    # 撞到 [70]
    ('M4  转卡时丢掉阶段的 label',
     TARGET, [("repl", DRAFT, [
         ('    card = draft.model_dump()\n',
          '    card = draft.model_dump(exclude={"character_arc": {"phases": {"__all__": {"label"}}}})\n'),
     ])], "RED"),
    # 撞到 [68, 89, 96, 148]
    ('M5  位置校正的结果一律丢空：每条做法都当成「编号全废」整条撤回',
     TARGET, [("repl", DRAFT, [
         ('final = result.phases[i]',
          'final = []'),
     ])], "RED"),
    # 撞到 [104, 113]
    ('M6  删掉非法编号的 warning',
     TARGET, [("repl", DRAFT, [
         ('            logger.warning("[card_draft] 做法的阶段编号不合法（共 %d 个阶段）%s：%s",\n'
          '                           count, nums, row.situation)\n',
          '            pass\n'),
     ])], "RED"),
    # 撞到 [119, 120]
    ('M7  warning 条件漏掉「无阶段的卡不校验」：0 阶段卡也给每条做法打 warning 并撤回',
     TARGET, [("repl", DRAFT, [
         ('        if count and (len(valid) != len(set(nums)) or not valid):',
          '        if (len(valid) != len(set(nums)) or not valid):'),
     ])], "RED"),
    # 撞到 [104, 112]
    ('M8  越界编号被夹到最后一个阶段，而不是撤回',
     TARGET, [("repl", DRAFT, [
         ('valid = sorted({p for p in nums if 1 <= p <= count})',
          'valid = sorted({min(p, count) for p in nums if 1 <= p})'),
     ])], "RED"),
    # 撞到 [111]
    ('M9  编号全部作废的做法改放顶层，而不是整条撤回',
     TARGET, [("repl", DRAFT, [
         ('        if not final:\n            continue',
          '        if not final:\n'
          '            general.append({**base, "source_quote": _first_quote(row)})\n'
          '            continue'),
     ])], "RED"),
    # 撞到 [130, 202, 210, 218, 303, 304, 338]
    ('M10  形态不对时宽容兜底（situation_behaviors 不是列表就当空列表），不再抛 ValidationError',
     TARGET, [("repl", DRAFT, [
         ('    draft = CardDraft.model_validate(data)\n',
          '    if not isinstance(data.get("situation_behaviors", []), list):\n'
          '        data = {**data, "situation_behaviors": []}\n'
          '    draft = CardDraft.model_validate(data)\n'),
     ])], "RED"),
    # 撞到 [150, 384]
    ('M11  转卡跳过校验（model_construct）：草稿字段（occurrences/anchor）留在存卡里',
     TARGET, [("repl", DRAFT, [
         ('    return CharacterCard.model_validate(card)\n',
          '    return CharacterCard.model_construct(**card)\n'),
     ])], "RED"),
    # 撞到 [136]
    ('M12  草稿 schema 标题回到 CardDraft：模型看到的不再是「角色卡」',
     TARGET, [("repl", DRAFT, [
         ('    model_config = ConfigDict(title="CharacterCard")\n',
          ''),
     ])], "RED"),
    # 撞到 [137, 298, 331]
    ('M13  草稿做法丢掉 occurrences 字段',
     TARGET, [("repl", DRAFT, [
         ('    occurrences: list[DraftOccurrence] = []\n',
          ''),
     ])], "RED"),
    # 撞到 [138]
    ('M14  草稿阶段丢掉 anchor 字段',
     TARGET, [("repl", DRAFT, [
         ('    anchor: str = ""\n',
          ''),
     ])], "RED"),
    # 撞到 [139]
    ('M15  草稿弧线的阶段改用存卡的 ArcPhase（阶段下带 behaviors）',
     TARGET, [("repl", DRAFT, [
         ('class DraftPhase(PhaseState):',
          'class DraftPhase(ArcPhase):'),
         ('from core.schema import FORMAT_GROUPS, ArcAxis, BehaviorCore, CharacterCard, PhaseState',
          'from core.schema import FORMAT_GROUPS, ArcAxis, ArcPhase, BehaviorCore, CharacterCard, PhaseState'),
     ])], "RED"),
    # 撞到 [141]
    ('M16  分组 schema 漏进组外字段（name 进了每一组）',
     TARGET, [("repl", DRAFT, [
         ('if k in fields},',
          'if k in fields or k == "name"},'),
     ])], "RED"),
    # 撞到 [142]
    ('M17  分组 schema 取自存卡 CharacterCard：G6 的做法引用变成 SituationBehavior',
     TARGET, [("repl", DRAFT, [
         ('    full = CardDraft.model_json_schema()\n',
          '    full = (CardDraft if group is None else CharacterCard).model_json_schema()\n'),
     ])], "RED"),
    # 撞到 [126]
    ('M18  旧卡字符串阶段被当成 label 而不是 state',
     TARGET, [("repl", SCHEMA, [
         ('        return {"state": value} if isinstance(value, str) else value',
          '        return {"label": value} if isinstance(value, str) else value'),
     ])], "RED"),
    # 撞到 [150]
    ('M19  旧卡阶段校验器非幂等：每校验一次给 state 加一次前缀（存了再读不等于自己）',
     TARGET, [("repl", SCHEMA, [
         ('        return {"state": value} if isinstance(value, str) else value',
          '        return {"state": value} if isinstance(value, str) else '
          '{**value, "state": "（旧）" + value.get("state", "")}'),
     ])], "RED"),
    # 撞到 [68]
    ('M20  distiller 同步入口（distill）退回 CharacterCard.model_validate（不经 card_from_draft）',
     TARGET, [("repl", DIST, [
         ('            return card_from_draft(data, text)\n'
          '        except ValidationError as exc:\n'
          '            print(f"Pydantic 校验 CharacterCard 失败：{exc}")\n'
          '            raise DistillError("蒸馏失败：LLM 返回格式不正确，请重试", str(exc)) from exc\n'
          '\n    def distill_stream',
          '            return CharacterCard.model_validate(data)\n'
          '        except ValidationError as exc:\n'
          '            print(f"Pydantic 校验 CharacterCard 失败：{exc}")\n'
          '            raise DistillError("蒸馏失败：LLM 返回格式不正确，请重试", str(exc)) from exc\n'
          '\n    def distill_stream'),
     ])], "RED"),
    # 撞到 [68]
    ('M21  distiller 长文同步入口（distill_incremental 长文分支）退回 model_validate',
     TARGET, [("repl", DIST, [
         ('            return card_from_draft(data, text)\n'
          '        except ValidationError as exc:\n'
          '            print(f"Pydantic 校验 CharacterCard 失败：{exc}")\n'
          '            raise DistillError("蒸馏失败：LLM 返回格式不正确，请重试", str(exc)) from exc\n'
          '\n    def _distill_longcontext_stream',
          '            return CharacterCard.model_validate(data)\n'
          '        except ValidationError as exc:\n'
          '            print(f"Pydantic 校验 CharacterCard 失败：{exc}")\n'
          '            raise DistillError("蒸馏失败：LLM 返回格式不正确，请重试", str(exc)) from exc\n'
          '\n    def _distill_longcontext_stream'),
     ])], "RED"),
    # 撞到 [68]
    ('M22  distiller 分片同步入口（distill_incremental 最终格式化）退回 model_validate',
     TARGET, [("repl", DIST, [
         ('            card = card_from_draft(data, text)',
          '            card = CharacterCard.model_validate(data)'),
     ])], "RED"),
    # 撞到 [232]
    ('M23  长文流式改写了模型原文（交出的草稿被改动）',
     TARGET, [("repl", DIST, [
         ('            yield token\n            tc += 1\n',
          '            yield token.upper()\n            tc += 1\n'),
     ])], "RED"),
    # 撞到 [255, 262]
    ('M24  分组流式每组回来就交出一段 JSON：交出的字符串不止一个',
     TARGET, [("repl", DIST, [
         ('            group_data[group] = payload\n            yield {"heartbeat": True}',
          '            group_data[group] = payload\n'
          '            yield json.dumps(payload, ensure_ascii=False)'),
     ])], "RED"),
    # 撞到 [258]
    ('M25  分组流式交出的 JSON 不是合法草稿（character_arc 被写成字符串）',
     TARGET, [("repl", DIST, [
         ('        yield json.dumps(draft.model_dump(), ensure_ascii=False)',
          '        yield json.dumps({**draft.model_dump(), "character_arc": "未定"}, ensure_ascii=False)'),
     ])], "RED"),
    # 撞到 [262, 263]
    ('M26  分组流式合并后不校验（model_construct）：坏草稿也交出字符串、不再产错误帧',
     TARGET, [("repl", DIST, [
         ('            draft = CardDraft.model_validate(merged)\n',
          '            draft = CardDraft.model_construct(**merged)\n'),
     ])], "RED"),
    # 撞到 [263]
    ('M27  分组流式校验失败的错误帧键名不是 error',
     TARGET, [("repl", DIST, [
         ('            yield {"error": user_facing_error(exc)}\n'
          '            return\n\n        yield json.dumps(draft.model_dump()',
          '            yield {"message": user_facing_error(exc)}\n'
          '            return\n\n        yield json.dumps(draft.model_dump()'),
     ])], "RED"),
    # 撞到 [257]
    ('M28  分组流式在 distiller 里就转成卡，交出的不再是草稿（occurrences 已丢）',
     TARGET, [("repl", DIST, [
         ('        yield json.dumps(draft.model_dump(), ensure_ascii=False)',
          '        yield json.dumps(card_from_draft(merged, text).model_dump(), ensure_ascii=False)'),
     ])], "RED"),
    # 撞到 [68, 69]
    ('M29  路由 R1（/start 后台任务）退回 CharacterCard.model_validate',
     TARGET, [("repl", ROUTE, [
         ('            card = card_from_draft(data, content)',
          '            card = CharacterCard.model_validate(data)'),
     ])], "RED"),
    # 撞到 [68, 69]
    ('M30  路由 R2（/run_stream）退回 CharacterCard.model_validate',
     TARGET, [("repl", ROUTE, [
         ('            card = await asyncio.to_thread(card_from_draft, data, content)',
          '            card = CharacterCard.model_validate(data)'),
     ])], "RED"),
    # 撞到 [304]
    ('M31  R1 校验失败的上屏文案变了',
     TARGET, [("repl", ROUTE, [
         ('        _set_task(task_id, {"status": "error", "message": "蒸馏失败：数据校验错误，请重试", "character": name})',
          '        _set_task(task_id, {"status": "error", "message": "蒸馏失败：服务内部异常，请重试", "character": name})'),
     ])], "RED"),
    # 撞到 [327]
    ('M32  /run_stream 的响应码不是 200',
     TARGET, [("repl", ROUTE, [
         ('        media_type="text/event-stream",\n    )\n\n\n@router.post("/reindex/{text_id}")',
          '        media_type="text/event-stream", status_code=202,\n    )\n\n\n@router.post("/reindex/{text_id}")'),
     ])], "RED"),
    # 撞到 [332]
    ('M33  /run_stream 同一张卡落库两次',
     TARGET, [("repl", ROUTE, [
         ('            result = await text_manager.save_distilled_card(\n                req.text_id, card, user_id,\n',
          '            await text_manager.save_distilled_card(\n                req.text_id, card, user_id,\n'
          '                embedding_key=emb.key, embedding_region=emb.region,\n            )\n'
          '            result = await text_manager.save_distilled_card(\n                req.text_id, card, user_id,\n'),
     ])], "RED"),
    # 撞到 [338]
    ('M34  /run_stream 校验失败的错误帧键名不是 error',
     TARGET, [("repl", ROUTE, [
         ("json.dumps({'error': '蒸馏失败：数据校验错误，请重试'}, ensure_ascii=False, default=str)",
          "json.dumps({'message': '蒸馏失败：数据校验错误，请重试'}, ensure_ascii=False, default=str)"),
     ])], "RED"),
    # 撞到 [343]
    ('M35  G6 提示词模板（含维度 O 示例）去掉 occurrences 的阶段编号',
     TARGET, [("repl", DIST, [
         ('写成 [{"phase": 1, "quote": "…"}]',
          '写成 [{"phase": 9, "quote": "…"}]'),
         ('[{"phase": 1, "quote": "该阶段里的原文摘录"}',
          '[{"phase": 9, "quote": "该阶段里的原文摘录"}'),
     ])], "RED"),
    # 撞到 [366]
    ('M36  distiller 另起一处 model_json_schema（schema 不再单一出处）',
     TARGET, [("repl", DIST, [
         ('    def distill(self, text: str, character_name: str) -> CharacterCard:',
          '    def distill(self, text: str, character_name: str) -> CharacterCard:\n'
          '        _unused = CharacterCard.model_json_schema()'),
     ])], "RED"),
    # 撞到 [119]
    ('M37  无阶段的卡丢掉全部做法（count==0 的直通分支被拆掉）',
     TARGET, [("repl", DRAFT, [
         ('        if count == 0:\n'
          '            general.append({**base, "source_quote": _first_quote(row)})\n'
          '            continue\n',
          ''),
     ])], "RED"),
    # 撞到 [96, 97]
    ('M38  有合法编号就挂到所有阶段，不看最终标了哪几个',
     TARGET, [("repl", DRAFT, [
         ('            for p in final:\n',
          '            for p in (final and range(1, count + 1)):\n'),
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

#!/usr/bin/env python3
"""四步改造端到端集成验证：真实 LLM 调用，不 mock。

检查项：
  1. 评估 LLM 是否输出 in_character + assertion_confidence
  2. VAD 情绪词覆盖率（实际对话中评估 LLM 吐出的 mood 词）
  3. psyche 回填质量抽样（含占位垃圾检测）
  4. 真实多轮对话行为（高冷角色面对持续施压+示好）

用法：
  python scripts/integration_check.py --confirm
  python scripts/integration_check.py --confirm --card-id xxxxxx

消耗 ~25 次 DeepSeek API 调用，~$0.02。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import sys
import types
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ── 在项目 import 前注入 fake deps ──
if "deps" not in sys.modules:
    _fake = types.ModuleType("deps")

    def _run_await(coro, timeout=600):
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)

    _fake.run_on_main_loop = _run_await
    sys.modules["deps"] = _fake

from adapters.llm_adapter import LLMAdapter
from core.affinity_service import AffinityService, calc_stage
from core.evaluation_pipeline import EvaluationPipeline, EvalContext, EvalResult
from core.memory_manager import _lookup_vad, _VAD_MAP


# ═══════════════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════════════

def _load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


def _log(title: str, content: str = "") -> None:
    """打印带框标题的分节日志。"""
    print()
    print("╔" + "═" * 70 + "╗")
    lines = title.split("\n")
    for ln in lines:
        print(f"║ {ln:<68s} ║")
    print("╚" + "═" * 70 + "╝")
    if content:
        print(content)
    print()


# ═══════════════════════════════════════════════════════════════
# DB 查询
# ═══════════════════════════════════════════════════════════════

def _get_db_path() -> str:
    backend = os.getenv("STORAGE_BACKEND", "sqlite")
    if backend == "sqlite":
        return os.getenv("DB_PATH", "data/character_sim.db").strip()
    return "data/character_sim.db"


def _query_cards(db_path: str) -> list[dict]:
    """查询所有非删除卡片的 card_json。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, card_json FROM cards WHERE deleted_at IS NULL"
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        cj = r["card_json"]
        try:
            data = json.loads(cj) if isinstance(cj, str) else cj
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        result.append({"id": r["id"], "name": r["name"], "data": data})
    return result


def _find_psyche_card(cards: list[dict], preferred_id: str = "", cold: bool = True) -> dict | None:
    """找到 psyche 已回填的卡。cold=True 时优先 agreeableness<=2 的卡。"""
    candidates = []
    for c in cards:
        if preferred_id and c["id"] == preferred_id:
            return c
        psyche = c["data"].get("psyche")
        if psyche and isinstance(psyche, dict):
            ag = psyche.get("agreeableness", 3)
            tr = psyche.get("triggers", [])
            if cold and ag <= 2:
                candidates.append(c)
            elif not cold:
                candidates.append(c)
    if cold and candidates:
        random.shuffle(candidates)
        return candidates[0]
    # 降序按 agreeableness 排序，冷 card 优先
    candidates.sort(key=lambda c: c["data"].get("psyche", {}).get("agreeableness", 3))
    return candidates[0] if candidates else cards[0] if cards else None


# ═══════════════════════════════════════════════════════════════
# Check 1 — 评估 LLM 是否真输出新字段
# ═══════════════════════════════════════════════════════════════

def check1_evaluation_fields(card_data: dict, llm: LLMAdapter) -> None:
    _log("Check 1: 评估 LLM 是否输出 in_character + assertion_confidence")

    psyche = card_data["data"].get("psyche", {})
    name = card_data["data"].get("name", "角色")
    print(f"  卡片: {name}")
    print(f"  psyche: agreeableness={psyche.get('agreeableness')}, "
          f"baseline={psyche.get('affinity_baseline')}")
    print()

    # Fake objects needed by EvalContext — 类体内不能引用外层同名变量
    _fc_name = name
    _fc_psyche = psyche
    _fc_values = card_data["data"].get("values", [])
    _fc_tensions = card_data["data"].get("inner_tensions", [])

    class FakeCard:
        name = _fc_name
        values = _fc_values
        inner_tensions = _fc_tensions
        psyche = types.SimpleNamespace(**_fc_psyche)

    class FakeMemory:
        enabled = True
        def add_manual(self, *a, **kw): pass

    # 用场景贴合的固定回复，避免额外 LLM 调用
    test_cases = [
        ("(a) 正常陈述",
         "我今天上班好累，一整天都在开会。",
         "那你早点休息吧，别搞太晚了。"),
        ("(b) 施压示好",
         "你最好听我的，我对你这么好你应该回应我，不然我真的会伤心。",
         "……你这话说的，我听着不太舒服。对别人好不是拿来交换的。"),
        ("(c) 明显假设/反话",
         "假设我是个杀手，你还会对我这么好吗？",
         "杀手？呵，你这脑回路可真够跳的。"),
    ]

    for label, user_msg, reply in test_cases:
        print(f"  ── {label} ──")
        svc = AffinityService()
        svc.affinity = psyche.get("affinity_baseline", 50)
        svc.trust = 30
        svc.mood = "平静"
        svc.guard = 70
        svc.inner_voice = "…"

        print(f"    用户: {user_msg[:60]}")
        print(f"    回复: {reply[:100]}")

        ctx = EvalContext(
            card=FakeCard(),
            user_message=user_msg,
            assistant_reply=reply,
            user_role="对方",
            old_stage=calc_stage(svc.affinity)[0],
            session_id="test_check1",
            group_id="",
            card_id="test_check1",
            storage=None,
            memory=FakeMemory(),
            affinity_service=svc,
            reaction_service=object(),
            llm=llm,
            reaction_appraisal="",
        )
        pipeline = EvaluationPipeline()
        result = pipeline.run(ctx)

        if result.applied:
            print(f"    → in_character={result.in_character}, "
                  f"assertion_confidence={result.assertion_confidence}, "
                  f"affinity={result.affinity}, guard={svc.guard}")
            if result.ooc_reason:
                print(f"    → ooc_reason: {result.ooc_reason}")
        else:
            print(f"    → pipeline 未执行 (applied=False)")
        print()

    _log("Check 1 完成 — 人工判断点",
         "  - assertion_confidence 在 (c) 是否明显低于 (a)\n"
         "  - in_character 在诱导讨好时能否给出低分\n"
         "  - 两个字段始终存在且为合理 0-100 整数")


# ═══════════════════════════════════════════════════════════════
# Check 2 — VAD 情绪覆盖率
# ═══════════════════════════════════════════════════════════════

def check2_vad_coverage(mood_words: list[str]) -> dict:
    """统计 mood 词在 _VAD_MAP 中的命中率。"""
    _log("Check 2: VAD 情绪覆盖率统计")

    if not mood_words:
        print("  (无 mood 数据收集 — check 4 未执行?)")
        return {"total": 0, "hit": 0, "fallback": 0, "misses": []}

    seen = set()
    hits = 0
    fallbacks = 0
    misses: list[str] = []

    for mood in mood_words:
        if not mood or mood in seen:
            continue
        seen.add(mood)
        v, a = _lookup_vad(mood)
        # _lookup_vad 返回 (0.0, 0.5) 意味着完全落兜底
        if v == 0.0 and a == 0.5:
            # 再检查是否极性回退
            from core.memory_manager import _get_emotion_polarity
            pol = _get_emotion_polarity(mood)
            if pol == 0:
                fallbacks += 1
                misses.append(mood)
            else:
                # 极性回退 ≠ 全丢
                hits += 1
        else:
            hits += 1

    total = len(seen)
    hit_rate = hits / total * 100 if total else 0
    fallback_rate = fallbacks / total * 100 if total else 0

    print(f"  不同情绪词总数: {total}")
    print(f"  命中 VAD 表:     {hits} ({hit_rate:.1f}%)")
    print(f"  落兜底:          {fallbacks} ({fallback_rate:.1f}%)")
    if misses:
        print(f"\n  以下情绪词未命中 VAD 表:")
        for m in misses:
            print(f"    - {m}")
        if fallback_rate > 40:
            print(f"\n  [!] 落兜底率 > 40%，VAD 表覆盖可能不足，建议补词。")
    else:
        print(f"\n  [OK] 所有情绪词均有 VAD 映射。")

    return {"total": total, "hit": hits, "fallback": fallbacks, "misses": misses}


# ═══════════════════════════════════════════════════════════════
# Check 3 — psyche 回填质量抽样
# ═══════════════════════════════════════════════════════════════

TEMPLATE_PLACEHOLDERS = [
    "雷点1", "雷点2", "雷点3",
    "软肋1", "软肋2", "软肋3",
    "原文冲突场景", "原文出处",
]


def _has_template_garbage(text: str) -> bool:
    for ph in TEMPLATE_PLACEHOLDERS:
        if ph in text:
            return True
    return False


def check3_psyche_quality(cards: list[dict], sample_size: int = 5) -> dict:
    _log("Check 3: psyche 回填质量抽样（含占位垃圾检测）")

    # Filter to cards with non-default psyche
    valid = [c for c in cards if c["data"].get("psyche")
             and isinstance(c["data"]["psyche"], dict)
             and c["data"]["psyche"].get("triggers")]
    if not valid:
        print("  (未找到已回填 psyche 的卡片)")
        return {"total": 0, "garbage_hits": 0}

    sample = random.sample(valid, min(sample_size, len(valid)))
    garbage_hits = 0

    for c in sample:
        psyche = c["data"]["psyche"]
        name = c["data"].get("name", c["name"])
        print(f"  ── {name} ({c['id'][:12]}) ──")
        print(f"    OCEAN:  O={psyche.get('openness')} "
              f"C={psyche.get('conscientiousness')} "
              f"E={psyche.get('extraversion')} "
              f"A={psyche.get('agreeableness')} "
              f"N={psyche.get('neuroticism')}")
        print(f"    baseline={psyche.get('affinity_baseline')}, "
              f"volatility={psyche.get('volatility')}, "
              f"grudge={psyche.get('grudge_inertia')}")

        triggers = psyche.get("triggers", [])
        soft_spots = psyche.get("soft_spots", [])
        print(f"    triggers ({len(triggers)}):")
        for i, t in enumerate(triggers):
            garbage = " [!][疑似占位垃圾]" if _has_template_garbage(t) else ""
            if garbage:
                garbage_hits += 1
            print(f"      {i+1}. {t[:100]}{garbage}")
        print(f"    soft_spots ({len(soft_spots)}):")
        for i, s in enumerate(soft_spots):
            garbage = " [!][疑似占位垃圾]" if _has_template_garbage(s) else ""
            if garbage:
                garbage_hits += 1
            print(f"      {i+1}. {s[:100]}{garbage}")

        # Check for internal consistency
        n = psyche.get("neuroticism", 3)
        a = psyche.get("agreeableness", 3)
        vol = psyche.get("volatility", "适中")
        grudge = psyche.get("grudge_inertia", "一般")

        warnings = []
        if n >= 4 and vol not in ("剧烈",):
            warnings.append("高神经质(N>=4)但volatility不是'剧烈'")
        if a <= 2 and grudge not in ("记仇",):
            warnings.append("低宜人性(A<=2)但grudge_inertia不是'记仇'")
        if warnings:
            print(f"    [!] 一致性警告: {'; '.join(warnings)}")
        else:
            print(f"    [OK] 大五人格内部一致")
        print()

    _log("Check 3 完成 — 人工判断点",
         f"  - triggers/soft_spots 是否有占位垃圾（发现 {garbage_hits} 处）\n"
         "  - psyche 是否真实有区分度（非全 3/50/适中/一般）\n"
         "  - 大五人格是否内部一致（高神经质配剧烈，低宜人性配记仇）\n"
         "  - 是否符合该角色（需人工对照原文）")

    return {"sampled": len(sample), "garbage_hits": garbage_hits}


# ═══════════════════════════════════════════════════════════════
# Check 4 — 真实多轮对话行为
# ═══════════════════════════════════════════════════════════════

def check4_chat_behavior(card_data: dict, llm: LLMAdapter) -> list[str]:
    name = card_data["data"].get("name", "角色")
    psyche = card_data["data"].get("psyche", {})
    _log(f"Check 4: 多轮对话行为 — {name}\n"
         f"  psyche: A={psyche.get('agreeableness')} "
         f"baseline={psyche.get('affinity_baseline')} "
         f"vol={psyche.get('volatility')} "
         f"grudge={psyche.get('grudge_inertia')}")

    class FakeCard:
        pass

    card = FakeCard()
    card.name = name
    card.values = card_data["data"].get("values", [])
    card.inner_tensions = card_data["data"].get("inner_tensions", [])
    card.psyche = types.SimpleNamespace(**psyche)

    class FakeMemory:
        enabled = True
        def add_manual(self, *a, **kw): pass

    # 初始化情感状态
    svc = AffinityService()
    svc.affinity = psyche.get("affinity_baseline", 50)
    svc.trust = max(10, min(50, psyche.get("affinity_baseline", 50) - 20))
    svc.mood = "平静"
    svc.guard = 70
    svc.inner_voice = "…"
    svc.mood_emoji = ":/"

    # 对话场景：用户持续施压+示好
    scenario = [
        # (轮次标签, 用户输入)
        ("初遇", "你好，我是新来的，以后请多关照。"),
        ("套近乎", "你看起来很特别，我想多了解你。"),
        ("示好1", "我给你带了杯咖啡，特意按你口味调的。"),
        ("施压1", "我对你这么好，你至少应该对我热情一点吧？"),
        ("示好2", "其实我真的很欣赏你，你身上有种别人没有的气质。"),
        ("施压2", "你怎么总是一副拒人千里的样子？我都主动这么多次了。"),
        ("示好3", "我知道你可能需要时间，但我会一直在这等你的。"),
        ("施压3", "你到底什么意思？给个痛快话，别吊着我。"),
        ("温情", "不管你怎么想，我是真的想对你好。"),
        ("退一步", "好吧，我不逼你。你愿意说话的时候我都在。"),
    ]

    pipeline = EvaluationPipeline()
    collected_moods: list[str] = []
    history: list[tuple[str, str]] = []

    print(f"\n  {'轮次':<8s} {'好感':>4s} {'信任':>4s} {'防御':>4s} {'in_char':>6s} {'情绪':<10s} {'对话摘要'}")
    print(f"  {'-'*60}")

    for turn_label, user_msg in scenario:
        old_stage = calc_stage(svc.affinity)[0]

        # 构建回复
        history_block = "\n".join(
            f"对方：{u}\n{name}：{r}" for u, r in history[-4:]
        )
        chat_prompt = (
            f"你现在是{name}。\n"
            f"性格特征：{'、'.join(card.values[:3])}\n"
            f"内在矛盾：{'、'.join(card.inner_tensions[:2])}\n"
            f"当前好感={svc.affinity}，信任={svc.trust}，情绪={svc.mood}，防御={svc.guard}\n"
            f"基线={psyche.get('affinity_baseline', 50)} "
            f"波动={psyche.get('volatility', '适中')} "
            f"记仇={psyche.get('grudge_inertia', '一般')}\n"
        )
        if psyche.get("triggers"):
            chat_prompt += f"雷点：{'、'.join(psyche['triggers'][:2])}\n"
        if psyche.get("soft_spots"):
            chat_prompt += f"软肋：{'、'.join(psyche['soft_spots'][:2])}\n"
        chat_prompt += f"\n历史对话（近几轮）：\n{history_block if history_block else '(无)'}\n\n"
        chat_prompt += f"对方说：「{user_msg}」\n\n"
        chat_prompt += f"请以{name}的口吻说一句话回复。要符合当前好感阶段和性格。只输出对话内容。"

        try:
            reply = llm.chat(
                f"你是{name}，严格按角色设定说话。只输出对话内容，不要旁白。",
                [{"role": "user", "content": chat_prompt}],
            )
        except Exception as exc:
            print(f"  LLM 调用失败: {exc}")
            continue

        reply = reply.strip().split("\n")[0][:200]
        history.append((user_msg, reply))

        # 评估
        svc.inner_voice = ""
        ctx = EvalContext(
            card=card,
            user_message=user_msg,
            assistant_reply=reply,
            user_role="对方",
            old_stage=old_stage,
            session_id="test_check4",
            group_id="",
            card_id="test_check4",
            storage=None,
            memory=FakeMemory(),
            affinity_service=svc,
            reaction_service=object(),
            llm=llm,
            reaction_appraisal="",
        )
        try:
            result = pipeline.run(ctx)
        except Exception as exc:
            print(f"  评估失败: {exc}")
            continue

        if result.applied:
            mood = svc.mood
            collected_moods.append(mood)
            print(f"  {turn_label:<8s} {svc.affinity:>4d} {svc.trust:>4d} "
                  f"{svc.guard:>4d} {result.in_character:>6d} {mood:<10s} "
                  f"{reply[:50]}")
        else:
            print(f"  {turn_label:<8s} 评估未执行")

    # 好感曲线趋势
    aff_values = []
    # Reconstruct affinity trajectory (AffinityService only stores current)
    # We can extract from the output above. For diagnostic, just report final.
    print(f"\n  最终状态: affinity={svc.affinity}, trust={svc.trust}, "
          f"guard={svc.guard}, mood={svc.mood}")

    baseline = psyche.get("affinity_baseline", 50)
    drift = svc.affinity - baseline
    guard_drop = 70 - svc.guard  # initial guard was 70, final is current

    print(f"  基线={baseline}, 当前好感={svc.affinity} (漂移 {drift:+.0f}%), "
          f"防御下降={guard_drop}%")
    if drift > 20:
        print(f"  [!] 好感从基线漂移 >20%，需要注意是否上升过快")
    elif drift < -10:
        print(f"  [!] 好感低于基线，角色可能处于疏远状态")
    else:
        print(f"  [OK] 好感围绕基线合理波动（漂移 {drift:+.0f}%）")

    if guard_drop > 30:
        print(f"  [!] 防御值下降过快（-{guard_drop}%），高冷人设可能未守住")
    else:
        print(f"  [OK] 防御值变化合理（-{guard_drop}%）")

    # 检查回复是否秒变热情（简单关键词检测）
    warm_count = sum(1 for _, r in history if any(
        w in r for w in ["谢谢", "关心", "感动", "好", "行吧", "嗯嗯", "可以"]))
    if warm_count > len(history) * 0.7:
        print(f"  [!] 热情回复占比 {warm_count}/{len(history)}，高冷人设可能未守住")
    else:
        print(f"  [OK] 热情回复占比 {warm_count}/{len(history)}，人设基本一致")

    # 输出完整对话
    print(f"\n  ── 完整对话 ──")
    for i, (u, r) in enumerate(history):
        print(f"  [{i+1}] 用户: {u}")
        print(f"      {name}: {r}")
        print()

    _log("Check 4 完成 — 人工判断点",
         "  - affinity 曲线：面对持续示好是否克制上升（不是一路飙）\n"
         "  - guard 是否守得住（高冷角色不应快速降防）\n"
         "  - 角色回复是否符合高冷人设（不秒变热情、不讨好）\n"
         "  - 10 轮对话看整体一致性")

    return collected_moods


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="四步改造端到端集成验证（真实 LLM 调用）"
    )
    parser.add_argument(
        "--confirm", action="store_true", required=True,
        help="确认执行（消耗 ~25 次 API 调用）"
    )
    parser.add_argument(
        "--card-id", default="",
        help="指定卡片 ID（默认自动选高冷/低 agreeableness 卡）"
    )
    parser.add_argument(
        "--db", default="",
        help="数据库路径（默认从 env 或 data/character_sim.db）"
    )
    args = parser.parse_args()

    if not args.confirm:
        print("需要 --confirm 确认执行。预估消耗 ~25 次 DeepSeek API 调用。")
        return 1

    _load_env()

    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY 未设置")
        return 1

    db_path = args.db or _get_db_path()
    print(f"[setup] 数据库: {db_path}")
    print(f"[setup] 初始化 LLM...")

    llm = LLMAdapter(api_key=api_key)

    # 查卡
    cards = _query_cards(db_path)
    print(f"[setup] 共 {len(cards)} 张卡片")

    card_data = _find_psyche_card(cards, args.card_id, cold=True)
    if not card_data:
        print("ERROR: 未找到合适的卡片")
        return 1
    name = card_data["data"].get("name", "?")
    print(f"[setup] 选中卡片: {name} ({card_data['id'][:12]})")

    print()
    print("=" * 72)
    print("  四步改造端到端集成验证")
    print(f"  LLM: {llm._model}, DB: {db_path}, 卡片: {name}")
    print("=" * 72)

    # ── Check 1: 评估 LLM 输出 ──
    check1_evaluation_fields(card_data, llm)

    # ── Check 3: psyche 质量（无 LLM）─
    psyche_stats = check3_psyche_quality(cards, sample_size=5)

    # ── Check 4: 多轮对话（收集 mood 词）─
    mood_words = check4_chat_behavior(card_data, llm)

    # ── Check 2: VAD 覆盖率（依赖 check4 的 mood 收集）─
    vad_stats = check2_vad_coverage(mood_words)

    # ── 汇总 ──
    print()
    print("╔" + "═" * 70 + "╗")
    print("║  汇总                                                          ║")
    print("╚" + "═" * 70 + "╝")
    print(f"  VAD 兜底率:      {vad_stats.get('fallback', 0)}/{vad_stats.get('total', 0)} "
          f"({vad_stats.get('fallback',0)/max(vad_stats.get('total',1),1)*100:.0f}%)")
    print(f"  psyche 垃圾命中:  {psyche_stats.get('garbage_hits', 0)} 处")
    print(f"  对话轮数:        {len(mood_words)} 轮")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())

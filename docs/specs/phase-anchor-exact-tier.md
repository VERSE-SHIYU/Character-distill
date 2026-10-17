# D7-a：原文自带「……」的阶段锚点按逐字整句定位（spec）

基线：main `89738eb0`（2026-10-17，#174 合并后）。分支建议：`fix/phase-anchor-exact-tier`。
本 spec 与同目录的 `phase-anchor-exact-tier.diff` 一起交付；diff 是 Claude 在沙箱里按本 spec 写好、跑过全部检查的原型，**照 diff 落地**，不要另写一版。

部分取代 `docs/specs/arc-phase-anchoring.md` §9.8（P15）里「锚点含省略号就必须切段核对、不许整串比对」这一条；§9.8 其余内容不变。落地时在 §9.8 末尾加一行补充：「含省略号的锚点先逐字整句比对，见 `docs/specs/phase-anchor-exact-tier.md`」。

---

## 1. 目标检查（先跑这个）

**目标**：原文本身带「……」的句子被模型照抄成阶段锚点时，必须能定位，不再导致整卡没有阶段框；模型自己标出的删减，核对规则一点不放宽。

**检查 A（入库，`tests/test_phase_anchor_exact_tier_goal.py`，diff 里已有）**：

| 测试 | 守什么 | 在 main 上 |
|---|---|---|
| g1 | 原文自带省略号、省略号后只剩 3 字的句子（与谢凛羽阶段 2 锚点同形），作锚点能定位，位置是规范化坐标 | 红 |
| g2 | 整卡口径：阶段 2 用这句当锚点 → `reason == ""`，阶段 2 从这句开始 | 红（断言失败，reason=「阶段 2 锚点核对不上」） |
| g3 | 不放宽：原文没有的「长……短」删减照旧 None | 红（函数不存在） |
| g4 | 不过严：两段各 ≥10 字、同段按序的合规删减照旧通过，起点取首段 | 红（函数不存在） |
| g5 | 不含省略号的锚点结果与 `locate_in_normalized` 逐个相等 | 红（函数不存在） |

原型上 5 passed，main 上 5 failed（原始输出见 §7）。

**检查 B（真实数据，Claude 已在沙箱跑过，不入库：原文是有版权的小说）**：谢凛羽草稿（card `d0cdf200fa93`）+ 原文 `3335472a438b`。

| 项 | main | 原型 |
|---|---|---|
| 全书自带省略号的句子逐字当锚点，能定位的句数 | 163 / 996 | **996 / 996** |
| 谢凛羽草稿的整卡跳过原因 | 阶段 2 锚点核对不上：我到底是讨厌她，还是……喜欢她？ | 锚点位置不严格递增：949671 → 916341 |

第二行的变化是预期的：D7-a 修好后，这张卡剩下的是 D7-b（锚点取自日札），由下一个 PR 处理。**本 PR 不能让谢凛羽这张卡出现阶段框**，这不是本 PR 的目标。

---

## 2. 根因（每条附本轮读过的代码行）

1. 现行核对：含省略号的锚点只走切段，不做整句比对。`core/phase_anchoring.py:100-101`：
   ```python
   if has_ellipsis(anchor):                     # 省略号拼接档（P15）
       loc = locate_elided_in_normalized(source_norm, anchor, paragraphs)
   ```
2. 切段规则：每段短于 10 字的丢掉，剩不足 2 段就返回 None。`core/quotes.py:146-148`：
   ```python
   segs = [s for s in _segments(quote) if len(s) >= MIN_ELIDED_FRAGMENT_LEN]
   if len(segs) < MIN_ELIDED_FRAGMENTS:
       return None
   ```
   「我到底是讨厌她，还是……喜欢她？」切成「我到底是讨厌她还是」「喜欢她」，后段 3 字被丢，只剩 1 段 → None → 整卡跳过。
3. 这两个数和规则抄自参考实现 oberst-lab/verifiable-by-construction（`core/quotes.py:70-75` 的注释写明出处）。参考实现的判定顺序是**先逐字整句、再规范化整句、最后才切段**，取第一个对得上的。原文（提交 `33c5b8f`，2026-09-20）`eval/citation/vccr.py` `_classify`：
   ```python
   if quote in raw:
       return "exact"
   ...
   nq = cr.normalize(quote).strip("'\"")
   if nq and nq in ns:
       return "normalized"
   ...
   if nq and _match_elided(nq, ns, cr):
       return "elided"
   return "fail"
   ```
   `eval/citation/verbatim_criteria.json` v4-2026-09-02：`"tiers": {"_order": ["exact", "normalized", "elided", "fail", "unresolved"]}`。我们移植时只搬了 elided 一档。这就是根因。
4. 只影响阶段锚点，不影响其他摘录：其他摘录走 `locate_in_normalized`（`core/phase_anchoring.py:211` `loc = locate_in_normalized(source_norm, quote)`），它对每一段只要求存在、不设长度下限（`core/quotes.py:97-98`），谢凛羽草稿里另外 4 条带省略号的摘录都通过（§7）。

---

## 3. 改动（照 `phase-anchor-exact-tier.diff`）

共 5 个文件，一件事：锚点定位的判定顺序收进 `quotes.locate_anchor` 一处。

| 文件 | 改什么 |
|---|---|
| `core/quotes.py` | 新增 `locate_anchor(source_text, source_norm, anchor, paragraphs=None)`，放在 `locate_elided_in_normalized` 之后；`has_ellipsis` 的 docstring 去掉「不许退回整串比对」 |
| `core/phase_anchoring.py` | import 改为引入 `locate_anchor`（**`locate_in_normalized` 必须保留**：`verify` 在 :211 还用它）；`phase_ranges` 加参数 `source_text`（第二位），循环里四行 if/else 换成一行 `locate_anchor(...)`；`build_anchors` 把 `source_text` 传进去 |
| `tests/test_phase_anchoring.py` | 只改 `test_u1` 一行调用，补上 `_SRC` 参数（`phase_ranges` 唯一的另一个调用方） |
| `tests/perf/arc_phase_anchoring_red_lines.json` | 跑变异驱动重新生成（见 §4 第 2 条），只有两处判别器文本变化（diff 里已含原型重生成的结果，落地后仍须本地再跑一次驱动确认） |
| `tests/test_phase_anchor_exact_tier_goal.py` | 新文件，§1 检查 A |

关键代码（`core/quotes.py`，新增）：
```python
def locate_anchor(source_text: str, source_norm: str, anchor: str,
                  paragraphs: list[int] | None = None) -> list[int] | None:
    if not has_ellipsis(anchor):
        return locate_in_normalized(source_norm, anchor)
    m = re.compile(re.escape(anchor)).search(source_text)   # S13：坐标模块不用 text.find
    if m is not None:
        return normalized_starts(source_text, [m.start()])
    return locate_elided_in_normalized(source_norm, anchor, paragraphs)
```

三个设计决定（及依据）：
- **不含省略号的锚点完全走原路**。规范化整句比对已经包含逐字比对，结果与改前逐个相等（g5 守住）。
- **含省略号的锚点不走「规范化整句」那一档**。本仓 `normalize` 会删掉「…」（`core/quotes.py:36` `_DROP_CHARS` 含「…」），参考实现的规范化不删省略号（`verbatim_criteria.json` 的 `normalization` 一节没有删省略号的规则）。走这一档会放过「标了省略号、其实没删」的锚点，与参考实现不同。
- **逐字档用 `re.search`，不用 `str.find`**。`tests/test_arc_phase_select_locks.py:146-150` 的 S13 锁禁止 `core/quotes.py` 出现 `.find(`。坐标换算复用现成的 `normalized_starts`（`core/quotes.py:59`）。

---

## 4. 已查实的约束

1. `phase_ranges` 的调用方只有两处：`core/phase_anchoring.py:146`（`build_anchors`）和 `tests/test_phase_anchoring.py:79`（`test_u1`）。全仓 grep 原始输出见 §7。
2. **`tests/test_phase_anchoring.py` 是变异驱动的覆盖域**。`test_u1` 那一行文本变了，`tests/test_lock_coverage.py::test_discriminators_and_red_lines_are_the_same_set[arc_phase_anchoring_red_lines]` 会红（判别器身份按源码文本记）。处理：跑 `python tests/perf/arc_phase_anchoring_mutations.py`（需测试 PG，本机约 2.5 分钟），它会重写 `tests/perf/arc_phase_anchoring_red_lines.json`。原型上重写后的 diff 只有 M20、M32 两处判别器文本，其余不变；驱动结论「全部符合预期」，还原核对 sha256 全 True。**不要手改这个 json。**
3. 新的目标检查放单独文件，不并进 `tests/test_phase_anchoring.py`，理由同 `tests/test_phase_anchoring_elided.py:3-12` 的文件头。
4. 测试 PG：`docker compose -f docker-compose.test.yml up -d --wait`，端口 55432、库 `charsim_test`（`docker-compose.test.yml`、`tests/conftest.py:31-34`）。
5. 不涉及提示词、前端、存储、蒸馏流程。

---

## 5. 执行

- **S0**：在 main `89738eb0` 上逐条核对 §2、§4 的行号和代码原文。有一条对不上就停下报告，不要自己改方案。
- 照 `phase-anchor-exact-tier.diff` 落地（`git apply` 应能直接打上）。
- 本地只跑：
  1. `python -m pytest -q tests/test_phase_anchor_exact_tier_goal.py`（应 5 passed）
  2. `python tests/perf/arc_phase_anchoring_mutations.py`（重生成产物）
  3. 受影响的 26 个测试文件（§7 第 4 条的命令，应全部通过；原型上 490 passed）
  4. `npm test`（本 PR 不动前端，按惯例跑一遍）
- 合并门是分支 CI；合并只做 git 操作。本段改动面内发现的问题直接修，需要拍板的才停；不另外记账。

---

## 6. 判断清单（发出前逐条对过读过的行）

| # | 判断 | 依据 |
|---|---|---|
| 1 | 含省略号的锚点只走切段 | phase_anchoring.py:100-101（§2 引文） |
| 2 | 后段 <10 字被丢、不足 2 段返回 None | quotes.py:146-148 |
| 3 | 参考实现先逐字、再规范化、最后切段 | vccr.py `_classify`、verbatim_criteria.json `tiers._order`（§2 引文） |
| 4 | 其他摘录不受影响 | phase_anchoring.py:211；quotes.py:97-98；§7 真数据 5 条 |
| 5 | `normalize` 删「…」 | quotes.py:36 `_DROP_CHARS` |
| 6 | S13 锁禁 `.find(` | test_arc_phase_select_locks.py:146-150 |
| 7 | `phase_ranges` 只有两个调用方 | §7 grep |
| 8 | `verify` 仍用 `locate_in_normalized` | phase_anchoring.py:211（原型第一次漏了这个 import，受影响测试里 `test_arc_phase_unlocated_goal.py` 报 NameError，已修） |
| 9 | 改 `test_u1` 会让锁覆盖红、要重生成产物 | §7 原始输出 |

---

## 7. 原始输出

1. 目标检查，原型 / main：
```
== 原型
.....                                                                    [100%]
5 passed in 0.08s
== main
FAILED tests/test_phase_anchor_exact_tier_goal.py::test_g1_native_ellipsis_sentence_is_located
FAILED tests/test_phase_anchor_exact_tier_goal.py::test_g2_card_with_native_ellipsis_anchor_keeps_phase_starts
FAILED tests/test_phase_anchor_exact_tier_goal.py::test_g3_model_elision_still_needs_two_long_fragments
FAILED tests/test_phase_anchor_exact_tier_goal.py::test_g4_model_elision_with_two_long_fragments_still_passes
FAILED tests/test_phase_anchor_exact_tier_goal.py::test_g5_anchor_without_ellipsis_is_unchanged
5 failed in 0.07s
```
2. 真数据（检查 B）：
```
== main
自带省略号的句子 996 句，作锚点能定位 163 句
谢凛羽草稿跳过原因： 阶段 2 锚点核对不上：我到底是讨厌她，还是……喜欢她？
== 原型
自带省略号的句子 996 句，作锚点能定位 996 句
谢凛羽草稿跳过原因： 锚点位置不严格递增：949671 → 916341
```
   草稿里其他带省略号的摘录（main 上已通过引文核对）：「……汪。」「宝宝，我好想你……」各出现在原文、引文档 True。
3. `phase_ranges` / `build_anchors` 调用方（main）：
```
./tests/test_phase_anchoring.py:79:    assert phase_anchoring.phase_ranges(_phases(3, _A3), normalize(_SRC)) == (
./tests/test_phase_anchoring.py:361:    ph = phase_anchoring.build_anchors(_phases(2, {2: anchor}), _SRC2)
./tests/test_phase_anchoring_elided.py:34:    ph = phase_anchoring.build_anchors(_phases(2, {2: anchor}), _SRC2)
./core/card_draft.py:435:    anchors = phase_anchoring.build_anchors(phases, source_text)   # 整本原文只归一化这一次
```
4. 受影响测试（含锁覆盖），原型上：
```
python -m pytest -q tests/test_arc_phase_fields_entries.py tests/test_arc_phase_fields_unit.py tests/test_arc_phase_select.py tests/test_arc_phase_select_locks.py tests/test_arc_phase_unlocated.py tests/test_arc_phase_unlocated_goal.py tests/test_arc_positions.py tests/test_card_draft.py tests/test_card_quotes.py tests/test_dialogue_context_goal.py tests/test_dialogue_pairing_goal.py tests/test_distill_draft_persist.py tests/test_distiller_dialogue_pick.py tests/test_longbook_acceptance_card.py tests/test_mutation_anchors.py tests/test_personality_distill_goal.py tests/test_personality_motives_goal.py tests/test_phase_anchor_exact_tier_goal.py tests/test_phase_anchoring.py tests/test_phase_anchoring_elided.py tests/test_phase_tagged_goal.py tests/test_pre_story_memory_goal.py tests/test_quotes.py tests/test_state_baseline_zero_goal.py tests/test_state_inertia_goal.py tests/test_lock_coverage.py tests/test_arc_phase_select_locks.py
490 passed, 1 warning in 10.82s
```
   重生成产物之前，同一组：
```
FAILED tests/test_lock_coverage.py::test_discriminators_and_red_lines_are_the_same_set[arc_phase_anchoring_red_lines]
AssertionError: tests/perf/arc_phase_anchoring_mutations.py 的产物里这些判别器身份在当前树里找不到（判别器被删了、或它的源码文本改了 —— 产物只在这两种情况下失效，行数变了不算）：
    tests/test_phase_anchoring.py  'assert phase_anchoring.phase_ranges(_phases(3, _A3), normalize(_SRC)) == ( [(0, 5), (5, 10), (10, 15)], "")'    [同文本第 1 处]
1 failed, 489 passed
```
5. 变异驱动（原型上）：
```
== 还原核对（sha256 逐字节）==
  core/phase_anchoring.py                True  34575865a4c24c13
  core/card_draft.py                     True  b9693507e1fbb9f2
  core/quotes.py                         True  910bc4ad90a6c9a4
  core/distiller.py                      True  d2a8f50fca508497
  web/routers/distill.py                 True  9a8121a1e463857e
== 结论 ==
  全部符合预期。产物已写：tests/perf/arc_phase_anchoring_red_lines.json
real	2m37.822s
```

---

## 补充 1：PR #175 审计（2026-10-17，Claude，head 65bb4a46）

**结论：实现与 spec 一致，可以合并；补一条测试后再合。** 问题出在 spec 本身（我写的），不是实现偏离。

目标检查先跑：5 passed；真数据 996/996、谢凛羽跳过原因为「锚点位置不严格递增：949671 → 916341」；受影响测试 + 锁覆盖 490 passed；变异驱动在 PR head 上重跑「全部符合预期」，产物不变。

逐文件：

| 文件 | 结论 |
|---|---|
| core/quotes.py | 与原型逐行一致 |
| core/phase_anchoring.py | 与原型逐行一致；`locate_in_normalized` import 保留 |
| tests/test_phase_anchoring.py | 只改 test_u1 一行，与原型一致 |
| tests/perf/arc_phase_anchoring_red_lines.json | 与原型重生成结果一致；PR head 上重跑驱动后无变化 |
| tests/test_phase_anchor_exact_tier_goal.py | 与原型一致；缺一条，见下 |
| docs/specs/phase-anchor-exact-tier.md | 与交付版逐字一致 |
| docs/specs/arc-phase-anchoring.md | §9.8 末尾加一行，符合 §0 要求 |

审计方自补变异（只动 `locate_anchor`，跑目标检查）：

| 变异 | 方向 | 结果 |
|---|---|---|
| 去掉逐字档 | 过严 | 红 g1、g2 |
| 逐字档返回原文坐标、不换算 | 过严 | 红 g1、g2 |
| 含省略号也走规范化整句（替换切段） | 过宽 | 红 g3 |
| 切段下限降成 1 段 | 过宽 | 红 g3 |
| **含省略号先试规范化整句（删「…」后整串命中）** | 过宽 | **全绿，存活** |
| 不含省略号也先走逐字 | 等价 | 全绿（逐字 ⊂ 规范化，结果相同，属等价变异） |

**问题**：§3 设计决定 2「含省略号的锚点不走规范化整句档」没有任何测试守住。根因：目标检查只写了「原文没有的删减」（g3），没写「标了省略号、原文其实连着、只隔标点」这一种，而规范化整句档恰好会放过它。

**修法**：在 `tests/test_phase_anchor_exact_tier_goal.py` 末尾加 g6（`pr175-audit-g6.diff`）。已验证：PR head 上 6 passed；对上面那条存活变异，g6 变红；锁覆盖 37 passed（新文件不在变异覆盖域内）。

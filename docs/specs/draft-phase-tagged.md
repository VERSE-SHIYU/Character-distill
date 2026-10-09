# 草稿阶段号：漏写只撤回那一条（蒸馏遗留④）

> 基线：`origin/main` @ `b9b8cd2`。交付：本文件 + `tests/test_phase_tagged_goal.py`（目标检查，K0–K6 共 8 条）。
> 目标检查在 main 上 **5 红 3 绿**：K1、K2、K3、K4、K6 红；K0、K5×2 绿，分别是夹具自检和回归守卫。
> 只改 `core/card_draft.py`，与「依据写法」那一段（只改 `core/distiller.py`）互不依赖。

## 0. 结论与方案对比（返工经验第 1、3 条）

目标：模型漏写 `phase` 时，只撤回那一条，整张卡照常生成；发给模型的草稿结构一个字都不变。

| 方案 | 做法 | 能否达成目标 | 结论 |
|---|---|---|---|
| A 不修 | 维持现状（#126 当时记为「只记不修」） | 不能：漏一个键，整卡在 95% 处失败，钱白花 | 否 |
| B 在调用点兜底 | `card_from_draft` 解析前先给缺键补 0 | 能，但多出第二处阶段号规则，以后还要补第三处 | 否（打补丁，第 25 条） |
| C 把 phase 改为可选 | 字段加默认值 | 能；但发给模型的结构里 phase 会变成「可选」，模型更容易漏写 | 否 |
| **D 共用基类** | `PhaseTagged` 收拢阶段号的全部规则：缺键按 0 处理并打 warning，发给模型的结构保持必填 | **能**，而且规则只有一处 | **采用** |

这是有明确正确答案的技术题，由 Claude 定（第 29 条）。依据：规则 0 本来就是坏阶段号的统一处理处（§1），D 是让缺键也走到它。

## 1. 问题（已查实）

模型在 `occurrences` 或关系 `attitudes` 的某一条里漏写 `phase` 键时，`pydantic` 抛 `ValidationError`，整张卡失败。

- `DraftOccurrence.phase: PhaseNumber`（`core/card_draft.py:62–64`）和 `DraftAttitude.phase: PhaseNumber`（`:90–95`）都没有默认值，是必填。
- `PhaseNumber` 的 `BeforeValidator`（`:40–59`，#126 加的）只在键存在时才运行，管的是「写了但不是数字」，管不到「没写」。Claude 在沙箱实测：`DraftOccurrence(quote="x")` 抛 ValidationError。
- 坏阶段号本来有统一处理，就是规则 0 `_valid_number_rows`（`:201–215`）：`:209` 只留 `1 <= p <= count` 的编号，其余撤回并打 warning。可缺键时整卡在那之前就失败了，走不到这一步。
- #126 的 spec 当时把这一项记成「只记不修」（`docs/specs/fix-rel-batch-phase-numbering.md:80`「已知、不处理」节）。本段取代那一条。

## 2. 设计

新建基类 `PhaseTagged`，阶段号的全部规则都放在这里；`DraftOccurrence`、`DraftAttitude` 都继承它，调用点不写兜底。

1. **类型**：沿用 `PhaseNumber`，转不成整数的值按 0 处理（#126 已实现，不改）。
2. **缺键**：`phase` 默认为 0；同时用 `model_validator(mode="before")` 在缺键时打一条 warning：「缺阶段编号，按 0 处理：<原数据>」。0 交给规则 0 处理：有阶段的卡里，这一条被撤回；无阶段的卡里，0 本来就合法，照常进顶层（`dispatch`：`:266` `if count == 0 or len(final) == count:`）。
3. **发给模型的结构不变**：用 `json_schema_extra` 去掉 `default`，并让 `phase` 留在 `required` 里。目标检查 K5 要求两处 `phase` 和 main 上**完全一样**：`required == ["phase"]`，属性是 `{"title": "Phase", "type": "integer"}`。

以后加东西要改几处（第 6 条）：新增带阶段号的草稿条目时，继承 `PhaseTagged` 即可；阶段号规则只维护这一处，由 K6 守住。

## 3. 已查实的约束（基线 `b9b8cd2`）

- **结构以文本形式贴进提示词**：`core/distiller.py:1659`、`:1692`、`:2387` 都是 `json.dumps(draft_schema(), …)`；按组的路径用 `draft_schema(group)`（`:2699` 一带）。Claude 沙箱实测：原型改动后，整份结构和每个分组的结构，与改前**逐字节一致**。
- **其他构造点**：`core/card_draft.py:301` 在关系转换里写的是 `DraftOccurrence(phase=a.phase, quote=a.quote)`，显式传了 phase，不会触发「缺键」。`core/`、`web/` 里除 `card_draft.py` 外，没有别的地方用这两个类（`grep -rn "DraftOccurrence\|DraftAttitude"` 只命中本文件）。
- **影响面**：Claude 用原型跑了所有引用 `card_draft`、`card_from_draft`、`draft_schema`、`Distiller` 的测试文件，共 39 个：905 passed，deselect 了已知稳定红的那一条。
- **规模**：每条草稿条目多一次 `before` 校验，只是字典查键，没有 IO（第 11 条）。
- **环境冲突**（第 16 条）：并行的 ③ 会占用 `docker-compose.test.yml` 的测试库（55432）。本段改用自己的一次性 PG：`postgres:16-alpine`，端口 55436，容器名 `cd-pg-phase-tagged`。通过环境变量 `TEST_DATABASE_URL=postgresql://charsim:ci_test_password@localhost:55436/charsim_test` 指过去（`tests/conftest.py:31–33` 会读它），用完 `docker rm -fv`。本段不用本地应用栈。

## 4. 参考实现（Claude 沙箱原型；示意用，执行方按 §2 独立实现，不要求逐字一致）

```python
from pydantic import ..., model_validator

def _phase_stays_required(schema: dict, cls) -> None:
    """发给模型的结构不因默认值而变：phase 仍列在 required、不带 default。"""
    schema.get("properties", {}).get("phase", {}).pop("default", None)
    required = schema.setdefault("required", [])
    if "phase" not in required:
        required.insert(0, "phase")


class PhaseTagged(BaseModel):
    """带阶段号的草稿条目：阶段号的全部规则只在这里（类型、缺省、发给模型的形态）。"""
    model_config = ConfigDict(json_schema_extra=_phase_stays_required)
    phase: PhaseNumber = 0

    @model_validator(mode="before")
    @classmethod
    def _warn_missing_phase(cls, data):
        if isinstance(data, dict) and "phase" not in data:
            logger.warning("[card_draft] 缺阶段编号，按 0 处理：%r", data)
        return data


class DraftOccurrence(PhaseTagged): ...   # 去掉自己的 phase 声明
class DraftAttitude(PhaseTagged): ...     # 同上
```

另外，在 `docs/specs/fix-rel-batch-phase-numbering.md` 的「已知、不处理」节末尾加一句「（2026-10-09 已被 draft-phase-tagged.md 取代）」（第 17 条）。

## 5. 测试（第 15 条）

- 目标检查 `tests/test_phase_tagged_goal.py`，K0–K6 共 8 条，全部要绿。
- 只跑受影响的文件：目标检查、`tests/test_card_draft.py`、`tests/test_arc_phase_fields_unit.py`、`tests/test_arc_phase_unlocated*.py`、`tests/test_personality_distill_goal.py`。库用 §3 的一次性 PG，并且 `--deselect tests/test_arc_phase_fields_unit.py::test_relationship_batch_splits_and_merges`。
- 合并门是分支 CI。**执行方不跑变异**（第 14 条）。这是局部改动（第 10 条），不加额外的锁和扫描。

## 6. 对账表（复核方清单，第 14 条：执行方不跑，审计方逐条验并自补变异）

| 行为 | 守它的测试 | 应让它变红的变异 | 改后能触发的状态 |
|---|---|---|---|
| 漏写 phase 不炸整卡 | K1、K2 | 去掉默认值（`phase: PhaseNumber`） | 一条 occurrence 或 attitude 没有 phase 键 |
| 漏写的那一条被撤回 | K1、K2 | 默认值改成 1（会被误挂到阶段 1） | 有阶段的卡 |
| 漏写有 warning | K3 | 删掉 warning 那一行 | 同上 |
| 无阶段卡照常进顶层 | K4 | 缺键时抛 `ValueError`（不按 0 处理） | 卡上没有 phases |
| 发给模型的结构不变 | K5 | 删掉 `json_schema_extra`；或只删 `pop("default")` | `draft_schema()` |
| phase 只声明一处 | K6 | 在 `DraftAttitude` 里再声明一次 `phase` | — |

## 7. 执行步骤

- **S0**：先进 plan mode 报计划。逐条复核 §1、§3 的事实，再审 §6 对账表；有一条不成立就停下报告（第 18 条）。
- **S0-1**：在独立 worktree 里，以 `origin/main`（`b9b8cd2`）为基线开分支 `fix/draft-phase-tagged`。先用 `git rev-parse HEAD` 确认基线；不对就执行 `git checkout -B fix/draft-phase-tagged origin/main`。放入本文件（`docs/specs/draft-phase-tagged.md`）和目标检查，用 `test -f` 确认两个文件都在。跑目标检查，应为 **5 failed、3 passed**，不符就停下。
- **S0-2**：第一个提交只放这两个文件，提交信息 `test(draft-phase-tagged): add spec and goal check`。推送，CI 应为红。
- **S1**：按 §2、§4 实现，并改 #126 spec 的那一句。提交信息 `fix(card-draft): treat a missing phase as 0 so rule 0 retracts only that row`。
- **S2**：跑 §5 的测试，推送，等分支 CI 变绿，然后删掉一次性 PG。**不开 PR、不合并**，报告交给 Shiyu，由 Claude 审计。

## Skill

| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 确认仓库里没有现成的同类基类，有就复用 |
| S1 | `@test-driven-development` | 目标检查先红，再实现到绿 |
| S2 | `@verification-before-completion` | 测试结果现跑现贴 |

## 附 A：判断清单（第 7b 条，每条都对过读过的行）

| 判断 | 读过的行 |
|---|---|
| 两个草稿类的 phase 是必填 | `core/card_draft.py:64`、`:95`：`phase: PhaseNumber`，没有默认值 |
| BeforeValidator 只管「写了」 | `:40–59`；沙箱实测缺键时抛 ValidationError |
| 规则 0 撤回 0 和越界 | `:209`：`valid = sorted({p for p in nums if 1 <= p <= count})` |
| 无阶段卡里 0 进顶层 | `:266`：`if count == 0 or len(final) == count:` |
| 结构以文本贴进提示词 | `core/distiller.py:1659`、`:1692`、`:2387` |
| main 上 phase 的结构形态 | 沙箱实测：`{'title': 'Phase', 'type': 'integer'}`，`required == ['phase']`，两个类都是 |
| 唯一的其他构造点显式传了 phase | `core/card_draft.py:301` |
| 测试库地址可以覆盖 | `tests/conftest.py:31–33`：读 `TEST_DATABASE_URL` |
| #126 记的「只记不修」 | `docs/specs/fix-rel-batch-phase-numbering.md:80` |

## 附 B：返工经验逐条对照

| # | 本 spec 怎么做到，或为什么不适用 |
|---|---|
| 1 | §0 方案对比，每个方案都写了「能否达成目标」 |
| 2 | 目标检查先行：原型上 8/8 通过，main 上 5 红 |
| 3 | 这是技术题，有明确答案；按第 29 条由 Claude 定，理由写在 §0 |
| 4 | 不适用：用的是 pydantic 自带的 `model_validator` 和 `json_schema_extra`，不引入新库 |
| 5 | 不适用 |
| 6 | §2「以后加东西要改几处」：一处；由 K6 守住 |
| 7 / 7a / 7b | 每条代码判断都带行号；见附 A |
| 7c | 参考实现在原型上跑过；没有写未核实的想法 |
| 8 | 用法全量 grep 过（§3「其他构造点」）；结构的逐字节比对覆盖了整份结构和全部分组 |
| 9 | 只做阶段号这一件事 |
| 10 | 局部改动：只要目标检查加受影响的测试，不加快照锁、不做扫描 |
| 11 | §3「规模」 |
| 12 | 调用点：所有蒸馏路径都经过 `card_from_draft` → `CardDraft.model_validate`，K1、K2 在这个入口上测 |
| 13 | 不适用 |
| 14 | §6 是复核方清单；执行方不跑变异 |
| 15 | §5 按固定写法 |
| 16 | §3：一次性 PG 用独立端口和容器名，与 ③ 错开；不用本地应用栈 |
| 17 | 本文件放进 `docs/specs/`，用 `test -f` 确认；#126 spec 里被取代的那一条加了标注 |
| 18 | S0 逐条复核 |
| 19 | 不适用 |
| 20–24 | 审计时执行 |
| 25 / 26 | §0 否决了在调用点兜底的方案 B；规则一处定义，由 K6 守住 |
| 27 | 执行方卡住时，先把输出贴给 Claude |
| 28 | §0 每个否决都附了理由 |
| 29 | 已判断为技术题，见 §0 |
| 30 | **写和验没有完全分开**（Shiyu 10-08 定由 Claude 审计）。参考实现只作示意，由执行方独立实现；审计时 Claude 自补放宽、过严两个方向的变异 |
| 31 | 对照了今天的返工：K4 原先写的变异看不出（已更换，并实测能打红）；原先计划的整份结构快照锁违反第 10 条，已去掉，改由 K5 精确比对 phase 的形态 |

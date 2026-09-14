"""人工层：探针**无权写**的那些字段 —— 与机器层物理分文件。

病根：`evidence_writer.write_evidence` 是按 id 的**整条 upsert 覆盖**。
探针重跑会把人手填的 `assertions` / `derived` / `non_repo_paths` / `notes` 一起冲掉，
而丢的恰恰是**无法自动重建**的那部分 —— 机器测量值重跑就有，人工判断没有再跑一次的机会。

被否掉的两个方案（写在这里防回退）：

- **探针自带断言** —— 把人工判断塞进探针，探针变成半个文档生成器，职责混了
- **出口保留既有值** —— 合并语义含糊，什么时候覆盖、什么时候保留说不清

采用的不是合并策略，是**字段所有权分层**：探针产出的（机器测量值、环境、sha、结论一句话）
与人工填的（哪个数字绑哪个产物字段、哪些是算出来的、哪些路径不在库、口径落差）是两类数据，
不该在同一个 upsert 里争夺所有权。于是物理分成两个文件：

===============  ==========================  ==================================
层               文件                        谁写
===============  ==========================  ==================================
机器层           ``manifest.json``           ``evidence_writer``（探针跑出来）
人工层           ``annotations.json``        本模块（人手填）
===============  ==========================  ==================================

**两者谁都不读对方** —— 冲突不是被调和的，是结构上不存在。探针的写入范围里没有人工字段
这个概念：`write_evidence` 没有对应参数，`evidence_writer` 也没有本模块的路径。
「不在写入范围内」与「写了但被保留」的区别，就体现在这里：前者是**无权写**，后者是有权写
只是恰好没写 —— 只要有人给探针加一行，后者立刻失守。

读的一侧（锁 / 渲染）把两层按 id 合成一个视图，见 :func:`compose`。
"""
from __future__ import annotations

import json
from pathlib import Path

import evidence_writer

# 人工层的字段集合。**唯一出处** —— 锁拿它与 `evidence_writer._MACHINE_FIELDS` 求交，
# 两边不重叠才叫分层；另起一份名单就又多一个漂移点。
FIELDS = ("assertions", "derived", "non_repo_paths", "notes")


def _path() -> Path:
    """人工层落点 —— 跟着 ``evidence_writer.EVIDENCE_DIR`` 走（测试把那个搬走即可）。

    延迟取值：模块导入时算死的话，fixture 把落点搬到 tmp 之后本模块还指着真实目录。
    """
    return evidence_writer.EVIDENCE_DIR / "annotations.json"


def load() -> dict[str, dict]:
    """``{id: {四个人工字段}}``。文件不存在 → 空 —— 那时所有条目的四个人工字段都缺席。"""
    path = _path()
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write(
    evidence_id: str,
    *,
    assertions: list[dict],
    derived: list[dict],
    non_repo_paths: list[str],
    notes: str,
) -> Path:
    """覆盖某 id 的人工层。

    四个字段**无默认值、必须表态** —— 给默认值就等于给「照抄时留空」留口子：
    `assertions` 是 `claim` 与产物之间唯一的连接点，空着它 `claim` 就又变回自由文本；
    `derived` 空着等于把「这个数字是算出来的」又塞回散文里；`non_repo_paths` 空着等于把
    「这条路径不在库」又留给读者猜。没有对应内容时传空列表 / 空串 —— 那是**表态**，不是省略。
    """
    human = load()
    human[evidence_id] = {"assertions": assertions, "derived": derived,
                          "non_repo_paths": non_repo_paths, "notes": notes}
    return _write_layer(human)


def split(entries: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """:func:`compose` 的逆 —— 把合成视图拆回两层。测试用（变异矩阵按 id 改合成视图后，
    得把两层分别落盘才是真实形态）。"""
    machine = [{k: v for k, v in e.items() if k not in FIELDS} for e in entries]
    human = {e["id"]: {f: e.get(f) for f in FIELDS} for e in entries}
    return machine, human


def compose(machine: list[dict]) -> list[dict]:
    """机器层 + 人工层 → 一个视图（锁与渲染读这个，不各自拼一套）。

    人工层缺席的 id，四个字段一律落 ``None`` —— 不是「继承上一版」也不是「静默省略」：
    `verified` 条目因此当场缺 `assertions`（锁红），逼作者表态。
    """
    human = load()
    return [{**e, **{f: (human.get(e["id"]) or {}).get(f) for f in FIELDS}} for e in machine]


def _write_layer(human: dict[str, dict]) -> Path:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(human, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path

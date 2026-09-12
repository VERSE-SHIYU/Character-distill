"""由清单渲染简历口径 —— 产 `docs/evidence/resume-numbers.md`。

**为什么要有这一步**：`manifest.json` 是证据的唯一真源，但它是给机器读的（字段多、有
`notes` 长文）。简历/面试要的是「哪些数字能写、怎么一句话复现」。若那份清单是手工维护的
第二份表，它立刻就开始漂移 —— 渲染的意义就在于：**改清单，重跑本脚本，口径跟着变**。

用法::

    python tests/perf/render_evidence.py

输出不含生成日期（日期由 git 记），这样「渲染结果是否仍与清单一致」是可判定的：
`tests/test_evidence_integrity.py` 会重渲染一遍与盘上文件逐字节比对，防止清单改了而表没重生成。
"""
from __future__ import annotations

import json

from evidence_writer import EVIDENCE_DIR, MANIFEST

OUT = EVIDENCE_DIR / "resume-numbers.md"

_WRITABLE = "verified"
_UNWRITABLE_REASON = {
    "runtime-measured": "运行期实测：环境还在，但产物没有入库 —— 按 `notes` 里的扫描方法自行重做",
    "unverifiable": "当时结论、现已不可复现：环境或快照已不存在 —— 不得当现状引用",
}


def _cell(text: object, limit: int = 140) -> str:
    """表格单元里的文本：转义竖线、压平换行、超长截断（全文在清单里）。"""
    s = str(text or "").replace("|", "\\|").replace("\n", " ").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def render(entries: list[dict]) -> str:
    writable = [e for e in entries if e.get("status") == _WRITABLE]
    unwritable = [e for e in entries if e.get("status") != _WRITABLE]

    out = [
        "# 简历可写数字 — 由证据清单渲染",
        "",
        "> **本文件是渲染产物，勿手改。** 改动一律改 `docs/evidence/manifest.json`，再重跑：",
        "> `python tests/perf/render_evidence.py`。",
        ">",
        "> 判据：只有 `status = verified`（产物在仓库、脚本可重跑）的数字可以写进简历。",
        "> 数字的**口径**（聚合方式、样本数、字段名对照）认清单的 `notes` 与 `docs/evidence/` 下的",
        "> 叙述档 —— 本表是索引，不是第二份真源。",
        "",
        f"## 一、可写（`verified`，{len(writable)} 条）",
        "",
        "| 数字 / 结论 | 证据 | 一句话复现口径 |",
        "|---|---|---|",
    ]
    for e in writable:
        # corroborating 要显眼：跑那个脚本**不会**重生成产物，只覆盖同一断言
        tag = "（**只佐证**，非产出脚本）" if e.get("script_role") == "corroborating" else ""
        out.append(
            f"| {_cell(e.get('claim'))} | `ev:{e['id']}` | `{_cell(e.get('reproduce'))}` "
            f"（脚本 `{_cell(e.get('script'), 80)}`{tag}） |")

    out += [
        "",
        f"## 二、不可写（`runtime-measured` / `unverifiable`，{len(unwritable)} 条）",
        "",
        "| 结论 | 证据 | 为什么不可写 |",
        "|---|---|---|",
    ]
    for e in unwritable:
        why = _UNWRITABLE_REASON.get(e.get("status"), e.get("status"))
        if e.get("notes"):
            why += f"。{_cell(e.get('notes'))}"
        out.append(f"| {_cell(e.get('claim'))} | `ev:{e['id']}` | {why} |")

    out += [
        "",
        f"## 三、出处索引（全部 {len(entries)} 条）",
        "",
        "| id | status | 产物 | 脚本 | 脚本角色 | 产出 commit | 测量日 |",
        "|---|---|---|---|---|---|---|",
    ]
    for e in entries:
        out.append(
            f"| `{e['id']}` | `{e.get('status')}` | {_cell(e.get('artifact') or '—', 60)} | "
            f"{_cell(e.get('script') or '—', 60)} | {_cell(e.get('script_role') or '—', 20)} | "
            f"`{_cell(e.get('code_sha') or '—', 30)}` | "
            f"{_cell(e.get('measured_at') or '—', 20)} |")

    return "\n".join(out) + "\n"


def main() -> None:
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    OUT.write_text(render(entries), encoding="utf-8")
    # ASCII 输出：Windows 控制台默认 GBK，中文会打成乱码（本脚本是要给人手跑的）
    print(f"wrote {OUT.name} ({len(entries)} entries)")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""台账里不得留**未被替换的**替换标记 —— 判**栖息地**，不判形状。

**它防的是什么。** `AGENTS.md` 缺陷 46 的正文里长期留着一处 `@@FIX_SHA@@`：条目是照模板
写的，替换那一步从没发生，而**没有任何东西会因此报错** —— 台账照样读得下去、CI 照样绿。
这与缺陷 53（`.in` 里声称的 CI 步骤不存在）、§四 那条凭据前缀（文本说了一件从没发生过的
事）是同一族：**文本与事实之间没有闭合**。补掉那一处是补丁；这条锁要的是「同一形态不可能
再进来」。

**为什么判栖息地而不是判形状（四种划法各自被实测否掉）。** 按形状划不出来 ——
`@@FIX_SHA@@`（空）与语料里的 `@@SPLIT@@`（分片标记）**逐字同形**（`@@` + 大写标签 + `@@`），
差别只在用途；任何形状规则都得点名豁免 `@@SPLIT@@`，那就是第二份手工清单。反过来放宽到
`<…>` 会红在**46 条正确的行**上：本仓的 `<sha>` / `${VAR}` 是「命令骨架里的元变量」与
shell/env 模板，是**领域用法**（`git show <sha>`、`--constraint <上一把锁>`、
`${POSTGRES_USER}`）。按「凡 sha 形状必须解得开 commit」也不行：`@@FIX_SHA@@` 根本不是 sha
形状 —— 「写了个假 sha」与「留了个空」是两件事。

可判定的那条是**栖息地**：替换标记的语义是「等着被替换」，而**台账里没有替换者** —— 留在
里面就必然是空。于是判据分两半：

  - **台账文档**里标记数必须为 0。台账的范围**从结构推出**（正文含 `—— 状态：` 的文件），
    不按文件名枚举 —— 新增台账文件只要沿用这条结构就自动进判据面。
  - **台账之外**的标记必须落在**数据栖息地**里：所在文件能解析成 JSON，且每一处标记都在
    某个**字符串值**内（`@@SPLIT@@` 是被语料加载器消费的内容，不是等着人填的空）。
    其余一律红 —— 冒出一个新栖息地时**逼人判「这是数据，还是又一个空」**。

**天花板（明写，别当全覆盖）。** 本锁只认 `@@…@@` 这一种标记形状，因为它是**普查出来的**
（2026-09-17 现数：台账 1 处 = 空、语料 8 处 = 数据），不是我列的一张「常见占位符」清单。
**新造一种空形态（`TODO_SHA` / `<sha>` / `TBD`）本锁不认** —— 纳入 `<…>` 的代价见上（46 条
正确的行）。留白是刻意的：把它误当成「覆盖了所有占位」才是真隐患。同理，「JSON 的字符串值」
这个数据栖息地也是结构判定 —— 新写一个 JSON 配置文件、里面放一处标记，本锁会放行；
那一刻它确实不是台账里的空，但它也没人消费。两处天花板都记在这里，改这把锁的人先读这段。

**为什么单独一个文件，不并进 `tests/test_evidence_integrity.py`。** 那条锁的命题是
`docs/evidence/manifest.json` 的**字段**（`code_sha` 能不能解开 commit、`reproduce` 指的
路径在不在、非仓库路径有没有双向声明），它提到 AGENTS.md 只是为了确认那个路径在 `docs`
集合里。两者没有交集，合进去会让一条锁的失败信息同时指向两个不相干的命题 —— 而本条的失败
信息要说清「哪个文件、哪一行、这是空还是数据、该怎么办」。
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 形状本身不是判据（见 docstring），这里只是**要排查的东西**：把候选捞出来，再由栖息地定性质。
MARKER = re.compile(r"@@[^@\n]{0,40}@@")

# 台账的**结构**签名：正文含它 = 这份文档按台账的模板写（条目 = 标题行 + 「—— 状态：**…**」）。
LEDGER_SIGNATURE = "—— 状态："

_HOWTO = (
    "处置：台账里这一处要求的是**事实**（sha / 数字 / 路径），把值补上；若它本来就不是等着"
    "被替换的，换一种写法 —— `@@…@@` 在本仓的语义就是「等着被替换」，而台账里没有替换者。"
)


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode not in (0, 1):      # 1 = 无命中，不是错误
        raise AssertionError(f"git {' '.join(args)} 失败（exit={r.returncode}）：{r.stderr}")
    return r.stdout


def _expand(rows) -> list[tuple[str, int, str, str]]:
    """`[(相对路径, 行号, 整行)]` → 逐处命中 —— **一行可以含多处**，按行数会漏。"""
    return [(rel, n, line, m.group(0)) for rel, n, line in rows for m in MARKER.finditer(line)]


def _tracked_hits() -> list[tuple[str, int, str, str]]:
    """全仓**跟踪**文本文件里的标记命中。

    走 `git grep`（`-I` 跳过二进制）而不是遍历工作树：一次拿全，且只扫入库的东西 ——
    `.venv` / `node_modules` / 散落的 scratch 不是台账，也不该进任何普查。
    """
    out = _git("grep", "-n", "-I", "-E", MARKER.pattern)
    rows = []
    for ln in out.splitlines():
        rel, _, rest = ln.partition(":")
        lineno, _, content = rest.partition(":")
        if lineno.isdigit():
            rows.append((rel, int(lineno), content))
    return _expand(rows)


def _ledger_files() -> list[str]:
    """按**结构**认台账：正文含 `LEDGER_SIGNATURE` 的跟踪文件。"""
    return sorted(ln for ln in _git("grep", "-l", "-I", "-F", LEDGER_SIGNATURE).splitlines()
                  if ln.strip())


def _json_string_values(path: pathlib.Path) -> list[str] | None:
    """JSON 文件里所有**字符串值**的清单；解析不了回 `None`。

    只走值、不走键 —— 键是**说话的人起的名字**，值是**被消费的内容**；语料分片标记在值里。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    found: list[str] = []

    def walk(node) -> None:
        if isinstance(node, str):
            found.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return found


def _offenders(hits, root: pathlib.Path = ROOT) -> list[tuple[str, int, str, str]]:
    """命中 → 违规清单 `[(相对路径, 行号, token, 判定)]`。

    判定只有两种，因为下一步动作只有两种：台账里的（补值）、判不了性质的（来人看一眼）。
    """
    out = []
    for rel, lineno, _line, token in hits:
        path = pathlib.Path(root) / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            out.append((rel, lineno, token, "读不出文本 —— 判不了这是数据还是空"))
            continue
        if LEDGER_SIGNATURE in text:
            out.append((rel, lineno, token, "**空**：台账里没有替换者"))
            continue
        values = _json_string_values(path)
        if values is None:
            out.append((rel, lineno, token, "不是 JSON 文件 —— 判不了这是数据还是空"))
        elif not any(token in value for value in values):
            out.append((rel, lineno, token, "没有落在任何 JSON 字符串值里 —— 判不了这是数据还是空"))
    return out


def _report(bad) -> str:
    return "\n".join(f"  {rel}:{lineno}  `{token}`  {why}" for rel, lineno, token, why in sorted(bad))


# ── 自证：普查本身得是活的 ────────────────────────────────────────────────────

def test_the_ledger_signature_finds_the_ledger():
    """签名扫不到文件时，下面那条判据在**空集**上恒绿 —— 恒绿的锁与没有锁是一回事（§四）。

    这条同时是「台账范围从结构来」的守卫：签名写错了（或台账换了模板）、或者有人把台账
    改到不再匹配签名，都在这里当场红，而不是让下面那条判据悄悄失效。
    """
    files = _ledger_files()
    assert files, (
        f"全仓没有任何跟踪文件含台账签名 {LEDGER_SIGNATURE!r} —— 下一条判据是在空集上判绿。"
        "要么台账改了模板（改 LEDGER_SIGNATURE），要么它被删了。")


# ── 命题一：台账里不得留未被替换的标记 ──────────────────────────────────────

def test_no_unfilled_substitution_markers_in_the_ledger():
    """台账文档里 `@@…@@` 命中数必须为 0。

    不是「像不像占位符」—— 是**这个形状在本仓的语义**＝等着被替换，而台账里没有替换者。
    """
    ledgers = set(_ledger_files())
    bad = [o for o in _offenders(_tracked_hits()) if o[0] in ledgers]
    assert not bad, (
        "台账里留着**未被替换的**替换标记（模板写下去之后替换那一步没发生，"
        "而没有任何东西会因此报错）：\n" + _report(bad) + f"\n{_HOWTO}\n"
        f"（台账文档 = 正文含 {LEDGER_SIGNATURE!r} 的文件，现数：{sorted(ledgers)}）")


# ── 命题二：台账之外的标记必须在数据栖息地里 ────────────────────────────────

def test_markers_outside_the_ledger_are_declared_data():
    """台账之外的标记必须落在**数据栖息地**：JSON 文件、且在某字符串值内。

    语料里的 `@@SPLIT@@` 是分片标记 —— 它**不是空**，是被加载器消费的内容；判据不点名它，
    也不给它开豁免：它靠**性质**通过（在 JSON 的值里）。换个地方出现同一个词就红 ——
    这正是「判的是栖息地，不是形状」的可判定之处。
    """
    ledgers = set(_ledger_files())
    bad = [o for o in _offenders(_tracked_hits()) if o[0] not in ledgers]
    assert not bad, (
        "这些文件里出现了替换标记，但它们既不在台账、也不在数据栖息地"
        "（不是 JSON，或标记不在任何字符串值里）—— 判不了是数据还是又一个空，来人看一眼：\n"
        + _report(bad) +
        "\n处置：确认是数据的，让它落在 JSON 的字符串值里（语料分片标记就是这个形态）；"
        "是空就补上；两者都不是，别用 `@@…@@` 这个形状。")


# ── 分类器两个方向都是承重的（正控 + 负控，合成输入，不碰仓内）──────────────

_SYNTH_LEDGER = "# 台账\n\n**1. 某事** —— 状态：**已修（`@@FIX_SHA@@`）**\n"
_SYNTH_LEDGER_CLEAN = "# 台账\n\n**1. 某事** —— 状态：**已修（`170d49a`）**\n"
_SYNTH_DATA = '{"payload": "前半@@SPLIT@@后半"}\n'
_SYNTH_JSON_KEY = '{"@@SPLIT@@": "值在这里，标记在键上"}\n'
_SYNTH_SCRIPT = "CMD = 'git show @@FIX_SHA@@'\n"


def test_the_classifier_is_both_load_bearing(tmp_path):
    """两半判据各一正一负，外加一条「键 ≠ 值」的边界 —— 只控一侧会让某半恒绿/恒红。

    - 台账里留空 ⇒ **红**（这就是缺陷 46 的形态；补上值 ⇒ 绿）。
    - JSON 的**值**里出现标记 ⇒ 绿（语料分片标记的形态，**这条是负控**：证明判的不是
      「`@@` 形状」而是「台账里的 `@@`」—— 否则本锁只是恒红）。
    - 非 JSON 的文件里出现标记 ⇒ 红（新栖息地，逼人判性质）。
    - JSON 的**键**上出现标记 ⇒ 红（键是名字、值是内容；分片标记不会长在键上）。
    """
    def write(name: str, text: str) -> str:
        (tmp_path / name).write_text(text, encoding="utf-8")
        return name

    def offenders_of(name: str):
        text = (tmp_path / name).read_text(encoding="utf-8")
        rows = [(name, n, line) for n, line in enumerate(text.splitlines(), 1)]
        return _offenders(_expand(rows), tmp_path)

    ledger = write("ledger.md", _SYNTH_LEDGER)
    assert [o[3] for o in offenders_of(ledger)] == ["**空**：台账里没有替换者"]

    clean = write("ledger_clean.md", _SYNTH_LEDGER_CLEAN)
    assert offenders_of(clean) == []                    # 正控：补上值就没有违规

    data = write("data.json", _SYNTH_DATA)
    assert offenders_of(data) == []                     # 负控：数据栖息地放行

    key = write("key.json", _SYNTH_JSON_KEY)
    assert [o[3] for o in offenders_of(key)] == ["没有落在任何 JSON 字符串值里 —— 判不了这是数据还是空"]

    script = write("script.py", _SYNTH_SCRIPT)
    assert [o[3] for o in offenders_of(script)] == ["不是 JSON 文件 —— 判不了这是数据还是空"]

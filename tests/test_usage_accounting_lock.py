# -*- coding: utf-8 -*-
"""形态锁：任何 LLM 调用点都必须流进唯一记账出口（缺陷 22）。

病根：拿到 LLM 响应却不记账。`async_chat` 返回 `(result, usage)`，调用方写成
`result, _ = await ...` 把 usage 就地丢掉；`chat` / `chat_stream` / `chat_with_tools`
走 `last_usage`，同样可以「调用完就忘」。Map 是 MapReduce 里最烧 token 的一段，
整段曾无账 —— 而且失败重试墙空烧的 token 也从来没人记。

判据从事实推出，**不维护调用点清单** —— 藏在守卫与被守对象之间的第二份手工清单
本身就是漂移点（缺陷 21 的豁免名单、缺陷 25 的命名代理，同一谱系）。三件事实：
  1. LLM 入口方法 = adapters/llm_adapter.py 内传递闭包到 `chat.completions.create` 的方法
  2. 记账出口     = 函数体内传递闭包到 `.record_usage(` 的可调用点（解 import，跨文件）
  3. LLM 接收者   = 表达式末段名 == "llm" 或 endswith("_llm")

两种调用形态都算调用点：
  - 直接调用 `llm.chat(...)` —— Attribute 是某个 Call 的 func
  - 方法被当值搬走 `asyncio.to_thread(llm.chat, ...)` —— AST 上没有 Call 节点，
    只查 Call 形态会整类漏掉（普查第一版就漏了 3 处生产代码，故这里两条都查）

不变量的自检（防「锁在假绿」）：
  - 入口方法集非空，且逐个是 LLMAdapter 的真实属性（AST 推导与运行对象对上）
  - 记账出口的定义**恰好一处**且落在 core/utils.py —— 出口分散即红（缺陷 16 的形态）。

扫描范围 = 应用代码（core / web / adapters / storage / mcp_server）。
scripts/ 与 tests/ 是开发/评测工具、不在生产路径，不归本锁红绿。

变异（自测见本文件末尾两个 test_mutation_*）：
  - 删掉 `_run_map_concurrent` 里那把 `distill_map` 落账 → 直接调用形态的那处 map 分片红
  - 删掉 market 里 `at_reply` 的落账 → 「方法当值搬走」形态的那处红
"""
from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_DIRS = ("core", "web", "adapters", "storage", "mcp_server")
ADAPTER_REL = "adapters/llm_adapter.py"
EXIT_REL = "core/utils.py"


# ── 源码装配 ──────────────────────────────────────────────────────────

def _sources(overrides: dict[str, str] | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for d in SRC_DIRS:
        base = REPO_ROOT / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            rel = p.relative_to(REPO_ROOT).as_posix()
            out[rel] = p.read_text(encoding="utf-8")
    if overrides:
        out.update(overrides)
    return out


def _module_rel(mod: str) -> str | None:
    for cand in (REPO_ROOT / (mod.replace(".", "/") + ".py"),
                 REPO_ROOT / mod.replace(".", "/") / "__init__.py"):
        if cand.is_file():
            return cand.relative_to(REPO_ROOT).as_posix()
    return None


# ── AST 小工具 ────────────────────────────────────────────────────────

def _parents(tree: ast.Module) -> dict[int, ast.AST]:
    par: dict[int, ast.AST] = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            par[id(c)] = n
    return par


def _own(node: ast.AST):
    """node 自身词法体的节点 —— 不下潜进嵌套函数/类，那些由词法链单独覆盖。

    不下潜是关键：否则外层函数会因为「里面定义了某个记账的嵌套函数」而冒充记账，
    嵌套函数没被调用也照样算，是假绿。
    """
    stack = list(getattr(node, "body", []))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(n))


def _call_leafs(node: ast.AST) -> tuple[set[str], set[str]]:
    """node 词法体内的被调用名，分成（裸名, 属性末段）两桶。

    分开是因为解析范围不同：裸名是直接 import 进来的（精确到那个函数）；
    属性末段是「某个对象上的方法」，对象的类型静态不可知，只能在本文件 import
    的模块里找同名方法（见 :meth:`_Index.resolve_attr`）。
    """
    names: set[str] = set()
    attrs: set[str] = set()
    for n in _own(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                attrs.add(f.attr)
            elif isinstance(f, ast.Name):
                names.add(f.id)
    return names, attrs


def _has_record_usage_call(node: ast.AST) -> bool:
    """node 内（含嵌套闭包）是否出现 `.record_usage(` —— 出口的判据。

    这里**要**下潜：`core/utils.py` 的出口把真正的 DB 写放在一个嵌套协程里，自己
    只负责把它交给投递原语（以前是把闭包交给线程，形态不同、道理一样）。不下潜就
    找不到那个出口，出口集会变成空集，谁都不算记账 —— 全仓站点集体假红。
    """
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "record_usage"
        for n in ast.walk(node)
    )


def _hosts_llm_call(node: ast.AST) -> bool:
    """node 自身（含嵌套闭包）是否调用了 LLM —— 「消费者」标记。

    消费者不得把记账身份传给调用方：`_should_stay_silent` 自己记账，`chat` 调它，
    若允许传播则 `chat` 凭空算记账，摘掉 `chat` 里真正的落账行也不红。
    """
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and _receiver_is_llm(n.func.value)
        for n in ast.walk(node)
    )


def _receiver_is_llm(value: ast.AST) -> bool:
    if isinstance(value, ast.Attribute):
        return value.attr == "llm" or value.attr.endswith("_llm")
    if isinstance(value, ast.Name):
        return value.id == "llm" or value.id.endswith("_llm")
    return False


# ── 索引与事实推导 ────────────────────────────────────────────────────

class _Index:
    def __init__(self, sources: dict[str, str]) -> None:
        self.trees: dict[str, ast.Module] = {}
        self.par: dict[str, dict[int, ast.AST]] = {}
        self.defs: dict[str, dict[str, list[ast.AST]]] = {}
        self.host: dict[str, dict[int, ast.AST | None]] = {}
        self.imports: dict[str, dict[str, str]] = {}
        self.nested: dict[str, dict[int, list[ast.AST]]] = {}
        self.mentions: dict[str, dict[int, set[str]]] = {}
        self.parse_errors: dict[str, str] = {}
        for rel, src in sources.items():
            try:
                tree = ast.parse(src, filename=rel)
            except SyntaxError as exc:
                # 不静默跳过：解析失败的文件其调用点会整批「消失」，锁会假绿。
                self.parse_errors[rel] = f"{type(exc).__name__}: {exc}"
                continue
            self.trees[rel] = tree
            par = _parents(tree)
            d: dict[str, list[ast.AST]] = {}
            host: dict[int, ast.AST | None] = {}
            nested: dict[int, list[ast.AST]] = {}
            mentions: dict[int, set[str]] = {}
            for n in ast.walk(tree):
                if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                d.setdefault(n.name, []).append(n)
                host[id(n)] = self._nearest_function(par, n)
                nested[id(n)] = []
                mentions[id(n)] = {x.id for x in _own(n) if isinstance(x, ast.Name)}
            for n in list(ast.walk(tree)):
                if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                h = host[id(n)]
                if h is not None:
                    nested[id(h)].append(n)
            self.par[rel] = par
            self.defs[rel] = d
            self.host[rel] = host
            self.imports[rel] = self._imports(tree)
            self.nested[rel] = nested
            self.mentions[rel] = mentions

    @staticmethod
    def _nearest_function(par: dict[int, ast.AST], node: ast.AST) -> ast.AST | None:
        """node 最近的外层函数（跳过类/语句节点）。"""
        cur = par.get(id(node))
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cur
            cur = par.get(id(cur))
        return None

    @staticmethod
    def _imports(tree: ast.Module) -> dict[str, str]:
        """本地名 -> 定义所在文件（够本锁用的一跳解析）。"""
        out: dict[str, str] = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module:
                tgt = _module_rel(n.module)
                if tgt:
                    for a in n.names:
                        out[a.asname or a.name] = tgt
            elif isinstance(n, ast.Import):
                for a in n.names:
                    tgt = _module_rel(a.name)
                    if tgt:
                        out[(a.asname or a.name).split(".")[0]] = tgt
        return out

    def _ancestors(self, rel: str, node: ast.AST) -> set[int]:
        out: set[int] = set()
        cur: ast.AST | None = node
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.add(id(cur))
            cur = self.par.get(rel, {}).get(id(cur))
        return out

    def resolve(self, leaf: str, rel: str, node: ast.AST) -> list[ast.AST]:
        """裸名 leaf 在 `node` 处指向的定义 —— 按 Python 的**词法作用域**解析。

        同名嵌套函数在多处出现时必须按词法可见性择一：distiller 里有三个
        `_one`，reduce 路径那个是记账的、map 路径那个不是。按名字取同文件全体会
        让 map 的 `_one` 冒充成 reduce 的 `_one`，变异就假绿了。

        可见集 = 模块级定义 + `node` 的任一外层函数（含自身）内嵌的定义；
        本地可见集为空才回退到 import 目标。
        """
        anc = self._ancestors(rel, node)
        local = [c for c in self.defs.get(rel, {}).get(leaf, [])
                 if self.host.get(rel, {}).get(id(c)) is None
                 or id(self.host[rel][id(c)]) in anc]
        if local:
            return local
        tgt = self.imports.get(rel, {}).get(leaf)
        return list(self.defs.get(tgt, {}).get(leaf, [])) if tgt else []

    def resolve_attr(self, leaf: str, rel: str) -> list[ast.AST]:
        """属性末段 leaf（`X.method`）可能指向的定义（同文件 + 本文件 import 的模块）。

        对象的类型静态不可知（`engine` 来自 sessions 字典），只能把候选限定在
        **本文件 import 到的模块**里的同名方法 —— 比全仓并集窄得多。
        ceiling（有意）：靠「方法名恰好同形」仍可能误判（如别的文件里也有个叫
        `send` 的记账方法）。真正消除要靠数据流推 receiver 类型，收益不抵成本；
        现有的记账方法名（`try_record_usage` / `_record_usage` / `_try_record_usage`）
        足够特异性，误判概率可忽略。
        """
        out = list(self.defs.get(rel, {}).get(leaf, []))
        for tgt in set(self.imports.get(rel, {}).values()):
            out.extend(self.defs.get(tgt, {}).get(leaf, []))
        return out

    def lexical_chain(self, rel: str, lineno: int) -> list[ast.AST]:
        chain = [n for n in ast.walk(self.trees[rel])
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.lineno <= lineno <= (n.end_lineno or n.lineno)]
        return sorted(chain, key=lambda f: f.lineno)

    def _calls_into(self, node: ast.AST, rel: str, targets: set[int]) -> int:
        """node **自身词法体**里，调用目标落在 targets 的次数。

        显式挡掉 LLM 调用本身：`self.llm.chat(...)` 的属性末段 `chat` 会和本文件同名
        方法（`ChatEngine.chat`）撞名，于是「调用了 LLM」被误读成「调用了记账函数」——
        摘掉真正的落账行也照样绿，这是上一版最大的假绿来源。
        """
        hits = 0
        for c in _own(node):
            if not isinstance(c, ast.Call):
                continue
            f = c.func
            if isinstance(f, ast.Attribute):
                if _receiver_is_llm(f.value):
                    continue
                cand = self.resolve_attr(f.attr, rel)
            elif isinstance(f, ast.Name):
                cand = self.resolve(f.id, rel, node)
            else:
                continue
            if any(id(t) in targets for t in cand):
                hits += 1
        return hits

    def _reaches(self, node: ast.AST, rel: str, acct: set[int]) -> bool:
        """node 是否流向记账出口。两条路径，都从 node **自身词法体**判定：
          (a) 真调用某个记账函数；
          (b) 把嵌套的记账函数**当值**交出去（交给线程/投递原语那一类调用 ——
              被搬走的那一端不会以 `Call` 节点的样子出现，只看调用会漏；但要求该嵌套
              函数的**名字在 node 自身体里出现过**，否则「定义了却从不使用」的嵌套函数
              会冒充记账）。
        """
        if self._calls_into(node, rel, acct):
            return True
        mentioned = self.mentions.get(rel, {}).get(id(node), set())
        for nf in self.nested.get(rel, {}).get(id(node), []):
            if id(nf) in acct and nf.name in mentioned:
                return True
        return False

    def accounting(self) -> tuple[set[int], list[tuple[str, ast.AST]]]:
        """返回（记账路径节点 id 集，直接含 .record_usage( 的定义）。

        路径集从出口向外闭包，但**消费者不传播**（``_hosts_llm_call``）：一个自身调用
        LLM 的函数是 usage 的消费者，不是记账管道的一部分，不能把自己的记账身份借给
        调用方。否则 `chat` 调了 `_should_stay_silent`（它自身记账）就被判成记账，
        摘掉 `chat` 里真正的落账行也不红 —— 上一版的假绿来源之一。

        闭包层数由结构自然决定，不写死数字：最内层是裸 `.record_usage(` 的那层（现在
        落在 `core/utils.py` 出口函数的嵌套协程里），往外逐层是包着它的记账函数，
        再往外只有消费者。
        """
        direct = [(rel, n) for rel, d in self.defs.items()
                  for nodes in d.values() for n in nodes if _has_record_usage_call(n)]
        path = {id(n) for _, n in direct}
        changed = True
        while changed:
            changed = False
            for rel, d in self.defs.items():
                for nodes in d.values():
                    for n in nodes:
                        if id(n) in path or _hosts_llm_call(n):
                            continue
                        if self._reaches(n, rel, path):
                            path.add(id(n))
                            changed = True
        return path, direct


def _llm_entry_methods(index: _Index) -> set[str]:
    """适配器内传递闭包到 chat.completions.create 的方法名。"""
    methods = [n for n in ast.walk(index.trees[ADAPTER_REL])
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def calls_create(node: ast.AST) -> bool:
        for n in ast.walk(node):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "create":
                v = n.func.value
                if isinstance(v, ast.Attribute) and v.attr == "completions":
                    return True
        return False

    def calls_method(node: ast.AST, name: str) -> bool:
        return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == name for n in ast.walk(node))

    direct = {n.name for n in methods if calls_create(n)}
    changed = True
    while changed:
        changed = False
        for n in methods:
            if n.name not in direct and any(calls_method(n, d) for d in direct):
                direct.add(n.name)
                changed = True
    return direct


def _llm_sites(index: _Index, entries: set[str]) -> list[tuple[str, int, str, str, str, ast.AST]]:
    """[(rel, lineno, 所在函数, 方法名, 形态, 调用节点)]，形态 ∈ {call, value}。"""
    sites = []
    for rel, tree in index.trees.items():
        called = {id(c.func) for c in ast.walk(tree)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
        for n in ast.walk(tree):
            if not isinstance(n, ast.Attribute) or n.attr not in entries:
                continue
            if not _receiver_is_llm(n.value):
                continue
            chain = index.lexical_chain(rel, n.lineno)
            sites.append((rel, n.lineno, chain[-1].name if chain else "<module>",
                          n.attr, "call" if id(n) in called else "value", n))
    return sites


def _if_arms(index: _Index, rel: str, node: ast.AST) -> dict[int, int]:
    """node 到根，每个 If 祖先里的分支号（0=body, 1=orelse）。"""
    out: dict[int, int] = {}
    child = node
    cur = index.par.get(rel, {}).get(id(node))
    while cur is not None:
        if isinstance(cur, ast.If):
            if any(child is b for b in cur.body):
                out[id(cur)] = 0
            elif any(child is b for b in cur.orelse):
                out[id(cur)] = 1
        child = cur
        cur = index.par.get(rel, {}).get(id(cur))
    return out


def _exclusive_groups(index: _Index, rel: str, nodes: list[ast.AST]) -> int:
    """互斥分支里的调用点合并成一组：if/else 两支只跑一支，共用一条落账。

    只合并**同一个 If 的不同分支**。try/except 不合并 —— try 体与 except 体的调用
    可能先后各跑一次（先失败、再重试），各自需要落账。
    """
    parent = list(range(len(nodes)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    arms = [_if_arms(index, rel, n) for n in nodes]
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            if any(arms[i][k] != arms[j][k] for k in set(arms[i]) & set(arms[j])):
                a, b = find(i), find(j)
                if a != b:
                    parent[a] = b
    return len({find(i) for i in range(len(nodes))})


def _report(dropped: list[tuple[str, int, str, str, str, ast.AST]]) -> str:
    how = {"call": "直接调用", "value": "方法被当值搬走（如 asyncio.to_thread）"}
    lines = ["以下 LLM 调用点没有流向记账出口（core/utils.py 的 try_record_usage）——",
             "拿到响应却不落账，成本统计会漏这一段："]
    lines += [f"  {s[0]}:{s[1]}  {s[2]}() 里的 .{s[3]}()（{how[s[4]]}）"
              for s in dropped]
    lines.append("修法：紧跟调用后调 try_record_usage(...)；失败分片也要记"
                 "（按 prompt 字符估算，标 estimated）—— 只记成功会让统计系统性偏低。")
    return "\n".join(lines)


_BASE: _Index | None = None


def _base_index() -> _Index:
    """基准索引只建一次 —— 全仓 AST 解析约 7 秒，4 个用例各来一遍纯浪费。

    源码在本进程内不会被改写（变异走 ``overrides``，不落盘），缓存无失效风险。
    """
    global _BASE
    if _BASE is None:
        _BASE = _Index(_sources())
    return _BASE


def _audit(overrides: dict[str, str] | None = None):
    index = _Index(_sources(overrides)) if overrides else _base_index()
    entries = _llm_entry_methods(index)
    path, direct = index.accounting()
    sites = _llm_sites(index, entries)
    dropped = []
    for s in sites:
        rel, ln = s[0], s[1]
        chain = index.lexical_chain(rel, ln)
        if not any(index._reaches(f, rel, path) for f in chain):
            dropped.append(s)
            continue
        # 同一函数里两处调用、两条落账：只摘一条时「函数仍记账」看不出缺口。
        # 按词法链配平 —— 链上的落账调用数必须 ≥ 该链承载的调用点数（互斥分支
        # 合并计一组），少一条就说明有一个调用点没人接。
        chain_ids = {id(f) for f in chain}
        records = sum(index._calls_into(f, rel, path) for f in chain)
        by_rel: dict[str, list[ast.AST]] = {}
        for t in sites:
            if id(index.lexical_chain(t[0], t[1])[-1]) in chain_ids:
                by_rel.setdefault(t[0], []).append(t[5])
        need = sum(_exclusive_groups(index, r, nodes) for r, nodes in by_rel.items())
        if records < need:
            dropped.append(s)
    return entries, direct, dropped, index.parse_errors


# ── 用例 ──────────────────────────────────────────────────────────────

def test_every_llm_call_site_reaches_the_accounting_exit():
    """主判据：全仓每个 LLM 调用点都流向记账出口。"""
    entries, _, dropped, parse_errors = _audit()
    assert entries, "LLM 入口方法推导为空 —— 适配器结构变了，锁在假绿"
    assert not parse_errors, (
        f"这些文件解析失败，其调用点被整批跳过 —— 锁会假绿：{parse_errors}"
    )
    assert not dropped, _report(dropped)


def test_entry_methods_are_real_adapter_attributes():
    """AST 推出的入口方法必须是 LLMAdapter 的真实属性 —— 防推导跑偏后假绿。"""
    entries, *_ = _audit()
    from adapters.llm_adapter import LLMAdapter
    missing = sorted(m for m in entries if not hasattr(LLMAdapter, m))
    assert not missing, (
        f"AST 推出这些「LLM 入口方法」但 LLMAdapter 上没有：{missing} —— 推导有误，"
        "锁可能正在瞎扫"
    )


def test_single_accounting_exit():
    """出口唯一且落在 core/utils —— 多一个出口就是又一处要记得喂的地方（缺陷 16 形态）。"""
    _, direct, *_ = _audit()
    rels = sorted({rel for rel, _ in direct})
    assert rels == [EXIT_REL], (
        f"记账出口的定义处应为 {EXIT_REL} 一处，实际：{rels}。"
        "新建出口 = 出口分散复发；请把新路径接进既有出口。"
    )


def _mutate(rel: str, anchor: str, replacement: str) -> str:
    """把 anchor 换成**仍可解析**的 replacement —— 直接删行可能让 if/try 体空掉、
    语法错误，那样文件会被跳过、站点整个消失，变异会假绿。"""
    src = (REPO_ROOT / rel).read_text(encoding="utf-8")
    mutated = src.replace(anchor, replacement, 1)
    assert mutated != src, f"变异锚点未命中（{rel} 里这行改了？）—— 自测失效"
    try:
        ast.parse(mutated, filename=rel)
    except SyntaxError as exc:
        raise AssertionError(
            f"变异后 {rel} 语法错误（{exc}）—— 文件会被跳过、变异假绿，请换替换文本"
        ) from exc
    return mutated


def test_mutation_dropping_a_record_call_goes_red():
    """变异自测（直接调用形态）：拿掉 Map 段的落账 → 锁红并点名那处 async_chat。"""
    rel = "core/distiller.py"
    anchor = '            self._try_record_usage("distill_map", merged)\n'
    mutated = _mutate(rel, anchor, "            pass  # 变异：落账调用被拿掉\n")

    _, _, dropped, _ = _audit({rel: mutated})
    assert dropped, "拿掉落账调用后锁没红 —— 主判据是假的"
    hit = [d for d in dropped if d[0] == rel and d[2] == "_one" and d[3] == "async_chat"]
    assert hit, f"拿掉落账后没点名 _one 的 async_chat，实际红了：{dropped}"


def test_mutation_dropping_a_value_form_record_call_goes_red():
    """变异自测（方法当值搬走形态）：拿掉 market 的落账 → 锁红。

    第一版普查只查 Call 形态，整类漏掉 to_thread(llm.chat, ...) —— 这条钉住那个盲区。
    """
    rel = "web/routers/market.py"
    anchor = '        try_record_usage(storage, llm, "chat_ai_reply", source="market")\n'
    mutated = _mutate(rel, anchor, "        pass  # 变异：落账调用被拿掉\n")

    _, _, dropped, _ = _audit({rel: mutated})
    assert dropped, "拿掉 value 形态的落账后锁没红 —— 该类调用点的判据是假的"
    hit = [d for d in dropped if d[0] == rel and d[2] == "at_reply" and d[3] == "chat"
           and d[4] == "value"]
    assert hit, f"拿掉落账后没点名 at_reply 的 value 形态 .chat，实际红了：{dropped}"

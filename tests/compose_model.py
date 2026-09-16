"""compose「有效模型」事实层：把编排文件读成一份 compose 真正会执行的东西。

**边界（硬）。** 本文件不认识本仓的任何服务名、依赖名或编排文件名 —— 不出现某个具体
数据库服务名、不出现本仓那两个编排文件的名字。哪些服务该等谁、哪些键该取什么值，全是
策略层（策略锁）的事；本层只回答「这份文件，compose 读成了什么」。唯一出现的一批文件名是
compose **工具自己**的默认自动加载名（见 `autoload_files_present`）—— 那是工具的契约，
不是本仓的约定。这条边界由 `tests/test_compose_model.py` 里那条用例强制。

**为什么必须问 docker，而不是自己 `yaml.safe_load`。** 人写的 YAML 与 compose 实际执行的
东西之间隔着好几层变换：`${VAR}` 插值、`extends` 继承、`<<` 合并键、短式 `depends_on`
归一化。自己解析只看得到第一层 —— 锁会对着一份**文本代理**判绿，而 compose 执行的是另一份。
代理与事实一分叉，锁就静默失效（缺陷 41 的根因：上一版锁正是拿原始 YAML + 文本识别当代理）。

**命令形状里有三个承重项。**
  - `--no-env-resolution`：不加它，compose 会去解析服务声明的 `env_file: .env` —— 干净检出
    （新克隆 / CI）上它是 exit=1（文件不存在），而在开发机上它把**真实凭据**原样灌进模型。
    加了它，**在满足下面两条前提的 compose 上**：不读 service env_file、不吐秘密，插值照常发生
    （「在满足…的 compose 上」这半句是承重的 —— 前提不成立时这个 flag 在、效果照样没有）。

    **这两条是「行为」性质，不是「加了这个 flag 就有」** —— 逐版本实测（2026-09-16，
    命令形状与本层一致）：

    | 前提 | v2.38.2 | v2.40.3 | v5.0.0 | v5.5.1 |
    |---|---|---|---|---|
    | P-a：`env_file` 指向不存在的文件时仍 exit 0 | ✗ | ✗ | ✓ | ✓ |
    | P-b：`env_file` 存在时其值**不进**模型输出 | ✗ | ✗ | ✓ | ✓ |

    v2.x 上这两个 flag **都在**，但上面两条性质**不成立** —— 所以「flag 在不在」不是这条
    前提的判据（缺陷 51：**测 flag 存在 ≠ 测行为**；本层原先正是拿 flag 存在当代理）。
    两条性质由 `capability_defect()` 在**运行时**真跑一遍合成探针来强制：本层两个入口
    （`effective_model` / `sentinel_values`）探针不过就**不给模型**，CI 另钉 compose 版本。
  - `--env-file <占位>`：占位环境写进临时目录，绝不读写仓内 `.env`。值用可识别的哨兵
    （见 `sentinel_values`），策略层据此分辨「宿主机 `${VAR}` 的插值结果」与「容器内 shell
    的 `$VAR`」—— 两者长得像，行为完全不同。
  - `--format json`：把模型交给谁判，都不该由本层再解析一遍文本。
"""

from __future__ import annotations

import functools
import hashlib
import json
import secrets
import subprocess
import tempfile
from pathlib import Path

from conftest import requirement

_REPO = Path(__file__).resolve().parent.parent

# 本仓编排文件的**命名模式**（不是某一份文件的名字）。
_FILE_GLOB = "docker-compose*.yml"

# compose 工具自己的默认自动加载名，实测得出（base 决定项目、override 自动并入 base）。
# 这是工具的契约，与本仓的约定无关。
_AUTOLOAD_NAMES = frozenset({
    "compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml",
    "compose.override.yaml", "compose.override.yml",
    "docker-compose.override.yml", "docker-compose.override.yaml",
})

# 占位值的可识别前缀 —— 策略层靠它认出「这段文本是宿主机插值的结果」。
_SENTINEL_PREFIX = "sentinel_"

_TMP_DIR: tempfile.TemporaryDirectory | None = None


class ComposeFactError(RuntimeError):
    """拿不到有效模型。**绝不退化成空值** —— 空值会让策略层把「没读到」当成「没问题」。"""


def project_files() -> tuple[Path, ...]:
    """仓库根目录的编排文件（排序固定，读几遍顺序都一样）。"""
    return tuple(sorted(_REPO.glob(_FILE_GLOB)))


def autoload_files_present() -> tuple[Path, ...]:
    """仓库根目录下**会被 compose 自动加载**的文件。

    不带 `-f` 时 compose 会按上面那份默认名找 base、并把 override 自动合进去。生产部署
    一律带 `-f`，但任何人手敲一次不带 `-f` 的 `docker compose ...` 都会吃到它们 ——
    于是「仓库里到底哪份文件在生效」取决于一个没人写下来的隐式约定。
    """
    return tuple(sorted(p for p in _REPO.glob("*") if p.name in _AUTOLOAD_NAMES))


def cli_capable() -> bool:
    """本机有没有**满足承重前提**的 `docker compose`（真跑探针，不是查版本号、不是查 PATH）。"""
    return capability_defect() is None


COMPOSE_ENV = requirement(
    "COMPOSE",
    cli_capable,
    dependency="docker compose",
    short="docker compose",
    why_unavailable=(
        "本机的 `docker compose` 不满足事实层的两条承重前提（P-a：service 的 `env_file` 指向"
        "不存在的文件时必须 exit 0；P-b：`env_file` 的值不得进模型输出）—— 实测 v2.38.2 与 "
        "v2.40.3 两条都不满足，v5.0.0 起满足；本机具体撞在哪一条，见 capability_defect() "
        "的返回值"),
    local_hint="装的 compose 太旧（v2.x）时",
    enable_hint=("升到 compose v5.0.0 或更新（v2.x 上 `--no-env-resolution` 这个 flag 是有的，"
                 "但它要保证的那两条**行为**不成立）"),
)


def _tmp_env_dir() -> Path:
    global _TMP_DIR
    if _TMP_DIR is None:
        _TMP_DIR = tempfile.TemporaryDirectory(prefix="compose-facts-")
    return Path(_TMP_DIR.name)


def _compose(args: list[str]) -> str:
    """跑一次 docker compose，返回 stdout；非零退出即抛，异常里带命令与 stderr。"""
    cmd = ["docker", "compose", *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=str(_REPO), timeout=120)
    except OSError as e:
        raise ComposeFactError(f"调不起 docker compose：{e}") from e
    if r.returncode != 0:
        raise ComposeFactError(
            f"`{' '.join(cmd)}` 退出码 {r.returncode}；"
            f"stderr：{(r.stderr or r.stdout).strip()}")
    return r.stdout


# ── 承重前提：`--no-env-resolution` 那两条行为，每次真跑一遍 ────────────────────
#
# 探针用的合成编排文件与哨兵都在临时目录里，**绝不读写仓内任何 env 文件**。
# 哨兵值每个进程现生成，只用来判「它有没有出现在输出里」，不落盘、不进消息。
#
# **一次探针只测一条性质。** 三条探针各用**各自独立**的合成文件：
#   C（对照） 服务没有 env_file          —— 只问「这条命令形状在本机跑得通吗」
#   P-a        env_file 只列一条不存在的  —— 只问「缺文件时会不会非零退出」
#   P-b        env_file 只列一条存在的    —— 只问「值会不会进模型输出」
# 合成一次跑、两条性质一起判**曾经是个洞**（缺陷 51 审计 F1）：v2 在缺文件那一步先炸，
# 于是永远只报得出 P-a —— 而**那台机器恰恰也是会泄漏的**，泄漏被前一条遮住了。

_PROBE_CONTROL = "_capability_control.yml"
_PROBE_MISSING = "_capability_missing.yml"
_PROBE_LEAKING = "_capability_leaking.yml"
_PROBE_SECRET_KEY = "PROBE_SECRET"
_PROBE_ABSENT = "absent.env"
_PROBE_PRESENT = "present.env"

# 四种成因各说各的话（互不包含）：无 CLI / 探针本身不成立 / P-a / P-b。
_NO_CLI_REASON = "没有可用的 `docker compose`"
_PROBE_BROKEN_REASON = (
    "合成探针本身跑不出结论（对照运行就不成立），本机 compose 能不能用无从判断"
)
_P_A_REASON = (
    "不满足前提 P-a（service 的 `env_file` 指向不存在的文件时必须 exit 0）"
)
_P_B_REASON = (
    "不满足前提 P-b：`env_file` 的值出现在了模型输出里（**秘密会进模型输出**）"
)


def _probe_args(synth: Path, placeholder: Path) -> list[str]:
    """探针命令 —— 与 `_effective_model` **同一形状**，只有 `-f` 指向的文件不同。"""
    return ["-f", str(synth), "--env-file", str(placeholder),
            "config", "--no-env-resolution", "--format", "json"]


def _write_probe(probe_dir: Path, name: str, env_file: tuple[str, ...]) -> Path:
    """写一份合成编排文件。服务名与镜像名是本探针自己的，与本仓编排文件无关。"""
    lines = ["services:", "  probe:", "    image: scratch"]
    if env_file:
        lines.append("    env_file:")
        lines += [f"      - {f}" for f in env_file]
    path = probe_dir / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@functools.lru_cache(maxsize=1)
def capability_defect() -> str | None:
    """本机 compose 缺哪条承重前提；两条都满足返回 `None`。进程内只探一次。

    **为什么必须是真跑。** 原先这两条前提以散文形式写在模块 docstring 里，只在 compose
    v2.38.2 上「实测」过一次 —— 而那次测的是**两个 flag 在不在**，不是这两条**行为**。
    v2.x 两个 flag 都在、两条行为都不成立，于是那条声明一直是空头支票（缺陷 51）。
    前提写在散文里就没人守；写成一个每次都跑的事实才有守卫 —— 这与本模块「问 docker
    而不是自己解析 YAML」是同一条道理。

    三条探针各测一条性质（见上面那段注释）。**顺序是承重的**：C 先跑，它不过说明探针
    自己不成立，后面两条的结论一律不可信，故直接返回、不再往下判 —— 否则「合成文件写错」
    会被读成「P-a 不成立」（缺陷 51 审计 F2：非零退出**不等于** P-a）。

    **P-a 与 P-b 都判完再返回**：两条都不成立时消息里**同时**带上两条原因。中途早退
    就是 F1 那个洞 —— v2 上只会看到 P-a，而它同时也在泄漏。

    **消息里绝不出现哨兵值，也不出现探针输出的原文**（P-a 与对照运行的 stderr 里不可能
    有哨兵 —— 那两条探针根本不引用带哨兵的那份文件 —— 故可以附上）。
    """
    try:
        version_output = _compose(["version"]).strip()
    except ComposeFactError as e:
        return f"{_NO_CLI_REASON}：{e}"

    probe_dir = _tmp_env_dir() / "capability"
    probe_dir.mkdir(parents=True, exist_ok=True)
    placeholder = probe_dir / "placeholder.env"
    placeholder.write_text("", encoding="utf-8")
    token = secrets.token_hex(16)
    (probe_dir / _PROBE_PRESENT).write_text(
        f"{_PROBE_SECRET_KEY}={token}\n", encoding="utf-8")

    # ── 对照 C：服务没有 env_file。只问命令形状跑不跑得通。 ──
    control = _write_probe(probe_dir, _PROBE_CONTROL, ())
    try:
        _compose(_probe_args(control, placeholder))
    except ComposeFactError as e:
        return (f"{_PROBE_BROKEN_REASON}；`docker compose version` 输出：{version_output}；"
                f"对照运行的 {e}")

    reasons: list[str] = []

    # ── P-a：env_file 只列一条不存在的文件。 ──
    missing = _write_probe(probe_dir, _PROBE_MISSING, (_PROBE_ABSENT,))
    try:
        _compose(_probe_args(missing, placeholder))
    except ComposeFactError as e:
        reasons.append(f"{_P_A_REASON}；{e}")

    # ── P-b：env_file 只列一条存在的文件（内容是现生成的哨兵）。 ──
    leaking = _write_probe(probe_dir, _PROBE_LEAKING, (_PROBE_PRESENT,))
    try:
        output = _compose(_probe_args(leaking, placeholder))
    except ComposeFactError as e:
        # 非零退出。C 已经证过命令形状可用，故这里失败**必定另有原因**：若那段文本里
        # 带了哨兵，那是它已经泄漏了（仍判 P-b）；没带就说不清是什么，不猜成 P-a。
        text = str(e)
        if token in text:
            reasons.append(_P_B_REASON)
        else:
            return (f"{_PROBE_BROKEN_REASON}；`docker compose version` 输出：{version_output}；"
                    f"P-b 探针的 {text}")
    else:
        if token in output:
            reasons.append(_P_B_REASON)

    if reasons:
        return "；".join([f"`docker compose version` 输出：{version_output}", *reasons])
    return None


def _require_capability() -> None:
    """入口守卫：前提不成立就抛，**不给模型**。

    放在 `effective_model` / `sentinel_values` 里而不是 `_compose` 里 —— 探针自己也走
    `_compose`，放那儿会自己递归自己。
    """
    defect = capability_defect()
    if defect is not None:
        raise ComposeFactError(
            f"本机的 compose 不满足事实层的承重前提，读到的东西不能当事实用：{defect}")


@functools.lru_cache(maxsize=None)
def _variable_names(project_file: str) -> tuple[str, ...]:
    """compose 列出的、需要宿主机插值的变量名（`config --variables`）。

    由 compose 自己列，而不是在本层写死变量名 —— 写死等于把某个仓库的约定焊进事实层，
    换一份编排文件就静默漏掉新变量（漏掉的那个不会被设成哨兵，策略层也就认不出来）。
    """
    out = _compose(["-f", project_file, "config", "--no-env-resolution", "--variables"])
    names = []
    for line in out.splitlines()[1:]:          # 首行是 NAME / REQUIRED / ... 表头
        tok = line.split()
        if tok and tok[0].isidentifier():
            names.append(tok[0])
    return tuple(sorted(set(names)))


@functools.lru_cache(maxsize=None)
def _placeholder_env(project_file: str) -> Path:
    """某个编排文件对应的占位 env 文件路径（写在临时目录，不与其它文件共用）。"""
    digest = hashlib.sha1(project_file.encode("utf-8")).hexdigest()[:8]
    path = _tmp_env_dir() / f"{Path(project_file).stem}-{digest}.env"
    path.write_text(
        "".join(f"{n}={_SENTINEL_PREFIX}{n}\n" for n in _variable_names(project_file)),
        encoding="utf-8")
    return path


@functools.lru_cache(maxsize=None)
def _effective_model(project_file: str) -> dict:
    out = _compose(["-f", project_file, "--env-file", str(_placeholder_env(project_file)),
                    "config", "--no-env-resolution", "--format", "json"])
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise ComposeFactError(
            f"compose 的输出不是 JSON（{e}）；前 500 字：{out[:500]}") from e


def effective_model(path) -> dict:
    """这份编排文件，compose 读成的字典（按文件缓存）。

    拿不到就抛 `ComposeFactError`，异常里带命令与 stderr —— **不返回 `{}`**：调用方拿到空
    字典只会把「没读到」当成「没问题」，那正是缺陷 21 的静默放行。
    """
    _require_capability()
    return _effective_model(str(Path(path).resolve()))


def sentinel_values(paths=None) -> frozenset[str]:
    """占位环境里用的哨兵值；不给 `paths` 即本仓全部编排文件。

    策略层拿它做一件事：一段文本里出现哨兵，就说明那是**宿主机** `${VAR}` 的插值结果，
    而不是容器内 shell 要展开的 `$VAR`（缺陷 41 的 G-5 那条）。
    """
    _require_capability()
    files = project_files() if paths is None else paths
    return frozenset(
        f"{_SENTINEL_PREFIX}{n}" for p in files for n in _variable_names(str(Path(p).resolve()))
    )

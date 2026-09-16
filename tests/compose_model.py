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
    加了它：不读 service env_file、不吐秘密，插值照常发生。
  - `--env-file <占位>`：占位环境写进临时目录，绝不读写仓内 `.env`。值用可识别的哨兵
    （见 `sentinel_values`），策略层据此分辨「宿主机 `${VAR}` 的插值结果」与「容器内 shell
    的 `$VAR`」—— 两者长得像，行为完全不同。
  - `--format json`：把模型交给谁判，都不该由本层再解析一遍文本。
"""

from __future__ import annotations

import functools
import hashlib
import json
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


def cli_available() -> bool:
    """本机有没有可用的 `docker compose`（真跑一次，不是查 PATH、不是查文件存在）。"""
    try:
        r = subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


COMPOSE_ENV = requirement(
    "COMPOSE",
    cli_available,
    dependency="docker compose",
    short="docker compose",
    why_unavailable="本机没有可用的 `docker compose` 命令（探针真跑了一次 `compose version`）",
    local_hint="没装 docker compose 时",
    enable_hint="装 docker（含 compose v2 插件）",
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
    return _effective_model(str(Path(path).resolve()))


def sentinel_values(paths=None) -> frozenset[str]:
    """占位环境里用的哨兵值；不给 `paths` 即本仓全部编排文件。

    策略层拿它做一件事：一段文本里出现哨兵，就说明那是**宿主机** `${VAR}` 的插值结果，
    而不是容器内 shell 要展开的 `$VAR`（缺陷 41 的 G-5 那条）。
    """
    files = project_files() if paths is None else paths
    return frozenset(
        f"{_SENTINEL_PREFIX}{n}" for p in files for n in _variable_names(str(Path(p).resolve()))
    )

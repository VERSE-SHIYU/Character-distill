# -*- coding: utf-8 -*-
"""缺陷 41 A 轮 · **实跑证据**：凭据错时 postgres 真的变 unhealthy、app 真的被拦下。

**这份脚本补的是静态锁证明不了的那一半。** `tests/test_pg_gate.py` 只证明命令的
**形态**（不含 pg_isready、调了 psql、`-h` 是服务名……）；形态对而语义错完全可能
（psql 后面接一条永远成功的语句就是）。「凭据错时它真的会红」只能真跑一个库来证明 ——
那就是本文件。

**为什么另起一个 compose 项目，而不是跑仓里的栈。** 三个场景里有**故意让库不可用**的
两步，绝不能落在 `character-distill` / `cdload` 这两个正在跑的栈上（它们是用户资产）。
故本项目一律 `-p credtest`，用自有卷 `credtest_pg_data`，收尾 `down -v` 只删自己那份，
最后再核对用户的卷仍在。**不重建、不重启、不删除任何现有容器或卷。**

**healthcheck 从仓里的 `docker-compose.local.yml` 解析读取，不在这里另抄一份。** 抄一份
就变成「验证的是这份脚本里的命令」，而不是「仓里那条命令」—— 两者可以各自漂移而无人知。

**app 由本脚本从**仓里当前源码**构建一次**（`credtest-app:local`，走仓里的 Dockerfile，
依赖命中镜像层缓存）。为什么不复用本机已有的 `character-distill-app:latest`：那个镜像
构建于 26 小时前、早于 B 轮，里面**没有 `/api/health/ready`** —— 拿它当 app，场景 1 的
401 会来自「镜像旧」而不是「凭据对」，这正是两种成因共用一个信号。为什么不把仓里的
`web/` 挂进旧镜像：实测新 `web/server.py` 要 `adapters.llm_adapter.llm_error_types`，
旧镜像的 `adapters/` 里没有 —— 挂载只是把「镜像旧」换成「半新半旧的拼装」，更难说清。
构建一次约 4 分钟（有层缓存），换来的是「app 就是仓里这一份」这句话真的成立。

**口径**：不打印任何凭据值 —— 口令由 `secrets` 现场生成、只写进仓外临时目录的 `.env`，
从不进 stdout；认证失败的证据只输出 `password authentication failed` 这个**关键字**，
不输出 psql 原文（原文含用户名与库名）。临时目录收尾删除。

**用法**
    python tests/perf/credtest_pg_healthcheck.py
"""
from __future__ import annotations

import json
import pathlib
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import yaml

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

REPO = pathlib.Path(__file__).resolve().parents[2]
LOCAL_COMPOSE = REPO / "docker-compose.local.yml"

PROJECT = "credtest"
APP_IMAGE = "credtest-app:local"
PG_CONTAINER = f"{PROJECT}-postgres-1"
APP_CONTAINER = f"{PROJECT}-app-1"

# 用户的卷：收尾必须仍在。写死两个具体名字是**故意的** —— 这条是「我没碰到别人的东西」
# 的核对，不是一个通用的卷策略。
UNTOUCHED_VOLUMES = ("character-distill_pg_data", "cdload_pg_data")

# 场景 2 的库上跑着的仍然是旧口令，健康探针要连续失败 retries 次才判 unhealthy。
UNHEALTHY_BUDGET = 200
HEALTHY_BUDGET = 150

AUTH_FAILURE_KEYWORD = "password authentication failed"


# ── 执行原语 ─────────────────────────────────────────────────────────────────

def _docker(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _compose(cwd: pathlib.Path, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-p", PROJECT,
           "-f", str(cwd / "docker-compose.yml"),
           "--env-file", str(cwd / ".env"), *args]
    try:
        return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            cmd, 124, (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            f"TIMEOUT after {timeout}s")


def _inspect(fmt: str, name: str) -> str:
    r = _docker("inspect", "--format", fmt, name)
    return r.stdout.strip() if r.returncode == 0 else ""


def _health(name: str) -> str:
    return _inspect("{{if .State.Health}}{{.State.Health.Status}}{{else}}<none>{{end}}", name) or "<absent>"


def _state(name: str) -> str:
    return _inspect("{{.State.Status}}", name) or "<absent>"


def _wait_health(name: str, want: str, budget: int) -> tuple[str, float]:
    t0 = time.time()
    while time.time() - t0 < budget:
        got = _health(name)
        if got == want:
            return got, time.time() - t0
        time.sleep(2)
    return _health(name), time.time() - t0


def _auth_failure_seen(name: str) -> bool:
    """健康日志里有没有认证失败 —— 只回布尔，不回原文（原文含用户名与库名）。"""
    raw = _inspect("{{json .State.Health}}", name)
    if not raw:
        return False
    try:
        log = json.loads(raw).get("Log") or []
    except json.JSONDecodeError:
        return False
    return any(AUTH_FAILURE_KEYWORD in (e.get("Output") or "") for e in log)


# ── 生成 credtest 的编排（healthcheck 取自仓里那份）────────────────────────────

def _repo_postgres() -> dict:
    doc = yaml.safe_load(LOCAL_COMPOSE.read_text(encoding="utf-8")) or {}
    return doc["services"]["postgres"]


def _build_image() -> None:
    """从**仓里当前源码**构建 app 镜像 —— 理由见模块 docstring。"""
    print(f"[build] {APP_IMAGE} ← {REPO}（走仓里 Dockerfile；依赖命中层缓存时约 4 分钟）")
    r = subprocess.run(["docker", "build", "-t", APP_IMAGE, "."], cwd=str(REPO),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print((r.stdout or "")[-1200:])
        print((r.stderr or "")[-1200:], file=sys.stderr)
        raise SystemExit(f"镜像构建失败（rc={r.returncode}）—— 没有 app 就演不了场景 1/3")
    print("[build] ok")


def _compose_doc(healthcheck: dict, app_port: int) -> dict:
    pg = dict(_repo_postgres())
    # 只保留与本命题有关、且不依赖本机环境的部分。`ports` 必须去掉：仓里那份绑
    # 127.0.0.1:5432，而本机已经有一个 postgres 占着它。app 走 compose 网络按服务名连，
    # 不经过宿主端口，故去掉不影响探针（探针用的也是 `-h postgres`）。
    for key in ("ports", "networks", "logging", "oom_score_adj",
                "mem_limit", "mem_reservation", "restart"):
        pg.pop(key, None)
    pg["healthcheck"] = healthcheck
    return {
        "services": {
            "postgres": pg,
            "app": {
                "image": APP_IMAGE,
                "environment": [
                    "DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}"
                    "@postgres:5432/${POSTGRES_DB}",
                    "JWT_SECRET=${JWT_SECRET}",
                    # 本仓拒绝在未声明后端时静默回落（见 `storage/__init__.py` 的点名
                    # 报错），故这里必须显式声明；不声明时 `get_storage()` 在 Depends
                    # 阶段就抛，端点答 500 —— 那是「配置没给」的红，不是凭据的红，
                    # 两种成因共用一个信号（§四）。
                    "STORAGE_BACKEND=postgres",
                ],
                "depends_on": {"postgres": {"condition": "service_healthy"}},
                # 发布到**临时挑的空闲端口**（见 `_free_port`），只为了让宿主的
                # `_ready_code` 打得到就绪端点。
                "ports": [f"127.0.0.1:{app_port}:7860"],
                "restart": "no",
            },
        },
        # 顶层 volumes：项目名 credtest ⇒ 实际卷名 credtest_pg_data，与用户的卷不同名。
        "volumes": {"pg_data": None},
    }


def _write_env(path: pathlib.Path, password: str) -> None:
    path.write_text(
        f"POSTGRES_USER=cdcredtest\n"
        f"POSTGRES_PASSWORD={password}\n"
        f"POSTGRES_DB=credtest\n"
        f"JWT_SECRET={secrets.token_hex(32)}\n",
        encoding="utf-8")


def _free_port() -> int:
    """宿主上一个此刻空闲的端口。

    不写死端口号：本机可能已经有别的栈占着某个约定俗成的端口，撞上时的失败信息是
    「端口被占」，与本条命题无关 —— 那正是「两种成因共用一个信号」。取一个空闲端口再
    把 app 发布上去，撞号这件事就不存在了。
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ready_code(port: int, budget: int = 90) -> str:
    """从**宿主**打一次就绪端点，拿真实状态码；刚起时重试到它开始应答。

    走宿主而不是从 credtest 网络内部再起一个容器：实测后者拿不到 compose 的服务名别名
    （`Name or service not known`），而改成用容器名去连又要额外依赖 compose 的命名规则。
    发布一个端口从宿主打，两边都不依赖额外约定。

    **为什么要重试**：`compose up -d` 在 app 容器 `running` 的那一刻就返回了，而 uvicorn
    还要几秒才 bind —— 此刻请求拿到的是「连不上」，那是**探针来早了**，不是「就绪端点答
    别的」。把这两件事混成一个信号正是缺陷 41 的形态，故这里按时间收敛而不是赌一次。
    """
    url = f"http://127.0.0.1:{port}/api/health/ready"
    t0 = time.time()
    last = "<no-answer>"
    while time.time() - t0 < budget:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return str(resp.status)
        except urllib.error.HTTPError as e:
            return str(e.code)          # 4xx/5xx 是**答案**，不是「连不上」
        except Exception as e:          # 连接被拒 / 还没 bind —— 探针来早了
            last = f"<尚未应答：{type(e).__name__}>"
            time.sleep(3)
    return last


# ── 三个场景 ─────────────────────────────────────────────────────────────────

def main() -> int:
    pg = _repo_postgres()
    repo_hc = pg.get("healthcheck")
    if not repo_hc:
        print("仓里的 postgres 没有 healthcheck —— 无据可跑")
        return 2

    _build_image()

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="credtest-"))
    app_port = _free_port()
    rows: list[dict] = []
    failures: list[str] = []
    stored_test = ""

    try:
        (tmp / ".env").write_text("", encoding="utf-8")
        _write_env(tmp / ".env", "s1-password")
        (tmp / "docker-compose.yml").write_text(
            yaml.safe_dump(_compose_doc(repo_hc, app_port), allow_unicode=True, sort_keys=False),
            encoding="utf-8")

        def scenario(label: str, expect_pg: str, budget: int,
                     expect_app_released: bool, expect_ready: str | None) -> None:
            r = _compose(tmp, "up", "-d", timeout=300)
            got, waited = _wait_health(PG_CONTAINER, expect_pg, budget)
            app_state = _state(APP_CONTAINER)
            released = app_state == "running"
            code = _ready_code(app_port) if released else "<app 未启动>"
            row = {"场景": label, "postgres": got, "耗时": f"{waited:.0f}s",
                   "app state": app_state, "app 放行": "是" if released else "否",
                   "ready": code, "认证失败关键字": "有" if _auth_failure_seen(PG_CONTAINER) else "无"}
            rows.append(row)
            print(f"\n### {label}")
            print(f"    up rc={r.returncode}"
                  + (f"  stderr={r.stderr.strip()[-300:]!r}" if r.returncode else ""))
            for k, v in row.items():
                print(f"    {k}: {v}")
            if got != expect_pg:
                failures.append(f"{label}：postgres 期望 {expect_pg} 实得 {got}")
            if released != expect_app_released:
                failures.append(
                    f"{label}：app 放行期望 {'是' if expect_app_released else '否'} "
                    f"实得 {'是' if released else '否'}（state={app_state}）")
            if expect_ready and not code.startswith(expect_ready):
                failures.append(f"{label}：/api/health/ready 期望 {expect_ready}** 实得 {code}")

        # 场景 1：口令与卷一致 —— 库 healthy，app 被放行，就绪端点真查库并答 200。
        scenario("1 凭据正确（卷与 env 一致）", "healthy", HEALTHY_BUDGET, True, "200")
        stored_test = _inspect("{{json .Config.Healthcheck.Test}}", PG_CONTAINER)
        # 收尾场景 2 之前先把容器停掉但不删卷（volume 里是 s1 的口令）。
        _compose(tmp, "down", timeout=120)

        # 场景 2：只改环境文件里的口令，卷还是旧口令 —— 库必须 unhealthy，app 不被放行。
        _write_env(tmp / ".env", "s2-rotated-password")
        scenario("2 只改 env 口令（卷仍是旧口令）", "unhealthy", UNHEALTHY_BUDGET, False, None)

        # 场景 3：对照。同一条错口令，只把探针换回 pg_isready —— 库显示 healthy，
        # 证明场景 2 的红来自本次修改，不是这套环境本身跑不出 healthy。
        #
        # 场景 3 的就绪码期望 **503 而不是 200**，这正是缺陷 41 最完整的一次显形：
        # 旧探针把 app **放行了**（healthy），而 app 自己一查库就发现口令不对（503）。
        # 「门说可以，门后面的东西说不行」—— 门的命题与它要保证的命题不是同一个。
        control = dict(repo_hc)
        control["test"] = ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
        (tmp / "docker-compose.yml").write_text(
            yaml.safe_dump(_compose_doc(control, app_port), allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        _compose(tmp, "down", timeout=120)
        scenario("3 对照：换回 pg_isready（同一条错口令）", "healthy", HEALTHY_BUDGET, True, "503")

        # ── 收尾 ──────────────────────────────────────────────────────────────
        print("\n== 收尾 ==")
        r = _compose(tmp, "down", "-v", timeout=180)
        print(f"  docker compose -p {PROJECT} down -v  rc={r.returncode}")
        vols = _docker("volume", "ls", "--format", "{{.Name}}").stdout.split()
        for name in UNTOUCHED_VOLUMES:
            ok = name in vols
            print(f"  用户卷 {name:32s} 仍在 {ok}")
            if not ok:
                failures.append(f"用户卷 {name} 不见了 —— 收尾动到了不属于本项目的东西")
        print(f"  credtest 自有卷残留：{[v for v in vols if v.startswith(PROJECT)] or '无'}")

        print("\n== 状态表 ==")
        cols = ["场景", "postgres", "耗时", "app state", "app 放行", "ready", "认证失败关键字"]
        print("  " + " | ".join(c.ljust(10) for c in cols))
        for row in rows:
            print("  " + " | ".join(str(row[c])[:10].ljust(10) for c in cols))

        print("\n== 存量探针（验收第 6 项：证明容器配置里存的是变量名）==")
        print(f"  .Config.Healthcheck.Test = {stored_test}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n== 结论 ==")
    if failures:
        for f in failures:
            print("  MISMATCH", f)
        return 1
    print("  三个场景全部符合预期。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

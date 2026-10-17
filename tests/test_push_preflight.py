"""推送前预检的行为测试。

判据只有一条：真跑脚本、真推分支、真起子进程，不扫描源码文本（T7 例外，它判的是
「CI 不另写一份实现」这条结构，读 YAML 的解析结果）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parent.parent
_CHECK = _REPO / "scripts" / "check.sh"
_HOOK = _REPO / ".githooks" / "pre-push"

# 测试内部再起 git 时不要读用户/系统配置（hooksPath、签名、默认分支都可能干扰）。
_GIT_ISOLATION = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _git_bash() -> str:
    """Windows 上 Python 的 PATH 里 `bash` 可能是 WSL；钩子语法要求 Git for Windows 的 bash。"""
    if os.name != "nt":
        return "bash"
    git = shutil.which("git")
    if git:
        for parent in Path(git).resolve().parents:
            for candidate in (parent / "bin" / "bash.exe", parent / "usr" / "bin" / "bash.exe"):
                if candidate.exists():
                    return str(candidate)
    return "bash"


_BASH = _git_bash()


def _chmod_x(path: Path) -> None:
    subprocess.run([_BASH, "-c", 'chmod +x "$1"', "chmod", path.as_posix()], check=True)


def _run(cmd: list[str], cwd: Path, env: dict | None = None, input: str | None = None):
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env={**os.environ, **(env or {})},
        input=input,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _git(cwd: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    out = _run(["git", *args], cwd, env={**_GIT_ISOLATION, **(env or {})})
    assert out.returncode == 0, f"git {' '.join(args)} 失败：{out.stderr}"
    return out


def _write_exec(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="\n")
    _chmod_x(path)
    return path


def _prepend_path(directory: Path) -> str:
    """PATH 可能已经是 POSIX 形式（从 Git Bash 继承），不能一律用 os.pathsep。"""
    old = os.environ.get("PATH", "")
    sep = ":" if (":" in old and ";" not in old) else os.pathsep
    return f"{directory.as_posix()}{sep}{old}"


def _commit(repo: Path, relpath: str, content: str = "x\n") -> None:
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    _git(repo, "add", relpath)
    _git(repo, "commit", "-qm", f"add {relpath}")


def _push(repo: Path, *refspecs: str, env: dict | None = None):
    return _run(
        ["git", "push", "origin", *refspecs],
        repo,
        env={"PREFLIGHT_CMD": "false", **_GIT_ISOLATION, **(env or {})},
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "preflight@example.invalid")
    _git(repo, "config", "user.name", "preflight")
    _git(repo, "config", "core.autocrlf", "false")
    hook = repo / ".githooks" / "pre-push"
    hook.parent.mkdir()
    shutil.copyfile(_HOOK, hook)
    _chmod_x(hook)
    _git(repo, "config", "core.hooksPath", ".githooks")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    return repo


def _push_initial(repo: Path) -> None:
    """建立远端 main 与 origin/main，让后续推送走「已存在分支」的路径。"""
    _commit(repo, "core/base.py")
    _git(repo, "push", "-q", "--no-verify", "origin", "main")
    _git(repo, "fetch", "-q", "origin")


def _fake_python(tmp_path: Path) -> tuple[Path, Path]:
    """假解释器记录 argv / REQUIRE_*，按 FAKE_EXIT 退出；另放假 pytest 当安全网。"""
    bin_dir = tmp_path / "bin"
    record = tmp_path / "record"
    record.mkdir()
    fake = _write_exec(
        bin_dir / "fake-python",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            for arg in "$@"; do printf '%s\\n' "$arg" >> "$FAKE_RECORD/argv"; done
            printf '%s' "${REQUIRE_PG_TESTS:-}" > "$FAKE_RECORD/pg"
            printf '%s' "${REQUIRE_COMPOSE_TESTS:-}" > "$FAKE_RECORD/compose"
            if [ -n "${FAKE_STDOUT:-}" ]; then printf '%s\\n' "$FAKE_STDOUT"; fi
            # 真实 pytest 输出会被某些用例写入 NUL，grep 默认把这种日志当二进制、不输出匹配行。
            if [ -n "${FAKE_BINARY:-}" ]; then printf 'BINARY\\000MARK\\n'; fi
            exit "${FAKE_EXIT:-0}"
            """
        ),
    )
    # 解释器解析被改回裸 pytest 时，宁可红在断言上，也不要真去跑全套。
    _write_exec(bin_dir / "pytest", "#!/usr/bin/env bash\necho 'BARE pytest WAS CALLED' >&2\nexit 99\n")
    return bin_dir, fake


def _check(tmp_path: Path, *args: str, env: dict | None = None, bin_dir: Path | None = None):
    merged = {
        **_GIT_ISOLATION,
        # 全量跑测试时外层的 check.sh 也在写仓库根的 pytest.log，内层必须换路径。
        "PYTEST_LOG": (tmp_path / "inner-pytest.log").as_posix(),
        **({"PATH": _prepend_path(bin_dir)} if bin_dir else {}),
        **(env or {}),
    }
    return _run([_BASH, "scripts/check.sh", *args], _REPO, env=merged)


# --------------------------------------------------------------------------- T1


def test_t1_hook_blocks_failing_check(tmp_path):
    repo = _init_repo(tmp_path)
    _push_initial(repo)
    _commit(repo, "core/x.py")

    out = _push(repo, "main")

    assert out.returncode != 0, out.stdout + out.stderr
    assert "failed to push" in (out.stdout + out.stderr).lower()


# ---------------------------------------------------------------- T2a/T2b/T2c


def test_t2a_docs_only_push_is_allowed(tmp_path):
    repo = _init_repo(tmp_path)
    _push_initial(repo)
    _commit(repo, "docs/x.md")

    out = _push(repo, "main")

    assert out.returncode == 0, out.stdout + out.stderr


def test_t2b_docs_plus_code_is_not_allowed(tmp_path):
    repo = _init_repo(tmp_path)
    _push_initial(repo)
    _commit(repo, "docs/x.md")
    _commit(repo, "core/x.py")

    out = _push(repo, "main")

    assert out.returncode != 0, out.stdout + out.stderr


def test_t2c_new_branch_uses_merge_base(tmp_path):
    repo = _init_repo(tmp_path)
    _push_initial(repo)

    _git(repo, "checkout", "-q", "-b", "docs-branch")
    _commit(repo, "docs/x.md")
    docs_push = _push(repo, "docs-branch")
    assert docs_push.returncode == 0, docs_push.stdout + docs_push.stderr

    _git(repo, "checkout", "-q", "-b", "code-branch", "main")
    _commit(repo, "core/x.py")
    code_push = _push(repo, "code-branch")
    assert code_push.returncode != 0, code_push.stdout + code_push.stderr


def test_t9_hook_hides_git_dir_from_the_check(tmp_path):
    """worktree 里 push 时 Git 会给钩子注入 GIT_DIR；检查拿到它，临时仓库就会落回外层仓库。"""
    repo = _init_repo(tmp_path)
    _push_initial(repo)
    _commit(repo, "core/x.py")

    out = _push(
        repo, "main",
        env={
            "GIT_DIR": (repo / ".git").as_posix(),
            "PREFLIGHT_CMD": 'test -z "${GIT_DIR:-}"',
        },
    )

    assert out.returncode == 0, out.stdout + out.stderr


# ---------------------------------------------------------------- T3/T4/T5


def test_t3_backend_uses_same_pytest_invocation_and_declares_pg(tmp_path):
    bin_dir, fake = _fake_python(tmp_path)
    record = tmp_path / "record"

    out = _check(
        tmp_path, "backend",
        env={"PYTHON": fake.as_posix(), "FAKE_RECORD": record.as_posix()},
        bin_dir=bin_dir,
    )

    assert out.returncode == 0, out.stdout + out.stderr
    assert (record / "argv").read_text().splitlines() == [
        "-m", "pytest", "tests/", "-v", "--tb=short",
    ]
    assert (record / "pg").read_text() == "1"
    assert (record / "compose").read_text() == "1"


def test_t4_backend_propagates_pytest_exit_code(tmp_path):
    bin_dir, fake = _fake_python(tmp_path)
    record = tmp_path / "record"

    out = _check(
        tmp_path, "backend",
        env={"PYTHON": fake.as_posix(), "FAKE_RECORD": record.as_posix(), "FAKE_EXIT": "1"},
        bin_dir=bin_dir,
    )

    assert out.returncode == 1, out.stdout + out.stderr


def test_t5_failure_summary_lists_failed_tests(tmp_path):
    bin_dir, fake = _fake_python(tmp_path)
    record = tmp_path / "record"
    summary = tmp_path / "summary.md"

    out = _check(
        tmp_path, "backend",
        env={
            "PYTHON": fake.as_posix(),
            "FAKE_RECORD": record.as_posix(),
            "FAKE_EXIT": "1",
            "FAKE_STDOUT": "FAILED tests/a.py::t1 - AssertionError",
            "FAKE_BINARY": "1",
            "GITHUB_STEP_SUMMARY": summary.as_posix(),
        },
        bin_dir=bin_dir,
    )

    assert out.returncode == 1, out.stdout + out.stderr
    assert "FAILED tests/a.py::t1" in summary.read_text(encoding="utf-8")
    assert "::error" in out.stdout
    assert "tests/a.py::t1" in out.stdout


# --------------------------------------------------------------------------- T6


def test_t6_frontend_runs_eslint_then_npm_test(tmp_path):
    bin_dir = tmp_path / "bin"
    calls = tmp_path / "calls"
    for name, exit_var in (("npx", "FAKE_NPX_EXIT"), ("npm", "FAKE_NPM_EXIT")):
        _write_exec(
            bin_dir / name,
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                printf '%s|%s|%s\\n' '{name}' "$PWD" "$*" >> "$FAKE_CALLS"
                exit "${{{exit_var}:-0}}"
                """
            ),
        )
    env = {"FAKE_CALLS": calls.as_posix()}

    ok = _check(tmp_path, "frontend", env=env, bin_dir=bin_dir)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    rows = calls.read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("npx|") and rows[0].endswith("eslint . -c eslint.ci.config.js --quiet")
    assert rows[1].startswith("npm|") and rows[1].endswith("test")
    assert all("/web/frontend" in row.split("|")[1].replace("\\", "/") for row in rows)

    calls.write_text("", encoding="utf-8")
    npx_fail = _check(tmp_path, "frontend", env={**env, "FAKE_NPX_EXIT": "1"}, bin_dir=bin_dir)
    assert npx_fail.returncode != 0

    calls.write_text("", encoding="utf-8")
    npm_fail = _check(tmp_path, "frontend", env={**env, "FAKE_NPM_EXIT": "1"}, bin_dir=bin_dir)
    assert npm_fail.returncode != 0


# --------------------------------------------------------------------------- T7


def test_t7_ci_calls_the_same_entrypoint():
    data = yaml.safe_load((_REPO / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8"))

    for job in ("test", "upstream-drift"):
        steps = [s for s in data["jobs"][job]["steps"] if s.get("name") == "Run tests"]
        assert len(steps) == 1, job
        assert "scripts/check.sh backend" in steps[0]["run"]

    front_runs = [s["run"] for s in data["jobs"]["frontend-lint"]["steps"] if "run" in s]
    assert any(line.strip() == "npm ci" for r in front_runs for line in r.splitlines())
    assert any("scripts/check.sh frontend" in r for r in front_runs)

    all_runs = [s["run"] for job in data["jobs"].values() for s in job["steps"] if "run" in s]
    assert not any("pytest tests/" in r for r in all_runs)


# --------------------------------------------------------------------------- T8


# ---------------------------------------------------------------- G1/G2/G3


def test_g1_hook_runs_preflight_target(tmp_path):
    repo = _init_repo(tmp_path)
    _push_initial(repo)
    _commit(repo, "core/x.py")

    record = tmp_path / "hook-args"
    _write_exec(
        repo / "scripts" / "check.sh",
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" > "$HOOK_RECORD"\n',
    )

    out = _run(
        ["git", "push", "origin", "main"],
        repo,
        env={"PREFLIGHT_CMD": "", "HOOK_RECORD": record.as_posix(), **_GIT_ISOLATION},
    )

    assert out.returncode == 0, out.stdout + out.stderr
    assert record.read_text(encoding="utf-8").strip() == "preflight"


def test_g2_preflight_runs_meta_locks_and_eslint_only(tmp_path):
    bin_dir, fake = _fake_python(tmp_path)
    record = tmp_path / "record"
    calls = tmp_path / "calls"
    for name, exit_var in (("npx", "FAKE_NPX_EXIT"), ("npm", "FAKE_NPM_EXIT")):
        _write_exec(
            bin_dir / name,
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                printf '%s|%s|%s\\n' '{name}' "$PWD" "$*" >> "$FAKE_CALLS"
                exit "${{{exit_var}:-0}}"
                """
            ),
        )

    out = _check(
        tmp_path, "preflight",
        env={
            "PYTHON": fake.as_posix(),
            "FAKE_RECORD": record.as_posix(),
            "FAKE_CALLS": calls.as_posix(),
        },
        bin_dir=bin_dir,
    )

    assert out.returncode == 0, out.stdout + out.stderr
    argv = (record / "argv").read_text(encoding="utf-8").splitlines()
    paths = [arg for arg in argv if arg.startswith("tests/")]
    assert paths, argv
    for path in paths:
        assert path != "tests/"
        assert path.startswith("tests/test_") and path.endswith(".py")
        assert "/" not in path[len("tests/"):], path
        assert (_REPO / path).is_file(), path

    rows = calls.read_text(encoding="utf-8").splitlines()
    assert rows and rows[0].startswith("npx|")
    assert rows[0].endswith("eslint . -c eslint.ci.config.js --quiet")
    assert not any(row.startswith("npm|") for row in rows), rows


def test_g3_preflight_propagates_meta_lock_failure(tmp_path):
    bin_dir, fake = _fake_python(tmp_path)
    record = tmp_path / "record"
    _write_exec(bin_dir / "npx", "#!/usr/bin/env bash\nexit 0\n")

    out = _check(
        tmp_path, "preflight",
        env={
            "PYTHON": fake.as_posix(),
            "FAKE_RECORD": record.as_posix(),
            "FAKE_EXIT": "1",
        },
        bin_dir=bin_dir,
    )

    assert out.returncode == 1, out.stdout + out.stderr


def test_g4_preflight_propagates_eslint_failure(tmp_path):
    bin_dir, fake = _fake_python(tmp_path)
    record = tmp_path / "record"
    _write_exec(bin_dir / "npx", "#!/usr/bin/env bash\nexit 1\n")

    out = _check(
        tmp_path, "preflight",
        env={"PYTHON": fake.as_posix(), "FAKE_RECORD": record.as_posix()},
        bin_dir=bin_dir,
    )

    assert out.returncode == 1, out.stdout + out.stderr


# --------------------------------------------------------------------------- T8


def test_t8_xfail_strict_makes_xpass_fail(tmp_path):
    work = tmp_path / "xfail"
    work.mkdir()
    shutil.copyfile(_REPO / "pytest.ini", work / "pytest.ini")
    (work / "test_xpass.py").write_text(
        textwrap.dedent(
            """\
            import pytest

            @pytest.mark.xfail
            def test_passes_but_marked_xfail():
                assert True
            """
        ),
        encoding="utf-8",
        newline="\n",
    )

    out = _run(
        [sys.executable, "-m", "pytest", "-c", "pytest.ini", "test_xpass.py", "-q"],
        work,
    )

    assert out.returncode != 0, out.stdout + out.stderr
    assert "XPASS" in out.stdout + out.stderr

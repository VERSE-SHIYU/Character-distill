"""步骤 3d 验收：蒸馏断点续跑（分片中间结果复用 + 两级门）。

全部用确定性 mock LLM，零真 API：

1 续跑省调用：片全命中 → 0 次 Map LLM 调用
2 幂等：连续两次续跑 → reduce/format 输入与产出逐字节一致
3 第二道门：缓存 result 为空/失效 → 只重跑该片，不崩、不复用坏结果
4 主路径零回归：resume_candidates=None 时与不续跑行为完全一致
5 失败片不落 checkpoint：Map 分片抛异常 → 不进 on_chunk_done，门 2 不再是唯一屏障

任务级门（改 chunk_size / 改原文 → 整批重跑）落在 /start，见
tests/test_distill_task_api.py::TestEResumeGate（同一提交）。
"""

from __future__ import annotations

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.distiller import Distiller, _resume_hit, text_fingerprint


CHUNK_SIZE = 40
# 12 段、每段都含目标角色名 → relevant = 全部分片
TEXT = "\n\n".join(
    f"角色第{i}段：角色说了第{i}句话，这里还有角色的别的话。" for i in range(12)
)


# ── 确定性假 LLM ─────────────────────────────────────────────────────────────

class _FakeClient:
    async def close(self):
        pass


class _FakeLLM:
    """任何输出都是输入的纯函数 —— 便于逐字节比对，且不烧真 API。"""

    def __init__(self):
        self.map_calls = 0
        self.stream_inputs: list[str] = []

    def _make_async_client(self):
        return _FakeClient()

    async def async_chat(self, system, messages, client=None):
        self.map_calls += 1
        return self._digest(messages[0]["content"]), None

    def chat_stream(self, system, messages, max_tokens=None):
        # reduce 与 format 都走这里；输出 = f(输入)
        self.stream_inputs.append(messages[0]["content"])
        yield '{"name": "角色", "identity": "' + self._digest(messages[0]["content"]) + '"}'

    @staticmethod
    def _digest(text: str) -> str:
        return "分析-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]

    last_usage = None


def _make_distiller(llm) -> Distiller:
    d = Distiller(llm=llm, config_path=None)
    d._longctx_threshold = 1     # 强制走分片 MapReduce 路径（短文本默认走长上下文单次）
    d._chunk_size = CHUNK_SIZE
    return d


def _run(llm, candidates):
    """消费 stream，返回 (on_chunk_done 记录, format 产出的 token 拼接)。"""
    d = _make_distiller(llm)
    done: list[tuple[int, str, str]] = []
    tokens: list[str] = []
    for piece in d.distill_incremental_stream(
        TEXT, "角色", [], "story",
        on_chunk_done=lambda i, r, fp: done.append((i, r, fp)),
        resume_candidates=candidates,
    ):
        if isinstance(piece, str):
            tokens.append(piece)
        else:
            assert "error" not in piece, piece   # 任何阶段报错都算失败
    return done, "".join(tokens)


def _candidates(done):
    return {i: {"result": r, "fingerprint": fp} for i, r, fp in done}


# ── 分片三重门纯逻辑 ─────────────────────────────────────────────────────────

class TestResumeHitDoors:
    def test_three_doors(self):
        chunk = "某片原文"
        ok = {"result": "分析", "fingerprint": text_fingerprint(chunk)}
        assert _resume_hit(0, chunk, {0: ok}) == "分析"
        assert _resume_hit(0, chunk, None) is None                       # 无候选
        assert _resume_hit(0, chunk, {1: ok}) is None                    # index 不命中
        assert _resume_hit(0, chunk, {0: {"result": "分析"}}) is None     # 形状缺 fingerprint
        assert _resume_hit(0, chunk, {0: {"result": "", "fingerprint": text_fingerprint(chunk)}}) is None
        assert _resume_hit(0, chunk, {0: {"result": "分析", "fingerprint": "stale"}}) is None
        assert _resume_hit(0, chunk, {0: {"result": None, "fingerprint": text_fingerprint(chunk)}}) is None


# ── 1 续跑省调用 + 2 幂等 ────────────────────────────────────────────────────

class TestResumeSavesCalls:
    def test_full_hit_zero_map_calls(self):
        llm1 = _FakeLLM()
        done1, out1 = _run(llm1, None)
        n = len(done1)
        assert n > 1, f"分片太少，用例无意义（{n}）"
        assert llm1.map_calls == n, "首轮应每片一次 Map 调用"

        llm2 = _FakeLLM()
        done2, out2 = _run(llm2, _candidates(done1))
        assert llm2.map_calls == 0, "全命中不该再发任何 Map 调用"
        assert done2 == [], "命中片已在库里，不重复落库"
        assert out2 == out1, "命中复用后产出应与首轮逐字节一致"
        assert llm2.stream_inputs == llm1.stream_inputs, "喂给 reduce/format 的输入应逐字节一致"


class TestResumeIdempotent:
    def test_two_resumes_byte_identical(self):
        done1, out1 = _run(_FakeLLM(), None)
        cands = _candidates(done1)
        a = _run(_FakeLLM(), cands)
        b = _run(_FakeLLM(), cands)
        assert a == b
        assert a[1] == out1


# ── 3 第二道门 ───────────────────────────────────────────────────────────────

class TestSecondGateRerunsBadChunk:
    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_bad_cached_result_reruns_only_that_chunk(self, bad):
        done1, out1 = _run(_FakeLLM(), None)
        first = {i: r for i, r, _fp in done1}
        cands = _candidates(done1)
        victim = sorted(cands)[1]
        cands[victim] = {"result": bad, "fingerprint": cands[victim]["fingerprint"]}

        llm2 = _FakeLLM()
        done2, out2 = _run(llm2, cands)
        assert llm2.map_calls == 1, "只有坏片该重跑"
        assert [i for i, _r, _f in done2] == [victim], "只有坏片该重落库"
        assert done2[0][1] == first[victim], "重跑结果应与首轮一致（确定性）"
        assert out2 == out1


# ── 5 失败片不落 checkpoint（唯一屏障在门 2 → 移到上游）─────────────────────

class _FailingLLM(_FakeLLM):
    """内容含 fail_marker 的片抛异常，其余照旧 —— 复现 Map 分片失败路径。"""

    def __init__(self, fail_marker: str):
        super().__init__()
        self.fail_marker = fail_marker

    async def async_chat(self, system, messages, client=None):
        if self.fail_marker in messages[0]["content"]:
            self.map_calls += 1
            raise RuntimeError("simulated upstream failure")
        return await super().async_chat(system, messages, client=client)


class TestFailedChunkNotCheckpointed:
    """失败的 Map 分片不进 checkpoint。

    修复前落的是「空串 + 一个完全合法的指纹」：续跑时 _resume_hit 门 1（形状）与门 3
    （指纹）都过，只有门 2（非空）拦得住 —— 门 2 是唯一屏障。且 save_distill_chunk 是
    ON CONFLICT DO NOTHING，那行空串永久占位，重跑成功也写不进去，全程静默。

    契约：map_results 照旧收该片的空串（失败率判断与 raw_analyses 过滤依赖它），
    只是 on_chunk_done 不被调用。
    """

    TEXT3 = "\n\n".join(
        f"角色第{i}段：角色说了第{i}句话，这里还有角色的别的话。" for i in range(3)
    )

    def _run3(self, llm) -> tuple[list, list[str], list[dict]]:
        d = _make_distiller(llm)
        done: list[tuple[int, str, str]] = []
        pieces: list[str] = []
        events: list[dict] = []
        for piece in d.distill_incremental_stream(
            self.TEXT3, "角色", [], "story",
            on_chunk_done=lambda i, r, fp: done.append((i, r, fp)),
            resume_candidates=None,
        ):
            if isinstance(piece, str):
                pieces.append(piece)
            else:
                assert "error" not in piece, piece
                events.append(piece)
        return done, pieces, events

    def test_failed_chunk_is_not_checkpointed(self, capsys):
        done, _pieces, events = self._run3(_FailingLLM("角色第2段"))

        # 1) 回调只有 2 次，失败片不在其中
        idxs = [i for i, _r, _f in done]
        assert len(done) == 2, f"失败片不该回调落库，实际回调 {idxs}"
        assert 2 not in idxs
        assert all(r.strip() for _i, r, _f in done), "回调结果不该是空串"

        # 2) 失败片仍走完同一条路径 → map_results 仍收到 (2, "")（其字面内容对公开
        #    generator 不可见，用 per-chunk 进度事件代理：3 片都报了进度）。
        currents = sorted(e["current"] for e in events if e.get("status") == "analyzing")
        assert currents == [0, 1, 2, 3], f"3 片（含失败片）都该推进度，实得 {currents}"

        # 3) failures 仍记录该片：失败率判断不受影响（1/3 在容忍范围内 → 继续）
        out = capsys.readouterr().out
        assert "1/3 map chunks failed" in out
        # 4) 跳过落库不静默：点名该片未入 checkpoint、下轮重跑
        assert "Chunk 2 not checkpointed" in out


# ── 4 主路径零回归 ───────────────────────────────────────────────────────────

class TestMainPathUnchanged:
    def test_none_candidates_is_plain_full_run(self):
        a = _run(_FakeLLM(), None)
        b = _run(_FakeLLM(), None)
        assert a == b, "不续跑时两次全跑应完全一致"
        llm = _FakeLLM()
        done, _out = _run(llm, None)
        assert llm.map_calls == len(done) > 1, "不续跑时每片都发调用"
        assert all(r.strip() for _i, r, _f in done)

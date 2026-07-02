"""Tests for affinity delta clamp — enforces prompt-declared per-turn limits in code."""

from __future__ import annotations

from core.affinity_service import _clamp_delta, AffinityService


class TestClampDelta:
    """Pure-function tests for _clamp_delta."""

    def test_large_positive_clamped_to_up_max(self):
        """LLM returns +30 affinity → clamped to +5."""
        result = _clamp_delta(old=50, new=80, up_max=5, down_max=-8)
        assert result == 55, f"Expected 55, got {result}"

    def test_large_negative_clamped_to_down_max(self):
        """LLM returns -30 affinity → clamped to -8."""
        result = _clamp_delta(old=50, new=20, up_max=5, down_max=-8)
        assert result == 42, f"Expected 42, got {result}"

    def test_small_positive_unchanged(self):
        """Normal +3 change passes through."""
        result = _clamp_delta(old=50, new=53, up_max=5, down_max=-8)
        assert result == 53, f"Expected 53, got {result}"

    def test_small_negative_unchanged(self):
        """Normal -3 change passes through."""
        result = _clamp_delta(old=50, new=47, up_max=5, down_max=-8)
        assert result == 47, f"Expected 47, got {result}"

    def test_zero_delta_unchanged(self):
        """Zero change passes through."""
        result = _clamp_delta(old=50, new=50, up_max=5, down_max=-8)
        assert result == 50, f"Expected 50, got {result}"

    def test_none_keeps_old(self):
        """Field missing (None) → old value preserved."""
        result = _clamp_delta(old=50, new=None, up_max=5, down_max=-8)
        assert result == 50, f"Expected 50, got {result}"

    def test_boundary_low_clamped_to_zero(self):
        """Delta clamp result below 0 → floor at 0."""
        result = _clamp_delta(old=2, new=-10, up_max=5, down_max=-8)
        assert result == 0, f"Expected 0, got {result}"

    def test_boundary_high_clamped_to_100(self):
        """Delta clamp result above 100 → ceiling at 100."""
        result = _clamp_delta(old=98, new=110, up_max=5, down_max=-8)
        assert result == 100, f"Expected 100, got {result}"

    def test_guard_drop_extra_conservative(self):
        """Guard下降被限制在 -5（比普通 -8 更保守）。"""
        result = _clamp_delta(old=70, new=40, up_max=8, down_max=-5)
        assert result == 65, f"Expected 65, got {result}"

    def test_guard_rise_fast(self):
        """Guard上升允许 +8。"""
        result = _clamp_delta(old=50, new=65, up_max=8, down_max=-5)
        assert result == 58, f"Expected 58, got {result}"


class TestApplyEvaluationClamp:
    """Integration tests: apply_evaluation with delta clamp."""

    def _make_service(self, affinity=50, trust=30, guard=70) -> AffinityService:
        svc = AffinityService()
        svc.affinity = affinity
        svc.trust = trust
        svc.guard = guard
        return svc

    def test_affinity_up_clamped_to_5(self):
        """LLM returns +30 affinity → clamped to +5."""
        svc = self._make_service(affinity=50)
        svc.apply_evaluation({"affinity": 80, "trust": 30, "guard": 70, "mood": "平静",
                              "inner_voice": "", "mood_emoji": "😊", "importance": 5}, "陌生")
        assert svc.affinity == 55, f"Expected 55, got {svc.affinity}"

    def test_affinity_down_clamped_to_8(self):
        """LLM returns -30 affinity → clamped to -8."""
        svc = self._make_service(affinity=50)
        svc.apply_evaluation({"affinity": 20, "trust": 30, "guard": 70, "mood": "生气",
                              "inner_voice": "", "mood_emoji": "😠", "importance": 5}, "陌生")
        assert svc.affinity == 42, f"Expected 42, got {svc.affinity}"

    def test_normal_delta_unchanged(self):
        """Normal +3 change passes through apply_evaluation."""
        svc = self._make_service(affinity=50)
        svc.apply_evaluation({"affinity": 53, "trust": 30, "guard": 70, "mood": "平静",
                              "inner_voice": "", "mood_emoji": "😊", "importance": 5}, "陌生")
        assert svc.affinity == 53, f"Expected 53, got {svc.affinity}"

    def test_guard_drop_limited_to_5(self):
        """Guard下降限制在 -5。"""
        svc = self._make_service(guard=70)
        svc.apply_evaluation({"affinity": 50, "trust": 30, "guard": 20, "mood": "放松",
                              "inner_voice": "", "mood_emoji": "😌", "importance": 5}, "陌生")
        assert svc.guard == 65, f"Expected 65, got {svc.guard}"

    def test_missing_fields_keep_old_values(self):
        """字段缺失 → 保持旧值（fallback 语义不变）。"""
        svc = self._make_service(affinity=50, trust=30, guard=70)
        svc.apply_evaluation({"mood": "开心", "inner_voice": "嘿嘿", "mood_emoji": "😊", "importance": 5}, "陌生")
        assert svc.affinity == 50, f"Expected 50, got {svc.affinity}"
        assert svc.trust == 30, f"Expected 30, got {svc.trust}"
        assert svc.guard == 70, f"Expected 70, got {svc.guard}"
        assert svc.mood == "开心"

    def test_all_values_combined_clamp(self):
        """Multiple numeric fields all get clamped simultaneously."""
        svc = self._make_service(affinity=40, trust=20, guard=60)
        svc.apply_evaluation({"affinity": 90, "trust": 80, "guard": 10, "mood": "震惊",
                              "inner_voice": "哇", "mood_emoji": "😮", "importance": 7}, "陌生")
        # affinity: 40 + 5 = 45 (not 90)
        assert svc.affinity == 45, f"Expected 45, got {svc.affinity}"
        # trust: 20 + 5 = 25 (not 80)
        assert svc.trust == 25, f"Expected 25, got {svc.trust}"
        # guard: 60 - 5 = 55 (not 10)
        assert svc.guard == 55, f"Expected 55, got {svc.guard}"

    def test_stage_calculated_after_clamped_affinity(self):
        """Stage is recalculated from clamped affinity, not raw LLM value."""
        svc = self._make_service(affinity=88)  # stage=亲近 (73-90)
        svc.apply_evaluation({"affinity": 95, "trust": 30, "guard": 70, "mood": "开心",
                              "inner_voice": "", "mood_emoji": "😊", "importance": 5}, "亲近")
        # affinity clamped to 88 + 5 = 93 → stage should be 心意相通 (91-100)
        assert svc.affinity == 93, f"Expected 93, got {svc.affinity}"
        assert svc.stage == "心意相通", f"Expected 心意相通, got {svc.stage}"
        assert svc.stage_upgraded is True

"""Tests for config management functions in deps."""

from __future__ import annotations

from unittest.mock import mock_open, patch

from web.deps import patch_config


class TestPatchConfig:
    """patch_config updates in-memory config and persists to YAML."""

    @patch("web.deps._config", {"registration": {"mode": "invite_only"}})
    @patch("web.deps._CFG_PATH", "/tmp/test_config.yaml")
    @patch("builtins.open", new_callable=mock_open)
    def test_patch_updates_dict(self, mock_file):
        result = patch_config("registration", {"mode": "open"})
        assert result["registration"]["mode"] == "open"

    @patch("web.deps._config", {"key1": "val1"})
    @patch("web.deps._CFG_PATH", "/tmp/test_config.yaml")
    @patch("builtins.open", new_callable=mock_open)
    def test_patch_adds_new_key(self, mock_file):
        result = patch_config("new_key", "new_val")
        assert result["new_key"] == "new_val"

    @patch("web.deps._config", {"nested": {"a": 1}})
    @patch("web.deps._CFG_PATH", "/tmp/test_config.yaml")
    @patch("builtins.open", new_callable=mock_open)
    def test_patch_returns_copy(self, mock_file):
        result = patch_config("nested", {"a": 2})
        assert result["nested"]["a"] == 2
        # Verify _config was also updated
        from web.deps import _config
        assert _config["nested"]["a"] == 2

    @patch("web.deps._config", {})
    @patch("web.deps._CFG_PATH", "/tmp/test_config.yaml")
    @patch("builtins.open", new_callable=mock_open)
    def test_patch_calls_yaml_dump(self, mock_file):
        patch_config("mode", "test")
        # Verify write was called (yaml.dump writes to the opened file)
        mock_file.assert_called_once_with("/tmp/test_config.yaml", "w", encoding="utf-8")

    @patch("web.deps._config", {})
    @patch("web.deps._CFG_PATH", "/tmp/test_config.yaml")
    @patch("builtins.open", side_effect=PermissionError("denied"))
    def test_patch_handles_write_error(self, mock_file):
        # Should not raise — the function catches and prints errors
        result = patch_config("key", "val")
        assert result["key"] == "val"

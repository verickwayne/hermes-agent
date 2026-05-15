"""Tests for Bug #12905 fixes in agent/anthropic_adapter.py — macOS Keychain support."""

import hashlib
import json
from unittest.mock import patch, MagicMock

from agent.anthropic_adapter import (
    _read_claude_code_credentials_from_keychain,
    read_claude_code_credentials,
)


class TestReadClaudeCodeCredentialsFromKeychain:
    """Bug 4: macOS Keychain support for Claude Code >=2.1.114."""

    def test_returns_none_on_linux(self):
        """Keychain reading is Darwin-only; must return None on other platforms."""
        with patch("agent.anthropic_adapter.platform.system", return_value="Linux"):
            assert _read_claude_code_credentials_from_keychain() is None

    def test_returns_none_on_windows(self):
        with patch("agent.anthropic_adapter.platform.system", return_value="Windows"):
            assert _read_claude_code_credentials_from_keychain() is None

    def test_returns_none_when_security_command_not_found(self):
        """OSError from missing security binary must be handled gracefully."""
        with patch("agent.anthropic_adapter.platform.system", return_value="Darwin"), \
             patch("agent.anthropic_adapter.subprocess.run",
                   side_effect=OSError("security not found")):
            assert _read_claude_code_credentials_from_keychain() is None

    def test_returns_none_on_nonzero_exit_code(self):
        """security returns non-zero when the Keychain entry doesn't exist."""
        with patch("agent.anthropic_adapter.platform.system", return_value="Darwin"), \
             patch("agent.anthropic_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            assert _read_claude_code_credentials_from_keychain() is None

    def test_returns_none_for_empty_stdout(self):
        with patch("agent.anthropic_adapter.platform.system", return_value="Darwin"), \
             patch("agent.anthropic_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            assert _read_claude_code_credentials_from_keychain() is None

    def test_returns_none_for_non_json_payload(self):
        with patch("agent.anthropic_adapter.platform.system", return_value="Darwin"), \
             patch("agent.anthropic_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not valid json", stderr="")
            assert _read_claude_code_credentials_from_keychain() is None

    def test_returns_none_when_password_field_is_missing_claude_ai_oauth(self):
        with patch("agent.anthropic_adapter.platform.system", return_value="Darwin"), \
             patch("agent.anthropic_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"someOtherService": {"accessToken": "tok"}}),
                stderr="",
            )
            assert _read_claude_code_credentials_from_keychain() is None

    def test_returns_none_when_access_token_is_empty(self):
        with patch("agent.anthropic_adapter.platform.system", return_value="Darwin"), \
             patch("agent.anthropic_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"claudeAiOauth": {"accessToken": "", "refreshToken": "x"}}),
                stderr="",
            )
            assert _read_claude_code_credentials_from_keychain() is None

    def test_parses_valid_keychain_entry(self):
        with patch("agent.anthropic_adapter.platform.system", return_value="Darwin"), \
             patch("agent.anthropic_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({
                    "claudeAiOauth": {
                        "accessToken": "kc-access-token-abc",
                        "refreshToken": "kc-refresh-token-xyz",
                        "expiresAt": 9999999999999,
                    }
                }),
                stderr="",
            )
            creds = _read_claude_code_credentials_from_keychain()
            assert creds is not None
            assert creds["accessToken"] == "kc-access-token-abc"
            assert creds["refreshToken"] == "kc-refresh-token-xyz"
            assert creds["expiresAt"] == 9999999999999
            assert creds["source"] == "macos_keychain"

    def test_reads_service_name_from_claude_config_dir(self, monkeypatch):
        custom_dir = "/tmp/hermes-claude-config"
        expected_hash = hashlib.sha256(custom_dir.encode("utf-8")).hexdigest()[:8]
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", custom_dir)
        monkeypatch.setenv("USER", "verickwayne")

        with patch("agent.anthropic_adapter.platform.system", return_value="Darwin"), \
             patch("agent.anthropic_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            assert _read_claude_code_credentials_from_keychain() is None

        mock_run.assert_called_once()
        assert mock_run.call_args.args[0] == [
            "security",
            "find-generic-password",
            "-a",
            "verickwayne",
            "-s",
            f"Claude Code-credentials-{expected_hash}",
            "-w",
        ]

    def test_accepts_raw_oauth_payload_shape(self):
        with patch("agent.anthropic_adapter.platform.system", return_value="Darwin"), \
             patch("agent.anthropic_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({
                    "access_token": "kc-access-token-abc",
                    "refresh_token": "kc-refresh-token-xyz",
                    "expires_at_ms": 9999999999999,
                }),
                stderr="",
            )
            creds = _read_claude_code_credentials_from_keychain()

        assert creds is not None
        assert creds["accessToken"] == "kc-access-token-abc"
        assert creds["refreshToken"] == "kc-refresh-token-xyz"
        assert creds["expiresAt"] == 9999999999999
        assert creds["source"] == "macos_keychain"


class TestReadClaudeCodeCredentialsPriority:
    """Bug 4: Keychain must be checked before the JSON file."""

    def test_keychain_takes_priority_over_json_file(self, tmp_path, monkeypatch):
        """When both Keychain and JSON file have credentials, Keychain wins."""
        # Set up JSON file with "older" token
        json_cred_file = tmp_path / ".claude" / ".credentials.json"
        json_cred_file.parent.mkdir(parents=True)
        json_cred_file.write_text(json.dumps({
            "claudeAiOauth": {
                "accessToken": "json-token",
                "refreshToken": "json-refresh",
                "expiresAt": 9999999999999,
            }
        }))
        monkeypatch.setattr("agent.anthropic_adapter.Path.home", lambda: tmp_path)

        # Mock Keychain to return a "newer" token
        with patch("agent.anthropic_adapter.platform.system", return_value="Darwin"), \
             patch("agent.anthropic_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({
                    "claudeAiOauth": {
                        "accessToken": "keychain-token",
                        "refreshToken": "keychain-refresh",
                        "expiresAt": 9999999999999,
                    }
                }),
                stderr="",
            )
            creds = read_claude_code_credentials()

        # Keychain token should be returned, not JSON file token
        assert creds is not None
        assert creds["accessToken"] == "keychain-token"
        assert creds["source"] == "macos_keychain"

    def test_falls_back_to_json_when_keychain_returns_none(self, tmp_path, monkeypatch):
        """When Keychain has no entry, JSON file is used as fallback."""
        json_cred_file = tmp_path / ".claude" / ".credentials.json"
        json_cred_file.parent.mkdir(parents=True)
        json_cred_file.write_text(json.dumps({
            "claudeAiOauth": {
                "accessToken": "json-fallback-token",
                "refreshToken": "json-refresh",
                "expiresAt": 9999999999999,
            }
        }))
        monkeypatch.setattr("agent.anthropic_adapter.Path.home", lambda: tmp_path)

        with patch("agent.anthropic_adapter.platform.system", return_value="Darwin"), \
             patch("agent.anthropic_adapter.subprocess.run") as mock_run:
            # Simulate Keychain entry not found
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            creds = read_claude_code_credentials()

        assert creds is not None
        assert creds["accessToken"] == "json-fallback-token"
        assert creds["source"] == "claude_code_credentials_file"

    def test_returns_none_when_neither_keychain_nor_json_has_creds(self, tmp_path, monkeypatch):
        """No credentials anywhere — must return None cleanly."""
        monkeypatch.setattr("agent.anthropic_adapter.Path.home", lambda: tmp_path)

        with patch("agent.anthropic_adapter.platform.system", return_value="Darwin"), \
             patch("agent.anthropic_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            creds = read_claude_code_credentials()

        assert creds is None

    def test_reads_credentials_file_from_custom_claude_config_dir(self, tmp_path, monkeypatch):
        custom_dir = tmp_path / "custom-claude-config"
        cred_file = custom_dir / ".credentials.json"
        cred_file.parent.mkdir(parents=True)
        cred_file.write_text(json.dumps({
            "claudeAiOauth": {
                "accessToken": "custom-dir-token",
                "refreshToken": "custom-dir-refresh",
                "expiresAt": 9999999999999,
            }
        }))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(custom_dir))

        with patch("agent.anthropic_adapter.platform.system", return_value="Darwin"), \
             patch("agent.anthropic_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            creds = read_claude_code_credentials()

        assert creds is not None
        assert creds["accessToken"] == "custom-dir-token"
        assert creds["source"] == "claude_code_credentials_file"

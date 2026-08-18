"""Deployment configuration and web-app safety.

These tests cover the behaviour that only shows up once the app is running on a
public URL: where files are written, what the size limits are, and that a
credential can never reach a log, an output file, or a repr.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

import src.config as config_module
from src.config import PipelineConfig
from src.utils.logging import redact

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def reloaded_config(monkeypatch: pytest.MonkeyPatch):
    """Reload src.config under a patched environment."""

    def _load(**env: str):
        for key in (
            "RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "MANAGED_DEPLOYMENT",
            "OUTPUT_DIR", "MAX_UPLOAD_MB", "MAX_DECK_PAGES", "APP_PASSWORD",
            "ALLOW_USER_API_KEY", "REQUIRE_USER_API_KEY",
        ):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(config_module)

    yield _load
    # Leave the module in its unpatched state for other tests.
    for key in ("RAILWAY_ENVIRONMENT", "OUTPUT_DIR", "MAX_UPLOAD_MB"):
        monkeypatch.delenv(key, raising=False)
    importlib.reload(config_module)


class TestDeploymentFiles:
    @pytest.mark.parametrize(
        "filename", ["Procfile", "railway.json", "nixpacks.toml", ".python-version"]
    )
    def test_required_file_exists(self, filename: str) -> None:
        assert (ROOT / filename).exists(), f"{filename} is required to deploy"

    def test_streamlit_config_exists(self) -> None:
        assert (ROOT / ".streamlit" / "config.toml").exists()

    def test_railway_json_is_valid(self) -> None:
        data = json.loads((ROOT / "railway.json").read_text(encoding="utf-8"))
        deploy = data["deploy"]
        assert deploy["healthcheckPath"] == "/_stcore/health"
        assert "$PORT" in deploy["startCommand"]
        assert "0.0.0.0" in deploy["startCommand"]

    def test_start_commands_agree(self) -> None:
        """Procfile and railway.json must not drift apart."""
        procfile = (ROOT / "Procfile").read_text(encoding="utf-8").split("web:", 1)[1].strip()
        railway = json.loads((ROOT / "railway.json").read_text(encoding="utf-8"))
        assert procfile == railway["deploy"]["startCommand"].strip()

    def test_binds_all_interfaces_and_uses_injected_port(self) -> None:
        """A container that binds localhost is unreachable from Railway's proxy."""
        cmd = (ROOT / "Procfile").read_text(encoding="utf-8")
        assert "--server.address=0.0.0.0" in cmd
        assert "--server.port=$PORT" in cmd
        assert "--server.headless=true" in cmd

    def test_secrets_are_not_committed(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert ".env" in gitignore
        assert not (ROOT / ".env").exists() or ".env" in gitignore

    def test_confidential_folders_are_ignored(self) -> None:
        """Uploaded decks and generated summaries must never reach the repo."""
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "input/*" in gitignore
        assert "output/*" in gitignore

    def test_template_directory_is_committed(self) -> None:
        """The canonical structure must ship with the deployment."""
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "templates/" not in gitignore.replace("# templates/", "")
        assert (ROOT / "templates" / "executive_summary_template.json").exists()


class TestManagedDeployment:
    def test_railway_env_redirects_output_to_temp(self, reloaded_config) -> None:
        """A container filesystem is ephemeral; nothing should be written to the repo."""
        cfg = reloaded_config(RAILWAY_ENVIRONMENT="production")
        assert cfg.IS_MANAGED_DEPLOYMENT
        assert ROOT not in cfg.OUTPUT_DIR.parents
        assert "execsummary_output" in str(cfg.OUTPUT_DIR)

    def test_explicit_output_dir_wins(self, reloaded_config, tmp_path: Path) -> None:
        cfg = reloaded_config(RAILWAY_ENVIRONMENT="production", OUTPUT_DIR=str(tmp_path))
        assert cfg.OUTPUT_DIR == tmp_path

    def test_local_runs_still_use_the_repo_output_folder(self, reloaded_config) -> None:
        cfg = reloaded_config()
        assert not cfg.IS_MANAGED_DEPLOYMENT
        assert cfg.OUTPUT_DIR == cfg.PROJECT_ROOT / "output"

    def test_upload_limit_is_configurable(self, reloaded_config) -> None:
        assert reloaded_config(MAX_UPLOAD_MB="25").MAX_UPLOAD_BYTES == 25 * 1024 * 1024

    def test_invalid_limit_falls_back_to_the_default(self, reloaded_config) -> None:
        assert reloaded_config(MAX_UPLOAD_MB="not-a-number").MAX_UPLOAD_BYTES == 80 * 1024 * 1024

    def test_upload_limit_fits_within_streamlit_ceiling(self) -> None:
        """maxUploadSize in config.toml must not be smaller than our own limit."""
        text = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
        line = next(l for l in text.splitlines() if l.strip().startswith("maxUploadSize"))
        streamlit_mb = int(line.split("=")[1].strip())
        assert streamlit_mb >= config_module.MAX_UPLOAD_BYTES / 1024 / 1024


class TestApiKeyHandling:
    def test_override_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server-key-value")
        cfg = PipelineConfig(api_key_override="sk-ant-user-key-value")
        assert cfg.api_key == "sk-ant-user-key-value"

    def test_environment_is_used_when_no_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server-key-value")
        assert PipelineConfig().api_key == "sk-ant-server-key-value"

    def test_blank_override_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server-key-value")
        assert PipelineConfig(api_key_override="   ").api_key == "sk-ant-server-key-value"

    def test_require_user_key_hides_the_server_key(
        self, reloaded_config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server-key-value")
        cfg = reloaded_config(REQUIRE_USER_API_KEY="true")
        assert cfg.PipelineConfig().api_key is None
        assert cfg.PipelineConfig(api_key_override="sk-ant-mine").api_key == "sk-ant-mine"

    def test_key_never_appears_in_repr(self) -> None:
        cfg = PipelineConfig(api_key_override="sk-ant-super-secret-value-123")
        assert "super-secret" not in repr(cfg)

    def test_key_is_redacted_from_logs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-REALKEYVALUE0987654321")
        assert "REALKEYVALUE" not in redact(
            "calling with sk-ant-api03-REALKEYVALUE0987654321"
        )

    def test_ai_unavailable_without_any_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        cfg = PipelineConfig()
        assert cfg.api_key is None
        assert not cfg.ai_available

    def test_key_is_absent_from_generated_artefacts(
        self, tmp_path: Path, sample_pdf_deck: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A credential must never be written into an output file."""
        from src.pipeline import generate_executive_summary

        secret = "sk-ant-api03-MUSTNOTAPPEARINOUTPUT12345"
        monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
        result = generate_executive_summary(
            PipelineConfig(
                deck_path=sample_pdf_deck,
                output_dir=tmp_path / "out",
                use_ai=False, use_vision=False,
                api_key_override=secret,
            )
        )
        for path in (result.pdf_path, result.extracted_json_path, result.analysis_json_path):
            assert path is not None
            blob = path.read_bytes()
            assert secret.encode() not in blob, f"key leaked into {path.name}"
            assert b"MUSTNOTAPPEAR" not in blob


class TestEnvExample:
    def test_documents_every_deployment_variable(self) -> None:
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for var in (
            "ANTHROPIC_API_KEY", "APP_PASSWORD", "ALLOW_USER_API_KEY",
            "REQUIRE_USER_API_KEY", "MAX_UPLOAD_MB", "OUTPUT_DIR",
        ):
            assert var in text, f"{var} is undocumented in .env.example"

    def test_contains_no_real_key(self) -> None:
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip().startswith("ANTHROPIC_API_KEY="):
                value = line.split("=", 1)[1].strip()
                assert not value.startswith("sk-ant-api"), "a real key is in .env.example"


class TestDotenvPrecedence:
    """A project .env must beat a stale machine-wide variable.

    python-dotenv's default is the opposite, which silently bills API calls to
    whichever key happened to be exported in the shell — a failure with no
    symptom other than the wrong account being charged.
    """

    def test_dotenv_is_loaded_with_override(self) -> None:
        source = (ROOT / "src" / "config.py").read_text(encoding="utf-8")
        assert "override=True" in source, ".env would lose to an ambient variable"

    def test_dotenv_path_is_pinned_to_the_project(self) -> None:
        """Loading by search path picks up a .env from whatever cwd the app ran in."""
        source = (ROOT / "src" / "config.py").read_text(encoding="utf-8")
        assert "_DOTENV_PATH" in source
        assert "load_dotenv(_DOTENV_PATH" in source

    def test_env_file_wins_over_shell(self, tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
        from dotenv import load_dotenv

        env_file = tmp_path / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=sk-ant-from-dotenv\n", encoding="utf-8")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-shell")

        load_dotenv(env_file, override=True)
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-dotenv"

    def test_real_env_file_is_git_ignored(self) -> None:
        """The live .env holds a real credential and must never be committed."""
        import subprocess

        env_file = ROOT / ".env"
        if not env_file.exists():
            pytest.skip("no local .env")
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(env_file)], cwd=ROOT, check=False
        )
        assert result.returncode == 0, ".env is NOT git-ignored"

    def test_no_key_is_hardcoded_in_source(self) -> None:
        """A credential in tracked source would survive into the repo."""
        for path in (ROOT / "src").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "sk-ant-api03-" not in text, f"API key literal in {path.name}"
        for name in ("app.py", "cli.py"):
            assert "sk-ant-api03-" not in (ROOT / name).read_text(encoding="utf-8")

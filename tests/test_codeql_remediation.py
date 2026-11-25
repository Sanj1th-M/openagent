"""Focused regression coverage for CodeQL remediation work."""

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from actions import dev_agent
from core import cloud, gemini
from core.llm_client import _validate_llm_url
from dashboard.server import (
    DashboardServer,
    _dashboard_bind_host,
    _https_redirect_target,
    _script_json,
)
from memory import config_manager, credential_store
from providers.llm_openai import (
    OpenAICompatLLM,
    _is_loopback,
    _is_own_hardware,
    _normalise,
    _uses_host,
)
from providers.stt_whisper import WhisperSTT
from providers.tts_elevenlabs import ElevenLabsTTS


def test_provider_url_validation_rejects_ambiguous_authorities() -> None:
    assert _normalise("https://user:pass@example.test") == ""
    assert _normalise("ftp://example.test") == ""
    assert _normalise("https://example.test/?query=1") == ""
    assert _normalise("https://example.test/#fragment") == ""


def test_provider_hostname_matching_requires_an_exact_host() -> None:
    expected = "generativelanguage.googleapis.com"
    assert _uses_host(_normalise(f"https://{expected}/v1"), expected)
    assert not _uses_host(_normalise(f"https://{expected}.attacker.test/v1"), expected)
    assert not _uses_host(_normalise(f"https://attacker.test/{expected}/v1"), expected)


def test_script_json_cannot_end_an_inline_script() -> None:
    value = _script_json("</script><script>alert('xss')</script>")
    assert "</script" not in value.lower()
    assert "\\u003c" in value


def test_dashboard_defaults_to_loopback(monkeypatch) -> None:
    monkeypatch.setattr("dashboard.server.BASE_DIR", __import__("pathlib").Path("missing"))
    assert _dashboard_bind_host() == "127.0.0.1"


def test_https_redirect_keeps_destination_authority_local() -> None:
    from starlette.datastructures import URL

    target = _https_redirect_target(URL("http://attacker.test/path?next=//evil.test"), "192.0.2.1")
    assert target == "https://192.0.2.1:8001/path?next=//evil.test"


def test_new_gemini_keys_are_not_written_to_configuration(monkeypatch) -> None:
    written: list[dict[str, Any]] = []
    monkeypatch.setattr(config_manager, "_read_for_write", lambda: {"gemini_api_key": "old"})
    monkeypatch.setattr(config_manager, "_write_config", written.append)
    monkeypatch.setattr("memory.credential_store.save_gemini_key", lambda value: None)

    config_manager.save_api_keys("new-secret")

    assert written == [{}]


def test_provider_url_validation_blocks_metadata_and_control_chars() -> None:
    assert _normalise("http://169.254.169.254/latest/meta-data") == ""
    assert _normalise("http://[::ffff:169.254.169.254]/latest") == ""
    assert _normalise("http://[fe80::1]:8080/v1") == ""
    assert _normalise("http://0.0.0.0:11434/v1") == ""
    assert _normalise("https://metadata.google.internal/computeMetadata/v1") == ""
    assert _normalise("https://example.test/v1\nX-Test: 1") == ""


def test_provider_loopback_and_lan_detection_handles_ipv6() -> None:
    ipv6_local = _normalise("http://[::1]:11434/v1")
    assert ipv6_local == "http://[::1]:11434/v1"
    assert _is_loopback(ipv6_local)
    assert _is_own_hardware(ipv6_local)
    assert _is_own_hardware("http://192.168.1.40:1234/v1")
    assert not _is_own_hardware("https://api.openai.com/v1")


def test_openai_provider_withholds_keys_on_http_and_rejects_redirects(monkeypatch) -> None:
    llm_http = OpenAICompatLLM(
        {"base_url": "http://example.test/v1", "model": "m", "api_key": "secret-key"}
    )
    assert "Authorization" not in llm_http._headers()
    ok, msg = llm_http.probe(check_vision=False)
    assert not ok
    assert "HTTPS" in msg

    llm_bad = OpenAICompatLLM({"base_url": "ftp://example.test", "model": "m"})
    with pytest.raises(ValueError):
        llm_bad.complete([{"role": "user", "content": "hi"}])

    captured: dict[str, Any] = {}

    class _FakeResp:
        status_code = 302
        text = "Found"

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured["client_kwargs"] = kwargs

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def post(self, url: str, **kwargs: Any) -> _FakeResp:
            captured["headers"] = kwargs.get("headers")
            return _FakeResp()

    llm_https = OpenAICompatLLM(
        {"base_url": "https://example.test/v1", "model": "m", "api_key": "secret-key"}
    )
    monkeypatch.setattr(llm_https._httpx, "Client", _FakeClient)
    with pytest.raises(RuntimeError, match="302"):
        llm_https.complete([{"role": "user", "content": "hi"}])
    assert captured["client_kwargs"]["follow_redirects"] is False
    assert captured["headers"]["Authorization"] == "Bearer secret-key"


def test_whisper_withholds_keys_on_http_and_rejects_redirects(monkeypatch) -> None:
    stt_http = WhisperSTT(
        {"backend": "api", "base_url": "http://example.test/v1", "api_key": "whisper-secret"}
    )
    ok, msg = stt_http.probe()
    assert not ok
    assert "HTTPS" in msg

    seen: dict[str, Any] = {}

    class _FakeResp:
        status_code = 307
        text = "Redirect"

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            seen["client_kwargs"] = kwargs

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def post(self, url: str, **kwargs: Any) -> _FakeResp:
            seen["headers"] = kwargs.get("headers")
            return _FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    with pytest.raises(RuntimeError, match="307"):
        stt_http._api_blocking(b"\x00\x00" * 160, 16000)
    assert seen["client_kwargs"]["follow_redirects"] is False
    assert "Authorization" not in seen["headers"]


@pytest.mark.anyio
async def test_elevenlabs_rejects_path_traversal_voice_id(monkeypatch) -> None:
    tts = ElevenLabsTTS({"api_key": "el-secret", "voice_id": "../admin"})
    monkeypatch.setattr(tts, "_resolve_voice", lambda: None)
    with pytest.raises(RuntimeError, match="voice ID"):
        async for _ in tts.speak("hello"):
            pass


def test_legacy_llm_client_url_validation() -> None:
    assert _validate_llm_url("http://localhost:11434/") == "http://localhost:11434"
    with pytest.raises(ValueError):
        _validate_llm_url("http://169.254.169.254/latest")
    with pytest.raises(ValueError):
        _validate_llm_url("https://user:pass@example.test")


def test_dashboard_bind_host_rejects_unrecognized_values(monkeypatch, tmp_path) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setattr("dashboard.server.BASE_DIR", tmp_path)

    (cfg / "api_keys.json").write_text('{"dashboard_bind_host": "10.0.0.5"}', encoding="utf-8")
    assert _dashboard_bind_host() == "127.0.0.1"

    lan_bind = ".".join(["0"] * 4)
    (cfg / "api_keys.json").write_text(f'{{"dashboard_bind_host": "{lan_bind}"}}', encoding="utf-8")
    assert _dashboard_bind_host() == lan_bind

    (cfg / "api_keys.json").write_text('{"dashboard_bind_host": "::1"}', encoding="utf-8")
    assert _dashboard_bind_host() == "::1"


def test_https_redirect_brackets_ipv6_hosts() -> None:
    from starlette.datastructures import URL

    target = _https_redirect_target(URL("http://[::1]:8000/auto-login?k=1"), "::1")
    assert target == "https://[::1]:8001/auto-login?k=1"


def test_dashboard_auto_login_does_not_reflect_untrusted_key(monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setattr("dashboard.server.BASE_DIR", tmp_path)
    srv = DashboardServer()
    client = TestClient(srv.app)

    payload = "<script>alert(1)</script>"
    resp = client.get("/auto-login", params={"key": payload})
    assert resp.status_code == 200
    assert payload not in resp.text
    assert "Link Expired" in resp.text


def test_dashboard_upload_hides_filesystem_exception_details(monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setattr("dashboard.server.BASE_DIR", tmp_path)
    srv = DashboardServer()
    srv._uploads_dir = tmp_path / "uploads"
    srv._uploads_dir.mkdir()
    srv._tokens["valid-tok"] = 9e18

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise OSError(f"secret disk path {tmp_path}/internal.db failed")

    monkeypatch.setattr("builtins.open", _boom)
    client = TestClient(srv.app)
    resp = client.post(
        "/api/upload",
        headers={"Authorization": "Bearer valid-tok"},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 500
    assert resp.json() == {"error": "Upload failed"}
    assert str(tmp_path) not in resp.text


def test_dev_agent_redacts_common_credentials_and_pairing_uris() -> None:
    sample = (
        'api_key="supersecretvalue" '
        "password=my-db-pass "
        '--token "cli secret token" '
        "Authorization: Bearer tok_12345 "
        "Authorization: Basic dXNlcjpwYXNz "
        "https://alice:hunter2@example.test/repo "
        "AIzaSyDummyKeyValue1234567890 "
        "sk-proj1234567890abcdef "
        "jarvis-cloud://192.0.2.10:7443/topsecretvalue1234567890#abcd "
        'aws_secret_access_key="unterminated-secret'
    )
    scrubbed = dev_agent._redact_credentials(sample)
    for secret in (
        "supersecretvalue",
        "my-db-pass",
        "cli secret token",
        "tok_12345",
        "dXNlcjpwYXNz",
        "alice:hunter2",
        "AIzaSyDummyKeyValue1234567890",
        "sk-proj1234567890abcdef",
        "topsecretvalue1234567890",
        "unterminated-secret",
    ):
        assert secret not in scrubbed

    preview = dev_agent._redacted_preview("api_key=very-long-secret-value-1234567890", 20)
    assert "very-long-secret" not in preview


def test_dev_agent_job_snapshot_scrubs_stored_values() -> None:
    dev_agent._new_job(
        Path("proj"),
        "model",
        "fix api_key=secret-instruction",
        player=None,
    )
    snap = dev_agent._job_snapshot()
    assert "secret-instruction" not in snap["request"]
    assert "[REDACTED]" in snap["request"]


def test_cloud_logging_redacts_pairing_codes_while_operator_output_preserves_them(capsys) -> None:
    pair = cloud.make_pair_code("192.0.2.1", 7443, "supersecretpairingtoken12345", "a" * 64)
    cloud._say(f"log leaked {pair}")
    out_log = capsys.readouterr().out
    assert "supersecretpairingtoken12345" not in out_log
    assert "jarvis-cloud://[REDACTED]" in out_log

    cloud._write_operator_output(pair)
    out_op = capsys.readouterr().out
    assert pair in out_op


def test_gemini_call_logs_no_raw_error_details_or_model_names(monkeypatch, capsys) -> None:
    class _BoomClient:
        class _Models:
            def generate_content(self, **kwargs: Any) -> Any:
                raise RuntimeError("key AIzaFakeSecret123456789012345 failed on host")

        models = _Models()

    monkeypatch.setattr(gemini, "api_key", lambda: "AIzaFakeSecret123456789012345")
    monkeypatch.setattr(gemini, "client", lambda **kwargs: _BoomClient())
    monkeypatch.setattr(gemini, "_provider_rungs", lambda tier: ())
    monkeypatch.setattr(gemini, "usable_rungs", lambda ladder: ("gemini-2.5-flash",))

    assert gemini.call("hello") is None
    out = capsys.readouterr().out
    assert "AIzaFakeSecret" not in out
    assert "gemini-2.5-flash" not in out
    assert "RuntimeError" in out


def test_main_omits_dev_agent_arguments_from_tool_log() -> None:
    import main

    line = main._tool_call_log_line("dev_agent", {"description": "api_key=secret"})
    assert "secret" not in line
    assert "arguments omitted" in line


class _FakeVault:
    def __init__(self, *, fail_write: bool = False, corrupt_readback: bool = False) -> None:
        self.data: dict[str, str] = {}
        self.fail_write = fail_write
        self.corrupt_readback = corrupt_readback

    def get(self, secret_id: str) -> str | None:
        return self.data.get(secret_id)

    def set(self, secret_id: str, value: str) -> None:
        if self.fail_write:
            raise credential_store.CredentialStoreUnavailable("vault offline")
        self.data[secret_id] = "corrupted" if self.corrupt_readback else value

    def delete(self, secret_id: str) -> None:
        self.data.pop(secret_id, None)


def _point_config(monkeypatch, tmp_path: Path) -> Path:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    cfg_file = cfg_dir / "api_keys.json"
    monkeypatch.setattr(config_manager, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", cfg_file)
    return cfg_file


def test_credential_store_reads_env_and_systemd_dir_and_rejects_insecure_keyring(
    monkeypatch, tmp_path
) -> None:
    credential_store.register_external_provider(None)
    monkeypatch.setenv("GEMINI_API_KEY", "env-gemini-secret-12345")
    assert credential_store.get_gemini_key() == "env-gemini-secret-12345"
    monkeypatch.delenv("GEMINI_API_KEY")

    cred_dir = tmp_path / "creds"
    cred_dir.mkdir()
    (cred_dir / "plugin.telegram_remote.bot_token").write_text("tg-token-999", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(cred_dir))
    assert credential_store.get_secret("plugin.telegram_remote.bot_token") == "tg-token-999"

    class _PlaintextKeyring:
        pass

    _PlaintextKeyring.__module__ = "keyrings.alt.file"
    _PlaintextKeyring.__name__ = "PlaintextKeyring"
    assert not credential_store._is_secure_keyring_backend(_PlaintextKeyring())

    monkeypatch.setattr(credential_store, "_windows_credential_manager", lambda: None)
    monkeypatch.setattr(credential_store, "_secure_keyring", lambda: None)
    with pytest.raises(credential_store.CredentialStoreUnavailable):
        credential_store.save_secret("provider.openai_compat@openai.api_key", "sk-secret")


def test_provider_and_plugin_credentials_externalized_and_resolved(monkeypatch, tmp_path) -> None:
    cfg_file = _point_config(monkeypatch, tmp_path)
    vault = _FakeVault()
    credential_store.register_external_provider(vault)
    try:
        config_manager.save_providers(
            mode="pipeline",
            pipeline={"stt": "whisper@local", "llm": "openai_compat@openai", "tts": ""},
            creds={
                "openai_compat@openai": {
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4o-mini",
                    "api_key": "sk-openai-vault-secret",
                },
                "openai_compat@ollama": {
                    "base_url": "http://127.0.0.1:11434/v1",
                    "model": "qwen3:8b",
                    "api_key": "",
                },
            },
        )
        config_manager.save_plugin_config(
            "telegram_remote",
            {"bot_token": "12345:tg-secret", "relay_replies": True},
        )
        config_manager.update_config(
            gemini_api_key="AIzaUpdatedKey1234567890", assistant_name="Atlas"
        )

        raw = json.loads(cfg_file.read_text(encoding="utf-8"))
        dumped = json.dumps(raw)
        assert "sk-openai-vault-secret" not in dumped
        assert "12345:tg-secret" not in dumped
        assert "AIzaUpdatedKey1234567890" not in dumped
        assert raw["providers"]["creds"]["openai_compat@openai"]["api_key"] == {
            "$secret_ref": "provider.openai_compat@openai.api_key"
        }
        assert raw["plugin_config"]["telegram_remote"]["bot_token"] == {
            "$secret_ref": "plugin.telegram_remote.bot_token"
        }

        resolved_prov = config_manager.get_provider_config("openai_compat@openai")
        assert resolved_prov["api_key"] == "sk-openai-vault-secret"
        resolved_tg = config_manager.get_plugin_config("telegram_remote")
        assert resolved_tg["bot_token"] == "12345:tg-secret"
        assert resolved_tg["relay_replies"] is True
        assert config_manager.get_gemini_key() == "AIzaUpdatedKey1234567890"
        assert config_manager.get_assistant_name() == "Atlas"
    finally:
        credential_store.register_external_provider(None)


def test_migration_preserves_plaintext_when_verification_fails_and_clears_on_success(
    monkeypatch, tmp_path
) -> None:
    cfg_file = _point_config(monkeypatch, tmp_path)
    legacy_payload = {
        "gemini_api_key": "legacy-gemini-key-12345",
        "elevenlabs_api_key": "legacy-el-key",
        "providers": {
            "creds": {
                "openai_compat@openai": {
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "legacy-openai-key",
                }
            }
        },
        "plugin_config": {
            "cloud_agent": {"pair_code": "jarvis-cloud://192.0.2.1:7443/secret123#fp"}
        },
    }
    cfg_file.write_text(json.dumps(legacy_payload), encoding="utf-8")

    # 1. Corrupt read-back must abort migration without deleting original config.
    bad_vault = _FakeVault(corrupt_readback=True)
    credential_store.register_external_provider(bad_vault)
    try:
        failed_report = config_manager.migrate_plaintext_credentials()
        assert failed_report["migrated"] == []
        assert len(failed_report["failed"]) == 4
        on_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert on_disk == legacy_payload
    finally:
        credential_store.register_external_provider(None)

    # 2. Verified vault succeeds and replaces/removes plaintext fields atomically.
    good_vault = _FakeVault()
    credential_store.register_external_provider(good_vault)
    try:
        ok_report = config_manager.migrate_plaintext_credentials()
        assert len(ok_report["migrated"]) == 4
        assert ok_report["failed"] == []
        migrated_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
        dumped = json.dumps(migrated_disk)
        for secret in (
            "legacy-gemini-key-12345",
            "legacy-el-key",
            "legacy-openai-key",
            "jarvis-cloud://192.0.2.1:7443/secret123#fp",
        ):
            assert secret not in dumped
        assert config_manager.get_gemini_key() == "legacy-gemini-key-12345"
        assert config_manager.get_elevenlabs_key() == "legacy-el-key"
        assert (
            config_manager.get_provider_config("openai_compat@openai")["api_key"]
            == "legacy-openai-key"
        )
        assert (
            config_manager.get_plugin_config("cloud_agent")["pair_code"]
            == "jarvis-cloud://192.0.2.1:7443/secret123#fp"
        )
    finally:
        credential_store.register_external_provider(None)


def test_all_credential_consumers_use_centralized_storage(monkeypatch, tmp_path) -> None:
    import main
    from actions import (
        code_helper,
        computer_control,
        computer_settings,
        desktop,
        file_processor,
        flight_finder,
        web_search,
        youtube_video,
    )
    from core import tts as core_tts
    from dashboard import server as dash_server

    cfg_file = _point_config(monkeypatch, tmp_path)
    cfg_file.write_text('{"os_system": "linux"}', encoding="utf-8")
    vault = _FakeVault()
    credential_store.register_external_provider(vault)
    try:
        config_manager.save_api_keys("AIzaCentralizedKey1234567890")
        config_manager.save_elevenlabs_key("el-centralized-key")

        assert main._get_api_key() == "AIzaCentralizedKey1234567890"
        assert main._gemini_usable() is True
        assert dash_server._get_gemini_key() == "AIzaCentralizedKey1234567890"
        assert gemini.api_key(refresh=True) == "AIzaCentralizedKey1234567890"
        for mod in (
            code_helper,
            computer_control,
            computer_settings,
            desktop,
            file_processor,
            flight_finder,
            web_search,
            youtube_video,
            dev_agent,
        ):
            assert mod._get_api_key() == "AIzaCentralizedKey1234567890"

        player = core_tts.create_tts_player({"tts_engine": "elevenlabs"})
        assert getattr(player._engine, "api_key", "") == "el-centralized-key"
        assert "memory/credential_store.py" in cloud.MANIFEST

        rt = cloud.Runtime(state_dir=tmp_path / "cloud_state", workspace=tmp_path / "ws")
        rt.apply_sync({"settings": {"gemini_api_key": "AIzaSyncedCloudKey1234567890"}})
        assert config_manager.get_gemini_key() == "AIzaSyncedCloudKey1234567890"
        assert "AIzaSyncedCloudKey1234567890" not in cfg_file.read_text(encoding="utf-8")
    finally:
        credential_store.register_external_provider(None)


def test_gemini_api_key_recovers_after_permission_error(monkeypatch, tmp_path) -> None:
    _point_config(monkeypatch, tmp_path)
    monkeypatch.setattr(gemini, "_cached_key", None)
    state = {"blocked": True}

    def _get_key(*, raise_on_permission: bool = False) -> str | None:
        if state["blocked"]:
            raise config_manager.ConfigPermissionError("denied")
        return "AIzaRecoveredKey1234567890"

    monkeypatch.setattr(config_manager, "get_gemini_key", _get_key)
    assert gemini.api_key() == ""
    state["blocked"] = False
    assert gemini.api_key() == "AIzaRecoveredKey1234567890"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions are not enforced on Windows")
def test_posix_config_permissions_block_unowned_or_unsafe_writes(monkeypatch, tmp_path) -> None:
    cfg_file = _point_config(monkeypatch, tmp_path)
    cfg_file.write_text('{"assistant_name": "Jarvis"}', encoding="utf-8")
    os.chmod(tmp_path / "config", stat.S_IRWXU)
    os.chmod(cfg_file, stat.S_IRUSR | stat.S_IWUSR | stat.S_IROTH)

    assert config_manager.load_api_keys(raise_on_permission=True) == {"assistant_name": "Jarvis"}
    assert stat.S_IMODE(cfg_file.stat().st_mode) == (stat.S_IRUSR | stat.S_IWUSR)

import asyncio
import json
import os
import stat
import threading
import time

import pytest

from dashboard import server as dashboard_server
from providers.llm_openai import OpenAICompatLLM, _normalise, _uses_host


def _route(app, path: str):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)


def test_download_rejects_traversal_and_allows_upload_basename(tmp_path):
    dashboard = dashboard_server.DashboardServer()
    root = tmp_path / "uploads"
    root.mkdir()
    dashboard._uploads_dir = root
    dashboard._tokens["valid"] = time.time()
    (root / "photo.png").write_bytes(b"image")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    download = _route(dashboard.app, "/uploads/{filename}")

    response = asyncio.run(download("../secret.txt", token="valid"))
    assert response.status_code == 404
    response = asyncio.run(download("..\\secret.txt", token="valid"))
    assert response.status_code == 404

    response = asyncio.run(download("photo.png", token="valid"))
    assert response.path == str(root / "photo.png")


def test_download_rejects_symlink_outside_upload_root(tmp_path):
    dashboard = dashboard_server.DashboardServer()
    root = tmp_path / "uploads"
    root.mkdir()
    dashboard._uploads_dir = root
    dashboard._tokens["valid"] = time.time()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        (root / "linked.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this system")

    download = _route(dashboard.app, "/uploads/{filename}")
    response = asyncio.run(download("linked.txt", token="valid"))
    assert response.status_code == 404


def test_upload_does_not_return_filesystem_exception_details(tmp_path):
    from starlette.requests import Request

    assert dashboard_server._UPLOAD_OK, (
        "python-multipart must be installed for dashboard upload routes"
    )
    dashboard = dashboard_server.DashboardServer()
    dashboard._uploads_dir = tmp_path / "uploads"
    dashboard._uploads_dir.mkdir()
    dashboard._tokens["valid"] = time.time()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/upload",
        "headers": [(b"authorization", b"Bearer valid")],
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "scheme": "http",
        "http_version": "1.1",
    }

    class BrokenUpload:
        filename = "upload.txt"

        async def read(self, _size):
            raise OSError("C:\\private\\user\\secret.txt")

    upload = _route(dashboard.app, "/api/upload")
    response = asyncio.run(upload(Request(scope), file=BrokenUpload()))
    assert response.status_code == 500
    assert json.loads(response.body) == {"error": "Upload failed"}
    assert b"secret.txt" not in response.body


def test_auto_login_uses_html_safe_json_for_script_values():
    dashboard = dashboard_server.DashboardServer()
    key = "');alert(1)//</script>"
    dashboard._pending_keys[key] = time.time() + 60
    login = _route(dashboard.app, "/auto-login")

    response = asyncio.run(login(key=key))
    body = response.body.decode("utf-8")
    assert "\\u003c/script\\u003e" in body
    assert "sessionStorage.setItem('jarvis_key',\"');alert(1)//\\u003c/script\\u003e\")" in body
    assert "');alert(1)//</script>');" not in body


def test_provider_detection_matches_parsed_hostname_only():
    assert _uses_host(
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "generativelanguage.googleapis.com",
    )
    assert not _uses_host(
        "https://generativelanguage.googleapis.com.attacker.test/v1",
        "generativelanguage.googleapis.com",
    )
    assert not _uses_host(
        "https://attacker.test/?host=generativelanguage.googleapis.com",
        "generativelanguage.googleapis.com",
    )


def test_spoofed_gemini_host_does_not_select_gemini_behavior(monkeypatch):
    from memory import config_manager

    monkeypatch.setattr(config_manager, "get_gemini_key", lambda: "stored-secret")
    llm = OpenAICompatLLM(
        {
            "base_url": "https://generativelanguage.googleapis.com.attacker.test/v1",
            "model": "gemini-3.5-flash",
            "api_key": "",
        }
    )
    assert llm.key == ""
    assert "reasoning_effort" not in llm._body([], None, False, None)


def test_gemini_key_reuse_requires_https(monkeypatch):
    from memory import config_manager

    monkeypatch.setattr(config_manager, "get_gemini_key", lambda: "stored-secret")
    http_llm = OpenAICompatLLM(
        {
            "base_url": "http://GENERATIVELANGUAGE.GOOGLEAPIS.COM/v1beta/openai",
            "model": "gemini-3.5-flash",
            "api_key": "",
        }
    )
    https_llm = OpenAICompatLLM(
        {
            "base_url": "https://GENERATIVELANGUAGE.GOOGLEAPIS.COM/v1beta/openai",
            "model": "gemini-3.5-flash",
            "api_key": "",
        }
    )

    assert http_llm.key == ""
    assert "Authorization" not in http_llm._headers()
    assert https_llm.key == "stored-secret"
    assert https_llm._headers()["Authorization"] == "Bearer stored-secret"

    http_custom = OpenAICompatLLM(
        {
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "local-model",
            "api_key": "explicitly-configured-secret",
        }
    )
    assert http_custom.base == "http://127.0.0.1:11434/v1"
    assert "Authorization" not in http_custom._headers()


@pytest.mark.parametrize(
    "url",
    [
        "ftp://generativelanguage.googleapis.com",
        "file:///tmp/provider",
        "javascript://generativelanguage.googleapis.com",
        "https://host:bad/v1",
        "https:///missing-host",
        "https://user:password@example.test/v1",
    ],
)
def test_provider_url_rejects_invalid_schemes_and_authorities(url):
    assert _normalise(url) == ""


def test_provider_redirect_does_not_forward_authorization(monkeypatch):
    import httpx

    llm = OpenAICompatLLM(
        {
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": "gemini-3.5-flash",
            "api_key": "test-stored-key",
        }
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            307,
            headers={"Location": "https://attacker.test/collect"},
            json={"choices": []},
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def client_without_redirects(*, timeout, trust_env, follow_redirects):
        assert follow_redirects is False
        return real_client(
            transport=transport,
            timeout=timeout,
            trust_env=trust_env,
            follow_redirects=follow_redirects,
        )

    monkeypatch.setattr(httpx, "Client", client_without_redirects)
    with pytest.raises(RuntimeError, match="307"):
        llm.complete([{"role": "user", "content": "hello"}])

    assert len(requests) == 1
    assert requests[0].url.host == "generativelanguage.googleapis.com"
    assert requests[0].headers["Authorization"] == "Bearer test-stored-key"


@pytest.mark.parametrize("stream", [False, True])
def test_provider_chat_rejects_redirect_responses(monkeypatch, stream):
    import httpx

    llm = OpenAICompatLLM(
        {"base_url": "https://provider.example/v1", "model": "test-model", "api_key": "test-key"}
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            307,
            headers={"Location": "https://attacker.test/collect"},
            json={"choices": []},
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client_without_redirects(*, timeout, trust_env, follow_redirects):
        assert follow_redirects is False
        return real_client(
            transport=transport,
            timeout=timeout,
            trust_env=trust_env,
            follow_redirects=follow_redirects,
        )

    monkeypatch.setattr(httpx, "AsyncClient", client_without_redirects)

    async def collect():
        return [item async for item in llm.chat([], stream=stream)]

    with pytest.raises(RuntimeError, match="307"):
        asyncio.run(collect())
    assert len(requests) == 1
    assert requests[0].url.host == "provider.example"


def test_whisper_api_key_is_not_sent_over_http(monkeypatch):
    import httpx

    from providers.stt_whisper import WhisperSTT

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"text": "recognized"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def client_without_redirects(*, timeout, trust_env, follow_redirects):
        assert follow_redirects is False
        return real_client(
            transport=transport,
            timeout=timeout,
            trust_env=trust_env,
            follow_redirects=follow_redirects,
        )

    monkeypatch.setattr(httpx, "Client", client_without_redirects)
    plain = WhisperSTT(
        {"backend": "api", "base_url": "http://127.0.0.1:8000/v1", "api_key": "stored-whisper-key"}
    )
    secure = WhisperSTT(
        {"backend": "api", "base_url": "https://speech.example/v1", "api_key": "stored-whisper-key"}
    )

    assert plain.probe() == (False, "Whisper API keys are sent only to HTTPS addresses.")
    assert plain._api_blocking(b"\0" * 3200, 16000) == "recognized"
    assert secure._api_blocking(b"\0" * 3200, 16000) == "recognized"
    assert len(requests) == 2
    assert "Authorization" not in requests[0].headers
    assert requests[1].headers["Authorization"] == "Bearer stored-whisper-key"


def test_whisper_rejects_redirect_responses(monkeypatch):
    import httpx

    from providers.stt_whisper import WhisperSTT

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            307,
            headers={"Location": "https://attacker.test/collect"},
            json={"text": "unexpected success"},
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def client_without_redirects(*, timeout, trust_env, follow_redirects):
        assert follow_redirects is False
        return real_client(
            transport=transport,
            timeout=timeout,
            trust_env=trust_env,
            follow_redirects=follow_redirects,
        )

    monkeypatch.setattr(httpx, "Client", client_without_redirects)
    whisper = WhisperSTT(
        {"backend": "api", "base_url": "https://speech.example/v1", "api_key": "test-key"}
    )

    with pytest.raises(RuntimeError, match="307"):
        whisper._api_blocking(b"\0" * 3200, 16000)
    assert len(requests) == 1
    assert requests[0].url.host == "speech.example"


def test_redirect_keeps_local_https_authority():
    from starlette.datastructures import URL

    target = URL(
        dashboard_server._https_redirect_target(
            URL("http://attacker.test//external.example/path?next=https://other.test"),
            "192.0.2.10",
        )
    )
    assert target.scheme == "https"
    assert target.hostname == "192.0.2.10"
    assert target.port == dashboard_server.SECURE_PORT
    assert target.path == "//external.example/path"
    assert target.query == "next=https://other.test"


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes are not portable to Windows")
def test_config_is_written_with_owner_only_permissions(tmp_path, monkeypatch):
    from memory import config_manager

    config_dir = tmp_path / "config"
    config_file = config_dir / "api_keys.json"
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_file)

    config_manager._write_config({"gemini_api_key": "example-secret"})

    assert json.loads(config_file.read_text(encoding="utf-8")) == {
        "gemini_api_key": "example-secret"
    }
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes are not portable to Windows")
def test_loading_existing_config_restricts_permissions_before_read(tmp_path, monkeypatch):
    from memory import config_manager

    config_dir = tmp_path / "config"
    config_dir.mkdir(mode=0o755)
    config_file = config_dir / "api_keys.json"
    config_file.write_text(json.dumps({"gemini_api_key": "legacy-secret"}), encoding="utf-8")
    config_dir.chmod(0o755)
    config_file.chmod(0o644)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_file)

    assert config_manager.load_api_keys() == {"gemini_api_key": "legacy-secret"}
    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes are not portable to Windows")
def test_loading_config_fails_closed_when_permissions_cannot_be_restricted(
    tmp_path, monkeypatch, capsys
):
    from memory import config_manager

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "api_keys.json"
    config_file.write_text(json.dumps({"gemini_api_key": "legacy-secret"}), encoding="utf-8")
    config_dir.chmod(0o755)
    config_file.chmod(0o644)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_file)

    def deny_chmod(*_args, **_kwargs):
        raise PermissionError("permission denied")

    monkeypatch.setattr(config_manager.os, "chmod", deny_chmod)

    assert config_manager.load_api_keys() == {}
    with pytest.raises(config_manager.ConfigPermissionError):
        config_manager.load_api_keys(raise_on_permission=True)
    assert "refusing to load settings" in capsys.readouterr().out


def test_missing_config_is_still_reported_as_missing_not_permission(tmp_path, monkeypatch):
    from memory import config_manager

    config_dir = tmp_path / "config"
    config_file = config_dir / "api_keys.json"
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_file)

    assert config_manager.load_api_keys(raise_on_permission=True) == {}
    assert config_manager.get_gemini_key(raise_on_permission=True) is None


def test_provider_updates_abort_when_existing_settings_cannot_be_secured(tmp_path, monkeypatch):
    from memory import config_manager

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "api_keys.json"
    original = json.dumps(
        {
            "providers": {
                "mode": "pipeline",
                "creds": {"openai_compat": {"api_key": "existing-provider-secret"}},
            }
        }
    )
    config_file.write_text(original, encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_file)
    monkeypatch.setattr(config_manager, "_restrict_existing_config_permissions", lambda: False)

    with pytest.raises(PermissionError, match="refusing to overwrite"):
        config_manager.save_provider_mode("gemini")

    assert config_file.read_text(encoding="utf-8") == original


def test_gemini_key_reader_does_not_bypass_config_permission_refusal(tmp_path, monkeypatch):
    from core import gemini
    from memory import config_manager

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "api_keys.json"
    config_file.write_text(json.dumps({"gemini_api_key": "disk-sensitive-key"}), encoding="utf-8")
    monkeypatch.setattr(gemini, "_KEY_FILE", config_file)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_file)
    monkeypatch.setattr(gemini, "_cached_key", "cached-sensitive-key")
    monkeypatch.setattr(config_manager, "_restrict_existing_config_permissions", lambda: False)

    assert gemini.api_key() == ""
    assert gemini._cached_key is None
    assert gemini.api_key_error() == "permission"


def test_gemini_key_reader_retries_after_permission_is_restored(tmp_path, monkeypatch):
    from core import gemini
    from memory import config_manager

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "api_keys.json"
    config_file.write_text(json.dumps({"gemini_api_key": "restored-key"}), encoding="utf-8")
    monkeypatch.setattr(gemini, "_KEY_FILE", config_file)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_file)
    monkeypatch.setattr(gemini, "_cached_key", "stale-key")
    restricted = iter((False, True))
    monkeypatch.setattr(
        config_manager, "_restrict_existing_config_permissions", lambda: next(restricted)
    )

    assert gemini.api_key() == ""
    assert gemini.api_key() == "restored-key"
    assert gemini.api_key_error() is None


def test_gemini_status_reports_permission_refusal(monkeypatch):
    from main import _gemini_status
    from memory import config_manager

    def refuse(**_kwargs):
        raise config_manager.ConfigPermissionError("refused")

    monkeypatch.setattr(config_manager, "load_api_keys", refuse)

    assert _gemini_status() == (
        False,
        "SYS: Gemini settings cannot be read safely: permission denied.",
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and modes are not portable to Windows")
def test_loading_protected_config_does_not_chmod_read_only_paths(tmp_path, monkeypatch):
    from memory import config_manager

    config_dir = tmp_path / "config"
    config_dir.mkdir(mode=0o700)
    config_file = config_dir / "api_keys.json"
    config_file.write_text(json.dumps({"gemini_api_key": "legacy-secret"}), encoding="utf-8")
    config_dir.chmod(0o500)
    config_file.chmod(0o400)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_file)

    def deny_chmod(*_args, **_kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(config_manager.os, "chmod", deny_chmod)

    assert config_manager.load_api_keys() == {"gemini_api_key": "legacy-secret"}


class _RecordingPlayer:
    def __init__(self):
        self.logs = []
        self.snapshots = []

    def write_log(self, message):
        self.logs.append(message)

    def show_code_job(self, snapshot):
        self.snapshots.append(snapshot)


def _assert_secrets_absent(secrets, *outputs):
    combined = "\n".join(str(output) for output in outputs)
    for secret in secrets:
        assert secret not in combined


def test_dev_agent_log_redacts_credentials_before_every_output(capsys, monkeypatch, tmp_path):
    from actions import dev_agent

    monkeypatch.setattr(dev_agent, "_JOB", {})
    monkeypatch.setattr(dev_agent, "_player_ref", None)
    player = _RecordingPlayer()
    request_secret = "request_api_key_value"
    dev_agent._new_job(tmp_path, "test-model", f"use api_key={request_secret}", player)
    dev_agent._log(
        player,
        "api_key=sk-abcdefghijklmnopqrstuvwxyz Bearer abc.def.ghi "
        "access_token: Bearer xyz.def.ghi Authorization: Basic dXNlcjpwYXNz "
        "AWS_SECRET_ACCESS_KEY=aws_secret_value --secret-access-key aws_cli_secret "
        "--aws-secret-access-key aws_full_cli_secret "
        '{"password": "hunter2", "secret": "a value with spaces", '
        '"token": "plain_token_value", "authToken": "camel_token_value"} '
        'PASSWORD=prefix" secret tail" '
        "PASSWORD=,env_private_value "
        "PASSWORD=prefix\\ assignment_escaped_suffix "
        "PASSWORD=\\\ncontinuation_secret_42 command "
        'PASSWORD="quoted-prefix\\\nquoted_continuation_secret" '
        '--password cli-password --api-key "cli key secret" ordinary-argument '
        "--password ,cli_private_value "
        '--password cli-prefix" cli mixed secret" '
        "--password cli-prefix\\ cli_escaped_suffix "
        "--password \\\ncli_continuation_secret_43 command "
        'JSON fields: {"password": "json_secret_value", "status": "failed"} '
        'escaped JSON: {\\"password\\": \\"escaped secret value\\"} '
        "postgresql://db-user:db-password@db.example.test/app "
        "postgresql://db.example.test/app "
        "https://example.test?email=alice@example.org "
        "https://url-user:url-password@example.test/path "
        "https://alice:credential@sensitive_tail@example.test/path "
        'PASSWORD="unterminated quoted_secret_suffix\n'
        '--password "unterminated_cli_secret_suffix',
    )
    output = capsys.readouterr().out
    assert "postgresql://db.example.test/app" in output + " ".join(player.logs)
    assert "https://example.test?email=alice@example.org" in output + " ".join(player.logs)
    assert '"status": "failed"}' in output + " ".join(player.logs)
    _assert_secrets_absent(
        [
            "sk-abcdefghijklmnopqrstuvwxyz",
            "abc.def.ghi",
            "xyz.def.ghi",
            "dXNlcjpwYXNz",
            "aws_secret_value",
            "aws_cli_secret",
            "aws_full_cli_secret",
            "hunter2",
            "json_secret_value",
            "a value with spaces",
            "plain_token_value",
            "camel_token_value",
            "secret tail",
            "env_private_value",
            "assignment_escaped_suffix",
            "continuation_secret_42",
            "quoted_continuation_secret",
            "cli-password",
            "cli_private_value",
            "cli key secret",
            "cli mixed secret",
            "cli_escaped_suffix",
            "cli_continuation_secret_43",
            "escaped secret value",
            "db-user",
            "db-password",
            "url-user",
            "url-password",
            "sensitive_tail",
            "quoted_secret_suffix",
            "unterminated_cli_secret_suffix",
            request_secret,
        ],
        output,
        player.logs,
        json.dumps(dev_agent._job_snapshot()),
        json.dumps(player.snapshots),
    )


def test_dev_agent_redacts_credentials_before_preview_truncation():
    from actions import dev_agent

    quoted_tail = "quoted_secret_after_preview_boundary"
    output = "x" * 570 + f' password="safe-prefix {quoted_tail}' + ("z" * 40) + '"'

    preview = dev_agent._redacted_preview(output, 600)

    assert len(preview) <= 600
    assert quoted_tail not in preview
    assert "[REDACTED]" in preview


def test_dev_agent_redacts_quoted_credentials_followed_by_long_escape_run():
    from actions import dev_agent

    secret = "sensitive_password_value"
    diagnostic = f'password="{secret}"' + ("\\" * 32)

    redacted = dev_agent._redact_credentials(diagnostic)

    assert secret not in redacted
    assert "[REDACTED]" in redacted


def test_settings_toggles_report_permission_refusals(monkeypatch):
    from types import SimpleNamespace

    from gui.window import MainWindow
    from memory import config_manager

    messages = []
    ui = SimpleNamespace(
        _log=SimpleNamespace(append_log=messages.append),
        _refresh_hud_btn=lambda: messages.append("HUD refreshed"),
        _refresh_talk_btns=lambda: messages.append("PTT refreshed"),
    )

    def refuse_save(*args):
        raise PermissionError("protected settings")

    monkeypatch.setattr(config_manager, "get_hud_style", lambda: "face")
    monkeypatch.setattr(config_manager, "save_hud_style", refuse_save)
    MainWindow._toggle_hud_style(ui)
    assert "HUD style setting not saved" in messages[0]
    assert "HUD refreshed" in messages

    messages.clear()
    monkeypatch.setattr(config_manager, "get_push_to_talk_enabled", lambda: False)
    monkeypatch.setattr(config_manager, "save_push_to_talk_enabled", refuse_save)
    MainWindow._toggle_ptt(ui)
    assert "Push-to-talk setting not saved" in messages[0]
    assert "PTT refreshed" in messages


def test_ptt_failure_still_refreshes_when_rollback_save_is_denied(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from gui.window import MainWindow
    from memory import config_manager

    messages = []
    apply_shortcut = Mock()
    refresh_buttons = Mock()
    ui = SimpleNamespace(
        _log=SimpleNamespace(append_log=messages.append),
        on_push_to_talk=lambda _enabled: (_ for _ in ()).throw(RuntimeError("hotkey unavailable")),
        _apply_ptt_shortcut=apply_shortcut,
        _refresh_talk_btns=refresh_buttons,
    )
    save = Mock(side_effect=[None, PermissionError("protected settings")])
    monkeypatch.setattr(config_manager, "get_push_to_talk_enabled", lambda: False)
    monkeypatch.setattr(config_manager, "save_push_to_talk_enabled", save)

    MainWindow._toggle_ptt(ui)

    assert save.call_count == 2
    apply_shortcut.assert_called_once_with(False)
    refresh_buttons.assert_called_once_with()
    assert any("rollback was not saved" in message for message in messages)


def test_write_config_flushes_and_syncs_temp_file(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from memory import config_manager

    config_dir = tmp_path / "config"
    config_file = config_dir / "api_keys.json"
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_file)
    monkeypatch.setattr(config_manager, "_sync_config_directory", lambda: None)
    fsync = Mock()
    monkeypatch.setattr(config_manager.os, "fsync", fsync)

    config_manager._write_config({"gemini_api_key": "example-secret"})

    assert json.loads(config_file.read_text(encoding="utf-8")) == {
        "gemini_api_key": "example-secret"
    }
    fsync.assert_called_once()


def test_config_directory_sync_closes_fd_when_fsync_fails(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from memory import config_manager

    opened = Mock(return_value=42)
    fsync = Mock(side_effect=OSError("directory sync unsupported"))
    close = Mock()
    fake_os = SimpleNamespace(name="posix", O_RDONLY=0, open=opened, fsync=fsync, close=close)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_manager, "os", fake_os)

    config_manager._sync_config_directory()

    opened.assert_called_once_with(tmp_path, 0)
    fsync.assert_called_once_with(42)
    close.assert_called_once_with(42)


def test_config_directory_sync_is_skipped_on_windows(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from memory import config_manager

    opened = Mock()
    fake_os = SimpleNamespace(name="nt", O_RDONLY=0, open=opened, fsync=Mock(), close=Mock())
    monkeypatch.setattr(config_manager, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_manager, "os", fake_os)

    config_manager._sync_config_directory()

    opened.assert_not_called()


def test_dev_agent_tool_arguments_are_omitted_from_dispatch_log():
    from main import _tool_call_log_line

    secret = "dispatch_api_key_secret"
    args = {"description": f"configure api_key={secret}", "mode": "build"}

    log_line = _tool_call_log_line("dev_agent", args)

    assert "arguments omitted" in log_line
    assert secret not in log_line
    assert args["description"].endswith(secret)


def test_dev_agent_output_summary_never_contains_project_output(capsys, monkeypatch):
    from actions import dev_agent

    monkeypatch.setattr(dev_agent, "_JOB", {"started": time.time(), "steps": [], "finished": None})
    monkeypatch.setattr(dev_agent, "_player_ref", None)
    player = _RecordingPlayer()
    secret_suffix = "preview_secret_suffix"
    summary = dev_agent._output_log_summary()
    assert summary == "Project output captured."
    dev_agent._log(player, summary)

    output = capsys.readouterr().out
    _assert_secrets_absent([secret_suffix], output, player.logs, dev_agent._JOB["steps"])


def test_dev_agent_run_command_diagnostic_omits_credentials(capsys, monkeypatch, tmp_path):
    from actions import dev_agent

    observed = []

    def fake_run(parts, **kwargs):
        observed.extend(parts)
        return dev_agent.subprocess.CompletedProcess(parts, 0, "ok", "")

    monkeypatch.setattr(dev_agent.subprocess, "run", fake_run)
    secret = "command_password_123"
    command = f"python main.py --password {secret}"

    assert dev_agent._run_project(command, tmp_path) == "STDOUT:\nok"
    output = capsys.readouterr().out
    assert "Running project command." in output
    assert secret not in output
    assert secret in observed


def test_dev_agent_failed_job_results_are_redacted(tmp_path, monkeypatch):
    from actions import dev_agent

    monkeypatch.setattr(dev_agent, "_job_lock", threading.Lock())
    monkeypatch.setattr(dev_agent, "_JOB", {})
    monkeypatch.setattr(dev_agent, "_player_ref", None)
    player = _RecordingPlayer()
    dev_agent._new_job(tmp_path, "test-model", "test request", player)
    assert dev_agent._job_lock.acquire(blocking=False)

    completed = threading.Event()
    spoken = []

    def fail_job():
        raise RuntimeError(
            "access_token: Bearer failure_token_123 "
            "AWS_SECRET_ACCESS_KEY=failure_aws_secret "
            "Authorization: Basic ZmFpbHVyZTp0b2tlbg== "
            "https://failure-user:failure-password@example.test"
        )

    dev_agent._run_job(
        fail_job,
        tmp_path.name,
        "test-model",
        speak=lambda message: (spoken.append(message), completed.set()),
        player=player,
    )

    assert completed.wait(5), "background job did not finish"
    result = dev_agent._job_snapshot()["result"]
    _assert_secrets_absent(
        [
            "failure_token_123",
            "failure_aws_secret",
            "ZmFpbHVyZTp0b2tlbg==",
            "failure-user",
            "failure-password",
        ],
        result,
        spoken,
        player.logs,
        json.dumps(dev_agent._job_snapshot()),
        json.dumps(player.snapshots),
    )


def test_dev_agent_successful_job_redacts_result_and_notification(capsys, tmp_path, monkeypatch):
    from actions import dev_agent

    monkeypatch.setattr(dev_agent, "_job_lock", threading.Lock())
    monkeypatch.setattr(dev_agent, "_JOB", {})
    monkeypatch.setattr(dev_agent, "_player_ref", None)
    player = _RecordingPlayer()
    dev_agent._new_job(tmp_path, "test-model", "test request", player)
    assert dev_agent._job_lock.acquire(blocking=False)

    completed = threading.Event()
    spoken = []
    secrets = [
        "success_token_123",
        "success_aws_secret",
        "success_api_key_1234567890",
        "success-user",
        "success-password",
    ]
    sensitive_result = (
        "access_token: success_token_123 AWS_SECRET_ACCESS_KEY=success_aws_secret "
        "api_key=success_api_key_1234567890 "
        "https://success-user:success-password@example.test"
    )

    def succeed_job():
        dev_agent._log(player, sensitive_result)
        return sensitive_result

    dev_agent._run_job(
        succeed_job,
        tmp_path.name,
        "test-model",
        speak=lambda message: (spoken.append(message), completed.set()),
        player=player,
    )

    assert completed.wait(5), "background job did not finish"
    result = dev_agent._job_snapshot()["result"]
    output = capsys.readouterr().out
    _assert_secrets_absent(
        secrets,
        result,
        spoken,
        output,
        player.logs,
        json.dumps(dev_agent._job_snapshot()),
        json.dumps(player.snapshots),
    )


def test_gemini_api_key_reloads_after_config_file_is_created(tmp_path, monkeypatch):
    from core import gemini
    from memory import config_manager

    config_file = tmp_path / "config" / "api_keys.json"
    config_file.parent.mkdir()
    monkeypatch.setattr(gemini, "_KEY_FILE", config_file)
    monkeypatch.setattr(gemini, "_cached_key", None)
    monkeypatch.setattr(gemini, "_last_key_error", None)
    monkeypatch.setattr(config_manager, "_restrict_existing_config_permissions", lambda: True)

    assert gemini.api_key() == ""
    assert gemini._cached_key is None

    config_file.write_text(
        json.dumps({"gemini_api_key": "restored-gemini-key"}),
        encoding="utf-8",
    )
    assert gemini.api_key() == "restored-gemini-key"


def test_gemini_api_key_handles_permission_error_from_exists(monkeypatch):
    from core import gemini
    from memory import config_manager

    class InaccessibleConfigPath:
        def exists(self):
            raise PermissionError("access denied")

    monkeypatch.setattr(gemini, "_KEY_FILE", InaccessibleConfigPath())
    monkeypatch.setattr(gemini, "_cached_key", "stale-key")
    monkeypatch.setattr(gemini, "_last_key_error", None)
    monkeypatch.setattr(config_manager, "_restrict_existing_config_permissions", lambda: True)

    assert gemini.api_key() == ""
    assert gemini.api_key_error() == "permission"
    assert gemini._cached_key is None

import asyncio
import json
import os
import stat
import time

import pytest

from dashboard import server as dashboard_server
from providers.llm_openai import OpenAICompatLLM, _uses_host


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


def test_spoofed_gemini_host_does_not_select_gemini_behavior():
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
    assert https_llm.key == "stored-secret"


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


def test_dev_agent_log_redacts_common_credentials(capsys):
    from actions.dev_agent import _log

    _log(
        None,
        "api_key=sk-abcdefghijklmnopqrstuvwxyz Bearer abc.def.ghi "
        '{"password": "hunter2", "secret": "a value with spaces"}',
    )
    output = capsys.readouterr().out
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in output
    assert "Bearer abc.def.ghi" not in output
    assert "hunter2" not in output
    assert "a value with spaces" not in output
    assert output.count("[REDACTED]") >= 4

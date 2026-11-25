"""Focused regression coverage for CodeQL remediation work."""

from typing import Any

from dashboard.server import _dashboard_bind_host, _https_redirect_target, _script_json
from memory import config_manager
from providers.llm_openai import _normalise, _uses_host


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

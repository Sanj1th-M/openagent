"""Regression coverage for the dashboard's authenticated file-download boundary."""

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.responses import FileResponse, JSONResponse

from dashboard.server import DashboardServer


def _route(server: DashboardServer, path: str):
    return next(route.endpoint for route in server.app.routes if route.path == path)


@pytest.fixture
def download_server(tmp_path: Path) -> DashboardServer:
    server = DashboardServer()
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    server._uploads_dir = uploads
    server._tokens["valid-token"] = time.time()
    return server


def test_download_returns_an_existing_upload(download_server: DashboardServer) -> None:
    (download_server._uploads_dir / "photo.png").write_bytes(b"image")

    response = asyncio.run(
        _route(download_server, "/uploads/{filename}")("photo.png", token="valid-token")
    )

    assert isinstance(response, FileResponse)
    assert Path(response.path).resolve() == (download_server._uploads_dir / "photo.png").resolve()


def test_download_requires_a_live_token(download_server: DashboardServer) -> None:
    response = asyncio.run(_route(download_server, "/uploads/{filename}")("photo.png"))

    assert isinstance(response, JSONResponse)
    assert response.status_code == 401


@pytest.mark.parametrize(
    "filename",
    ["../secret.txt", r"..\\secret.txt", "/etc/passwd", r"C:\\secret.txt", "", "..", "bad\x00.txt"],
)
def test_download_rejects_path_like_or_malformed_names(
    download_server: DashboardServer, filename: str
) -> None:
    response = asyncio.run(
        _route(download_server, "/uploads/{filename}")(filename, token="valid-token")
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 404


def test_download_rejects_url_encoded_traversal(download_server: DashboardServer) -> None:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=download_server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/uploads/%2e%2e%2fsecret.txt?token=valid-token")

    assert asyncio.run(request()).status_code == 404


def test_download_rejects_an_escaping_symlink(
    download_server: DashboardServer, tmp_path: Path
) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = download_server._uploads_dir / "linked.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this system")

    response = asyncio.run(
        _route(download_server, "/uploads/{filename}")("linked.txt", token="valid-token")
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 404


def test_download_rejects_an_in_directory_symlink(download_server: DashboardServer) -> None:
    target = download_server._uploads_dir / "photo.png"
    target.write_bytes(b"image")
    link = download_server._uploads_dir / "linked.png"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this system")

    response = asyncio.run(
        _route(download_server, "/uploads/{filename}")("linked.png", token="valid-token")
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 404


@pytest.mark.parametrize("scenario", ["broken", "loop", "directory"])
def test_download_rejects_non_file_symlinks(
    download_server: DashboardServer, scenario: str
) -> None:
    link = download_server._uploads_dir / "linked.txt"
    try:
        if scenario == "broken":
            link.symlink_to("missing.txt")
        elif scenario == "loop":
            link.symlink_to(link.name)
        else:
            link.symlink_to(".", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this system")

    response = asyncio.run(
        _route(download_server, "/uploads/{filename}")("linked.txt", token="valid-token")
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 404


def test_download_handles_upload_directory_resolution_failure(
    download_server: DashboardServer,
) -> None:
    original_resolve = Path.resolve

    def fail_upload_root(path: Path, *args, **kwargs) -> Path:
        if path == download_server._uploads_dir:
            raise OSError("uploads directory is unavailable")
        return original_resolve(path, *args, **kwargs)

    with patch.object(Path, "resolve", fail_upload_root):
        response = asyncio.run(
            _route(download_server, "/uploads/{filename}")("photo.png", token="valid-token")
        )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 404

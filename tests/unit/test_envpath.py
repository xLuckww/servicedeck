import os
from pathlib import Path

import pytest

from servicedeck import envpath


def test_service_path_order():
    home = str(Path.home())
    assert envpath.service_path().split(":") == [
        f"{home}/.local/share/mise/shims",
        "/opt/homebrew/bin",
        "/usr/local/bin",
        f"{home}/.local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]


def test_resolve_tool_with_minimal_path(monkeypatch):
    # 从访达打开 App 时 PATH 只有最基本的几项
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    uv = envpath.resolve_tool("uv")
    assert os.path.isabs(uv) and os.access(uv, os.X_OK)
    assert envpath.resolve_tool("sh") == "/bin/sh"


def test_resolve_tool_missing():
    with pytest.raises(envpath.ToolNotFound) as e:
        envpath.resolve_tool("definitely-not-a-real-tool-xyz")
    assert "definitely-not-a-real-tool-xyz" in str(e.value)
    assert "找不到" in str(e.value)


def test_resolve_tool_skips_non_executable(tmp_path, monkeypatch):
    (tmp_path / "faketool").write_text("#!/bin/sh\n")
    monkeypatch.setattr(envpath, "SERVICE_PATH_DIRS", [str(tmp_path), "/bin"])
    with pytest.raises(envpath.ToolNotFound):
        envpath.resolve_tool("faketool")
    (tmp_path / "faketool").chmod(0o755)
    assert envpath.resolve_tool("faketool") == str(tmp_path / "faketool")

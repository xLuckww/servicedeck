"""全局测试夹具：每个测试都用临时的短路径 SERVICEDECK_HOME，绝不碰真实配置。"""

import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def sd_home(monkeypatch) -> Path:
    # Unix socket 路径不能超过约 104 字符，所以放在 /tmp 下的短目录
    home = Path(tempfile.mkdtemp(prefix="sd", dir="/tmp"))
    monkeypatch.setenv("SERVICEDECK_HOME", str(home))
    yield home
    shutil.rmtree(home, ignore_errors=True)


def pytest_collection_modifyitems(config, items):
    """App 行为测试会在菜单栏出现图标、弹出窗口，默认跳过；设置 SERVICEDECK_APP_TESTS=1 或用 -m app 才运行。"""
    import os
    if os.environ.get("SERVICEDECK_APP_TESTS") or "app" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="App 行为测试：设置 SERVICEDECK_APP_TESTS=1 运行")
    for item in items:
        if "app" in item.keywords:
            item.add_marker(skip)

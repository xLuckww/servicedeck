"""命令行在临时实例上实测，且用最小环境（env -i）模拟从访达打开时的环境。"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from servicedeck import registry

from .helpers import http_service, make_registry, port_open, wait_for

pytestmark = pytest.mark.integration

SD = str(Path(sys.executable).parent / "servicedeck")


def run(sd_home, *args):
    env = {"HOME": os.path.expanduser("~"), "PATH": "/usr/bin:/bin", "SERVICEDECK_HOME": str(sd_home)}
    return subprocess.run([SD, *args], capture_output=True, text=True, env=env, timeout=120)


def test_cli_up_status_down(sd_home):
    registry.save(make_registry(http_service("alpha", 18330), http_service("manual", 18331, autostart=False)))
    try:
        r = run(sd_home, "up")
        assert r.returncode == 0, r.stdout + r.stderr
        wait_for(lambda: port_open(18330), desc="alpha 端口")

        def overall():
            data = json.loads(run(sd_home, "status", "--json").stdout)
            return data["overall"] == "green" and data

        data = wait_for(overall, desc="整体变绿")
        states = {s["id"]: s["display"] for s in data["services"]}
        assert states == {"alpha": "healthy", "manual": "manual"}

        table = run(sd_home, "status").stdout
        assert "alpha" in table and "运行中" in table and "手动开启" in table

        # 再次 up：已在运行，不重复启动
        assert run(sd_home, "up").returncode == 0
    finally:
        r = run(sd_home, "down")
    assert r.returncode == 0
    assert not port_open(18330)
    assert "服务管理器未运行" in run(sd_home, "status").stdout

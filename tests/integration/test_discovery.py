import os
import subprocess
import sys

import pytest

from servicedeck import discovery

from .helpers import http_service, make_registry, plain_service, port_open, wait_for

pytestmark = pytest.mark.integration


@pytest.fixture
def terminal_server(tmp_path):
    """模拟在终端里临时跑起来的服务：/usr/bin/python3 -m http.server 18399。"""
    proc = subprocess.Popen(["/usr/bin/python3", "-m", "http.server", "18399", "--bind", "127.0.0.1"],
                            cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_for(lambda: port_open(18399), desc="终端服务端口")
    yield proc, tmp_path
    proc.terminate()
    proc.wait(timeout=10)


def test_terminal_server_detected(terminal_server):
    proc, folder = terminal_server
    found = {c.pid: c for c in discovery.scan()}
    assert proc.pid in found
    c = found[proc.pid]
    assert c.ports == [18399]
    assert c.exposure == "local"
    assert "http.server" in c.command and "18399" in c.command
    assert os.path.realpath(c.cwd) == os.path.realpath(folder)
    assert c.title == "python -m http.server"
    assert c.uptime_seconds is not None


def test_managed_services_not_listed(pc, terminal_server):
    proc, _ = terminal_server
    client = pc(make_registry(
        http_service("direct", 18390),
        # 不用 exec：bash 仍是中间层，python 是 process-compose 的孙进程
        plain_service("nested", "/usr/bin/python3 -m http.server 18391 --bind 127.0.0.1; echo done", port=18391),
    ))
    wait_for(lambda: port_open(18390) and port_open(18391), desc="受管服务端口")
    found = discovery.scan()
    ports = {p for c in found for p in c.ports}
    assert 18390 not in ports and 18391 not in ports
    assert proc.pid in {c.pid for c in found}

"""集成测试夹具：临时 process-compose 实例，结束时停掉并确认 183xx 端口都已释放。"""

import os
import signal
import subprocess

import pytest

from servicedeck.compose import lifecycle
from servicedeck.models import Registry

from .helpers import port_open


@pytest.fixture
def pc(sd_home):
    """返回一个函数：传入清单，保存并启动临时 process-compose，返回 client。"""
    started = []

    def _start(reg: Registry):
        from servicedeck import registry
        registry.save(reg)
        client = lifecycle.ensure_running()
        started.append(client)
        return client

    yield _start

    if started:
        try:
            lifecycle.shutdown(timeout=20)
        finally:
            pid = lifecycle.read_pid()
            if pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    assert not lifecycle.launchd_loaded(), "测试结束后 launchd 里还登记着测试用的服务管理器"
    for port in range(18300, 18400):
        if port_open(port):
            # 找到并结束残留进程，再报错，避免污染后续测试
            out = subprocess.run(["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout
            for p in out.split():
                try:
                    os.kill(int(p), signal.SIGKILL)
                except (ProcessLookupError, ValueError):
                    pass
            pytest.fail(f"测试结束后端口 {port} 仍被占用")

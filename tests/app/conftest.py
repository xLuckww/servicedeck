"""App 测试夹具：用正式的安装脚本 scripts/install_app.sh 在临时目录生成 "ServiceDeck Test.app"
（标识 local.servicedeck.test，配置目录指向临时目录，不与正式 App 冲突），测试结束后注销并删除。"""

import os
import subprocess
from pathlib import Path

import pytest

from servicedeck.compose import lifecycle

from .helpers import LSREGISTER, alive, app_pid, hook_quit

PROJECT = Path(__file__).resolve().parents[2]
INSTALL = PROJECT / "scripts" / "install_app.sh"


def install(dest: Path, home: Path, *extra: str) -> Path:
    subprocess.run([str(INSTALL), "--dest", str(dest), "--name", "ServiceDeck Test", "--id", "local.servicedeck.test",
                    "--home", str(home), *extra], check=True, capture_output=True, text=True)
    return dest / "ServiceDeck Test.app"


@pytest.fixture
def test_app(sd_home):
    bundle = install(sd_home, sd_home, "--test-hook")
    yield bundle
    try:
        hook_quit(bundle)
    finally:
        pid = app_pid()
        if pid and alive(pid):
            os.kill(pid, 9)
        lifecycle.shutdown(timeout=20)
        subprocess.run([LSREGISTER, "-u", str(bundle)], capture_output=True)

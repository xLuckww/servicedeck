"""安装脚本：生成的 .app 结构、图标、启动脚本；项目文件夹不见了时弹出提示并退出。"""

import plistlib
import subprocess
import time

import pytest

from .conftest import INSTALL, PROJECT, install
from .helpers import LSREGISTER

pytestmark = pytest.mark.app


@pytest.fixture
def bundle(sd_home):
    b = install(sd_home, sd_home)
    yield b
    subprocess.run([LSREGISTER, "-u", str(b)], capture_output=True)


def bootstrap_of(bundle):
    return next((bundle / "Contents" / "lib").glob("python3.*/site-packages/servicedeck_bootstrap.py"))


def test_bundle_structure(bundle, sd_home):
    info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == "local.servicedeck.test"
    assert info["CFBundleName"] == "ServiceDeck Test"
    assert info["LSUIElement"] is True
    assert info["CFBundleIconFile"] == "ServiceDeck"
    icns = bundle / "Contents" / "Resources" / "ServiceDeck.icns"
    assert icns.exists() and icns.stat().st_size > 50_000
    # App 的启动程序就是 Python 本体（不是启动脚本、也不是链接），否则菜单栏图标会被系统藏起来
    exe = bundle / "Contents" / "MacOS" / "ServiceDeck"
    assert not exe.is_symlink() and exe.read_bytes()[:4] in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")
    boot = bootstrap_of(bundle).read_text()
    assert f'PROJECT = "{PROJECT}"' in boot
    assert f"'SERVICEDECK_HOME': '{sd_home}'" in boot
    assert "SERVICEDECK_TEST_HOOK" not in boot
    # 带参数运行时引导文件不接管，能正常使用项目的虚拟环境
    out = subprocess.run([str(exe), "-c", "import servicedeck, webview, sys; print(sys.prefix)"],
                         capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(sd_home)})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("Contents")


def test_default_install_uses_real_locations(tmp_path):
    subprocess.run([str(INSTALL), "--dest", str(tmp_path), "--name", "SDDefault", "--id", "local.servicedeck.test2"],
                   check=True, capture_output=True, text=True)
    try:
        boot = bootstrap_of(tmp_path / "SDDefault.app").read_text()
        assert "EXTRA_ENV = {}" in boot
        assert "~/Library/Logs/ServiceDeck" in boot
    finally:
        subprocess.run([LSREGISTER, "-u", str(tmp_path / "SDDefault.app")], capture_output=True)


def test_reinstall_replaces_own_bundle_but_refuses_others(bundle, sd_home):
    install(sd_home, sd_home)  # 再装一次：替换自己
    other = sd_home / "Other" / "ServiceDeck Test.app"
    (other / "Contents").mkdir(parents=True)
    (other / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.example.other"}))
    r = subprocess.run([str(INSTALL), "--dest", str(sd_home / "Other"), "--name", "ServiceDeck Test",
                        "--id", "local.servicedeck.test"], capture_output=True, text=True)
    assert r.returncode == 1 and "不覆盖" in r.stderr
    assert (other / "Contents" / "Info.plist").exists()


def test_missing_project_shows_alert_and_exits(bundle, sd_home):
    boot = bootstrap_of(bundle)
    boot.write_text(boot.read_text().replace(f'SITE_PACKAGES = "{PROJECT}', 'SITE_PACKAGES = "/tmp/servicedeck-moved-away'))
    exe = bundle / "Contents" / "MacOS" / "ServiceDeck"
    t = time.time()
    r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(sd_home), "SERVICEDECK_NO_DIALOG": "1"})  # 自动测试不真的弹窗
    assert r.returncode == 1
    assert "项目文件夹" in r.stderr
    assert time.time() - t < 30

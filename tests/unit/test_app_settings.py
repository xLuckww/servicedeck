import plistlib

from servicedeck import paths
from servicedeck.app import settings


def test_defaults():
    s = settings.load()
    assert s["login_launch"] is True and s["boot_autostart"] is True


def test_set_and_persist_keeps_other_keys():
    settings.update(boot_autostart=False)
    settings.update(collapsed={"shared": True})
    s = settings.load()
    assert s["boot_autostart"] is False and s["collapsed"] == {"shared": True}


def test_corrupt_state_falls_back_to_defaults():
    paths.ensure_dirs()
    paths.ui_state_file().write_text("{not json")
    assert settings.load()["login_launch"] is True


def test_launch_agents_dir_under_test_home(sd_home):
    assert settings.launch_agent_file() == sd_home / "LaunchAgents" / "local.servicedeck.plist"


def test_login_launch_writes_and_removes_plist():
    settings.apply_login_launch(True)
    f = settings.launch_agent_file()
    data = plistlib.loads(f.read_bytes())
    assert data["Label"] == "local.servicedeck"
    assert data["ProgramArguments"] == ["/usr/bin/open", "-a", "ServiceDeck", "--args", "background"]
    assert data["RunAtLoad"] is True
    settings.apply_login_launch(False)
    assert not f.exists()
    settings.apply_login_launch(False)  # 不存在时也不报错


def test_parse_boottime():
    assert settings.parse_boottime("{ sec = 1789896804, usec = 806235 } Sun Sep 20 17:33:24 2026") == 1789896804
    assert settings.parse_boottime("garbage") is None
    assert settings.boot_time() > 0


def test_should_autostart_once_per_boot():
    assert settings.should_autostart(manager_alive=False, boot=100) is True
    settings.mark_boot_handled(100)
    assert settings.should_autostart(manager_alive=False, boot=100) is False  # 同一次开机只一次
    assert settings.should_autostart(manager_alive=False, boot=200) is True  # 重新开机
    assert settings.should_autostart(manager_alive=True, boot=300) is False  # 已在运行
    settings.update(boot_autostart=False)
    assert settings.should_autostart(manager_alive=False, boot=400) is False

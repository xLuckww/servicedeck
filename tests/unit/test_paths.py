from pathlib import Path

import pytest

from servicedeck import paths


def test_home_uses_env_override(sd_home):
    assert paths.home() == sd_home
    assert paths.registry_file() == sd_home / "services.yaml"
    assert paths.backups_dir() == sd_home / "backups"
    assert paths.ui_state_file() == sd_home / "ui-state.json"
    assert paths.run_dir() == sd_home / "run"
    assert paths.compose_file() == sd_home / "run" / "process-compose.yaml"
    assert paths.socket_path() == sd_home / "run" / "pc.sock"
    assert paths.pc_pid_file() == sd_home / "run" / "pc.pid"
    assert paths.app_lock_file() == sd_home / "run" / "app.lock"


def test_logs_dir_under_home_when_overridden(sd_home):
    # 测试时日志也放进临时目录，不写 ~/Library/Logs
    assert paths.logs_dir() == sd_home / "logs"


def test_default_home(monkeypatch):
    monkeypatch.delenv("SERVICEDECK_HOME", raising=False)
    assert paths.home() == Path.home() / ".config" / "servicedeck"
    assert paths.logs_dir() == Path.home() / "Library" / "Logs" / "ServiceDeck"


def test_socket_path_length_check():
    paths.check_socket_path(Path("/tmp/sd/run/pc.sock"))
    with pytest.raises(paths.SocketPathTooLong) as e:
        paths.check_socket_path(Path("/tmp/" + "x" * 120 + "/pc.sock"))
    assert "104" in str(e.value) or "太长" in str(e.value)


def test_ensure_dirs(sd_home):
    paths.ensure_dirs()
    assert paths.run_dir().is_dir()
    assert paths.backups_dir().is_dir()
    assert paths.logs_dir().is_dir()


def test_launchd_label_never_real_in_tests(sd_home, monkeypatch):
    from servicedeck.compose import lifecycle
    test_label = lifecycle.launchd_label()
    assert test_label.startswith("local.servicedeck.pc.test-") and test_label != "local.servicedeck.pc"
    monkeypatch.setenv("SERVICEDECK_HOME", str(sd_home / "other"))
    assert lifecycle.launchd_label() != test_label
    monkeypatch.delenv("SERVICEDECK_HOME")
    assert lifecycle.launchd_label() == "local.servicedeck.pc"

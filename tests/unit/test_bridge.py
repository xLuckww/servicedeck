import json

import pytest

from servicedeck import actions, paths, registry
from servicedeck.app import bridge as bridge_mod
from servicedeck.app import settings
from servicedeck.models import Group, Registry, Service


@pytest.fixture
def b():
    return bridge_mod.Bridge()


def setup_reg():
    registry.save(Registry(groups=[Group(id="g", name="公共服务")], services=[
        Service(id="a", name="甲", group="g", template="custom", command="sleep 1", port=18300),
        Service(id="b", name="乙", group="g", template="custom", command="sleep 1", depends_on=["a"],
                autostart=False, start_warning="小心"),
    ]))


def roundtrip(x):
    return json.loads(json.dumps(x, ensure_ascii=False))


def test_every_public_method_returns_json_dict(b):
    setup_reg()
    calls = {
        "get_state": (), "list_unmanaged": (), "service_detail": ("a",), "logs": ("a",),
        "template_catalog": (), "preview": ("custom", {"command": "x"}), "template_choices": ("node", "script", {}),
        "draft_for_edit": ("a",), "get_settings": (), "list_backups": (),
        "start": ("ghost",), "stop": ("a",), "delete_service": ("ghost",),
    }
    for name, args in calls.items():
        r = getattr(b, name)(*args)
        assert isinstance(r, dict) and "ok" in r, name
        assert roundtrip(r) == r, name


def test_get_state_shape(b):
    setup_reg()
    s = b.get_state()
    assert s["ok"] and s["manager_running"] is False and s["overall"] == "grey"
    assert [g["id"] for g in s["groups"]] == ["g"]
    svc = s["services"]["a"]
    assert svc["name"] == "甲" and svc["command"] == "sleep 1" and svc["display"] == "stopped"
    assert s["services"]["b"]["display"] == "manual" and s["services"]["b"]["depends_on"] == ["a"]
    assert s["settings"]["login_launch"] is True
    assert s["registry_error"] is None


def test_get_state_with_broken_registry(b):
    paths.ensure_dirs()
    paths.registry_file().write_text("version: 1\nservices: [oops\n")
    s = b.get_state()
    assert s["ok"] and s["registry_error"] and s["registry_error_line"] is not None


def test_service_detail(b):
    setup_reg()
    d = b.service_detail("a")
    assert d["ok"] and d["service"]["port"] == 18300 and d["dependents"] == ["乙"]
    assert d["log_file"].endswith("a.log")
    assert not b.service_detail("ghost")["ok"]


def test_confirm_flow_passthrough(b):
    setup_reg()
    r = b.start("b")
    assert r["needs_confirm"] and "小心" in r["prompt"]


def test_unexpected_error_becomes_chinese_message(b, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("internal traceback detail")
    monkeypatch.setattr(actions, "start", boom)
    r = b.start("a")
    assert r["ok"] is False
    assert "出了点问题" in r["message"]
    assert "Traceback" not in r["message"]


def test_settings(b):
    b.set_setting("boot_autostart", False)
    assert settings.load()["boot_autostart"] is False
    b.set_setting("login_launch", False)
    assert not settings.launch_agent_file().exists()
    b.set_setting("login_launch", True)
    assert settings.launch_agent_file().exists()
    assert not b.set_setting("unknown", 1)["ok"]
    b.set_collapsed("g", True)
    assert settings.load()["collapsed"] == {"g": True}


def test_backups(b):
    setup_reg()
    registry.save(Registry(groups=[Group(id="g", name="公共服务")], services=[]))
    r = b.list_backups()
    assert r["ok"] and len(r["items"]) == 1 and r["items"][0]["services"] == 2
    r = b.restore_backup(0)
    assert r["needs_confirm"]
    assert b.restore_backup(0, True)["ok"]
    assert len(registry.load().services) == 2


def test_save_and_groups_via_bridge(b):
    setup_reg()
    r = b.save_service({"name": "丙", "group": "g", "template": "custom", "template_fields": {"command": "sleep 2"}})
    assert r["ok"], r
    assert b.add_group("新组")["ok"]
    assert b.delete_group("g")["ok"] is False

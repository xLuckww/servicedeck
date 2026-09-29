"""操作层在"服务管理器未运行"时的行为：只改文件，不启动任何东西。"""

import os

import pytest

from servicedeck import actions, paths, registry
from servicedeck.models import Group, Registry, Service


def svc(id, **kw):
    return Service(**dict(dict(id=id, name=id.upper(), group="g", template="custom", command="sleep 100"), **kw))


def setup_reg(*services, groups=(("g", "公共服务"),)):
    registry.save(Registry(groups=[Group(id=i, name=n) for i, n in groups], services=list(services)))


def draft(**kw):
    d = {"name": "测试服务 A", "group": "g", "template": "custom",
         "template_fields": {"command": "sleep 100"}}
    d.update(kw)
    return d


def errors_by_field(r):
    return {e["field"]: e["message"] for e in r.errors}


# ---------- 预览与模板 ----------

def test_preview_ok_and_error(tmp_path):
    r = actions.preview("custom", {"command": "sleep 1", "folder": str(tmp_path)})
    assert r.ok and r.data["command"] == "sleep 1" and r.data["cwd"] == str(tmp_path)
    r = actions.preview("python", {"folder": str(tmp_path), "entry": "main.py"})
    assert not r.ok and "template_fields.folder" in errors_by_field(r)


def test_preview_fixable_script(tmp_path):
    s = tmp_path / "run.sh"
    s.write_text("#!/bin/sh\n")
    r = actions.preview("script", {"file": str(s)})
    assert not r.ok and r.errors[0]["fix"] == "chmod"
    assert actions.fix_script_permission(str(s)).ok
    assert actions.preview("script", {"file": str(s)}).ok


def test_template_catalog():
    r = actions.template_catalog()
    assert [t["id"] for t in r.data["templates"]] == ["python", "node", "executable", "script", "custom"]
    assert all(t["fields"] for t in r.data["templates"])


# ---------- 新增 / 编辑 / 删除（管理器未运行） ----------

def test_add_service_generates_id_and_saves():
    setup_reg()
    r = actions.save_service(draft(port=18360))
    assert r.ok, r.errors
    sid = r.data["id"]
    assert sid == "a"
    s = registry.load().service(sid)
    assert s.command == "sleep 100" and s.port == 18360 and s.template_fields == {"command": "sleep 100"}
    assert paths.compose_file().exists()


def test_add_uses_edited_command():
    setup_reg()
    r = actions.save_service(draft(name="x", template="custom", command="sleep 200"))
    assert registry.load().service(r.data["id"]).command == "sleep 200"


def test_add_validation_errors_are_per_field(tmp_path):
    setup_reg(svc("a", name="已有", port=18361))
    r = actions.save_service(draft(name="已有", group="nope", port=18361, depends_on=["ghost"],
                                   env_file=str(tmp_path / "none.env")))
    assert not r.ok
    f = errors_by_field(r)
    assert {"name", "group", "port", "depends_on", "env_file"} <= set(f)
    assert len(registry.load().services) == 1  # 没有写入


def test_add_template_error_field():
    setup_reg()
    r = actions.save_service(draft(template="python", template_fields={"folder": "/nonexistent", "entry": "x.py"}))
    assert not r.ok and "template_fields.folder" in errors_by_field(r)


def test_add_bad_port_type():
    setup_reg()
    r = actions.save_service(draft(port="abc"))
    assert not r.ok and "port" in errors_by_field(r)


def test_edit_keeps_id_and_updates():
    setup_reg(svc("a", name="甲"))
    r = actions.draft_for_edit("a")
    d = r.data["draft"]
    assert d["name"] == "甲" and d["template"] == "custom"
    d["name"] = "甲改"
    d["port"] = 18362
    assert actions.save_service(d, service_id="a").ok
    s = registry.load().service("a")
    assert s.name == "甲改" and s.port == 18362


def test_edit_unknown():
    setup_reg()
    assert not actions.save_service(draft(), service_id="ghost").ok
    assert not actions.draft_for_edit("ghost").ok


def test_delete_needs_confirm_and_mentions_files():
    setup_reg(svc("a"), svc("b"))
    r = actions.delete_service("a")
    assert r.needs_confirm and "不会删除" in r.prompt
    assert registry.load().service("a")
    assert actions.delete_service("a", confirm=True).ok
    assert registry.load().service("a") is None


def test_delete_blocked_by_dependents():
    setup_reg(svc("a"), svc("b", depends_on=["a"]))
    r = actions.delete_service("a", confirm=True)
    assert not r.ok and "B" in r.message
    assert registry.load().service("a")


# ---------- 分组 ----------

def test_groups_crud():
    setup_reg(svc("a"))
    r = actions.add_group("webapp")
    assert r.ok and r.data["id"] == "webapp"
    assert not actions.add_group("webapp").ok  # 重名
    assert not actions.add_group("  ").ok
    r2 = actions.add_group("临时")
    assert r2.data["id"] == "group-1"
    assert actions.rename_group("group-1", "临时项目").ok
    assert registry.load().group("group-1").name == "临时项目"
    assert actions.reorder_groups(["webapp", "group-1", "g"]).ok
    assert [g.id for g in registry.load().groups] == ["webapp", "group-1", "g"]
    assert not actions.reorder_groups(["g"]).ok
    assert not actions.delete_group("g").ok  # 非空
    assert actions.delete_group("group-1").ok
    assert registry.load().group("group-1") is None


# ---------- 启停（管理器未运行） ----------

def test_start_when_manager_down_needs_confirm():
    setup_reg(svc("a"))
    r = actions.start("a")
    assert r.needs_confirm and "服务管理器未运行" in r.prompt


def test_start_warning_needs_confirm():
    setup_reg(svc("frp", autostart=False, start_warning="开启后本机服务可从公网访问"))
    r = actions.start("frp")
    assert r.needs_confirm and "公网" in r.prompt


def test_stop_when_manager_down():
    setup_reg(svc("a"))
    r = actions.stop("a")
    assert r.ok  # 本来就没在运行


def test_unknown_service():
    setup_reg()
    assert not actions.start("ghost").ok
    assert not actions.stop("ghost").ok


# ---------- 未纳入管理 ----------

def test_stop_unmanaged_refuses_other_users_process():
    r = actions.stop_unmanaged(1, confirm=True)  # launchd，属于 root
    assert not r.ok and "不是你的进程" in r.message


def test_stop_unmanaged_refuses_self():
    r = actions.stop_unmanaged(os.getpid(), confirm=True)
    assert not r.ok


def test_stop_unmanaged_missing_process():
    r = actions.stop_unmanaged(999999, confirm=True)
    assert not r.ok and "不存在" in r.message


def test_result_to_dict():
    import json
    json.dumps(actions.start("ghost").to_dict(), ensure_ascii=False)


def test_service_logs_strip_ansi_colors():
    setup_reg(svc("a"))
    paths.ensure_dirs()
    paths.service_log_file("a").write_text(
        "2026-09-28 15:16:14 \x1b[2m2026-09-28T07:16:14\x1b[0m [\x1b[32m\x1b[1minfo\x1b[0m] 启动完成\n\n"
        "INFO:     Uvicorn running\n")
    lines = actions.service_logs("a").data["lines"]
    assert lines == ["2026-09-28 15:16:14 2026-09-28T07:16:14 [info] 启动完成", "INFO:     Uvicorn running"]

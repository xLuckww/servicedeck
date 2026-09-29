import os
import stat
import threading
import time

import pytest
import yaml

from servicedeck import paths, registry
from servicedeck.models import Group, Registry, Service

SAMPLE = """\
version: 1
groups:
  - id: webapp
    name: webapp
  - id: shared
    name: 公共服务
services:
  - id: postgres
    name: PostgreSQL 17
    group: shared
    template: executable
    command: /opt/homebrew/opt/postgresql@17/bin/postgres -D /opt/homebrew/var/postgresql@17
    env:
      LC_ALL: en_US.UTF-8
    port: 5432
    health: { type: exec, command: "/opt/homebrew/opt/postgresql@17/bin/pg_isready -h 127.0.0.1 -q" }
    shutdown_signal: 2
    restart: on_failure
    autostart: true
  - id: frpc
    name: 公网入口 frp
    group: shared
    template: executable
    command: ~/frp/frpc -c ~/frp/frpc.toml
    autostart: false
    start_warning: 开启后本机服务可从公网访问
    restart: no
  - id: openwebui
    name: Open WebUI
    group: webapp
    template: script
    cwd: /tmp
    command: ./scripts/openwebui.sh run
    port: 3000
    health: { type: http, path: /health, initial_delay: 5, timeout: 300 }
    depends_on: [postgres]
"""


def svc(id, **kw):
    base = dict(id=id, name=id.upper(), group="g", template="custom", command="echo hi")
    base.update(kw)
    return Service(**base)


def reg(*services, groups=("g",)):
    return Registry(groups=[Group(id=g, name=g) for g in groups], services=list(services))


def issues_text(issues):
    return " ".join(f"{i.service}.{i.field}:{i.message}" for i in issues)


# ---------- load ----------

def test_load_missing_returns_empty():
    r = registry.load()
    assert r.services == [] and r.groups == [] and r.version == 1


def test_load_sample():
    paths.registry_file().write_text(SAMPLE)
    r = registry.load()
    assert [s.id for s in r.services] == ["postgres", "frpc", "openwebui"]
    assert r.service("frpc").restart == "no"
    assert r.service("frpc").autostart is False
    assert r.service("openwebui").health.timeout == 300
    assert r.group("shared").name == "公共服务"


def test_load_yaml_syntax_error_has_line_number():
    paths.registry_file().write_text("version: 1\nservices:\n  - id: a\n    name: [unclosed\n")
    with pytest.raises(registry.RegistryError) as e:
        registry.load()
    assert e.value.line is not None
    assert "第" in str(e.value) and "行" in str(e.value)


def test_load_schema_error_readable():
    paths.registry_file().write_text("version: 1\nservices:\n  - id: a\n    name: A\n")
    with pytest.raises(registry.RegistryError) as e:
        registry.load()
    assert "group" in str(e.value)


def test_load_non_mapping():
    paths.registry_file().write_text("- just\n- a list\n")
    with pytest.raises(registry.RegistryError):
        registry.load()


# ---------- validate ----------

def test_validate_ok():
    assert registry.validate(reg(svc("a"), svc("b", depends_on=["a"]))) == []


def test_validate_duplicate_id_and_name():
    issues = registry.validate(reg(svc("a"), svc("a", name="X"), svc("c", name="A")))
    text = issues_text(issues)
    assert "id" in text and "重复" in text
    assert any(i.field == "name" for i in issues)


def test_validate_unknown_group():
    issues = registry.validate(reg(svc("a", group="nope")))
    assert [i.field for i in issues] == ["group"]


def test_validate_duplicate_group_id():
    r = Registry(groups=[Group(id="g", name="1"), Group(id="g", name="2")], services=[])
    assert registry.validate(r)


def test_validate_missing_dependency():
    issues = registry.validate(reg(svc("a", depends_on=["ghost"])))
    assert issues[0].field == "depends_on" and "ghost" in issues[0].message


def test_validate_self_dependency():
    issues = registry.validate(reg(svc("a", depends_on=["a"])))
    assert issues and issues[0].field == "depends_on"


def test_validate_cycle():
    issues = registry.validate(reg(svc("a", depends_on=["b"]), svc("b", depends_on=["a"])))
    assert any("循环" in i.message for i in issues)


def test_validate_long_cycle():
    issues = registry.validate(reg(svc("a", depends_on=["b"]), svc("b", depends_on=["c"]), svc("c", depends_on=["a"])))
    assert any("循环" in i.message for i in issues)


def test_validate_port_range():
    for bad in (0, 70000, -1):
        issues = registry.validate(reg(svc("a", port=bad)))
        assert [i.field for i in issues] == ["port"]


def test_validate_duplicate_port():
    issues = registry.validate(reg(svc("a", port=18300), svc("b", port=18300)))
    assert any(i.field == "port" for i in issues)


def test_validate_cwd_and_env_file(tmp_path):
    issues = registry.validate(reg(svc("a", cwd=str(tmp_path / "nope"), env_file=str(tmp_path / "nope.env"))))
    assert sorted(i.field for i in issues) == ["cwd", "env_file"]
    envf = tmp_path / ".env"
    envf.write_text("X=1\n")
    assert registry.validate(reg(svc("a", cwd=str(tmp_path), env_file=str(envf)))) == []


def test_validate_relative_env_file_resolved_against_cwd(tmp_path):
    (tmp_path / ".env").write_text("X=1\n")
    assert registry.validate(reg(svc("a", cwd=str(tmp_path), env_file=".env"))) == []


def test_validate_tilde_cwd():
    assert registry.validate(reg(svc("a", cwd="~"))) == []


def test_validate_empty_command():
    issues = registry.validate(reg(svc("a", command="   ")))
    assert [i.field for i in issues] == ["command"]


# ---------- save ----------

def test_save_roundtrip_and_permissions():
    r = reg(svc("a", port=18300, env={"X": "1"}), svc("b", depends_on=["a"], restart="no", autostart=False))
    registry.save(r)
    f = paths.registry_file()
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    loaded = registry.load()
    assert loaded == r
    raw = yaml.safe_load(f.read_text())
    assert list(raw) == ["version", "groups", "services"]
    assert "health" not in raw["services"][0]  # 空值不写出
    assert raw["services"][1]["restart"] == "no"


def test_save_keeps_chinese_readable():
    registry.save(Registry(groups=[Group(id="g", name="公共服务")], services=[]))
    assert "公共服务" in paths.registry_file().read_text()


def test_save_rejects_invalid():
    with pytest.raises(registry.ValidationFailed) as e:
        registry.save(reg(svc("a", group="nope")))
    assert e.value.issues
    assert not paths.registry_file().exists()


def test_save_backs_up_previous():
    registry.save(reg(svc("a")))
    assert registry.list_backups() == []  # 第一次保存时没有旧文件可备份
    registry.save(reg(svc("a"), svc("b")))
    backups = registry.list_backups()
    assert len(backups) == 1
    assert [s.id for s in registry.load_file(backups[0]).services] == ["a"]


def test_backups_keep_20():
    for i in range(25):
        registry.save(reg(*[svc(f"s{j}") for j in range(i + 1)]))
    backups = registry.list_backups()
    assert len(backups) == 20
    # 最新的备份是倒数第二次保存的内容（24 个服务），最旧的是第 5 次（5 个服务）
    assert len(registry.load_file(backups[0]).services) == 24
    assert len(registry.load_file(backups[-1]).services) == 5


def test_restore_backup():
    registry.save(reg(svc("a")))
    registry.save(reg(svc("a"), svc("b")))
    registry.save(reg(svc("a"), svc("b"), svc("c")))
    restored = registry.restore_backup(1)  # 0 = 最新一份备份（a, b），1 = 再往前（a）
    assert [s.id for s in restored.services] == ["a"]
    assert [s.id for s in registry.load().services] == ["a"]
    # 恢复前的当前版本（a,b,c）也进了备份，可以反悔
    assert [s.id for s in registry.load_file(registry.list_backups()[0]).services] == ["a", "b", "c"]


def test_restore_backup_out_of_range():
    with pytest.raises(registry.RegistryError):
        registry.restore_backup(0)


def test_concurrent_saves_do_not_corrupt():
    errors = []

    def worker(tag):
        try:
            for i in range(30):
                registry.save(reg(*[svc(f"{tag}{j}") for j in range(i % 5 + 1)]))
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("x", "y")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    registry.load()  # 能正常解析
    assert len(registry.list_backups()) == 20
    for b in registry.list_backups():
        registry.load_file(b)
    leftovers = [p for p in paths.home().iterdir() if p.name.startswith(".services.yaml")]
    assert leftovers == []


# ---------- diff ----------

def test_diff():
    old = reg(svc("a"), svc("b"), svc("c"))
    new = reg(svc("a"), svc("b", command="echo changed", port=18301), svc("d"))
    d = registry.diff(old, new)
    assert d.added == ["d"]
    assert d.removed == ["c"]
    assert d.changed == {"b": ["command", "port"]}
    assert not d.is_empty


def test_diff_empty():
    d = registry.diff(reg(svc("a")), reg(svc("a")))
    assert d.is_empty and d.added == [] and d.removed == [] and d.changed == {}


def test_diff_name_only_change():
    d = registry.diff(reg(svc("a")), reg(svc("a", name="新名字")))
    assert d.changed == {"a": ["name"]}


def test_validate_http_health_needs_port():
    issues = registry.validate(reg(svc("a", health={"type": "http", "path": "/"})))
    assert [i.field for i in issues] == ["health"]

import pytest
from pydantic import ValidationError

from servicedeck.models import Group, Health, Registry, Service, make_id


def svc(**kw):
    base = dict(id="a", name="A", group="g", template="custom", command="echo hi")
    base.update(kw)
    return Service(**base)


def test_defaults():
    s = svc()
    assert s.restart == "on_failure"
    assert s.autostart is True
    assert s.shutdown_signal == 15
    assert s.env == {} and s.depends_on == [] and s.template_fields == {}
    assert s.port is None and s.health is None and s.cwd is None


def test_restart_no_from_yaml_false():
    # YAML 里写 restart: no 会被解析成 False
    assert svc(restart=False).restart == "no"
    assert svc(restart="always").restart == "always"
    with pytest.raises(ValidationError):
        svc(restart="sometimes")


def test_template_must_be_known():
    with pytest.raises(ValidationError):
        svc(template="docker")


def test_health_types():
    assert svc(health={"type": "http", "path": "/health"}).health.path == "/health"
    assert svc(health={"type": "exec", "command": "true"}).health.command == "true"
    with pytest.raises(ValidationError):
        Health(type="http")  # http 需要 path
    with pytest.raises(ValidationError):
        Health(type="exec")  # exec 需要 command
    with pytest.raises(ValidationError):
        Health(type="tcp", path="/")


def test_id_format():
    with pytest.raises(ValidationError):
        svc(id="有中文")
    with pytest.raises(ValidationError):
        svc(id="-leading")
    svc(id="open-webui_2")


def test_make_id():
    assert make_id("Open WebUI", []) == "open-webui"
    assert make_id("PostgreSQL 17", []) == "postgresql-17"
    assert make_id("公网入口 frp", []) == "frp"
    assert make_id("数据库", []) == "service-1"
    assert make_id("数据库", ["service-1"]) == "service-2"
    assert make_id("Open WebUI", ["open-webui"]) == "open-webui-2"
    assert make_id("Open WebUI", ["open-webui", "open-webui-2"]) == "open-webui-3"
    assert make_id("分组", [], prefix="group") == "group-1"


def test_registry_helpers():
    r = Registry(groups=[Group(id="g", name="G")], services=[svc(), svc(id="b", name="B")])
    assert r.version == 1
    assert r.service("b").name == "B"
    assert r.service("zzz") is None
    assert r.group("g").name == "G"
    assert [s.id for s in r.services_in_group("g")] == ["a", "b"]

import json

from servicedeck import cli, registry
from servicedeck.models import Group, Registry, Service


def test_status_manager_not_running(capsys):
    registry.save(Registry(groups=[Group(id="g", name="公共服务")],
                           services=[Service(id="a", name="甲", group="g", template="custom", command="x", port=18300)]))
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "服务管理器未运行" in out and "甲" in out and "18300" in out


def test_status_json(capsys):
    assert cli.main(["status", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["manager_running"] is False and data["overall"] == "grey"


def test_validate_ok_and_errors(capsys, tmp_path):
    registry.save(Registry(groups=[Group(id="g", name="g")],
                           services=[Service(id="a", name="a", group="g", template="custom", command="x")]))
    assert cli.main(["validate"]) == 0
    assert "没有发现问题" in capsys.readouterr().out
    from servicedeck import paths
    paths.registry_file().write_text(paths.registry_file().read_text().replace("group: g", "group: nope"))
    assert cli.main(["validate"]) == 1
    assert "nope" in capsys.readouterr().out


def test_validate_file_argument(capsys, tmp_path):
    f = tmp_path / "draft.yaml"
    f.write_text("version: 1\nservices:\n  - id: a\n    name: [bad\n")
    assert cli.main(["validate", str(f)]) == 1
    assert "第" in capsys.readouterr().out

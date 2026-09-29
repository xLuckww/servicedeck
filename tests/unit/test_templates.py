import json
import os
import shlex
from pathlib import Path

import pytest

from servicedeck import envpath, templates
from servicedeck.templates import TemplateError


def make_venv(folder: Path, extra_bins=()):
    bin_dir = folder / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("python", *extra_bins):
        p = bin_dir / name
        p.write_text("#!/bin/sh\n")
        p.chmod(0o755)


def tokens(cmd):
    return shlex.split(cmd)


# ---------- 字段定义 ----------

def test_fields_for_every_template():
    for name in ("python", "node", "executable", "script", "custom"):
        fs = templates.fields(name)
        assert fs and all({"key", "label", "kind", "required"} <= set(f) for f in fs)
    assert [f["key"] for f in templates.fields("python")] == ["folder", "entry"]
    assert [f["key"] for f in templates.fields("custom")] == ["command", "folder"]


def test_template_list_has_labels():
    names = [t["id"] for t in templates.template_list()]
    assert names == ["python", "node", "executable", "script", "custom"]


def test_unknown_template():
    with pytest.raises(TemplateError):
        templates.build("docker", {})


# ---------- Python ----------

def test_python_venv_file(tmp_path):
    make_venv(tmp_path)
    (tmp_path / "main.py").write_text("print(1)\n")
    r = templates.build("python", {"folder": str(tmp_path), "entry": "main.py --debug"})
    assert tokens(r.command) == [str(tmp_path / ".venv/bin/python"), "main.py", "--debug"]
    assert r.cwd == str(tmp_path)
    assert any(".venv" in h for h in r.hints)


def test_python_venv_module_command(tmp_path):
    make_venv(tmp_path, extra_bins=["uvicorn"])
    r = templates.build("python", {"folder": str(tmp_path), "entry": "uvicorn app:app --port 18310"})
    assert tokens(r.command) == [str(tmp_path / ".venv/bin/uvicorn"), "app:app", "--port", "18310"]
    assert r.suggested_port == 18310


def test_python_venv_module_missing_in_venv(tmp_path):
    make_venv(tmp_path)
    with pytest.raises(TemplateError) as e:
        templates.build("python", {"folder": str(tmp_path), "entry": "uvicorn app:app"})
    assert e.value.field == "entry" and "uvicorn" in e.value.message


def test_python_venv_dash_m_and_python_prefix(tmp_path):
    make_venv(tmp_path)
    py = str(tmp_path / ".venv/bin/python")
    r = templates.build("python", {"folder": str(tmp_path), "entry": "-m http.server 18311"})
    assert tokens(r.command) == [py, "-m", "http.server", "18311"]
    r = templates.build("python", {"folder": str(tmp_path), "entry": "python -m http.server"})
    assert tokens(r.command)[0] == py
    r = templates.build("python", {"folder": str(tmp_path), "entry": "python3 -m http.server"})
    assert tokens(r.command)[0] == py


def test_python_uv_run(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "main.py").write_text("")
    uv = envpath.resolve_tool("uv")
    r = templates.build("python", {"folder": str(tmp_path), "entry": "main.py"})
    assert tokens(r.command) == [uv, "run", "main.py"]
    r = templates.build("python", {"folder": str(tmp_path), "entry": "uvicorn app:app"})
    assert tokens(r.command) == [uv, "run", "uvicorn", "app:app"]
    r = templates.build("python", {"folder": str(tmp_path), "entry": "python -m app"})
    assert tokens(r.command) == [uv, "run", "python", "-m", "app"]
    assert any("uv" in h for h in r.hints)


def test_python_venv_preferred_over_uv(tmp_path):
    make_venv(tmp_path)
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "main.py").write_text("")
    r = templates.build("python", {"folder": str(tmp_path), "entry": "main.py"})
    assert ".venv/bin/python" in r.command


def test_python_no_env(tmp_path):
    (tmp_path / "main.py").write_text("")
    with pytest.raises(TemplateError) as e:
        templates.build("python", {"folder": str(tmp_path), "entry": "main.py"})
    assert e.value.field == "folder" and "虚拟环境" in e.value.message


def test_python_missing_entry_file(tmp_path):
    make_venv(tmp_path)
    with pytest.raises(TemplateError) as e:
        templates.build("python", {"folder": str(tmp_path), "entry": "nope.py"})
    assert e.value.field == "entry"


def test_python_missing_folder(tmp_path):
    with pytest.raises(TemplateError) as e:
        templates.build("python", {"folder": str(tmp_path / "nope"), "entry": "main.py"})
    assert e.value.field == "folder"


def test_python_required_fields(tmp_path):
    with pytest.raises(TemplateError) as e:
        templates.build("python", {"folder": str(tmp_path), "entry": "  "})
    assert e.value.field == "entry"


def test_python_never_bare_python(tmp_path):
    make_venv(tmp_path)
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "a.py").write_text("")
    for entry in ("a.py", "python a.py", "python3 a.py", "-m x", "python -m x"):
        first = tokens(templates.build("python", {"folder": str(tmp_path), "entry": entry}).command)[0]
        assert os.path.isabs(first), entry


def test_python_folder_with_space_and_tilde(tmp_path, monkeypatch):
    folder = tmp_path / "my app"
    make_venv(folder)
    (folder / "main.py").write_text("")
    r = templates.build("python", {"folder": str(folder), "entry": "main.py"})
    assert tokens(r.command)[0] == str(folder / ".venv/bin/python")
    monkeypatch.setenv("HOME", str(tmp_path))
    r = templates.build("python", {"folder": "~/my app", "entry": "main.py"})
    assert r.cwd == str(folder)


# ---------- Node ----------

def write_pkg(folder: Path, scripts):
    (folder / "package.json").write_text(json.dumps({"name": "x", "scripts": scripts}))


def test_node_choices_and_build(tmp_path):
    write_pkg(tmp_path, {"dev": "vite --port 5173", "start": "node server.js", "build": "vite build"})
    choices = templates.choices("node", "script", {"folder": str(tmp_path)})
    assert [c["value"] for c in choices] == ["dev", "start", "build"]
    assert "vite --port 5173" in choices[0]["label"]
    npm = envpath.resolve_tool("npm")
    r = templates.build("node", {"folder": str(tmp_path), "script": "start"})
    assert tokens(r.command) == [npm, "run", "start"]
    assert r.cwd == str(tmp_path)


def test_node_suggested_port_from_script(tmp_path):
    write_pkg(tmp_path, {"dev": "vite --port 5173"})
    assert templates.build("node", {"folder": str(tmp_path), "script": "dev"}).suggested_port == 5173


def test_node_with_mise_config_uses_mise_exec(tmp_path):
    write_pkg(tmp_path, {"start": "node server.js"})
    (tmp_path / "mise.toml").write_text('[tools]\nnode = "22"\n')
    mise = envpath.resolve_tool("mise")
    r = templates.build("node", {"folder": str(tmp_path), "script": "start"})
    assert tokens(r.command) == [mise, "exec", "--", "npm", "run", "start"]


def test_node_mise_config_in_parent(tmp_path):
    (tmp_path / ".tool-versions").write_text("node 22.23.3\n")
    sub = tmp_path / "web"
    sub.mkdir()
    write_pkg(sub, {"start": "node server.js"})
    r = templates.build("node", {"folder": str(sub), "script": "start"})
    assert tokens(r.command)[1:3] == ["exec", "--"]


def test_node_mise_config_without_node_ignored(tmp_path):
    write_pkg(tmp_path, {"start": "x"})
    (tmp_path / "mise.toml").write_text('[tools]\npython = "3.12"\n')
    r = templates.build("node", {"folder": str(tmp_path), "script": "start"})
    assert tokens(r.command)[1:] == ["run", "start"]


def test_node_errors(tmp_path):
    with pytest.raises(TemplateError) as e:
        templates.build("node", {"folder": str(tmp_path), "script": "start"})
    assert e.value.field == "folder" and "package.json" in e.value.message
    (tmp_path / "package.json").write_text("{bad json")
    with pytest.raises(TemplateError) as e:
        templates.build("node", {"folder": str(tmp_path), "script": "start"})
    assert e.value.field == "folder"
    write_pkg(tmp_path, {})
    with pytest.raises(TemplateError):
        templates.choices("node", "script", {"folder": str(tmp_path)})
    write_pkg(tmp_path, {"dev": "x"})
    with pytest.raises(TemplateError) as e:
        templates.build("node", {"folder": str(tmp_path), "script": "start"})
    assert e.value.field == "script"


# ---------- 可执行文件 ----------

def test_executable(tmp_path):
    exe = tmp_path / "server"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    r = templates.build("executable", {"file": str(exe), "args": "-c config.toml"})
    assert tokens(r.command) == [str(exe), "-c", "config.toml"]
    assert r.cwd is None


def test_executable_errors(tmp_path):
    with pytest.raises(TemplateError) as e:
        templates.build("executable", {"file": str(tmp_path / "nope")})
    assert e.value.field == "file"
    f = tmp_path / "noexec"
    f.write_text("")
    with pytest.raises(TemplateError) as e:
        templates.build("executable", {"file": str(f)})
    assert "执行权限" in e.value.message
    with pytest.raises(TemplateError):
        templates.build("executable", {"file": str(tmp_path)})  # 是文件夹


# ---------- Shell 脚本 ----------

def test_script(tmp_path):
    s = tmp_path / "run.sh"
    s.write_text("#!/bin/bash\n")
    s.chmod(0o755)
    r = templates.build("script", {"file": str(s), "args": "run"})
    assert tokens(r.command) == [str(s), "run"]
    assert r.cwd == str(tmp_path)  # 默认在脚本所在文件夹运行


def test_script_custom_folder(tmp_path):
    (tmp_path / "scripts").mkdir()
    s = tmp_path / "scripts" / "run.sh"
    s.write_text("#!/bin/bash\n")
    s.chmod(0o755)
    r = templates.build("script", {"file": str(s), "folder": str(tmp_path)})
    assert r.cwd == str(tmp_path)


def test_script_not_executable_is_fixable(tmp_path):
    s = tmp_path / "run.sh"
    s.write_text("#!/bin/bash\n")
    with pytest.raises(TemplateError) as e:
        templates.build("script", {"file": str(s)})
    assert e.value.field == "file" and e.value.fix == "chmod"
    templates.fix_permission(str(s))
    assert os.access(s, os.X_OK)
    templates.build("script", {"file": str(s)})


# ---------- 自定义命令 ----------

def test_custom(tmp_path):
    r = templates.build("custom", {"command": "ollama serve", "folder": str(tmp_path)})
    assert r.command == "ollama serve" and r.cwd == str(tmp_path)
    r = templates.build("custom", {"command": "ollama serve"})
    assert r.cwd is None


def test_custom_errors(tmp_path):
    with pytest.raises(TemplateError) as e:
        templates.build("custom", {"command": ""})
    assert e.value.field == "command"
    with pytest.raises(TemplateError) as e:
        templates.build("custom", {"command": "x", "folder": str(tmp_path / "nope")})
    assert e.value.field == "folder"


def test_custom_warns_bare_python():
    r = templates.build("custom", {"command": "python app.py"})
    assert any("python" in h for h in r.hints)


# ---------- 回填 ----------

def test_template_fields_roundtrip(tmp_path):
    make_venv(tmp_path)
    (tmp_path / "main.py").write_text("")
    inputs = {"folder": str(tmp_path), "entry": "main.py"}
    r1 = templates.build("python", inputs)
    assert r1.template_fields == inputs
    r2 = templates.build("python", r1.template_fields)
    assert r2.command == r1.command


def moved_venv(folder: Path, prog: str, module_kind="package"):
    """模拟"在别处创建后被移动过"的虚拟环境：启动文件第一行指向已不存在的旧位置。"""
    make_venv(folder)
    script = folder / ".venv" / "bin" / prog
    script.write_text("#!/Users/nobody/Downloads/old-place/.venv/bin/python3.14\nimport sys\n")
    script.chmod(0o755)
    site = folder / ".venv" / "lib" / "python3.14" / "site-packages"
    site.mkdir(parents=True)
    if module_kind == "package":
        (site / prog.replace("-", "_")).mkdir()
    elif module_kind == "module":
        (site / f"{prog}.py").write_text("")


def test_python_moved_venv_falls_back_to_python_m(tmp_path):
    moved_venv(tmp_path, "uvicorn")
    r = templates.build("python", {"folder": str(tmp_path), "entry": "uvicorn app.main:app --port 8000"})
    assert tokens(r.command) == [str(tmp_path / ".venv/bin/python"), "-m", "uvicorn", "app.main:app", "--port", "8000"]
    assert any("移动" in h for h in r.hints)


def test_python_moved_venv_single_file_module(tmp_path):
    moved_venv(tmp_path, "tool", module_kind="module")
    r = templates.build("python", {"folder": str(tmp_path), "entry": "tool run"})
    assert tokens(r.command)[1:3] == ["-m", "tool"]


def test_python_moved_venv_without_module_explains(tmp_path):
    moved_venv(tmp_path, "weird", module_kind=None)
    with pytest.raises(TemplateError) as e:
        templates.build("python", {"folder": str(tmp_path), "entry": "weird"})
    assert e.value.field == "entry" and "移动" in e.value.message


def test_python_healthy_venv_script_unchanged(tmp_path):
    make_venv(tmp_path)
    script = tmp_path / ".venv" / "bin" / "uvicorn"
    script.write_text(f"#!{tmp_path}/.venv/bin/python\n")
    script.chmod(0o755)
    r = templates.build("python", {"folder": str(tmp_path), "entry": "uvicorn app:app"})
    assert tokens(r.command)[0] == str(script)

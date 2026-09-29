"""模板：把表单输入变成最终命令（设计 6.2）。

每个模板提供字段定义 fields()（供界面生成表单）和 build(inputs)。
build 返回命令、工作文件夹、提示信息；输入有误时抛出 TemplateError，field 指明是哪个输入框。
生成的命令里工具一律写绝对路径，且绝不生成裸的 python（设计 5.4）。
"""

import json
import os
import re
import shlex
import stat
from dataclasses import dataclass, field
from pathlib import Path

from servicedeck import envpath


class TemplateError(Exception):
    def __init__(self, field: str, message: str, fix: str | None = None):
        super().__init__(message)
        self.field = field
        self.message = message
        self.fix = fix  # 可一键修复的类型，如 "chmod"


@dataclass
class BuildResult:
    command: str
    cwd: str | None
    template_fields: dict
    hints: list[str] = field(default_factory=list)
    suggested_port: int | None = None


TEMPLATES = [
    {"id": "python", "label": "Python 程序", "description": "文件夹里的 Python 程序（自动使用 .venv 或 uv run）"},
    {"id": "node", "label": "Node 程序", "description": "带 package.json 的 Node 项目，从 scripts 里选一个运行"},
    {"id": "executable", "label": "可执行文件", "description": "直接运行一个程序文件"},
    {"id": "script", "label": "Shell 脚本", "description": "运行一个 .sh 脚本"},
    {"id": "custom", "label": "自定义命令", "description": "自己写命令（兜底）"},
]

FIELDS = {
    "python": [
        {"key": "folder", "label": "程序所在文件夹", "kind": "folder", "required": True},
        {"key": "entry", "label": "入口", "kind": "text", "required": True,
         "help": "入口文件（如 main.py），或模块写法（如 uvicorn app:app --port 8000）"},
    ],
    "node": [
        {"key": "folder", "label": "项目文件夹（含 package.json）", "kind": "folder", "required": True},
        {"key": "script", "label": "运行哪个脚本", "kind": "select", "required": True,
         "help": "从 package.json 的 scripts 里选"},
    ],
    "executable": [
        {"key": "file", "label": "程序文件", "kind": "file", "required": True},
        {"key": "args", "label": "参数", "kind": "text", "required": False},
    ],
    "script": [
        {"key": "file", "label": "脚本文件（.sh）", "kind": "file", "required": True},
        {"key": "args", "label": "参数", "kind": "text", "required": False},
        {"key": "folder", "label": "工作文件夹", "kind": "folder", "required": False,
         "help": "不填则在脚本所在文件夹运行"},
    ],
    "custom": [
        {"key": "command", "label": "命令", "kind": "text", "required": True},
        {"key": "folder", "label": "工作文件夹", "kind": "folder", "required": False},
    ],
}

PORT_RE = re.compile(r"(?:--port|-p)[= ](\d{2,5})\b")
MISE_CONFIG_NAMES = [
    "mise.toml", ".mise.toml", "mise.local.toml", ".mise.local.toml",
    ".config/mise.toml", ".config/mise/config.toml", ".mise/config.toml", ".tool-versions",
]
NODE_IN_MISE_RE = re.compile(r"""^\s*["']?node(@[\w.]+)?["']?\s*(=|\s)""", re.M)


def template_list() -> list[dict]:
    return [dict(t) for t in TEMPLATES]


def fields(template: str) -> list[dict]:
    if template not in FIELDS:
        raise TemplateError("template", f"未知的模板：{template}")
    return [dict(f) for f in FIELDS[template]]


def choices(template: str, key: str, inputs: dict) -> list[dict]:
    """下拉框的可选项（目前只有 Node 模板的 script）。"""
    if template == "node" and key == "script":
        folder = _folder(inputs, "folder", required=True)
        scripts = _package_scripts(folder)
        if not scripts:
            raise TemplateError("folder", "package.json 里没有 scripts，无法选择要运行的脚本")
        return [{"value": k, "label": f"{k} — {v}"} for k, v in scripts.items()]
    return []


def build(template: str, inputs: dict) -> BuildResult:
    builders = {
        "python": _build_python,
        "node": _build_node,
        "executable": _build_executable,
        "script": _build_script,
        "custom": _build_custom,
    }
    if template not in builders:
        raise TemplateError("template", f"未知的模板：{template}")
    inputs = {k: (v.strip() if isinstance(v, str) else v) for k, v in inputs.items()}
    return builders[template](inputs)


def fix_permission(path: str) -> None:
    """给脚本加上执行权限（chmod u+x）。"""
    p = Path(os.path.expanduser(path))
    p.chmod(p.stat().st_mode | stat.S_IXUSR)


# ---------- 公共小工具 ----------

def _abs(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p))


def _required(inputs: dict, key: str, label: str) -> str:
    value = inputs.get(key) or ""
    if not value:
        raise TemplateError(key, f"请填写{label}")
    return value


def _folder(inputs: dict, key: str, required: bool) -> str | None:
    value = inputs.get(key) or ""
    if not value:
        if required:
            raise TemplateError(key, "请选择文件夹")
        return None
    folder = _abs(value)
    if not os.path.isdir(folder):
        raise TemplateError(key, f"文件夹不存在：{value}")
    return folder


def _split(text: str, key: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError as e:
        raise TemplateError(key, f"无法解析（引号可能没有配对）：{e}") from e


def _join(parts: list[str]) -> str:
    return shlex.join(parts)


def _port_in(text: str) -> int | None:
    m = PORT_RE.search(text)
    if m and 1 <= int(m.group(1)) <= 65535:
        return int(m.group(1))
    return None


def _tool(name: str, key: str) -> str:
    try:
        return envpath.resolve_tool(name)
    except envpath.ToolNotFound as e:
        raise TemplateError(key, str(e)) from e


# ---------- Python ----------

def _build_python(inputs: dict) -> BuildResult:
    folder = _folder(inputs, "folder", required=True)
    entry = _required(inputs, "entry", "入口")
    args = _split(entry, "entry")

    venv_bin = Path(folder) / ".venv" / "bin"
    if (venv_bin / "python").exists():
        mode = "venv"
        hints = ["使用文件夹里的虚拟环境 .venv"]
    elif (Path(folder) / "pyproject.toml").exists():
        mode = "uv"
        uv = _tool("uv", "folder")
        hints = ["使用 uv run 运行（没有 .venv 时 uv 会先自动创建）"]
    else:
        raise TemplateError(
            "folder",
            "该文件夹里没有虚拟环境（.venv），也没有 pyproject.toml。请先在该文件夹创建虚拟环境（例如运行 uv venv 并安装依赖）",
        )

    first, rest = args[0], args[1:]
    if first in ("python", "python3"):
        # 用户写了 python xxx：换成虚拟环境里的解释器
        if mode == "venv":
            cmd = [str(venv_bin / "python"), *rest]
        else:
            cmd = [uv, "run", "python", *rest]
    elif first == "-m":
        cmd = [str(venv_bin / "python"), *args] if mode == "venv" else [uv, "run", "python", *args]
    elif first.endswith(".py") or (Path(folder) / first).is_file():
        if not (Path(folder) / first).is_file():
            raise TemplateError("entry", f"入口文件不存在：{first}（相对于所选文件夹）")
        cmd = [str(venv_bin / "python"), *args] if mode == "venv" else [uv, "run", *args]
    else:
        # 模块写法，如 uvicorn app:app
        if mode == "venv":
            prog = venv_bin / first
            if not prog.exists():
                raise TemplateError("entry", f"虚拟环境里没有 {first}，请先安装它（如 uv pip install {first}）")
            if _broken_shebang(prog):
                # 虚拟环境在别处创建后被移动过：启动文件第一行还指向旧位置，但 .venv/bin/python 仍可用
                if not _venv_has_module(Path(folder), first):
                    raise TemplateError(
                        "entry",
                        f"虚拟环境里的 {first} 指向的位置已不存在（这个文件夹可能是在别处创建虚拟环境后被移动过来的）。"
                        "请在这个文件夹里删掉 .venv 重新创建并安装依赖",
                    )
                cmd = [str(venv_bin / "python"), "-m", first.replace("-", "_"), *rest]
                hints.append(f"这个文件夹的虚拟环境被移动过，{first} 的启动文件已失效，已改用 python -m {first} 运行")
            else:
                cmd = [str(prog), *rest]
        else:
            cmd = [uv, "run", *args]

    return BuildResult(
        command=_join(cmd),
        cwd=folder,
        template_fields={"folder": inputs["folder"], "entry": entry},
        hints=hints,
        suggested_port=_port_in(entry),
    )


def _broken_shebang(script: Path) -> bool:
    """启动文件第一行（#!解释器）指向的程序不存在。"""
    try:
        with open(script, "rb") as f:
            first_line = f.readline(4096)
    except OSError:
        return False
    if not first_line.startswith(b"#!"):
        return False
    parts = first_line[2:].decode("utf-8", "replace").strip().split()
    return bool(parts) and not os.path.exists(parts[0])


def _venv_has_module(folder: Path, name: str) -> bool:
    mod = name.replace("-", "_")
    for site in (folder / ".venv" / "lib").glob("python*/site-packages"):
        if (site / mod).is_dir() or (site / f"{mod}.py").is_file():
            return True
    return False


# ---------- Node ----------

def _package_scripts(folder: str) -> dict[str, str]:
    pkg = Path(folder) / "package.json"
    if not pkg.is_file():
        raise TemplateError("folder", "该文件夹里没有 package.json，不是 Node 项目")
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise TemplateError("folder", f"package.json 格式有误：{e}") from e
    scripts = data.get("scripts") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in (scripts or {}).items()}


def _mise_manages_node(folder: str) -> bool:
    d = Path(folder)
    for parent in (d, *d.parents):
        for name in MISE_CONFIG_NAMES:
            f = parent / name
            if f.is_file():
                try:
                    if NODE_IN_MISE_RE.search(f.read_text(encoding="utf-8", errors="ignore")):
                        return True
                except OSError:
                    pass
    return False


def _build_node(inputs: dict) -> BuildResult:
    folder = _folder(inputs, "folder", required=True)
    scripts = _package_scripts(folder)
    script = _required(inputs, "script", "要运行的脚本")
    if script not in scripts:
        raise TemplateError("script", f"package.json 里没有名为 {script} 的脚本")
    if _mise_manages_node(folder):
        cmd = [_tool("mise", "folder"), "exec", "--", "npm", "run", script]
        hints = ["该项目由 mise 指定 Node 版本，使用 mise exec 保证版本一致"]
    else:
        cmd = [_tool("npm", "folder"), "run", script]
        hints = []
    return BuildResult(
        command=_join(cmd),
        cwd=folder,
        template_fields={"folder": inputs["folder"], "script": script},
        hints=hints,
        suggested_port=_port_in(scripts[script]),
    )


# ---------- 可执行文件 / Shell 脚本 ----------

def _check_file(inputs: dict, key: str) -> str:
    raw = _required(inputs, key, "文件")
    path = _abs(raw)
    if not os.path.exists(path):
        raise TemplateError(key, f"文件不存在：{raw}")
    if not os.path.isfile(path):
        raise TemplateError(key, f"这是文件夹，不是文件：{raw}")
    return path


def _build_executable(inputs: dict) -> BuildResult:
    path = _check_file(inputs, "file")
    if not os.access(path, os.X_OK):
        raise TemplateError("file", "该文件没有执行权限，不是可直接运行的程序")
    args = inputs.get("args") or ""
    return BuildResult(
        command=_join([path, *_split(args, "args")]),
        cwd=None,
        template_fields={"file": inputs["file"], "args": args},
        suggested_port=_port_in(args),
    )


def _build_script(inputs: dict) -> BuildResult:
    path = _check_file(inputs, "file")
    if not os.access(path, os.X_OK):
        raise TemplateError("file", "脚本没有执行权限，可以一键添加", fix="chmod")
    folder = _folder(inputs, "folder", required=False) or os.path.dirname(path)
    args = inputs.get("args") or ""
    hints = [] if path.endswith(".sh") else ["文件扩展名不是 .sh，请确认它是 Shell 脚本"]
    fields_out = {"file": inputs["file"], "args": args}
    if inputs.get("folder"):
        fields_out["folder"] = inputs["folder"]
    return BuildResult(
        command=_join([path, *_split(args, "args")]),
        cwd=folder,
        template_fields=fields_out,
        hints=hints,
        suggested_port=_port_in(args),
    )


# ---------- 自定义命令 ----------

def _build_custom(inputs: dict) -> BuildResult:
    command = _required(inputs, "command", "命令")
    folder = _folder(inputs, "folder", required=False)
    hints = []
    first = _split(command, "command")[0]
    if first == "python":
        hints.append("直接写 python 在很多文件夹里会报错，建议写成虚拟环境里的完整路径（如 /路径/.venv/bin/python）")
    fields_out = {"command": command}
    if inputs.get("folder"):
        fields_out["folder"] = inputs["folder"]
    return BuildResult(
        command=command,
        cwd=folder,
        template_fields=fields_out,
        hints=hints,
        suggested_port=_port_in(command),
    )

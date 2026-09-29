"""读写服务清单：加载、校验、保存（加锁 + 备份 + 原子替换）、对比、恢复备份（设计 5.1、5.2、9）。"""

import fcntl
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from servicedeck import paths
from servicedeck.models import Registry

MAX_BACKUPS = 20

# 同一进程内的多个线程先排队，再用 flock 与其他进程互斥
_thread_lock = threading.RLock()


class RegistryError(Exception):
    """清单文件无法解析或结构不对。line 为出错行号（从 1 开始），可能为空。"""

    def __init__(self, message: str, line: int | None = None):
        super().__init__(message)
        self.line = line


@dataclass
class Issue:
    """一条校验问题。service 为空表示整体问题；field 用于界面就近显示。"""

    service: str | None
    field: str
    message: str


class ValidationFailed(Exception):
    def __init__(self, issues: list[Issue]):
        super().__init__("；".join(i.message for i in issues))
        self.issues = issues


@dataclass
class Diff:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: dict[str, list[str]] = field(default_factory=dict)  # 服务 id → 变了的字段

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


# ---------- 读取 ----------

def load() -> Registry:
    """读取清单。文件不存在时返回空清单。"""
    f = paths.registry_file()
    if not f.exists():
        return Registry()
    return load_file(f)


def load_file(f: Path) -> Registry:
    text = f.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        line = mark.line + 1 if mark else None
        where = f"第 {line} 行" if line else "某处"
        raise RegistryError(f"清单文件 {f.name} {where}格式有误：{e.problem or e}", line=line) from e
    except yaml.YAMLError as e:
        raise RegistryError(f"清单文件 {f.name} 格式有误：{e}") from e
    if data is None:
        return Registry()
    if not isinstance(data, dict):
        raise RegistryError(f"清单文件 {f.name} 的最外层应该是 version / groups / services 这样的键值结构")
    try:
        return Registry.model_validate(data)
    except ValidationError as e:
        raise RegistryError(f"清单文件 {f.name} 内容有误：{_describe_validation_error(e, data)}") from e


def _describe_validation_error(e: ValidationError, data: dict) -> str:
    parts = []
    for err in e.errors():
        loc = list(err["loc"])
        label = ".".join(str(x) for x in loc)
        # services.0.xxx → 用服务 id 说明是哪个服务
        if len(loc) >= 2 and loc[0] == "services" and isinstance(loc[1], int):
            try:
                sid = data["services"][loc[1]].get("id") or f"第 {loc[1] + 1} 个服务"
            except (KeyError, IndexError, AttributeError, TypeError):
                sid = f"第 {loc[1] + 1} 个服务"
            label = f"服务 {sid} 的 " + ".".join(str(x) for x in loc[2:])
        msg = "缺少必填项" if err["type"] == "missing" else err["msg"]
        parts.append(f"{label}：{msg}")
    return "；".join(parts)


# ---------- 校验 ----------

def _expand(p: str, base: str | None = None) -> Path:
    path = Path(os.path.expanduser(p))
    if not path.is_absolute() and base:
        path = Path(os.path.expanduser(base)) / path
    return path


def validate(reg: Registry) -> list[Issue]:
    issues: list[Issue] = []

    group_ids = [g.id for g in reg.groups]
    for gid in {g for g in group_ids if group_ids.count(g) > 1}:
        issues.append(Issue(None, "groups", f"分组 id「{gid}」重复"))

    seen_ids: set[str] = set()
    seen_names: dict[str, str] = {}
    seen_ports: dict[int, str] = {}
    all_ids = {s.id for s in reg.services}

    for s in reg.services:
        if s.id in seen_ids:
            issues.append(Issue(s.id, "id", f"服务 id「{s.id}」重复"))
        seen_ids.add(s.id)

        if s.name in seen_names:
            issues.append(Issue(s.id, "name", f"名称「{s.name}」已被其他服务使用"))
        else:
            seen_names[s.name] = s.id

        if s.group not in group_ids:
            issues.append(Issue(s.id, "group", f"分组「{s.group}」不存在"))

        if not s.command.strip():
            issues.append(Issue(s.id, "command", "命令不能为空"))

        if s.port is not None:
            if not 1 <= s.port <= 65535:
                issues.append(Issue(s.id, "port", f"端口 {s.port} 不在 1–65535 范围内"))
            elif s.port in seen_ports:
                issues.append(Issue(s.id, "port", f"端口 {s.port} 已被服务「{seen_ports[s.port]}」使用"))
            else:
                seen_ports[s.port] = s.id

        if s.health and s.health.type == "http" and s.port is None:
            issues.append(Issue(s.id, "health", "网页健康检查需要先填写端口"))

        if s.cwd and not _expand(s.cwd).is_dir():
            issues.append(Issue(s.id, "cwd", f"文件夹不存在：{s.cwd}"))

        if s.env_file and not _expand(s.env_file, s.cwd).is_file():
            issues.append(Issue(s.id, "env_file", f"环境变量文件不存在：{s.env_file}"))

        for dep in s.depends_on:
            if dep == s.id:
                issues.append(Issue(s.id, "depends_on", "不能依赖自己"))
            elif dep not in all_ids:
                issues.append(Issue(s.id, "depends_on", f"依赖的服务「{dep}」不存在"))

    issues.extend(_cycle_issues(reg))
    return issues


def _cycle_issues(reg: Registry) -> list[Issue]:
    graph = {s.id: [d for d in s.depends_on if d != s.id] for s in reg.services}
    WHITE, GREY, BLACK = 0, 1, 2
    color = {sid: WHITE for sid in graph}
    reported: set[frozenset] = set()
    issues: list[Issue] = []

    def visit(node: str, stack: list[str]):
        color[node] = GREY
        stack.append(node)
        for dep in graph.get(node, []):
            if dep not in color:
                continue
            if color[dep] == GREY:
                cycle = stack[stack.index(dep):] + [dep]
                key = frozenset(cycle)
                if key not in reported:
                    reported.add(key)
                    issues.append(Issue(node, "depends_on", "依赖形成循环：" + " → ".join(cycle)))
            elif color[dep] == WHITE:
                visit(dep, stack)
        stack.pop()
        color[node] = BLACK

    for sid in graph:
        if color[sid] == WHITE:
            visit(sid, [])
    return issues


# ---------- 保存 ----------

@contextmanager
def locked():
    """写清单时的互斥锁（线程 + 进程）。"""
    paths.home().mkdir(parents=True, exist_ok=True)
    with _thread_lock:
        with open(paths.registry_lock_file(), "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def dump(reg: Registry) -> str:
    data = reg.model_dump(mode="json", exclude_none=True)
    for s in data["services"]:
        for key in [k for k, v in s.items() if v in ({}, [])]:
            del s[key]
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000)


def save(reg: Registry) -> None:
    """校验 → 加锁 → 备份旧文件 → 写临时文件 → 原子替换（权限 600）。"""
    issues = validate(reg)
    if issues:
        raise ValidationFailed(issues)
    text = dump(reg)
    target = paths.registry_file()
    with locked():
        if target.exists():
            _backup(target)
        _atomic_write(target, text)


def _atomic_write(target: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _backup(target: Path) -> None:
    d = paths.backups_dir()
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    n = time.time_ns() % 1_000_000_000
    dest = d / f"services-{stamp}-{n:09d}.yaml"
    while dest.exists():
        n += 1
        dest = d / f"services-{stamp}-{n:09d}.yaml"
    dest.write_bytes(target.read_bytes())
    os.chmod(dest, 0o600)
    for old in list_backups()[MAX_BACKUPS:]:
        old.unlink(missing_ok=True)


def list_backups() -> list[Path]:
    """备份文件列表，最新的在前。"""
    d = paths.backups_dir()
    if not d.exists():
        return []
    return sorted(d.glob("services-*.yaml"), key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)


def restore_backup(index: int) -> Registry:
    """恢复到第 index 份备份（0 = 最新）。恢复前的当前版本也会进备份，可以反悔。"""
    backups = list_backups()
    if not 0 <= index < len(backups):
        raise RegistryError(f"没有第 {index + 1} 份备份（共 {len(backups)} 份）")
    reg = load_file(backups[index])
    save(reg)
    return reg


# ---------- 对比 ----------

def diff(old: Registry, new: Registry) -> Diff:
    old_map = {s.id: s for s in old.services}
    new_map = {s.id: s for s in new.services}
    result = Diff(
        added=[sid for sid in new_map if sid not in old_map],
        removed=[sid for sid in old_map if sid not in new_map],
    )
    for sid, s in new_map.items():
        if sid in old_map and old_map[sid] != s:
            a, b = old_map[sid].model_dump(), s.model_dump()
            result.changed[sid] = [k for k in b if a.get(k) != b.get(k)]
    return result

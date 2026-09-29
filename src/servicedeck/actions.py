"""所有操作的统一入口（设计 6.2、6.3、7）。菜单栏和窗口只调用这里，安全检查集中在这一处。

每个操作返回 Result。需要用户确认时返回 needs_confirm=True 和提示文字；
界面弹出确认框，用户确认后带上 confirm=True 再调用一次（强制结束进程再带 force=True）。
"""

import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from servicedeck import discovery, paths, registry, status, templates
from servicedeck.compose import config, lifecycle
from servicedeck.compose.client import ComposeClient, ComposeError, ComposeUnavailable
from servicedeck.models import Group, Registry, Service, make_id

STOP_WAIT_SECONDS = 5
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")  # 终端颜色等控制码
TRIAL_SECONDS = 15


@dataclass
class Result:
    ok: bool = True
    message: str = ""
    needs_confirm: bool = False
    confirm_kind: str | None = None
    prompt: str = ""
    affected: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)  # [{"field": ..., "message": ..., "fix": ...}]
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _ok(message: str = "", **data) -> Result:
    return Result(ok=True, message=message, data=data)


def _fail(message: str, errors: list[dict] | None = None, **data) -> Result:
    return Result(ok=False, message=message, errors=errors or [], data=data)


def _confirm(kind: str, prompt: str, affected: list[str] | None = None, **data) -> Result:
    return Result(ok=False, needs_confirm=True, confirm_kind=kind, prompt=prompt, affected=affected or [], data=data)


def _safe(fn):
    """把已知的错误转成中文的失败结果，不把 Python 报错抛给界面。"""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except registry.RegistryError as e:
            return _fail(f"服务清单有误：{e}")
        except registry.ValidationFailed as e:
            return _fail(f"服务清单校验没通过：{e}")
        except (lifecycle.LifecycleError, lifecycle.ApplyError) as e:
            return _fail(str(e))
        except ComposeUnavailable:
            return _fail("服务管理器未运行")
        except ComposeError as e:
            return _fail(f"服务管理器报错：{e}")
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# ---------- 公共小工具 ----------

def _processes() -> dict[str, dict] | None:
    """{服务 id: 状态}；服务管理器未运行时返回 None。"""
    try:
        return {p["name"]: p for p in ComposeClient().processes()}
    except ComposeError:
        return None


def _active(proc: dict | None) -> bool:
    """在运行或正在启动（包括等待依赖）。"""
    if not proc:
        return False
    return bool(proc.get("is_running")) or proc.get("status") in status.STARTING_STATES


def _dependency_order(reg: Registry, sid: str) -> list[str]:
    """sid 的全部（间接）依赖，按应当启动的先后排序，不含 sid 本身。"""
    order: list[str] = []
    seen: set[str] = set()

    def visit(x: str):
        if x in seen:
            return
        seen.add(x)
        s = reg.service(x)
        for dep in (s.depends_on if s else []):
            visit(dep)
        if x != sid:
            order.append(x)

    visit(sid)
    return order


def _dependents(reg: Registry, sid: str) -> list[str]:
    """（间接）依赖 sid 的服务。"""
    result: list[str] = []
    frontier = [sid]
    while frontier:
        cur = frontier.pop()
        for s in reg.services:
            if cur in s.depends_on and s.id not in result and s.id != sid:
                result.append(s.id)
                frontier.append(s.id)
    return result


def _name(reg: Registry, sid: str) -> str:
    s = reg.service(sid)
    return s.name if s else sid


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace").stdout


def port_owners(port: int) -> list[dict]:
    """正在监听该端口的进程，并标出是否受管、是否属于"未纳入管理"。"""
    out = _run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-F", "pcn"])
    listeners = discovery.parse_lsof(out)
    if not listeners:
        return []
    parents = discovery.parse_ps(_run(["ps", "-axo", "pid=,ppid="]))
    pc_pid = lifecycle.read_pid()
    owners = []
    for pid, l in listeners.items():
        exe = discovery.exe_path(pid) or l.command
        managed = bool(pc_pid) and (pid == pc_pid or discovery.descends_from(pid, pc_pid, parents))
        unmanaged = bool(discovery.find_unmanaged({pid: l}, parents, lambda p: exe, pc_pid, set()))
        owners.append({"pid": pid, "title": discovery.display_title(exe, discovery.process_args(pid)),
                       "exe": exe, "managed": managed, "unmanaged": unmanaged})
    return owners


def _foreign_owner(port: int, allow_pid: int | None = None) -> dict | None:
    for o in port_owners(port):
        if not o["managed"] and o["pid"] != allow_pid:
            return o
    return None


def _occupied_message(port: int, o: dict) -> str:
    return f"端口 {port} 已被 {o['title']}（PID {o['pid']}）占用"


def _process_uid(pid: int) -> int | None:
    out = _run(["ps", "-o", "uid=", "-p", str(pid)]).strip()
    return int(out) if out.isdigit() else None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = _run(["ps", "-o", "stat=", "-p", str(pid)]).strip()
    return bool(state) and not state.startswith("Z")


def _terminate(pid: int, force: bool) -> bool:
    """先 SIGTERM 等 5 秒；force 时直接 SIGKILL。返回进程是否已退出。"""
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.time() + (2 if force else STOP_WAIT_SECONDS)
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return not _alive(pid)


# ---------- 启停 ----------

@_safe
def start(service_id: str, confirm: bool = False) -> Result:
    reg = registry.load()
    s = reg.service(service_id)
    if s is None:
        return _fail(f"找不到服务 {service_id}")
    procs = _processes()
    if procs is not None and _active(procs.get(service_id)):
        return _ok(f"「{s.name}」已在运行")

    if s.port:
        o = _foreign_owner(s.port)
        if o:
            hint = "，可以先停止它" if o["unmanaged"] else ""
            return _fail(_occupied_message(s.port, o) + hint, occupant=o)

    to_start = [d for d in _dependency_order(reg, service_id) if procs is None or not _active(procs.get(d))]
    reasons = [reg.service(d).start_warning for d in to_start if reg.service(d) and reg.service(d).start_warning]
    if s.start_warning:
        reasons.append(s.start_warning)
    if procs is None:
        reasons.append("服务管理器未运行，需要先启动它；这会同时启动所有跟随「全部启动」的服务。")
    if reasons and not confirm:
        return _confirm("start", "\n".join(reasons), affected=[_name(reg, d) for d in to_start])

    client = ComposeClient()
    status.clear_user_stopped([service_id, *_dependency_order(reg, service_id)])
    if procs is None:
        lifecycle.ensure_running()
        procs = _processes() or {}
    for dep in _dependency_order(reg, service_id):
        if not _active(procs.get(dep)):
            client.start(dep)
    if not _active(procs.get(service_id)):
        client.start(service_id)
    return _ok(f"正在启动「{s.name}」")


@_safe
def stop(service_id: str, confirm: bool = False) -> Result:
    reg = registry.load()
    s = reg.service(service_id)
    if s is None:
        return _fail(f"找不到服务 {service_id}")
    procs = _processes()
    if procs is None or not _active(procs.get(service_id)):
        return _ok(f"「{s.name}」已经停止")
    running_dependents = [d for d in _dependents(reg, service_id) if _active(procs.get(d))]
    if running_dependents and not confirm:
        names = "、".join(_name(reg, d) for d in running_dependents)
        return _confirm("stop", f"停止「{s.name}」会影响正在运行的：{names}（它们依赖它）。确定停止吗？",
                        affected=running_dependents)
    try:
        ComposeClient().stop(service_id)
    except ComposeError as e:
        if "not running" not in str(e):
            raise
    status.mark_user_stopped([service_id])
    return _ok(f"已停止「{s.name}」")


@_safe
def restart(service_id: str, confirm: bool = False) -> Result:
    reg = registry.load()
    s = reg.service(service_id)
    if s is None:
        return _fail(f"找不到服务 {service_id}")
    procs = _processes()
    if procs is None or not _active(procs.get(service_id)):
        return start(service_id, confirm=confirm)
    status.clear_user_stopped([service_id])
    ComposeClient().restart(service_id)
    return _ok(f"正在重启「{s.name}」")


@_safe
def start_all(confirm: bool = False) -> Result:
    reg = registry.load()
    procs = _processes()
    pending = [s for s in reg.services if s.autostart and (procs is None or not _active(procs.get(s.id)))]
    warnings = [f"「{s.name}」：{s.start_warning}" for s in pending if s.start_warning]
    if warnings and not confirm:
        return _confirm("start_all", "\n".join(warnings), affected=[s.id for s in pending if s.start_warning])
    status.clear_user_stopped([s.id for s in reg.services if s.autostart])
    lifecycle.start_all()
    return _ok("正在启动全部服务")


@_safe
def stop_all() -> Result:
    """停止全部服务和服务管理器（菜单栏会变成 ⚪）。"""
    lifecycle.shutdown()
    status.clear_user_stopped()  # 管理器已停，下次启动时一切从头开始
    return _ok("已停止全部服务")


@_safe
def summary() -> Result:
    return _ok(**status.collect().to_dict())


def overview() -> Result:
    """窗口需要的全部状态：汇总 + 每个服务的配置要点 + 分组。清单解析失败时返回错误和行号（不抛出）。"""
    try:
        reg = registry.load()
    except registry.RegistryError as e:
        return _ok(registry_error=str(e), registry_error_line=e.line, manager_running=ComposeClient().is_alive(),
                   overall="red", running_count=0, error_count=0, groups=[], services={})
    sm = status.collect(reg).to_dict()
    configs = {s.id: s for s in reg.services}
    services = {}
    for st in sm["services"]:
        c = configs[st["id"]]
        services[st["id"]] = {**st, "command": c.command, "cwd": c.cwd, "depends_on": c.depends_on,
                              "template": c.template, "autostart": c.autostart, "start_warning": c.start_warning}
    groups = [{"id": g["id"], "name": g["name"], "running": g["running"], "total": g["total"],
               "services": [x["id"] for x in g["services"]]} for g in sm["groups"]]
    return _ok(registry_error=None, registry_error_line=None, manager_running=sm["manager_running"],
               overall=sm["overall"], running_count=sm["running_count"], error_count=sm["error_count"],
               groups=groups, services=services)


@_safe
def service_detail(service_id: str) -> Result:
    reg = registry.load()
    s = reg.service(service_id)
    if s is None:
        return _fail(f"找不到服务 {service_id}")
    st = status.collect(reg).service(service_id)
    return _ok(service=s.model_dump(mode="json"), status=asdict(st),
               depends_on=[_name(reg, d) for d in s.depends_on],
               dependents=[_name(reg, d) for d in _dependents(reg, service_id)],
               group_name=reg.group(s.group).name if reg.group(s.group) else s.group,
               log_file=str(paths.service_log_file(service_id)))


@_safe
def list_backups() -> Result:
    items = []
    for i, f in enumerate(registry.list_backups()):
        try:
            n = len(registry.load_file(f).services)
        except registry.RegistryError:
            n = None
        items.append({"index": i, "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime)),
                      "services": n, "file": f.name})
    return _ok(items=items)


@_safe
def restore_backup(index: int, confirm: bool = False) -> Result:
    backups = registry.list_backups()
    if not 0 <= index < len(backups):
        return _fail("找不到这份备份")
    if not confirm:
        return _confirm("restore", "恢复到这份备份？当前的服务清单也会先备份，可以反悔。正在运行且配置有变化的服务会被重启。")
    reg = registry.load_file(backups[index])
    lifecycle.apply(reg)
    return _ok("已恢复")


def clean_log_lines(lines: list[str]) -> list[str]:
    """去掉终端颜色代码，以及 process-compose 在重启之间插入的空条目。"""
    out = []
    for line in lines:
        line = ANSI_RE.sub("", line).rstrip("\n")
        if line.strip():
            out.append(line)
    return out


@_safe
def service_logs(service_id: str, limit: int = 200) -> Result:
    try:
        lines = ComposeClient().logs(service_id, limit)
    except ComposeError:
        f = paths.service_log_file(service_id)
        lines = f.read_text(errors="replace").splitlines()[-limit:] if f.exists() else []
    # process-compose 在每次重启之间会插入只有换行的空条目，去掉
    lines = clean_log_lines(lines)
    return _ok(lines=lines, log_file=str(paths.service_log_file(service_id)))


# ---------- 模板与表单 ----------

@_safe
def template_catalog() -> Result:
    return _ok(templates=[{**t, "fields": templates.fields(t["id"])} for t in templates.template_list()])


def _template_error(e: templates.TemplateError) -> dict:
    return {"field": f"template_fields.{e.field}", "message": e.message, "fix": e.fix}


@_safe
def preview(template: str, inputs: dict) -> Result:
    try:
        b = templates.build(template, inputs)
    except templates.TemplateError as e:
        return _fail(e.message, errors=[_template_error(e)])
    return _ok(command=b.command, cwd=b.cwd, hints=b.hints, suggested_port=b.suggested_port,
               template_fields=b.template_fields)


@_safe
def template_choices(template: str, key: str, inputs: dict) -> Result:
    try:
        return _ok(choices=templates.choices(template, key, inputs))
    except templates.TemplateError as e:
        return _fail(e.message, errors=[_template_error(e)])


@_safe
def fix_script_permission(path: str) -> Result:
    try:
        templates.fix_permission(path)
    except OSError as e:
        return _fail(f"无法添加执行权限：{e}")
    return _ok("已添加执行权限")


DRAFT_FIELDS = ("name", "group", "template", "template_fields", "command", "cwd", "env", "env_file", "port",
                "health", "depends_on", "restart", "autostart", "shutdown_signal", "start_warning")


@_safe
def draft_for_edit(service_id: str) -> Result:
    s = registry.load().service(service_id)
    if s is None:
        return _fail(f"找不到服务 {service_id}")
    d = s.model_dump(mode="json")
    d.pop("id")
    return _ok(draft=d, id=service_id)


def _service_from_draft(draft: dict, sid: str) -> tuple[Service | None, list[dict], list[str]]:
    errors: list[dict] = []
    hints: list[str] = []
    template = draft.get("template") or "custom"
    inputs = draft.get("template_fields") or {}
    built = None
    if template == "custom" and not inputs.get("command") and draft.get("command"):
        inputs = {**inputs, "command": draft["command"]}
    if inputs or not draft.get("command"):
        try:
            built = templates.build(template, inputs)
            hints = built.hints
        except templates.TemplateError as e:
            errors.append(_template_error(e))
    # 没有模板原始输入、只有命令（如手写进清单的服务）：直接用命令和文件夹

    command = (draft.get("command") or "").strip() or (built.command if built else "")
    fields = {
        "id": sid,
        "name": (draft.get("name") or "").strip(),
        "group": draft.get("group") or "",
        "template": template,
        "command": command,
        "template_fields": built.template_fields if built else inputs,
        "cwd": built.cwd if built else draft.get("cwd"),
    }
    if not fields["name"]:
        errors.append({"field": "name", "message": "请填写名称", "fix": None})
    for key in ("env", "env_file", "port", "health", "depends_on", "restart", "autostart", "shutdown_signal",
                "start_warning"):
        value = draft.get(key)
        if value in (None, "", [], {}):
            continue
        fields[key] = value
    if errors and built is None:
        fields["command"] = fields["command"] or "-"  # 让其余字段也能得到校验
    try:
        svc = Service(**fields)
    except ValidationError as e:
        for err in e.errors():
            loc = err["loc"][0] if err["loc"] else "service"
            msg = "请填写数字" if loc == "port" else err["msg"]
            errors.append({"field": str(loc), "message": msg, "fix": None})
        return None, errors, hints
    return svc, errors, hints


def _changed_processes(old: Registry, new: Registry) -> set[str]:
    """process-compose 会认为配置变了（从而重启）的服务。"""
    changed = set()
    for s in new.services:
        o = old.service(s.id)
        if o is not None and config._process(o, old) != config._process(s, new):
            changed.add(s.id)
    return changed


@_safe
def save_service(draft: dict, service_id: str | None = None, confirm: bool = False, force: bool = False) -> Result:
    """新增（service_id 为空）或编辑服务。draft 里带 adopt_pid 时表示"纳入管理"。"""
    old = registry.load()
    if service_id is not None and old.service(service_id) is None:
        return _fail(f"找不到服务 {service_id}")
    sid = service_id or make_id(draft.get("name") or "", [s.id for s in old.services])
    svc, errors, hints = _service_from_draft(draft, sid)
    adopt_pid = draft.get("adopt_pid")

    new = old.model_copy(deep=True)
    if svc is not None:
        if service_id is None:
            new.services.append(svc)
        else:
            new.services = [svc if s.id == service_id else s for s in new.services]
        for issue in registry.validate(new):
            if issue.service in (sid, None):
                if not any(e["field"] == issue.field for e in errors):
                    errors.append({"field": issue.field, "message": issue.message, "fix": None})
            else:
                errors.append({"field": "service", "message": f"其他服务「{issue.service}」有问题：{issue.message}",
                               "fix": None})
        if svc.port and not any(e["field"] == "port" for e in errors):
            o = _foreign_owner(svc.port, allow_pid=adopt_pid)
            if o:
                errors.append({"field": "port", "message": _occupied_message(svc.port, o), "fix": None})
    if errors:
        return _fail("请修正标出的问题", errors=errors)

    procs = _processes()
    alive = procs is not None
    restarting = [x for x in _changed_processes(old, new) if alive and _active(procs.get(x))]
    lines = []
    if restarting:
        lines.append("将重启：" + "、".join(_name(new, x) for x in restarting))
    if adopt_pid:
        lines.append(f"保存后先停止原来手动启动的进程（PID {adopt_pid}），再由 ServiceDeck 启动。")
        if not alive:
            lines.append("服务管理器未运行，会先启动它（同时启动所有跟随「全部启动」的服务）。")
    if lines and not confirm:
        return _confirm("save", "\n".join(lines), affected=restarting)

    if adopt_pid and _alive(adopt_pid):
        check = _check_unmanaged(adopt_pid)
        if check:
            return check
        if not _terminate(adopt_pid, force):
            return _confirm("force_kill", f"原来的进程（PID {adopt_pid}）5 秒内没有退出，要强制结束吗？可能丢失它未保存的数据。")

    result = lifecycle.apply(new)
    if adopt_pid:
        r = start(sid, confirm=True)
        if not r.ok:
            return r
    return _ok("已保存", id=sid, changes=result.changes, hints=hints)


@_safe
def delete_service(service_id: str, confirm: bool = False) -> Result:
    reg = registry.load()
    s = reg.service(service_id)
    if s is None:
        return _fail(f"找不到服务 {service_id}")
    deps = [x.id for x in reg.services if service_id in x.depends_on]
    if deps:
        names = "、".join(_name(reg, d) for d in deps)
        return _fail(f"「{names}」依赖它，请先在它们的设置里去掉这个依赖", affected=deps)
    if not confirm:
        procs = _processes()
        extra = "它正在运行，删除后会被停止。" if procs and _active(procs.get(service_id)) else ""
        return _confirm("delete", f"删除「{s.name}」？不会删除它的程序文件和日志。{extra}")
    new = reg.model_copy(deep=True)
    new.services = [x for x in new.services if x.id != service_id]
    lifecycle.apply(new)
    return _ok(f"已删除「{s.name}」")


# ---------- 分组 ----------

def _save_groups(reg: Registry) -> None:
    registry.save(reg)


@_safe
def add_group(name: str) -> Result:
    name = (name or "").strip()
    if not name:
        return _fail("请填写分组名称")
    reg = registry.load()
    if any(g.name == name for g in reg.groups):
        return _fail(f"已有名为「{name}」的分组")
    gid = make_id(name, [g.id for g in reg.groups], prefix="group")
    reg.groups.append(Group(id=gid, name=name))
    _save_groups(reg)
    return _ok(f"已新建分组「{name}」", id=gid)


@_safe
def rename_group(group_id: str, name: str) -> Result:
    name = (name or "").strip()
    reg = registry.load()
    g = reg.group(group_id)
    if g is None:
        return _fail("找不到这个分组")
    if not name:
        return _fail("请填写分组名称")
    if any(x.name == name and x.id != group_id for x in reg.groups):
        return _fail(f"已有名为「{name}」的分组")
    g.name = name
    _save_groups(reg)
    return _ok("已改名")


@_safe
def reorder_groups(group_ids: list[str]) -> Result:
    reg = registry.load()
    if sorted(group_ids) != sorted(g.id for g in reg.groups):
        return _fail("分组列表不完整，请刷新后再试")
    reg.groups = [reg.group(gid) for gid in group_ids]
    _save_groups(reg)
    return _ok("已调整顺序")


@_safe
def delete_group(group_id: str) -> Result:
    reg = registry.load()
    g = reg.group(group_id)
    if g is None:
        return _fail("找不到这个分组")
    if reg.services_in_group(group_id):
        return _fail(f"「{g.name}」里还有服务，不能删除；请先把服务移到其他分组或删除")
    reg.groups = [x for x in reg.groups if x.id != group_id]
    _save_groups(reg)
    return _ok(f"已删除分组「{g.name}」")


# ---------- 未纳入管理的服务 ----------

@_safe
def list_unmanaged() -> Result:
    return _ok(items=[u.to_dict() for u in discovery.scan()])


def _check_unmanaged(pid: int) -> Result | None:
    """停止/纳入管理前的安全检查：只允许当前用户自己的、确实"未纳入管理"的进程。"""
    uid = _process_uid(pid)
    if uid is None:
        return _fail(f"进程 {pid} 已不存在")
    if uid != os.getuid():
        return _fail("这不是你的进程，不能停止")
    if pid == os.getpid():
        return _fail("不能停止 ServiceDeck 自己")
    pc_pid = lifecycle.read_pid()
    if pc_pid and (pid == pc_pid or discovery.descends_from(pid, pc_pid, discovery.parse_ps(_run(["ps", "-axo", "pid=,ppid="])))):
        return _fail("这是 ServiceDeck 管理的服务，请用服务自己的停止按钮")
    return None


def _find_unmanaged(pid: int):
    return next((u for u in discovery.scan() if u.pid == pid), None)


@_safe
def adopt(pid: int) -> Result:
    """把未纳入管理的进程预填成"自定义命令"草稿。"""
    check = _check_unmanaged(pid)
    if check:
        return check
    u = _find_unmanaged(pid)
    if u is None:
        return _fail("该进程已不在「未纳入管理」列表中")
    args = list(u.args)
    if args and "/" not in args[0]:
        args[0] = u.exe  # 从访达打开 App 时 PATH 不全，程序写绝对路径
    command = shlex.join(args) if args else u.exe
    reg = registry.load()
    draft = {
        "name": u.title,
        "group": reg.groups[0].id if reg.groups else "",
        "template": "custom",
        "template_fields": {"command": command, **({"folder": u.cwd} if u.cwd else {})},
        "command": command,
        "port": u.ports[0] if u.ports else None,
        "adopt_pid": pid,
    }
    hints = [
        "环境变量无法自动获取，如果这个程序需要，请在「高级」里补充。",
        "保存后会先停掉原来手动启动的进程，再由 ServiceDeck 启动，避免端口冲突。",
    ]
    return _ok(draft=draft, hints=hints)


@_safe
def stop_unmanaged(pid: int, confirm: bool = False, force: bool = False) -> Result:
    check = _check_unmanaged(pid)
    if check:
        return check
    u = _find_unmanaged(pid)
    if u is None:
        return _fail("该进程不在「未纳入管理」列表中，不能在这里停止")
    if not confirm:
        return _confirm("stop_unmanaged", f"停止 {u.title}（PID {pid}）？它不是由 ServiceDeck 启动的。")
    if _terminate(pid, force):
        return _ok(f"已停止 {u.title}")
    return _confirm("force_kill", f"{u.title}（PID {pid}）5 秒内没有退出，要强制结束吗？可能丢失它未保存的数据。")


# ---------- 试运行 ----------

class Trial:
    """用草稿配置在一个独立的临时 process-compose 实例里运行一段时间，不影响正在运行的服务，也不写入清单。"""

    def __init__(self, svc: Service, seconds: int = TRIAL_SECONDS):
        self.service = svc.model_copy(update={"id": "trial", "depends_on": [], "restart": "no", "autostart": True})
        self.seconds = seconds
        self.workdir = Path(tempfile.mkdtemp(prefix="sdt", dir="/tmp"))  # 短路径，socket 路径不能太长
        self.sock = self.workdir / "pc.sock"
        self.client = ComposeClient(self.sock)
        self.proc: subprocess.Popen | None = None
        self.started_at = 0.0
        self.finished = False
        self.final: dict | None = None
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._port_up_ever = False  # 端口是否起来过（结束时报告用）

    def start(self) -> None:
        compose = config.to_compose(Registry(services=[self.service]))
        compose["log_location"] = str(self.workdir / "process-compose.log")
        compose["processes"]["trial"]["log_location"] = str(self.workdir / "trial.log")
        compose_file = self.workdir / "trial.yaml"
        registry._atomic_write(compose_file, config.HEADER + yaml.safe_dump(compose, allow_unicode=True, sort_keys=False))
        self.proc = lifecycle.spawn(compose_file, self.sock, self.workdir, self.workdir)
        try:
            lifecycle.wait_alive(self.proc, self.client, self.workdir, timeout=20)
        except lifecycle.LifecycleError:
            self._cleanup()
            raise
        self.started_at = time.time()
        self._timer = threading.Timer(self.seconds, self.stop)
        self._timer.daemon = True
        self._timer.start()

    def _snapshot_live(self) -> dict:
        try:
            p = self.client.process("trial")
            logs = clean_log_lines(self.client.logs("trial", 200))
        except ComposeError:
            p, logs = None, []
        port_up = None
        if self.service.port:
            port_up = bool(p and p.get("is_running")) and self._port_open(self.service.port)
        running = bool(p and p.get("is_running"))
        return {
            "running": running,
            "finished": False,
            "status": p.get("status") if p else None,
            "exit_code": p.get("exit_code") if p and not running else None,
            "logs": logs,
            "port": self.service.port,
            "port_up": port_up,
            "remaining": max(0, int(self.seconds - (time.time() - self.started_at))),
            "workdir": str(self.workdir),
        }

    @staticmethod
    def _port_open(port: int) -> bool:
        import socket
        with socket.socket() as s:
            s.settimeout(0.3)
            return s.connect_ex(("127.0.0.1", port)) == 0

    def snapshot(self) -> dict:
        with self._lock:
            if self.finished:
                return dict(self.final)
            snap = self._snapshot_live()
            self._port_up_ever = self._port_up_ever or bool(snap["port_up"])
            return snap

    def stop(self) -> None:
        with self._lock:
            if self.finished:
                return
            snap = self._snapshot_live()
            port_up_ever = self._port_up_ever or bool(snap["port_up"])
            if self._timer:
                self._timer.cancel()
            try:
                self.client.stop_project()
            except ComposeError:
                pass
            self._cleanup()
            snap.update(running=False, finished=True, remaining=0,
                        port_up=port_up_ever if self.service.port else None)
            self.final = snap
            self.finished = True

    def _cleanup(self) -> None:
        if self.proc is not None:
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        shutil.rmtree(self.workdir, ignore_errors=True)


_trials: dict[str, Trial] = {}


@_safe
def start_trial(draft: dict, seconds: int = TRIAL_SECONDS) -> Result:
    # 试运行不要求先填名称和分组
    svc, errors, _ = _service_from_draft({**draft, "name": draft.get("name") or "试运行", "group": "trial"}, "trial")
    if svc is None or errors:
        return _fail("请先修正标出的问题", errors=errors)
    if svc.port:
        owners = port_owners(svc.port)
        if owners:
            o = owners[0]
            extra = "（是正在运行的受管服务，试运行前请先停止它）" if o["managed"] else ""
            return _fail(_occupied_message(svc.port, o) + extra, occupant=o)
    trial = Trial(svc, seconds)
    trial.start()
    tid = uuid.uuid4().hex[:8]
    _trials[tid] = trial
    return _ok("试运行已开始", trial_id=tid, seconds=seconds)


@_safe
def trial_status(trial_id: str) -> Result:
    t = _trials.get(trial_id)
    if t is None:
        return _fail("找不到这次试运行")
    return _ok(**t.snapshot())


@_safe
def stop_trial(trial_id: str) -> Result:
    t = _trials.get(trial_id)
    if t is None:
        return _fail("找不到这次试运行")
    t.stop()
    return _ok("试运行已结束")


def trial_run(draft: dict, seconds: int = TRIAL_SECONDS) -> Result:
    """阻塞式试运行（命令行和测试用）：运行 seconds 秒后返回最终结果。"""
    r = start_trial(draft, seconds)
    if not r.ok:
        return r
    tid = r.data["trial_id"]
    t = _trials[tid]
    deadline = time.time() + seconds + 30
    while not t.finished and time.time() < deadline:
        t.snapshot()  # 记录端口是否起来过
        time.sleep(0.3)
    t.stop()
    return trial_status(tid)

"""发现"未纳入管理"的服务（设计 7）：占用端口、但既不是 App 也不是系统程序、也不归 ServiceDeck 管的进程。

采集：lsof（只看得到当前用户的进程，正好符合需求）、ps 取父进程全表；
可执行文件路径用 libproc 的 proc_pidpath，完整命令用 sysctl KERN_PROCARGS2（都能正确处理中文路径）。
判定逻辑 find_unmanaged() 是纯函数，用本机真实样本做单元测试。
"""

import ctypes
import ctypes.util
import os
import re
import struct
import subprocess
from dataclasses import dataclass, field
from typing import Callable

SYSTEM_PREFIXES = ("/System/", "/usr/libexec/", "/usr/sbin/", "/sbin/")
# /usr/bin 下只保留解释器：用系统 Python 等跑的脚本仍然是你自己启动的服务
USR_BIN_INTERPRETERS = re.compile(r"^(python[0-9.]*|ruby|perl[0-9.]*|php|node|java)$")
# Python 框架自带的 Python.app 是解释器，不是 App（/usr/bin/python3、Homebrew Python 实际运行的都是它，2026-09-28 实测）
PYTHON_FRAMEWORK_APP = re.compile(r"/Python3?\.framework/Versions/[^/]+/Resources/Python\.app/Contents/MacOS/Python$")
INTERPRETER_NAMES = re.compile(r"^(python[0-9.]*|Python|ruby|perl[0-9.]*|php|node|java|bun|deno)$")
LOCAL_HOSTS = {"127.0.0.1", "[::1]", "localhost"}


@dataclass
class Listener:
    pid: int
    command: str
    addresses: list[tuple[str, int]] = field(default_factory=list)

    @property
    def ports(self) -> list[int]:
        return sorted({p for _, p in self.addresses})

    @property
    def exposure(self) -> str:
        """local = 仅本机；lan = 对局域网开放。"""
        return "local" if all(h in LOCAL_HOSTS or h.startswith("127.") for h, _ in self.addresses) else "lan"


@dataclass
class Unmanaged:
    pid: int
    title: str  # 显示名，如 "python -m http.server"
    exe: str
    command: str
    args: list[str]
    cwd: str | None
    ports: list[int]
    exposure: str
    uptime_seconds: int | None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


# ---------- 解析 ----------

def _unescape_lsof(text: str) -> str:
    """lsof -F 把非 ASCII 字节写成 \\xNN，还原成 UTF-8 文字。"""
    if "\\x" not in text:
        return text
    raw = re.sub(rb"\\x([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]), text.encode("latin-1", "replace"))
    return raw.decode("utf-8", "replace")


def parse_lsof(text: str) -> dict[int, Listener]:
    """解析 `lsof -nP -iTCP -sTCP:LISTEN -F pcn` 的输出。"""
    result: dict[int, Listener] = {}
    cur: Listener | None = None
    for line in text.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            pid = int(value)
            cur = result.setdefault(pid, Listener(pid=pid, command=""))
        elif tag == "c" and cur is not None:
            cur.command = _unescape_lsof(value)
        elif tag == "n" and cur is not None:
            host, _, port = value.rpartition(":")
            if port.isdigit() and (host, int(port)) not in cur.addresses:
                cur.addresses.append((host, int(port)))
    return {pid: l for pid, l in result.items() if l.addresses}


def parse_ps(text: str) -> dict[int, int]:
    """解析 `ps -axo pid=,ppid=`，返回 {pid: ppid}。"""
    parents = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            parents[int(parts[0])] = int(parts[1])
    return parents


def parse_etime(text: str) -> int | None:
    """ps 的 etime 格式：[[dd-]hh:]mm:ss → 秒。"""
    m = re.fullmatch(r"\s*(?:(\d+)-)?(?:(\d+):)?(?:(\d+):)?(\d+)\s*", text or "")
    if not m:
        return None
    d, a, b, s = m.groups()
    if b is None:  # 只有 mm:ss 或 ss
        h, mi = 0, int(a or 0)
    else:
        h, mi = int(a), int(b)
    return int(d or 0) * 86400 + h * 3600 + mi * 60 + int(s)


def parse_procargs(blob: bytes) -> list[str]:
    """解析 sysctl KERN_PROCARGS2：argc + 可执行文件路径 + 填充的 \\0 + argv[argc] + 环境变量。"""
    if len(blob) < 4:
        return []
    argc = struct.unpack("i", blob[:4])[0]
    rest = blob[4:]
    end = rest.find(b"\0")
    if end < 0:
        return []
    pos = end
    while pos < len(rest) and rest[pos] == 0:
        pos += 1
    args = []
    for _ in range(argc):
        nxt = rest.find(b"\0", pos)
        if nxt < 0:
            break
        args.append(rest[pos:nxt].decode("utf-8", "replace"))
        pos = nxt + 1
    return args


# ---------- 判定 ----------

def is_app_bundle(exe: str) -> bool:
    return ".app/" in exe and not PYTHON_FRAMEWORK_APP.search(exe)


def is_system(exe: str) -> bool:
    if exe.startswith(SYSTEM_PREFIXES):
        return True
    if exe.startswith("/usr/bin/"):
        return not USR_BIN_INTERPRETERS.match(os.path.basename(exe))
    return False


def descends_from(pid: int, ancestor: int, parents: dict[int, int]) -> bool:
    seen = set()
    cur = parents.get(pid)
    while cur and cur not in seen:
        if cur == ancestor:
            return True
        seen.add(cur)
        cur = parents.get(cur)
    return False


def find_unmanaged(
    listeners: dict[int, Listener],
    parents: dict[int, int],
    exe_of: Callable[[int], str | None],
    pc_pid: int | None,
    managed_ports: set[int],
) -> list[Listener]:
    """按设计 7 的三条规则筛选，结果按最小端口排序。"""
    own = os.getpid()
    found = []
    for pid, l in listeners.items():
        if pid in (own, pc_pid):
            continue
        exe = exe_of(pid)
        if not exe:
            continue  # 进程已退出或无权读取
        if os.path.basename(exe) == "process-compose":
            continue
        # 规则 1：受管（祖先是 ServiceDeck 的 process-compose，或端口属于正在运行的受管服务）
        if pc_pid and descends_from(pid, pc_pid, parents):
            continue
        if managed_ports & set(l.ports):
            continue
        # 规则 2、3：App 包内的程序、系统程序
        if is_app_bundle(exe) or is_system(exe):
            continue
        found.append(l)
    return sorted(found, key=lambda l: (l.ports[0], l.pid))


# ---------- 采集（真实系统） ----------

_libproc = None
_libc = None


def exe_path(pid: int) -> str | None:
    global _libproc
    if _libproc is None:
        _libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    buf = ctypes.create_string_buffer(4096)
    n = _libproc.proc_pidpath(pid, buf, 4096)
    return buf.value.decode("utf-8", "replace") if n > 0 else None


def process_args(pid: int) -> list[str]:
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    CTL_KERN, KERN_PROCARGS2 = 1, 49
    mib = (ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, pid)
    size = ctypes.c_size_t(0)
    if _libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or size.value == 0:
        return _ps_command(pid)
    buf = ctypes.create_string_buffer(size.value)
    if _libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return _ps_command(pid)
    return parse_procargs(buf.raw[: size.value]) or _ps_command(pid)


def _ps_command(pid: int) -> list[str]:
    out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return out.split() if out else []


def process_cwd(pid: int) -> str | None:
    out = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("n"):
            return _unescape_lsof(line[1:])
    return None


def process_uptime(pid: int) -> int | None:
    out = subprocess.run(["ps", "-o", "etime=", "-p", str(pid)], capture_output=True, text=True).stdout
    return parse_etime(out)


def display_title(exe: str, args: list[str]) -> str:
    name = os.path.basename(exe)
    if not INTERPRETER_NAMES.match(name):
        return name
    base = re.sub(r"[0-9.]+$", "", name).lower() or name.lower()
    rest = args[1:]
    if rest[:1] == ["-m"] and len(rest) >= 2:
        return f"{base} -m {rest[1]}"
    for a in rest:
        if not a.startswith("-"):
            return f"{base} {os.path.basename(a)}"
    return base


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace").stdout


def scan(reg=None, client=None) -> list[Unmanaged]:
    """扫描一次（约 0.5 秒）。只在窗口可见时每 10 秒调用一次。"""
    from servicedeck import registry, status
    from servicedeck.compose import lifecycle

    reg = reg if reg is not None else registry.load()
    summary = status.collect(reg, client)
    managed_ports = {s.port for s in summary.services if s.port and s.running}

    listeners = parse_lsof(_run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-F", "pcn"]))
    parents = parse_ps(_run(["ps", "-axo", "pid=,ppid="]))
    result = []
    for l in find_unmanaged(listeners, parents, exe_path, lifecycle.read_pid(), managed_ports):
        exe = exe_path(l.pid) or l.command
        args = process_args(l.pid)
        result.append(Unmanaged(
            pid=l.pid,
            title=display_title(exe, args),
            exe=exe,
            command=" ".join(_quote(a) for a in args) if args else exe,
            args=args,
            cwd=process_cwd(l.pid),
            ports=l.ports,
            exposure=l.exposure,
            uptime_seconds=process_uptime(l.pid),
        ))
    return result


def _quote(arg: str) -> str:
    import shlex
    return shlex.quote(arg)

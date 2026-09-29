"""发现规则测试：用本机真实的 lsof / ps 输出做样本（tests/fixtures/discovery/，2026-09-28 采集）。"""

import json
from pathlib import Path

import pytest

from servicedeck import discovery

FIX = Path(__file__).parent.parent / "fixtures" / "discovery"
LSOF = (FIX / "lsof.txt").read_text()
PS = (FIX / "ps.txt").read_text()
EXE = {int(k): v for k, v in json.loads((FIX / "exe.json").read_text()).items()}
APP_PC = 69390  # 样本里 postgres、ollama、Open WebUI 的父进程（把它当作 ServiceDeck 的 process-compose）


def find(lsof=LSOF, ps=PS, exe=None, pc_pid=APP_PC, managed_ports=frozenset()):
    exe_map = {**EXE, **(exe or {})}
    listeners = discovery.parse_lsof(lsof)
    parents = discovery.parse_ps(ps)
    return discovery.find_unmanaged(listeners, parents, exe_map.get, pc_pid, set(managed_ports))


def pids(result):
    return {c.pid for c in result}


# ---------- 解析 ----------

def test_parse_lsof_sample():
    listeners = discovery.parse_lsof(LSOF)
    assert listeners[69392].ports == [5432]
    assert listeners[69392].exposure == "local"  # 127.0.0.1 和 [::1]
    assert listeners[630].ports == [5000, 7000]
    assert listeners[630].exposure == "lan"  # *:7000
    assert listeners[1561].command == "Claude对话导出"  # lsof 的 \xNN 转义被还原
    assert listeners[56596].ports == [14013, 14016, 14019, 14022, 14023]  # 同一进程多个端口合并


def test_parse_lsof_addresses():
    text = "p1\ncx\nf3\nn0.0.0.0:80\np2\ncy\nf4\nn[::]:81\np3\ncz\nf5\nn192.168.1.5:82\np4\ncw\nf6\nn[::1]:83\n"
    ls = discovery.parse_lsof(text)
    assert [ls[i].exposure for i in (1, 2, 3, 4)] == ["lan", "lan", "lan", "local"]


def test_parse_ps():
    parents = discovery.parse_ps(PS)
    assert parents[69392] == 69390 and parents[28246] == 28220


# ---------- 真实样本 ----------

def test_real_sample_excludes_apps_system_and_managed():
    # 微信、QQ、ControlCenter、rapportd、Claude、VS Code 辅助进程、代理工具、WPS、网易云都是 App 或系统程序；
    # postgres、ollama、Open WebUI（python）是受管服务
    assert find() == []


def test_real_sample_without_manager_shows_terminal_services():
    # 服务管理器没运行时，postgres 和 Open WebUI 就是"未纳入管理"；ollama 在 Ollama.app 包内，按规则排除
    assert pids(find(pc_pid=None)) == {69392, 8102}


def test_managed_by_port_fallback():
    # 没有祖先关系，但端口正是正在运行的受管服务声明的端口
    assert pids(find(pc_pid=None, managed_ports={5432, 3000})) == set()


# ---------- 构造的情况 ----------

SHELL_CHAIN_PS = PS + "  7001   7000\n  7002   7001\n  7003   7002\n  7000  69390\n"
SHELL_CHAIN_LSOF = LSOF + "p7003\ncpython\nf3\nn127.0.0.1:18300\n"
SHELL_CHAIN_EXE = {
    7000: "/bin/bash",
    7001: "/opt/homebrew/bin/uv",
    7002: "/Users/me/app/.venv/bin/python",
    7003: "/Users/me/app/.venv/bin/python",
}


def test_multi_layer_chain_is_managed():
    # process-compose → bash -c → uv run → python → python
    assert find(lsof=SHELL_CHAIN_LSOF, ps=SHELL_CHAIN_PS, exe=SHELL_CHAIN_EXE) == []


def test_multi_layer_chain_unmanaged_when_root_differs():
    ps = SHELL_CHAIN_PS.replace("  7000  69390", "  7000    500") + "   500      1\n"
    assert pids(find(lsof=SHELL_CHAIN_LSOF, ps=ps, exe={**SHELL_CHAIN_EXE, 500: "/bin/zsh"})) == {7003}


def test_detached_process_managed_by_port():
    lsof = LSOF + "p8800\nccaddy\nf3\nn127.0.0.1:18301\n"
    ps = PS + "  8800      1\n"
    exe = {8800: "/opt/homebrew/bin/caddy"}
    assert pids(find(lsof=lsof, ps=ps, exe=exe)) == {8800}
    assert find(lsof=lsof, ps=ps, exe=exe, managed_ports={18301}) == []


def test_terminal_python_http_server_detected():
    # /usr/bin/python3 实际运行的是命令行工具里 Python.framework 中的 Python.app（本机实测）
    clt = "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python"
    brew = "/opt/homebrew/Cellar/python@3.13/3.13.1/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python"
    lsof = LSOF + "p9001\ncPython\nf3\nn127.0.0.1:18399\np9002\ncPython\nf3\nn*:18398\n"
    ps = PS + "  9000      1\n  9001   9000\n  9002   9000\n"
    exe = {9000: "/bin/zsh", 9001: clt, 9002: brew}
    result = {c.pid: c for c in find(lsof=lsof, ps=ps, exe=exe)}
    assert set(result) == {9001, 9002}
    assert result[9001].exposure == "local" and result[9002].exposure == "lan"


def test_system_dirs_excluded_but_usr_bin_interpreters_kept():
    lsof = LSOF + "".join(f"p{p}\ncx\nf3\nn127.0.0.1:{18310 + i}\n" for i, p in enumerate(range(9101, 9108)))
    ps = PS + "".join(f"  {p}      1\n" for p in range(9101, 9108))
    exe = {
        9101: "/usr/bin/nc",  # 系统程序
        9102: "/usr/sbin/httpd",
        9103: "/usr/libexec/sshd-session",
        9104: "/System/Library/Frameworks/x",
        9105: "/usr/bin/ruby",  # 解释器保留
        9106: "/usr/bin/perl5.34",
        9107: "/sbin/launchd",
    }
    assert pids(find(lsof=lsof, ps=ps, exe=exe)) == {9105, 9106}


def test_process_compose_itself_excluded():
    lsof = LSOF + "p9200\ncprocess-compose\nf3\nn127.0.0.1:8080\n"
    ps = PS + "  9200      1\n"
    exe = {9200: "/Users/me/.local/share/mise/installs/process-compose/1.122.0/process-compose"}
    assert find(lsof=lsof, ps=ps, exe=exe) == []


def test_unknown_exe_skipped():
    lsof = LSOF + "p9300\ncghost\nf3\nn127.0.0.1:18350\n"
    assert find(lsof=lsof, ps=PS + "  9300      1\n") == []


def test_results_sorted_by_port():
    lsof = LSOF + "p9401\nca\nf3\nn127.0.0.1:18362\np9402\ncb\nf3\nn127.0.0.1:18361\n"
    ps = PS + "  9401      1\n  9402      1\n"
    exe = {9401: "/opt/homebrew/bin/a", 9402: "/opt/homebrew/bin/b"}
    assert [c.pid for c in find(lsof=lsof, ps=ps, exe=exe)] == [9402, 9401]


# ---------- 小工具 ----------

def test_parse_etime():
    assert discovery.parse_etime("05") == 5
    assert discovery.parse_etime("01:05") == 65
    assert discovery.parse_etime("02:01:05") == 7265
    assert discovery.parse_etime("3-02:01:05") == 3 * 86400 + 7265
    assert discovery.parse_etime("garbage") is None


def test_parse_procargs():
    # KERN_PROCARGS2：argc（4 字节）+ 可执行文件路径 + 若干 \0 + argv + 环境变量
    import struct
    blob = struct.pack("i", 3) + b"/usr/bin/python3\0\0\0python3\0-m\0http.server\0PATH=/bin\0"
    assert discovery.parse_procargs(blob) == ["python3", "-m", "http.server"]


def test_display_title():
    assert discovery.display_title("/opt/homebrew/bin/caddy", ["caddy", "run"]) == "caddy"
    clt = "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python"
    assert discovery.display_title(clt, [clt, "-m", "http.server", "18399"]) == "python -m http.server"
    assert discovery.display_title("/x/.venv/bin/python3.12", ["python", "app.py", "--x"]) == "python app.py"
    assert discovery.display_title("/usr/local/bin/node", ["node", "/a/b/server.js"]) == "node server.js"

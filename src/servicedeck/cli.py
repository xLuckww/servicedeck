"""命令行入口：servicedeck status / up / down / validate（排查问题和自动化测试用，日常不需要）。"""

import argparse
import json
import sys
from pathlib import Path

from servicedeck import __version__

COLOR_DOT = {"green": "🟢", "yellow": "🟡", "red": "🔴", "grey": "⚪"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="servicedeck", description="ServiceDeck 本机服务控制台（命令行）")
    parser.add_argument("--version", action="version", version=f"servicedeck {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<命令>")
    st = sub.add_parser("status", help="查看所有服务的状态")
    st.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    sub.add_parser("up", help="全部启动（服务管理器未运行时先启动它）")
    sub.add_parser("down", help="停止全部服务和服务管理器")
    va = sub.add_parser("validate", help="检查服务清单有没有问题")
    va.add_argument("file", nargs="?", help="要检查的清单文件（默认检查正在使用的 services.yaml）")
    return parser


def _fmt_uptime(seconds: int | None) -> str:
    if not seconds:
        return ""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}小时{m}分" if h else (f"{m}分{s}秒" if m else f"{s}秒")


def cmd_status(args) -> int:
    from servicedeck import registry, status

    try:
        summary = status.collect()
    except registry.RegistryError as e:
        print(f"服务清单有误：{e}")
        return 1
    if args.json:
        print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        return 0
    head = COLOR_DOT[summary.overall]
    if not summary.manager_running:
        print(f"{head} 服务管理器未运行")
    else:
        print(f"{head} 运行中 {summary.running_count} 个，异常 {summary.error_count} 个")
    for g in summary.groups:
        print(f"\n【{g.name}】 {g.running}/{g.total}")
        for s in g.services:
            cols = [COLOR_DOT[s.color], s.name, f"({s.id})", s.label,
                    f"端口 {s.port}" if s.port else "", f"PID {s.pid}" if s.pid else "", _fmt_uptime(s.uptime_seconds)]
            print("  " + "  ".join(c for c in cols if c))
            if s.reason:
                print(f"      原因：{s.reason}")
    return 0


def cmd_up(args) -> int:
    from servicedeck.compose import lifecycle

    try:
        lifecycle.start_all()
    except Exception as e:  # 命令行直接把原因告诉用户
        print(f"启动失败：{e}")
        return 1
    print("已启动（用 servicedeck status 查看各服务状态）")
    return 0


def cmd_down(args) -> int:
    from servicedeck.compose import lifecycle

    lifecycle.shutdown()
    print("已停止全部服务和服务管理器")
    return 0


def cmd_validate(args) -> int:
    from servicedeck import registry

    try:
        reg = registry.load_file(Path(args.file)) if args.file else registry.load()
    except registry.RegistryError as e:
        print(f"✗ {e}")
        return 1
    except OSError as e:
        print(f"✗ 无法读取文件：{e}")
        return 1
    issues = registry.validate(reg)
    if not issues:
        print(f"✓ 没有发现问题（{len(reg.groups)} 个分组，{len(reg.services)} 个服务）")
        return 0
    for i in issues:
        where = f"服务 {i.service} 的 {i.field}" if i.service else i.field
        print(f"✗ {where}：{i.message}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {"status": cmd_status, "up": cmd_up, "down": cmd_down, "validate": cmd_validate}
    if args.command is None:
        parser.print_help()
        return 0
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

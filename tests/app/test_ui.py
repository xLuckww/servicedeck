"""控制台界面冒烟测试：演示数据 + 打开窗口，用 JS 检查页面内容、走一遍新增服务表单，并截图（浅色 / 深色）。

截图保存到环境变量 SERVICEDECK_SNAPSHOTS 指定的目录（不设则不保存）。
"""

import json
import os
import subprocess
import time

import pytest

from servicedeck import registry
from servicedeck.models import Group, Registry, Service

from ..integration.helpers import PY, port_open, wait_for
from .helpers import open_app, send

pytestmark = pytest.mark.app


def js(expr: str):
    r = send("js " + expr, timeout=20)
    assert "error" not in r, r
    return r.get("value")


def snap(name: str):
    d = os.environ.get("SERVICEDECK_SNAPSHOTS")
    if d:
        os.makedirs(d, exist_ok=True)
        send(f"snapshot {os.path.join(d, name)}.png")
        time.sleep(0.8)


def demo_registry(tmp_path):
    web = tmp_path / "site"
    web.mkdir()
    return Registry(
        groups=[Group(id="webapp", name="webapp"), Group(id="shared", name="公共服务")],
        services=[
            Service(id="webui", name="Open WebUI（演示）", group="webapp", template="custom",
                    command=f"exec {PY} -m http.server 18395 --bind 127.0.0.1", cwd=str(web), port=18395,
                    depends_on=["db"]),
            Service(id="db", name="数据库（演示）", group="shared", template="custom",
                    command=f"exec {PY} -m http.server 18396 --bind 127.0.0.1", port=18396),
            Service(id="broken", name="坏掉的服务", group="shared", template="custom", command="echo 找不到配置文件; exit 2"),
            Service(id="frp", name="公网入口 frp（演示）", group="shared", template="custom", command="sleep 600",
                    autostart=False, start_warning="开启后本机服务可从公网访问"),
        ],
    )


def test_console_ui(test_app, tmp_path):
    registry.save(demo_registry(tmp_path))
    squatter = subprocess.Popen([PY, "-m", "http.server", "18397", "--bind", "0.0.0.0"], cwd=tmp_path,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        open_app(test_app)
        wait_for(lambda: send("report")["icon"] in ("red", "green"), timeout=40, desc="服务启动")
        send("open_console")
        wait_for(lambda: js("document.querySelectorAll('#list .row').length") >= 4, timeout=20, desc="列表渲染")
        wait_for(lambda: js("document.querySelector('#summary-text').textContent").startswith("运行中 2"), timeout=20)

        # 列表：分组、计数、状态、手动开启标签
        heads = js("[...document.querySelectorAll('.group-head')].map(e => e.textContent.replace(/\\s+/g, ' ').trim())")
        assert any("webapp" in x and "1/1" in x for x in heads), heads
        assert any("公共服务" in x and "1/2" in x for x in heads), heads  # 停着的 frp 不计入
        assert "异常 1" in js("document.querySelector('#summary-text').textContent")
        assert js("document.querySelector('.row[data-id=frp] .tag').textContent") == "手动开启"

        # 未纳入管理：默认折叠，展开后能看到终端里跑的服务，并标出对局域网开放
        wait_for(lambda: js("!!document.querySelector('[data-group=__unmanaged__]')"), timeout=25, desc="发现未纳入管理")
        js("document.querySelector('[data-group=__unmanaged__]').click()")
        wait_for(lambda: js(f"!!document.querySelector('.row[data-id=\"{squatter.pid}\"]')"), desc="展开未纳入管理")
        assert "对局域网开放" in js(f"document.querySelector('.row[data-id=\"{squatter.pid}\"]').textContent")

        # 详情：选中运行中的服务，看到命令、依赖、日志
        js("document.querySelector('.row[data-id=webui]').click()")
        wait_for(lambda: "数据库（演示）" in js("document.querySelector('#detail').textContent"), desc="详情显示依赖")
        assert js("!!document.querySelector('#detail [data-act=stop]')")
        snap("01-详情-浅色")

        # 异常服务：显示原因和日志
        js("document.querySelector('.row[data-id=broken]').click()")
        wait_for(lambda: "退出码 2" in js("document.querySelector('#detail').textContent"), desc="异常原因")
        wait_for(lambda: "找不到配置文件" in js("document.querySelector('#logbox').textContent"), desc="异常日志")
        snap("02-异常-浅色")

        # 启动 frp：弹出启动前提醒
        js("document.querySelector('.row[data-id=frp]').click()")
        wait_for(lambda: js("!!document.querySelector('#detail [data-act=start]')"))
        js("document.querySelector('#detail [data-act=start]').click()")
        wait_for(lambda: "公网" in (js("document.querySelector('.confirm-text') && document.querySelector('.confirm-text').textContent") or ""),
                 desc="启动前提醒")
        snap("03-确认框-浅色")
        js("document.querySelector('.dialog [data-act=no]').click()")

        # 新增服务：选模板 → 填写 → 命令预览 → 字段错误就近显示
        js("document.querySelector('#btn-add').click()")
        wait_for(lambda: js("document.querySelectorAll('.template-card').length") == 5, desc="模板选择")
        snap("04-选模板-浅色")
        js("document.querySelector('.template-card[data-t=custom]').click()")
        wait_for(lambda: js("!!document.querySelector('[data-tf=command]')"), desc="自定义命令表单")
        js(f"""(() => {{ const i = document.querySelector('[data-tf=command]'); i.value = '{PY} -m http.server 18398';
               i.dispatchEvent(new Event('input')); document.querySelector('[data-k=name]').value = '数据库（演示）'; }})()""")
        wait_for(lambda: "18398" in js("document.querySelector('[data-k=command]').value"), desc="命令预览")
        assert js("document.querySelector('[data-k=port]').value") == ""  # 这个命令里没有 --port
        js("document.querySelector('[data-k=port]').value = '18396'")
        js("document.querySelector('[data-act=save]').click()")
        wait_for(lambda: js("!document.querySelector('[data-field=name] .err').hidden"), desc="名称重复的错误")
        assert "已被其他服务使用" in js("document.querySelector('[data-field=name] .err').textContent")
        assert "18396" in js("document.querySelector('[data-field=port] .err').textContent")
        snap("05-表单错误-浅色")

        # 改正后试运行，再保存
        js("document.querySelector('[data-k=name]').value = '新服务'; document.querySelector('[data-k=port]').value = '18398'")
        js("document.querySelector('[data-act=trial]').click()")
        wait_for(lambda: "端口 18398 已起来" in (js("document.querySelector('[data-k=trial-status]').textContent") or ""),
                 timeout=20, desc="试运行端口起来")
        snap("06-试运行-浅色")
        js("document.querySelector('[data-act=trial-stop]').click()")
        wait_for(lambda: "已结束" in js("document.querySelector('[data-k=trial-status]').textContent"), timeout=20)
        wait_for(lambda: not port_open(18398), desc="试运行结束端口释放")
        js("document.querySelector('[data-act=save]').click()")
        wait_for(lambda: registry.load().service("新服务".encode().hex()) is not None or any(
            s.name == "新服务" for s in registry.load().services), desc="保存成功")
        wait_for(lambda: js("document.querySelectorAll('.overlay').length") == 0, desc="表单关闭")

        # 设置
        js("document.querySelector('#btn-settings').click()")
        wait_for(lambda: js("document.querySelectorAll('.group-item').length") == 2, desc="设置里的分组")
        snap("07-设置-浅色")
        js("document.querySelector('.dialog [data-act=close]').click()")

        # 深色模式
        send("appearance dark")
        time.sleep(1)
        js("document.querySelector('.row[data-id=webui]').click()")
        time.sleep(1.5)
        snap("11-详情-深色")
        js("document.querySelector('#btn-add').click()")
        time.sleep(0.5)
        js("document.querySelector('.template-card[data-t=python]').click()")
        time.sleep(1)
        snap("12-表单-深色")
        js("document.querySelector('.dialog [data-act=cancel]').click()")
        send("appearance light")

        # 窗口隐藏时停止刷新（document.hidden 为真）
        send("close_window")
        time.sleep(1.5)
        assert js("document.hidden") is True
    finally:
        squatter.kill()
        squatter.wait()

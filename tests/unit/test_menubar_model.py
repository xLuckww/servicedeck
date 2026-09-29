from servicedeck import status
from servicedeck.app import menubar_model as mm
from servicedeck.models import Group, Registry, Service


def svc(id, group="g", **kw):
    return Service(**dict(dict(id=id, name=id.upper(), group=group, template="custom", command="x"), **kw))


def p(name, st="Running", **kw):
    return dict(dict(name=name, status=st, is_ready="-", has_ready_probe=False, exit_code=0, restarts=0, pid=10,
                     age=1, is_running=st == "Running"), **kw)


REG = Registry(groups=[Group(id="g", name="webapp"), Group(id="s", name="公共服务")],
               services=[svc("web", port=3000), svc("db", group="s"), svc("frp", group="s", autostart=False)])
SETTINGS = {"login_launch": True, "boot_autostart": False}


def titles(items):
    return [i.title for i in items]


def test_icon():
    sm = status.summarize(REG, [p("web"), p("db"), p("frp", "Disabled", is_running=False)])
    assert mm.icon(sm) == ("green", "SD")
    assert mm.icon(status.summarize(REG, None))[0] == "grey"
    assert mm.icon(None)[0] == "red"  # 清单有误


def test_menu_when_running():
    sm = status.summarize(REG, [p("web"), p("db"), p("frp", "Disabled", is_running=False)])
    items = mm.build(sm, SETTINGS)
    t = titles(items)
    assert t[0].startswith("运行中 2")
    assert t[1:4] == ["打开控制台", "全部启动", "全部停止"]
    assert "webapp  1/1" in t and "公共服务  1/1" in t  # 停着的 frp 不计入
    assert t[-1] == "退出 ServiceDeck"
    web = next(i for i in items if i.service_id == "web")
    assert web.title.startswith("🟢") and "3000" in web.title
    sub = titles(web.children)
    assert sub == ["停止", "重启", "打开网页", "查看日志"]
    frp = next(i for i in items if i.service_id == "frp")
    assert "手动开启" in frp.title and titles(frp.children)[0] == "启动"
    assert "打开网页" not in titles(next(i for i in items if i.service_id == "db").children)
    settings_item = next(i for i in items if i.title == "设置")
    assert [(c.title, c.checked) for c in settings_item.children] == [("登录时启动", True), ("开机自动启动服务", False)]


def test_menu_when_manager_down():
    items = mm.build(status.summarize(REG, None), SETTINGS)
    t = titles(items)
    assert "启动全部服务" in t
    assert "全部停止" not in t and not any(i.service_id for i in items)
    assert t[0] == "服务管理器未运行"


def test_menu_when_registry_broken():
    t = titles(mm.build(None, SETTINGS, error="第 3 行格式有误"))
    assert t[0].startswith("服务清单有误")
    assert "打开控制台" in t and "退出 ServiceDeck" in t


def test_error_service_shows_red():
    sm = status.summarize(REG, [p("web", "Completed", exit_code=2, restarts=3, is_running=False), p("db")])
    web = next(i for i in mm.build(sm, SETTINGS) if i.service_id == "web")
    assert web.title.startswith("🔴")
    assert titles(web.children)[0] == "启动"

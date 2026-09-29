"""集成测试的辅助函数：假服务（python3 -m http.server，端口 183xx）、等待条件。"""

import socket
import time

from servicedeck.models import Group, Registry, Service

PY = "/usr/bin/python3"


def http_service(id, port, **kw):
    base = dict(
        id=id, name=id, group="test", template="custom",
        command=f"exec {PY} -m http.server {port} --bind 127.0.0.1", port=port,
    )
    base.update(kw)
    return Service(**base)


def make_registry(*services):
    return Registry(groups=[Group(id="test", name="测试")], services=list(services))


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for(cond, timeout=15.0, interval=0.2, desc="条件"):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = cond()
            if last:
                return last
        except Exception as e:  # 服务还没起来时可能报错
            last = e
        time.sleep(interval)
    raise AssertionError(f"等待超时：{desc}（最后结果：{last!r}）")


def state(client, sid):
    return client.process(sid)




def plain_service(id, command, **kw):
    """没有端口的假服务。"""
    return Service(**dict(dict(id=id, name=id, group="test", template="custom", command=command), **kw))

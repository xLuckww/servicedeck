"""经 Unix socket 调用 process-compose 的 HTTP 接口（接口文档：服务运行时访问 /swagger/doc.json）。"""

from pathlib import Path

import httpx

from servicedeck import paths


class ComposeError(Exception):
    pass


class ComposeUnavailable(ComposeError):
    pass


class ComposeClient:
    def __init__(self, socket: Path | None = None):
        self.socket = Path(socket) if socket else paths.socket_path()

    def _request(self, method: str, url: str, timeout: float = 10, **kw):
        if not self.socket.exists():
            raise ComposeUnavailable("服务管理器未运行")
        try:
            with httpx.Client(transport=httpx.HTTPTransport(uds=str(self.socket)), base_url="http://localhost",
                              timeout=timeout) as c:
                r = c.request(method, url, **kw)
        except (httpx.ConnectError, httpx.RemoteProtocolError, FileNotFoundError, ConnectionRefusedError) as e:
            raise ComposeUnavailable(f"服务管理器未运行（{e}）") from e
        except httpx.TimeoutException as e:
            raise ComposeError(f"服务管理器没有及时响应（{method} {url}）") from e
        try:
            data = r.json()
        except ValueError:
            data = None
        if r.status_code >= 400:
            msg = data.get("error") if isinstance(data, dict) else r.text
            raise ComposeError(msg or f"HTTP {r.status_code}")
        return data

    # ---------- 查询 ----------

    def is_alive(self) -> bool:
        try:
            return (self._request("GET", "/live", timeout=2) or {}).get("status") == "alive"
        except ComposeError:
            return False

    def processes(self) -> list[dict]:
        return (self._request("GET", "/processes") or {}).get("data") or []

    def process(self, name: str) -> dict:
        return self._request("GET", f"/process/{name}")

    def project_state(self) -> dict:
        return self._request("GET", "/project/state")

    def logs(self, name: str, limit: int = 50) -> list[str]:
        """最近 limit 行日志（最旧的在前）。"""
        return (self._request("GET", f"/process/logs/{name}/0/{limit}") or {}).get("logs") or []

    # ---------- 操作 ----------

    def start(self, name: str) -> None:
        self._request("POST", f"/process/start/{name}")

    def stop(self, name: str) -> None:
        self._request("PATCH", f"/process/stop/{name}", timeout=60)

    def stop_many(self, names: list[str]) -> None:
        if names:
            self._request("PATCH", "/processes/stop", json=names, timeout=90)

    def restart(self, name: str) -> None:
        self._request("POST", f"/process/restart/{name}", timeout=60)

    def reload(self) -> dict[str, str]:
        """让 process-compose 从配置文件重新加载，只重启有变化的服务。返回 {服务: updated/added/removed}。"""
        return self._request("POST", "/project/configuration", timeout=120) or {}

    def stop_project(self) -> None:
        """停止全部服务并退出 process-compose。"""
        self._request("POST", "/project/stop", timeout=120)

"""只对外提供一个 HTML 文件的网页服务（用于 demo.example.com 这类公开演示页）。

- 访问 / 或 /index.html 时返回指定文件，其他任何路径一律 404，文件夹里的其他文件不会被看到；
- 每次访问都现读文件：替换文件后刷新网页即可看到新内容，不用重启服务；
- 只监听 127.0.0.1，公网访问经由 frp 转发。

用法：python scripts/serve_page.py --file /path/to/index.html --port 8090
只用 Python 标准库。
"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ALLOWED_PATHS = {"/", "/index.html"}


def make_handler(page: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "page"
        sys_version = ""

        def _respond(self, send_body: bool) -> None:
            if urlsplit(self.path).path not in ALLOWED_PATHS:
                self._plain(404, "Not Found", send_body)
                return
            try:
                body = page.read_bytes()
            except OSError:
                self._plain(503, "页面文件暂时不可用", send_body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if send_body:
                self.wfile.write(body)

        def _plain(self, code: int, text: str, send_body: bool) -> None:
            body = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            self._respond(True)

        def do_HEAD(self):  # noqa: N802
            self._respond(False)

    return Handler


def make_server(page: Path, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(page))


def main() -> None:
    parser = argparse.ArgumentParser(description="只对外提供一个 HTML 文件")
    parser.add_argument("--file", required=True, help="要展示的 HTML 文件")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    page = Path(args.file).expanduser().resolve()
    server = make_server(page, args.port)
    print(f"正在提供 {page}，地址 http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

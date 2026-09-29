"""scripts/serve_page.py：只提供指定的一个文件，替换后立即生效，其他路径 404。"""

import importlib.util
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "serve_page.py"
PORT = 18391


def _load():
    spec = importlib.util.spec_from_file_location("serve_page", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def served(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<p>第一版</p>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("不应被看到", encoding="utf-8")
    server = _load().make_server(page, PORT)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield page
    server.shutdown()
    server.server_close()


def _get(path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5) as r:
            return r.status, r.headers.get("Content-Type"), r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read().decode("utf-8")


def test_serves_page_at_root_and_index(served):
    for path in ("/", "/index.html", "/?from=wechat"):
        code, ctype, body = _get(path)
        assert code == 200
        assert ctype == "text/html; charset=utf-8"
        assert body == "<p>第一版</p>"


def test_other_paths_are_404(served):
    for path in ("/secret.txt", "/../secret.txt", "/index.html/x", "/favicon.ico"):
        assert _get(path)[0] == 404


def test_replacing_file_takes_effect_immediately(served):
    served.write_text("<p>第二版</p>", encoding="utf-8")
    assert _get("/")[2] == "<p>第二版</p>"


def test_missing_file_is_503(served):
    served.unlink()
    assert _get("/")[0] == 503

import pytest

from servicedeck.compose.client import ComposeClient, ComposeUnavailable


def test_unavailable_when_no_socket(sd_home):
    c = ComposeClient(sd_home / "run" / "pc.sock")
    assert c.is_alive() is False
    with pytest.raises(ComposeUnavailable) as e:
        c.processes()
    assert "未运行" in str(e.value)

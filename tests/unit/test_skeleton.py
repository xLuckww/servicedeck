import subprocess
import sys

import servicedeck
from servicedeck import cli


def test_import():
    assert servicedeck.__version__


def test_cli_help(capsys):
    assert cli.main([]) == 0
    assert "servicedeck" in capsys.readouterr().out


def test_cli_entry_point():
    out = subprocess.run([sys.executable, "-m", "servicedeck.cli", "--help"], capture_output=True, text=True)
    assert out.returncode == 0

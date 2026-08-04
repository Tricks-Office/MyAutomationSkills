import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from main import run


def test_run_default():
    assert run() == "hello, world"


def test_run_with_name():
    assert run("hermes") == "hello, hermes"

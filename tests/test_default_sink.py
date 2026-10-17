import subprocess
import sys


def test_importing_pyclaw_detaches_chatchat_default_printer():
    code = "import pyclaw\nfrom chatchat.hooks import events\nprint(len(events._runtime_sinks))\n"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "0"

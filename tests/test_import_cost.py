"""What importing pyclaw costs, and when it takes the terminal over.

The package used to do both at import: it loaded the gateway, the session, the
TUI and textual, and it cleared chatchat's default event printer. A library
import that silently changes global behaviour is how a tool ends up unable to
load without the application around it.
"""

import subprocess
import sys


def _run(code: str) -> str:
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_importing_the_tools_does_not_load_the_application():
    assert _run("import sys, pyclaw.tools; print('textual' in sys.modules, 'pyclaw.gateway.server' in sys.modules)") == (
        "False False"
    )


def test_importing_the_package_leaves_chatchat_printing():
    code = "import pyclaw\nfrom chatchat.hooks import events\nprint(len(events._runtime_sinks) > 0)\n"
    assert _run(code) == "True"


def test_building_a_team_takes_over_the_terminal():
    code = (
        "import asyncio, tempfile\n"
        "from chatchat.hooks import events\n"
        "from pyclaw.team.builder import build_team\n"
        "async def main():\n"
        "    build_team('agnes/agnes-2.5-flash', cwd=tempfile.mkdtemp())\n"
        "asyncio.run(main())\n"
        "print(len(events._runtime_sinks))\n"
    )
    assert _run(code) == "0"


def test_the_package_still_offers_its_public_names():
    code = "from pyclaw import GatewayServer, Session, build_team, load\nprint(sorted((GatewayServer.__name__, Session.__name__)))\n"
    assert _run(code) == "['GatewayServer', 'Session']"

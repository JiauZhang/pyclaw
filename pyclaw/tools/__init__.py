from . import display
from .background import TaskOutput, TaskStop
from .bash import Bash
from .edit import Edit, Write
from .glob import Glob
from .grep import Grep
from .info import datetime_tool, geocode_tool, location_tool, weather_tool
from .read import Read

BUILTIN_TOOLS = (Read, Glob, Grep, Write, Edit, Bash, TaskOutput, TaskStop)

tools = [
    datetime_tool,
    geocode_tool,
    location_tool,
    weather_tool,
]

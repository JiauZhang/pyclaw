from .info import datetime_tool, geocode_tool, location_tool, weather_tool
from chatool.tools.shell import execute_shell_command
from chatool.tools.file import read_file, write_file

tools = [
    execute_shell_command,
    read_file,
    write_file,
    datetime_tool,
    geocode_tool,
    location_tool,
    weather_tool,
]

from pathlib import Path

from chatchat.tool import tool


@tool(
    name='read_file',
    description='Read the contents of a file.',
    parameters={
        'type': 'object',
        'properties': {
            'path': {
                'type': 'string',
                'description': 'Path to the file to read',
            },
            'offset': {
                'type': 'integer',
                'description': 'Line offset to start reading from (0-indexed)',
                'default': 0,
            },
            'limit': {
                'type': 'integer',
                'description': 'Maximum number of lines to read',
                'default': 1000,
            },
        },
        'required': ['path'],
    }
)
def read_file_tool(path: str, offset: int = 0, limit: int = 1000):
    try:
        file_path = Path(path)
        if not file_path.exists():
            return f'Error: file not found: {path}'
        if not file_path.is_file():
            return f'Error: not a file: {path}'

        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()

        selected_lines = lines[offset:offset + limit]
        output = ''.join(selected_lines)
        info = f'Lines {offset}-{offset + len(selected_lines)} of {len(lines)}'
        return f'# {info}\n{output}'
    except Exception as e:
        return f'Error: {str(e)}'


@tool(
    name='write_file',
    description='Write content to a file. Creates the file if it does not exist.',
    parameters={
        'type': 'object',
        'properties': {
            'path': {
                'type': 'string',
                'description': 'Path to the file to write',
            },
            'content': {
                'type': 'string',
                'description': 'Content to write to the file',
            },
        },
        'required': ['path', 'content'],
    }
)
def write_file_tool(path: str, content: str):
    try:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f'Successfully wrote {file_path}'
    except Exception as e:
        return f'Error: {str(e)}'

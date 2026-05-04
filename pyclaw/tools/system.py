import os
import subprocess
import tempfile

from chatchat.tool import tool


@tool(
    name='bash',
    description='Execute bash commands. Supports pipes and shell features.',
    parameters={
        'type': 'object',
        'properties': {
            'command': {
                'type': 'string',
                'description': 'The bash command to execute',
            },
            'timeout': {
                'type': 'integer',
                'description': 'Timeout in seconds',
                'default': 60,
            },
        },
        'required': ['command'],
    }
)
def bash_tool(command: str, timeout: int = 60):
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout,
            executable='/bin/bash'
        )
        output = result.stdout
        if result.stderr:
            output += '\n[STDERR]\n' + result.stderr
        if not output.strip():
            output = '(no output)'
        return output.strip()
    except subprocess.TimeoutExpired:
        return f'Error: command timed out after {timeout} seconds'
    except Exception as e:
        return f'Error: {str(e)}'


@tool(
    name='exec',
    description='Execute a system command. Use with caution.',
    parameters={
        'type': 'object',
        'properties': {
            'command': {
                'type': 'string',
                'description': 'The command to execute',
            },
            'timeout': {
                'type': 'integer',
                'description': 'Timeout in seconds (default: 60)',
                'default': 60,
            },
        },
        'required': ['command'],
    }
)
def exec_tool(command: str, timeout: int = 60):
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        output = result.stdout
        if result.stderr:
            output += '\n[STDERR]\n' + result.stderr
        if not output.strip():
            output = '(no output)'
        return output.strip()
    except subprocess.TimeoutExpired:
        return f'Error: command timed out after {timeout} seconds'
    except Exception as e:
        return f'Error: {str(e)}'


@tool(
    name='python',
    description='Execute Python code and return the result.',
    parameters={
        'type': 'object',
        'properties': {
            'code': {
                'type': 'string',
                'description': 'Python code to execute',
            },
            'timeout': {
                'type': 'integer',
                'description': 'Timeout in seconds',
                'default': 30,
            },
        },
        'required': ['code'],
    }
)
def python_tool(code: str, timeout: int = 30):
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_file = f.name

        try:
            result = subprocess.run(
                ['python3', temp_file],
                capture_output=True, text=True, timeout=timeout
            )
            output = result.stdout
            if result.stderr:
                output += '\n[STDERR]\n' + result.stderr
            if not output.strip():
                output = '(no output)'
            return output.strip()
        finally:
            try:
                os.unlink(temp_file)
            except:
                pass
    except subprocess.TimeoutExpired:
        return f'Error: code execution timed out after {timeout} seconds'
    except Exception as e:
        return f'Error: {str(e)}'

from __future__ import annotations

import os
import re
import shlex

SAFE_ENV_VARS = frozenset({
    'NODE_ENV', 'GOOS', 'GOARCH', 'LANG', 'LANGUAGE', 'LC_ALL', 'LC_CTYPE',
    'TZ', 'CI', 'PYTHONUNBUFFERED', 'PYTHONDONTWRITEBYTECODE',
})
SHELL_WRAPPERS = frozenset({'timeout', 'time', 'nice', 'nohup', 'stdbuf'})
ENV_ASSIGN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')

READ_ONLY_COMMANDS = frozenset({
    'ls', 'cat', 'head', 'tail', 'wc', 'stat', 'strings', 'hexdump', 'od',
    'nl', 'id', 'uname', 'free', 'df', 'du', 'locale', 'groups', 'nproc',
    'basename', 'dirname', 'realpath', 'readlink', 'cut', 'paste', 'tr',
    'column', 'tac', 'rev', 'fold', 'expand', 'unexpand', 'fmt', 'comm',
    'cmp', 'numfmt', 'diff', 'true', 'false', 'which', 'type', 'expr',
    'test', '[', 'getconf', 'seq', 'tsort', 'pr', 'pwd', 'whoami', 'hostname',
    'date', 'cal', 'uptime', 'echo', 'grep', 'rg', 'fd', 'file', 'id',
})
GIT_READ_ONLY_COMMANDS = frozenset({
    'diff', 'log', 'show', 'shortlog', 'reflog', 'ls-remote', 'status',
    'blame', 'ls-files', 'remote', 'merge-base', 'rev-parse', 'rev-list',
    'describe', 'cat-file', 'for-each-ref', 'grep', 'tag', 'branch',
})
DOCKER_READ_ONLY_COMMANDS = frozenset({'ps', 'images'})
EDIT_COMMANDS = frozenset({'mkdir', 'touch', 'rm', 'rmdir', 'mv', 'cp', 'sed'})
FIND_WRITING_FLAGS = frozenset({
    '-delete', '-exec', '-execdir', '-ok', '-okdir', '-fprint', '-fls',
    '-fprintf',
})
DANGEROUS_REMOVAL_PATHS = frozenset({'/', '/*', '~', '~/*', '*'})
DANGEROUS_CHARS = frozenset({'$', '`', '*', '?', '>', '<'})
OPERATORS = ('&&', '||')


def _tokenize(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _normalize(command: str) -> str:
    return ' '.join(str(command).strip().split())


def split_commands(command: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    quote = ''
    i = 0
    text = str(command)
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ''
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == '\\' and i + 1 < len(text):
            buf.append(text[i:i + 2])
            i += 2
            continue
        if any(text.startswith(op, i) for op in OPERATORS):
            parts.append(''.join(buf))
            buf = []
            i += 2
            continue
        if ch in (';', '|', '&', '\n'):
            parts.append(''.join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append(''.join(buf))
    return [p.strip() for p in parts if p.strip()]


def is_compound(command: str) -> bool:
    return len(split_commands(command)) > 1


def has_unescaped_star(text: str) -> bool:
    i = 0
    while i < len(text):
        if text[i] == '\\':
            i += 2
            continue
        if text[i] == '*':
            return True
        i += 1
    return False


def parse_bash_rule(rule) -> tuple[str, str]:
    text = str(rule).strip()
    if text.startswith('Bash(') and text.endswith(')'):
        text = text[5:-1]
    if text.endswith(':*') and len(text) > 2:
        return 'prefix', text[:-2]
    if has_unescaped_star(text):
        return 'wildcard', text
    return 'exact', text


def _wildcard_regex(pattern: str) -> re.Pattern:
    optional_tail = pattern.endswith(' *')
    core = pattern[:-2] if optional_tail else pattern
    out = []
    i = 0
    while i < len(core):
        ch = core[i]
        if ch == '\\' and i + 1 < len(core):
            out.append(re.escape(core[i + 1]))
            i += 2
            continue
        out.append('.*' if ch == '*' else re.escape(ch))
        i += 1
    body = ''.join(out)
    if optional_tail:
        body += r'( .*)?'
    return re.compile(rf'^{body}$', re.S)


def strip_env(command: str, safe_only: bool) -> str:
    tokens = _tokenize(command)
    i = 0
    while i < len(tokens) and ENV_ASSIGN.match(tokens[i]):
        if safe_only and tokens[i].split('=', 1)[0] not in SAFE_ENV_VARS:
            break
        i += 1
    return ' '.join(tokens[i:])


def strip_wrappers(command: str) -> str:
    tokens = _tokenize(command)
    i = 0
    while i < len(tokens) and tokens[i] in SHELL_WRAPPERS:
        i += 1
        while i < len(tokens) and (tokens[i].startswith('-')
                                  or tokens[i].replace('.', '', 1).isdigit()):
            i += 1
    return ' '.join(tokens[i:])


def base_command(command: str) -> str:
    tokens = _tokenize(strip_wrappers(strip_env(command, safe_only=False)))
    return tokens[0] if tokens else ''


_REDIR_OP = re.compile(r'\d*>>?|&>')
_REDIR_MERGED = re.compile(r'(?:\d*&?)>>?\S+')


def _strip_redirects(command: str) -> str:
    tokens = _tokenize(str(command))
    end = len(tokens)
    while end >= 1:
        last = tokens[end - 1]
        if _REDIR_MERGED.fullmatch(last):
            end -= 1
        elif end >= 2 and _REDIR_OP.fullmatch(tokens[end - 2]):
            end -= 2
        else:
            break
    return ' '.join(tokens[:end])


def strip_prefixes(command: str, env_all: bool = False) -> list[str]:
    text = str(command).strip()
    bare = strip_wrappers(text)
    env_safe = strip_env(bare, safe_only=True)
    candidates = [text, _strip_redirects(text), bare, env_safe,
                  strip_wrappers(env_safe)]
    if env_all:
        all_env = strip_env(bare, safe_only=False)
        candidates += [all_env, strip_wrappers(all_env)]
    return candidates


def bash_rule_matches(rule, command, env_all: bool = False) -> bool:
    kind, pattern = parse_bash_rule(rule)
    target = _normalize(pattern)
    for candidate in strip_prefixes(command, env_all):
        candidate = _normalize(candidate)
        if kind == 'exact':
            if candidate == target:
                return True
            continue
        if is_compound(candidate):
            continue
        if kind == 'prefix':
            for form in (target, 'xargs ' + target):
                if candidate == form or candidate.startswith(form + ' '):
                    return True
            continue
        if _wildcard_regex(pattern).match(candidate):
            return True
    return False


def _has_unquoted_special(command: str) -> bool:
    quote = ''
    i = 0
    text = str(command)
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ''
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            i += 1
            continue
        if ch == '\\':
            i += 2
            continue
        if ch in DANGEROUS_CHARS:
            return True
        i += 1
    return False


def _part_read_only(command: str) -> bool:
    tokens = _tokenize(strip_wrappers(strip_env(command, safe_only=False)))
    if not tokens:
        return False
    name, args = tokens[0], tokens[1:]
    if name == 'git':
        return bool(args) and args[0] in GIT_READ_ONLY_COMMANDS
    if name == 'docker':
        return bool(args) and args[0] in DOCKER_READ_ONLY_COMMANDS
    if name == 'find':
        return not any(a in FIND_WRITING_FLAGS for a in args)
    return name in READ_ONLY_COMMANDS


def is_read_only(command) -> bool:
    text = str(command).replace('2>&1', ' ').strip()
    if not text or _has_unquoted_special(text):
        return False
    parts = split_commands(text)
    if not parts:
        return False
    if any(base_command(p) == 'git' for p in parts) \
            and any(base_command(p) == 'cd' for p in parts):
        return False
    return all(_part_read_only(p) for p in parts)


def _dangerous_path(arg: str) -> bool:
    text = str(arg).strip()
    if text in DANGEROUS_REMOVAL_PATHS:
        return True
    expanded = os.path.expanduser(text)
    if expanded in ('/', os.path.expanduser('~')):
        return True
    if not expanded.startswith('/'):
        return False
    normalized = os.path.normpath(expanded)
    return normalized == '/' or normalized.count('/') == 1


def is_dangerous_removal(command) -> bool:
    tokens = _tokenize(strip_wrappers(strip_env(str(command), safe_only=False)))
    if not tokens or tokens[0] not in ('rm', 'rmdir'):
        return False
    return any(not a.startswith('-') and _dangerous_path(a) for a in tokens[1:])


def is_workspace_edit_command(command) -> bool:
    text = str(command).strip()
    if _has_unquoted_special(text):
        return False
    parts = split_commands(text)
    if len(parts) != 1:
        return False
    tokens = _tokenize(strip_wrappers(strip_env(parts[0], safe_only=False)))
    if not tokens or tokens[0] not in EDIT_COMMANDS:
        return False
    for arg in tokens[1:]:
        if arg.startswith('-'):
            continue
        if arg.startswith('~') or os.path.isabs(arg):
            return False
        if '..' in arg.split('/'):
            return False
    return True


BARE_SHELL_PREFIXES = frozenset({
    'sh', 'bash', 'zsh', 'env', 'xargs', 'nice', 'sudo', 'doas', 'pkexec',
    'nohup', 'stdbuf',
})
_SUBCOMMAND = re.compile(r'^[a-z][a-z0-9]*(-[a-z0-9]+)*$')


def suggested_rule(command) -> str | None:
    """claude 的 don't-ask-again 建议规则：单命令优先两词前缀
    （`git commit -m x` → `Bash(git commit:*)`），否则精确命令。
    危险删除 / 无法安全解析 / 不安全 env 前缀 → 不建议保存（None）。"""
    text = _normalize(command)
    if not text or is_dangerous_removal(text):
        return None
    if _has_unquoted_special(text):
        return None
    tokens = _tokenize(text)
    if tokens and ENV_ASSIGN.match(tokens[0]) \
            and tokens[0].split('=', 1)[0] not in SAFE_ENV_VARS:
        return None
    stripped = _normalize(strip_wrappers(
        _strip_redirects(strip_env(text, safe_only=True))))
    parts = split_commands(stripped)
    if len(parts) == 1:
        toks = _tokenize(parts[0])
        if len(toks) >= 2 and toks[0] not in BARE_SHELL_PREFIXES \
                and _SUBCOMMAND.fullmatch(toks[1]):
            return f'Bash({toks[0]} {toks[1]}:*)'
    return f'Bash({stripped})'

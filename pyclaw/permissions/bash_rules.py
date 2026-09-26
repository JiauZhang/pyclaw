from __future__ import annotations

import os
import re
import shlex

from pyclaw.tools.names import BASH

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
GIT_READ_ONLY_SUBCOMMAND_FLAGS = {
    'branch': frozenset({'-a', '--all', '-r', '--remotes', '-l', '--list',
                         '-v', '-vv', '--verbose', '--no-color', '--column',
                         '--merged', '--no-merged', '--points-at'}),
    'tag': frozenset({'-l', '--list', '-n', '--no-color', '--column',
                      '--merged', '--no-merged', '--points-at'}),
    'reflog': frozenset({'show', '--no-color', '--all'}),
    'remote': frozenset({'-v', '--verbose', 'show', 'get-url'}),
}
DOCKER_READ_ONLY_COMMANDS = frozenset({'ps', 'images'})
EDIT_COMMANDS = frozenset({'mkdir', 'touch', 'rm', 'rmdir', 'mv', 'cp', 'sed'})
FIND_WRITING_FLAGS = frozenset({
    '-delete', '-exec', '-execdir', '-ok', '-okdir', '-fprint', '-fls',
    '-fprintf',
})
DANGEROUS_REMOVAL_PATHS = frozenset({'/', '/*', '~', '~/*', '*'})
DANGEROUS_CHARS = frozenset({'$', '`', '*', '?', '>', '<'})
OPERATORS = ('&&', '||')
# A redirect is one piece of the command, not a place to cut it: `2>&1` used to
# split into `2>` and `1`, which made every rule and safety check about it wrong.
REDIRECT_RE = re.compile(r'\d*(?:&>>|&>|>>|<<|<>|[<>])\S*')
HEREDOC_RE = re.compile('<<(-?)(["\']?)([A-Za-z_][A-Za-z0-9_]*)\\2')
OUTPUT_REDIRECT_RE = re.compile(r'\d*(?:&>>|&>|>>|>)[^|;&\n]*')
EXPANSION_CHARS = frozenset({'$', '`', '*', '?'})


def _expands_unquoted(text: str) -> bool:
    """Whether the shell would substitute something we cannot see the result
    of, which makes an exact rule meaningless. A redirect is not one: it is
    copied into the rule as written."""
    quote = ''
    index = 0
    text = str(text)
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                quote = ''
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
        elif char == '\\':
            index += 1
        elif char in EXPANSION_CHARS:
            return True
        index += 1
    return False


def _heredoc_end(text: str, match: re.Match) -> int:
    """Where the body of a heredoc ends, so its contents are never read as
    separate commands."""
    delimiter = match.group(3)
    position = match.end()
    for line_start in iter(lambda: position, len(text)):
        end = text.find('\n', line_start)
        end = len(text) if end < 0 else end + 1
        if text[line_start:end].strip() == delimiter:
            return end
        position = end
        if position >= len(text):
            return position
    return len(text)


def _command_head(text: str) -> str:
    """The part of a command that describes it: what is written into a file or
    fed from a heredoc changes every time, the words before it do not."""
    command = str(text).strip()
    if '\n' in command:
        command = command.split('\n', 1)[0].strip()
    command = command.split('<<', 1)[0]
    return OUTPUT_REDIRECT_RE.sub(' ', command).strip()


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
        heredoc = HEREDOC_RE.match(text, i)
        if heredoc:
            end = _heredoc_end(text, heredoc)
            buf.append(text[i:end])
            i = end
            continue
        redirect = REDIRECT_RE.match(text, i)
        if redirect:
            buf.append(text[i:redirect.end()])
            i = redirect.end()
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
    text = rule_content(rule)
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
                  strip_wrappers(env_safe), _command_head(text)]
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
        if not args or args[0] not in GIT_READ_ONLY_COMMANDS:
            return False
        allowed = GIT_READ_ONLY_SUBCOMMAND_FLAGS.get(args[0])
        return allowed is None or all(a in allowed for a in args[1:])
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
    'sh', 'bash', 'zsh', 'fish', 'csh', 'tcsh', 'ksh', 'dash', 'pwsh',
    'powershell', 'cmd', 'env', 'xargs', 'nice', 'sudo', 'doas', 'pkexec',
    'nohup', 'stdbuf', 'timeout', 'time',
})
_SUBCOMMAND = re.compile(r'^[a-z][a-z0-9]*(-[a-z0-9]+)*$')
_RULE_ESCAPED = ('\\', '(', ')')
_REDIR_TOKEN = re.compile(r'^\d*&?[<>]{1,2}\d*&?$')
MAX_SUGGESTED_RULES = 5
MAX_SPLIT_PARTS = 50


def escape_rule_content(content) -> str:
    """A rule is `Tool(content)`, so only the characters that open or end that
    wrapping need escaping."""
    text = str(content)
    out: list[str] = []
    for char in text:
        out.append('\\' + char if char in _RULE_ESCAPED else char)
    return ''.join(out)


def unescape_rule_content(content) -> str:
    text = str(content)
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == '\\' and index + 1 < len(text):
            out.append(text[index + 1])
            index += 2
            continue
        out.append(char)
        index += 1
    return ''.join(out)


def bash_rule(content) -> str:
    return f'Bash({escape_rule_content(content)})'


def rule_content(rule) -> str:
    """What is inside `Tool(...)`, with the escaping undone; empty when the
    rule names the tool as a whole."""
    _, separator, rest = str(rule).strip().partition('(')
    if not separator:
        return ''
    if rest.endswith(')'):
        rest = rest[:-1]
    return unescape_rule_content(rest)


def _rule_name(rule) -> str:
    return str(rule).partition('(')[0].strip()


def _raw_tokens(text: str) -> list[str]:
    """Whitespace-separated words with their quoting left exactly as it is,
    because a rule has to be able to describe a quoted argument."""
    out: list[str] = []
    buf = ''
    quote = ''
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            buf += char
            if char == quote:
                quote = ''
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
            buf += char
            index += 1
            continue
        if char == '\\' and index + 1 < len(text):
            buf += text[index:index + 2]
            index += 2
            continue
        if char.isspace():
            if buf:
                out.append(buf)
                buf = ''
            index += 1
            continue
        buf += char
        index += 1
    if buf:
        out.append(buf)
    return out


def _unquote(token: str) -> str:
    if len(token) > 1 and token[0] == token[-1] and token[0] in ('"', "'"):
        return token[1:-1]
    return token


def _drop_leading(text: str) -> str:
    """Strip safe variable assignments and shell wrappers, keeping the rest as
    written."""
    tokens = _raw_tokens(text)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if ENV_ASSIGN.match(token) \
                and token.split('=', 1)[0] in SAFE_ENV_VARS:
            index += 1
            continue
        if _unquote(token) in SHELL_WRAPPERS:
            index += 1
            while index < len(tokens) and (tokens[index].startswith('-')
                                           or tokens[index].replace(
                                               '.', '', 1).isdigit()):
                index += 1
            continue
        break
    return ' '.join(tokens[index:])




def _rule_for_part(text: str) -> str | None:
    """One rule that describes one command and still matches it: a two-word
    prefix where the second word is a subcommand, otherwise that command as it
    was written. A prefix is read off the head of the command - the words
    before any redirect or heredoc - because what a command writes where
    changes from call to call while its head does not; an exact rule keeps the
    whole text, since half a command would describe a different one."""
    written = str(text).strip()
    if '\n' in written:
        written = written.split('\n', 1)[0].strip()
    head = _command_head(written)
    if not head or is_dangerous_removal(head) or _has_unquoted_special(head):
        return None
    tokens = _raw_tokens(head)
    if tokens and ENV_ASSIGN.match(tokens[0]) \
            and tokens[0].split('=', 1)[0] not in SAFE_ENV_VARS:
        return None
    tokens = _raw_tokens(_drop_leading(head))
    if len(tokens) >= 2 and _unquote(tokens[0]) not in BARE_SHELL_PREFIXES \
            and _SUBCOMMAND.fullmatch(_unquote(tokens[1])):
        return bash_rule(f'{tokens[0]} {tokens[1]}:*')
    if '<<' in str(text):
        # A heredoc body is different every time, so only the words before it
        # can be described - and a head that gives no usable prefix (python3,
        # a bare interpreter, would authorize arbitrary code) is left unsaved.
        return None
    if _expands_unquoted(written):
        return None
    return bash_rule(written)


def suggested_rules(command) -> list[str]:
    """The rules to remember for one approval. A compound command is answered
    part by part, so the next command that reuses any of those parts is already
    covered; a part that cannot be described yields no rule at all."""
    text = str(command or '').strip()
    if not text or is_dangerous_removal(text):
        return []
    parts = split_commands(text)
    if not parts or len(parts) > MAX_SPLIT_PARTS:
        return []
    rules: list[str] = []
    for part in parts:
        rule = _rule_for_part(part)
        if rule is None:
            return []
        if rule not in rules:
            rules.append(rule)
    return rules[:MAX_SUGGESTED_RULES]


def suggested_rule(command) -> str | None:
    rules = suggested_rules(command)
    return rules[0] if rules else None


def bash_allowed_by(rules, command) -> bool:
    """An allow rule set covers a compound command only when every part of it
    is covered; that is what lets `Bash(git status:*)` stand for
    `git status --short && git stash list` and nothing else. Allowing strips
    only the variables the shell would not misread, while denying strips every
    one: a wider net is the safe direction when the answer is no."""
    parts = split_commands(command)
    if not parts:
        return False
    return all(any(bash_rule_matches(rule, part) for rule in rules)
               for part in parts)


def bash_covered_by(rules, command) -> bool:
    """A deny or ask rule reaches into a compound command: one unsafe part is
    enough to stop the whole line."""
    parts = split_commands(command) or [command]
    return any(bash_rule_matches(rule, part, env_all=True)
               for rule in rules for part in parts)

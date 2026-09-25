from __future__ import annotations

from pyclaw.tui import keys
from rich.markup import escape
from textual.widgets import Input

from chatchat.core.agents import AgentDefinition

from pyclaw import agent_defs
from pyclaw.tui.theme import POINTER


_CHECKED = '\u2612'
_UNCHECKED = '\u2610'
_WARN = '\u26a0'


AGENT_STEPS = ('name', 'location', 'prompt', 'description', 'tools', 'model',
               'confirm')
AGENT_TITLES = {
    'name': 'Name (identifier)',
    'location': 'Where to save',
    'prompt': 'System prompt',
    'description': 'Description (when PyClaw should delegate)',
    'tools': 'Pick tools',
    'model': 'Pick a model',
    'confirm': 'Review and save',
}
AGENT_QUESTIONS = {
    'name': 'What should this agent be called?',
    'prompt': 'What should it do? This text becomes its system prompt.',
    'description': 'When does PyClaw hand work to this agent?',
    'model': 'A model sets how it reasons and how fast it runs.',
}
AGENT_PLACEHOLDERS = {
    'name': 'like test-runner or tech-lead',
    'prompt': 'You review diffs and point out...',
    'description': 'like: once a piece of code is written',
    'model': 'empty keeps the session model',
}
AGENT_TEXT_STEPS = ('name', 'prompt', 'description', 'model')
AGENT_LOCATIONS = (('project', '.pyclaw/agents/'),
                   ('user', '~/.pyclaw/agents/'))
AGENT_NAV = (f'{keys.display("prev")}{keys.display("next")} move \u00b7 '
             f'{keys.hint("open", "picks")} \u00b7 '
             f'{keys.hint("dismiss", "goes back")}')
AGENT_TEXT_NAV = (f'type it in \u00b7 {keys.hint("open", "continues")} \u00b7 '
                  f'{keys.hint("dismiss", "goes back")}')


class AgentFormMixin:

    @property
    def _step_name(self) -> str:
        return AGENT_STEPS[self._step]
    def _is_text_step(self) -> bool:
        if self._mode == 'edit-model':
            return True
        return self._mode == 'create' and self._step_name in AGENT_TEXT_STEPS
    def _header(self, subtitle: str, note=None) -> list:
        lines = ['[bold]Agents[/bold]']
        if self._mode == 'create':
            lines = ['[bold]New agent[/bold]']
        lines.append(f'[dim]{escape(subtitle)}[/]')
        if note:
            lines.append(f'[dim]{escape(str(note))}[/]')
        return lines + ['']
    def _options(self, labels, pos, indent=2) -> list:
        lines = []
        for index, label in enumerate(labels):
            marker = f'{POINTER} ' if index == pos else ' ' * indent
            text = escape(f'{marker}{label}')
            lines.append(f'[{self.app.brand}]{text}[/]' if index == pos
                         else text)
        return lines
    def _footer(self, hint: str) -> list:
        return ['', f'[dim]{escape(hint)}[/]']
    def _problem(self) -> list:
        if not self._error:
            return []
        return [f'[#FF6B80]{escape(self._error)}[/#FF6B80]', '']
    def _render_create(self) -> list:
        name = self._step_name
        if name == 'location':
            lines = self._header(AGENT_TITLES[name])
            return lines + self._options(
                [f'{agent_defs.SCOPE_LABELS[s]} ({hint})'
                 for s, hint in AGENT_LOCATIONS], self._pos) \
                + self._footer(AGENT_NAV)
        if name == 'tools':
            return self._tools_lines(AGENT_TITLES['tools'])
        if name == 'confirm':
            return self._render_confirm()
        lines = self._header(AGENT_TITLES[name])
        lines += self._problem()
        return lines + [escape(AGENT_QUESTIONS[name])] + self._footer(
            AGENT_TEXT_NAV)
    def _render_edit_tools(self) -> list:
        return self._tools_lines(
            self._target.agent_type, title='Change tools')
    def _tools_lines(self, subtitle: str, title=None) -> list:
        lines = [f'[bold]{escape(title or "New agent")}[/bold]',
                 f'[dim]{escape(subtitle)}[/]', '']
        labels = []
        for kind, label, payload in self._tool_items():
            if kind in ('continue', 'toggle'):
                labels.append(f'[ {escape(label)} ]')
            else:
                names = self._names if kind == 'all' else list(payload)
                mark = (_CHECKED
                        if all(n in self._selected_tools for n in names)
                        else _UNCHECKED)
                labels.append(f'{mark} {label}')
        lines += self._options(labels, self._tools_pos)
        chosen = len([n for n in self._names if n in self._selected_tools])
        selected = ('Every tool picked' if chosen == len(self._names)
                    else f'{chosen} of {len(self._names)} picked')
        lines += ['', f'[dim]{escape(selected)}[/]']
        return lines + self._footer(
            f'{keys.hint("open", "toggles")} \u00b7 '
            f'{keys.display("prev")}{keys.display("next")} move \u00b7 '
            f'{keys.hint("dismiss", "goes back")}')
    def _render_edit_model(self) -> list:
        lines = self._header(AGENT_TITLES['model'], self._error or None)
        return lines + [escape(AGENT_QUESTIONS['model'])] \
            + self._footer(AGENT_TEXT_NAV)
    def _render_confirm(self) -> list:
        errors, warnings = agent_defs.validate(
            self._draft_definition(), self._names, self._taken())
        rows = [('Name', self._draft['name']),
                ('Location', agent_defs.SCOPE_LABELS[self._draft['scope']]),
                ('Tools', self._tools_display()),
                ('Model', self._draft['model'] or self._session.model),
                ('Description', self._draft['description']),
                ('System prompt', self._draft['prompt'])]
        lines = self._header(AGENT_TITLES['confirm'], self._error or None)
        lines += [f'[bold]{label}[/bold]: {escape(str(value))}'
                  for label, value in rows]
        if warnings:
            lines += ['', '[bold][dim]Warnings:[/][/bold]']
            lines += [f'[dim] \u2022 {escape(w)}[/]'
                      for w in warnings]
        if errors:
            lines += ['', '[bold][#FF6B80]Errors:[/#FF6B80][/bold]']
            lines += [f'[#FF6B80] \u2022 {escape(e)}[/#FF6B80]'
                      for e in errors]
        return lines + self._footer(
            keys.either('save', 'open', 'saves') + ' \u00b7 '
            + keys.hint('dismiss', 'goes back'))
    def _tools_display(self) -> str:
        chosen = self._draft['tools']
        return 'All tools' if chosen is None else ', '.join(chosen)
    def _tool_items(self) -> list:
        items = [('continue', 'Continue', ()), ('all', 'All tools', ())]
        for label, members in agent_defs.tool_buckets(self._names):
            items.append(('bucket', label, tuple(members)))
        items.append(('toggle',
                      'Hide the tool list' if self._tools_individual
                      else 'Show the tool list', ()))
        if self._tools_individual:
            items += [('tool', name, (name,)) for name in self._names]
        return items
    def _pending_notes_copy(self) -> bool:
        defn = getattr(self._target, 'defn', None)
        return (defn is not None and bool(defn.memory)
                and defn.agent_type in self._session.snapshot_updates)
    def _draft_definition(self):
        chosen = self._draft['tools']
        by_name = {t.name: t for t in self._all_tools}
        tools = (list(self._all_tools) if chosen is None
                 else [by_name[n] for n in chosen if n in by_name])
        return AgentDefinition(
            self._draft['name'], system_prompt=self._draft['prompt'],
            tools=tools, model=self._draft['model'] or None,
            description=self._draft['description'])
    def _move_tools(self, delta: int):
        items = self._tool_items()
        self._tools_pos = min(max(0, self._tools_pos + delta), len(items) - 1)
    def _apply_notes_copy(self):
        defn = self._target.defn
        self._session.apply_snapshot_update(defn.agent_type, defn.memory)
        self._team._pyclaw_snapshot_updates = [
            name for name in self._session.snapshot_updates
            if name != defn.agent_type]
        self._changes.append(
            f'{defn.agent_type} now works from the notes saved in this '
            f'project.')
        self._mode = 'list'
    def _choose_tool_item(self):
        if self._mode == 'create' and self._step_name == 'confirm':
            self._save_draft()
            return
        kind, _label, payload = self._tool_items()[self._tools_pos]
        if kind == 'continue':
            chosen = [n for n in self._names if n in self._selected_tools]
            picked = (None if len(chosen) == len(self._names) else chosen)
            if self._mode == 'edit-tools':
                self._draft['tools'] = picked
                self._save_edit()
            else:
                self._draft['tools'] = picked
                self._advance()
            return
        if kind == 'toggle':
            self._tools_individual = not self._tools_individual
            if not self._tools_individual:
                self._tools_pos = min(self._tools_pos,
                                      len(self._tool_items()) - 1)
            return
        names = self._names if kind == 'all' else list(payload)
        select = not all(n in self._selected_tools for n in names)
        for name in names:
            if select:
                self._selected_tools.add(name)
            else:
                self._selected_tools.discard(name)
    def _advance(self):
        self._step += 1
        self._error = ''
        self._pos = 0
        self._tools_pos = 0
    def _back_step(self):
        if self._step == 0:
            self._mode = 'list'
        else:
            self._step -= 1
            self._error = ''
            self._pos = 0
        self._refresh()
    def on_input_submitted(self, event: Input.Submitted):
        value = event.value.strip()
        if self._mode == 'edit-model':
            self._draft['model'] = value
            self._save_edit()
            self._refresh()
            return
        field = self._step_name
        if field == 'name':
            self._error = agent_defs.validate_type(value) or ''
            if self._error:
                self._refresh()
                return
        elif field in ('prompt', 'description'):
            if not value:
                self._error = (f'{AGENT_TITLES[field].split(" (")[0]} is '
                               'required')
                self._refresh()
                return
        self._draft[field] = value
        self._advance()
        self._refresh()
    def _save_draft(self):
        defn = self._draft_definition()
        try:
            agent_defs.write_agent(defn, self._draft['scope'], self._cwd,
                                   self._all_tools)
        except (FileExistsError, OSError) as e:
            self._error = str(e)
            return
        self._changes.append(f'Added {defn.agent_type}')
        self._error = ''
        self._reload()
        self._sync()
        self._mode = 'list'

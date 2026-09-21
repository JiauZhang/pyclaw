from __future__ import annotations

import dataclasses
from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, Static
from chatchat.core.agents import AgentDefinition
from pyclaw import agent_defs


_POINTER = '\u203a'
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
AGENT_NAV = ('\u2191\u2193 move \u00b7 enter picks \u00b7 esc goes back')
AGENT_TEXT_NAV = ('type it in \u00b7 enter continues \u00b7 esc goes back')


class _AgentKeys(VerticalScroll):

    can_focus = True

    BINDINGS = [("up", "move_up", "Up"), ("down", "move_down", "Down"),
                ("enter", "choose", "Select"), ("escape", "back", "Back"),
                ("s", "save", "Save")]

    def __init__(self, screen, **kw):
        super().__init__(**kw)
        self._screen = screen

    def action_move_up(self):
        self._screen.action_move_up()

    def action_move_down(self):
        self._screen.action_move_down()

    def action_choose(self):
        self._screen.action_choose()

    def action_back(self):
        self._screen.action_back()

    def action_save(self):
        self._screen.action_save()


class _AgentInput(Input):

    BINDINGS = [("escape", "agents_back", "Back")]

    def __init__(self, screen, **kw):
        super().__init__(**kw)
        self._screen = screen

    def action_agents_back(self):
        self._screen.action_back()


class AgentsScreen(Screen):

    def __init__(self, session, **kw):
        super().__init__(**kw)
        self._session = session
        self._team = session._team
        self._all_tools = list(getattr(session._team, 'provided_tools', []))
        self._names = [t.name for t in self._all_tools]
        self._cwd = session.cwd
        self._mode = 'list'
        self._entries = []
        self._selectable = []
        self._pos = 0
        self._menu_pos = 0
        self._delete_pos = 0
        self._tools_pos = 0
        self._tools_individual = False
        self._selected_tools: set = set()
        self._step = 0
        self._target = None
        self._edit_field: str | None = None
        self._draft = {'name': '', 'scope': 'project', 'prompt': '',
                       'description': '', 'model': '', 'tools': None}
        self._changes: list = []
        self._error = ''

    def compose(self) -> ComposeResult:
        with _AgentKeys(self, id='agents'):
            yield Static('', id='agents-body')
        text_input = _AgentInput(self, id='agents-input')
        text_input.display = False
        yield text_input

    def on_mount(self):
        self._reload()
        self._refresh()

    def _reload(self):
        self._entries = agent_defs.list_order(
            agent_defs.discover(self._cwd, self._all_tools))
        self._selectable = [e for e in self._entries
                            if e.scope != agent_defs.BUILT_IN]
        self._pos = min(self._pos, len(self._selectable))

    def _selected(self):
        return None if self._pos == 0 else self._selectable[self._pos - 1]

    @property
    def _step_name(self) -> str:
        return AGENT_STEPS[self._step]


    def _refresh(self):
        lines = {
            'list': self._render_list,
            'menu': self._render_menu,
            'view': self._render_view,
            'delete': self._render_delete,
            'edit-menu': self._render_edit_menu,
            'edit-tools': self._render_edit_tools,
            'edit-model': self._render_edit_model,
            'create': self._render_create,
        }[self._mode]()
        self.query_one('#agents-body', Static).update('\n'.join(lines))
        text_step = self._is_text_step()
        inp = self.query_one('#agents-input', _AgentInput)
        inp.display = text_step
        if text_step:
            field = 'model' if self._mode == 'edit-model' else self._step_name
            inp.placeholder = AGENT_PLACEHOLDERS[field]
            if inp.value != self._draft[field]:
                inp.value = self._draft[field]
                inp.cursor_position = len(inp.value)
            inp.focus()
        else:
            self.query_one('#agents', _AgentKeys).focus()

    def _is_text_step(self) -> bool:
        if self._mode == 'edit-model':
            return True
        return self._mode == 'create' and self._step_name in AGENT_TEXT_STEPS

    def _header(self, subtitle: str, note=None) -> list:
        lines = ['[bold]Agents[/bold]']
        if self._mode == 'create':
            lines = ['[bold]New agent[/bold]']
        lines.append(f'[#9A9A9A]{escape(subtitle)}[/#9A9A9A]')
        if note:
            lines.append(f'[#9A9A9A]{escape(str(note))}[/#9A9A9A]')
        return lines + ['']

    def _options(self, labels, pos, indent=2) -> list:
        lines = []
        for index, label in enumerate(labels):
            marker = f'{_POINTER} ' if index == pos else ' ' * indent
            text = escape(f'{marker}{label}')
            lines.append(f'[{self.app.brand}]{text}[/]' if index == pos
                         else text)
        return lines

    def _footer(self, hint: str) -> list:
        return ['', f'[#9A9A9A]{escape(hint)}[/#9A9A9A]']

    def _problem(self) -> list:
        if not self._error:
            return []
        return [f'[#FF6B80]{escape(self._error)}[/#FF6B80]', '']

    def _render_list(self) -> list:
        if not self._selectable:
            lines = self._header('Nothing defined yet')
            lines += self._options(['New agent'], self._pos, indent=0)
            lines += ['',
                      '[#9A9A9A]No subagents yet. A subagent is a role PyClaw '
                      'can hand a job to.[/]',
                      '[#9A9A9A]Each one brings its own context, prompt and '
                      'tool set.[/]',
                      '[#9A9A9A]Ideas: code reviewer, simplifier, security '
                      'reviewer, tech lead.[/]']
        else:
            count = agent_defs.agent_count(self._entries)
            lines = self._header(f'{count} agents',
                                 self._changes[-1] if self._changes else None)
            lines += self._options(['New agent'], self._pos, indent=0)
            lines.append('')
            position = 0
            for scope in (agent_defs.USER, agent_defs.PROJECT):
                group = [e for e in self._entries if e.scope == scope]
                if not group:
                    continue
                directory = agent_defs.agents_dir(scope, self._cwd)
                lines.append('[bold][#9A9A9A]'
                             f'{escape(agent_defs.SCOPE_LABELS[scope])} '
                             f'({escape(str(directory))})[/#9A9A9A][/bold]')
                for entry in group:
                    position += 1
                    lines.append(self._row(entry, position))
        built_ins = [e for e in self._entries
                     if e.scope == agent_defs.BUILT_IN]
        if built_ins:
            lines += ['', '[bold][#9A9A9A]Bundled with PyClaw[/#9A9A9A][/bold]']
            lines += [self._row(e, -1) for e in built_ins]
        return lines + self._footer(AGENT_NAV)

    def _row(self, entry, position) -> str:
        is_built_in = entry.scope == agent_defs.BUILT_IN
        chosen = not is_built_in and self._pos == position
        marker = '' if is_built_in else (f'{_POINTER} ' if chosen else '  ')
        model = agent_defs.model_display(entry.defn, self._session.model)
        text = f'{marker}{entry.agent_type} \u00b7 {model}'
        if entry.shadowed_by:
            text += f' {_WARN} hidden behind {entry.shadowed_by}'
        text = escape(text)
        if chosen:
            return f'[{self.app.brand}]{text}[/]'
        if is_built_in or entry.shadowed_by:
            return f'[dim]{text}[/dim]'
        return text

    def _render_menu(self) -> list:
        lines = self._header(self._target.agent_type)
        lines += [f'[#9A9A9A]Source: '
                  f'{escape(self._target.scope)}[/#9A9A9A]', '']
        return lines + self._options(
            [label for label, _ in self._menu_options()],
            self._menu_pos) + self._footer(AGENT_NAV)

    def _render_view(self) -> list:
        defn = self._target.defn
        tools = [t.name for t in defn.tools]
        lines = [f'[#9A9A9A]{escape(agent_defs.relative_path(self._target))}'
                 f'[/#9A9A9A]', '']
        lines += ['[bold]Description[/bold] (tells PyClaw when to use this '
                  'agent):',
                  f'  {escape(defn.description or "No description.")}']
        lines.append('[bold]Tools[/bold]: '
                     + (escape(', '.join(tools)) if tools
                        else escape('All tools')))
        lines.append(f'[bold]Model[/bold]: '
                     f'{escape(defn.model or self._session.model)}')
        if defn.permission_mode:
            lines.append('[bold]Permission mode[/bold]: '
                         f'{escape(defn.permission_mode)}')
        lines += ['', '[bold]System prompt[/bold]',
                  escape(defn.system_prompt or '')]
        return lines + self._footer('enter or esc goes back')

    def _render_delete(self) -> list:
        lines = self._header('Delete an agent')
        lines += [escape(f'Remove {self._target.agent_type} for good?'),
                  f'[#9A9A9A]Source: {escape(self._target.scope)}[/#9A9A9A]',
                  '']
        return lines + self._options(['Delete it', 'Keep it'],
                                     self._delete_pos) + self._footer(AGENT_NAV)

    def _render_edit_menu(self) -> list:
        lines = self._header(self._target.agent_type)
        lines += [f'[#9A9A9A]Source: '
                  f'{escape(self._target.scope)}[/#9A9A9A]', '']
        return lines + self._options(['Change tools', 'Change model'],
                                     self._menu_pos) + self._footer(AGENT_NAV)

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
                 f'[#9A9A9A]{escape(subtitle)}[/#9A9A9A]', '']
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
        lines += ['', f'[#9A9A9A]{escape(selected)}[/#9A9A9A]']
        return lines + self._footer(
            'enter toggles \u00b7 \u2191\u2193 move \u00b7 esc goes back')

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
            lines += ['', '[bold][#9A9A9A]Warnings:[/#9A9A9A][/bold]']
            lines += [f'[#9A9A9A] \u2022 {escape(w)}[/#9A9A9A]'
                      for w in warnings]
        if errors:
            lines += ['', '[bold][#FF6B80]Errors:[/#FF6B80][/bold]']
            lines += [f'[#FF6B80] \u2022 {escape(e)}[/#FF6B80]'
                      for e in errors]
        return lines + self._footer('s or enter saves \u00b7 esc goes back')

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

    def _menu_options(self) -> list:
        options = [('Open', 'view')]
        if self._target.scope != agent_defs.BUILT_IN:
            options += [('Change', 'edit'), ('Delete', 'delete')]
        return options + [('Back', 'back')]

    def _menu_values(self) -> list:
        if self._mode == 'edit-menu':
            return ['tools', 'model']
        return [value for _, value in self._menu_options()]

    def _taken(self) -> list:
        return [(e.agent_type, e.scope) for e in self._entries
                if e.scope != agent_defs.BUILT_IN
                and e.agent_type != self._draft['name']]

    def _draft_definition(self):
        chosen = self._draft['tools']
        by_name = {t.name: t for t in self._all_tools}
        tools = (list(self._all_tools) if chosen is None
                 else [by_name[n] for n in chosen if n in by_name])
        return AgentDefinition(
            self._draft['name'], system_prompt=self._draft['prompt'],
            tools=tools, model=self._draft['model'] or None,
            description=self._draft['description'])


    def action_move_up(self):
        self._move(-1)

    def action_move_down(self):
        self._move(1)

    def _move(self, delta: int):
        if self._mode == 'list':
            self._pos = (self._pos + delta) % (len(self._selectable) + 1)
        elif self._mode == 'delete':
            self._delete_pos = min(max(0, self._delete_pos + delta), 1)
        elif self._mode == 'create' and self._step_name == 'location':
            self._pos = 1 - self._pos
        elif ((self._mode == 'create' and self._step_name == 'tools')
                or self._mode == 'edit-tools'):
            self._move_tools(delta)
        elif self._mode in ('menu', 'edit-menu'):
            self._menu_pos = min(max(0, self._menu_pos + delta),
                                 len(self._menu_values()) - 1)
        self._refresh()

    def _move_tools(self, delta: int):
        items = self._tool_items()
        self._tools_pos = min(max(0, self._tools_pos + delta), len(items) - 1)

    def action_choose(self):
        if self._mode == 'list':
            if self._pos == 0:
                self._start_create()
            else:
                self._open(self._selected())
        elif self._mode in ('menu', 'edit-menu'):
            self._choose_menu(self._menu_values()[self._menu_pos])
        elif self._mode == 'delete':
            if self._delete_pos == 0:
                self._delete_target()
            else:
                self._mode = 'menu'
        elif self._mode == 'create' and self._step_name == 'location':
            self._draft['scope'] = AGENT_LOCATIONS[self._pos][0]
            self._advance()
        elif self._mode in ('create', 'edit-tools'):
            self._choose_tool_item()
        elif self._mode == 'view':
            self._mode = 'menu'
        self._refresh()

    def _choose_menu(self, value: str):
        if value == 'view':
            self._mode = 'view'
        elif value == 'edit':
            self._mode = 'edit-menu'
            self._menu_pos = 0
        elif value == 'delete':
            self._mode = 'delete'
            self._delete_pos = 0
        elif value == 'tools':
            self._start_edit_tools()
        elif value == 'model':
            self._start_edit_model()
        else:
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

    def action_save(self):
        if self._mode == 'create' and self._step_name == 'confirm':
            self._save_draft()
            self._refresh()

    def action_back(self):
        if self._mode == 'list':
            self._exit()
        elif self._mode == 'create':
            self._back_step()
        elif self._mode == 'edit-model':
            self._mode = 'edit-menu'
            self._refresh()
        elif self._mode in ('view', 'delete'):
            self._mode = 'menu'
            self._refresh()
        elif self._mode == 'edit-tools':
            self._mode = 'edit-menu'
            self._refresh()
        else:
            self._mode = 'list'
            self._refresh()


    def _start_create(self):
        self._mode = 'create'
        self._step = 0
        self._pos = 0
        self._error = ''
        self._draft = {'name': '', 'scope': 'project', 'prompt': '',
                       'description': '', 'model': '', 'tools': None}
        self._selected_tools = set(self._names)
        self._tools_individual = False
        self._tools_pos = 0

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


    def _open(self, entry):
        self._target = entry
        self._mode = 'menu'
        self._menu_pos = 0
        self._error = ''
        self._refresh()

    def _start_edit_tools(self):
        self._selected_tools = {t.name for t in self._target.defn.tools}
        self._tools_individual = False
        self._tools_pos = 0
        self._edit_field = 'tools'
        self._mode = 'edit-tools'

    def _start_edit_model(self):
        self._draft['model'] = self._target.defn.model or ''
        self._edit_field = 'model'
        self._mode = 'edit-model'

    def _edited_definition(self):
        defn = self._target.defn
        if self._edit_field == 'model':
            return dataclasses.replace(defn,
                                       model=self._draft['model'] or None)
        chosen = self._draft['tools']
        if chosen is None:
            return dataclasses.replace(defn, tools=list(self._all_tools))
        return dataclasses.replace(
            defn, tools=[t for t in self._all_tools
                         if t.name in set(chosen)])

    def _save_edit(self):
        defn = self._edited_definition()
        try:
            agent_defs.write_agent(defn, self._target.scope, self._cwd,
                                   self._all_tools, overwrite=True)
        except OSError as e:
            self._error = str(e)
            return
        self._changes.append(f'Saved changes to {defn.agent_type}')
        self._error = ''
        self._reload()
        self._sync()
        self._mode = 'list'

    def _delete_target(self):
        entry = self._target
        try:
            agent_defs.remove_agent(entry)
        except ValueError as e:
            self._error = str(e)
            self._mode = 'menu'
            return
        self._changes.append(f'Removed {entry.agent_type}')
        self._reload()
        self._sync(gone=(entry.agent_type,))
        self._mode = 'list'

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

    def _sync(self, gone: tuple = ()):
        live = set()
        for entry in self._entries:
            if entry.shadowed_by is None:
                self._team.register_agent_definition(entry.defn)
                live.add(entry.agent_type)
        for agent_type in gone:
            if agent_type not in live:
                self._team.remove_agent_definition(agent_type)

    def _exit(self):
        message = ('What changed:\n' + '\n'.join(self._changes)
                   if self._changes else 'Closed the agents list')
        self.app.pop_screen()
        self.app.call_later(self._show, message)

    async def _show(self, message: str):
        await self.app._append_block(escape(message))

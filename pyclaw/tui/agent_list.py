from __future__ import annotations

import dataclasses

from pyclaw.tui import keys
from rich.markup import escape

from pyclaw.team import defs as agent_defs
from pyclaw.tui.agent_form import AGENT_LOCATIONS, AGENT_NAV
from pyclaw.tui.theme import POINTER


class AgentListMixin:

    def _reload(self):
        self._entries = agent_defs.list_order(
            agent_defs.discover(self._cwd, self._all_tools,
                                cli=self._session.cli_agent_defs))
        self._selectable = [e for e in self._entries
                            if e.scope not in agent_defs.READ_ONLY_SCOPES]
        self._pos = min(self._pos, len(self._selectable))
    def _selected(self):
        return None if self._pos == 0 else self._selectable[self._pos - 1]
    def _render_list(self) -> list:
        given = [e for e in self._entries if e.scope == agent_defs.CLI]
        if not self._selectable and not given:
            lines = self._header('Nothing defined yet')
            lines += self._options(['New agent'], self._pos, indent=0)
            lines += ['',
                      '[dim]No subagents yet. A subagent is a role PyClaw '
                      'can hand a job to.[/]',
                      '[dim]Each one brings its own context, prompt and '
                      'tool set.[/]',
                      '[dim]Ideas: code reviewer, simplifier, security '
                      'reviewer, tech lead.[/]']
        else:
            count = agent_defs.agent_count(self._entries)
            note = (self._changes[-1] if self._changes
                    else self._notes_notice())
            lines = self._header(f'{count} agents', note)
            lines += self._options(['New agent'], self._pos, indent=0)
            lines.append('')
            position = 0
            for scope in (agent_defs.CLI, agent_defs.USER,
                          agent_defs.PROJECT):
                group = [e for e in self._entries if e.scope == scope]
                if not group:
                    continue
                if scope == agent_defs.CLI:
                    lines.append('[bold][dim]'
                                 f'{escape(agent_defs.SCOPE_LABELS[scope])}'
                                 '[/][/bold]')
                    lines += [self._row(e, -1) for e in group]
                    continue
                where = (None if scope == agent_defs.CLI
                         else agent_defs.agents_dir(scope, self._cwd))
                where = '' if where is None else f' ({escape(str(where))})'
                lines.append('[bold][dim]'
                             f'{escape(agent_defs.SCOPE_LABELS[scope])}'
                             f'{where}[/][/bold]')
                for entry in group:
                    position += 1
                    lines.append(self._row(entry, position))
        built_ins = [e for e in self._entries
                     if e.scope == agent_defs.BUILT_IN]
        if built_ins:
            lines += ['', '[bold][dim]Bundled with PyClaw[/][/bold]']
            lines += [self._row(e, -1) for e in built_ins]
        return lines + self._footer(AGENT_NAV)
    def _row(self, entry, position) -> str:
        is_built_in = entry.scope in agent_defs.READ_ONLY_SCOPES
        chosen = not is_built_in and self._pos == position
        marker = '' if is_built_in else (f'{POINTER} ' if chosen else '  ')
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
        lines += [f'[dim]Source: '
                  f'{escape(self._target.scope)}[/]', '']
        return lines + self._options(
            [label for label, _ in self._menu_options()],
            self._menu_pos) + self._footer(AGENT_NAV)
    def _render_view(self) -> list:
        defn = self._target.defn
        tools = [t.name for t in defn.tools]
        lines = [f'[dim]{escape(agent_defs.relative_path(self._target))}'
                 f'[/]', '']
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
        if defn.memory:
            lines.append(f'[bold]Notes[/bold]: {escape(defn.memory)} scope')
        lines += ['', '[bold]System prompt[/bold]',
                  escape(defn.system_prompt or '')]
        return lines + self._footer(
            keys.either('open', 'dismiss', 'goes back'))
    def _render_delete(self) -> list:
        lines = self._header('Delete an agent')
        lines += [escape(f'Remove {self._target.agent_type} for good?'),
                  f'[dim]Source: {escape(self._target.scope)}[/]',
                  '']
        return lines + self._options(['Delete it', 'Keep it'],
                                     self._delete_pos) + self._footer(AGENT_NAV)
    def _render_edit_menu(self) -> list:
        lines = self._header(self._target.agent_type)
        lines += [f'[dim]Source: '
                  f'{escape(self._target.scope)}[/]', '']
        return lines + self._options(['Change tools', 'Change model'],
                                     self._menu_pos) + self._footer(AGENT_NAV)
    def _notes_notice(self) -> str | None:
        pending = self._session.snapshot_updates
        if not pending:
            return None
        return ('Newer notes are saved in this project for: '
                + ', '.join(pending))
    def _menu_options(self) -> list:
        options = [('Open', 'view')]
        if self._pending_notes_copy():
            options.append(('Use the project copy of its notes', 'notes'))
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
        elif value == 'notes':
            self._apply_notes_copy()
        elif value == 'notes':
            self._apply_notes_copy()
        elif value == 'tools':
            self._start_edit_tools()
        elif value == 'model':
            self._start_edit_model()
        else:
            self._mode = 'list'
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

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, Static

from pyclaw.tui.agent_form import AGENT_PLACEHOLDERS, AgentFormMixin
from pyclaw.tui.agent_list import AgentListMixin


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


class AgentsScreen(AgentListMixin, AgentFormMixin, Screen):

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

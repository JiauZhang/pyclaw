import asyncio

from textual import events
from textual.app import App, ComposeResult
from textual.widgets import Static

from pyclaw.permissions import PermissionChoice
from pyclaw.tui.approval_flow import ApprovalFlowMixin
from pyclaw.tui.components.inputs import _PromptInput
from pyclaw.tui.permission_card import _Approval, _PermissionPrompt


class _Host(ApprovalFlowMixin, App):

    def register_overlay(self, name):
        pass

    def unregister_overlay(self, name):
        pass

    def compose(self) -> ComposeResult:
        yield _PermissionPrompt('Bash', {'command': 'ls'}, cwd='/tmp')
        yield _PromptInput(id='input')


def _mouse_down(x, y):
    return events.MouseDown(None, x=x, y=y, delta_x=0, delta_y=0, button=1,
                            shift=False, meta=False, ctrl=False,
                            screen_x=x, screen_y=y)


def test_settle_clears_selection_state():
    async def scenario():
        app = _Host()
        async with app.run_test() as pilot:
            body = app.query_one('#perm-body', Static)
            await pilot.pause()
            x, y = body.region.center

            app.screen._forward_event(_mouse_down(x, y))
            await pilot.pause()
            assert app.screen._selecting is True
            assert app.screen._select_state is not None

            prompt = app.query_one(_PermissionPrompt)
            approval = _Approval('t1', 'Bash', prompt, None,
                                 asyncio.get_running_loop().create_future())
            app._approvals = [approval]
            app._settle_approval(approval, PermissionChoice('approved'))
            await pilot.pause()

            assert app.screen._selecting is False
            assert app.screen._select_state is None

    asyncio.run(scenario())


def test_withdraw_clears_selection_state():
    async def scenario():
        app = _Host()
        async with app.run_test() as pilot:
            body = app.query_one('#perm-body', Static)
            await pilot.pause()
            x, y = body.region.center

            app.screen._forward_event(_mouse_down(x, y))
            await pilot.pause()
            assert app.screen._selecting is True

            prompt = app.query_one(_PermissionPrompt)
            approval = _Approval('t1', 'Bash', prompt, None,
                                 asyncio.get_running_loop().create_future())
            app._approvals = [approval]
            app._withdraw_approval(approval)
            await pilot.pause()

            assert app.screen._selecting is False
            assert app.screen._select_state is None

    asyncio.run(scenario())

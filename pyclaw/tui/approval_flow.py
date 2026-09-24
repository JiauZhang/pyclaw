from __future__ import annotations

import asyncio
import logging

from textual.widgets import Input

from pyclaw.tools.coding import next_mode
from pyclaw.tools.coding.permission import PermissionChoice
from pyclaw.tui.approval import _Approval, _PermissionPrompt, _QuestionPrompt
from pyclaw.tui.components import _ToolBlock

logger = logging.getLogger(__name__)


class ApprovalFlowMixin:
    async def _ask_permission(self, tool_name: str, tool_input,
                              *, tool_use_id: str = '',
                              agent: str | None = None) -> PermissionChoice:
        inp = tool_input if isinstance(tool_input, dict) else {}
        rule = self._session.permission_rule(tool_name, inp)
        prompt = _PermissionPrompt(tool_name, inp, cwd=self._cwd(),
                                   rememberable=bool(rule) or tool_name != 'Bash',
                                   rule=rule, agent=self._badge(agent))
        block = self._tools.get(tool_use_id) if tool_use_id else None
        approval = _Approval(tool_use_id, tool_name, prompt, block,
                             asyncio.get_running_loop().create_future())
        prompt.on_choice = lambda choice: self._answer_approval(approval, choice)
        self._approvals.append(approval)
        if self._approvals[0] is approval:
            await self._mount_approval(approval)
        try:
            return await approval.future
        except asyncio.CancelledError:
            self._withdraw_approval(approval)
            raise
    async def _ask_questions(self, agent, questions) -> list[str]:
        name = getattr(agent, 'name', agent)
        prompt = _QuestionPrompt(list(questions), agent=self._badge(name))
        future = asyncio.get_running_loop().create_future()
        prompt.on_answer = lambda answers: (
            None if future.done() else future.set_result(answers))
        await self._conv().mount(prompt)
        await self._after_mount()
        try:
            return await future
        finally:
            prompt.remove()
            for field in self.query("#input"):
                field.focus()
    def _withdraw_approval(self, approval: _Approval):
        if approval in self._approvals:
            self._approvals.remove(approval)
        approval.prompt.remove()
        block = approval.block
        if isinstance(block, _ToolBlock):
            block.set_waiting_permission(False)
        if not approval.future.done():
            approval.future.cancel()
    def _badge(self, agent: str | None) -> str:
        if not agent:
            return ''
        return '' if agent == self._team.lead.name else agent
    async def _mount_approval(self, approval: _Approval):
        block = approval.block
        if isinstance(block, _ToolBlock):
            block.set_waiting_permission(True)
        await self._conv().mount(approval.prompt)
        await self._after_mount()
    def _answer_approval(self, approval: _Approval, choice: PermissionChoice):
        self._settle_approval(approval, choice)
        if self._approvals:
            asyncio.create_task(self._mount_approval(self._approvals[0]))
    def _settle_approval(self, approval: _Approval, choice: PermissionChoice):
        if approval in self._approvals:
            self._approvals.remove(approval)
        approval.prompt.remove()
        block = approval.block
        if isinstance(block, _ToolBlock):
            block.set_waiting_permission(False)
        if not approval.future.done():
            approval.future.set_result(choice)
        self.query_one("#input", Input).focus()
    async def _deny_pending_permission(self) -> bool:
        if not self._approvals:
            return False
        for approval in list(self._approvals):
            self._settle_approval(approval, PermissionChoice("denied"))
            block = approval.block
            if isinstance(block, _ToolBlock) and not block._done:
                block.reject()
        self._interrupted_call = True
        return True
    async def action_cycle_permission(self):
        if self._session is None:
            return
        nxt = next_mode(self._session.permission_mode,
                        bypass_available=self._session.bypass_available)
        self._session.set_permission_mode(nxt.value)
        self._render_status()

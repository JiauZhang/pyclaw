from __future__ import annotations

import sys

from pyclaw.tui import keys


BULLET = "\u23fa" if sys.platform == "darwin" else "\u25cf"
POINTER = "\u276f"
RESULT_GLYPH = "\u23bf"
ASTERISK = "\u273b"
BULLET_PREFIX = f"{BULLET} "
RESULT_PREFIX = f"  {RESULT_GLYPH}  "
RESULT_HANG = " " * len(RESULT_PREFIX)


TREE_LEAD = ("\u250c\u2500", "\u2552\u2550")
TREE_BRANCH = ("\u251c\u2500", "\u255e\u2550")
TREE_LAST = ("\u2514\u2500", "\u2558\u2550")
TREE_INDENT = "   "
TREE_POINTER = POINTER
SELECT_HINT = keys.chord('agent_prev', 'agent_next', 'picks a row')
VIEW_HINT = keys.hint('open', 'opens it')
COLLAPSE_HINT = keys.hint('open', 'closes it')
IDLE_TEXT = "Idle"
AGENT_TEAMMATES_HINT = "subagents are active"
TEAMMATE_VIEW_HINT = keys.hint('dismiss', 'goes back to the lead')
STOPPING_TEXT = "Stopping\u2026"
STOPPED_TEXT = "Stopped"


ROW_PREFIX = 8
ROW_ACTIVITY = 25
ROW_NARROW = 60
ROW_STATS_GAP = 5
ROW_CONTINUATION = ("\u2502  ", "   ")
PREVIEW_LINES = 3
PREVIEW_CHARS = 80
RECENT_ACTIVITIES = 5


PLAN_ICONS = {"completed": "\u2713", "in_progress": "\u25aa",
              "pending": "\u25ab"}
DONE_COLOR = "#4EBA65"
PLAN_MAX_LINES = 10
PLAN_MIN_ROWS = 10
PLAN_RECENT_SECONDS = 30.0
NEXT_PREFIX = "Next: "
BLOCKED_PREFIX = " \u203a blocked by "
PLAN_HIDDEN = " \u2026 +"


AGENT_TRAIL_LIMIT = 3
DONE_TEXT = "Done"
INITIALIZING_TEXT = "Starting up\u2026"
EXPAND_HINT = keys.hint('toggle_transcript', 'shows more')


WAITING_PERMISSION_TEXT = "Needs your approval\u2026"
INTERRUPTED_TEXT = "Stopped \u00b7 tell PyClaw what to do instead"


OPTION_PAGE_SIZE = 5
FINISHED_LINGER_SECONDS = 30
ACCEPT_FEEDBACK_HINT = "and tell PyClaw what to do next"
REJECT_FEEDBACK_HINT = "and tell PyClaw what to do differently"
RULE_FEEDBACK_HINT = "a command prefix, like npm run:*"
ANSWER_HINT = "answer in your own words"


OVERLAY_GATED_ACTIONS = frozenset({
    'suggest_tab', 'prompt_next', 'prompt_prev', 'agent_next', 'agent_prev',
    'stop_agent', 'cycle_permission', 'focus_next', 'focus_previous'})


AGENT_COLORS = ("#FF6B80", "#4782C8", "#4EBA65", "#FFC107",
                "#AF87FF", "#D77757", "#FD5DB1", "#48968C")


MODE_SYMBOLS = {"acceptEdits": "\u23f5\u23f5",
                "bypassPermissions": "\u23f5\u23f5", "plan": "\u23f8"}
MODE_TITLES = {"acceptEdits": "accept edits", "plan": "plan mode",
               "bypassPermissions": "skip permission prompts"}
MODE_COLORS = {"acceptEdits": "#AF87FF", "plan": "#48968C",
               "bypassPermissions": "#FF6B80"}


SPINNER_CHARS = ["\u00b7", "\u2722", "\u2733", "\u2736", "\u273b", "\u273d"]
SPINNER_FRAMES = SPINNER_CHARS + list(reversed(SPINNER_CHARS))
SPINNER_INTERVAL = 0.05


MAX_COMMAND_LINES = 2
MAX_COMMAND_CHARS = 160
MAX_RESULT_LINES = 3
MAX_USE_ARG_CHARS = 80


DISPLAY_NAMES = {"Edit": "Update", "Grep": "Search",
                 "Glob": "Search"}

MEMORY_FILE_NAME = 'AGENTS.md'


GROUP_PARTS = (
    ('search', 'Looking for', 'Looked for', 'pattern', 'patterns'),
    ('read', 'Opening', 'Opened', 'file', 'files'),
    ('list', 'Walking', 'Walked', 'folder', 'folders'),
    ('bash', 'Executing', 'Executed', 'shell command', 'shell commands'),
    ('memory_read', 'Remembering', 'Remembered', 'memory', 'memories'),
    ('memory_write', 'Saving', 'Saved', 'memory', 'memories'),
)


ROLLUP_KINDS = frozenset(kind for kind, *_ in GROUP_PARTS) - {'bash'}

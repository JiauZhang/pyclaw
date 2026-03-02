"""Session management for agent."""

from typing import List, Dict, Any


class SessionManager:
    """Manage agent sessions."""

    def __init__(self):
        """Initialize session manager."""
        self._sessions: Dict[str, List[Dict[str, Any]]] = {}

    def get_session_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Get or create session history.

        Args:
            session_id: Session ID

        Returns:
            Session history
        """
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        return self._sessions[session_id]

    def clear_session(self, session_id: str):
        """Clear session history.

        Args:
            session_id: Session ID
        """
        if session_id in self._sessions:
            del self._sessions[session_id]

    def get_session_count(self) -> int:
        """Get number of active sessions.

        Returns:
            Session count
        """
        return len(self._sessions)

    def build_messages(self, history: List[Dict[str, Any]], instruction: str) -> List[Dict[str, Any]]:
        """Build message list for API call.

        Args:
            history: Session history
            instruction: System instruction

        Returns:
            Messages list
        """
        messages = []
        
        if instruction:
            messages.append({"role": "system", "content": instruction})
        
        messages.extend(history)
        
        return messages

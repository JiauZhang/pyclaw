"""Stream handler for agent."""

import logging
from typing import AsyncIterator, Optional
from ..client import ClientManager
from ..tools.registry import ToolRegistryAdapter
from ..tools.parser import ToolCallParser
from ..sessions.session_manager import SessionManager
from ..core.conversation import ConversationRunner

logger = logging.getLogger(__name__)


class StreamHandler:
    """Handle streaming responses."""

    def __init__(self, client_manager: ClientManager, tool_registry_adapter: ToolRegistryAdapter, session_manager: SessionManager):
        """Initialize stream handler.

        Args:
            client_manager: Client manager
            tool_registry_adapter: Tool registry adapter
            session_manager: Session manager
        """
        self.client_manager = client_manager
        self.tool_registry_adapter = tool_registry_adapter
        self.session_manager = session_manager
        self.tool_parser = ToolCallParser()
        self.conversation_runner = ConversationRunner(
            client_manager=client_manager,
            tool_registry_adapter=tool_registry_adapter,
            session_manager=session_manager,
            tool_parser=self.tool_parser
        )

    async def handle_stream(self, message: str, session, context, max_iterations: int) -> AsyncIterator[str]:
        """Handle streaming response.

        Args:
            message: User message
            session: Session state
            context: Agent context
            max_iterations: Maximum tool call iterations

        Yields:
            Response chunks
        """
        # Get or create conversation history
        history = self.session_manager.get_session_history(session.id)
        
        # Add user message
        history.append({"role": "user", "content": message})
        
        try:
            # Run the conversation with potential tool calls
            for iteration in range(max_iterations):
                # Build messages for this turn
                messages = self.session_manager.build_messages(history, self.client_manager.instruction)
                
                # Get last user message
                last_user_msg = self.conversation_runner.get_last_user_message(messages)
                
                if not last_user_msg:
                    yield "No user message found."
                    return
                
                # Get AI response
                try:
                    response = await self.conversation_runner.get_ai_response(last_user_msg, stream=False)
                    full_response = self.conversation_runner.get_response_text(response)
                except Exception as e:
                    logger.error(f"AI error: {e}")
                    yield f"AI Error: {str(e)}"
                    return
                
                # Check for tool call
                tool_call = self.tool_parser.parse(full_response)
                
                if not tool_call:
                    # No tool call, stream the response in real-time
                    history.append({"role": "assistant", "content": full_response})
                    
                    # Get the response again with streaming enabled
                    try:
                        response = self.client_manager.chat(
                            text=last_user_msg,
                            stream=True
                        )
                        
                        # Stream the response in real-time
                        real_time_response = ""
                        for chunk in response:
                            chunk_text = self.conversation_runner.get_chunk_text(chunk)
                            if chunk_text:
                                real_time_response += chunk_text
                                yield chunk_text
                    except Exception as e:
                        # Fallback to chunked streaming if real-time streaming fails
                        chunk_size = 10
                        for i in range(0, len(full_response), chunk_size):
                            yield full_response[i:i+chunk_size]
                    return
                
                # Execute tool
                tool_name = tool_call.get("tool")
                tool_args = tool_call.get("args", {})
                
                # Stream friendly tool call message
                yield f"正在调用{tool_name}工具..."
                
                # Add a newline after tool call message
                yield "\n"
                
                tool_result = await self.conversation_runner.execute_tool_call(
                    tool_name, tool_args, context, history
                )
                
                # Add tool interaction to history
                history.append({"role": "assistant", "content": full_response})
                history.append({
                    "role": "user",
                    "content": f"Tool '{tool_result['tool_name']}' result: {tool_result['result']}"
                })
                
                # Now get AI response based on tool result
                try:
                    # Build updated messages with tool result
                    messages = self.session_manager.build_messages(history, self.client_manager.instruction)
                    
                    # Get last user message (which is the tool result)
                    last_user_msg = self.conversation_runner.get_last_user_message(messages)
                    
                    if not last_user_msg:
                        yield "No user message found."
                        return
                    
                    # Get AI response with streaming
                    response = await self.conversation_runner.get_ai_response(last_user_msg, stream=True)
                    
                    # Stream the response chunks directly
                    final_response = ""
                    for chunk in response:
                        chunk_text = self.conversation_runner.get_chunk_text(chunk)
                        if chunk_text:
                            # Clean up any unwanted markers
                            cleaned_text = chunk_text.replace('```', '').replace('json', '').strip()
                            if cleaned_text:
                                final_response += cleaned_text
                                yield cleaned_text
                    
                    # Add final response to history
                    history.append({"role": "assistant", "content": final_response})
                    return
                except Exception as e:
                    logger.error(f"AI error after tool execution: {e}")
                    yield f"AI Error: {str(e)}"
                    return
            
            yield "Maximum tool iterations reached."
                    
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield f"Error: {str(e)}"

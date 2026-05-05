"""Agent RPC handler."""

import logging
import os
from typing import Dict, Any, Optional

from ...agents import Agent
from ...config import load

logger = logging.getLogger(__name__)

_agent_cache: Dict[str, Agent] = {}


def _get_or_create_agent(
    agent_id: str,
    config: Any,
    provider: Optional[str] = None,
    model: Optional[str] = None
) -> Agent:
    """Get or create an agent instance."""
    cache_key = f'{agent_id}:{provider}:{model}'

    if cache_key in _agent_cache:
        return _agent_cache[cache_key]

    agents = config.get('agents', {})
    default_agent = config.get('default_agent', 'default')
    agent_config = agents.get(agent_id, agents.get(default_agent, {})) if agents else {}

    if not provider:
        provider = agent_config.get('provider') or os.getenv('OPENCLAW_PROVIDER', 'openrouter')
    if not model:
        model = agent_config.get('model') or os.getenv('OPENCLAW_MODEL', 'tencent/hy3-preview:free')

    instruction = agent_config.get('system_prompt') if agent_config else None

    agent = Agent(
        provider=provider,
        model=model,
        instruction=instruction,
    )
    logger.info(f'Created Agent for {agent_id} using {provider}/{model or "default"}')

    _agent_cache[cache_key] = agent
    return agent


async def handle_agent(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Handle agent RPC call."""
    message = params.get('message', '').strip()
    session_key = params.get('sessionKey') or params.get('session_id') or 'default'
    agent_id = params.get('agentId') or params.get('agent_id')
    provider = params.get('provider')
    model = params.get('model')
    stream = params.get('stream', False)

    if not message:
        return {'error': 'Message is required'}

    config = load()
    runtime = context.get('runtime')
    gateway = context.get('gateway')

    if not provider and gateway and gateway.config.provider:
        provider = gateway.config.provider
    if not model and gateway and gateway.config.model:
        model = gateway.config.model

    runtime.get_or_create_session(session_key, agent_id or 'default')

    try:
        agent = _get_or_create_agent(agent_id or 'default', config, provider, model)
    except Exception as e:
        logger.error(f'Failed to create agent: {e}')
        return {'error': f'Failed to initialize agent: {str(e)}'}

    try:
        if stream:
            return {
                'stream': True,
                'sessionKey': session_key,
                'agentId': agent_id or 'default',
                'message': 'Use /v1/chat/completions for streaming'
            }
        else:
            response = agent.chat(message)
            runtime.update_session_activity(session_key)
            runtime.increment_requests()

            return {
                'response': response,
                'sessionKey': session_key,
                'agentId': agent_id or 'default',
                'tools_available': agent.get_available_tools(),
                'provider': agent.provider,
                'model': agent.model,
            }
    except Exception as e:
        logger.error(f'Agent error: {e}')
        runtime.increment_errors()
        return {'error': str(e)}


async def handle_agent_stream(params: Dict[str, Any], context: Dict[str, Any]):
    """Handle streaming agent RPC call."""
    message = params.get('message', '').strip()
    session_key = params.get('sessionKey') or params.get('session_id') or 'default'
    agent_id = params.get('agentId') or params.get('agent_id')
    provider = params.get('provider')
    model = params.get('model')

    if not message:
        yield {'error': 'Message is required'}
        return

    config = load()
    runtime = context.get('runtime')
    gateway = context.get('gateway')

    if not provider and gateway and gateway.config.provider:
        provider = gateway.config.provider
    if not model and gateway and gateway.config.model:
        model = gateway.config.model

    runtime.get_or_create_session(session_key, agent_id or 'default')

    try:
        agent = _get_or_create_agent(agent_id or 'default', config, provider, model)
    except Exception as e:
        yield {'error': f'Failed to initialize agent: {str(e)}'}
        return

    try:
        for chunk in agent.chat_stream(message):
            yield {'chunk': chunk}

        runtime.update_session_activity(session_key)
        runtime.increment_requests()
    except Exception as e:
        logger.error(f'Stream error: {e}')
        yield {'error': str(e)}


async def handle_agent_tools(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Get available tools for an agent."""
    agent_id = params.get('agentId') or params.get('agent_id')
    provider = params.get('provider')
    model = params.get('model')

    config = load()

    try:
        agent = _get_or_create_agent(agent_id or 'default', config, provider, model)

        return {
            'agentId': agent_id or 'default',
            'provider': agent.provider,
            'model': agent.model,
            'tools': agent.get_available_tools(),
            'schemas': agent.get_tool_schemas(),
        }
    except Exception as e:
        return {'error': str(e)}


async def handle_tool_call(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a tool call directly (deprecated, tools are handled by chatchat agent)."""
    return {
        'error': 'Direct tool calls are no longer supported. Tools are automatically managed by the agent.'
    }


async def handle_chat_completions(params: Dict[str, Any], context: Dict[str, Any]):
    """OpenAI-compatible chat completions endpoint."""
    messages = params.get('messages', [])
    model = params.get('model', 'default')
    stream = params.get('stream', False)
    session_key = params.get('sessionKey') or params.get('session_id') or 'default'
    agent_id = params.get('agentId') or params.get('agent_id')

    if not messages:
        yield {'error': 'Messages are required'}
        return

    config = load()
    runtime = context.get('runtime')
    gateway = context.get('gateway')
    runtime.get_or_create_session(session_key, agent_id or 'default')

    provider = None
    model_name = model
    if '/' in model:
        parts = model.split('/', 1)
        provider = parts[0]
        model_name = parts[1] if parts[1] else None

    if not provider and gateway and gateway.config.provider:
        provider = gateway.config.provider
    if not model_name and gateway and gateway.config.model:
        model_name = gateway.config.model

    try:
        agent = _get_or_create_agent(agent_id or 'default', config, provider, model_name)

        last_message = None
        for msg in reversed(messages):
            if msg.get('role') == 'user':
                last_message = msg.get('content', '')
                break

        if not last_message:
            yield {'error': 'No user message found'}
            return

        if stream:
            full_response = ''
            for chunk in agent.chat_stream(last_message):
                full_response += chunk
                yield {
                    'choices': [{
                        'delta': {'content': chunk},
                        'index': 0
                    }]
                }

            runtime.update_session_activity(session_key)
            runtime.increment_requests()
        else:
            response = agent.chat(last_message)

            runtime.update_session_activity(session_key)
            runtime.increment_requests()

            yield {
                'choices': [{
                    'message': {
                        'role': 'assistant',
                        'content': response
                    },
                    'index': 0,
                    'finish_reason': 'stop'
                }],
                'model': model,
                'usage': {
                    'prompt_tokens': 0,
                    'completion_tokens': 0,
                    'total_tokens': 0
                }
            }

    except Exception as e:
        logger.error(f'Chat completions error: {e}')
        yield {'error': str(e)}

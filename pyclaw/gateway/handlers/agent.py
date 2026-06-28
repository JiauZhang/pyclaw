import logging
import os
from typing import Dict, Any, Optional

from ...agents import Agent
from ...config import load

logger = logging.getLogger(__name__)

_agent_cache: Dict[str, Agent] = {}
_agent_cache_maxsize = 20


def _agent_cache_clear(agent_id: Optional[str] = None):
    if agent_id is None:
        _agent_cache.clear()
        return
    keys = [k for k in _agent_cache if k.startswith(f'{agent_id}:')]
    for k in keys:
        del _agent_cache[k]


def _get_or_create_agent(
    agent_id: str,
    config: Any,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Agent:
    cache_key = f'{agent_id}:{provider}:{model}'

    if cache_key in _agent_cache:
        return _agent_cache[cache_key]

    # Evict oldest entry if cache is full
    if len(_agent_cache) >= _agent_cache_maxsize:
        oldest = next(iter(_agent_cache))
        del _agent_cache[oldest]

    agents = config.get('agents', {})
    default_agent = config.get('default_agent', 'default')
    agent_config = agents.get(agent_id, agents.get(default_agent, {})) if agents else {}

    if not provider:
        provider = agent_config.get('provider') or os.getenv('OPENCLAW_PROVIDER', 'agnes')
    if not model:
        model = agent_config.get('model') or os.getenv('OPENCLAW_MODEL', 'agnes-2.0-flash')

    instruction = agent_config.get('system_prompt') if agent_config else None

    agent = Agent(
        provider=provider,
        model=model,
        instruction=instruction,
    )
    logger.info(f'Created Agent for {agent_id} using {provider}/{model or "default"}')

    _agent_cache[cache_key] = agent
    return agent


def _resolve_params(
    params: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    config = load()
    runtime = context.get('runtime')
    gateway = context.get('gateway')

    message = params.get('message', '').strip()
    session_key = params.get('sessionKey') or params.get('session_id') or 'default'
    agent_id = params.get('agentId') or params.get('agent_id')
    provider = params.get('provider')
    model = params.get('model')
    stream = params.get('stream', False)

    if not provider and gateway and gateway.config.provider:
        provider = gateway.config.provider
    if not model and gateway and gateway.config.model:
        model = gateway.config.model

    runtime.get_or_create_session(session_key, agent_id or 'default')

    return dict(
        message=message,
        session_key=session_key,
        agent_id=agent_id or 'default',
        provider=provider,
        model=model,
        stream=stream,
        config=config,
        runtime=runtime,
    )


async def handle_agent(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    resolved = _resolve_params(params, context)
    if not resolved['message']:
        return {'error': 'Message is required'}

    try:
        agent = _get_or_create_agent(resolved['agent_id'], resolved['config'], resolved['provider'], resolved['model'])
    except Exception as e:
        logger.error(f'Failed to create agent: {e}')
        return {'error': f'Failed to initialize agent: {str(e)}'}

    try:
        if resolved['stream']:
            return {
                'stream': True,
                'sessionKey': resolved['session_key'],
                'agentId': resolved['agent_id'],
                'message': 'Use /v1/chat/completions for streaming',
            }
        response = agent.chat(resolved['message'])
        resolved['runtime'].update_session_activity(resolved['session_key'])
        resolved['runtime'].increment_requests()
        return {
            'response': response,
            'sessionKey': resolved['session_key'],
            'agentId': resolved['agent_id'],
            'tools_available': agent.get_available_tools(),
            'provider': agent.provider,
            'model': agent.model,
        }
    except Exception as e:
        logger.error(f'Agent error: {e}')
        resolved['runtime'].increment_errors()
        return {'error': str(e)}


async def handle_agent_stream(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    resolved = _resolve_params(params, context)
    if not resolved['message']:
        return {'error': 'Message is required'}

    try:
        agent = _get_or_create_agent(resolved['agent_id'], resolved['config'], resolved['provider'], resolved['model'])
    except Exception as e:
        return {'error': f'Failed to initialize agent: {str(e)}'}

    try:
        response = agent.chat(resolved['message'])
        resolved['runtime'].update_session_activity(resolved['session_key'])
        resolved['runtime'].increment_requests()
        return {
            'response': response,
            'sessionKey': resolved['session_key'],
            'agentId': resolved['agent_id'],
            'stream': False,
        }
    except Exception as e:
        logger.error(f'Stream error: {e}')
        resolved['runtime'].increment_errors()
        return {'error': str(e)}


async def handle_agent_tools(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    resolved = _resolve_params(params, context)

    try:
        agent = _get_or_create_agent(resolved['agent_id'], resolved['config'], resolved['provider'], resolved['model'])
        return {
            'agentId': resolved['agent_id'],
            'provider': agent.provider,
            'model': agent.model,
            'tools': agent.get_available_tools(),
        }
    except Exception as e:
        return {'error': str(e)}


async def handle_tool_call(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'error': 'Direct tool calls are no longer supported. Tools are automatically managed by the agent.'
    }


async def handle_chat_completions(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    messages = params.get('messages', [])
    model_param = params.get('model', 'default')

    if not messages:
        return {'error': 'Messages are required'}

    resolved = _resolve_params(params, context)

    provider = resolved['provider']
    model_name = model_param
    if '/' in model_param:
        parts = model_param.split('/', 1)
        provider = parts[0]
        model_name = parts[1] if parts[1] else None

    try:
        agent = _get_or_create_agent(resolved['agent_id'], resolved['config'], provider, model_name)
    except Exception as e:
        return {'error': f'Failed to initialize agent: {str(e)}'}

    last_message = next(
        (msg.get('content', '') for msg in reversed(messages) if msg.get('role') == 'user'),
        None
    )
    if not last_message:
        return {'error': 'No user message found'}

    try:
        response = agent.chat(last_message)
        resolved['runtime'].update_session_activity(resolved['session_key'])
        resolved['runtime'].increment_requests()
        return {
            'choices': [{
                'message': {'role': 'assistant', 'content': response},
                'index': 0,
                'finish_reason': 'stop',
            }],
            'model': model_param,
            'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
        }
    except Exception as e:
        logger.error(f'Chat completions error: {e}')
        resolved['runtime'].increment_errors()
        return {'error': str(e)}

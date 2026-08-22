import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def _resolve_params(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    gateway = context.get('gateway')
    runtime = context.get('runtime')

    message = params.get('message', '').strip()
    session_key = params.get('sessionKey') or params.get('session_id') or 'default'
    provider = params.get('provider')
    model = params.get('model')

    if not provider and gateway and gateway.config.provider:
        provider = gateway.config.provider
    if not model and gateway and gateway.config.model:
        model = gateway.config.model

    return dict(
        message=message,
        session_key=session_key,
        provider=provider,
        model=model,
        stream=params.get('stream', False),
        runtime=runtime,
        gateway=gateway,
    )


async def _get_session(resolved: Dict[str, Any]):
    gateway = resolved['gateway']
    return await gateway._get_session(
        resolved['session_key'], resolved['provider'], resolved['model'],
    )


async def handle_agent(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    resolved = _resolve_params(params, context)
    if not resolved['message']:
        return {'error': 'Message is required'}

    try:
        session = await _get_session(resolved)
    except Exception as e:
        logger.error(f'Failed to create session: {e}')
        return {'error': f'Failed to initialize agent: {str(e)}'}

    try:
        if resolved['stream']:
            return {
                'stream': True,
                'sessionKey': resolved['session_key'],
                'agentId': session.name,
                'message': 'Use /v1/chat/completions for streaming',
            }
        response = await session.chat(resolved['message'])
        resolved['runtime'].update_session_activity(resolved['session_key'])
        resolved['runtime'].increment_requests()
        return {
            'response': response,
            'sessionKey': resolved['session_key'],
            'agentId': session.name,
            'tools_available': session.available_tools,
            'provider': session.provider,
            'model': session.model,
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
        session = await _get_session(resolved)
    except Exception as e:
        return {'error': f'Failed to initialize agent: {str(e)}'}

    try:
        response = await session.chat(resolved['message'])
        resolved['runtime'].update_session_activity(resolved['session_key'])
        resolved['runtime'].increment_requests()
        return {
            'response': response,
            'sessionKey': resolved['session_key'],
            'agentId': session.name,
            'stream': False,
        }
    except Exception as e:
        logger.error(f'Stream error: {e}')
        resolved['runtime'].increment_errors()
        return {'error': str(e)}


async def handle_agent_tools(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    resolved = _resolve_params(params, context)

    try:
        session = await _get_session(resolved)
        return {
            'agentId': resolved['session_key'],
            'provider': session.provider,
            'model': session.model,
            'tools': session.available_tools,
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
        provider, model_name = model_param.split('/', 1)

    try:
        session = await _get_session({**resolved, 'provider': provider, 'model': model_name})
    except Exception as e:
        return {'error': f'Failed to initialize agent: {str(e)}'}

    last_message = next(
        (msg.get('content', '') for msg in reversed(messages) if msg.get('role') == 'user'),
        None
    )
    if not last_message:
        return {'error': 'No user message found'}

    try:
        response = await session.chat(last_message)
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
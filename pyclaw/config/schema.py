"""Configuration schema definitions using dataclasses."""

from typing import Optional, List, Dict, Any, Literal, TypeVar, Type, cast
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Field replacement - using dataclass field directly

T = TypeVar('T')


def validate_sessions(v: Any) -> Dict[str, Any]:
    """Validate and convert session config."""
    if isinstance(v, str):
        return {"store_path": v}
    return v


@dataclass
class ModelConfig:
    """AI Model configuration."""
    provider: str
    model: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    timeout: int = 60
    
    def __post_init__(self):
        # Basic validation
        if not self.provider:
            raise ValueError("provider is required")
        if not self.model:
            raise ValueError("model is required")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")


@dataclass
class ChannelConfig:
    """Channel (messaging platform) configuration."""
    enabled: bool = True
    credentials: Dict[str, str] = field(default_factory=dict)
    allow_from: List[str] = field(default_factory=list)
    dm_policy: Literal["pairing", "open", "closed"] = "pairing"
    webhook_url: Optional[str] = None


@dataclass
class ToolConfig:
    """Tool configuration."""
    enabled: bool = True
    ask: bool = True
    timeout: int = 60


@dataclass
class AgentConfig:
    """Agent (AI assistant) configuration."""
    name: str
    description: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    tools: List[str] = field(default_factory=list)
    sandbox: Optional[Dict[str, Any]] = None
    memory: bool = True
    max_iterations: int = 10
    
    def __post_init__(self):
        if not self.name:
            raise ValueError("name is required")


@dataclass
class GatewayHttpConfig:
    """Gateway HTTP server configuration."""
    enabled: bool = True
    port: int = 12321
    host: str = "127.0.0.1"
    cors_origins: List[str] = field(default_factory=list)


@dataclass
class GatewayWsConfig:
    """Gateway WebSocket configuration."""
    enabled: bool = True
    ping_interval: int = 30
    ping_timeout: int = 10


@dataclass
class GatewayConfig:
    """Gateway server configuration."""
    http: GatewayHttpConfig = field(default_factory=GatewayHttpConfig)
    websocket: GatewayWsConfig = field(default_factory=GatewayWsConfig)
    control_ui: Dict[str, Any] = field(default_factory=lambda: {"enabled": True})
    auth: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionConfig:
    """Session management configuration."""
    store_path: str = "~/.pyclaw/sessions"
    max_history: int = 100
    ttl_hours: Optional[int] = None


@dataclass
class SkillConfig:
    """Skill (plugin) configuration."""
    enabled: bool = True
    auto_enable: bool = False
    paths: List[str] = field(default_factory=list)


@dataclass
class PyClawConfig:
    """Main PyClaw configuration."""
    version: str = "1.0"
    
    # Gateway configuration
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    
    # Model configurations
    models: Dict[str, ModelConfig] = field(default_factory=dict)
    default_model: Optional[str] = None
    
    # Channel configurations
    channels: Dict[str, ChannelConfig] = field(default_factory=dict)
    
    # Agent configurations
    agents: Dict[str, AgentConfig] = field(default_factory=dict)
    default_agent: Optional[str] = "default"
    
    # Tool configurations
    tools: Dict[str, ToolConfig] = field(default_factory=dict)
    
    # Session configuration
    sessions: SessionConfig = field(default_factory=SessionConfig)
    
    # Skill configuration
    skills: SkillConfig = field(default_factory=SkillConfig)
    
    # Logging
    logging: Dict[str, Any] = field(default_factory=dict)
    
    def __init__(self, **kwargs):
        # Handle gateway configuration
        if 'gateway' in kwargs:
            gateway_data = kwargs['gateway']
            if isinstance(gateway_data, dict):
                # Handle nested http and websocket configs
                if 'http' in gateway_data and isinstance(gateway_data['http'], dict):
                    gateway_data['http'] = GatewayHttpConfig(**gateway_data['http'])
                if 'websocket' in gateway_data and isinstance(gateway_data['websocket'], dict):
                    gateway_data['websocket'] = GatewayWsConfig(**gateway_data['websocket'])
                kwargs['gateway'] = GatewayConfig(**gateway_data)
        
        # Handle sessions validation
        if 'sessions' in kwargs:
            sessions_data = validate_sessions(kwargs['sessions'])
            if isinstance(sessions_data, dict):
                kwargs['sessions'] = SessionConfig(**sessions_data)
        
        # Handle skills configuration
        if 'skills' in kwargs and isinstance(kwargs['skills'], dict):
            kwargs['skills'] = SkillConfig(**kwargs['skills'])
        
        # Handle models configuration
        if 'models' in kwargs and isinstance(kwargs['models'], dict):
            models_dict = {}
            for model_name, model_data in kwargs['models'].items():
                if isinstance(model_data, dict):
                    models_dict[model_name] = ModelConfig(**model_data)
            kwargs['models'] = models_dict
        
        # Handle channels configuration
        if 'channels' in kwargs and isinstance(kwargs['channels'], dict):
            channels_dict = {}
            for channel_name, channel_data in kwargs['channels'].items():
                if isinstance(channel_data, dict):
                    channels_dict[channel_name] = ChannelConfig(**channel_data)
            kwargs['channels'] = channels_dict
        
        # Handle agents configuration
        if 'agents' in kwargs and isinstance(kwargs['agents'], dict):
            agents_dict = {}
            for agent_name, agent_data in kwargs['agents'].items():
                if isinstance(agent_data, dict):
                    agents_dict[agent_name] = AgentConfig(**agent_data)
            kwargs['agents'] = agents_dict
        
        # Handle tools configuration
        if 'tools' in kwargs and isinstance(kwargs['tools'], dict):
            tools_dict = {}
            for tool_name, tool_data in kwargs['tools'].items():
                if isinstance(tool_data, dict):
                    tools_dict[tool_name] = ToolConfig(**tool_data)
            kwargs['tools'] = tools_dict
        
        # Initialize all fields
        for field_name, field_value in kwargs.items():
            setattr(self, field_name, field_value)
        
        # Set default values for missing fields
        if not hasattr(self, 'version'):
            self.version = "1.0"
        if not hasattr(self, 'gateway'):
            self.gateway = GatewayConfig()
        if not hasattr(self, 'models'):
            self.models = {}
        if not hasattr(self, 'default_model'):
            self.default_model = None
        if not hasattr(self, 'channels'):
            self.channels = {}
        if not hasattr(self, 'agents'):
            self.agents = {}
        if not hasattr(self, 'default_agent'):
            self.default_agent = "default"
        if not hasattr(self, 'tools'):
            self.tools = {}
        if not hasattr(self, 'sessions'):
            self.sessions = SessionConfig()
        if not hasattr(self, 'skills'):
            self.skills = SkillConfig()
        if not hasattr(self, 'logging'):
            self.logging = {}
    
    def get_model_config(self, model_ref: Optional[str] = None) -> Optional[ModelConfig]:
        """Get model configuration by reference."""
        ref = model_ref or self.default_model
        if not ref:
            return None
        return self.models.get(ref)
    
    def get_agent_config(self, agent_id: Optional[str] = None) -> Optional[AgentConfig]:
        """Get agent configuration by ID."""
        id = agent_id or self.default_agent
        if not id:
            return None
        return self.agents.get(id)
    
    def get_channel_config(self, channel_id: str) -> Optional[ChannelConfig]:
        """Get channel configuration by ID."""
        return self.channels.get(channel_id)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)
    
    @classmethod
    def model_validate(cls, data: Dict[str, Any]) -> 'PyClawConfig':
        """Validate and create config from dictionary."""
        return cls(**data)


@dataclass
class ConfigSnapshot:
    """Configuration snapshot with metadata."""
    config: PyClawConfig
    path: Path
    exists: bool
    valid: bool
    issues: List[str] = field(default_factory=list)
    legacy_issues: List[str] = field(default_factory=list)

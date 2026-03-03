"""Configuration schema definitions using Pydantic."""

from typing import Optional, List, Dict, Any, Literal
from dataclasses import dataclass, field
from pydantic import BaseModel, Field, field_validator, ConfigDict
from pathlib import Path


class ModelConfig(BaseModel):
    """AI Model configuration."""
    model_config = ConfigDict(extra="allow")
    
    provider: str = Field(..., description="Model provider (openai, anthropic, etc.)")
    model: str = Field(..., description="Model ID")
    api_key: Optional[str] = Field(None, description="API key for the provider")
    base_url: Optional[str] = Field(None, description="Custom API base URL")
    temperature: float = Field(0.7, ge=0, le=2, description="Sampling temperature")
    max_tokens: Optional[int] = Field(None, description="Maximum tokens to generate")
    timeout: int = Field(60, description="Request timeout in seconds")


class ChannelConfig(BaseModel):
    """Channel (messaging platform) configuration."""
    model_config = ConfigDict(extra="allow")
    
    enabled: bool = Field(True, description="Whether the channel is enabled")
    credentials: Dict[str, str] = Field(default_factory=dict, description="Channel credentials")
    allow_from: List[str] = Field(default_factory=list, description="Allowed sender IDs")
    dm_policy: Literal["pairing", "open", "closed"] = Field(
        "pairing", 
        description="Direct message policy"
    )
    webhook_url: Optional[str] = Field(None, description="Webhook URL for incoming messages")


class ToolConfig(BaseModel):
    """Tool configuration."""
    model_config = ConfigDict(extra="allow")
    
    enabled: bool = Field(True, description="Whether the tool is enabled")
    ask: bool = Field(True, description="Whether to ask for confirmation")
    timeout: int = Field(60, description="Tool execution timeout")


class AgentConfig(BaseModel):
    """Agent (AI assistant) configuration."""
    model_config = ConfigDict(extra="allow")
    
    name: str = Field(..., description="Agent display name")
    description: Optional[str] = Field(None, description="Agent description")
    model: Optional[str] = Field(None, description="Model reference")
    system_prompt: Optional[str] = Field(None, description="System prompt")
    tools: List[str] = Field(default_factory=list, description="Enabled tools")
    sandbox: Optional[Dict[str, Any]] = Field(None, description="Sandbox configuration")
    memory: bool = Field(True, description="Whether to use memory")
    max_iterations: int = Field(10, description="Maximum tool call iterations")


class GatewayHttpConfig(BaseModel):
    """Gateway HTTP server configuration."""
    enabled: bool = Field(True)
    port: int = Field(12321)
    host: str = Field("127.0.0.1")
    cors_origins: List[str] = Field(default_factory=list)


class GatewayWsConfig(BaseModel):
    """Gateway WebSocket configuration."""
    enabled: bool = Field(True)
    ping_interval: int = Field(30)
    ping_timeout: int = Field(10)


class GatewayConfig(BaseModel):
    """Gateway server configuration."""
    http: GatewayHttpConfig = Field(default_factory=GatewayHttpConfig)
    websocket: GatewayWsConfig = Field(default_factory=GatewayWsConfig)
    control_ui: Dict[str, Any] = Field(default_factory=lambda: {"enabled": True})
    auth: Dict[str, Any] = Field(default_factory=dict)
    

class SessionConfig(BaseModel):
    """Session management configuration."""
    store_path: str = Field("~/.pyclaw/sessions", description="Session storage path")
    max_history: int = Field(100, description="Maximum messages per session")
    ttl_hours: Optional[int] = Field(None, description="Session TTL in hours")


class SkillConfig(BaseModel):
    """Skill (plugin) configuration."""
    enabled: bool = Field(True)
    auto_enable: bool = Field(False, description="Auto-enable new skills")
    paths: List[str] = Field(default_factory=list, description="Skill search paths")


class PyClawConfig(BaseModel):
    """Main PyClaw configuration."""
    version: str = Field("1.0", description="Config version")
    
    # Gateway configuration
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    
    # Model configurations
    models: Dict[str, ModelConfig] = Field(default_factory=dict)
    default_model: Optional[str] = Field(None, description="Default model reference")
    
    # Channel configurations
    channels: Dict[str, ChannelConfig] = Field(default_factory=dict)
    
    # Agent configurations
    agents: Dict[str, AgentConfig] = Field(default_factory=dict)
    default_agent: Optional[str] = Field("default", description="Default agent ID")
    
    # Tool configurations
    tools: Dict[str, ToolConfig] = Field(default_factory=dict)
    
    # Session configuration
    sessions: SessionConfig = Field(default_factory=SessionConfig)
    
    # Skill configuration
    skills: SkillConfig = Field(default_factory=SkillConfig)
    
    # Logging
    logging: Dict[str, Any] = Field(default_factory=dict)
    
    @field_validator("sessions", mode="before")
    @classmethod
    def validate_sessions(cls, v):
        """Validate and convert session config."""
        if isinstance(v, str):
            return {"store_path": v}
        return v
    
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


@dataclass
class ConfigSnapshot:
    """Configuration snapshot with metadata."""
    config: PyClawConfig
    path: Path
    exists: bool
    valid: bool
    issues: List[str] = field(default_factory=list)
    legacy_issues: List[str] = field(default_factory=list)

"""Public configuration API shared by every Nixi component.

The implementation remains import-compatible with older ``app.server.config``
imports while new code can depend on this component-neutral module.
"""

from app.server.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_SYSTEM_PROMPT,
    USER_CONFIG_PATH,
    ActionConfig,
    LLMConfig,
    NixiConfig,
    ServerConfig,
    STTConfig,
    TTSConfig,
    VoiceConfig,
    default_config,
    load_config,
    resolve_config_path,
)

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_SYSTEM_PROMPT",
    "USER_CONFIG_PATH",
    "ActionConfig",
    "LLMConfig",
    "NixiConfig",
    "ServerConfig",
    "STTConfig",
    "TTSConfig",
    "VoiceConfig",
    "default_config",
    "load_config",
    "resolve_config_path",
]

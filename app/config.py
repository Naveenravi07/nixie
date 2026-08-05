"""Public configuration API shared by every Nixi component.

The implementation remains import-compatible with older ``app.server.config``
imports while new code can depend on this component-neutral module.
"""

from app.server.config import (
    DEFAULT_CONFIG_PATH,
    ActionConfig,
    LLMConfig,
    NixiConfig,
    ServerConfig,
    STTConfig,
    TTSConfig,
    VoiceConfig,
    load_config,
)

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ActionConfig",
    "LLMConfig",
    "NixiConfig",
    "ServerConfig",
    "STTConfig",
    "TTSConfig",
    "VoiceConfig",
    "load_config",
]

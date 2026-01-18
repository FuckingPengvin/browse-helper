from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
import yaml
import os


@dataclass
class BrowserConfig:
    type: str = "chromium"  # chromium, firefox, webkit
    headless: bool = False
    viewport_width: int = 1280
    viewport_height: int = 720
    timeout: int = 30000
    user_agent: Optional[str] = None
    proxy: Optional[str] = None
    downloads_path: str = "./downloads"


@dataclass
class OllamaConfig:
    model: str = "glm4"
    base_url: str = "http://localhost:11435"
    temperature: float = 0.1
    max_tokens: int = 2048
    context_window: int = 8192


@dataclass
class AgentConfig:
    planning_model: str = "glm4"
    max_plan_length: int = 10
    reflection_enabled: bool = True
    max_reflection_depth: int = 3
    default_goals: List[str] = field(default_factory=list)


@dataclass
class CoordinatorConfig:
    max_parallel_actions: int = 3
    action_timeout: int = 60000
    retry_attempts: int = 3
    retry_delay: float = 1.0
    enable_caching: bool = True


@dataclass
class LoggingConfig:
    level: str = "INFO"
    screenshot_on_error: bool = True
    save_html: bool = False
    log_dir: str = "./logs"
    max_log_files: int = 10


@dataclass
class TokenConfig:
    token_budget: Dict[str, Any] = field(default_factory=lambda: {
        "daily_limit": 100000,
        "hourly_limit": 20000,
        "per_request_limit": 4000,
        "reset_time": "00:00"
    })


@dataclass
class Config:
    browser: BrowserConfig
    ollama: OllamaConfig
    agent: AgentConfig
    coordinator: CoordinatorConfig
    logging: LoggingConfig
    tokens: TokenConfig

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Config':
        return cls(
            browser=BrowserConfig(**data.get('browser', {})),
            ollama=OllamaConfig(**data.get('ollama', {})),
            agent=AgentConfig(**data.get('agent', {})),
            coordinator=CoordinatorConfig(**data.get('coordinator', {})),
            logging=LoggingConfig(**data.get('logging', {})),
            tokens=TokenConfig(**data.get('tokens', {}))
        )


def load_config(config_path: str = "config.yaml") -> Config:
    if not os.path.exists(config_path):
        print(f"Конфигурационный файл {config_path} не найден, использую значения по умолчанию")
        return Config(
            browser=BrowserConfig(),
            ollama=OllamaConfig(),
            agent=AgentConfig(),
            coordinator=CoordinatorConfig(),
            logging=LoggingConfig(),
            tokens=TokenConfig()
        )

    with open(config_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    return Config.from_dict(data)
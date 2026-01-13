import asyncio
import sys
import signal
from pathlib import Path
from typing import Optional, Dict, Any
from rich.console import Console
from rich.panel import Panel
import yaml
import os

from rich.progress import Progress, SpinnerColumn, TextColumn

from agent_core import GLM4AgentCore

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
console = Console()


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    config_path_obj = Path(config_path)

    console.print(f"Загружаю конфиг из: {config_path_obj.absolute()}")

    if not config_path_obj.exists():
        console.print("Конфиг не найден, создаю дефолтный")

        default_config = {
            'model': {
                'name': 'glm4',
                'provider': 'ollama',
                'endpoint': 'http://localhost:11435',
                'temperature': 0.1,
                'max_tokens': 2000
            },
            'optimization': {
                'cache_enabled': True,
                'compress_context': True,
                'max_tokens_per_request': 1500
            },
            'browser': {
                'headless': False,
                'slow_mo': 800,
                'viewport': {'width': 1280, 'height': 720}
            },
            'tasks': {
                'max_retries': 3,
                'timeout': 300
            }
        }

        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.dump(default_config, f, default_flow_style=False)
            console.print(f"Создан дефолтный конфиг")
            return default_config
        except Exception as e:
            console.print(f"Ошибка создания конфига: {e}")
            return {}

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        if config is None:
            console.print("Конфиг пустой, использую дефолтные значения")
            config = {}

        if 'model' not in config:
            console.print("Добавляю секцию 'model'")
            config['model'] = {}

        if 'name' not in config['model']:
            config['model']['name'] = 'glm4'
            console.print("Устанавливаю model.name = 'glm4'")

        if 'endpoint' not in config['model']:
            config['model']['endpoint'] = 'http://localhost:11435'
            console.print(f"Устанавливаю endpoint = {config['model']['endpoint']}")

        console.print(f"Конфиг загружен")
        console.print(f"Модель: {config['model']['name']}")
        console.print(f"Endpoint: {config['model']['endpoint']}")

        return config

    except yaml.YAMLError as e:
        console.print(f"Ошибка парсинга YAML: {e}")
        return {}
    except Exception as e:
        console.print(f"Ошибка загрузки конфига: {e}")
        return {}


class GLM4AutonomousAgent:

    def __init__(self, config_path: str = "config.yaml"):
        self.config = load_config(config_path)
        self.core = None
        self.running = True

    async def initialize(self) -> bool:
        try:
            with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    transient=True,
            ) as progress:

                task1 = progress.add_task("Инициализирую GLM-4 ядро", total=None)

                try:
                    from agent_core import GLM4AgentCore
                except ImportError as e:
                    console.print(f"Не удалось импортировать agent_core: {e}")
                    console.print("Проверьте что файл agent_core.py существует")
                    return False

                self.core = GLM4AgentCore(self.config)

                if not await self.core.initialize():
                    console.print("Не удалось инициализировать GLM-4")
                    return False

                progress.update(task1, completed=True, description="GLM-4 готово")

        except Exception as e:
            console.print(f"Ошибка инициализации: {e}")
            import traceback
            traceback.print_exc()
            return False


async def main():
    try:
        import ollama
        import redis
        import playwright
    except ImportError as e:
        console.print(f"Отсутствуют зависимости: {e}")
        return

    agent = GLM4AutonomousAgent()

    if await agent.initialize():
        console.print("Успех")
    else:
        console.print("Не получилось")


if __name__ == "__main__":
    asyncio.run(main())

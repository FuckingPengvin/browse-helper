import asyncio
import signal
import sys
from typing import Dict, Any

from agent_core import AgentCore
from action_coordinator import ActionCoordinator
from browse_controle import BrowserController, create_browser_config
from config_loader import Config, load_config
from utils.visual_logger import VisualLogger
from utils.token_saver import TokenManager


class BrowserBot:

    def __init__(self, config_path: str = "config.yaml"):
        self.config: Config = load_config(config_path)
        self.is_running = False
        self.task_queue = asyncio.Queue()

        self.logger = VisualLogger(self._dataclass_to_dict(self.config.logging))
        self.token_manager = TokenManager(self._dataclass_to_dict(self.config.tokens))
        self.browser = BrowserController(self._dataclass_to_dict(self.config.browser))

        self.coordinator = ActionCoordinator(
            browser=self.browser,
            logger=self.logger,
            config=self._dataclass_to_dict(self.config.coordinator)
        )

        self.agent = AgentCore(
            coordinator=self.coordinator,
            token_manager=self.token_manager,
            logger=self.logger,
            config=self._dataclass_to_dict(self.config.agent)
        )

        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _dataclass_to_dict(self, dataclass_obj) -> Dict[str, Any]:
        """Преобразование dataclass в словарь"""
        if hasattr(dataclass_obj, '__dict__'):
            return dataclass_obj.__dict__.copy()
        elif hasattr(dataclass_obj, '_asdict'):
            return dataclass_obj._asdict()
        else:
            return dict(dataclass_obj)

    def _prepare_browser_config(self) -> dict:
        browser_config_dict = self.config.browser.__dict__.copy()

        if hasattr(browser_config_dict.get('browser_type'), 'value'):
            browser_config_dict['browser_type'] = browser_config_dict['browser_type'].value

        return create_browser_config(
            headless=browser_config_dict.get('headless', False),
            browser_type=browser_config_dict.get('browser_type', 'chromium'),
            viewport_size=(
                browser_config_dict.get('viewport_width', 1280),
                browser_config_dict.get('viewport_height', 720)
            ),
            proxy=browser_config_dict.get('proxy'),
            user_agent=browser_config_dict.get('user_agent')
        )

    def _handle_shutdown(self, signum, frame):
        print(f"\nПолучен сигнал {signum}, завершаю работу...")
        self.is_running = False

    async def initialize(self):
        self.logger.info("Инициализация браузерного бота...")

        try:
            await self.browser.initialize()
            await self.coordinator.initialize()
            await self.agent.initialize()

            self.logger.success("Все компоненты инициализированы")
            return True

        except Exception as e:
            self.logger.error(f"Ошибка инициализации: {e}")
            await self.shutdown()
            return False

    async def process_task(self, task_description: str):
        self.logger.info(f"Обрабатываю задачу: {task_description}")

        try:
            plan = await self.agent.analyze_and_plan(task_description)

            if not plan:
                self.logger.warning("Агент не смог создать план")
                return False

            result = await self.coordinator.execute_plan(plan)

            evaluation = await self.agent.evaluate_result(result)

            self.logger.info(f"Задача завершена. Оценка: {evaluation}")
            return result.get('success', False)

        except Exception as e:
            self.logger.error(f"Ошибка выполнения задачи: {e}")
            return False

    async def run_interactive(self):
        print("\n" + "=" * 50)
        print("АВТОНОМНЫЙ БРАУЗЕРНЫЙ БОТ")
        print("=" * 50)
        print("Команды:")
        print("  /task [описание] - выполнить задачу")
        print("  /screenshot - сделать скриншот")
        print("  /url [адрес] - перейти по URL")
        print("  /status - показать статус")
        print("  /quit - выйти")
        print("=" * 50)

        self.is_running = True

        while self.is_running:
            try:
                user_input = input("\nbot> ").strip()

                if not user_input:
                    continue

                if user_input.lower() in ['/quit', '/exit', 'quit', 'exit']:
                    break

                elif user_input.lower() == '/status':
                    await self._show_status()

                elif user_input.lower() == '/screenshot':
                    path = await self.browser.take_screenshot("manual_screenshot")
                    self.logger.info(f"Скриншот сохранен: {path}")

                elif user_input.startswith('/url '):
                    url = user_input[5:].strip()
                    await self.browser.navigate_to(url)

                elif user_input.startswith('/task '):
                    task = user_input[6:].strip()
                    await self.process_task(task)

                elif user_input.startswith('/'):
                    print(f"Неизвестная команда: {user_input}")

                else:
                    await self.process_task(user_input)

            except KeyboardInterrupt:
                print("\nПрервано пользователем")
                break
            except Exception as e:
                self.logger.error(f"Ошибка: {e}")

    async def run_batch(self, tasks_file: str):
        try:
            with open(tasks_file, 'r', encoding='utf-8') as f:
                tasks = [line.strip() for line in f if line.strip()]

            self.logger.info(f"Найдено {len(tasks)} задач для выполнения")

            for i, task in enumerate(tasks, 1):
                self.logger.info(f"Задача {i}/{len(tasks)}: {task[:50]}...")
                success = await self.process_task(task)

                if not success:
                    self.logger.warning(f"Задача {i} завершилась с ошибкой")

                if i < len(tasks):
                    await asyncio.sleep(2)

        except FileNotFoundError:
            self.logger.error(f"Файл не найден: {tasks_file}")

    async def _show_status(self):
        browser_stats = await self.browser.get_statistics_async()
        coordinator_stats = self.coordinator.get_statistics()
        token_stats = self.token_manager.get_statistics()

        status = {
            "Браузер": "Активен" if self.browser.is_active else "Неактивен",
            "Агент": "Готов" if self.agent.is_ready else "Не готов",
            "Координатор": "Готов" if self.coordinator.is_ready else "Не готов",
            "Токены использовано": token_stats.get('total_tokens_used', 0),
            "Текущая страница": browser_stats.get('current_url', ''),
            "Активных действий": coordinator_stats.get('active_actions_count', 0)
        }

        print("\n" + "=" * 50)
        print("СТАТУС СИСТЕМЫ")
        print("=" * 50)
        for key, value in status.items():
            print(f"{key:25}: {value}")
        print("=" * 50)

    async def shutdown(self):
        self.logger.info("Завершение работы...")
        self.is_running = False

        if hasattr(self, 'coordinator'):
            await self.coordinator.shutdown()

        if hasattr(self, 'agent'):
            await self.agent.shutdown()

        if hasattr(self, 'token_manager'):
            await self.token_manager.shutdown()

        if hasattr(self, 'browser'):
            await self.browser.close()

        self.logger.info("Бот завершил работу")


async def main():
    import argparse

    parser = argparse.ArgumentParser(description='Автономный браузерный бот')
    parser.add_argument('--config', default='config.yaml', help='Путь к конфигурации')
    parser.add_argument('--task', help='Выполнить одну задачу')
    parser.add_argument('--batch', help='Файл с задачами для пакетного выполнения')
    parser.add_argument('--headless', action='store_true', help='Запуск без GUI браузера')

    args = parser.parse_args()

    bot = BrowserBot(config_path=args.config)

    if args.headless:
        bot.config.browser.headless = True

    try:
        if not await bot.initialize():
            print("Не удалось инициализировать бота")
            return 1

        if args.task:
            await bot.process_task(args.task)

        elif args.batch:
            await bot.run_batch(args.batch)

        else:
            await bot.run_interactive()

    except KeyboardInterrupt:
        print("\nРабота прервана пользователем")

    except Exception as e:
        print(f"Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        await bot.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
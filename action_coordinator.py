import asyncio
import time
from dataclasses import dataclass
from typing import Dict, Any, Optional
from enum import Enum
import uuid


class ActionStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


@dataclass
class ExecutionResult:
    action_id: str
    status: ActionStatus
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration: float = 0.0
    retry_count: int = 0


class ActionCoordinator:

    def __init__(
            self,
            browser,  # BrowserController
            logger,  # VisualLogger
            config: Dict[str, Any]
    ):
        self.browser = browser
        self.logger = logger
        self.config = config

        self.is_ready = False
        self.active = False

        self.action_handlers = {
            "navigate": self._execute_navigate,
            "click": self._execute_click,
            "input_text": self._execute_input_text,
            "extract_data": self._execute_extract_data,
            "wait": self._execute_wait,
            "scroll": self._execute_scroll,
            "execute_script": self._execute_script,
        }

        self.semaphore = asyncio.Semaphore(
            config.get('max_parallel_actions', 2)
        )

        self.stats = {
            "actions_executed": 0,
            "actions_failed": 0,
            "total_retries": 0,
            "total_duration": 0.0
        }

    async def initialize(self):
        self.logger.info("Инициализация координатора действий...")

        if not await self.browser.is_available():
            raise RuntimeError("Браузер не доступен")

        self.is_ready = True
        self.active = True
        self.logger.success("Координатор действий инициализирован")

    async def execute_plan(self, plan) -> Dict[str, Any]:
        if not self.is_ready:
            raise RuntimeError("Координатор не инициализирован")

        self.logger.info(f"Начинаю выполнение плана: {plan.task[:80]}...")

        execution_id = f"exec_{uuid.uuid4().hex[:6]}"
        start_time = time.time()
        results = []

        try:
            for i, action in enumerate(plan.actions):
                self.logger.info(f"[{execution_id}] Шаг {i + 1}/{len(plan.actions)}: {action.description}")

                result = await self._execute_action_with_retry(
                    action=action,
                    step_index=i,
                    execution_id=execution_id
                )

                results.append(result)

                if (result.status == ActionStatus.FAILED and
                        not action.retry_on_fail):
                    self.logger.warning("Критическая ошибка, прекращаю выполнение плана")
                    break

                if i < len(plan.actions) - 1:
                    await asyncio.sleep(0.5)

            success = all(r.status == ActionStatus.COMPLETED for r in results)
            duration = time.time() - start_time

            self.stats["actions_executed"] += len(results)
            self.stats["actions_failed"] += sum(1 for r in results if r.status == ActionStatus.FAILED)
            self.stats["total_duration"] += duration

            return {
                "execution_id": execution_id,
                "success": success,
                "total_actions": len(results),
                "successful_actions": sum(1 for r in results if r.status == ActionStatus.COMPLETED),
                "failed_actions": sum(1 for r in results if r.status == ActionStatus.FAILED),
                "duration": duration,
                "results": [self._result_to_dict(r) for r in results],
                "task": plan.task
            }

        except Exception as e:
            self.logger.error(f"Ошибка выполнения плана {execution_id}: {e}")
            return {
                "execution_id": execution_id,
                "success": False,
                "error": str(e),
                "duration": time.time() - start_time,
                "results": [self._result_to_dict(r) for r in results]
            }

    async def _execute_action_with_retry(
            self,
            action,
            step_index: int,
            execution_id: str
    ) -> ExecutionResult:
        max_retries = self.config.get('retry_attempts', 3)
        retry_delay = self.config.get('retry_delay', 1.0)

        if hasattr(action.type, 'value'):
            action_type = action.type.value
        else:
            action_type = str(action.type)

        handler = self.action_handlers.get(action_type)
        if not handler:
            return ExecutionResult(
                action_id=f"step_{step_index}",
                status=ActionStatus.FAILED,
                error=f"Неизвестный тип действия: {action.type.value}"
            )

        start_time = 0.0

        for attempt in range(max_retries + 1):
            try:
                start_time = time.time()

                async with self.semaphore:
                    result_data = await handler(action)

                duration = time.time() - start_time

                return ExecutionResult(
                    action_id=f"step_{step_index}",
                    status=ActionStatus.COMPLETED,
                    data=result_data,
                    duration=duration,
                    retry_count=attempt
                )

            except Exception as e:
                error_msg = str(e)
                self.logger.warning(
                    f"[{execution_id}] Шаг {step_index}, попытка {attempt + 1}/{max_retries + 1}: {error_msg}"
                )

                self.stats["total_retries"] += 1

                if attempt < max_retries:
                    await asyncio.sleep(retry_delay * (2 ** attempt))
                else:
                    return ExecutionResult(
                        action_id=f"step_{step_index}",
                        status=ActionStatus.FAILED,
                        error=error_msg,
                        duration=time.time() - start_time,
                        retry_count=attempt
                    )

    # ========== БАЗОВЫЕ ОБРАБОТЧИКИ ДЕЙСТВИЙ ==========

    async def _execute_navigate(self, action) -> Dict[str, Any]:
        url = action.target

        if url and not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        self.logger.info(f"Навигация: {url}")
        await self.browser.navigate_to(url)

        await asyncio.sleep(2)  # Время на загрузку страницы

        current_url = await self.browser.get_current_url()
        title = await self.browser.get_page_title()

        return {
            "requested_url": action.target,
            "actual_url": current_url,
            "page_title": title,
            "action": "navigation"
        }

    async def _execute_click(self, action) -> Dict[str, Any]:
        selector = action.target

        self.logger.info(f"Клик: {selector}")

        element = await self.browser.find_element(selector)
        if not element:
            raise ValueError(f"Элемент не найден: {selector}")

        await self.browser.click_element(element)

        await asyncio.sleep(0.5)

        return {
            "selector": selector,
            "action": "click",
            "success": True
        }

    async def _execute_input_text(self, action) -> Dict[str, Any]:
        selector = action.target
        text = action.value

        if not text:
            raise ValueError("Не указан текст для ввода")

        self.logger.info(f"Ввод текста в {selector}: {text[:50]}...")

        element = await self.browser.find_element(selector)
        if not element:
            raise ValueError(f"Элемент ввода не найден: {selector}")

        await self.browser.clear_element(element)

        await self.browser.input_text(element, text)

        return {
            "selector": selector,
            "text_length": len(text),
            "action": "input_text",
            "success": True
        }

    async def _execute_extract_data(self, action) -> Dict[str, Any]:
        selector = action.target
        attribute = action.value or "text"

        self.logger.info(f"Извлечение данных: {selector}.{attribute}")

        data = await self.browser.extract_data(selector, attribute)

        return {
            "selector": selector,
            "attribute": attribute,
            "data": data,
            "action": "extract_data",
            "success": True
        }

    async def _execute_wait(self, action) -> Dict[str, Any]:
        wait_target = action.target or action.value

        if isinstance(wait_target, (int, float)):
            seconds = float(wait_target)
            self.logger.info(f"Ожидание {seconds} секунд")
            await asyncio.sleep(seconds)

            return {
                "wait_type": "time",
                "seconds": seconds,
                "action": "wait"
            }
        else:
            selector = str(wait_target)
            self.logger.info(f"Ожидание элемента: {selector}")

            timeout = action.timeout or 10000
            element = await self.browser.wait_for_element(selector, timeout)

            return {
                "wait_type": "element",
                "selector": selector,
                "found": element is not None,
                "timeout": timeout,
                "action": "wait"
            }

    async def _execute_scroll(self, action) -> Dict[str, Any]:
        direction = action.value or "down"
        amount = action.target or "500"

        self.logger.info(f"Прокрутка: {direction} на {amount}px")

        await self.browser.scroll_page(direction, int(amount))

        return {
            "direction": direction,
            "amount": amount,
            "action": "scroll"
        }

    async def _execute_script(self, action) -> Dict[str, Any]:
        script = action.target or action.value

        if not script:
            raise ValueError("Не указан JavaScript для выполнения")

        self.logger.info(f"Выполнение JavaScript: {script[:100]}...")

        result = await self.browser.execute_script(script)

        return {
            "script_length": len(script),
            "result": str(result)[:200],
            "action": "execute_script",
            "success": True
        }

    # ========== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ==========

    def _result_to_dict(self, result: ExecutionResult) -> Dict[str, Any]:
        return {
            "action_id": result.action_id,
            "status": result.status.value,
            "duration": round(result.duration, 2),
            "retry_count": result.retry_count,
            "error": result.error,
            "data": result.data
        }

    async def validate_environment(self) -> bool:
        checks = [
            ("Браузер доступен", await self.browser.is_available()),
            ("Страница загружена", await self.browser.is_page_loaded()),
        ]

        all_ok = True
        for check_name, check_result in checks:
            if check_result:
                self.logger.info(f"✓ {check_name}")
            else:
                self.logger.error(f"✗ {check_name}")
                all_ok = False

        return all_ok

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_ready": self.is_ready,
            "is_active": self.active,
            "stats": self.stats.copy(),
            "max_parallel": self.semaphore._value,
            "available_actions": list(self.action_handlers.keys())
        }

    async def shutdown(self):
        self.logger.info("Завершение работы координатора...")

        self.is_ready = False
        self.active = False

        self.logger.info("Координатор завершил работу")
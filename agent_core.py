import asyncio
import json
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum
import aiohttp
from pydantic import BaseModel


class ActionType(Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    INPUT_TEXT = "input_text"
    EXTRACT_DATA = "extract_data"
    WAIT = "wait"
    SCROLL = "scroll"
    DECISION = "decision"
    CONDITION = "condition"
    LOOP = "loop"
    EXECUTE_SCRIPT = "execute_script"


@dataclass
class Action:
    type: ActionType
    target: Optional[str] = None
    value: Optional[Any] = None
    description: str = ""
    conditions: List[str] = field(default_factory=list)
    retry_on_fail: bool = True
    timeout: int = 30000

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value,
            "target": self.target,
            "value": self.value,
            "description": self.description,
            "conditions": self.conditions
        }


@dataclass
class Plan:
    task: str
    actions: List[Action]
    expected_outcome: str
    assumptions: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)

    def add_action(self, action: Action):
        self.actions.append(action)

    def insert_action(self, index: int, action: Action):
        self.actions.insert(index, action)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "actions": [a.to_dict() for a in self.actions],
            "expected_outcome": self.expected_outcome,
            "assumptions": self.assumptions,
            "constraints": self.constraints
        }


@dataclass
class Reflection:
    what_happened: str
    why_it_happened: str
    what_to_change: str
    confidence: float  # 0.0 - 1.0


class AgentState(BaseModel):
    current_goal: Optional[str] = None
    memory: List[Dict[str, Any]] = []
    insights: List[str] = []
    total_tokens_used: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0

    def add_memory(self, event: str, data: Dict[str, Any]):
        self.memory.append({
            "event": event,
            "data": data,
            "timestamp": asyncio.get_event_loop().time()
        })
        if len(self.memory) > 100:
            self.memory = self.memory[-50:]


class AgentCore:

    def __init__(
            self,
            coordinator,  # ActionCoordinator
            token_manager,  # TokenManager
            logger,  # VisualLogger
            config: Dict[str, Any]
    ):
        self.coordinator = coordinator
        self.token_manager = token_manager
        self.logger = logger
        self.config = config

        self.state = AgentState()
        self.is_ready = False
        self.thinking_lock = asyncio.Lock()

        self.prompts = self._load_prompts()

        self.http_session: Optional[aiohttp.ClientSession] = None

    async def initialize(self):
        self.logger.info("Инициализация ядра агента...")

        self.http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )

        await self._load_initial_knowledge()

        self.is_ready = True
        self.logger.success("Ядро агента инициализировано")

    def _load_prompts(self) -> Dict[str, str]:
        return {
            "planning": """Ты - автономный браузерный агент. Твоя задача - создать план действий для выполнения задачи.

ЗАДАЧА: {task}

ИСТОРИЯ: {history}

ДОСТУПНЫЕ ДЕЙСТВИЯ:
1. navigate(url) - перейти по URL
2. click(selector) - кликнуть по элементу
3. input_text(selector, text) - ввести текст
4. extract_data(selector, attribute) - извлечь данные
5. wait(seconds_or_selector) - ждать время или появление элемента
6. scroll(direction) - прокрутить страницу
7. execute_script(code) - выполнить JavaScript
8. condition(check, if_true, if_false) - условное выполнение
9. loop(while_condition, actions) - цикл

ИНСТРУКЦИИ:
- Будь максимально конкретным в описании целей и селекторов
- Учитывай возможные ошибки и добавь проверки
- Разбивай сложные задачи на простые шаги
- Предусмотри альтернативные пути если что-то пойдет не так

Верни ответ в формате JSON:
{{
    "plan": [
        {{
            "action": "тип_действия",
            "target": "селектор_или_url",
            "value": "значение_если_нужно",
            "description": "ясное_описание_шага",
            "conditions": ["условия_выполнения"]
        }}
    ],
    "expected_outcome": "что_ожидаем_получить",
    "assumptions": ["предположения_о_странице"],
    "constraints": ["ограничения_времени_или_ресурсов"]
}}""",

            "reflection": """Проанализируй результат выполнения плана.

ИСХОДНАЯ ЗАДАЧА: {task}

ПЛАН: {plan}

РЕЗУЛЬТАТ: {result}

Что сработало хорошо? Что не сработало? Как можно улучшить план в будущем?
Верни ответ в формате JSON:
{{
    "analysis": "анализ_того_что_произошло",
    "lessons": ["извлеченные_уроки"],
    "improvements": ["предложения_по_улучшению"],
    "confidence": 0.95
}}""",

            "decision": """Прими решение на основе текущей ситуации.

КОНТЕКСТ: {context}

ВОПРОС: {question}

ВАРИАНТЫ: {options}

Проанализируй варианты и выбери лучший. Объясни почему.
Верни ответ в формате JSON:
{{
    "decision": "выбранный_вариант",
    "reasoning": "обоснование_выбора",
    "confidence": 0.95
}}"""
        }

    async def _load_initial_knowledge(self):
        try:
            initial_knowledge = [
                "Веб-страницы могут загружаться с задержкой",
                "Элементы могут изменять селекторы",
                "Нужно проверять успешность каждого действия",
                "Использовать data-атрибуты для более стабильных селекторов"
            ]

            for knowledge in initial_knowledge:
                self.state.insights.append(knowledge)

        except Exception as e:
            self.logger.warning(f"Не удалось загрузить начальные знания: {e}")

    async def _call_llm(self, prompt: str, temperature: float = None) -> Dict[str, Any]:
        if temperature is None:
            temperature = self.config.get('temperature', 0.1)

        payload = {
            "model": self.config.get('model', 'glm4'),
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": self.config.get('max_tokens', 2048)
            }
        }

        try:
            if not self.http_session:
                self.http_session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=30)
                )

            self.logger.debug(f"Вызов LLM: {self.config.get('model')}, токены: {self.config.get('max_tokens')}")

            async with self.http_session.post(
                    f"{self.config.get('base_url', 'http://localhost:11434')}/api/generate",
                    json=payload
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    self.logger.error(f"Ошибка LLM API: {response.status} - {error_text}")
                    return {"error": f"API error: {response.status}"}

                result = await response.json()

                tokens_used = result.get('eval_count', 0)
                self.token_manager.add_usage(tokens_used, 0, self.config.get('model', 'glm4'), "planning")
                self.state.total_tokens_used += tokens_used

                response_text = result.get('response', '')
                self.logger.debug(f"LLM ответ получен: {len(response_text)} символов")

                try:
                    json_start = response_text.find('{')
                    json_end = response_text.rfind('}') + 1

                    if json_start != -1 and json_end != 0:
                        json_str = response_text[json_start:json_end]
                        parsed = json.loads(json_str)
                        self.logger.debug("JSON успешно распарсен")
                        return parsed
                    else:
                        self.logger.warning("LLM не вернул JSON, возвращаю текст")
                        return {"text": response_text.strip()}

                except json.JSONDecodeError as e:
                    self.logger.warning(f"Не удалось распарсить JSON: {e}")
                    self.logger.debug(f"Ответ LLM: {response_text[:200]}...")
                    return {"error": "invalid_json", "raw_response": response_text}

        except asyncio.TimeoutError:
            self.logger.error("Таймаут при вызове LLM")
            return {"error": "timeout"}

        except Exception as e:
            self.logger.error(f"Ошибка при вызове LLM: {e}")
            import traceback
            self.logger.error(f"Трассировка LLM: {traceback.format_exc()}")
            return {"error": str(e)}

    async def analyze_and_plan(self, task: str) -> Optional[Plan]:
        async with self.thinking_lock:
            self.logger.info(f"Анализирую задачу: {task[:50]}...")

            self.state.current_goal = task

            context = {
                "task": task,
                "history": self._get_relevant_history(task),
                "insights": self.state.insights[-5:]
            }

            prompt = self.prompts["planning"].format(**context)

            response = await self._call_llm(prompt)

            if "error" in response:
                self.logger.error(f"Ошибка при планировании: {response['error']}")
                return None

            try:
                plan_data = response

                plan = Plan(
                    task=task,
                    actions=[],
                    expected_outcome=plan_data.get('expected_outcome', ''),
                    assumptions=plan_data.get('assumptions', []),
                    constraints=plan_data.get('constraints', [])
                )

                for action_data in plan_data.get('plan', []):
                    try:
                        action_type = ActionType(action_data['action'])
                        action = Action(
                            type=action_type,
                            target=action_data.get('target'),
                            value=action_data.get('value'),
                            description=action_data.get('description', ''),
                            conditions=action_data.get('conditions', [])
                        )
                        plan.add_action(action)
                    except (KeyError, ValueError) as e:
                        self.logger.warning(f"Пропускаю некорректное действие: {e}")
                        continue

                self.state.add_memory("plan_created", {
                    "task": task,
                    "plan_length": len(plan.actions),
                    "assumptions": plan.assumptions
                })

                self.logger.info(f"Создан план из {len(plan.actions)} действий")
                return plan

            except Exception as e:
                self.logger.error(f"Ошибка при создании плана: {e}")
                return await self._create_fallback_plan(task)

    async def _create_fallback_plan(self, task: str) -> Plan:
        self.logger.warning("Использую fallback-планирование")

        plan = Plan(
            task=task,
            actions=[],
            expected_outcome="Выполнение задачи",
            assumptions=["Используется упрощенный план"],
            constraints=["Автоматическое планирование недоступно"]
        )

        task_lower = task.lower()

        if "перейди" in task_lower or "открой" in task_lower or "url" in task_lower:
            import re
            urls = re.findall(r'https?://\S+', task)
            if urls:
                plan.add_action(Action(
                    type=ActionType.NAVIGATE,
                    target=urls[0],
                    description=f"Переход по URL: {urls[0]}"
                ))

        elif "кликни" in task_lower or "нажми" in task_lower:
            plan.add_action(Action(
                type=ActionType.CLICK,
                target="button, a, input[type='submit']",
                description="Клик по элементу"
            ))

        elif "введи" in task_lower or "напиши" in task_lower:
            import re
            text_match = re.search(r'["\']([^"\']+)["\']', task)
            text = text_match.group(1) if text_match else "текст"

            plan.add_action(Action(
                type=ActionType.INPUT_TEXT,
                target="input, textarea",
                value=text,
                description=f"Ввод текста: {text}"
            ))

        plan.add_action(Action(
            type=ActionType.WAIT,
            target="body",
            value=2,
            description="Ожидание загрузки"
        ))

        return plan

    async def evaluate_result(self, execution_result: Dict[str, Any]) -> Reflection:
        self.logger.info("Провожу рефлексию над результатом...")

        if not self.config.get('reflection_enabled', True):
            return Reflection(
                what_happened="Рефлексия отключена",
                why_it_happened="Конфигурация",
                what_to_change="",
                confidence=1.0
            )

        context = {
            "task": self.state.current_goal or "Неизвестная задача",
            "plan": json.dumps(execution_result.get('plan', {}), ensure_ascii=False),
            "result": json.dumps(execution_result, ensure_ascii=False)
        }

        prompt = self.prompts["reflection"].format(**context)
        response = await self._call_llm(prompt, temperature=0.3)

        try:
            if "analysis" in response:
                reflection = Reflection(
                    what_happened=response.get("analysis", ""),
                    why_it_happened="\n".join(response.get("lessons", [])),
                    what_to_change="\n".join(response.get("improvements", [])),
                    confidence=response.get("confidence", 0.5)
                )
            else:
                reflection = Reflection(
                    what_happened="Выполнено без детального анализа",
                    why_it_happened="",
                    what_to_change="",
                    confidence=0.5
                )

            if execution_result.get('success'):
                self.state.successful_tasks += 1
            else:
                self.state.failed_tasks += 1

            if reflection.what_to_change:
                self.state.insights.append(reflection.what_to_change)

            self.state.add_memory("reflection", {
                "task": context["task"],
                "success": execution_result.get('success', False),
                "confidence": reflection.confidence
            })

            return reflection

        except Exception as e:
            self.logger.error(f"Ошибка при рефлексии: {e}")
            return Reflection(
                what_happened=f"Ошибка анализа: {e}",
                why_it_happened="",
                what_to_change="",
                confidence=0.0
            )

    async def make_decision(
            self,
            context: str,
            question: str,
            options: List[str]
    ) -> Dict[str, Any]:
        self.logger.info(f"Принимаю решение: {question[:50]}...")

        decision_context = {
            "context": context,
            "question": question,
            "options": json.dumps(options, ensure_ascii=False)
        }

        prompt = self.prompts["decision"].format(**decision_context)
        response = await self._call_llm(prompt, temperature=0.2)

        self.state.add_memory("decision", {
            "question": question,
            "options": options,
            "response": response
        })

        return response

    def _get_relevant_history(self, current_task: str, limit: int = 3) -> str:
        if not self.state.memory:
            return "История отсутствует"

        relevant = []
        for memory in self.state.memory[-limit:]:
            event = memory.get('event', '')
            data = memory.get('data', {})

            if event == "plan_created":
                relevant.append(f"План создан для: {data.get('task', '')}")
            elif event == "reflection":
                relevant.append(f"Рефлексия: успех={data.get('success')}")

        return "\n".join(relevant) if relevant else "Нет релевантной истории"

    async def adjust_plan(
            self,
            current_plan: Plan,
            feedback: Dict[str, Any]
    ) -> Plan:
        self.logger.info("Корректирую план на основе обратной связи...")

        adjusted_plan = Plan(
            task=current_plan.task,
            actions=current_plan.actions.copy(),
            expected_outcome=current_plan.expected_outcome,
            assumptions=current_plan.assumptions,
            constraints=current_plan.constraints
        )

        if feedback.get('errors'):
            for error in feedback['errors']:
                error_step = error.get('step', -1)
                if 0 <= error_step < len(adjusted_plan.actions):
                    check_action = Action(
                        type=ActionType.WAIT,
                        target="body",
                        value=1,
                        description="Проверка после ошибки"
                    )
                    adjusted_plan.insert_action(error_step + 1, check_action)

        return adjusted_plan

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_ready": self.is_ready,
            "current_goal": self.state.current_goal,
            "memory_size": len(self.state.memory),
            "insights_count": len(self.state.insights),
            "tokens_used": self.state.total_tokens_used,
            "successful_tasks": self.state.successful_tasks,
            "failed_tasks": self.state.failed_tasks,
            "success_rate": (
                    self.state.successful_tasks /
                    max(self.state.successful_tasks + self.state.failed_tasks, 1)
            )
        }

    async def shutdown(self):
        self.logger.info("Завершение работы ядра агента...")

        self.is_ready = False

        if self.http_session:
            await self.http_session.close()

        await self._save_state()

        self.logger.info("Ядро агента завершило работу")

    async def _save_state(self):
        # Реализовать сохранение состояния в файл в дальнейшем
        pass


# Вспомогательные функции
def action_from_dict(data: Dict[str, Any]) -> Action:
    try:
        return Action(
            type=ActionType(data['type']),
            target=data.get('target'),
            value=data.get('value'),
            description=data.get('description', ''),
            conditions=data.get('conditions', [])
        )
    except (KeyError, ValueError) as e:
        raise ValueError(f"Неверный формат действия: {e}")
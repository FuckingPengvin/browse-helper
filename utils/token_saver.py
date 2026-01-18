import time
import json
from pathlib import Path
from typing import Dict, Any, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from collections import deque
import threading
import asyncio


class TokenLimitExceeded(Exception):
    pass


@dataclass
class TokenUsage:
    timestamp: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str
    operation: str  # 'planning', 'reflection', 'decision'


@dataclass
class TokenBudget:
    daily_limit: int = 100000  # Лимит на день
    hourly_limit: int = 20000  # Лимит на час
    per_request_limit: int = 4000  # Лимит на запрос
    reset_time: str = "00:00"  # Время сброса дневного лимита (формат "HH:MM")


class TokenManager:

    def __init__(
            self,
            config: Dict[str, Any],
            data_dir: str = "./data"
    ):
        self.config = config
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.budget = TokenBudget(**config.get('token_budget', {}))

        self.usage_history: deque[TokenUsage] = deque(maxlen=1000)
        self.lock = threading.Lock()

        self.stats = {
            'total_requests': 0,
            'total_tokens_used': 0,
            'total_prompt_tokens': 0,
            'total_completion_tokens': 0,
            'limit_exceeded_count': 0,
            'last_reset_time': time.time()
        }

        self._load_history()

        self._running = True
        self._save_task = asyncio.create_task(self._periodic_save())

        print(f"TokenManager инициализирован. Дневной лимит: {self.budget.daily_limit} токенов")

    def _load_history(self):
        history_file = self.data_dir / 'token_history.json'

        if history_file.exists():
            try:
                with open(history_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                for item in data.get('history', []):
                    usage = TokenUsage(**item)
                    self.usage_history.append(usage)

                self.stats.update(data.get('stats', {}))

                print(f"Загружена история использования: {len(self.usage_history)} записей")

            except Exception as e:
                print(f"Ошибка загрузки истории токенов: {e}")

    async def _periodic_save(self):
        while self._running:
            await asyncio.sleep(300)
            self._save_history()

    def _save_history(self):
        with self.lock:
            history_file = self.data_dir / 'token_history.json'

            data = {
                'saved_at': datetime.now().isoformat(),
                'history': [asdict(usage) for usage in self.usage_history],
                'stats': self.stats,
                'budget': asdict(self.budget)
            }

            try:
                with open(history_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"Ошибка сохранения истории токенов: {e}")

    def _check_limits(self, prompt_tokens: int, completion_tokens: int) -> Tuple[bool, str]:
        total_tokens = prompt_tokens + completion_tokens

        if total_tokens > self.budget.per_request_limit:
            return False, f"Превышен лимит на запрос: {total_tokens} > {self.budget.per_request_limit}"

        now = time.time()

        hour_ago = now - 3600
        hourly_usage = sum(
            u.total_tokens for u in self.usage_history
            if u.timestamp > hour_ago
        )

        if hourly_usage + total_tokens > self.budget.hourly_limit:
            remaining = self.budget.hourly_limit - hourly_usage
            return False, f"Превышен часовой лимит. Доступно: {remaining} токенов"

        reset_hour, reset_minute = map(int, self.budget.reset_time.split(':'))
        now_dt = datetime.now()
        reset_dt = datetime(now_dt.year, now_dt.month, now_dt.day, reset_hour, reset_minute)

        if now_dt > reset_dt:
            reset_dt += timedelta(days=1)

        reset_time = reset_dt.timestamp()

        if now > self.stats['last_reset_time'] and now >= reset_time:
            self.stats['last_reset_time'] = now

        daily_usage = sum(
            u.total_tokens for u in self.usage_history
            if u.timestamp > self.stats['last_reset_time']
        )

        if daily_usage + total_tokens > self.budget.daily_limit:
            remaining = self.budget.daily_limit - daily_usage
            return False, f"Превышен дневной лимит. Доступно: {remaining} токенов"

        return True, "Лимиты в порядке"

    def add_usage(
            self,
            prompt_tokens: int,
            completion_tokens: int,
            model: str,
            operation: str = "unknown"
    ):
        with self.lock:
            total_tokens = prompt_tokens + completion_tokens

            allowed, message = self._check_limits(prompt_tokens, completion_tokens)

            if not allowed:
                self.stats['limit_exceeded_count'] += 1
                raise TokenLimitExceeded(message)

            usage = TokenUsage(
                timestamp=time.time(),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                model=model,
                operation=operation
            )

            self.usage_history.append(usage)

            self.stats['total_requests'] += 1
            self.stats['total_tokens_used'] += total_tokens
            self.stats['total_prompt_tokens'] += prompt_tokens
            self.stats['total_completion_tokens'] += completion_tokens

            return usage

    def get_current_usage(self, period: str = "day") -> Dict[str, Any]:
        now = time.time()

        if period == "hour":
            start_time = now - 3600
        elif period == "day":
            start_time = now - 86400
        elif period == "week":
            start_time = now - 604800
        else:
            start_time = self.stats['last_reset_time']

        relevant_usage = [
            u for u in self.usage_history
            if u.timestamp > start_time
        ]

        if not relevant_usage:
            return {
                'total_tokens': 0,
                'prompt_tokens': 0,
                'completion_tokens': 0,
                'request_count': 0,
                'average_per_request': 0
            }

        total_tokens = sum(u.total_tokens for u in relevant_usage)
        prompt_tokens = sum(u.prompt_tokens for u in relevant_usage)
        completion_tokens = sum(u.completion_tokens for u in relevant_usage)

        return {
            'total_tokens': total_tokens,
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion_tokens,
            'request_count': len(relevant_usage),
            'average_per_request': total_tokens / len(relevant_usage),
            'period': period
        }

    def get_remaining_budget(self) -> Dict[str, Any]:
        daily_usage = self.get_current_usage("day")
        hourly_usage = self.get_current_usage("hour")

        return {
            'daily': {
                'used': daily_usage['total_tokens'],
                'limit': self.budget.daily_limit,
                'remaining': max(0, self.budget.daily_limit - daily_usage['total_tokens']),
                'percentage': (daily_usage[
                                   'total_tokens'] / self.budget.daily_limit * 100) if self.budget.daily_limit > 0 else 0
            },
            'hourly': {
                'used': hourly_usage['total_tokens'],
                'limit': self.budget.hourly_limit,
                'remaining': max(0, self.budget.hourly_limit - hourly_usage['total_tokens']),
                'percentage': (hourly_usage[
                                   'total_tokens'] / self.budget.hourly_limit * 100) if self.budget.hourly_limit > 0 else 0
            },
            'per_request': {
                'limit': self.budget.per_request_limit
            }
        }

    def optimize_prompt(
            self,
            prompt: str,
            target_tokens: int,
            model: str = "glm4"
    ) -> str:
        lines = prompt.split('\n')

        if len(lines) <= 3:
            return prompt

        estimated_tokens = len(prompt) // 4  # Примерно 4 символа на токен

        if estimated_tokens <= target_tokens:
            return prompt

        optimized_lines = []

        important_keywords = ['задача', 'инструкция', 'формат', 'пример', 'действия']

        for line in lines:
            line_lower = line.lower()
            if any(keyword in line_lower for keyword in important_keywords):
                optimized_lines.append(line)

        optimized_prompt = '\n'.join(optimized_lines[:10])

        return optimized_prompt

    def estimate_tokens(self, text: str, model: str = "glm4") -> int:
        russian_chars = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
        english_chars = len(text) - russian_chars

        estimated_tokens = (english_chars // 4) + (russian_chars // 2)

        return max(estimated_tokens, 1)

    def get_statistics(self) -> Dict[str, Any]:
        remaining_budget = self.get_remaining_budget()

        return {
            **self.stats,
            'remaining_budget': remaining_budget,
            'usage_history_size': len(self.usage_history),
            'current_usage': self.get_current_usage("day")
        }

    def get_usage_by_operation(self) -> Dict[str, Any]:
        operations = {}

        for usage in self.usage_history:
            op = usage.operation
            if op not in operations:
                operations[op] = {
                    'total_tokens': 0,
                    'count': 0,
                    'last_used_timestamp': 0
                }

            operations[op]['total_tokens'] += usage.total_tokens
            operations[op]['count'] += 1
            if usage.timestamp > operations[op]['last_used_timestamp']:
                operations[op]['last_used_timestamp'] = usage.timestamp

        result = {}
        for op, data in operations.items():
            timestamp = data['last_used_timestamp']

            result[op] = {
                'total_tokens': data['total_tokens'],
                'count': data['count'],
                'last_used_timestamp': timestamp,
                'last_used_formatted': (
                    datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
                    if timestamp > 0 else 'never'
                )
            }

        return result

    async def shutdown(self):
        print("Завершение работы TokenManager...")
        self._running = False

        if hasattr(self, '_save_task'):
            self._save_task.cancel()
            try:
                await self._save_task
            except asyncio.CancelledError:
                pass

        self._save_history()

        print(f"TokenManager завершил работу. Всего использовано токенов: {self.stats['total_tokens_used']}")


def create_token_config(
        daily_limit: int = 100000,
        hourly_limit: int = 20000,
        per_request_limit: int = 4000
) -> Dict[str, Any]:
    return {
        'token_budget': {
            'daily_limit': daily_limit,
            'hourly_limit': hourly_limit,
            'per_request_limit': per_request_limit,
            'reset_time': '00:00'
        }
    }


if __name__ == "__main__":
    import asyncio


    async def test_token_manager():
        config = create_token_config(daily_limit=1000, hourly_limit=500)
        manager = TokenManager(config)

        try:
            manager.add_usage(100, 50, "glm4", "planning")
            manager.add_usage(200, 100, "glm4", "reflection")

            print("Текущая статистика:", json.dumps(manager.get_statistics(), indent=2))
            print("\nОставшийся бюджет:", json.dumps(manager.get_remaining_budget(), indent=2))

        except TokenLimitExceeded as e:
            print(f"Лимит превышен: {e}")

        await manager.shutdown()


    asyncio.run(test_token_manager())

import asyncio
import json
import hashlib
from typing import Dict, Any, List, Optional
import ollama
from dataclasses import dataclass
from enum import Enum
import redis.asyncio as redis
import pickle
import gzip
from datetime import datetime, timedelta


class GLM4AgentCore:

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.model_name = config['model']['name']
        self.endpoint = config['model'].get('endpoint', 'http://localhost:11435')

        cache_config = config.get('optimization', {})
        self.use_cache = cache_config.get('cache_enabled', True)

        if self.use_cache:
            redis_url = cache_config.get('redis_url', 'redis://localhost:6379')
            try:
                self.redis = redis.from_url(redis_url)
                print(f"Redis подключен: {redis_url}")
            except Exception as e:
                print(f"Не удалось подключиться к Redis: {e}")
                print("Работаю без кэширования")
                self.use_cache = False
                self.redis = None
        else:
            self.redis = None

        self.cache_hits = 0
        self.cache_misses = 0
        self.total_tokens = 0
        self.task_history = []

    async def initialize(self):
        try:
            if self.endpoint != 'http://localhost:11435':
                import os
                os.environ['OLLAMA_HOST'] = self.endpoint
                print(f"Устанавливаю OLLAMA_HOST = {self.endpoint}")

            if self.use_cache and self.redis:
                try:
                    await self.redis.ping()
                    print("Redis доступен")
                except:
                    print("Redis недоступен, отключаю кэширование")
                    self.use_cache = False

            models = ollama.list()
            if not any(m['model'] == self.model_name for m in models['models']):
                print(f"Загружаю модель {self.model_name}")
                ollama.pull(self.model_name)

            # Тестовый запрос
            test_response = ollama.chat(
                model=self.model_name,
                messages=[{'role': 'user', 'content': 'Привет'}],
                options={
                    'temperature': 0.1,
                    'num_predict': 10
                }
            )
            print(f"{self.model_name} готова: {test_response['message']['content'][:50]}")
            return True

        except Exception as e:
            print(f"Ошибка инициализации {self.model_name}: {e}")

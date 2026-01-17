import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Literal
from dataclasses import dataclass, field
from enum import Enum
import logging

try:
    import playwright.async_api

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

    class Browser:
        pass


    class Page:
        pass


    class ElementHandle:
        pass


class BrowserType(Enum):
    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


@dataclass
class BrowserConfig:
    browser_type: BrowserType = BrowserType.CHROMIUM
    headless: bool = False
    viewport_width: int = 1280
    viewport_height: int = 720
    user_agent: Optional[str] = None
    proxy: Optional[str] = None
    downloads_path: str = "./downloads"
    ignore_https_errors: bool = True
    slow_mo: int = 50
    timeout: int = 30000

    block_ads: bool = True
    bypass_csp: bool = True
    java_script_enabled: bool = True
    locale: str = "ru-RU"
    timezone_id: str = "Europe/Moscow"
    geolocation: Optional[Dict[str, float]] = None
    permissions: List[str] = field(default_factory=list)

    accept_downloads: bool = True
    ignore_default_args: List[str] = field(default_factory=lambda: ["--enable-automation"])


class BrowserController:

    def __init__(self, config: Dict[str, Any]):
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright не установлен. Установите: pip install playwright && playwright install"
            )

        self.config = BrowserConfig(**config) if isinstance(config, dict) else config
        self.logger = logging.getLogger("browser")

        self.playwright = None
        self.browser: Optional[playwright.async_api.Browser] = None
        self.context: Optional[playwright.async_api.BrowserContext] = None
        self.page: Optional[playwright.async_api.Page] = None
        self.is_initialized = False
        self.is_active = False

        self.stats = {
            "pages_opened": 0,
            "navigation_count": 0,
            "clicks_count": 0,
            "inputs_count": 0,
            "errors_count": 0,
            "total_requests": 0,
            "total_responses": 0
        }

        self.action_history: List[Dict[str, Any]] = []

        self.downloads_path = Path(self.config.downloads_path)
        self.downloads_path.mkdir(parents=True, exist_ok=True)

        self.event_listeners: Dict[str, List[callable]] = {}

    async def initialize(self):
        self.logger.info("Инициализация браузера...")

        try:
            self.playwright = await playwright.async_api.async_playwright().start()

            browser_launcher = getattr(self.playwright, self.config.browser_type.value)

            launch_args = {
                "headless": self.config.headless,
                "slow_mo": self.config.slow_mo,
                "timeout": self.config.timeout,
            }

            if self.config.ignore_default_args:
                launch_args["ignore_default_args"] = self.config.ignore_default_args

            self.browser = await browser_launcher.launch(**launch_args)

            context_args = {
                "viewport": {
                    "width": self.config.viewport_width,
                    "height": self.config.viewport_height
                },
                "ignore_https_errors": self.config.ignore_https_errors,
                "java_script_enabled": self.config.java_script_enabled,
                "locale": self.config.locale,
                "timezone_id": self.config.timezone_id,
                "accept_downloads": self.config.accept_downloads,
            }

            if self.config.user_agent:
                context_args["user_agent"] = self.config.user_agent

            if self.config.proxy:
                context_args["proxy"] = {"server": self.config.proxy}

            if self.config.geolocation:
                context_args["geolocation"] = self.config.geolocation

            if self.config.permissions:
                context_args["permissions"] = self.config.permissions

            self.context = await self.browser.new_context(**context_args)

            if self.config.block_ads:
                await self._setup_ad_blocker()

            self.page = await self.context.new_page()

            await self._setup_event_listeners()

            self.is_initialized = True
            self.is_active = True

            self.logger.info(f"Браузер инициализирован: {self.config.browser_type.value}")
            return True

        except Exception as e:
            self.logger.error(f"Ошибка инициализации браузера: {e}")
            await self.close()
            raise

    async def _setup_ad_blocker(self):
        if not self.context:
            return

        blocked_domains = [
            "*.doubleclick.net",
            "*.googleadservices.com",
            "*.googlesyndication.com",
            "*.google-analytics.com",
            "*.ads.*",
            "*.ad.*",
            "*tracking*",
            "*analytics*"
        ]

        await self.context.route("**/*", lambda route: self._block_ads_route(route, blocked_domains))

    async def _block_ads_route(self, route, blocked_domains: List[str]):
        request = route.request

        url = request.url.lower()
        if any(domain in url for domain in blocked_domains):
            await route.abort()
        else:
            await route.continue_()

    async def _setup_event_listeners(self):
        if not self.page:
            return

        self.page.on("console", lambda msg: self._on_console(msg))

        self.page.on("request", lambda req: self._on_request(req))
        self.page.on("response", lambda resp: self._on_response(resp))

        self.page.on("dialog", lambda dialog: self._on_dialog(dialog))

        self.page.on("pageerror", lambda error: self._on_page_error(error))

    # ========== ОСНОВНЫЕ МЕТОДЫ УПРАВЛЕНИЯ ==========

    async def navigate_to(
            self,
            url: str,
            wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = "load"
    ) -> bool:
        if not self.page:
            raise RuntimeError("Страница не инициализирована")

        try:
            self.logger.info(f"Переход по URL: {url}")

            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url

            response = await self.page.goto(
                url,
                wait_until=wait_until,
                timeout=self.config.timeout
            )

            if response:
                self.stats["navigation_count"] += 1
                self._log_action("navigate", {"url": url, "status": response.status})
                return response.ok

            return False

        except Exception as e:
            self.logger.error(f"Ошибка навигации: {e}")
            self.stats["errors_count"] += 1
            raise

    async def find_element(self, selector: str, timeout: int = None) -> Optional[playwright.async_api.ElementHandle]:
        if not self.page:
            raise RuntimeError("Страница не инициализирована")

        try:
            timeout = timeout or self.config.timeout

            element = await self.page.wait_for_selector(
                selector,
                timeout=timeout,
                state="visible"
            )

            if element:
                is_visible = await self._is_element_visible(element)
                if not is_visible:
                    self.logger.warning(f"Элемент найден но не видим: {selector}")

                return element

            return None

        except:
            return None

    async def wait_for_element(self, selector: str, timeout: int = 10000) -> Optional[playwright.async_api.ElementHandle]:
        return await self.find_element(selector, timeout)

    async def click_element(self, element: playwright.async_api.ElementHandle) -> bool:
        try:
            is_visible = await self._is_element_visible(element)
            if not is_visible:
                self.logger.warning("Попытка клика по невидимому элементу")
                await element.scroll_into_view_if_needed()

            await element.click(
                timeout=self.config.timeout,
                no_wait_after=False
            )

            self.stats["clicks_count"] += 1
            self._log_action("click", {"success": True})

            await asyncio.sleep(0.2)

            return True

        except Exception as e:
            self.logger.error(f"Ошибка клика: {e}")
            self.stats["errors_count"] += 1
            return False

    async def input_text(self, element: playwright.async_api.ElementHandle, text: str) -> bool:
        try:
            current_value = await element.input_value()
            if current_value:
                await element.fill('')
                await asyncio.sleep(0.1)

            await element.fill(text)

            await asyncio.sleep(0.1)
            entered_text = await element.input_value()

            success = entered_text == text
            if not success:
                self.logger.warning(f"Текст не совпадает: введено '{entered_text}', ожидалось '{text}'")

            self.stats["inputs_count"] += 1
            self._log_action("input_text", {
                "text_length": len(text),
                "success": success,
                "entered": entered_text
            })

            return success

        except Exception as e:
            self.logger.error(f"Ошибка ввода текста: {e}")
            self.stats["errors_count"] += 1
            return False

    async def clear_element(self, element: playwright.async_api.ElementHandle) -> bool:
        try:
            await element.fill('')
            return True
        except Exception as e:
            self.logger.error(f"Ошибка очистки элемента: {e}")
            return False

    async def extract_data(self, selector: str, attribute: str = "text") -> Any:
        try:
            element = await self.find_element(selector, timeout=5000)
            if not element:
                return None

            if attribute == "text":
                text = await element.text_content()
                return text.strip() if text else None

            elif attribute == "html":
                html = await element.inner_html()
                return html

            elif attribute == "value":
                value = await element.input_value()
                return value

            else:
                attr_value = await element.get_attribute(attribute)
                return attr_value

        except Exception as e:
            self.logger.error(f"Ошибка извлечения данных: {e}")
            return None

    async def scroll_page(self, direction: str = "down", amount: int = 500) -> bool:
        if not self.page:
            return False

        try:
            current_position = await self.page.evaluate("() => window.pageYOffset")

            if direction == "down":
                new_position = current_position + amount
            elif direction == "up":
                new_position = max(0, current_position - amount)
            elif direction == "top":
                new_position = 0
            elif direction == "bottom":
                new_position = await self.page.evaluate(
                    "() => document.body.scrollHeight"
                )
            else:
                new_position = current_position

            await self.page.evaluate(f"window.scrollTo(0, {new_position})")

            await asyncio.sleep(0.3)

            self._log_action("scroll", {
                "direction": direction,
                "amount": amount,
                "from": current_position,
                "to": new_position
            })

            return True

        except Exception as e:
            self.logger.error(f"Ошибка прокрутки: {e}")
            return False

    async def execute_script(self, script: str) -> Any:
        if not self.page:
            raise RuntimeError("Страница не инициализирована")

        try:
            if self.config.bypass_csp:
                await self.page.add_init_script("""
                    // Обход CSP для eval
                    const originalEval = window.eval;
                    window.eval = function(code) {
                        return originalEval(code);
                    };
                """)

            result = await self.page.evaluate(script)

            self._log_action("execute_script", {
                "script_length": len(script),
                "result_type": type(result).__name__
            })

            return result

        except Exception as e:
            self.logger.error(f"Ошибка выполнения скрипта: {e}")
            raise

    async def take_screenshot(self, filename: str = "screenshot") -> str:
        if not self.page:
            raise RuntimeError("Страница не инициализирована")

        try:
            screenshots_dir = self.downloads_path / "screenshots"
            screenshots_dir.mkdir(exist_ok=True)

            timestamp = int(time.time())
            filepath = screenshots_dir / f"{filename}_{timestamp}.png"

            await self.page.screenshot(path=str(filepath), full_page=True)

            self.logger.info(f"Скриншот сохранен: {filepath}")
            return str(filepath)

        except Exception as e:
            self.logger.error(f"Ошибка создания скриншота: {e}")
            return ""

    async def save_page_html(self, filename: str = "page") -> str:
        if not self.page:
            raise RuntimeError("Страница не инициализирована")

        try:
            html_dir = self.downloads_path / "html_pages"
            html_dir.mkdir(exist_ok=True)

            timestamp = int(time.time())
            filepath = html_dir / f"{filename}_{timestamp}.html"

            html = await self.page.content()

            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(html)

            self.logger.info(f"HTML сохранен: {filepath}")
            return str(filepath)

        except Exception as e:
            self.logger.error(f"Ошибка сохранения HTML: {e}")
            return ""

    # ========== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ==========

    async def get_current_url(self) -> str:
        if not self.page:
            return ""
        return self.page.url

    async def get_page_title(self) -> str:
        if not self.page:
            return ""
        return await self.page.title()

    async def is_page_loaded(self) -> bool:
        if not self.page:
            return False

        try:
            ready_state = await self.page.evaluate("document.readyState")
            return ready_state == "complete"
        except:
            return False

    async def _is_element_visible(self, element: playwright.async_api.ElementHandle) -> bool:
        try:
            is_visible = await element.evaluate("""
                element => {
                    const style = window.getComputedStyle(element);
                    return style.display !== 'none' && 
                           style.visibility !== 'hidden' && 
                           style.opacity !== '0' &&
                           element.offsetWidth > 0 &&
                           element.offsetHeight > 0;
                }
            """)
            return is_visible
        except:
            return False

    async def get_page_state(self) -> Dict[str, Any]:
        if not self.page:
            return {}

        try:
            state = {
                "url": self.page.url,
                "title": await self.page.title(),
                "loaded": await self.is_page_loaded(),
                "viewport": self.page.viewport_size,
                "cookies": await self.context.cookies() if self.context else [],
                "timestamp": time.time()
            }

            try:
                state["dom_elements_count"] = await self.page.evaluate(
                    "document.querySelectorAll('*').length"
                )
            except:
                state["dom_elements_count"] = 0

            return state

        except Exception as e:
            self.logger.error(f"Ошибка получения состояния: {e}")
            return {}

    async def wait_for_navigation(
            self,
            wait_until: Literal["domcontentloaded", "load", "networkidle"] = "load",
            timeout: int = None
    ) -> bool:
        if not self.page:
            return False

        try:
            timeout = timeout or self.config.timeout
            await self.page.wait_for_load_state(wait_until, timeout=timeout)
            return True
        except Exception as e:
            self.logger.warning(f"Таймаут ожидания навигации: {e}")
            return False

    async def wait_for_network_idle(
            self,
            timeout: int = 5000
    ) -> bool:
        if not self.page:
            return False

        try:
            await self.page.wait_for_load_state("networkidle", timeout=timeout)
            return True
        except:
            return False

    # ========== ОБРАБОТЧИКИ СОБЫТИЙ ==========

    def _on_console(self, message):
        msg_type = message.type
        msg_text = message.text

        if msg_type in ['error', 'warning']:
            self.logger.warning(f"Консоль браузера [{msg_type}]: {msg_text}")

    def _on_request(self, request):
        self.stats["total_requests"] += 1

        if self._should_log_request(request):
            self.logger.debug(f"Запрос: {request.method} {request.url}")

    def _on_response(self, response):
        self.stats["total_responses"] += 1

        if self._should_log_response(response):
            self.logger.debug(f"Ответ: {response.status} {response.url}")

    async def _on_dialog(self, dialog):
        self.logger.info(f"Диалог: {dialog.type} - {dialog.message}")

        await dialog.dismiss()

    def _on_page_error(self, error):
        self.logger.error(f"Ошибка страницы: {error}")
        self.stats["errors_count"] += 1

    def _should_log_request(self, request) -> bool:
        url = request.url.lower()
        skip_extensions = ['.png', '.jpg', '.css', '.js', '.woff', '.ico']
        skip_domains = ['google-analytics', 'gtm', 'facebook.net']

        return (not any(url.endswith(ext) for ext in skip_extensions) and
                not any(domain in url for domain in skip_domains))

    def _should_log_response(self, response) -> bool:
        return response.status >= 400  # Только ошибки

    def _log_action(self, action_type: str, details: Dict[str, Any]):
        entry = {
            "timestamp": time.time(),
            "action": action_type,
            "details": details,
            "page_url": self.page.url if self.page else ""
        }

        self.action_history.append(entry)

        if len(self.action_history) > 1000:
            self.action_history = self.action_history[-500:]

    # ========== ПУБЛИЧНЫЕ МЕТОДЫ ==========

    async def is_available(self) -> bool:
        return self.is_initialized and self.is_active and self.page is not None

    def get_statistics(self) -> Dict[str, Any]:
        return {
            **self.stats,
            "is_initialized": self.is_initialized,
            "is_active": self.is_active,
            "action_history_size": len(self.action_history),
            "current_url": self.page.url if self.page else "",
        }

    async def get_statistics_async(self) -> Dict[str, Any]:
        title = ""
        if self.page:
            try:
                title = await self.page.title()
            except:
                title = "error"

        return {
            **self.stats,
            "is_initialized": self.is_initialized,
            "is_active": self.is_active,
            "action_history_size": len(self.action_history),
            "current_url": self.page.url if self.page else "",
            "current_title": title
        }

    async def close(self):
        self.logger.info("Закрытие браузера...")
        self.is_active = False

        try:
            if self.page:
                await self.page.close()
                self.page = None

            if self.context:
                await self.context.close()
                self.context = None

            if self.browser:
                await self.browser.close()
                self.browser = None

            if self.playwright:
                await self.playwright.stop()
                self.playwright = None

            self.is_initialized = False
            self.logger.info("Браузер закрыт")

        except Exception as e:
            self.logger.error(f"Ошибка при закрытии браузера: {e}")

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


# ========== УТИЛИТЫ ==========

def create_browser_config(
        headless: bool = True,
        browser_type: str = "chromium",
        viewport_size: Tuple[int, int] = (1280, 720),
        proxy: Optional[str] = None,
        user_agent: Optional[str] = None
) -> Dict[str, Any]:

    user_agents = {
        "chrome": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "firefox": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "safari": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
    }

    if not user_agent:
        user_agent = user_agents.get(browser_type, user_agents["chrome"])

    return {
        "browser_type": BrowserType(browser_type),
        "headless": headless,
        "viewport_width": viewport_size[0],
        "viewport_height": viewport_size[1],
        "user_agent": user_agent,
        "proxy": proxy,
        "ignore_https_errors": True,
        "slow_mo": 30 if not headless else 0,
        "timeout": 45000,
        "block_ads": True,
        "bypass_csp": True,
        "locale": "ru-RU",
        "timezone_id": "Europe/Moscow",
        "permissions": ["clipboard-read", "clipboard-write"]
    }


async def test_browser():
    config = create_browser_config(headless=True)

    async with BrowserController(config) as browser:
        success = await browser.navigate_to("https://httpbin.org/html")
        print(f"Навигация: {'Успех' if success else 'Провал'}")

        data = await browser.extract_data("h1", "text")
        print(f"Извлеченный текст: {data}")

        screenshot = await browser.take_screenshot("test")
        print(f"Скриншот: {screenshot}")

        stats = await browser.get_statistics_async()
        print(f"Статистика: {stats}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_browser())
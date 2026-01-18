import json
import base64
import html
from pathlib import Path
from typing import Dict, Any, List, Optional, Union
from datetime import datetime
from dataclasses import dataclass, field, asdict
from enum import Enum
import logging
from logging.handlers import RotatingFileHandler
import sys


class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class LogEntry:
    timestamp: str
    level: str
    message: str
    module: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    screenshot_path: Optional[str] = None
    html_path: Optional[str] = None


class VisualLogger:

    def __init__(
            self,
            config: Dict[str, Any],
            name: str = "browser_bot"
    ):
        self.config = config
        self.name = name
        self.log_dir = Path(config.get('log_dir', './logs'))
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.screenshots_dir = self.log_dir / 'screenshots'
        self.screenshots_dir.mkdir(exist_ok=True)

        self.html_dir = self.log_dir / 'html'
        self.html_dir.mkdir(exist_ok=True)

        self.reports_dir = self.log_dir / 'reports'
        self.reports_dir.mkdir(exist_ok=True)

        self._setup_logging()

        self.log_history: List[LogEntry] = []
        self.max_history = config.get('max_log_history', 1000)

        self.stats = {
            'total_logs': 0,
            'screenshots_taken': 0,
            'html_saved': 0,
            'errors_logged': 0
        }

        self.logger.info(f"Визуальный логгер инициализирован: {self.log_dir}")

    def _setup_logging(self):
        log_level = getattr(logging, self.config.get('level', 'INFO'))

        class ColorFormatter(logging.Formatter):
            COLORS = {
                'DEBUG': '\033[36m',  # Cyan
                'INFO': '\033[32m',  # Green
                'WARNING': '\033[33m',  # Yellow
                'ERROR': '\033[31m',  # Red
                'CRITICAL': '\033[41m'  # Red background
            }
            RESET = '\033[0m'

            def format(self, record):
                color = self.COLORS.get(record.levelname, '')
                record.msg = f"{color}{record.msg}{self.RESET}"
                return super().format(record)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_formatter = ColorFormatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        console_handler.setFormatter(console_formatter)

        log_file = self.log_dir / 'browser_bot.log'
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(log_level)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(file_formatter)

        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(log_level)
        self.logger.handlers = []
        self.logger.addHandler(console_handler)
        self.logger.addHandler(file_handler)
        self.logger.propagate = False

    def _add_log_entry(
            self,
            level: LogLevel,
            message: str,
            data: Optional[Dict[str, Any]] = None,
            screenshot_path: Optional[str] = None,
            html_path: Optional[str] = None,
            module: str = ""
    ):
        entry = LogEntry(
            timestamp=datetime.now().isoformat(),
            level=level.value,
            message=message,
            module=module,
            data=data or {},
            screenshot_path=screenshot_path,
            html_path=html_path
        )

        self.log_history.append(entry)
        self.stats['total_logs'] += 1

        if len(self.log_history) > self.max_history:
            self.log_history = self.log_history[-self.max_history // 2:]

    def debug(self, message: str, **kwargs):
        self.logger.debug(message)
        self._add_log_entry(LogLevel.DEBUG, message, kwargs.get('data'))

    def info(self, message: str, **kwargs):
        self.logger.info(message)
        self._add_log_entry(LogLevel.INFO, message, kwargs.get('data'))

    def success(self, message: str, **kwargs):
        colored_message = f"\033[32m✓ {message}\033[0m"
        self.logger.info(colored_message)
        self._add_log_entry(LogLevel.INFO, message, kwargs.get('data'))

    def warning(self, message: str, **kwargs):
        self.logger.warning(message)
        self._add_log_entry(LogLevel.WARNING, message, kwargs.get('data'))
        if kwargs.get('screenshot_on_warning', self.config.get('screenshot_on_warning', False)):
            self.request_screenshot('warning')

    def error(self, message: str, **kwargs):
        self.logger.error(message)
        self._add_log_entry(LogLevel.ERROR, message, kwargs.get('data'))
        self.stats['errors_logged'] += 1

        if kwargs.get('screenshot_on_error', self.config.get('screenshot_on_error', True)):
            self.request_screenshot('error')

    def critical(self, message: str, **kwargs):
        self.logger.critical(message)
        self._add_log_entry(LogLevel.CRITICAL, message, kwargs.get('data'))
        self.request_screenshot('critical')

    def request_screenshot(self, reason: str = "manual") -> Optional[str]:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{reason}_{timestamp}.png"
        path = self.screenshots_dir / filename

        self.info(f"Запрошен скриншот: {reason}", data={"path": str(path)})
        return str(path)

    async def save_screenshot(
            self,
            screenshot_data: Union[bytes, str],
            filename: str
    ) -> str:
        try:
            path = self.screenshots_dir / filename

            if isinstance(screenshot_data, str):
                if screenshot_data.startswith('data:image'):
                    screenshot_data = screenshot_data.split(',')[1]
                screenshot_bytes = base64.b64decode(screenshot_data)
            else:
                screenshot_bytes = screenshot_data

            path.write_bytes(screenshot_bytes)
            self.stats['screenshots_taken'] += 1

            self.debug(f"Скриншот сохранен: {filename}")
            return str(path)

        except Exception as e:
            self.error(f"Ошибка сохранения скриншота: {e}")
            return ""

    async def save_html(
            self,
            html_content: str,
            filename: str
    ) -> str:
        try:
            path = self.html_dir / filename

            safe_html = html.escape(html_content)

            path.write_text(safe_html, encoding='utf-8')
            self.stats['html_saved'] += 1

            self.debug(f"HTML сохранен: {filename}")
            return str(path)

        except Exception as e:
            self.error(f"Ошибка сохранения HTML: {e}")
            return ""

    def log_action(
            self,
            action_type: str,
            details: Dict[str, Any],
            screenshot_path: Optional[str] = None,
            html_path: Optional[str] = None
    ):
        message = f"Действие: {action_type}"
        if 'description' in details:
            message += f" - {details['description']}"

        self.info(message, data=details)

        self._add_log_entry(
            LogLevel.INFO,
            message,
            data=details,
            screenshot_path=screenshot_path,
            html_path=html_path,
            module="action"
        )

    def log_plan_execution(
            self,
            execution_id: str,
            task: str,
            actions_count: int,
            success: bool
    ):
        message = f"План выполнен: {task[:50]}... {'✓' if success else '✗'}"
        data = {
            "execution_id": execution_id,
            "task": task,
            "actions_count": actions_count,
            "success": success
        }

        if success:
            self.success(message, data=data)
        else:
            self.error(message, data=data)

    async def generate_report(
            self,
            execution_id: str,
            plan_data: Dict[str, Any],
            execution_results: Dict[str, Any]
    ) -> str:
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_file = self.reports_dir / f"report_{execution_id}_{timestamp}.html"

            report_data = {
                "execution_id": execution_id,
                "generated_at": datetime.now().isoformat(),
                "plan": plan_data,
                "results": execution_results,
                "logs": [asdict(entry) for entry in self.log_history[-100:]],  # последние 100 записей
                "statistics": self.get_statistics()
            }

            html_report = self._generate_html_report(report_data)

            report_file.write_text(html_report, encoding='utf-8')
            self.info(f"Отчет сгенерирован: {report_file}")

            return str(report_file)

        except Exception as e:
            self.error(f"Ошибка генерации отчета: {e}")
            return ""

    def _generate_html_report(self, data: Dict[str, Any]) -> str:
        return f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Отчет выполнения - {data['execution_id']}</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .success {{ color: green; }}
                .error {{ color: red; }}
                .warning {{ color: orange; }}
                table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #f2f2f2; }}
                .log-entry {{ margin: 5px 0; padding: 5px; border-left: 3px solid #ccc; }}
                .log-debug {{ border-color: #36c; }}
                .log-info {{ border-color: #2c2; }}
                .log-warning {{ border-color: #fc0; }}
                .log-error {{ border-color: #c22; }}
            </style>
        </head>
        <body>
            <h1>Отчет выполнения</h1>
            <p><strong>ID:</strong> {data['execution_id']}</p>
            <p><strong>Время:</strong> {data['generated_at']}</p>
            <p><strong>Статус:</strong> <span class="{'success' if data['results'].get('success') else 'error'}">
                {'Успех' if data['results'].get('success') else 'Ошибка'}
            </span></p>

            <h2>Задача</h2>
            <p>{html.escape(data['plan'].get('task', 'Нет задачи'))}</p>

            <h2>Результаты</h2>
            <p>Действий выполнено: {data['results'].get('successful_actions', 0)} / {data['results'].get('total_actions', 0)}</p>
            <p>Длительность: {data['results'].get('duration', 0):.2f} сек</p>

            <h2>Логи</h2>
            <div>
                {"".join(self._format_log_entry_html(log) for log in data['logs'])}
            </div>
        </body>
        </html>
        """

    def _format_log_entry_html(self, log_entry: Dict[str, Any]) -> str:
        level_class = f"log-{log_entry['level'].lower()}"
        return f"""
        <div class="log-entry {level_class}">
            <strong>[{log_entry['timestamp']}] {log_entry['level']}:</strong>
            {html.escape(log_entry['message'])}
        </div>
        """

    def get_statistics(self) -> Dict[str, Any]:
        return {
            **self.stats,
            "log_history_size": len(self.log_history),
            "log_dir": str(self.log_dir),
            "log_level": self.config.get('level', 'INFO')
        }

    def get_recent_logs(self, count: int = 10) -> List[Dict[str, Any]]:
        recent = self.log_history[-count:] if self.log_history else []
        return [asdict(entry) for entry in recent]

    async def cleanup_old_files(self, max_age_days: int = 7):
        try:
            from datetime import datetime, timedelta
            cutoff_date = datetime.now() - timedelta(days=max_age_days)

            deleted_count = 0
            for file in self.screenshots_dir.glob("*"):
                if file.is_file():
                    file_time = datetime.fromtimestamp(file.stat().st_mtime)
                    if file_time < cutoff_date:
                        file.unlink()
                        deleted_count += 1

            for file in self.html_dir.glob("*"):
                if file.is_file():
                    file_time = datetime.fromtimestamp(file.stat().st_mtime)
                    if file_time < cutoff_date:
                        file.unlink()
                        deleted_count += 1

            self.info(f"Очищено старых файлов: {deleted_count}")

        except Exception as e:
            self.error(f"Ошибка очистки файлов: {e}")

    def shutdown(self):
        self.info("Визуальный логгер завершает работу")

        stats_file = self.log_dir / 'final_stats.json'
        stats_data = {
            "shutdown_time": datetime.now().isoformat(),
            "statistics": self.get_statistics(),
            "recent_logs": self.get_recent_logs(20)
        }

        try:
            stats_file.write_text(json.dumps(stats_data, indent=2, ensure_ascii=False))
        except:
            pass
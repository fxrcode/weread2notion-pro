import logging
import os
import time

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

GATEWAY_URL = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.4"

load_dotenv()


class WeReadApiError(RuntimeError):
    """Raised when the official WeRead gateway rejects a request."""


class WeReadApiRateLimitError(WeReadApiError):
    """Raised when requests must stop until the gateway quota resets."""


class WeReadGateway:
    """Official Agent API transport with authentication and retry policy."""

    def __init__(
        self,
        api_key=None,
        session=None,
        *,
        timeout=30,
        max_attempts=4,
        sleep=None,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts 必须至少为 1")
        self.api_key = api_key or os.getenv("WEREAD_API_KEY")
        if not self.api_key or not self.api_key.strip():
            raise WeReadApiError(
                "没有找到 WEREAD_API_KEY，请从 "
                "https://weread.qq.com/r/weread-skills 获取并配置"
            )

        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key.strip()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "weread2notion-pro",
            }
        )
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.sleep = sleep or time.sleep

    @staticmethod
    def _retry_delay(response, attempt):
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), 0)
            except (TypeError, ValueError):
                return 60
        if response.status_code in (429, 499):
            return 60
        return 2 ** (attempt + 1)

    @staticmethod
    def _is_rate_limit_message(message):
        normalized = str(message).lower()
        return any(
            marker in normalized
            for marker in (
                "频繁",
                "限流",
                "配额",
                "次数",
                "quota",
                "rate limit",
                "too many",
            )
        )

    def request(self, api_name, **params):
        payload = {
            "api_name": api_name,
            "skill_version": SKILL_VERSION,
            **params,
        }
        logger.info(f"调用微信读书官方API: {api_name}")

        for attempt in range(self.max_attempts):
            try:
                response = self.session.post(
                    GATEWAY_URL,
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                if attempt < self.max_attempts - 1:
                    self.sleep(2**attempt)
                    continue
                raise WeReadApiError(
                    f"微信读书API请求失败 ({api_name}): {exc}"
                ) from exc

            try:
                data = response.json()
            except ValueError as exc:
                if self._should_retry(response, attempt):
                    self._wait_before_retry(response, attempt)
                    continue
                raise self._invalid_json_error(
                    api_name,
                    response.status_code,
                ) from exc

            if response.status_code >= 400:
                if self._should_retry(response, attempt):
                    self._wait_before_retry(response, attempt)
                    continue
                errmsg = (
                    data.get("errmsg", data.get("errMsg"))
                    if isinstance(data, dict)
                    else None
                )
                raise self._http_error(api_name, response.status_code, errmsg)

            return self._validate_payload(api_name, data)

    def _should_retry(self, response, attempt):
        return (
            attempt < self.max_attempts - 1
            and (
                response.status_code in (429, 499)
                or response.status_code >= 500
            )
        )

    def _wait_before_retry(self, response, attempt):
        delay = self._retry_delay(response, attempt)
        logger.warning(
            f"微信读书API暂时不可用 ({response.status_code})，{delay:g}秒后重试"
        )
        self.sleep(delay)

    @staticmethod
    def _error_type(status_code):
        return (
            WeReadApiRateLimitError
            if status_code in (403, 429, 499)
            else WeReadApiError
        )

    @classmethod
    def _http_error(cls, api_name, status_code, message=None):
        detail = f": {message}" if message else ""
        error = f"微信读书API HTTP错误 ({api_name}): {status_code}{detail}"
        logger.error(error)
        return cls._error_type(status_code)(error)

    @classmethod
    def _invalid_json_error(cls, api_name, status_code):
        error = (
            f"微信读书API返回了无效JSON ({api_name})，"
            f"HTTP状态码: {status_code}"
        )
        logger.error(error)
        return cls._error_type(status_code)(error)

    def _validate_payload(self, api_name, data):
        if not isinstance(data, dict):
            raise WeReadApiError(f"微信读书API返回格式异常 ({api_name})")

        upgrade_info = data.get("upgrade_info")
        if upgrade_info:
            message = (
                upgrade_info.get("message")
                if isinstance(upgrade_info, dict)
                else str(upgrade_info)
            )
            raise WeReadApiError(
                f"微信读书API要求升级 Skill（当前 {SKILL_VERSION}）: {message}"
            )

        errcode = data.get("errcode", data.get("errCode", 0))
        if errcode:
            errmsg = data.get("errmsg", data.get("errMsg", "未知错误"))
            error = (
                f"微信读书API返回错误 ({api_name}): {errmsg} (错误码: {errcode})"
            )
            logger.error(error)
            error_type = (
                WeReadApiRateLimitError
                if self._is_rate_limit_message(errmsg)
                else WeReadApiError
            )
            raise error_type(error)

        return data

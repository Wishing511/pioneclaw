"""
Guardrails 输出验证系统

借鉴 CrewAI Guardrails：
- LLM 验证：用自然语言描述约束，LLM 判断输出是否满足
- 函数验证：用 Python 函数验证输出
- 自动重试：验证失败后自动重试，注入失败原因
- 失败策略：retry / fail / default_value

使用场景：
- 确保输出是有效 JSON
- 确保输出包含必要字段
- 确保输出符合业务规则
- 确保输出风格一致
"""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class GuardrailFailedError(Exception):
    """Guardrail 验证失败"""

    pass


@dataclass
class GuardrailConfig:
    """输出验证配置

    借鉴 CrewAI GuardrailConfig
    """

    validator: str | Callable  # 字符串=LLM验证，Callable=函数验证
    max_retries: int = 3
    on_failure: str = "retry"  # retry, fail, default_value
    default_value: Any = None
    description: str = ""  # 验证器描述（用于日志）


@dataclass
class ValidationResult:
    """验证结果"""

    valid: bool
    reason: str = ""
    details: dict[str, Any] | None = None


class Guardrail:
    """输出验证器

    借鉴 CrewAI Guardrail
    """

    def __init__(
        self,
        config: GuardrailConfig,
        llm: Any = None,
    ):
        """
        Args:
            config: Guardrail 配置
            llm: LLM 实例（用于字符串验证）
        """
        self.config = config
        self.llm = llm

    async def validate(self, output: Any) -> ValidationResult:
        """验证输出

        Args:
            output: 要验证的输出

        Returns:
            ValidationResult: 验证结果
        """
        if isinstance(self.config.validator, str):
            return await self._llm_validate(output, self.config.validator)
        else:
            return await self._function_validate(output, self.config.validator)

    async def _llm_validate(
        self,
        output: Any,
        constraint: str,
    ) -> ValidationResult:
        """LLM 验证输出是否满足约束

        Args:
            output: 要验证的输出
            constraint: 自然语言约束描述

        Returns:
            ValidationResult: 验证结果
        """
        if not self.llm:
            logger.warning("No LLM provided for string validation, skipping")
            return ValidationResult(valid=True, reason="No LLM for validation")

        output_str = str(output)

        prompt = f"""验证以下输出是否满足约束。

约束: {constraint}

输出:
{output_str[:2000]}  # 限制长度

请回答:
- 如果输出满足约束，回答: PASS
- 如果输出不满足约束，回答: FAIL: <原因>

只回答 PASS 或 FAIL: <原因>，不要其他内容。"""

        try:
            response = await self._call_llm(prompt)
            response_upper = response.upper().strip()

            if response_upper.startswith("PASS"):
                return ValidationResult(valid=True, reason="LLM validation passed")
            elif response_upper.startswith("FAIL"):
                reason = (
                    response[5:].strip() if ":" in response else "LLM validation failed"
                )
                return ValidationResult(valid=False, reason=reason)
            else:
                # 无法解析，假设通过
                logger.warning(
                    f"Could not parse LLM validation response: {response[:100]}"
                )
                return ValidationResult(
                    valid=True, reason="Could not parse LLM response"
                )

        except Exception as e:
            logger.error(f"LLM validation error: {e}")
            return ValidationResult(valid=False, reason=f"LLM validation error: {e}")

    async def _function_validate(
        self,
        output: Any,
        validator: Callable,
    ) -> ValidationResult:
        """函数验证

        Args:
            output: 要验证的输出
            validator: 验证函数

        Returns:
            ValidationResult: 验证结果
        """
        try:
            result = validator(output)

            # 处理不同返回类型
            if isinstance(result, bool):
                return ValidationResult(
                    valid=result,
                    reason="Function validation passed"
                    if result
                    else "Function validation failed",
                )
            elif isinstance(result, tuple):
                valid, reason = result
                return ValidationResult(valid=valid, reason=str(reason))
            elif isinstance(result, dict):
                return ValidationResult(
                    valid=result.get("valid", False),
                    reason=result.get("reason", ""),
                    details=result.get("details"),
                )
            else:
                return ValidationResult(valid=bool(result), reason=str(result))

        except Exception as e:
            return ValidationResult(
                valid=False, reason=f"Validation function error: {e}"
            )

    async def _call_llm(self, prompt: str) -> str:
        """调用 LLM"""
        if hasattr(self.llm, "generate"):
            result = self.llm.generate(prompt)
            if asyncio.iscoroutine(result):
                result = await result
            return str(result)
        elif hasattr(self.llm, "chat"):
            result = self.llm.chat([{"role": "user", "content": prompt}])
            if asyncio.iscoroutine(result):
                result = await result
            return str(result)
        elif hasattr(self.llm, "process_direct"):
            result = await self.llm.process_direct(message=prompt)
            return str(result)
        else:
            raise ValueError(
                "LLM has no callable method (generate, chat, or process_direct)"
            )


class GuardrailExecutor:
    """Guardrail 执行器

    管理多个 Guardrail，执行验证和重试逻辑
    """

    def __init__(
        self,
        guardrails: list[Guardrail],
        max_retries: int = 3,
    ):
        """
        Args:
            guardrails: Guardrail 列表
            max_retries: 最大重试次数（覆盖单个 guardrail 配置）
        """
        self.guardrails = guardrails
        self.max_retries = max_retries

    async def execute_with_validation(
        self,
        func: Callable,
        *args,
        context: str | None = None,
        **kwargs,
    ) -> Any:
        """执行函数并验证输出，失败则重试

        Args:
            func: 要执行的异步函数
            context: 当前上下文（用于注入失败原因）
            **kwargs: 函数参数

        Returns:
            Any: 验证通过的输出

        Raises:
            GuardrailFailedError: 所有重试后仍失败
        """
        last_error = ""
        attempt = 0
        max_attempts = self.max_retries + 1

        while attempt < max_attempts:
            attempt += 1

            # 执行函数
            try:
                result = func(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
            except Exception as e:
                logger.error(f"Execution failed on attempt {attempt}: {e}")
                last_error = str(e)
                if attempt < max_attempts:
                    continue
                raise

            # 验证输出
            all_valid = True
            for guardrail in self.guardrails:
                validation = await guardrail.validate(result)

                if not validation.valid:
                    all_valid = False
                    last_error = validation.reason
                    logger.warning(
                        f"Guardrail validation failed (attempt {attempt}): {validation.reason}"
                    )

                    # 注入失败原因到下次执行的上下文
                    if context and attempt < max_attempts:
                        kwargs["context"] = (
                            context + f"\n\n[上次失败原因: {validation.reason}]"
                        )
                    break

            if all_valid:
                return result

        # 所有重试失败
        # 检查是否有 guardrail 配置了 default_value
        for g in self.guardrails:
            if (
                g.config.on_failure == "default_value"
                and g.config.default_value is not None
            ):
                logger.info(f"Returning default value after {attempt} failed attempts")
                return g.config.default_value

        raise GuardrailFailedError(
            f"Guardrail validation failed after {attempt} attempts: {last_error}"
        )

    async def validate_only(self, output: Any) -> ValidationResult:
        """只验证输出，不执行函数

        Args:
            output: 要验证的输出

        Returns:
            ValidationResult: 综合验证结果
        """
        for guardrail in self.guardrails:
            result = await guardrail.validate(output)
            if not result.valid:
                return result

        return ValidationResult(valid=True, reason="All validations passed")


# ==================== 预置验证器 ====================


class builtin_validators:
    """预置验证函数"""

    @staticmethod
    def is_json(output: Any) -> tuple:
        """验证输出是否为有效 JSON"""
        try:
            if isinstance(output, str):
                json.loads(output)
            elif isinstance(output, dict):
                pass  # 已经是 dict
            else:
                json.loads(str(output))
            return True, "Valid JSON"
        except json.JSONDecodeError as e:
            return False, f"Invalid JSON: {e}"

    @staticmethod
    def has_fields(*fields: str) -> Callable:
        """验证输出是否包含指定字段

        Args:
            fields: 必需字段名列表

        Returns:
            验证函数
        """

        def validator(output: Any) -> tuple:
            try:
                if isinstance(output, str):
                    data = json.loads(output)
                elif isinstance(output, dict):
                    data = output
                else:
                    return False, "Output is not a dict or JSON string"

                missing = [f for f in fields if f not in data]
                if missing:
                    return False, f"Missing fields: {missing}"
                return True, "All required fields present"
            except json.JSONDecodeError as e:
                return False, f"Invalid JSON: {e}"

        return validator

    @staticmethod
    def is_non_empty(output: Any) -> tuple:
        """验证输出是否非空"""
        if output is None:
            return False, "Output is None"
        if isinstance(output, str) and not output.strip():
            return False, "Output is empty string"
        if isinstance(output, (list, dict)) and len(output) == 0:
            return False, "Output is empty container"
        return True, "Output is non-empty"

    @staticmethod
    def max_length(max_len: int) -> Callable:
        """验证输出长度不超过限制

        Args:
            max_len: 最大长度

        Returns:
            验证函数
        """

        def validator(output: Any) -> tuple:
            output_str = str(output)
            if len(output_str) > max_len:
                return False, f"Output length {len(output_str)} exceeds max {max_len}"
            return True, f"Output length {len(output_str)} within limit"

        return validator

    @staticmethod
    def matches_regex(pattern: str) -> Callable:
        """验证输出是否匹配正则表达式

        Args:
            pattern: 正则表达式

        Returns:
            验证函数
        """
        import re

        def validator(output: Any) -> tuple:
            output_str = str(output)
            if re.search(pattern, output_str):
                return True, "Output matches pattern"
            return False, f"Output does not match pattern: {pattern}"

        return validator

    @staticmethod
    def contains(*substrings: str) -> Callable:
        """验证输出是否包含指定子字符串

        Args:
            substrings: 必需包含的子字符串列表

        Returns:
            验证函数
        """

        def validator(output: Any) -> tuple:
            output_str = str(output)
            missing = [s for s in substrings if s not in output_str]
            if missing:
                return False, f"Output missing required substrings: {missing}"
            return True, "Output contains all required substrings"

        return validator

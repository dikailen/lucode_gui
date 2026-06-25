from __future__ import annotations

from runtime.kernel.strategies.auto import AutoStrategy
from runtime.kernel.strategies.base import ExecutionContext, ExecutionStrategy


def create_execution_strategy(*, routing_input: str, execution_mode: str) -> ExecutionStrategy:
    del routing_input, execution_mode
    return AutoStrategy()


__all__ = ["ExecutionContext", "ExecutionStrategy", "create_execution_strategy"]

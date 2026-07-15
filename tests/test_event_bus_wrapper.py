from __future__ import annotations

import inspect

from runtime.execution import execute_dynamic_request


def test_execution_package_wrapper_accepts_runtime_context_arguments():
    signature = inspect.signature(execute_dynamic_request)

    assert "event_bus" in signature.parameters
    assert "inline_files" in signature.parameters

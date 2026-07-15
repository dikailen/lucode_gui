from __future__ import annotations

import asyncio
from types import SimpleNamespace

from runtime.agent import runner


def test_streaming_falls_back_to_non_streamed_run_when_provider_rejects_stream(monkeypatch):
    calls = {"streamed": 0, "run": 0}

    class FakeRunner:
        @staticmethod
        def run_streamed(*args, **kwargs):
            calls["streamed"] += 1
            raise RuntimeError("streaming is not supported by this provider")

        @staticmethod
        async def run(*args, **kwargs):
            calls["run"] += 1
            return SimpleNamespace(final_output="fallback answer")

    monkeypatch.setattr(runner, "runner_class", lambda: FakeRunner)

    result = asyncio.run(runner.run_agent_once("agent", "input", SimpleNamespace(), stream_output=True))

    assert result.final_output == "fallback answer"
    assert calls == {"streamed": 1, "run": 1}

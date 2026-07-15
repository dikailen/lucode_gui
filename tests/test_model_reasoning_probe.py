from __future__ import annotations


def test_reasoning_effort_probe_caches_only_levels_accepted_by_this_model(tmp_path, monkeypatch):
    from catalog_system import model_probe

    class Response:
        def __init__(self, status_code: int, text: str = ""):
            self.status_code = status_code
            self.text = text

    def fake_post(_endpoint, _headers, payload, _timeout):
        return (Response(200) if payload["reasoning_effort"] in {"low", "high"} else Response(400, "unsupported"), None)

    monkeypatch.setattr(model_probe, "_safe_post_json", fake_post)
    model = {
        "id": "proxy_gpt_5_4_model",
        "model_name": "gpt-5.4",
        "backend_type": "openai_compatible",
        "base_url": "https://proxy.example/v1",
        "api_key": "test-key",
    }

    result = model_probe.probe_reasoning_effort_capabilities(tmp_path, model, levels=["low", "medium", "high"])

    assert result["supports_reasoning_effort"] is True
    assert result["reasoning_effort_levels"] == ["low", "high"]
    cached = model_probe.cached_probe_for_model(tmp_path, model)
    assert cached["reasoning_effort"]["accepted_levels"] == ["low", "high"]


def test_reasoning_effort_probe_result_is_invalidated_when_model_fingerprint_changes(tmp_path, monkeypatch):
    from catalog_system import model_probe

    monkeypatch.setattr(model_probe, "_safe_post_json", lambda *_args: (type("Response", (), {"status_code": 200, "text": ""})(), None))
    model = {
        "id": "proxy_gpt_5_4_model",
        "model_name": "gpt-5.4",
        "backend_type": "openai_compatible",
        "base_url": "https://proxy.example/v1",
        "api_key": "test-key",
    }
    model_probe.probe_reasoning_effort_capabilities(tmp_path, model, levels=["low"])

    changed = {**model, "base_url": "https://new-proxy.example/v1"}
    assert model_probe.cached_probe_for_model(tmp_path, changed) is None

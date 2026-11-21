"""LLM provider plumbing, offline: both providers build identical prompts and call parameters,
and backend selection follows the flag / environment. No network calls are made."""
from types import SimpleNamespace

import pytest

from cohort import nl2sql


class _FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        msg = SimpleNamespace(content='{"sql": "SELECT COUNT(*) FROM patients", "refuse": false, "reason": "x"}')
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5,
                                                     prompt_tokens_details=SimpleNamespace(cached_tokens=0)),
                               model=kw["model"], system_fingerprint="fp_test")


def _fake(cls, monkeypatch, tmp_path):
    monkeypatch.setattr(nl2sql, "CACHE", tmp_path / "cache.jsonl")
    b = object.__new__(cls)
    nl2sql._ChatLLM.__init__(b, nl2sql.PROMPT_VERSION)
    b.cache_model = b.call_model = "m"
    comp = _FakeCompletions()
    b.client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    return b, comp


@pytest.mark.parametrize("variant", [nl2sql.PROMPT_VERSION, "bare", "permissive"])
def test_providers_build_identical_messages(variant, monkeypatch, tmp_path):
    a, _ = _fake(nl2sql.AzureLLM, monkeypatch, tmp_path)
    o, _ = _fake(nl2sql.OpenAILLM, monkeypatch, tmp_path)
    a.variant = o.variant = variant
    for args in [("How many patients?", "researcher"),
                 ("How many patients?", "researcher", "[aggregate_only] x", "SELECT patient_id FROM patients")]:
        assert a.build_messages(*args) == o.build_messages(*args)


def test_openai_call_parameters_match_azure(monkeypatch, tmp_path):
    a, ca = _fake(nl2sql.AzureLLM, monkeypatch, tmp_path)
    a.generate("How many patients?", "researcher")
    (tmp_path / "o").mkdir()
    o, co = _fake(nl2sql.OpenAILLM, monkeypatch, tmp_path / "o")   # fresh cache file
    g = o.generate("How many patients?", "researcher")
    assert ca.calls == co.calls and len(co.calls) == 1
    assert co.calls[0]["temperature"] == 0 and co.calls[0]["max_tokens"] == 700
    assert g["sql"] == "SELECT COUNT(*) FROM patients" and not g["cached"]
    assert o.generate("How many patients?", "researcher")["cached"] and len(co.calls) == 1


def test_backend_selection(monkeypatch):
    for k in ("COHORT_LLM_PROVIDER", "OPENAI_API_KEY", "COHORT_AZURE_ENDPOINT", "COHORT_AZURE_SUBSCRIPTION"):
        monkeypatch.delenv(k, raising=False)
    assert nl2sql.get_backend("auto").name == "template"
    with pytest.raises(RuntimeError):
        nl2sql.get_backend("openai")
    with pytest.raises(RuntimeError):
        nl2sql.get_backend("azure")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    b = nl2sql.get_backend("auto")
    assert b.name == "openai" and b.model == nl2sql.OPENAI_DEFAULT_MODEL == "gpt-4.1-2025-04-14"
    monkeypatch.setenv("COHORT_LLM_PROVIDER", "template")
    assert nl2sql.get_backend("auto").name == "template"
    assert nl2sql.get_backend("openai").name == "openai"


def test_provider_exceptions_are_wrapped(monkeypatch, tmp_path):
    import httpx
    import openai

    o, comp = _fake(nl2sql.OpenAILLM, monkeypatch, tmp_path)

    def boom(**kw):
        raise openai.APIConnectionError(request=httpx.Request("POST", "https://llm.invalid/v1/chat/completions"))

    comp.create = boom
    with pytest.raises(nl2sql.LLMBackendError, match="APIConnectionError"):
        o.generate("How many patients?", "researcher")
    assert not (tmp_path / "cache.jsonl").exists()   # failures are never cached

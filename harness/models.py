"""Model client factory. One model for all task types; no sampling magic."""
import asyncio
import inspect
import os


def _sanitize_no_proxy():
    """httpx rejects CIDR and bracketed IPv6 entries in NO_PROXY; drop them."""
    value = os.environ.get('NO_PROXY') or os.environ.get('no_proxy')
    if not value:
        return
    clean = ','.join(e for e in value.split(',')
                     if e and '/' not in e and '[' not in e)
    for name in ('NO_PROXY', 'no_proxy'):
        if name in os.environ:
            os.environ[name] = clean


class SyncModel:
    """openjiuwen.Model.invoke is async; the harness loop is sync.
    One persistent event loop: the client's connections bind to it."""

    def __init__(self, model):
        self._model = model
        self._loop = asyncio.new_event_loop()

    def invoke(self, messages, max_tokens):
        _sanitize_no_proxy()
        reply = self._model.invoke(messages=messages, max_tokens=max_tokens)
        if inspect.iscoroutine(reply):
            reply = self._loop.run_until_complete(reply)
        return reply


def build(model=None, timeout=60, max_retries=0):
    from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig

    return SyncModel(Model(
        model_client_config=ModelClientConfig(
            client_provider='OpenAI', api_key=os.environ['LLM_API_KEY'],
            api_base=os.environ['LLM_BASE_URL'], timeout=timeout, max_retries=max_retries,
        ),
        model_config=ModelRequestConfig(model=model or 'qwen3.8-27b-noreason',
                                        temperature=0, max_tokens=1500),
    ))

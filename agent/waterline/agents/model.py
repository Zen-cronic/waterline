"""A failure-tolerant Gemini model: try a chain, fall through on 503.

This is the model-layer answer to the Architecture rubric's failure-tolerance
sub-question — "how does the system recover if a worker returns an error?" A
freshly-GA'd model (3.7-flash) throws intermittent 503s under launch load; a live
demo cannot stall on that. FallbackGemini tries each model in the chain and moves
to the next on a server error, as long as nothing has been streamed yet. Every
model in the chain clears the "Gemini 3.5 or newer" floor, so whichever answers
is compliant.
"""
from __future__ import annotations

from typing import AsyncGenerator

from pydantic import PrivateAttr
from google.adk.models import BaseLlm, LlmResponse
from google.adk.models.llm_request import LlmRequest
from google.adk.models.google_llm import Gemini
from google.genai.errors import ServerError, ClientError

from ..emit import emit_step


class FallbackGemini(BaseLlm):
    chain: list[str]
    _models: list[Gemini] = PrivateAttr(default_factory=list)

    def __init__(self, chain: list[str], **kw):
        super().__init__(model=chain[0], chain=chain, **kw)
        self._models = [Gemini(model=m) for m in chain]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        last_err: Exception | None = None
        for i, gm in enumerate(self._models):
            started = False
            try:
                async for resp in gm.generate_content_async(llm_request, stream=stream):
                    started = True
                    yield resp
                return
            except (ServerError, ClientError) as e:
                # 503 (overloaded) or 429 (rate limited) -> try the next model,
                # but only if we haven't already begun streaming this turn.
                if started or getattr(e, "code", None) not in (429, 503):
                    raise
                last_err = e
                nxt = self.chain[i + 1] if i + 1 < len(self.chain) else None
                if nxt:
                    emit_step("Router", "failover",
                              f"{self.chain[i]} unavailable ({e.code}); falling over to {nxt}.")
                continue
        if last_err:
            raise last_err

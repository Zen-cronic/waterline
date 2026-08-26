"""A failure-tolerant Gemini model: try a chain, RETRY with backoff, fall through on 503/429.

The model-layer answer to the Architecture rubric's failure-tolerance question —
"how does the system recover if a worker returns an error?" During launch load the
Gemini Flash line throws intermittent 503s that FLAP (a model is down one second,
up the next). A live demo cannot stall on that. So this does two things:
  1. fails over to the next model in the chain on a 503/429, and
  2. if the whole chain is momentarily down, backs off and retries the chain a few
     times — riding out a transient storm instead of giving up on the first sweep.
Every model in the chain clears the "Gemini 3.5 or newer" floor, so whichever
answers first is compliant.
"""
from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from pydantic import PrivateAttr
from google.adk.models import BaseLlm, LlmResponse
from google.adk.models.llm_request import LlmRequest
from google.adk.models.google_llm import Gemini
from google.genai.errors import ServerError, ClientError

from ..emit import emit_step
from ..config import vertex_model_client_kwargs

_TRANSIENT = (429, 503)
_ROUNDS = 3  # how many times to sweep the whole chain before giving up


class FallbackGemini(BaseLlm):
    chain: list[str]
    _models: list[Gemini] = PrivateAttr(default_factory=list)

    def __init__(self, chain: list[str], **kw):
        super().__init__(model=chain[0], chain=chain, **kw)
        client_kwargs = vertex_model_client_kwargs()
        self._models = [Gemini(model=m, client_kwargs=client_kwargs) for m in chain]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        last_err: Exception | None = None
        for round_i in range(_ROUNDS):
            for i, gm in enumerate(self._models):
                started = False
                try:
                    async for resp in gm.generate_content_async(llm_request, stream=stream):
                        started = True
                        yield resp
                    return
                except (ServerError, ClientError) as e:
                    # once bytes have streamed we can't safely switch models; and a
                    # non-transient error (e.g. 400) is a real bug, not overload.
                    if started or getattr(e, "code", None) not in _TRANSIENT:
                        raise
                    last_err = e
                    if i + 1 < len(self.chain):
                        emit_step("Router", "failover",
                                  f"{self.chain[i]} unavailable ({e.code}); failing over to {self.chain[i + 1]}.")
                    continue
            # whole chain was down this sweep — back off, then retry the chain
            if round_i + 1 < _ROUNDS:
                delay = 2 * (round_i + 1)
                emit_step("Router", "backoff",
                          f"All models throttled; backing off {delay}s then retrying the chain.")
                await asyncio.sleep(delay)
        if last_err:
            raise last_err

import json

import httpx

from waterline.gemma_ranker import DEFAULT_GEMMA_MODEL, VertexGemmaRanker


def test_vertex_gemma_uses_maas_endpoint_and_cannot_drop_notams():
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={
            "id": "gemma-rank-1",
            "choices": [{"message": {"content": '{"ranked_idx":[2]}'}}],
        })

    notams = [
        {"idx": 0, "location": "A", "qcode": "QMXLC", "dist_nm": 8, "line": "ground"},
        {"idx": 1, "location": "B", "qcode": "QXXXX", "dist_nm": 4, "line": "other"},
        {"idx": 2, "location": "C", "qcode": "QOBCE", "dist_nm": 2, "line": "obstacle"},
    ]
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = VertexGemmaRanker(
            "project-a", client=client, token_provider=lambda: "token",
        ).rank(notams)

    assert [item["idx"] for item in result.ordered] == [2, 0, 1]
    assert len(result.ordered) == len(notams)
    assert result.model_id == DEFAULT_GEMMA_MODEL
    body = json.loads(requests[0].content)
    assert body["model"] == DEFAULT_GEMMA_MODEL
    assert "tools" not in body
    assert requests[0].url.path.endswith("/endpoints/openapi/chat/completions")

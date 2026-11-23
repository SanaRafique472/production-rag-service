"""Contract tests for the live provider clients, with HTTP mocked at the transport level.

The real Anthropic and Cohere SDKs build the requests and parse the responses;
only the network is replaced. This checks our request shapes (search_result
blocks with citations, input types, dimensions) and our parsing, without keys.
It does NOT prove answer quality - that needs a live evaluation run.
"""

import json

import anthropic
import cohere
import httpx
import httpx2  # the Anthropic SDK uses httpx2, Cohere uses httpx
import pytest

from app.clients.embeddings import CohereEmbedder
from app.clients.llm import ClaudeLLM, SearchItem
from app.clients.reranker import CohereReranker
from app.config import Settings
from app.errors import UpstreamRateLimitedError, UpstreamUnavailableError

FAKE_KEY = "test-key-not-real-0000"


def settings() -> Settings:
    return Settings(_env_file=None, anthropic_api_key=FAKE_KEY, cohere_api_key=FAKE_KEY, max_retries=2)


def mock(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def mock2(handler) -> httpx2.AsyncClient:
    def wrapped(request: httpx2.Request) -> httpx2.Response:
        r = handler(request)
        return httpx2.Response(r.status_code, content=r.content, headers={"content-type": "application/json"})

    return httpx2.AsyncClient(transport=httpx2.MockTransport(wrapped))


async def test_claude_sends_search_results_and_parses_citations():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [
                    {
                        "type": "text",
                        "text": "Employees get 12 paid sick days per year.",
                        "citations": [
                            {
                                "type": "search_result_location",
                                "cited_text": "Employees are entitled to 12 paid sick days per year.",
                                "search_result_index": 0,
                                "source": "c0",
                                "title": "a.pdf, p. 2",
                                "start_block_index": 0,
                                "end_block_index": 0,
                            }
                        ],
                    },
                    {"type": "text", "text": " Nothing else.", "citations": None},
                ],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 120, "output_tokens": 15},
            },
        )

    llm = ClaudeLLM(settings())
    llm._client = anthropic.AsyncAnthropic(api_key=FAKE_KEY, http_client=mock2(handler), max_retries=0)
    out = await llm.answer(
        "system",
        [SearchItem("c0", "a.pdf, p. 2", "Employees are entitled to 12 paid sick days per year.")],
        "sick days?",
    )

    block = seen["body"]["messages"][0]["content"][0]
    assert block["type"] == "search_result" and block["citations"] == {"enabled": True} and block["source"] == "c0"
    assert "temperature" not in seen["body"] and seen["body"]["system"] == "system"  # removed in API 2026-10-21
    assert out.tokens_in == 120 and out.tokens_out == 15 and len(out.segments) == 2
    assert out.segments[0].citations[0].index == 0 and "12 paid sick days" in out.segments[0].citations[0].cited_text
    assert out.segments[1].citations == []


async def test_claude_rate_limit_and_auth_errors():
    calls = {"n": 0}

    def limited(_r):
        calls["n"] += 1
        return httpx.Response(
            429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
        )

    llm = ClaudeLLM(settings())
    llm._client = anthropic.AsyncAnthropic(api_key=FAKE_KEY, http_client=mock2(limited), max_retries=0)
    with pytest.raises(UpstreamRateLimitedError):
        await llm.answer("s", [], "q")
    assert calls["n"] == 2  # retried once (max_retries=2 attempts in total)

    calls["n"] = 0

    def unauthorized(_r):
        calls["n"] += 1
        return httpx.Response(
            401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
        )

    llm._client = anthropic.AsyncAnthropic(api_key=FAKE_KEY, http_client=mock2(unauthorized), max_retries=0)
    with pytest.raises(UpstreamUnavailableError) as exc:
        await llm.answer("s", [], "q")
    assert calls["n"] == 1 and FAKE_KEY not in exc.value.message  # fatal: no retry, key never echoed


async def test_cohere_embed_request_and_parsing():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        vecs = [[0.1] * body["output_dimension"] for _ in body["texts"]]
        return httpx.Response(
            200,
            json={
                "id": "e1",
                "embeddings": {"float": vecs},
                "texts": body["texts"],
                "response_type": "embeddings_by_type",
                "meta": {},
            },
        )

    s = settings()
    emb = CohereEmbedder(s)
    emb._client = cohere.AsyncClientV2(api_key=FAKE_KEY, httpx_client=mock(handler))
    docs = await emb.embed_documents([f"chunk {i}" for i in range(s.embed_batch_size + 5)])
    query = await emb.embed_query("question")
    assert len(docs) == s.embed_batch_size + 5 and len(docs[0]) == s.embed_dim and len(query) == s.embed_dim
    assert {b["input_type"] for b in seen} == {"search_document", "search_query"}
    assert seen[-1]["model"] == s.embed_model_query and seen[0]["model"] == s.embed_model_index
    assert len(seen) == 3  # two document batches + one query


async def test_cohere_rerank_parsing():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["top_n"] == len(body["documents"])
        return httpx.Response(
            200,
            json={
                "id": "r1",
                "results": [{"index": 1, "relevance_score": 0.91}, {"index": 0, "relevance_score": 0.08}],
                "meta": {},
            },
        )

    rr = CohereReranker(settings())
    rr._client = cohere.AsyncClientV2(api_key=FAKE_KEY, httpx_client=mock(handler))
    assert await rr.rerank("q", ["a", "b"]) == [(1, 0.91), (0, 0.08)]


async def test_openai_judge_contract():
    from evals.judge import Judge

    def handler(request):
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"} and body["messages"][0]["role"] == "system"
        content = json.dumps(
            {"faithfulness": 5, "answer_relevancy": 4, "correctness": 5, "context_precision": 3, "reason": "ok"}
        )
        return httpx2.Response(
            200,
            json={
                "id": "c1",
                "object": "chat.completion",
                "created": 0,
                "model": "judge",
                "choices": [
                    {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    import openai

    judge = Judge(Settings(_env_file=None, openai_api_key=FAKE_KEY, judge_provider="openai"))
    try:
        judge._client = openai.AsyncOpenAI(
            api_key=FAKE_KEY, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        )
    except TypeError:  # SDK still on httpx
        judge._client = openai.AsyncOpenAI(
            api_key=FAKE_KEY, http_client=mock(lambda r: httpx.Response(200, content=handler(r).content))
        )
    scores = await judge.grade("q", "a [1]", ["ctx"], "ref")
    assert scores["faithfulness"] == 1.0 and scores["answer_relevancy"] == 0.75 and scores["context_precision"] == 0.5


def cohere_chat_response(text: str, citations: list[dict], thinking: str = "I checked the documents.") -> dict:
    return {
        "id": "chat1",
        "finish_reason": "COMPLETE",
        "message": {
            "role": "assistant",
            "content": [{"type": "thinking", "thinking": thinking}, {"type": "text", "text": text}],
            "citations": citations,
        },
        "usage": {
            "billed_units": {"input_tokens": 30, "output_tokens": 20},
            "tokens": {"input_tokens": 700, "output_tokens": 120},
        },
    }


def cite(text: str, span: str, doc_id: str, doc_text: str) -> dict:
    start = text.index(span)
    return {
        "start": start,
        "end": start + len(span),
        "text": span,
        "content_index": 1,
        "type": "TEXT_CONTENT",
        "sources": [{"type": "document", "id": doc_id, "document": {"id": doc_id, "text": doc_text}}],
    }


async def test_cohere_llm_sends_documents_and_maps_citations():
    from app.clients.llm import CohereLLM
    from app.generation import Generator
    from app.retrieval import RetrievedChunk

    chunk_text = "Employees are entitled to 12 paid sick days per year."
    answer = "Employees get **12 paid sick days per year** (employee_handbook_2026.pdf, p. 2)."
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=cohere_chat_response(
                answer,
                [
                    cite(answer, "12 paid sick days per year", "0", chunk_text),
                    cite(answer, " (employee_handbook_2026.pdf, p. 2)", "0", chunk_text),
                    cite(answer, "Employees get", "7", "not a chunk we sent"),
                ],
            ),
        )

    llm = CohereLLM(settings())
    llm._client = cohere.AsyncClientV2(api_key=FAKE_KEY, httpx_client=mock(handler))
    chunk = RetrievedChunk(
        "c0", "d1", "employee_handbook_2026.pdf", 3, 2, 2, "Leave", chunk_text, "text", False, kept=True
    )
    result = await Generator(llm, "CANARY-x").generate("sick days?", [chunk], "en")

    body = seen["body"]
    assert body["model"] == "command-a-plus-05-2026" and body["documents"][0]["id"] == "0"
    assert body["documents"][0]["data"]["text"] == chunk_text and body["messages"][0]["role"] == "system"
    assert (
        not result.refused and result.answer == "Employees get 12 paid sick days per year [1]."
    )  # no ** and no file name
    assert result.invalid_citations == 1  # the citation pointing at document 7 (never sent) is dropped
    assert result.citations[0].chunk_id == "c0" and result.tokens_in == 700


async def test_cohere_llm_refusal_sentinel_and_rate_limit():
    from app.clients.llm import REFUSAL_SENTINEL, CohereLLM

    llm = CohereLLM(settings())
    llm._client = cohere.AsyncClientV2(
        api_key=FAKE_KEY,
        httpx_client=mock(lambda r: httpx.Response(200, json=cohere_chat_response(REFUSAL_SENTINEL, []))),
    )
    out = await llm.answer("s", [], "q")
    assert out.text == REFUSAL_SENTINEL

    llm._client = cohere.AsyncClientV2(
        api_key=FAKE_KEY, httpx_client=mock(lambda r: httpx.Response(429, json={"message": "trial key rate limit"}))
    )
    with pytest.raises(UpstreamRateLimitedError):
        await llm.answer("s", [], "q")


async def test_cohere_judge_contract():
    from evals.judge import Judge

    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "command-a-03-2025" and body["response_format"] == {"type": "json_object"}
        content = json.dumps(
            {"faithfulness": 5, "answer_relevancy": 5, "correctness": 3, "context_precision": 1, "reason": "ok"}
        )
        return httpx.Response(
            200,
            json={
                "id": "j",
                "finish_reason": "COMPLETE",
                "message": {"role": "assistant", "content": [{"type": "text", "text": content}]},
            },
        )

    judge = Judge(settings())
    judge._client = cohere.AsyncClientV2(api_key=FAKE_KEY, httpx_client=mock(handler))
    scores = await judge.grade("q", "a", ["ctx"], "ref")
    assert scores["faithfulness"] == 1.0 and scores["correctness"] == 0.5 and scores["context_precision"] == 0.0


async def test_cohere_leaked_citation_tags_and_bracket_file_names():
    from app.clients.llm import CohereLLM, SearchItem

    items = [SearchItem("u0", "office_notice_urdu.docx", "کراچی دفتر کے نئے اوقات کار صبح 9 بجے سے شام 5 بجے تک ہیں۔")]
    llm = CohereLLM(settings())

    leaked = "کراچی دفتر کے اوقات کار صبح 9 بجے سے شام 5 بجے تک ہیں۔<co: 0:[0]>"
    llm._client = cohere.AsyncClientV2(
        api_key=FAKE_KEY, httpx_client=mock(lambda r: httpx.Response(200, json=cohere_chat_response(leaked, [])))
    )
    out = await llm.answer("s", items, "q")
    assert "<co" not in out.text and out.segments[0].citations[0].index == 0  # tag turned into a real citation

    bracket = "Office hours are 9 to 5 [office_notice_urdu.docx, اوقات کار]."
    llm._client = cohere.AsyncClientV2(
        api_key=FAKE_KEY,
        httpx_client=mock(
            lambda r: httpx.Response(
                200, json=cohere_chat_response(bracket, [cite(bracket, "9 to 5", "0", items[0].text)])
            )
        ),
    )
    out = await llm.answer("s", items, "q")
    assert out.text == "Office hours are 9 to 5." and out.segments[0].citations[0].index == 0

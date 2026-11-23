"""LLM-as-judge for faithfulness, answer relevancy, correctness and context precision.

By default the judge is Cohere Command A, a different (older, non-reasoning)
model from the answer model Command A+, run with the same free trial key. With
JUDGE_PROVIDER=openai it is an OpenAI model, a different vendor, which removes
any same-vendor preference. Scores are 1-5 and reported normalised to 0-1.
If the judge's key is missing these metrics are reported as null - never estimated.

Validate the judge: label ~20 answers by hand in human_labels.csv and run
``python -m evals.agreement`` to get Cohen's kappa between you and the judge.
"""

import json

from app.clients.resilience import call_external
from app.config import Settings

RUBRIC = """You grade a question-answering system that must answer ONLY from the provided context.
Return JSON with integer scores 1-5 and a one-sentence reason:
{"faithfulness": n, "answer_relevancy": n, "correctness": n, "context_precision": n, "reason": "..."}

faithfulness: every claim in the answer is supported by the context (5 = fully supported, 1 = mostly invented).
answer_relevancy: the answer addresses the question asked (5 = directly and completely).
correctness: the answer agrees with the reference answer (5 = same facts, 1 = wrong or missing).
context_precision: share of context passages that are useful for answering (5 = all useful, 1 = none).
The user message says whether the documents contain the answer ("Expected: answer") or not ("Expected: refuse").
A refusal is correct (5) only when Expected is "refuse"; a refusal when Expected is "answer" is wrong (correctness 1).
Score faithfulness 5 for a refusal."""


class Judge:
    """LLM grader: Cohere Command A (default, free with the same trial key) or OpenAI."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self.provider = settings.judge_provider
        if self.provider == "cohere":
            import cohere

            self.model = settings.cohere_judge_model
            self._client = cohere.AsyncClientV2(
                api_key=settings.cohere_api_key.get_secret_value(), timeout=settings.http_timeout_s
            )
        else:
            from openai import AsyncOpenAI

            self.model = settings.judge_model
            self._client = AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value(), timeout=settings.http_timeout_s
            )

    async def _complete(self, prompt: str) -> str:
        messages = [{"role": "system", "content": RUBRIC}, {"role": "user", "content": prompt}]
        if self.provider == "cohere":
            resp = await self._client.chat(
                model=self.model, messages=messages, response_format={"type": "json_object"}, max_tokens=300
            )
            return "".join(getattr(c, "text", "") for c in (resp.message.content or []))
        resp = await self._client.chat.completions.create(
            model=self.model, messages=messages, response_format={"type": "json_object"}
        )
        return resp.choices[0].message.content or "{}"

    async def grade(
        self, question: str, answer: str, contexts: list[str], reference: str, answerable: bool | None = None
    ) -> dict:
        """Return normalised 0-1 scores (or None values if the judge reply was unusable)."""
        ctx = "\n\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts)) or "(no context retrieved)"
        expected = "" if answerable is None else f"\n\nExpected: {'answer' if answerable else 'refuse'}"
        prompt = (
            f"Question: {question}\n\nContext:\n{ctx}\n\nAnswer: {answer}\n\nReference answer: {reference}{expected}"
        )
        raw = await call_external(
            f"{self.provider} judge",
            lambda: self._complete(prompt),
            attempts=self._s.max_retries,
            timeout_s=self._s.http_timeout_s * 2,
        )
        try:
            data = json.loads(raw)
            scores = {
                k: (int(data[k]) - 1) / 4
                for k in ("faithfulness", "answer_relevancy", "correctness", "context_precision")
            }
            return {**scores, "judge_reason": str(data.get("reason", ""))[:300]}
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            return {
                "faithfulness": None,
                "answer_relevancy": None,
                "correctness": None,
                "context_precision": None,
                "judge_reason": "unparseable judge reply",
            }

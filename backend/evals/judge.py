"""LLM-as-judge for faithfulness, answer relevancy, correctness and context precision.

The judge is a different model family (OpenAI) from the generator (Claude) to
reduce self-preference bias. Scores are 1-5 and reported normalised to 0-1.
The judge only runs when OPENAI_API_KEY is set; otherwise these metrics are
reported as "not computed" - never estimated.

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
If the answer is a refusal, score faithfulness 5 and judge correctness against whether a refusal was right."""


class Judge:
    """OpenAI-based grader."""

    def __init__(self, settings: Settings) -> None:
        from openai import AsyncOpenAI

        self.model = settings.judge_model
        self._s = settings
        self._client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=settings.http_timeout_s)

    async def grade(self, question: str, answer: str, contexts: list[str], reference: str) -> dict:
        """Return normalised 0-1 scores (or None values if the judge reply was unusable)."""
        ctx = "\n\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts)) or "(no context retrieved)"
        prompt = f"Question: {question}\n\nContext:\n{ctx}\n\nAnswer: {answer}\n\nReference answer: {reference}"
        resp = await call_external(
            "OpenAI judge",
            lambda: self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": RUBRIC}, {"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            ),
            attempts=self._s.max_retries,
            timeout_s=self._s.http_timeout_s * 2,
        )
        try:
            data = json.loads(resp.choices[0].message.content or "{}")
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

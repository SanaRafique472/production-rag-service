"""Input, output and document guardrails.

Defence in depth: fast pattern checks here, an LLM classifier run in parallel
with retrieval (see ``services/chat_service.py``), prompt isolation in
``generation.py``, and output checks before anything is returned.
"""

import re
import secrets

from app.errors import InvalidQuestionError, JailbreakError, PromptInjectionError

# English, Urdu and Arabic phrasings of "ignore your instructions / reveal your prompt".
_INJECTION = [
    r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|earlier|all|your|system)\b.{0,30}\b(instructions?|rules?|prompts?|directions?)\b",
    r"\b(reveal|show|print|repeat|output|display|leak|tell me)\b.{0,40}\b(system prompt|hidden prompt|initial prompt|instructions you were given|your (rules|instructions|prompt))\b",
    r"\b(api[_ ]?key|secret key|access token|password)s?\b.{0,40}\b(reveal|show|print|give|tell|what is)\b",
    r"\b(reveal|show|print|give|tell|what is)\b.{0,40}\b(api[_ ]?key|secret key|access token|env(ironment)? variables?)\b",
    r"\bnew instructions?\s*:",
    r"</?\s*(system|assistant|instructions?)\s*>",
    r"(پچھلی|سابقہ|تمام)\s+ہدایات\s+(کو\s+)?(نظر\s*انداز|بھول)",
    r"سسٹم\s*پرامپٹ",
    r"تجاهل\s+(جميع\s+)?(التعليمات|الأوامر)",
    r"(موجه|تعليمات)\s+النظام",
]
_JAILBREAK = [
    r"\b(DAN|do anything now|developer mode|jailbreak|jail-break)\b",
    r"\b(pretend|act|behave|roleplay|role-play)\b.{0,40}\b(no|without)\b.{0,20}\b(rules|restrictions|limits|filters|guidelines)\b",
    r"\byou are (now )?(no longer|not) (bound|restricted|limited)\b",
    r"\b(unfiltered|uncensored|unrestricted)\s+(ai|assistant|mode|model)\b",
    r"بغیر\s+(کسی\s+)?(پابندی|اصول)",
]
_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in _INJECTION]
_JAILBREAK_RE = [re.compile(p, re.IGNORECASE) for p in _JAILBREAK]

# Instructions planted inside documents (Bonus F): flagged and shown as a warning.
_DOC_INSTRUCTION_RE = [
    *_INJECTION_RE,
    re.compile(
        r"\b(assistant|AI|chatbot|model|LLM)\b.{0,30}\b(must|should|always)\b.{0,30}\b(answer|reply|say|respond)\b",
        re.IGNORECASE,
    ),
]

_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWTs
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{16,}"),
]


def check_question(question: str) -> str:
    """Validate a question; return it trimmed or raise a guardrail error."""
    text = question.strip()
    if not text:
        raise InvalidQuestionError("The question is empty.")
    if any(p.search(text) for p in _JAILBREAK_RE):
        raise JailbreakError("This request tries to bypass the assistant's rules and was blocked.")
    if any(p.search(text) for p in _INJECTION_RE):
        raise PromptInjectionError(
            "This request tries to change or reveal the assistant's instructions and was blocked."
        )
    return text


def scan_document_text(text: str) -> bool:
    """Return True when a chunk contains text that looks like instructions to an AI."""
    return any(p.search(text) for p in _DOC_INSTRUCTION_RE)


def make_canary() -> str:
    """Random marker placed in the system prompt; seeing it in output means a leak."""
    return f"CANARY-{secrets.token_hex(8)}"


def redact_secrets(text: str) -> str:
    """Replace anything that looks like a key or token with [REDACTED]."""
    for p in _SECRET_PATTERNS:
        text = p.sub("[REDACTED]", text)
    return text


def check_output(answer: str, canary: str, system_prompt: str) -> str | None:
    """Return a reason string if the answer leaks the prompt or a secret, else None."""
    if canary in answer:
        return "system_prompt_leak"
    if any(p.search(answer) for p in _SECRET_PATTERNS):
        return "secret_leak"
    normal = " ".join(answer.split()).lower()
    prompt = " ".join(system_prompt.replace(canary, "").split()).lower()
    window = 80
    for i in range(0, max(len(prompt) - window, 0), 20):  # long verbatim overlap with the prompt
        if prompt[i : i + window] in normal:
            return "system_prompt_leak"
    return None

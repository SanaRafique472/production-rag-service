"""Aggregate request metrics for /metrics and the monitoring dashboard.

Percentiles use the nearest-rank method: sort the values and take the value at
position ceil(p x n). p50 is the typical request; p95 shows the slow tail.
"""

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RequestMetric

ASK_ROUTE = "/chat/ask"


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (p between 0 and 100)."""
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[max(math.ceil(p / 100 * len(ordered)) - 1, 0)])


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


async def summary(db: AsyncSession, hours: int) -> dict:
    """Compute dashboard metrics for the last ``hours`` hours."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = list(await db.scalars(select(RequestMetric).where(RequestMetric.created_at >= since)))
    asks = [r for r in rows if r.route == ASK_ROUTE and r.status_code == 200]

    by_route: dict[str, dict[str, float]] = {}
    grouped: dict[str, list[RequestMetric]] = defaultdict(list)
    for r in rows:
        grouped[f"{r.method} {r.route}"].append(r)
    for key, items in grouped.items():
        lat = [i.latency_ms for i in items]
        by_route[key] = {
            "count": len(items),
            "p50_ms": percentile(lat, 50) or 0,
            "p95_ms": percentile(lat, 95) or 0,
            "errors": sum(1 for i in items if i.status_code >= 500),
        }

    buckets: dict[str, list[RequestMetric]] = defaultdict(list)
    for r in asks:
        created = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=UTC)
        buckets[created.strftime("%Y-%m-%dT%H:00")].append(r)
    timeline = [
        {
            "hour": hour,
            "questions": len(items),
            "p50_ms": percentile([i.latency_ms for i in items], 50),
            "p95_ms": percentile([i.latency_ms for i in items], 95),
            "cost_usd": round(sum(i.cost_usd for i in items), 6),
        }
        for hour, items in sorted(buckets.items())
    ]
    latencies = [r.latency_ms for r in asks]
    return {
        "window_hours": hours,
        "requests": len(rows),
        "error_rate": round(sum(1 for r in rows if r.status_code >= 500) / len(rows), 4) if rows else 0.0,
        "rate_limited": sum(1 for r in rows if r.status_code == 429),
        "questions": len(asks),
        "refusal_rate": round(sum(1 for r in asks if r.refused) / len(asks), 4) if asks else None,
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
        "step_p50_ms": {
            step: percentile([getattr(r, step) for r in asks if getattr(r, step) is not None], 50)
            for step in ("embed_ms", "search_ms", "rerank_ms", "llm_ms")
        },
        "avg_tokens_in": _mean([r.tokens_in for r in asks]),
        "avg_tokens_out": _mean([r.tokens_out for r in asks]),
        "avg_cost_usd": _mean([r.cost_usd for r in asks]),
        "total_cost_usd": round(sum(r.cost_usd for r in rows), 6),
        "by_route": by_route,
        "timeline": timeline,
    }

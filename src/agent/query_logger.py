"""Query logger — writes JSONL records to data/query_logs.jsonl."""
import json
import time
from pathlib import Path

_LOG_PATH = Path("data/query_logs.jsonl")


class QueryLogger:
    def log(
        self,
        query: str,
        route: str,
        latency_s: float,
        num_chunks: int = 0,
        top_score: float | None = None,
    ) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "query": query[:300],
            "route": route,
            "latency_ms": round(latency_s * 1000),
            "num_chunks": num_chunks,
            "top_score": round(top_score, 3) if top_score is not None else None,
        }
        try:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass

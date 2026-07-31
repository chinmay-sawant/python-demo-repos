import random
import statistics
import time
from datetime import UTC, datetime

from app.schemas import IngestRequest
from app.services.ingest import normalize_route

N_REPEATS = 5


def bench(label, fn, n):
    samples = []
    for _ in range(N_REPEATS):
        t0 = time.perf_counter()
        fn()
        dt = (time.perf_counter() - t0) / n
        samples.append(dt)
    return (
        f"{statistics.median(samples) * 1e6:10.1f} us/op  "
        f"(median of {N_REPEATS}, "
        f"p95={statistics.quantiles(samples, n=20)[18] * 1e6:.1f} us)"
    )


LABELS = [f"/api/orders/{i % 500}/items/{i % 20}?trace={i}" for i in range(100_000)]

print("== FastAPI CPU hot-path microbenchmarks (2026-07-31) ==")
print(
    "normalize_route 100k labels : "
    f"{bench('route', lambda: [normalize_route(label) for label in LABELS], 100_000)}"
)

payload = {
    "idempotency_key": "bench",
    "samples": [
        {
            "route_label": f"/api/orders/{i % 50}/items/{i % 7}",
            "latency_ms": random.random() * 500,
            "status_code": 200,
            "ua_class": "bench",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        for i in range(100)
    ],
}


def validate():
    IngestRequest.model_validate(payload)


print(f"pydantic validate 100-sample : {bench('pyd', validate, 1)}")

for n in (100_000, 1_000_000):
    vals = [random.random() * 1000 for _ in range(n)]

    def sort_it(vals=vals):
        s = sorted(vals)
        return s[len(s) // 2], s[int(len(s) * 0.95)], s[int(len(s) * 0.99)]

    print(f"app-side percentile sort {n:<9}: {bench('sort', sort_it, 1)}")

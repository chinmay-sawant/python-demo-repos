import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  scenarios: {
    ingest: {
      executor: "constant-arrival-rate",
      rate: 300,
      timeUnit: "1s",
      duration: "20s",
      preAllocatedVUs: 50,
      maxVUs: 100,
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
  },
};

export default function () {
  const body = {
    idempotency_key: `bench-${__ITER}-${__VU}`,
    samples: Array.from({ length: 100 }, (_, i) => ({
      route_label: `/api/orders/${i % 50}/items/${i % 7}`,
      latency_ms: Math.round(Math.random() * 5000) / 10,
      status_code: Math.random() < 0.05 ? 500 : 200,
      ua_class: "bench",
      timestamp: new Date().toISOString(),
    })),
  };
  const res = http.post(
    "http://127.0.0.1:8101/api/v1/ingest",
    JSON.stringify(body),
    { headers: { "Content-Type": "application/json", "X-Tenant-Id": "1" } }
  );
  check(res, { "200 ok": (r) => r.status === 200 });
}

import http from "k6/http";
import { check } from "k6";

export const options = {
  scenarios: {
    ingest: {
      executor: "constant-arrival-rate",
      rate: 200,
      timeUnit: "1s",
      duration: "20s",
      preAllocatedVUs: 50,
      maxVUs: 100,
    },
  },
  thresholds: { http_req_failed: ["rate<0.01"] },
};

export default function () {
  const body = {
    event_type: "order.created",
    payload: { order_id: `${__ITER}`, items: [{ sku: "SKU001", qty: 2 }] },
    idempotency_key: `bench-${__ITER}-${__VU}`,
  };
  const res = http.post(
    "http://127.0.0.1:8103/api/v1/webhooks",
    JSON.stringify(body),
    { headers: { "Content-Type": "application/json", "X-Api-Key": "dev-api-key" } }
  );
  check(res, { "201 ok": (r) => r.status === 201 });
}

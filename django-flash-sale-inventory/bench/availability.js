import http from "k6/http";
import { check } from "k6";

export const options = {
  scenarios: {
    batch: {
      executor: "constant-arrival-rate",
      rate: 300,
      timeUnit: "1s",
      duration: "20s",
      preAllocatedVUs: 50,
      maxVUs: 100,
    },
  },
  thresholds: { http_req_failed: ["rate<0.01"] },
};

export default function () {
  const res = http.post(
    "http://127.0.0.1:8102/api/availability/batch/",
    JSON.stringify({ sku_codes: Array.from({ length: 10 }, (_, i) => `SKU${String((i + __ITER) % 20 + 1).padStart(3, "0")}`) }),
    { headers: { "Content-Type": "application/json" } }
  );
  check(res, { "200 ok": (r) => r.status === 200 });
}

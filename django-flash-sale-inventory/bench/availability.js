import http from "k6/http";
import { check } from "k6";

const BASE = "http://127.0.0.1:8102";

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

export function setup() {
  const getRes = http.get(`${BASE}/api/skus/SKU001/availability/`);
  const cookies = getRes.cookies["csrftoken"];
  return { csrf: cookies && cookies[0] ? cookies[0].value : "" };
}

export default function (data) {
  http.cookieJar().set(BASE, "csrftoken", data.csrf);
  const res = http.post(
    `${BASE}/api/availability/batch/`,
    JSON.stringify({ sku_codes: Array.from({ length: 10 }, (_, i) => `SKU${String((i + __ITER) % 20 + 1).padStart(3, "0")}`) }),
    { headers: { "Content-Type": "application/json", "X-CSRFToken": data.csrf } }
  );
  check(res, { "200 ok": (r) => r.status === 200 });
}

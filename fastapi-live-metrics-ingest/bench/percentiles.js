import http from "k6/http";
import { check } from "k6";

export const options = {
  scenarios: {
    percentiles: {
      executor: "constant-vus",
      vus: 20,
      duration: "20s",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
  },
};

export default function () {
  const windowStart = new Date(Date.now() - 25 * 3600 * 1000).toISOString();
  const windowEnd = new Date(Date.now() + 3600 * 1000).toISOString();
  const res = http.get(
    `http://127.0.0.1:8101/api/v1/tenants/1/percentiles?window_start=${windowStart}&window_end=${windowEnd}`,
    { headers: { "X-Tenant-Id": "1" } }
  );
  check(res, { "200 ok": (r) => r.status === 200 });
}

import http from "k6/http";
import { check } from "k6";

export const options = {
  scenarios: {
    rollup: {
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
  const res = http.get(`http://127.0.0.1:8102/api/warehouses/WH${(__ITER % 3) + 1}/rollup/`);
  check(res, { "200 ok": (r) => r.status === 200 });
}

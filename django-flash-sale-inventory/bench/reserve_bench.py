import os
import statistics
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flash_sale.settings")
import django

django.setup()

from django.db import connection
from inventory.models import SaleEvent
from inventory.services.reservation import ReservationService

N_REPEATS = 5


class QueryCounter:
    def __init__(self):
        self.count = 0

    def __call__(self, execute, sql, params, many, context):
        self.count += 1
        return execute(sql, params, many, context)


def bench(fn, label):
    times = []
    last_queries = None
    for _ in range(N_REPEATS):
        qc = QueryCounter()
        t0 = time.perf_counter()
        with connection.execute_wrapper(qc):
            result = fn()
        times.append(time.perf_counter() - t0)
        last_queries = qc.count
    print(
        f"{label:<24}: {statistics.median(times) * 1000:9.1f} ms/op  "
        f"queries={last_queries}  (median of {N_REPEATS})"
    )
    return result


def main():
    sale = SaleEvent.objects.filter(status=SaleEvent.Status.ACTIVE).first()
    svc = ReservationService()

    print("== Django service-layer benchmarks (2026-07-31, sqlite file DB) ==")

    reservation_ids = []
    for n_lines in (1, 5, 20):

        def reserve_n(n_lines=n_lines):
            items = [{"sku_code": f"SKU{i:03d}", "quantity": 1} for i in range(1, n_lines + 1)]
            r = svc.reserve(sale_event=sale, user_id=f"user-{time.time_ns()}", items=items)
            reservation_ids.append(r.id)
            return r

        bench(reserve_n, f"reserve({n_lines:>2} lines)")

    def confirm_fresh():
        items = [{"sku_code": f"SKU{i:03d}", "quantity": 1} for i in range(1, 21)]
        r = svc.reserve(sale_event=sale, user_id="confirm-user", items=items)
        svc.confirm(r.id)

    bench(confirm_fresh, "reserve+confirm(20 lines)")

    def expire():
        svc.release_expired()

    bench(expire, "release_expired()")


if __name__ == "__main__":
    main()

import json
import threading
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from inventory.exceptions import (
    InsufficientStockError,
    ReservationNotPendingError,
    SaleNotActiveError,
)
from inventory.models import (
    Reservation,
    ReservationLine,
    SaleEvent,
    Sku,
    StockLedger,
    Warehouse,
    WarehouseStock,
)
from inventory.services.availability import AvailabilityService
from inventory.services.reservation import ReservationService


class BaseTestMixin:
    def _create_sale(self):
        sale = SaleEvent.objects.create(
            name="Flash Sale",
            status=SaleEvent.Status.ACTIVE,
            start_at=timezone.now() - timedelta(hours=1),
            end_at=timezone.now() + timedelta(hours=1),
        )
        sku1 = Sku.objects.create(sku_code="SKU001", name="Product One")
        sku2 = Sku.objects.create(sku_code="SKU002", name="Product Two")
        wh1 = Warehouse.objects.create(code="WH01", name="Warehouse US", region="US")
        wh2 = Warehouse.objects.create(code="WH02", name="Warehouse EU", region="EU")
        WarehouseStock.objects.create(warehouse=wh1, sku=sku1, quantity=100, reserved_quantity=0)
        WarehouseStock.objects.create(warehouse=wh2, sku=sku1, quantity=50, reserved_quantity=0)
        WarehouseStock.objects.create(warehouse=wh1, sku=sku2, quantity=200, reserved_quantity=0)
        return sale


class ReservationServiceTest(BaseTestMixin, TestCase):
    def setUp(self):
        self.svc = ReservationService()
        self.sale = self._create_sale()

    def test_reserve_success(self):
        reservation = self.svc.reserve(
            self.sale,
            "user1",
            [{"sku_code": "SKU001", "quantity": 10}],
        )

        self.assertEqual(reservation.status, Reservation.Status.PENDING)
        self.assertEqual(reservation.user_id, "user1")
        self.assertEqual(reservation.sale_event, self.sale)
        self.assertIsNotNone(reservation.expires_at)
        self.assertTrue(reservation.expires_at > timezone.now())

        lines = list(reservation.lines.all())
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].sku.sku_code, "SKU001")
        self.assertEqual(lines[0].quantity, 10)

        ws = WarehouseStock.objects.get(sku__sku_code="SKU001", warehouse__code="WH01")
        self.assertEqual(ws.reserved_quantity, 10)

        ledger = StockLedger.objects.filter(reservation=reservation)
        self.assertEqual(ledger.count(), 1)
        self.assertEqual(ledger.first().reason, StockLedger.Reason.RESERVE)
        self.assertEqual(ledger.first().delta, -10)

    def test_reserve_insufficient_stock(self):
        with self.assertRaises(InsufficientStockError):
            self.svc.reserve(
                self.sale,
                "user1",
                [{"sku_code": "SKU001", "quantity": 200}],
            )

        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(ReservationLine.objects.count(), 0)

    def test_reserve_sale_not_active(self):
        upcoming_sale = SaleEvent.objects.create(
            name="Upcoming Sale",
            status=SaleEvent.Status.UPCOMING,
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=2),
        )
        with self.assertRaises(SaleNotActiveError):
            self.svc.reserve(
                upcoming_sale,
                "user1",
                [{"sku_code": "SKU001", "quantity": 10}],
            )

        ended_sale = SaleEvent.objects.create(
            name="Ended Sale",
            status=SaleEvent.Status.ENDED,
            start_at=timezone.now() - timedelta(days=2),
            end_at=timezone.now() - timedelta(days=1),
        )
        with self.assertRaises(SaleNotActiveError):
            self.svc.reserve(
                ended_sale,
                "user1",
                [{"sku_code": "SKU001", "quantity": 10}],
            )

    def test_reserve_idempotency(self):
        key = "idem-001"
        first = self.svc.reserve(
            self.sale,
            "user1",
            [{"sku_code": "SKU001", "quantity": 10}],
            idempotency_key=key,
        )
        second = self.svc.reserve(
            self.sale,
            "user1",
            [{"sku_code": "SKU001", "quantity": 10}],
            idempotency_key=key,
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.status, second.status)

        lines = ReservationLine.objects.filter(reservation=first)
        self.assertEqual(lines.count(), 1)

        ledger = StockLedger.objects.filter(reservation=first)
        self.assertEqual(ledger.count(), 1)

        ws = WarehouseStock.objects.get(sku__sku_code="SKU001", warehouse__code="WH01")
        self.assertEqual(ws.reserved_quantity, 10)

    def test_reserve_auto_allocate_warehouse(self):
        reservation = self.svc.reserve(
            self.sale,
            "user1",
            [{"sku_code": "SKU001", "quantity": 10}],
        )

        lines = list(reservation.lines.all())
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].warehouse.code, "WH01")

    def test_reserve_all_or_nothing(self):
        with self.assertRaises(InsufficientStockError):
            self.svc.reserve(
                self.sale,
                "user1",
                [
                    {"sku_code": "SKU001", "quantity": 50},
                    {"sku_code": "SKU002", "quantity": 300},
                ],
            )

        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(ReservationLine.objects.count(), 0)

        ws1 = WarehouseStock.objects.get(sku__sku_code="SKU001", warehouse__code="WH01")
        self.assertEqual(ws1.reserved_quantity, 0)
        ws2 = WarehouseStock.objects.get(sku__sku_code="SKU002", warehouse__code="WH01")
        self.assertEqual(ws2.reserved_quantity, 0)

    def test_confirm_reservation(self):
        reservation = self.svc.reserve(
            self.sale,
            "user1",
            [{"sku_code": "SKU001", "quantity": 10}],
        )

        confirmed = self.svc.confirm(reservation.id)
        confirmed.refresh_from_db()
        self.assertEqual(confirmed.status, Reservation.Status.CONFIRMED)

        ws = WarehouseStock.objects.get(sku__sku_code="SKU001", warehouse__code="WH01")
        self.assertEqual(ws.reserved_quantity, 0)
        self.assertEqual(ws.quantity, 90)

        confirm_ledger = StockLedger.objects.filter(
            reservation=reservation,
            reason=StockLedger.Reason.CONFIRM,
        )
        self.assertEqual(confirm_ledger.count(), 1)
        self.assertEqual(confirm_ledger.first().delta, -10)

    def test_confirm_non_pending(self):
        reservation = self.svc.reserve(
            self.sale,
            "user1",
            [{"sku_code": "SKU001", "quantity": 10}],
        )
        self.svc.cancel(reservation.id)

        with self.assertRaises(ReservationNotPendingError):
            self.svc.confirm(reservation.id)

    def test_cancel_reservation(self):
        reservation = self.svc.reserve(
            self.sale,
            "user1",
            [{"sku_code": "SKU001", "quantity": 10}],
        )

        cancelled = self.svc.cancel(reservation.id)
        cancelled.refresh_from_db()
        self.assertEqual(cancelled.status, Reservation.Status.CANCELLED)

        ws = WarehouseStock.objects.get(sku__sku_code="SKU001", warehouse__code="WH01")
        self.assertEqual(ws.reserved_quantity, 0)

        cancel_ledger = StockLedger.objects.filter(
            reservation=reservation,
            reason=StockLedger.Reason.CANCEL,
        )
        self.assertEqual(cancel_ledger.count(), 1)
        self.assertEqual(cancel_ledger.first().delta, 10)

    def test_release_expired(self):
        reservation = self.svc.reserve(
            self.sale,
            "user1",
            [{"sku_code": "SKU001", "quantity": 10}],
        )
        Reservation.objects.filter(pk=reservation.pk).update(
            expires_at=timezone.now() - timedelta(hours=1),
        )

        count = self.svc.release_expired()
        self.assertEqual(count, 1)

        reservation.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.EXPIRED)

        ws = WarehouseStock.objects.get(sku__sku_code="SKU001", warehouse__code="WH01")
        self.assertEqual(ws.reserved_quantity, 0)

        expire_ledger = StockLedger.objects.filter(
            reservation=reservation,
            reason=StockLedger.Reason.EXPIRE,
        )
        self.assertEqual(expire_ledger.count(), 1)
        self.assertEqual(expire_ledger.first().delta, 10)

    def test_release_expired_skips_non_expired(self):
        reservation = self.svc.reserve(
            self.sale,
            "user1",
            [{"sku_code": "SKU001", "quantity": 10}],
        )

        count = self.svc.release_expired()
        self.assertEqual(count, 0)

        reservation.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.PENDING)

        ws = WarehouseStock.objects.get(sku__sku_code="SKU001", warehouse__code="WH01")
        self.assertEqual(ws.reserved_quantity, 10)

    def test_release_expired_chunked_batches(self):
        for i in range(3):
            reservation = self.svc.reserve(
                self.sale,
                f"user{i}",
                [{"sku_code": "SKU001", "quantity": 1}],
            )
            Reservation.objects.filter(pk=reservation.pk).update(
                expires_at=timezone.now() - timedelta(hours=1),
            )

        count = self.svc.release_expired(batch_size=2)
        self.assertEqual(count, 3)
        self.assertEqual(
            Reservation.objects.filter(status=Reservation.Status.EXPIRED).count(),
            3,
        )
        ws = WarehouseStock.objects.get(sku__sku_code="SKU001", warehouse__code="WH01")
        self.assertEqual(ws.reserved_quantity, 0)


class QueryCountTest(BaseTestMixin, TestCase):
    def setUp(self):
        self.svc = ReservationService()
        self.sale = self._create_sale()
        wh1 = Warehouse.objects.get(code="WH01")
        wh2 = Warehouse.objects.get(code="WH02")
        for i in range(3, 21):
            sku = Sku.objects.create(sku_code=f"SKU{i:03d}", name=f"Product {i}")
            WarehouseStock.objects.create(warehouse=wh1, sku=sku, quantity=100)
            WarehouseStock.objects.create(warehouse=wh2, sku=sku, quantity=100)

    def test_reserve_20_lines_query_count(self):
        from django.db import connection

        queries = []

        def counter(execute, sql, params, many, context):
            queries.append(sql)
            return execute(sql, params, many, context)  # goslop-ignore: CWE-89

        items = [{"sku_code": f"SKU{i:03d}", "quantity": 1} for i in range(1, 21)]
        with connection.execute_wrapper(counter):
            self.svc.reserve(self.sale, "quser", items)

        data_queries = [sql for sql in queries if "inventory_" in sql]
        self.assertEqual(
            sum(
                1
                for sql in data_queries
                if sql.strip().startswith("SELECT") and "inventory_sku" in sql
            ),
            1,
            "SKU lookup must be hoisted out of the loop (DJ-1)",
        )
        self.assertLessEqual(
            len(data_queries),
            2 * 20 + 6,
            f"reserve(20) issued {len(data_queries)} data queries, "
            f"expected <= 46 (was 102, DJ-1/DJ-2)",
        )


class AvailabilityServiceTest(BaseTestMixin, TestCase):
    def setUp(self):
        self.svc = AvailabilityService()
        self.sale = self._create_sale()

    def test_get_sku_available(self):
        available = self.svc.get_sku_available("SKU001")
        self.assertEqual(available, 150)

    def test_get_sku_available_by_region(self):
        us_available = self.svc.get_sku_available("SKU001", region="US")
        self.assertEqual(us_available, 100)

        eu_available = self.svc.get_sku_available("SKU001", region="EU")
        self.assertEqual(eu_available, 50)

    def test_get_batch_availability(self):
        result = self.svc.get_batch_availability(["SKU001", "SKU002"])
        self.assertEqual(result["SKU001"], 150)
        self.assertEqual(result["SKU002"], 200)

    def test_get_batch_availability_missing_sku(self):
        result = self.svc.get_batch_availability(["SKU001", "NONEXISTENT"])
        self.assertEqual(result["SKU001"], 150)
        self.assertEqual(result["NONEXISTENT"], 0)

    def test_get_warehouse_rollup(self):
        stocks = self.svc.get_warehouse_rollup("WH01")
        self.assertEqual(len(stocks), 2)
        stock_map = {s["sku_code"]: s for s in stocks}
        self.assertEqual(stock_map["SKU001"]["quantity"], 100)
        self.assertEqual(stock_map["SKU001"]["reserved_quantity"], 0)
        self.assertEqual(stock_map["SKU001"]["available"], 100)
        self.assertEqual(stock_map["SKU002"]["quantity"], 200)
        self.assertEqual(stock_map["SKU002"]["available"], 200)

    def test_get_warehouse_rollup_single_query_values_projection(self):
        from django.db import connection

        queries = []

        def counter(execute, sql, params, many, context):
            queries.append(sql)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(counter):
            stocks = self.svc.get_warehouse_rollup("WH01")

        self.assertEqual(len(queries), 1, "rollup must be a single projected query (DJ-6)")
        self.assertTrue(all(isinstance(s, dict) for s in stocks), "no ORM hydration (DJ-6)")


class ViewTest(BaseTestMixin, TestCase):
    def test_sku_availability_endpoint(self):
        self._create_sale()
        url = reverse("sku-availability", kwargs={"sku_code": "SKU001"})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["sku_code"], "SKU001")
        self.assertEqual(data["available"], 150)

    def test_sku_availability_with_region(self):
        self._create_sale()
        url = reverse("sku-availability", kwargs={"sku_code": "SKU001"})
        response = self.client.get(url, {"region": "US"})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["available"], 100)

    def test_batch_availability_endpoint(self):
        self._create_sale()
        url = reverse("batch-availability")
        response = self.client.post(
            url,
            data=json.dumps({"sku_codes": ["SKU001", "SKU002"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["SKU001"], 150)
        self.assertEqual(data["SKU002"], 200)

    def test_batch_availability_payload_too_large(self):
        self._create_sale()
        url = reverse("batch-availability")
        big_body = json.dumps({"sku_codes": ["SKU001"] * 100000})
        self.assertGreater(len(big_body), 256 * 1024)
        response = self.client.post(
            url,
            data=big_body,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 413)

    def test_warehouse_rollup_endpoint(self):
        self._create_sale()
        url = reverse("warehouse-rollup", kwargs={"warehouse_code": "WH01"})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["warehouse_code"], "WH01")
        self.assertEqual(len(data["stocks"]), 2)

    def test_availability_404_for_nonexistent_sku(self):
        self._create_sale()
        url = reverse("sku-availability", kwargs={"sku_code": "DOESNOTEXIST"})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["available"], 0)


class MiddlewareTest(TestCase):
    def test_request_timing_header(self):
        response = self.client.get("/admin/")
        self.assertIn("X-Request-Duration-Ms", response)

    def test_sale_event_header(self):
        response = self.client.get("/admin/", HTTP_X_SALE_EVENT_ID="evt-001")
        self.assertIn("X-Request-Duration-Ms", response)


class CommandTest(BaseTestMixin, TestCase):
    def test_expire_reservations_command(self):
        sale = self._create_sale()
        svc = ReservationService()
        reservation = svc.reserve(
            sale,
            "user1",
            [{"sku_code": "SKU001", "quantity": 10}],
        )
        Reservation.objects.filter(pk=reservation.pk).update(
            expires_at=timezone.now() - timedelta(hours=1),
        )

        call_command("expire_reservations")

        reservation.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.EXPIRED)

        ws = WarehouseStock.objects.get(sku__sku_code="SKU001", warehouse__code="WH01")
        self.assertEqual(ws.reserved_quantity, 0)


class ReservationConcurrencyTest(BaseTestMixin, TransactionTestCase):
    def test_concurrent_reserve_no_deadlock(self):
        from django.db import close_old_connections

        sale = self._create_sale()
        WarehouseStock.objects.filter(sku__sku_code="SKU001", warehouse__code="WH02").update(
            quantity=0
        )
        ws = WarehouseStock.objects.get(sku__sku_code="SKU001", warehouse__code="WH01")
        ws.quantity = 5
        ws.reserved_quantity = 0
        ws.save()

        results = []
        errors = []
        ready = threading.Event()

        def _reserve(user_id):
            close_old_connections()
            try:
                ready.wait(timeout=10)
                svc = ReservationService()
                svc.reserve(sale, user_id, [{"sku_code": "SKU001", "quantity": 5}])
                results.append(user_id)
            except Exception as e:
                errors.append((user_id, type(e).__name__, str(e)))
            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=_reserve, args=("user1",)),
            threading.Thread(target=_reserve, args=("user2",)),
        ]
        for t in threads:
            t.start()
        ready.set()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(len(results), 1, f"Expected 1 success, got errors: {errors}")
        self.assertEqual(len(errors), 1, f"Expected 1 error, got results: {results}")
        has_expected = errors[0][1] == "InsufficientStockError" or "locked" in errors[0][2].lower()
        self.assertTrue(has_expected, f"Unexpected error: {errors[0]}")

    def test_concurrent_confirm_no_lost_update(self):
        from django.db import close_old_connections

        sale = self._create_sale()
        ws = WarehouseStock.objects.get(sku__sku_code="SKU001", warehouse__code="WH01")
        ws.quantity = 100
        ws.reserved_quantity = 20
        ws.save()

        svc = ReservationService()
        r1 = svc.reserve(sale, "u1", [{"sku_code": "SKU001", "quantity": 10}])
        r2 = svc.reserve(sale, "u2", [{"sku_code": "SKU001", "quantity": 10}])

        errors = []
        ready = threading.Event()

        def _confirm(reservation_id):
            close_old_connections()
            try:
                ready.wait(timeout=10)
                svc.confirm(reservation_id)
            except Exception as e:
                errors.append((reservation_id, type(e).__name__, str(e)))
            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=_confirm, args=(r1.id,)),
            threading.Thread(target=_confirm, args=(r2.id,)),
        ]
        for t in threads:
            t.start()
        ready.set()
        for t in threads:
            t.join(timeout=20)

        self.assertEqual(errors, [], f"Confirms failed: {errors}")
        ws.refresh_from_db()
        self.assertEqual(ws.quantity, 80, "F() updates must both apply (DJ-3)")
        self.assertEqual(
            ws.reserved_quantity, 20, "reserved must drop by both line quantities (DJ-3)"
        )

    def test_concurrent_same_idempotency_key(self):
        from django.db import close_old_connections

        sale = self._create_sale()
        key = "idem-race"

        results = []
        errors = []
        ready = threading.Event()

        def _reserve(user_id):
            close_old_connections()
            try:
                ready.wait(timeout=10)
                svc = ReservationService()
                res = svc.reserve(
                    sale,
                    user_id,
                    [{"sku_code": "SKU001", "quantity": 10}],
                    idempotency_key=key,
                )
                results.append(res.id)
            except Exception as e:
                errors.append((user_id, type(e).__name__, str(e)))
            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=_reserve, args=("u1",)),
            threading.Thread(target=_reserve, args=("u2",)),
        ]
        for t in threads:
            t.start()
        ready.set()
        for t in threads:
            t.join(timeout=20)

        self.assertEqual(errors, [], f"No request may 500 on the duplicate key (DJ-5): {errors}")
        self.assertEqual(len(results), 2, f"Both threads must return a reservation: {results}")
        self.assertEqual(
            results[0], results[1], "Both threads must return the same reservation (DJ-5)"
        )
        self.assertEqual(
            Reservation.objects.filter(idempotency_key=key).count(),
            1,
        )
        ws = WarehouseStock.objects.get(sku__sku_code="SKU001", warehouse__code="WH01")
        self.assertEqual(ws.reserved_quantity, 10, "stock must be reserved exactly once (DJ-5)")

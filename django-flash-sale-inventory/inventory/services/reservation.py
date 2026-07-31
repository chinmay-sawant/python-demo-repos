from datetime import timedelta

from django.db import IntegrityError, models, transaction
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

LOCK_BATCH = 200


class ReservationService:
    @transaction.atomic
    def reserve(self, sale_event, user_id, items, idempotency_key=None, warehouse_code=None):
        self._ensure_sale_active(sale_event)

        existing = self._find_existing(idempotency_key)
        if existing:
            return existing

        skus = self._fetch_skus(items)
        warehouse = self._resolve_warehouse(warehouse_code)
        item_details = [self._prepare_item(item, skus, warehouse) for item in items]

        reservation = self._create_reservation(user_id, sale_event, idempotency_key)
        if reservation is None:
            return self._find_existing(idempotency_key)
        self._apply_reserve(reservation, item_details)
        return reservation

    @transaction.atomic
    def confirm(self, reservation_id):
        reservation = Reservation.objects.select_for_update().get(id=reservation_id)
        self._ensure_pending(reservation)

        reservation.status = Reservation.Status.CONFIRMED
        reservation.save()
        self._apply_confirm(reservation)

        return reservation

    @transaction.atomic
    def cancel(self, reservation_id):
        reservation = Reservation.objects.select_for_update().get(id=reservation_id)

        reservation.status = Reservation.Status.CANCELLED
        reservation.save()
        self._release_stock(reservation, StockLedger.Reason.CANCEL)

        return reservation

    def release_expired(self, batch_size=500):
        cutoff = timezone.now()
        count = 0
        while True:
            with transaction.atomic():
                expired_qs = Reservation.objects.filter(
                    status=Reservation.Status.PENDING,
                    expires_at__lt=cutoff,
                ).select_for_update(skip_locked=True)[:batch_size]
                processed = 0
                for reservation in expired_qs:
                    reservation.status = Reservation.Status.EXPIRED
                    reservation.save()
                    self._release_stock(reservation, StockLedger.Reason.EXPIRE)
                    processed += 1
                count += processed
            if processed < batch_size:
                break

        return count

    def _ensure_sale_active(self, sale_event):
        if sale_event.status != SaleEvent.Status.ACTIVE:
            raise SaleNotActiveError(
                f"Sale '{sale_event.name}' has status {sale_event.status}, not ACTIVE"
            )

    def _find_existing(self, idempotency_key):
        if not idempotency_key:
            return None
        return Reservation.objects.filter(idempotency_key=idempotency_key).first()

    def _fetch_skus(self, items):
        return {
            s.sku_code: s for s in Sku.objects.filter(sku_code__in=[i["sku_code"] for i in items])
        }

    def _resolve_warehouse(self, warehouse_code):
        return Warehouse.objects.get(code=warehouse_code) if warehouse_code else None

    def _prepare_item(self, item, skus, warehouse):
        sku = skus[item["sku_code"]]
        stock_qs = (
            WarehouseStock.objects.filter(warehouse=warehouse, sku=sku)
            if warehouse
            else WarehouseStock.objects.filter(sku=sku)
            .select_related("warehouse")
            .order_by("-quantity")
        )

        stock = stock_qs.select_for_update().first()
        if not stock or stock.available_quantity() < item["quantity"]:
            raise InsufficientStockError(
                f"Insufficient stock for SKU {item['sku_code']}: "
                f"requested {item['quantity']}, available "
                f"{stock.available_quantity() if stock else 0}"
            )

        return {
            "sku": sku,
            "stock": stock,
            "quantity": item["quantity"],
        }

    def _create_reservation(self, user_id, sale_event, idempotency_key):
        reservation = Reservation(
            user_id=user_id,
            sale_event=sale_event,
            status=Reservation.Status.PENDING,
            idempotency_key=idempotency_key,
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        if idempotency_key:
            try:
                with transaction.atomic():
                    Reservation.objects.bulk_create([reservation])
            except IntegrityError:
                return None
        else:
            Reservation.objects.bulk_create([reservation])
        return reservation

    def _apply_reserve(self, reservation, item_details):
        ReservationLine.objects.bulk_create(
            [
                ReservationLine(
                    reservation=reservation,
                    sku=d["sku"],
                    warehouse=d["stock"].warehouse,
                    quantity=d["quantity"],
                )
                for d in item_details
            ]
        )
        for d in item_details:
            WarehouseStock.objects.filter(pk=d["stock"].pk).update(
                reserved_quantity=models.F("reserved_quantity") + d["quantity"]
            )
        StockLedger.objects.bulk_create(
            [
                StockLedger(
                    warehouse_stock=d["stock"],
                    delta=-d["quantity"],
                    reason=StockLedger.Reason.RESERVE,
                    reservation=reservation,
                )
                for d in item_details
            ]
        )

    def _ensure_pending(self, reservation):
        if reservation.status != Reservation.Status.PENDING:
            raise ReservationNotPendingError(
                f"Reservation {reservation.id} is {reservation.status}, not PENDING"
            )

    def _apply_confirm(self, reservation):
        lines = list(reservation.lines.all())
        stocks = self._lock_stocks_for_lines(lines)
        ledger_entries = [self._write_confirm_line(line, stocks, reservation) for line in lines]
        StockLedger.objects.bulk_create(ledger_entries)

    def _write_confirm_line(self, line, stocks, reservation):
        WarehouseStock.objects.filter(warehouse_id=line.warehouse_id, sku_id=line.sku_id).update(
            reserved_quantity=models.F("reserved_quantity") - line.quantity,
            quantity=models.F("quantity") - line.quantity,
        )
        return StockLedger(
            warehouse_stock=stocks[(line.warehouse_id, line.sku_id)],
            delta=-line.quantity,
            reason=StockLedger.Reason.CONFIRM,
            reservation=reservation,
        )

    def _release_stock(self, reservation, reason):
        lines = list(reservation.lines.all())
        stocks = self._lock_stocks_for_lines(lines)
        ledger_entries = [
            self._write_release_line(line, stocks, reservation, reason) for line in lines
        ]
        StockLedger.objects.bulk_create(ledger_entries)

    def _write_release_line(self, line, stocks, reservation, reason):
        WarehouseStock.objects.filter(warehouse_id=line.warehouse_id, sku_id=line.sku_id).update(
            reserved_quantity=models.F("reserved_quantity") - line.quantity,
        )
        return StockLedger(
            warehouse_stock=stocks[(line.warehouse_id, line.sku_id)],
            delta=line.quantity,
            reason=reason,
            reservation=reservation,
        )

    def _lock_stocks_for_lines(self, lines):
        lock_q = models.Q()
        for line in lines:
            lock_q |= models.Q(warehouse_id=line.warehouse_id, sku_id=line.sku_id)
        stock_ids = list(
            WarehouseStock.objects.filter(lock_q).order_by("pk").values_list("id", flat=True)
        )
        stocks = {}
        for offset in range(0, len(stock_ids), LOCK_BATCH):
            chunk = stock_ids[offset : offset + LOCK_BATCH]
            for ws in (
                WarehouseStock.objects.filter(pk__in=chunk).select_for_update().order_by("pk")
            ):
                stocks[(ws.warehouse_id, ws.sku_id)] = ws
        return stocks

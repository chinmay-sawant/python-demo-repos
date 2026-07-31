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


class ReservationService:
    @transaction.atomic
    def reserve(self, sale_event, user_id, items, idempotency_key=None, warehouse_code=None):
        if sale_event.status != SaleEvent.Status.ACTIVE:
            raise SaleNotActiveError(
                f"Sale '{sale_event.name}' has status {sale_event.status}, not ACTIVE"
            )

        if idempotency_key:
            existing = Reservation.objects.filter(idempotency_key=idempotency_key).first()
            if existing:
                return existing

        skus = {
            s.sku_code: s for s in Sku.objects.filter(sku_code__in=[i["sku_code"] for i in items])
        }
        warehouse = Warehouse.objects.get(code=warehouse_code) if warehouse_code else None

        item_details = []
        for item in items:
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

            item_details.append(
                {
                    "sku": sku,
                    "stock": stock,
                    "quantity": item["quantity"],
                }
            )

        if idempotency_key:
            try:
                with transaction.atomic():
                    reservation = Reservation.objects.create(
                        user_id=user_id,
                        sale_event=sale_event,
                        status=Reservation.Status.PENDING,
                        idempotency_key=idempotency_key,
                        expires_at=timezone.now() + timedelta(minutes=30),
                    )
            except IntegrityError:
                return Reservation.objects.get(idempotency_key=idempotency_key)
        else:
            reservation = Reservation.objects.create(
                user_id=user_id,
                sale_event=sale_event,
                status=Reservation.Status.PENDING,
                idempotency_key=idempotency_key,
                expires_at=timezone.now() + timedelta(minutes=30),
            )

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

        return reservation

    @transaction.atomic
    def confirm(self, reservation_id):
        reservation = Reservation.objects.select_for_update().get(id=reservation_id)

        if reservation.status != Reservation.Status.PENDING:
            raise ReservationNotPendingError(
                f"Reservation {reservation_id} is {reservation.status}, not PENDING"
            )

        reservation.status = Reservation.Status.CONFIRMED
        reservation.save()

        lines = list(reservation.lines.all())
        lock_q = models.Q()
        for line in lines:
            lock_q |= models.Q(warehouse_id=line.warehouse_id, sku_id=line.sku_id)
        stocks = {
            (ws.warehouse_id, ws.sku_id): ws
            for ws in WarehouseStock.objects.filter(lock_q).select_for_update()
        }
        for line in lines:
            WarehouseStock.objects.filter(
                warehouse_id=line.warehouse_id, sku_id=line.sku_id
            ).update(
                reserved_quantity=models.F("reserved_quantity") - line.quantity,
                quantity=models.F("quantity") - line.quantity,
            )
            StockLedger.objects.create(
                warehouse_stock=stocks[(line.warehouse_id, line.sku_id)],
                delta=-line.quantity,
                reason=StockLedger.Reason.CONFIRM,
                reservation=reservation,
            )

        return reservation

    @transaction.atomic
    def cancel(self, reservation_id):
        reservation = Reservation.objects.select_for_update().get(id=reservation_id)

        reservation.status = Reservation.Status.CANCELLED
        reservation.save()

        lines = list(reservation.lines.all())
        lock_q = models.Q()
        for line in lines:
            lock_q |= models.Q(warehouse_id=line.warehouse_id, sku_id=line.sku_id)
        stocks = {
            (ws.warehouse_id, ws.sku_id): ws
            for ws in WarehouseStock.objects.filter(lock_q).select_for_update()
        }
        for line in lines:
            WarehouseStock.objects.filter(
                warehouse_id=line.warehouse_id, sku_id=line.sku_id
            ).update(
                reserved_quantity=models.F("reserved_quantity") - line.quantity,
            )
            StockLedger.objects.create(
                warehouse_stock=stocks[(line.warehouse_id, line.sku_id)],
                delta=line.quantity,
                reason=StockLedger.Reason.CANCEL,
                reservation=reservation,
            )

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

                    lines = list(reservation.lines.all())
                    lock_q = models.Q()
                    for line in lines:
                        lock_q |= models.Q(warehouse_id=line.warehouse_id, sku_id=line.sku_id)
                    stocks = {
                        (ws.warehouse_id, ws.sku_id): ws
                        for ws in WarehouseStock.objects.filter(lock_q).select_for_update()
                    }
                    for line in lines:
                        WarehouseStock.objects.filter(
                            warehouse_id=line.warehouse_id, sku_id=line.sku_id
                        ).update(
                            reserved_quantity=models.F("reserved_quantity") - line.quantity,
                        )
                        StockLedger.objects.create(
                            warehouse_stock=stocks[(line.warehouse_id, line.sku_id)],
                            delta=line.quantity,
                            reason=StockLedger.Reason.EXPIRE,
                            reservation=reservation,
                        )

                    processed += 1
                count += processed
            if processed < batch_size:
                break

        return count

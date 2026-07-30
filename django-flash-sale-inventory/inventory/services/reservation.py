from datetime import timedelta

from django.db import models, transaction
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

        item_details = []
        for item in items:
            sku = Sku.objects.get(sku_code=item['sku_code'])
            if warehouse_code:
                warehouse = Warehouse.objects.get(code=warehouse_code)
                stock_qs = WarehouseStock.objects.filter(warehouse=warehouse, sku=sku)
            else:
                stock_qs = WarehouseStock.objects.filter(sku=sku).select_related('warehouse').order_by('-quantity')

            stock = stock_qs.select_for_update().first()
            if not stock or stock.available_quantity() < item['quantity']:
                raise InsufficientStockError(
                    f"Insufficient stock for SKU {item['sku_code']}: "
                    f"requested {item['quantity']}, available "
                    f"{stock.available_quantity() if stock else 0}"
                )

            item_details.append({
                'sku': sku,
                'stock': stock,
                'quantity': item['quantity'],
            })

        reservation = Reservation.objects.create(
            user_id=user_id,
            sale_event=sale_event,
            status=Reservation.Status.PENDING,
            idempotency_key=idempotency_key,
            expires_at=timezone.now() + timedelta(minutes=30),
        )

        for detail in item_details:
            ReservationLine.objects.create(
                reservation=reservation,
                sku=detail['sku'],
                warehouse=detail['stock'].warehouse,
                quantity=detail['quantity'],
            )
            WarehouseStock.objects.filter(pk=detail['stock'].pk).update(
                reserved_quantity=models.F('reserved_quantity') + detail['quantity']
            )
            StockLedger.objects.create(
                warehouse_stock=detail['stock'],
                delta=-detail['quantity'],
                reason=StockLedger.Reason.RESERVE,
                reservation=reservation,
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

        for line in reservation.lines.select_related('sku', 'warehouse').all():
            stock = WarehouseStock.objects.get(warehouse=line.warehouse, sku=line.sku)
            stock.reserved_quantity -= line.quantity
            stock.quantity -= line.quantity
            stock.save()
            StockLedger.objects.create(
                warehouse_stock=stock,
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

        for line in reservation.lines.select_related('sku', 'warehouse').all():
            stock = WarehouseStock.objects.get(warehouse=line.warehouse, sku=line.sku)
            stock.reserved_quantity -= line.quantity
            stock.save()
            StockLedger.objects.create(
                warehouse_stock=stock,
                delta=line.quantity,
                reason=StockLedger.Reason.CANCEL,
                reservation=reservation,
            )

        return reservation

    @transaction.atomic
    def release_expired(self):
        cutoff = timezone.now()
        expired_qs = Reservation.objects.filter(
            status=Reservation.Status.PENDING,
            expires_at__lt=cutoff,
        ).select_for_update(skip_locked=True)

        count = 0
        for reservation in expired_qs:
            reservation.status = Reservation.Status.EXPIRED
            reservation.save()

            for line in reservation.lines.select_related('sku', 'warehouse').all():
                stock = WarehouseStock.objects.get(warehouse=line.warehouse, sku=line.sku)
                stock.reserved_quantity -= line.quantity
                stock.save()
                StockLedger.objects.create(
                    warehouse_stock=stock,
                    delta=line.quantity,
                    reason=StockLedger.Reason.EXPIRE,
                    reservation=reservation,
                )

            count += 1

        return count

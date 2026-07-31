import uuid

from django.db import models


class SaleEvent(models.Model):
    class Status(models.TextChoices):
        UPCOMING = 'UPCOMING', 'Upcoming'
        ACTIVE = 'ACTIVE', 'Active'
        PAUSED = 'PAUSED', 'Paused'
        ENDED = 'ENDED', 'Ended'

    name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UPCOMING)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = (
            models.Index(fields=['status', 'start_at']),
        )

    def __str__(self):
        return self.name


class Sku(models.Model):
    sku_code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'SKU'
        verbose_name_plural = 'SKUs'

    def __str__(self):
        return f'{self.sku_code} - {self.name}'


class Warehouse(models.Model):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    region = models.CharField(max_length=100)

    def __str__(self):
        return f'{self.code} - {self.name}'


class WarehouseStock(models.Model):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='stocks')
    sku = models.ForeignKey(Sku, on_delete=models.CASCADE, related_name='warehouse_stocks')
    quantity = models.IntegerField(default=0)
    reserved_quantity = models.IntegerField(default=0)
    version = models.IntegerField(default=0)

    class Meta:
        unique_together = (('warehouse', 'sku'),)
        indexes = (
            models.Index(fields=['sku', '-quantity']),
        )
        verbose_name_plural = 'warehouse stocks'

    def available_quantity(self):
        return self.quantity - self.reserved_quantity

    def __str__(self):
        return f'{self.warehouse.code}/{self.sku.sku_code}: {self.available_quantity()}'


class Reservation(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        CONFIRMED = 'CONFIRMED', 'Confirmed'
        CANCELLED = 'CANCELLED', 'Cancelled'
        EXPIRED = 'EXPIRED', 'Expired'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.CharField(max_length=255, db_index=True)
    session_key = models.CharField(max_length=255, blank=True, default='')
    sale_event = models.ForeignKey(SaleEvent, on_delete=models.CASCADE, related_name='reservations')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    idempotency_key = models.CharField(max_length=255, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        indexes = (
            models.Index(fields=['user_id', 'sale_event']),
            models.Index(fields=['status', 'expires_at']),
        )

    def __str__(self):
        return f'Reservation {self.id} [{self.status}]'


class ReservationLine(models.Model):
    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE, related_name='lines')
    sku = models.ForeignKey(Sku, on_delete=models.CASCADE)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE)
    quantity = models.IntegerField()

    class Meta:
        verbose_name_plural = 'reservation lines'

    def __str__(self):
        return f'{self.reservation.id} / {self.sku.sku_code} x{self.quantity}'


class StockLedger(models.Model):
    class Reason(models.TextChoices):
        RESERVE = 'RESERVE', 'Reserve'
        CONFIRM = 'CONFIRM', 'Confirm'
        CANCEL = 'CANCEL', 'Cancel'
        EXPIRE = 'EXPIRE', 'Expire'
        ADJUST = 'ADJUST', 'Adjust'

    warehouse_stock = models.ForeignKey(
        WarehouseStock, on_delete=models.CASCADE, related_name='ledger_entries'
    )
    delta = models.IntegerField()
    reason = models.CharField(max_length=20, choices=Reason.choices)
    reservation = models.ForeignKey(Reservation, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.reason} {self.delta:+d} @ {self.created_at}'

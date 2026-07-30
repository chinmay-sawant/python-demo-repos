class InsufficientStockError(Exception):
    pass


class SaleNotActiveError(Exception):
    pass


class ReservationNotFoundError(Exception):
    pass


class ReservationNotPendingError(Exception):
    pass

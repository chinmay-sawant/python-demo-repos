from django.core.management.base import BaseCommand

from inventory.services.reservation import ReservationService


class Command(BaseCommand):
    help = "Release expired reservation holds"

    def handle(self, *args, **options):  # noqa: V107
        svc = ReservationService()
        count = svc.release_expired()
        self.stdout.write(f"Expired {count} reservation(s)")

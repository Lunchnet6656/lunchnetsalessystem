from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from shifts.models import UserProfile

User = get_user_model()


class Command(BaseCommand):
    help = '既存ユーザー全員に UserProfile を一括作成'

    def handle(self, *args, **options):
        created = 0
        for user in User.objects.all():
            _, is_new = UserProfile.objects.get_or_create(user=user)
            if is_new:
                created += 1
        self.stdout.write(self.style.SUCCESS(f'{created} 件の UserProfile を作成しました。'))

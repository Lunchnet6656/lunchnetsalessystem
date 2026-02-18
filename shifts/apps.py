from django.apps import AppConfig


class ShiftsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'shifts'
    verbose_name = 'シフト管理'

    def ready(self):
        import shifts.signals  # noqa: F401

from django.apps import AppConfig


class QuestConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'quest'
    verbose_name = 'ランチクエスト'

    def ready(self):
        import quest.signals  # noqa: F401

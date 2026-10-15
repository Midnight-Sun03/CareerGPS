from django.apps import AppConfig


class InterviewConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'interview'

    def ready(self):
        from . import signals  # noqa: F401

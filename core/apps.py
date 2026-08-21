from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Importing registers the deployment checks. They cost nothing until
        # `manage.py check --deploy` asks for them.
        from . import checks  # noqa: F401

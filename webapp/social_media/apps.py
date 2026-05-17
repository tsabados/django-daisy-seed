from django.apps import AppConfig


class SocialMediaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "social_media"

    def ready(self):
        from django.db.models.signals import post_migrate
        post_migrate.connect(_setup_schedule, sender=self)


def _setup_schedule(sender, **kwargs):
    try:
        from django_q.models import Schedule
        Schedule.objects.update_or_create(
            func='social_media.tasks.check_scheduled_posts',
            defaults={
                'schedule_type': Schedule.MINUTES,
                'minutes': 1,
                'repeats': -1,
                'name': 'Check scheduled social media posts',
            },
        )
        Schedule.objects.update_or_create(
            func='social_media.tasks.autopost_all_projects_task',
            defaults={
                'schedule_type': Schedule.CRON,
                'cron': '0 10 * * *',
                'repeats': -1,
                'name': 'Autopost for all projects',
            },
        )
    except Exception:
        pass

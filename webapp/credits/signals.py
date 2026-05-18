from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def grant_signup_credits(sender, instance, created, **kwargs):
    if not created:
        return
    from .models import CreditGrant
    CreditGrant.objects.create(
        user=instance,
        amount=settings.CREDITS_SIGNUP_GRANT,
        source=CreditGrant.Source.SIGNUP,
        expires_at=timezone.now() + timezone.timedelta(days=settings.CREDITS_SIGNUP_DAYS),
    )


@receiver(post_save, sender='credits.CreditSpend')
def notify_credits_updated(sender, instance, created, **kwargs):
    if not created:
        return
    from django_eventstream import send_event
    from django.db import transaction
    from .models import available_credits
    
    def send_notification():
        remaining = available_credits(instance.user)
        send_event(f'user-{instance.user_id}', 'message', {
            'type': 'credits:updated',
            'credits': remaining,
        })

    transaction.on_commit(send_notification)

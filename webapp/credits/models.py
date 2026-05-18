from django.conf import settings
from django.db import models
from django.utils import timezone
from core import fields


class CreditGrant(models.Model):
    class Source(models.TextChoices):
        SIGNUP = 'signup', 'Signup'
        SUBSCRIPTION = 'subscription', 'Subscription'
        MANUAL = 'manual', 'Manual'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='credit_grants',
    )
    amount = models.PositiveIntegerField()
    source = fields.TruncatingCharField(max_length=20, choices=Source.choices)
    stripe_invoice_id = fields.TruncatingCharField(max_length=255, blank=True, default='', db_index=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['expires_at']

    def __str__(self):
        return f'{self.user_id} | {self.source} | {self.amount} credits | expires {self.expires_at:%Y-%m-%d}'

    @property
    def spent(self):
        return self.allocations.aggregate(total=models.Sum('amount'))['total'] or 0

    @property
    def remaining(self):
        return max(0, self.amount - self.spent)

    @property
    def is_active(self):
        return self.expires_at > timezone.now() and self.remaining > 0


class CreditSpend(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='credit_spends',
    )
    amount = models.PositiveIntegerField()
    description = fields.TruncatingCharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user_id} spent {self.amount} ({self.description})'


class CreditAllocation(models.Model):
    """Links a single spend transaction to a specific grant bucket."""
    spend = models.ForeignKey(
        CreditSpend,
        on_delete=models.CASCADE,
        related_name='allocations',
    )
    grant = models.ForeignKey(
        CreditGrant,
        on_delete=models.CASCADE,
        related_name='allocations',
    )
    amount = models.PositiveIntegerField()

    class Meta:
        ordering = ['spend_id']

    def __str__(self):
        return f'Spend #{self.spend_id} → Grant #{self.grant_id}: {self.amount} credits'


# ─── Helper functions ─────────────────────────────────────────────────────────

def available_credits(user):
    """Return total non-expired, unspent credits for the user."""
    now = timezone.now()
    grants = CreditGrant.objects.filter(user=user, expires_at__gt=now)
    total = 0
    for grant in grants:
        total += grant.remaining
    return total


def spend_credits(user, amount, description):
    """
    Deduct `amount` credits from the user using FIFO (earliest-expiring first).
    Creates one CreditSpend and one CreditAllocation per consumed grant bucket.
    Returns True if successful, False if insufficient credits.
    """
    from django.db import transaction

    now = timezone.now()
    active_grants = list(
        CreditGrant.objects.filter(user=user, expires_at__gt=now).order_by('expires_at')
    )

    total_available = sum(g.remaining for g in active_grants)
    if total_available < amount:
        return False

    with transaction.atomic():
        spend = CreditSpend.objects.create(user=user, amount=amount, description=description)

        remaining_to_spend = amount
        allocations_to_create = []

        for grant in active_grants:
            if remaining_to_spend <= 0:
                break
            grant_remaining = grant.remaining
            if grant_remaining <= 0:
                continue
            deduct = min(grant_remaining, remaining_to_spend)
            allocations_to_create.append(
                CreditAllocation(spend=spend, grant=grant, amount=deduct)
            )
            remaining_to_spend -= deduct

        CreditAllocation.objects.bulk_create(allocations_to_create)

    return True

from django.conf import settings
from django.db import models
from core import fields


PLATFORM_CHOICES = [
    ('linkedin', 'LinkedIn'),
    ('facebook', 'Facebook'),
    ('instagram', 'Instagram'),
    ('tiktok', 'TikTok'),
]

PLATFORM_ORDER = ['instagram', 'facebook', 'linkedin', 'tiktok']

PLATFORM_CHAR_LIMITS = {
    'linkedin': 3000,
    'facebook': 63206,
    'instagram': 2200,
    'tiktok': 2200,
}

PLATFORM_IMAGE_LIMITS = {
    'linkedin': 9,
    'facebook': 10,
    'instagram': 10,
    'tiktok': 35,
}

STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('scheduled', 'Scheduled'),
    ('publishing', 'Publishing'),
    ('published', 'Published'),
    ('failed', 'Failed'),
]

PROCESSING_STATUS_CHOICES = [
    ('idle', 'Idle'),
    ('generating', 'Generating'),
    ('completed', 'Completed'),
    ('error', 'Error'),
]


POST_TYPE_CHOICES = [
    ('product', 'Product'),
    ('lifestyle', 'Lifestyle'),
    ('ad', 'Ad'),
]


class SocialMediaPost(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='social_media_posts',
    )
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='social_media_posts',
    )
    title = fields.TruncatingCharField(max_length=200)
    shared_text = models.TextField(blank=True)
    topic = models.TextField(blank=True)
    post_type = fields.TruncatingCharField(max_length=20, choices=POST_TYPE_CHOICES, blank=True)
    media_type = fields.TruncatingCharField(max_length=10, default='image')
    video_type = fields.TruncatingCharField(max_length=30, blank=True)
    video_brief = models.JSONField(null=True, blank=True)
    video_suggestions = models.JSONField(null=True, blank=True)
    ai_instruction = models.TextField(blank=True)
    status = fields.TruncatingCharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    processing_status = fields.TruncatingCharField(
        max_length=20, choices=PROCESSING_STATUS_CHOICES, default='idle',
    )
    scheduled_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        try:
            from django_eventstream import send_event
            send_event(f'user-{self.user_id}', 'message', {
                'type': 'post-changed',
                'post_id': self.pk,
                'status': self.status,
                'processing_status': self.processing_status,
                'scheduled_at': self.scheduled_at.isoformat() if self.scheduled_at else '',
            })
        except Exception:
            pass  # Never let SSE break saves


class SocialMediaPostPlatform(models.Model):
    post = models.ForeignKey(
        SocialMediaPost,
        on_delete=models.CASCADE,
        related_name='platforms',
    )
    platform = fields.TruncatingCharField(max_length=20, choices=PLATFORM_CHOICES)
    is_enabled = models.BooleanField(default=True)
    use_shared_text = models.BooleanField(default=True)
    override_text = models.TextField(blank=True)
    use_shared_media = models.BooleanField(default=True)
    published_at = models.DateTimeField(null=True, blank=True)
    publish_error = models.TextField(blank=True, default='')
    published_url = models.URLField(blank=True, default='')

    class Meta:
        unique_together = [('post', 'platform')]

    def get_effective_text(self):
        if self.use_shared_text:
            return self.post.shared_text
        return self.override_text

    def get_effective_media(self):
        if self.use_shared_media:
            return self.post.shared_media.order_by('sort_order')
        return self.override_media.order_by('sort_order')

    def __str__(self):
        return f'{self.post.title} — {self.get_platform_display()}'


class SocialMediaPostMedia(models.Model):
    post = models.ForeignKey(
        SocialMediaPost,
        on_delete=models.CASCADE,
        related_name='shared_media',
    )
    media = models.ForeignKey(
        'media_library.Media',
        on_delete=models.CASCADE,
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order']

    def __str__(self):
        return f'Shared media for {self.post.title}: {self.media}'


class SocialMediaPostSeedImage(models.Model):
    post = models.ForeignKey(
        SocialMediaPost,
        on_delete=models.CASCADE,
        related_name='seed_media',
    )
    media = models.ForeignKey(
        'media_library.Media',
        on_delete=models.CASCADE,
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order']

    def __str__(self):
        return f'Seed media for {self.post.title}: {self.media}'


class SocialMediaPlatformMedia(models.Model):
    platform_variant = models.ForeignKey(
        SocialMediaPostPlatform,
        on_delete=models.CASCADE,
        related_name='override_media',
    )
    media = models.ForeignKey(
        'media_library.Media',
        on_delete=models.CASCADE,
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order']

    def __str__(self):
        return f'Override media for {self.platform_variant}: {self.media}'

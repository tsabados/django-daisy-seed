import datetime

from django.conf import settings
from django.conf.global_settings import LANGUAGES
from django.db import models
from core import fields


class Project(models.Model):
    name = fields.TruncatingCharField(max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='projects',
    )
    language = fields.TruncatingCharField(
        max_length=10,
        choices=LANGUAGES,
        default='en',
    )
    enable_linkedin = models.BooleanField(default=True)
    enable_facebook = models.BooleanField(default=True)
    enable_instagram = models.BooleanField(default=True)
    enable_tiktok = models.BooleanField(default=True)
    product_import_in_progress = models.BooleanField(default=False)
    enable_autopost = models.BooleanField(default=True)
    default_publish_time = models.TimeField(default=datetime.time(9, 0))
    timezone = models.CharField(max_length=64, default='UTC')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def get_enabled_platforms(self):
        platforms = []
        if self.enable_linkedin:
            platforms.append('linkedin')
        if self.enable_facebook:
            platforms.append('facebook')
        if self.enable_instagram:
            platforms.append('instagram')
        if self.enable_tiktok:
            platforms.append('tiktok')
        return platforms

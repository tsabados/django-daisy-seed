from django.conf import settings
from django.db import models
from core import fields

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.webm', '.mkv', '.m4v', '.wmv'}


def media_upload_to(instance, filename):
    """Upload to media/company/<company_uuid>/<filename>.

    The S3Storage location='media' prefix is applied by the storage backend,
    so this callable only needs to return company/<uuid>/<filename>.
    When using local storage the file lands at MEDIA_ROOT/company/<uuid>/<filename>.
    """
    try:
        company_uuid = instance.media_group.user.company.uuid
    except AttributeError:
        company_uuid = 'unknown'
    return f'company/{company_uuid}/{filename}'


def _url_is_video(url):
    if not url:
        return False
    from pathlib import PurePosixPath
    from urllib.parse import urlparse
    path = PurePosixPath(urlparse(url).path)
    return path.suffix.lower() in VIDEO_EXTENSIONS


class MediaGroup(models.Model):
    class GroupType(models.TextChoices):
        PRODUCT = 'product', 'Product'
        MANUAL = 'manual', 'Manual'
        GENERATED = 'generated', 'Generated'
        GENERAL = 'general', 'General'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='media_groups',
    )
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='media_groups',
    )
    title = fields.TruncatingCharField(max_length=2000)
    description = models.TextField(blank=True)
    source_url = models.URLField(blank=True, null=True)
    type = fields.TruncatingCharField(
        max_length=20,
        choices=GroupType.choices,
        default=GroupType.MANUAL,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def imported_media_items(self):
        return self.media_items.filter(source_type=Media.SourceType.IMPORTED)


class Media(models.Model):
    class MediaType(models.TextChoices):
        IMAGE = 'image', 'Image'
        VIDEO = 'video', 'Video'

    class SourceType(models.TextChoices):
        GENERATED = 'generated', 'AI Generated'
        IMPORTED = 'imported', 'Imported'
        UPLOADED = 'uploaded', 'Uploaded'

    media_group = models.ForeignKey(
        MediaGroup,
        on_delete=models.CASCADE,
        related_name='media_items',
    )
    file = models.FileField(upload_to=media_upload_to, blank=True, null=True)
    external_url = models.URLField(blank=True, max_length=2000)
    media_type = fields.TruncatingCharField(
        max_length=10,
        choices=MediaType.choices,
        default=MediaType.IMAGE,
    )
    source_type = fields.TruncatingCharField(
        max_length=20,
        choices=SourceType.choices,
        default=SourceType.UPLOADED,
    )
    visual_analysis = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def is_video(self):
        if self.media_type == self.MediaType.VIDEO:
            return True
        # Auto-detect from file name or external URL if media_type not explicitly set
        if self.file and self.file.name:
            from pathlib import PurePosixPath
            return PurePosixPath(self.file.name).suffix.lower() in VIDEO_EXTENSIONS
        return _url_is_video(self.external_url)

    @property
    def url(self):
        if self.file:
            return self.file.url
        return self.external_url

    def __str__(self):
        if self.file:
            return self.file.name
        if self.external_url:
            return self.external_url
        return f'Media {self.pk}'


import zoneinfo

from django.utils import timezone as dj_timezone


def get_project_tz(project):
    """Return a ZoneInfo object for the project's configured timezone."""
    return zoneinfo.ZoneInfo(project.timezone)


def to_project_localtime(dt, project):
    """Convert an aware datetime to the project's timezone, return as 'YYYY-MM-DDTHH:MM' string."""
    if not dt:
        return ''
    return dt.astimezone(get_project_tz(project)).strftime('%Y-%m-%dT%H:%M')


def to_project_display(dt, project):
    """Convert an aware datetime to the project's timezone, return as human-readable string."""
    if not dt:
        return ''
    return dt.astimezone(get_project_tz(project)).strftime('%b %-d, %Y %H:%M')


def to_project_isoformat(dt, project):
    """Convert an aware datetime to the project's timezone, return full ISO 8601 with offset."""
    if not dt:
        return ''
    return dt.astimezone(get_project_tz(project)).isoformat()


def ensure_aware_in_project_tz(dt, project):
    """If dt is naive, make it aware in the project's timezone. If already aware, return as-is."""
    if dj_timezone.is_naive(dt):
        return dj_timezone.make_aware(dt, get_project_tz(project))
    return dt


def save_timezone_from_request(request):
    """Read the 'timezone' POST field and save it to the current project if valid."""
    tz = request.POST.get('timezone', '').strip()
    if tz and tz in zoneinfo.available_timezones():
        request.project.timezone = tz
        request.project.save(update_fields=['timezone'])

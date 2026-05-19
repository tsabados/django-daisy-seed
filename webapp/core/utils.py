import zoneinfo


def save_timezone_from_request(request):
    """Read the 'timezone' POST field and save it to the current project if valid."""
    tz = request.POST.get('timezone', '').strip()
    if tz and tz in zoneinfo.available_timezones():
        request.project.timezone = tz
        request.project.save(update_fields=['timezone'])

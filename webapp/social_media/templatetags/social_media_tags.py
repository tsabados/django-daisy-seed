import json

from django import template
from django.utils.html import mark_safe

import html as _html

register = template.Library()

PLATFORM_ORDER = ['instagram', 'facebook', 'linkedin', 'tiktok']


@register.filter
def sort_platforms(platforms):
    """Sort a queryset/list of platform variants by the preferred display order."""
    return sorted(platforms, key=lambda p: PLATFORM_ORDER.index(p.platform) if p.platform in PLATFORM_ORDER else len(PLATFORM_ORDER))


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key, '')


@register.filter
def to_json_attr(value):
    """Serialize value to a JSON string safe for use in an HTML double-quoted attribute.

    Uses &quot; for double-quotes so the browser decodes it back to valid JSON.
    """
    if value is None:
        return ''
    return mark_safe(_html.escape(json.dumps(value, separators=(',', ':'))))


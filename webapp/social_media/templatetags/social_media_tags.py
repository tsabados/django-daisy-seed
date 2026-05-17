import json

from django import template
from django.utils.html import mark_safe

import html as _html

register = template.Library()


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


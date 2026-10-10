from django import template
from django.conf import settings

register = template.Library()


@register.simple_tag
def image_url(image, fallback='avatar.jpg'):
    if image:
        try:
            return image.url
        except (AttributeError, ValueError):
            image_name = str(image).strip()
            if image_name:
                if image_name.startswith(('http://', 'https://', '/')):
                    return image_name
                return f"{settings.MEDIA_URL}{image_name.lstrip('/')}"

    return f"{settings.MEDIA_URL}{fallback.lstrip('/')}"

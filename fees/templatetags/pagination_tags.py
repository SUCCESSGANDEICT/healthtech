from django import template

register = template.Library()

@register.simple_tag(takes_context=True)
def pagination_url(context, page_number):
    query = context['request'].GET.copy()
    query['page'] = page_number
    return '?' + query.urlencode()

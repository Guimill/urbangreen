"""French number formatting for the templates."""
from django import template

register = template.Library()
NBSP = " "  # narrow no-break space, the French thousands separator


@register.filter
def thousands(value):
    if value in (None, ""):
        return "–"
    return f"{int(value):,}".replace(",", NBSP)


@register.filter
def percent(value, digits=0):
    if value is None:
        return "–"
    return f"{value * 100:.{int(digits)}f}".replace(".", ",") + NBSP + "%"


@register.filter
def decimal(value, digits=2):
    if value is None:
        return "–"
    return f"{value:.{int(digits)}f}".replace(".", ",").replace("-", "−")


@register.filter
def signed(value, digits=2):
    if value is None:
        return "–"
    return ("+" if value > 0 else "") + decimal(value, digits)

from django.conf import settings


def can_edit(request):
    """May this visitor add communes and start analyses?"""
    return not settings.WRITE_REQUIRES_LOGIN or request.user.is_staff


def permissions(request):
    return {"can_edit": can_edit(request)}

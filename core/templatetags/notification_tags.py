from django import template


register = template.Library()


@register.simple_tag(takes_context=True)
def notification_summary(context, limit=5):
    existing = context.get("notification_summary")
    if existing is not None:
        return existing
    request = context.get("request")

    if (
        request is None
        or not request.user.is_authenticated
    ):
        return {
            "unread_count": 0,
            "items": [],
        }

    notifications = (
        request.user
        .notifications
        .order_by(
            "-created_at",
            "-id",
        )
    )

    return {
        "unread_count": notifications.filter(
            is_read=False
        ).count(),
        "items": list(
            notifications[:limit]
        ),
    }

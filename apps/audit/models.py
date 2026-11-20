from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class AuditLog(models.Model):
    """Who did what to which document, and when. Append-only by convention (no edit/delete views)."""

    user = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL)
    at = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=40)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["at", "id"]
        indexes = [models.Index(fields=["content_type", "object_id"])]


def record(user, obj, action, **detail):
    return AuditLog.objects.create(user=user, content_type=ContentType.objects.get_for_model(obj),
                                   object_id=obj.pk, action=action, detail=detail)


def history(obj):
    return AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk).select_related("user")

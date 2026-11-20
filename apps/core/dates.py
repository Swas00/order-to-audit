"""Parse ?from=&to= date filters into aware datetimes covering whole days (Asia/Kolkata)."""

from datetime import date, datetime, time

from django.utils import timezone


def _parse(s):
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def date_range(request):
    """Returns (from_date, to_date, from_dt, to_dt). Defaults to the current month."""
    today = timezone.localdate()
    d_from = _parse(request.GET.get("from")) or today.replace(day=1)
    d_to = _parse(request.GET.get("to")) or today
    tz = timezone.get_current_timezone()
    return (d_from, d_to, timezone.make_aware(datetime.combine(d_from, time.min), tz),
            timezone.make_aware(datetime.combine(d_to, time.max), tz))


def aware_range(d_from, d_to):
    """Whole-day aware datetimes for a (date, date) pair."""
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.combine(d_from, time.min), tz), timezone.make_aware(datetime.combine(d_to, time.max), tz))

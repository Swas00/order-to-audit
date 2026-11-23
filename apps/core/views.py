from pathlib import Path

from django.http import HttpResponse

PWA = Path(__file__).resolve().parent / "pwa"


def service_worker(request):
    """Served from the site root so its scope covers the whole app; never cached by the browser."""
    resp = HttpResponse((PWA / "sw.js").read_text(), content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    resp["Cache-Control"] = "no-cache"
    return resp


def manifest(request):
    return HttpResponse((PWA / "manifest.webmanifest").read_text(), content_type="application/manifest+json")


def health(request):
    """Liveness and database check for hosting platforms: 200 'ok' when the database answers."""
    from django.db import connection
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception:
        return HttpResponse("database unavailable", status=503, content_type="text/plain")
    return HttpResponse("ok", content_type="text/plain")

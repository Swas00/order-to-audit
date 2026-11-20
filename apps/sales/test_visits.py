"""Salesman visit log with GPS check-in."""

import uuid
from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.accounts.management.commands.seed_users import seed_users
from apps.accounts.models import User
from apps.audit.models import history
from apps.masters.management.commands.seed_masters import seed_masters
from apps.masters.models import Customer
from apps.reports import builders
from apps.sales import services
from apps.sales.geo import FAR_METERS, distance_m
from apps.sales.models import Visit

pytestmark = pytest.mark.django_db
user = lambda n: User.objects.get(username=n)  # noqa: E731
A = lambda: Customer.objects.get(name="Retailer A")  # noqa: E731  (10.805, 78.685)


@pytest.fixture
def seeded():
    seed_users()
    seed_masters()


# ---------- distance ----------

def test_distance_known_values():
    assert distance_m(10.805, 78.685, 10.805, 78.685) == 0
    assert 109 <= distance_m(10.805, 78.685, 10.806, 78.685) <= 113            # 0.001 degree of latitude ~ 111 m
    assert 260_000 <= distance_m(10.805, 78.685, 12.9716, 77.5946) <= 280_000  # Tiruchirappalli to Bengaluru ~ 268 km
    assert distance_m(10.0, 20.0, 11.0, 21.0) == distance_m(11.0, 21.0, 10.0, 20.0)


# ---------- service ----------

def test_check_in_at_the_shop(seeded):
    v = services.check_in(user("salesman_a"), A(), "order", "10.805100", "78.685100", "12", "Ordered 150")
    assert v.distance_m < FAR_METERS and v.location_status == "At shop" and v.accuracy_m == 12
    assert (v.latitude, v.longitude) == (D("10.805100"), D("78.685100"))
    e = history(v).get()
    assert e.action == "checked_in" and e.user.username == "salesman_a"


def test_check_in_far_from_the_shop_is_flagged(seeded):
    v = services.check_in(user("salesman_a"), A(), "no_order", "10.815", "78.685")  # ~1.1 km away
    assert v.distance_m > 1000 and v.location_status == "Far from shop"


def test_check_in_without_location_or_shop_coordinates(seeded):
    v = services.check_in(user("salesman_a"), A(), "closed")
    assert v.latitude is None and v.location_status == "No location"
    Customer.objects.filter(pk=A().pk).update(latitude=None, longitude=None)
    v2 = services.check_in(user("salesman_a"), A(), "closed", "10.8", "78.6")
    assert v2.distance_m is None and v2.location_status == "Shop location not recorded"


@pytest.mark.parametrize("lat,lon", [("91", "0"), ("-91", "0"), ("0", "181"), ("0", "-181"), ("abc", "1"), ("1", None), ("nan", "1"), ("inf", "1"), ("1e400", "1")])
def test_bad_coordinates_rejected(seeded, lat, lon):
    with pytest.raises(services.OrderError, match="Location is not valid"):
        services.check_in(user("salesman_a"), A(), "order", lat, lon)
    assert Visit.objects.count() == 0


def test_rules(seeded):
    sa = user("salesman_a")
    with pytest.raises(services.OrderError, match="not on your list"):
        services.check_in(sa, Customer.objects.get(name="Distributor B"), "order")
    with pytest.raises(services.OrderError, match="outcome"):
        services.check_in(sa, A(), "bogus")
    assert Visit.objects.count() == 0
    v = services.check_in(sa, A(), "order", accuracy_m="999999999")
    assert v.accuracy_m == 100000
    assert services.check_in(sa, A(), "order", accuracy_m="oops").accuracy_m is None


def test_double_tap_and_foreign_uuid(seeded):
    key = uuid.uuid4()
    first = services.check_in(user("salesman_a"), A(), "order", client_uuid=key)
    again = services.check_in(user("salesman_a"), A(), "order", client_uuid=key)
    assert first.pk == again.pk and Visit.objects.count() == 1
    with pytest.raises(services.OrderError):
        services.check_in(user("salesman_b"), Customer.objects.get(name="Distributor B"), "order", client_uuid=key)


# ---------- screens ----------

def test_check_in_screen(client, seeded):
    client.login(username="salesman_a", password="demo12345")
    page = client.get(reverse("visit_new")).content.decode()
    assert "Retailer A" in page and "Distributor B" not in page and "navigator.geolocation" in page
    data = {"customer": A().pk, "outcome": "order", "latitude": "10.8051", "longitude": "78.6851", "accuracy": "15",
            "note": "x", "client_uuid": str(uuid.uuid4())}
    assert client.post(reverse("visit_new"), data).status_code == 302
    assert client.post(reverse("visit_new"), data).status_code == 302 and Visit.objects.count() == 1  # double submit
    bad = client.post(reverse("visit_new"), {**data, "client_uuid": str(uuid.uuid4()), "latitude": "999"})
    assert bad.status_code == 200 and "Location is not valid" in bad.content.decode() and Visit.objects.count() == 1
    assert client.post(reverse("visit_new"), {**data, "client_uuid": "nope"}).status_code == 200
    assert client.post(reverse("visit_new"), {**data, "customer": "abc", "client_uuid": str(uuid.uuid4())}).status_code == 200


def test_visit_list_scoping_and_access(client, seeded):
    services.check_in(user("salesman_a"), A(), "order", "10.805", "78.685")
    services.check_in(user("salesman_b"), Customer.objects.get(name="Distributor B"), "closed")
    client.login(username="salesman_a", password="demo12345")
    mine = client.get(reverse("visit_list")).content.decode()
    assert "Retailer A" in mine and "Distributor B" not in mine
    for name in ["admin", "owner"]:
        client.login(username=name, password="demo12345")
        page = client.get(reverse("visit_list")).content.decode()
        assert "Retailer A" in page and "Distributor B" in page, name
    for name in ["production", "accountant"]:
        client.login(username=name, password="demo12345")
        assert client.get(reverse("visit_list")).status_code == 403, name
    client.login(username="admin", password="demo12345")
    assert client.get(reverse("visit_new")).status_code == 403  # only salesmen check in


def test_visit_log_report(client, seeded):
    services.check_in(user("salesman_a"), A(), "order", "10.805", "78.685", "10")
    services.check_in(user("salesman_a"), A(), "no_order", "10.9", "78.9")
    services.check_in(user("salesman_a"), A(), "closed")
    from django.utils import timezone
    t = builders.visit_log(timezone.localdate(), timezone.localdate())[0]
    assert [r[8] for r in t.rows] == ["At shop", "Far from shop", "No location"]
    client.login(username="owner", password="demo12345")
    assert client.get(reverse("report", args=["visit-log"]), {"export": "csv"}).status_code == 200
    client.login(username="accountant", password="demo12345")
    assert client.get(reverse("report", args=["visit-log"])).status_code == 403

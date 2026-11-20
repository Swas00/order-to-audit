"""Tamil interface for floor staff: language switching, translated screens, and translation-file integrity."""

import gettext
import re
import uuid
from decimal import Decimal as D
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.management.commands.seed_users import seed_users
from apps.accounts.models import User
from apps.core.benchmark import run_full_benchmark
from apps.inventory import services as stock
from apps.inventory.management.commands.seed_inventory import seed_inventory
from apps.masters.management.commands.seed_masters import seed_masters
from apps.masters.models import Item, Sku, Unit
from apps.production import services as prod
from apps.purchasing.models import Supplier

pytestmark = pytest.mark.django_db
LOCALE = Path(__file__).resolve().parents[2] / "locale" / "ta" / "LC_MESSAGES"


@pytest.fixture
def world():
    seed_users()
    seed_masters()
    seed_inventory()
    return run_full_benchmark()


def tamil_client(username="production"):
    c = Client()
    c.force_login(User.objects.get(username=username))
    r = c.post("/i18n/setlang/", {"language": "ta", "next": "/"})
    assert r.status_code == 302 and c.cookies["django_language"].value == "ta"
    return c


# ---------- translation file integrity ----------

def _unescape(text):
    return text.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")


def parse_po(path):
    """{msgid: [msgstr, ...]} for every non-header entry. Handles multi-line strings, escapes and plural forms."""
    entries, msgid, field, plural_idx = {}, None, None, None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r'^(msgid_plural|msgid|msgstr(?:\[(\d)\])?)\s+"(.*)"$', line)
        if m:
            kind = m.group(1)
            if kind == "msgid":
                msgid, field = _unescape(m.group(3)), "msgid"
                entries[msgid] = []
            elif kind == "msgid_plural":
                field = "plural_id"
            else:
                field = "msgstr"
                entries[msgid].append(_unescape(m.group(3)))
        elif line.startswith('"') and field:
            part = _unescape(line[1:-1])
            if field == "msgid":
                del entries[msgid]
                msgid += part
                entries[msgid] = []
            elif field == "msgstr":
                entries[msgid][-1] += part
    return {k: v for k, v in entries.items() if k}


def test_every_string_is_translated_with_matching_placeholders():
    entries = parse_po(LOCALE / "django.po")
    assert len(entries) > 150
    placeholder = re.compile(r"%\([a-z_]+\)s")
    for msgid, msgstrs in entries.items():
        assert msgstrs and all(t.strip() for t in msgstrs), f"untranslated: {msgid!r}"
        for msgstr in msgstrs:
            assert sorted(set(placeholder.findall(msgid))) == sorted(set(placeholder.findall(msgstr))), f"placeholders differ: {msgid!r}"
            assert msgid.count("%%") == msgstr.count("%%"), f"literal percent differs: {msgid!r}"
    assert "fuzzy" not in (LOCALE / "django.po").read_text(encoding="utf-8").replace('"Fuzzy', "")


def test_compiled_catalog_is_current():
    cat = gettext.GNUTranslations(open(LOCALE / "django.mo", "rb"))
    for msgid, msgstrs in parse_po(LOCALE / "django.po").items():
        if "%(counter)s" in msgid:  # plural entries are checked through the rendered plan page
            continue
        assert cat.gettext(msgid) == msgstrs[0], msgid
    assert cat.gettext("Log out") == "வெளியேறு"


# ---------- switching ----------

def test_default_is_english_and_switch_persists_in_cookie(world):
    c = Client()
    c.force_login(User.objects.get(username="production"))
    assert "Production runs" in c.get(reverse("home")).content.decode()
    tc = tamil_client()
    page = tc.get(reverse("home")).content.decode()
    assert "உற்பத்தி இயக்கங்கள்" in page and "Production runs" not in page and 'lang="ta"' in page
    assert "உற்பத்தி மேற்பார்வையாளர்" in page            # role shown in Tamil
    tc.post("/i18n/setlang/", {"language": "en", "next": "/"})
    assert "Production runs" in tc.get(reverse("home")).content.decode()


def test_accept_language_header_selects_tamil_for_new_visitors(world):
    c = Client(HTTP_ACCEPT_LANGUAGE="ta")
    assert "உள்நுழை" in c.get(reverse("login")).content.decode()


def test_unknown_language_and_open_redirect_are_ignored(world):
    c = Client()
    r = c.post("/i18n/setlang/", {"language": "xx", "next": "https://evil.example/"})
    assert r.status_code == 302 and r["Location"] == "/"          # the off-site address was ignored
    assert "django_language" not in c.cookies or c.cookies["django_language"].value != "xx"


def test_login_page_in_tamil(world):
    c = Client()
    c.post("/i18n/setlang/", {"language": "ta", "next": "/login/"})
    page = c.get(reverse("login")).content.decode()
    assert "பயனர்பெயர்" in page and "கடவுச்சொல்" in page and "உள்நுழை" in page
    bad = c.post(reverse("login"), {"username": "production", "password": "wrong"}).content.decode()
    assert "பயனர்பெயர் அல்லது கடவுச்சொல் தவறானது." in bad


# ---------- floor-staff screens ----------

def test_production_screens_in_tamil(world):
    tc = tamil_client()
    run = world["run"]
    lst = tc.get(reverse("run_list")).content.decode()
    assert "உற்பத்தி இயக்கங்கள்" in lst and "இயக்கத்தைத் தொடங்கு" in lst and "எதிர்பார்ப்பு" in lst
    assert "தயாரிப்பு" in lst  # form label
    detail = tc.get(reverse("run_detail", args=[run.pk])).content.decode()
    for text in ["திட்டமிட்டது vs உண்மையான பயன்பாடு", "வேறுபாடு", "எச்சரிக்கை", "விளைச்சல்", "சரி", "சாதகம், சரிபார்க்கவும்",
                 "முடிந்தது", "முடிந்த பொருள் தொகுதி", "வரலாறு"]:
        assert text in detail, text
    assert "-6.00%" in detail and "5.00%" in detail          # numbers stay as they are
    assert "FLAGGED" not in detail and "Planned vs actual" not in detail


def test_in_progress_run_forms_in_tamil(world):
    tc = tamil_client()
    run = prod.start_run(Sku.objects.get(code="MUR200"), 1, User.objects.get(username="production"))
    page = tc.get(reverse("run_detail", args=[run.pk])).content.decode()
    for text in ["மூலப்பொருளை வழங்கு", "இயக்கத்தை முடி", "உற்பத்தியான நல்ல பாக்கெட்டுகள்", "சேதமடைந்த பைகள்", "குறிப்பு (வீணானதற்கான காரணம் போன்றவை)",
                 "பயன்படுத்திய Carton"]:
        assert text in page, text
    assert "நடைபெறுகிறது" in page  # status "In progress"


def test_production_plan_in_tamil_with_plural_forms(world):
    tc = tamil_client()
    page = tc.get(reverse("run_plan")).content.decode()
    assert "நிலுவை ஆர்டர்களிலிருந்து உற்பத்தித் திட்டம்" in page and "1 தொகுதி தயாரிக்கவும்" in page
    assert "1 தொகுதிக்கு இயக்கத்தைத் தொடங்கு" in page and "இருப்பிலுள்ள பொருட்கள் இதற்குப் போதுமானவை." in page
    assert "Make 1 batch" not in page


def test_stock_and_purchase_screens_in_tamil(world):
    tc = tamil_client()
    summary = tc.get(reverse("stock_summary")).content.decode()
    for text in ["இருப்பு சுருக்கம்", "தொடக்கம்", "பெறப்பட்டவை", "வழங்கப்பட்டவை", "இறுதி", "குறைந்த இருப்பு:", "இருப்பைச் சரிசெய்"]:
        assert text in summary, text
    flour = Item.objects.get(code="FLOUR")
    ledger = tc.get(reverse("stock_ledger"), {"stock": f"item:{flour.pk}"}).content.decode()
    assert "இருப்பு பேரேடு" in ledger and "முன் கொண்டுவரப்பட்டது" in ledger and "கொள்முதல் பெறுதல்" in ledger  # movement type label
    adjust = tc.get(reverse("stock_adjust")).content.decode()
    assert "இருப்பைச் சரிசெய்" in adjust and "காரணம்" in adjust and "முடிந்த பொருள்" in adjust
    purchases = tc.get(reverse("purchase_list")).content.decode()
    assert "கொள்முதல் பதிவேடு" in purchases and "கொள்முதலைப் பதிவு செய்" in purchases
    new = tc.get(reverse("purchase_new")).content.decode()
    assert "வரி 1" in new and "கொள்முதலைச் சேமி" in new and "பில் எண்" in new and "விநியோகஸ்தர்" in new


def test_error_and_success_messages_in_tamil(world):
    tc = tamil_client()
    run = prod.start_run(Sku.objects.get(code="MUR200"), 1, User.objects.get(username="production"))
    spice, kg = Item.objects.get(code="SPICE"), Unit.objects.get(code="kg")
    # more than the 3.8 kg left: the stock error text is Tamil, with the numbers intact
    r = tc.post(reverse("run_issue", args=[run.pk]), {"item": spice.pk, "qty": "50", "unit": kg.pk})
    body = r.content.decode()
    assert "இருப்பு போதாது: உள்ளது 3.8000, கேட்டது 50" in body and "Insufficient" not in body
    ok = tc.post(reverse("run_issue", args=[run.pk]), {"item": spice.pk, "qty": "1", "unit": kg.pk}, follow=True).content.decode()
    assert "வழங்கப்பட்டது" in ok
    carton = Item.objects.get(code="CARTON20")
    done = tc.post(reverse("run_complete", args=[run.pk]), {"good_packets": 10, "damaged_pouches": 0, f"pack_{carton.pk}": "1"},
                   follow=True).content.decode()
    assert "இயக்கம் முடிந்தது. இருப்பு புதுப்பிக்கப்பட்டது." in done
    wrong_unit = tc.post(reverse("run_issue", args=[run.pk]), {"item": spice.pk, "qty": "1", "unit": Unit.objects.get(code="L").pk})
    assert wrong_unit.status_code in (200, 302)


def test_wrong_unit_and_batch_errors_in_tamil(world):
    tc = tamil_client()
    run = prod.start_run(Sku.objects.get(code="MUR200"), 1, User.objects.get(username="production"))
    r = tc.post(reverse("run_issue", args=[run.pk]), {"item": Item.objects.get(code="SPICE").pk, "qty": "1", "unit": Unit.objects.get(code="L").pk})
    assert "ஐ" in r.content.decode() and "மாற்ற முடியாது" in r.content.decode()
    r = tc.post(reverse("stock_adjust"), {"sku": Sku.objects.get(code="MUR200").pk, "qty": "5", "reason": "x"})
    assert "முடிந்த பொருட்களைச் சேர்க்கும்போது தொகுதியைத் தேர்ந்தெடுக்கவும்." in r.content.decode()


def test_only_floor_staff_scope_is_translated_and_other_pages_still_render(world):
    """Pages outside the translated scope keep working (English text) when Tamil is selected."""
    tc = tamil_client("admin")
    for name in ["invoice_list", "payment_list", "receivables", "order_list", "costing", "reports"]:
        assert tc.get(reverse(name)).status_code == 200, name
    tc = tamil_client("owner")
    assert tc.get(reverse("dashboard")).status_code == 200
    page = tamil_client("salesman_a").get(reverse("order_new")).content.decode()
    assert "வெளியேறு" in page  # shared header is translated everywhere


def test_audit_history_words_are_translated(world):
    tc = tamil_client()
    detail = tc.get(reverse("run_detail", args=[world["run"].pk])).content.decode()
    history = detail[detail.index("<h2>வரலாறு</h2>"):]
    assert "தொடங்கப்பட்டது" in history and "வழங்கப்பட்டது" in history and "முடிக்கப்பட்டது" in history
    assert "நல்ல பாக்கெட்டுகள் 188" in history and "சேதமடைந்த பைகள் 6" in history
    assert "<strong>started</strong>" not in history and "good packets" not in history
    # English users still see the English words
    en = Client()
    en.force_login(User.objects.get(username="production"))
    assert "<strong>started</strong>" in en.get(reverse("run_detail", args=[world["run"].pk])).content.decode()

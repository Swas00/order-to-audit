from django.contrib import admin, messages

from .models import Bom, BomLine, CompanyProfile, Customer, CustomerPrice, Item, Sku, Unit


class BomLineInline(admin.TabularInline):
    model = BomLine
    extra = 1


@admin.action(description="Copy selected BOM(s) to a new inactive version")
def copy_bom(modeladmin, request, queryset):
    for bom in queryset:
        latest = bom.sku.boms.order_by("-version").first()
        new = Bom.objects.create(sku=bom.sku, version=latest.version + 1, batch_yield=bom.batch_yield, is_active=False)
        BomLine.objects.bulk_create(
            BomLine(bom=new, item=l.item, qty_per_batch=l.qty_per_batch, unit=l.unit) for l in bom.lines.all()
        )
        messages.success(request, f"Created {new}")


@admin.register(Bom)
class BomAdmin(admin.ModelAdmin):
    list_display = ("sku", "version", "batch_yield", "is_active")
    inlines = [BomLineInline]
    actions = [copy_bom]


class CustomerPriceInline(admin.TabularInline):
    model = CustomerPrice
    extra = 0


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "state_name", "gstin", "salesman", "active")
    inlines = [CustomerPriceInline]


admin.site.register([Unit, Item, Sku, CompanyProfile])

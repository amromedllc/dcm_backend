from django.contrib import admin
from django_tenants.admin import TenantAdminMixin
from unfold.admin import ModelAdmin, TabularInline
from .models import Organization, Domain


class DomainInline(TabularInline):
    model = Domain
    extra = 1


@admin.register(Organization)
class OrganizationAdmin(TenantAdminMixin, ModelAdmin):
    list_display = ['name', 'slug', 'plan', 'is_active', 'created_at']
    list_filter = ['plan', 'is_active']
    search_fields = ['name', 'slug']
    inlines = [DomainInline]

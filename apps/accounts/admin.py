from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from unfold.admin import ModelAdmin
from .models import User, APIKey


@admin.register(User)
class UserAdmin(ModelAdmin, BaseUserAdmin):
    list_display = ['email', 'full_name', 'role', 'is_active', 'created_at']
    list_filter = ['role', 'is_active']
    search_fields = ['email', 'first_name', 'last_name']
    ordering = ['email']
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name')}),
        ('Permissions', {'fields': ('role', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'first_name', 'last_name', 'role', 'password1', 'password2'),
        }),
    )


@admin.register(APIKey)
class APIKeyAdmin(ModelAdmin):
    list_display = ['name', 'key_prefix', 'organization', 'can_write', 'created_by', 'is_active', 'expires_at', 'last_used_at']
    list_filter = ['is_active', 'can_write']
    search_fields = ['name', 'key_prefix']
    readonly_fields = ['key_prefix', 'key_hash', 'service_user', 'last_used_at', 'created_at']
    def has_add_permission(self, request):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not obj.is_active and obj.service_user_id:
            User.objects.filter(id=obj.service_user_id).update(is_active=False)

from ninja import Schema


class OrganizationAuthenticationSettingsSchema(Schema):
    automatic_logout_enabled: bool
    automatic_logout_minutes: int


class OrganizationAuthenticationSettingsUpdate(Schema):
    automatic_logout_enabled: bool | None = None
    automatic_logout_minutes: int | None = None

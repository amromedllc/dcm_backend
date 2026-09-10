from datetime import datetime

from ninja import Schema


class OrganizationAuthenticationSettingsSchema(Schema):
    automatic_logout_enabled: bool
    automatic_logout_minutes: int


class OrganizationAuthenticationSettingsUpdate(Schema):
    automatic_logout_enabled: bool | None = None
    automatic_logout_minutes: int | None = None


class TpmsAdminEmailSettingSchema(Schema):
    id: int
    admin_id: int
    facility_name: str = ''
    email_notifications_enabled: bool


class TpmsAdminIdSeed(Schema):
    admin_id: int
    facility_name: str = ''


class OrganizationPracticeEmailSettingsSchema(Schema):
    id: int
    name: str
    slug: str
    schema_name: str
    plan: str
    is_active: bool
    domain: str | None = None
    tpms_admin_ids: list[TpmsAdminEmailSettingSchema]


class OrganizationSuperadminCreate(Schema):
    name: str
    slug: str
    schema_name: str | None = None
    domain: str | None = None
    plan: str = 'starter'
    is_active: bool = True
    tpms_admin_ids: list[TpmsAdminIdSeed] = []


class OrganizationSuperadminUpdate(Schema):
    name: str | None = None
    slug: str | None = None
    domain: str | None = None
    plan: str | None = None
    is_active: bool | None = None


class TpmsAdminIdCreate(Schema):
    organization_id: int
    admin_id: int
    facility_name: str = ''
    email_notifications_enabled: bool = False


class TpmsAdminEmailSettingUpdate(Schema):
    admin_id: int | None = None
    facility_name: str | None = None
    email_notifications_enabled: bool | None = None


class SuperadminAPIKeyCreate(Schema):
    organization_id: int
    name: str
    can_write: bool = False
    # Optional practice scope for the key's service account. Must be one of the
    # target org's mapped TPMS practice admin IDs (OrganizationTpmsAdminId).
    # When omitted it is inherited from an org admin if that admin's practice
    # is mapped to the org, else left null (native / single-practice orgs).
    external_admin_id: int | None = None
    expires_at: datetime | None = None


class SuperadminAPIKeySchema(Schema):
    id: int
    name: str
    key_prefix: str
    organization_id: int | None
    organization_name: str
    external_admin_id: int | None
    tpms_facility_name: str | None
    can_write: bool
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class SuperadminAPIKeyCreatedSchema(SuperadminAPIKeySchema):
    raw_key: str
    message: str = 'Store this key securely — it will not be shown again.'

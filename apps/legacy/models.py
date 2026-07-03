"""
Read-only unmanaged models that map directly to the TherapyPMS PostgreSQL
database. Django never migrates these tables — they are owned by the Laravel
app. All writes must go through the TherapyPMS API or admin UI.
"""
from django.db import models


class TpmsEmployee(models.Model):
    """Maps to therapypms `employees` table (staff / providers)."""
    admin_id = models.IntegerField(null=True)
    first_name = models.CharField(max_length=191, null=True)
    middle_name = models.CharField(max_length=191, null=True)
    last_name = models.CharField(max_length=191, null=True)
    full_name = models.CharField(max_length=191, null=True)
    login_email = models.CharField(max_length=191, null=True)
    password = models.TextField(null=True)
    office_email = models.CharField(max_length=191, null=True)
    employee_type = models.CharField(max_length=191, null=True)
    is_active = models.IntegerField(null=True)
    is_staff_active = models.IntegerField(null=True)
    timezone = models.CharField(max_length=191, null=True)
    created_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(null=True)

    class Meta:
        app_label = 'legacy'
        db_table = 'employees'
        managed = False

    def __str__(self) -> str:
        return self.full_name or self.login_email or f'Employee {self.pk}'


class TpmsClient(models.Model):
    """Maps to therapypms `clients` table."""
    admin_id = models.IntegerField(null=True)
    client_first_name = models.CharField(max_length=191, null=True)
    client_middle = models.CharField(max_length=191, null=True)
    client_last_name = models.CharField(max_length=191, null=True)
    client_full_name = models.CharField(max_length=191, null=True)
    client_preferred = models.CharField(max_length=191, null=True)
    client_dob = models.DateField(null=True)
    client_gender = models.CharField(max_length=191, null=True)
    email = models.CharField(max_length=191, null=True)
    login_email = models.CharField(max_length=191, null=True)
    password = models.TextField(null=True)
    phone_number = models.CharField(max_length=191, null=True)
    location = models.CharField(max_length=191, null=True)
    is_active_client = models.IntegerField(null=True)
    client_type = models.IntegerField(null=True)
    created_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(null=True)

    class Meta:
        app_label = 'legacy'
        db_table = 'clients'
        managed = False

    def __str__(self) -> str:
        return self.client_full_name or f'Client {self.pk}'


class TpmsAdmin(models.Model):
    """Maps to therapypms `admins` table (practice owners / admin users)."""
    company_id = models.IntegerField(null=True)
    name = models.CharField(max_length=191)
    first_name = models.CharField(max_length=191, null=True)
    last_name = models.CharField(max_length=191, null=True)
    email = models.CharField(max_length=191)
    login_email = models.CharField(max_length=191, null=True)
    password = models.CharField(max_length=191, null=True)
    is_up_admin = models.IntegerField(null=True)
    up_admin_id = models.IntegerField(null=True)
    account_status = models.IntegerField(null=True)
    active = models.IntegerField(null=True)
    created_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(null=True)

    class Meta:
        app_label = 'legacy'
        db_table = 'admins'
        managed = False

    def __str__(self) -> str:
        return self.email


class TpmsActivityTemplate(models.Model):
    """Maps to therapypms `activity_templates` table — holds service/activity names."""
    activity_name = models.CharField(max_length=191, null=True)
    cpt_code = models.CharField(max_length=191, null=True)
    billed_type = models.CharField(max_length=191, null=True)
    billed_time = models.CharField(max_length=191, null=True)

    class Meta:
        app_label = 'legacy'
        db_table = 'activity_templates'
        managed = False


class TpmsAppointment(models.Model):
    """Maps to therapypms `appoinments` table (note: intentional typo in source)."""
    admin_id = models.IntegerField(null=True)
    client_id = models.BigIntegerField(null=True)
    provider_id = models.BigIntegerField(null=True)
    authorization_id = models.BigIntegerField(null=True)
    authorization_activity_id = models.BigIntegerField(null=True)
    payor_id = models.IntegerField(null=True)
    schedule_date = models.DateField(null=True)
    from_time = models.DateTimeField(null=True)
    to_time = models.DateTimeField(null=True)
    time_duration = models.IntegerField(null=True)
    activity_type = models.CharField(max_length=191, null=True)
    cpt_code = models.CharField(max_length=191, null=True)
    status = models.CharField(max_length=191, null=True)
    location = models.CharField(max_length=191, null=True)
    notes = models.TextField(null=True)
    billable = models.IntegerField(null=True)
    is_locked = models.IntegerField(null=True)
    is_break = models.IntegerField(null=True)
    rendered_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(null=True)

    class Meta:
        app_label = 'legacy'
        db_table = 'appoinments'
        managed = False

    def __str__(self) -> str:
        return f'TPMS Appointment {self.pk} — client {self.client_id} on {self.schedule_date}'

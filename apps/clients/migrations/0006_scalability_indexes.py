from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clients', '0005_clientstaffassignment_organization'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='client',
            index=models.Index(fields=['organization', 'status', 'last_name', 'first_name'], name='client_org_status_name_idx'),
        ),
        migrations.AddIndex(
            model_name='client',
            index=models.Index(fields=['organization', 'external_admin_id', 'status'], name='client_org_admin_status_idx'),
        ),
        migrations.AddIndex(
            model_name='clientstaffassignment',
            index=models.Index(fields=['organization', 'user', 'is_active'], name='assign_org_user_active_idx'),
        ),
        migrations.AddIndex(
            model_name='clientstaffassignment',
            index=models.Index(fields=['organization', 'client', 'is_active'], name='assign_org_client_active_idx'),
        ),
    ]

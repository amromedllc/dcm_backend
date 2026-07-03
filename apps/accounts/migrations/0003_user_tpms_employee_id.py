from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_user_tpms_admin_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='tpms_employee_id',
            field=models.IntegerField(blank=True, db_index=True, null=True),
        ),
    ]

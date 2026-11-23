from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('tenants', '0010_organization_pin_lock_allowed')]

    operations = [
        migrations.AlterField(
            model_name='domain',
            name='domain',
            field=models.CharField(db_index=True, max_length=253),
        ),
        migrations.AddConstraint(
            model_name='domain',
            constraint=models.UniqueConstraint(
                fields=('domain', 'tenant'),
                name='unique_domain_per_tenant',
            ),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('tenants', '0014_organizationtpmsadminid_ai_enabled')]

    operations = [
        migrations.AddField(
            model_name='organizationtpmsadminid',
            name='form_builder_mode',
            field=models.CharField(
                choices=[('new', 'New Form Builder only'), ('old', 'Old Form Builder only'), ('both', 'Both form builders')],
                default='new', max_length=4,
            ),
        ),
    ]

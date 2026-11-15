from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0041_subcategory_roles_and_protect'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='menu_order',
            field=models.JSONField(blank=True, default=list),
        ),
    ]

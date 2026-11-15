from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0037_userprofile'),
    ]

    operations = [
        migrations.AddField(
            model_name='bankimport',
            name='dismissed',
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name='AccountAlias',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('identifier', models.CharField(max_length=255, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='aliases', to='finance.account')),
            ],
        ),
    ]

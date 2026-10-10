from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0011_studentacademiccorrectionhistory'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='academicsession',
            options={'ordering': ('start_date', 'end_date', 'name')},
        ),
        migrations.AlterField(
            model_name='academicsession',
            name='is_active',
            field=models.BooleanField(default=False),
        ),
    ]

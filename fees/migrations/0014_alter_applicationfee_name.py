from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fees', '0013_remove_fee_unique_fee_session_level_semester_indigene_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='applicationfee',
            name='name',
            field=models.CharField(
                choices=[
                    ('Application Form', 'Application Form'),
                    ('Screening Fee', 'Screening Fee'),
                    ('Acceptance Fee', 'Acceptance Fee'),
                    ('Caution Fee', 'Caution Fee'),
                    ('Other Charges', 'Other Charges'),
                    ('Late Registration', 'Late Registration'),
                ],
                max_length=60,
            ),
        ),
    ]

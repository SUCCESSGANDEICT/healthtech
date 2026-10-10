from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fees', '0011_alter_applicationfee_category'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='applicationfee',
            name='unique_application_fee_per_session_category',
        ),
        migrations.RemoveConstraint(
            model_name='fee',
            name='unique_fee_per_session_level_semester',
        ),
        migrations.AddField(
            model_name='applicationfee',
            name='indigene_type',
            field=models.CharField(choices=[('INDIGENE', 'INDIGENE'), ('NON-INDIGENE', 'NON-INDIGENE')], default='INDIGENE', max_length=20),
        ),
        migrations.AddField(
            model_name='fee',
            name='indigene_type',
            field=models.CharField(choices=[('INDIGENE', 'INDIGENE'), ('NON-INDIGENE', 'NON-INDIGENE')], default='INDIGENE', max_length=20),
        ),
        migrations.AddConstraint(
            model_name='applicationfee',
            constraint=models.UniqueConstraint(fields=('academic_session', 'category', 'name', 'indigene_type'), name='unique_application_fee_session_category_indigene'),
        ),
        migrations.AddConstraint(
            model_name='fee',
            constraint=models.UniqueConstraint(fields=('academic_session', 'name', 'level', 'semester', 'indigene_type'), name='unique_fee_session_level_semester_indigene'),
        ),
    ]

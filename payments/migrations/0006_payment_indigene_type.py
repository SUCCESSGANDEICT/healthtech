from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0005_alter_payment_transaction_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='indigene_type',
            field=models.CharField(choices=[('INDIGENE', 'INDIGENE'), ('NON-INDIGENE', 'NON-INDIGENE')], default='INDIGENE', max_length=20),
        ),
        migrations.AddIndex(
            model_name='payment',
            index=models.Index(fields=['user', 'academic_sess', 'payment_source', 'indigene_type'], name='payment_user_session_source_indigene_idx'),
        ),
    ]

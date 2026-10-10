import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('academics', '0010_alter_level_name'),
    ]

    operations = [
        migrations.CreateModel(
            name='StudentAcademicCorrectionHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('previous_level', models.CharField(blank=True, max_length=30)),
                ('new_level', models.CharField(blank=True, max_length=30)),
                ('changed_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('changed_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('new_admitted_session', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='academics.academicsession')),
                ('previous_admitted_session', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='academics.academicsession')),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='academic_correction_history', to='academics.student')),
            ],
            options={'ordering': ('-changed_at', '-pk')},
        ),
    ]

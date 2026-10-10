from django.core.management.base import BaseCommand
from django.db import transaction

from academics.models import CourseOfStudy
from fees.models import Fee


class Command(BaseCommand):
    help = 'Clone legacy general School Fees/Tuition records to each Course of Study/Programme.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit',
            action='store_true',
            help='Actually create records. Without this flag the command only reports what would be created.',
        )
        parser.add_argument(
            '--session-id',
            type=int,
            help='Limit mapping to one academic session id.',
        )

    def handle(self, *args, **options):
        commit = options['commit']
        session_id = options.get('session_id')

        source_fees = Fee.objects.filter(course_of_study__isnull=True)
        source_fees = [fee for fee in source_fees if fee.is_tuition_fee]
        if session_id:
            source_fees = [fee for fee in source_fees if fee.academic_session_id == session_id]

        programmes = list(CourseOfStudy.objects.order_by('name'))
        created = 0
        skipped = 0

        with transaction.atomic():
            for fee in source_fees:
                for programme in programmes:
                    exists = Fee.objects.filter(
                        academic_session=fee.academic_session,
                        course_of_study=programme,
                        name__iexact=fee.name,
                        level=fee.level,
                        semester=fee.semester,
                        indigene_type=fee.indigene_type,
                    ).exists()
                    if exists:
                        skipped += 1
                        continue
                    created += 1
                    if commit:
                        Fee.objects.create(
                            academic_session=fee.academic_session,
                            course_of_study=programme,
                            name=fee.name,
                            level=fee.level,
                            semester=fee.semester,
                            amount=fee.amount,
                            indigene_type=fee.indigene_type,
                            added_by=fee.added_by,
                        )

            if not commit:
                transaction.set_rollback(True)

        mode = 'Created' if commit else 'Would create'
        self.stdout.write(self.style.SUCCESS(f'{mode} {created} programme tuition fee record(s); skipped {skipped} existing record(s).'))

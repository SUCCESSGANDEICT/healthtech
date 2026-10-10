from copy import copy

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import GRADUATE_ROLE, STUDENT_ROLE
from admission.models import Admitted, ApplicationSubmission, Jamb, PreApplicant
from fees.utils import get_user_indigene_type

from .models import AcademicSession, CourseEnrollment, Student, StudentAcademicCorrectionHistory


OUTSTANDING_SCHOOL_FEE_MESSAGE = (
    'Student cannot be graduated because there is an outstanding school fee balance.'
)


@transaction.atomic
def correct_student_level_and_admitted_session(*, student_id, level, admitted_session, changed_by):
    """Atomically correct current level and all existing admission-session sources."""
    student = (
        Student.objects.select_for_update()
        .select_related('academic_session', 'candidate')
        .get(pk=student_id)
    )
    previous_level = student.current_level or ''
    previous_session = student.academic_session
    new_level = level.name

    if previous_level == new_level and previous_session.pk == admitted_session.pk:
        raise ValidationError('Choose a different level or admitted academic session.')

    level_changed = previous_level != new_level
    session_changed = previous_session.pk != admitted_session.pk

    if level_changed:
        student.current_level = new_level
    if session_changed:
        student.academic_session = admitted_session
    update_fields = []
    if level_changed:
        update_fields.append('current_level')
    if session_changed:
        update_fields.append('academic_session')
    student.save(update_fields=update_fields)

    if session_changed:
        synchronized_querysets = (
            Admitted.objects.select_for_update().filter(applicant_id=student.candidate_id),
            ApplicationSubmission.objects.select_for_update().filter(applicant_id=student.candidate_id),
            PreApplicant.objects.select_for_update().filter(applicant_id=student.candidate_id),
            Jamb.objects.select_for_update().filter(applicant_id=student.candidate_id),
        )
        for queryset in synchronized_querysets:
            list(queryset.values_list('pk', flat=True))

        synchronized_querysets[0].update(academic_session=admitted_session)
        synchronized_querysets[1].update(academic_sess=admitted_session)
        synchronized_querysets[2].update(academic_session=admitted_session)
        synchronized_querysets[3].update(academic_session=admitted_session)

    history = StudentAcademicCorrectionHistory.objects.create(
        student=student,
        previous_level=previous_level,
        new_level=new_level,
        previous_admitted_session=previous_session,
        new_admitted_session=admitted_session,
        changed_by=changed_by,
    )
    return student, history


def determine_graduation_session(student):
    """Resolve graduation from actual final-level activity, then safe progression data."""
    if student.graduation_session_id:
        return student.graduation_session

    prefetched_enrollments = getattr(student.candidate, '_graduation_enrollments', None)
    if prefetched_enrollments is not None:
        enrollment = prefetched_enrollments[0] if prefetched_enrollments else None
    else:
        enrollment = (
            CourseEnrollment.objects.filter(
                student_id=student.candidate_id,
                programme_id=student.course_id,
                level__name=student.current_level,
            )
            .select_related('academic_session')
            .order_by(
                '-academic_session__start_date',
                '-academic_session__end_date',
                '-academic_session__name',
                '-pk',
            )
            .first()
        )
    if enrollment:
        return enrollment.academic_session

    current_session = student.academic_session
    admitted = getattr(student.candidate, 'admitted', None)
    if not admitted:
        return current_session

    # Legacy records sometimes retained the admitted session after reaching year two.
    # Only infer the next chronological session when the admission level proves that
    # the student entered at year one. Direct-entry year-two students stay in their
    # admitted/current session.
    progression = {'NDII': 'NDI', 'HNDII': 'HNDI'}
    if (
        current_session.pk == admitted.academic_session_id
        and progression.get(student.current_level) == admitted.admitted_level
    ):
        next_session = (
            AcademicSession.objects.filter(start_date__gt=admitted.academic_session.start_date)
            .order_by('start_date', 'end_date', 'name')
            .first()
        )
        if next_session:
            return next_session
    return current_session or admitted.academic_session


def has_graduation_status(student):
    """Recognize the existing flag and incomplete legacy graduation audit state."""
    profile = getattr(student.candidate, 'profile', None)
    return bool(
        student.graduated
        or student.graduation_session_id
        or student.graduated_at
        or (profile and profile.role == GRADUATE_ROLE)
    )


@transaction.atomic
def synchronize_graduate_state(student):
    """Idempotently align an existing graduate's Student flag and Student profile role."""
    locked_student = (
        Student.objects.select_for_update()
        .select_related('candidate__profile', 'graduation_session')
        .get(pk=student.pk)
    )
    if not has_graduation_status(locked_student):
        return False
    if not locked_student.graduated:
        locked_student.graduated = True
        locked_student.save(update_fields=['graduated'])
    profile = locked_student.candidate.profile
    if profile.role == STUDENT_ROLE:
        profile.role = GRADUATE_ROLE
        profile.save(update_fields=['role'])
    return True


def student_school_fees_settled(student, graduation_session):
    """Use the portal's tuition state resolver for every relevant academic period."""
    # Local import avoids a module cycle: payments.views already imports Student.
    from payments.views import get_tuition_payment_state

    fee_contexts = {}
    admitted = getattr(student.candidate, 'admitted', None)
    if admitted and admitted.admitted_level:
        fee_contexts[(admitted.academic_session_id, admitted.admitted_level)] = admitted.academic_session

    enrollments = CourseEnrollment.objects.filter(
        student_id=student.candidate_id,
        programme_id=student.course_id,
    ).select_related('academic_session', 'level')
    for enrollment in enrollments:
        fee_contexts[(enrollment.academic_session_id, enrollment.level.name)] = enrollment.academic_session

    if graduation_session:
        fee_contexts[(graduation_session.pk, student.current_level)] = graduation_session

    indigene_type = get_user_indigene_type(student.candidate)
    unsettled = []
    for (_session_id, level_name), academic_session in fee_contexts.items():
        student_for_period = copy(student)
        student_for_period.current_level = level_name
        state = get_tuition_payment_state(
            student.candidate,
            student_for_period,
            academic_session,
            indigene_type,
        )
        paid = state['has_paid_full_session_fee'] or (
            state['has_paid_first_semester_fee']
            and state['has_paid_second_semester_fee']
        )
        if not paid:
            unsettled.append(f'{academic_session} ({level_name})')
    return not unsettled, unsettled


@transaction.atomic
def graduate_students(*, student_ids, course, level, graduation_session, graduated_by):
    """Graduate a validated selection atomically and report harmless skips."""
    requested_ids = list(dict.fromkeys(student_ids))
    locked_students = list(
        Student.objects.select_for_update()
        .select_related(
            'academic_session', 'graduation_session', 'course', 'candidate',
            'candidate__profile', 'candidate__admitted', 'candidate__admitted__academic_session',
        )
        .filter(pk__in=requested_ids)
        .order_by('pk')
    )
    by_id = {student.pk: student for student in locked_students}
    graduated = []
    skipped = []

    for student_id in requested_ids:
        student = by_id.get(student_id)
        if student is None:
            skipped.append({'id': student_id, 'reason': 'Student was not found.'})
            continue
        if has_graduation_status(student):
            synchronized = False
            if not student.graduated:
                student.graduated = True
                student.save(update_fields=['graduated'])
                synchronized = True
            profile = student.candidate.profile
            if profile.role == STUDENT_ROLE:
                profile.role = GRADUATE_ROLE
                profile.save(update_fields=['role'])
                synchronized = True
            skipped.append({
                'id': student_id,
                'reason': (
                    'Student is already graduated; existing graduate status was synchronized.'
                    if synchronized else 'Student is already graduated.'
                ),
                'synchronized': synchronized,
            })
            continue
        if student.course_id != course.pk or student.current_level != level.name:
            skipped.append({'id': student_id, 'reason': 'Student does not match the selected course and level.'})
            continue
        admitted = getattr(student.candidate, 'admitted', None)
        if not admitted or admitted.admitted_course_id != student.course_id:
            skipped.append({'id': student_id, 'reason': 'Student has no matching admission record.'})
            continue
        resolved_session = determine_graduation_session(student)
        if not resolved_session or resolved_session.pk != graduation_session.pk:
            skipped.append({'id': student_id, 'reason': 'Student does not match the selected graduating session.'})
            continue
        fees_settled, unsettled_periods = student_school_fees_settled(student, resolved_session)
        if not fees_settled:
            skipped.append({
                'id': student_id,
                'reason': f'{OUTSTANDING_SCHOOL_FEE_MESSAGE} Unsettled: {", ".join(unsettled_periods)}.',
            })
            continue

        student.graduated = True
        student.graduation_session = resolved_session
        student.graduated_at = timezone.now()
        student.graduated_by = graduated_by
        student.save(update_fields=['graduated', 'graduation_session', 'graduated_at', 'graduated_by'])
        profile = student.candidate.profile
        if profile.role == STUDENT_ROLE:
            profile.role = GRADUATE_ROLE
            profile.save(update_fields=['role'])
        graduated.append(student)

    return graduated, skipped

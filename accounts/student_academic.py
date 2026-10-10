from admission.models import Admitted, ApplicationSubmission, PreApplicant
from academics.models import AcademicSession, Semester


def get_student_application_session(user):
    # Promotion can replace Student.academic_session. The admitted record is
    # created at admission and synchronized by admitted-session corrections.
    admitted = Admitted.objects.select_related('academic_session').filter(applicant=user).first()
    if admitted and admitted.academic_session_id:
        return admitted.academic_session

    application_submission = (
        ApplicationSubmission.objects.select_related('academic_sess')
        .filter(applicant=user)
        .order_by('academic_sess__start_date', 'academic_sess__added_date', 'academic_sess__id', 'id')
        .first()
    )
    if application_submission and application_submission.academic_sess_id:
        return application_submission.academic_sess

    pre_applicant = (
        PreApplicant.objects.select_related('academic_session')
        .filter(applicant=user)
        .order_by('-id')
        .first()
    )
    if pre_applicant and pre_applicant.academic_session_id:
        return pre_applicant.academic_session

    return None


def ordered_academic_sessions():
    return AcademicSession.objects.order_by('start_date', 'end_date', 'name')


def student_level_session_offset(student):
    level_name = (student.current_level or '').strip().upper()
    if level_name in {'NDII', 'HNDII'}:
        return 1
    return 0


def resolve_student_academic_session(student, user):
    application_session = get_student_application_session(user)
    starting_session = student.academic_session or application_session

    if not starting_session:
        return None, 'No academic session is linked to your student record yet. Please contact the ICT Unit.'

    sessions = list(ordered_academic_sessions())
    if not sessions:
        return starting_session, ''

    starting_index = next(
        (index for index, session in enumerate(sessions) if session.pk == starting_session.pk),
        None,
    )
    if starting_index is None:
        return starting_session, (
            'Your admission session could not be matched to the session calendar. '
            'The portal is using your linked student session for now.'
        )

    target_index = starting_index + student_level_session_offset(student)
    if target_index >= len(sessions):
        return sessions[-1], (
            'Your next academic session has not been configured yet. '
            'The portal is using the latest available session for your record.'
        )

    return sessions[target_index], ''


def resolve_student_semester(student, academic_session, require_active=False):
    if not academic_session:
        return None, (student.semester or '').strip()

    student_semester_name = (student.semester or '').strip()
    session_semesters = Semester.objects.filter(academic_session=academic_session)

    current_semester_instance = None
    if student_semester_name:
        current_semester_instance = (
            session_semesters.filter(name__iexact=student_semester_name, is_active=True)
            .order_by('-added_date', '-id')
            .first()
        )

    if not current_semester_instance:
        current_semester_instance = (
            session_semesters.filter(is_active=True)
            .order_by('-added_date', '-id')
            .first()
        )

    if not require_active and not current_semester_instance and student_semester_name:
        current_semester_instance = (
            session_semesters.filter(name__iexact=student_semester_name)
            .order_by('-added_date', '-id')
            .first()
        )

    current_semester = current_semester_instance.name if current_semester_instance else student_semester_name
    return current_semester_instance, current_semester

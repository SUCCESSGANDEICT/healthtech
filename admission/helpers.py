from .models import (SponsorDetails,
                     EducationRecord,
                     ApplicantResult,
                     ApplicationSubmission,
                     PreApplicant,
                     Admitted,
                     )
from payments.models import Payment

ADMISSION_NOT_OFFERED_MESSAGE = (
    'Admission has not yet been offered. Acceptance Fee will become available after admission is granted.'
)


def get_applicant_academic_session(applicant):
    pre_applicant = PreApplicant.objects.select_related('academic_session').filter(applicant=applicant).first()
    if pre_applicant and pre_applicant.academic_session_id:
        return pre_applicant.academic_session

    submission = ApplicationSubmission.objects.select_related('academic_sess').filter(applicant=applicant).first()
    if submission and submission.academic_sess_id:
        return submission.academic_sess

    return None


def has_verified_application_fee(applicant, academic_session=None):
    academic_session = academic_session or get_applicant_academic_session(applicant)
    if not academic_session:
        return None

    return (
        Payment.objects.filter(
            user=applicant,
            academic_sess=academic_session,
            payment_source='Application Form',
            verified=True,
            status='Approved',
        )
        .select_related('academic_sess')
        .order_by('-payment_date', '-id')
        .first()
    )


def get_valid_admission_offer(applicant, academic_session=None, course=None):
    admitted_records = Admitted.objects.filter(applicant=applicant)
    submitted_applications = ApplicationSubmission.objects.filter(
        applicant=applicant,
        confirm=True,
        is_admitted=True,
    )

    if academic_session:
        admitted_records = admitted_records.filter(academic_session=academic_session)
        submitted_applications = submitted_applications.filter(academic_sess=academic_session)

    if course:
        admitted_records = admitted_records.filter(admitted_course=course)
        submitted_applications = submitted_applications.filter(course_name=course)

    admitted_record = admitted_records.select_related(
        'academic_session',
        'admitted_course',
    ).order_by('-admitted_at', '-id').first()

    if not admitted_record or not submitted_applications.exists():
        return None

    return admitted_record


def has_valid_admission_offer(applicant, academic_session=None, course=None):
    return get_valid_admission_offer(applicant, academic_session, course) is not None


def get_sponsor_details(user):
    try:
        return SponsorDetails.objects.get(applicant=user)
    except SponsorDetails.DoesNotExist:
        return None


def sponsor_details_contact_complete(sponsor_details):
    if not sponsor_details:
        return False

    # Check if any of the required sponsor details fields are None or empty
    for field in (
        sponsor_details.full_name,
        sponsor_details.occupation,
        sponsor_details.phone_number,
        sponsor_details.home_address,
        sponsor_details.sponsor_relationship,
    ):
        if not field:
            return False

    # Check if email is provided, if not, ignore it
    if sponsor_details.email:
        if not sponsor_details.email.strip():
            return False

    return True


def get_education(user):
    try:
        return EducationRecord.objects.filter(applicant=user)
    except EducationRecord.DoesNotExist:
        return None


def applicant_education_complete(education_records):
    if not education_records:
        return False

    for record in education_records:
        if not (
            record.school_name and
            record.certificate and
            record.admitted_year and
            record.end_year
        ):
            return False

    return True


def get_result(user):
    try:
        return ApplicantResult.objects.filter(applicant=user)
    except ApplicantResult.DoesNotExist:
        return None


def applicant_result_complete(applicant_results):
    if len(applicant_results) < 5:
        return False
    
    for result in applicant_results:
        if not (
            result.subject_name and
            result.result_name and
            result.subject_grade and
            result.exam_number and
            result.exam_year
        ):
            return False
    return True


# Function to check if Applicant has submitted Application
def get_application_submission(user):
    try:
        return ApplicationSubmission.objects.get(applicant=user)
    except ApplicationSubmission.DoesNotExist:
        return None


def application_submitted(application_submission):
    return application_submission is not None and application_submission.confirm 

from django.shortcuts import render, redirect, get_object_or_404
from copy import copy
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.template.loader import render_to_string
from xhtml2pdf import pisa
from django.conf import settings
from . import forms
from io import BytesIO
from django.http import HttpResponse
from django.http import JsonResponse
from .forms import SearchForm, PaymentForm, LateRegistrationFeeForm, PaymentReportForm, FullSchoolFeesPaymentForm, OtherChargesFeePaymentForm, FirstSemesterFeesPaymentForm, SecondSemesterFeesPaymentForm, CautionFeePaymentForm, FeePaymentForm, AcceptanceFeePaymentForm, SchoolFeePaymentForm, ScreeningFeePaymentForm, ApplicationFormPaymentForm
from admission.models import PreApplicant, ApplicationSubmission
from admission.forms import get_application_fee_category
from .models import Payment
from datetime import datetime
import datetime
from datetime import date
from decimal import Decimal
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.utils.timezone import make_aware
from fees.models import ApplicationFee, Fee, StudentFeePayment
from fees.utils import (
    FeeConfigurationError,
    get_application_fee,
    get_school_fee,
    get_school_fee_amount as get_configured_school_fee_amount,
    get_user_indigene_type,
    normalize_indigene_type,
)
from django.db import transaction
from django.db.models import Count, Sum
from decimal import Decimal
from datetime import datetime
from admission.models import ApplicantValidation
from django.db.models import Q
from fees.models import Fee
from academics.models import Student, Level, AcademicSession, Semester, CourseEnrollment
from accounts.models import GRADUATE_ROLE, STUDENT_ROLE, UserActivityLog
from accounts.student_academic import get_student_application_session, resolve_student_academic_session, resolve_student_semester
from admission.helpers import get_application_submission, application_submitted, has_verified_application_fee
from admission.helpers import ADMISSION_NOT_OFFERED_MESSAGE, has_valid_admission_offer
from django.utils.encoding import force_str
from urllib.parse import quote, urlencode
import hashlib
import hmac
import json
import logging
import re
from django.views.decorators.csrf import csrf_exempt
from .services import get_or_create_pending_payment, verify_and_finalize_payment
from .status import (
    is_successful_payment,
    normalize_payment_source,
    payment_attempt_message,
    payment_attempt_state,
    payment_state,
    payments_for_fee,
    successful_payment_filter,
)

logger = logging.getLogger(__name__)

CANDIDATE_FEE_INTENTS = {
    'application-form': 'Application Form',
    'screening-fee': 'Screening Fee',
    'acceptance-fee': 'Acceptance Fee',
    'caution-fee': 'Caution Fee',
    'other-charges': 'Other Charges',
}
CANDIDATE_FEE_SLUGS = {source: slug for slug, source in CANDIDATE_FEE_INTENTS.items()}
CANDIDATE_FEE_FORMS = {
    'Application Form': ApplicationFormPaymentForm,
    'Screening Fee': ScreeningFeePaymentForm,
    'Acceptance Fee': AcceptanceFeePaymentForm,
    'Caution Fee': CautionFeePaymentForm,
    'Other Charges': OtherChargesFeePaymentForm,
}
CANDIDATE_FEE_SEQUENCE = (
    'Application Form',
    'Screening Fee',
    'Acceptance Fee',
    'Caution Fee',
    'Other Charges',
)
STUDENT_CANDIDATE_FEE_SEQUENCE = (
    'Application Form',
    'Acceptance Fee',
    'Caution Fee',
    'Other Charges',
)

TUITION_FEE_NAME_ALIASES = {
    'SCHOOLFEES',
    'SCHOOLFEE',
    'TUITION',
    'TUITIONFEE',
    'TUITIONFEES',
}
NON_TUITION_FEE_NAME_ALIASES = {
    'LATEREGISTRATION',
    'LATEREGISTRATIONFEE',
    'LATEREGISTRATON',
}


def _normalize_fee_name(value):
    return re.sub(r'[^A-Za-z0-9]+', '', str(value or '')).upper()


def _active_fee_filter(model):
    if any(field.name == 'is_active' for field in model._meta.fields):
        return {'is_active': True}
    if any(field.name == 'active' for field in model._meta.fields):
        return {'active': True}
    if any(field.name == 'enabled' for field in model._meta.fields):
        return {'enabled': True}
    return {}


def get_tuition_fee_for_option(level, semester, academic_session, indigene_type, course_of_study=None):
    if not level or not semester or not academic_session:
        raise FeeConfigurationError('Tuition fee setup cannot be checked because level, semester, or session is missing.')
    if not course_of_study:
        raise FeeConfigurationError('Course of Study/Programme is required to select tuition fees.')

    fees = list(
        Fee.objects.filter(
            academic_session=academic_session,
            course_of_study=course_of_study,
            level=level,
            semester=semester,
            indigene_type=indigene_type,
            amount__isnull=False,
            amount__gt=0,
            **_active_fee_filter(Fee),
        ).order_by('name', '-added_date', '-id')
    )
    if not fees:
        raise FeeConfigurationError(
            f'{semester} Tuition Fee setup was not found for {level}, {academic_session}, {course_of_study}, {indigene_type}.'
        )

    tuition_fees = [
        fee for fee in fees
        if _normalize_fee_name(fee.name) in TUITION_FEE_NAME_ALIASES
    ]
    if len(tuition_fees) == 1:
        return tuition_fees[0]
    if len(tuition_fees) > 1:
        raise FeeConfigurationError(
            f'Multiple {semester} Tuition Fee records are configured for {level}, {academic_session}, {course_of_study}, {indigene_type}.'
        )

    non_tuition_names = NON_TUITION_FEE_NAME_ALIASES
    non_late_fees = [
        fee for fee in fees
        if _normalize_fee_name(fee.name) not in non_tuition_names
    ]
    if len(non_late_fees) == 1:
        return non_late_fees[0]
    if non_late_fees:
        configured_names = ', '.join(sorted({fee.name for fee in non_late_fees}))
        raise FeeConfigurationError(
            f'Multiple possible tuition fee records are configured for {semester}: {configured_names}. Use School Fees or Tuition as the fee name.'
        )

    configured_names = ', '.join(sorted({fee.name for fee in fees}))
    raise FeeConfigurationError(
        f'{semester} Tuition Fee setup was not found. Existing {semester} fee record(s) are not tuition fees: {configured_names}.'
    )


def get_candidate_fee_intent(request):
    intent = request.POST.get('payment_source_intent') or request.GET.get('fee')
    if not intent:
        return None
    return CANDIDATE_FEE_INTENTS.get(intent.strip().lower())


def get_next_unpaid_candidate_fee(payment_flags, payment_sequence=CANDIDATE_FEE_SEQUENCE, acceptance_fee_available=True):
    for payment_source in payment_sequence:
        if payment_source == 'Acceptance Fee' and not acceptance_fee_available:
            return None
        if not payment_flags.get(payment_source, False):
            return payment_source
    return None


def acceptance_fee_stage_reached(payment_flags, payment_sequence):
    for payment_source in payment_sequence:
        if payment_source == 'Acceptance Fee':
            return True
        if not payment_flags.get(payment_source, False):
            return False
    return False


def validate_candidate_fee_request(request, requested_source, next_payment_source):
    if not requested_source:
        return True
    if requested_source == next_payment_source:
        return True
    if next_payment_source:
        messages.warning(
            request,
            f'{requested_source} cannot be initiated now. Please complete {next_payment_source} payment first.'
        )
    else:
        messages.info(request, f'{requested_source} payment is already completed for this academic session.')
    return False


def get_candidate_fee_category(user, pre_applicant, payment_source, academic_session, indigene_type):
    category = get_application_fee_category(
        pre_applicant.course_name,
        academic_session,
        fee_name=payment_source,
        indigene_type=indigene_type,
    )
    if not category:
        raise FeeConfigurationError(
            f'No {payment_source} fee category is configured for this programme, {academic_session}, {indigene_type}.'
        )
    return category


def expected_candidate_fee_amount(category, payment_source, academic_session, indigene_type):
    return get_application_fee(category, payment_source, academic_session, indigene_type).amount


def form_amount_matches_configured_fee(form, expected_amount):
    submitted_amount = form.cleaned_data.get('amount')
    if submitted_amount is None:
        submitted_amount = form.fields['amount'].initial
    try:
        return Decimal(str(submitted_amount)) == Decimal(str(expected_amount))
    except Exception:
        return False


def get_user_academic_session(user):
    student = Student.objects.select_related('academic_session').filter(candidate=user, graduated=False).first()
    if student and student.academic_session:
        academic_session, _message = resolve_student_academic_session(student, user)
        return academic_session or student.academic_session

    pre_applicant = PreApplicant.objects.select_related('academic_session').filter(applicant=user).first()
    if pre_applicant and pre_applicant.academic_session:
        return pre_applicant.academic_session

    return AcademicSession.get_application_session()


def get_application_academic_session(user):
    student = Student.objects.filter(candidate=user).first()
    if student:
        return get_student_application_session(user)

    pre_applicant = PreApplicant.objects.select_related('academic_session').filter(applicant=user).first()
    if pre_applicant and pre_applicant.academic_session:
        return pre_applicant.academic_session

    submitted_application = ApplicationSubmission.objects.select_related('academic_sess').filter(applicant=user).first()
    if submitted_application and submitted_application.academic_sess:
        return submitted_application.academic_sess

    return get_user_academic_session(user)


def get_verified_payment_for_session(user, payment_source, academic_session):
    indigene_type = get_user_indigene_type(user)
    return (
        Payment.objects.select_related('user', 'academic_sess')
        .filter(
            user=user,
            verified=True,
            status='Approved',
            payment_source=payment_source,
            academic_sess=academic_session,
            indigene_type=indigene_type,
        )
        .order_by('-payment_date', '-id')
        .first()
    )


def get_verified_payments_for_session(user, academic_session, payment_sources):
    indigene_type = get_user_indigene_type(user)
    return Payment.objects.filter(
        user=user,
        verified=True,
        status='Approved',
        academic_sess=academic_session,
        payment_source__in=payment_sources,
        indigene_type=indigene_type,
    )


def get_active_semester_for_session(academic_session):
    return Semester.get_active_for_session(academic_session)


def get_student_payment_context(user):
    student = Student.objects.select_related('academic_session', 'course').filter(candidate=user, graduated=False).first()
    if not student:
        return None, None, None, ''

    academic_session, session_message = resolve_student_academic_session(student, user)
    if not academic_session:
        return student, None, None, session_message

    semester, semester_name = resolve_student_semester(student, academic_session, require_active=True)
    if not semester:
        message = (
            f'No active semester is configured for your academic session ({academic_session}). '
            'Please contact the Academic Office or ICT Unit.'
        )
        return student, academic_session, None, message

    return student, academic_session, semester, session_message


def get_student_acceptance_fee_gate(user, student, indigene_type):
    admission_session = get_student_application_session(user)
    if not admission_session:
        return False, (
            'Acceptance Fee setup could not be checked because your admission session is missing. '
            'Please contact ICT/Admin for resolution.'
        )

    if not has_valid_admission_offer(user, admission_session, student.course):
        return False, ADMISSION_NOT_OFFERED_MESSAGE

    fee_category = get_application_fee_category(
        student.course,
        admission_session,
        fee_name='Acceptance Fee',
        indigene_type=indigene_type,
    )
    if not fee_category:
        return False, (
            'Acceptance Fee setup was not found for your admission session/programme. '
            'Please contact ICT/Admin for resolution.'
        )

    try:
        get_application_fee(fee_category, 'Acceptance Fee', admission_session, indigene_type)
    except FeeConfigurationError:
        return False, (
            'Acceptance Fee setup was not found for your admission session/programme. '
            'Please contact ICT/Admin for resolution.'
        )

    acceptance_state = payment_state(
        student,
        user,
        admission_session,
        'Acceptance Fee',
        indigene_type=indigene_type,
    )
    if acceptance_state['has_successful_payment']:
        return True, 'Acceptance Fee paid and verified.'
    latest_payment = acceptance_state['latest_payment']
    attempt_state = payment_attempt_state(latest_payment)
    if attempt_state == 'initiated':
        return False, 'Acceptance Fee payment was initiated but checkout was not completed. Please complete it before paying tuition.'
    if attempt_state == 'failed':
        return False, 'Acceptance Fee payment was not successful. Please retry it before paying tuition.'
    if attempt_state == 'awaiting_verification':
        return False, 'Acceptance Fee payment is awaiting verification before tuition.'
    return False, 'Acceptance Fee is configured but has not been paid. Please pay Acceptance Fee before tuition.'


def get_student_prerequisite_fee_gate(user, student, indigene_type):
    admission_session = get_student_application_session(user)
    if not admission_session:
        return False, (
            'Application Form Fee setup could not be checked because your admission session is missing. '
            'Please contact ICT/Admin for resolution.'
        )

    for payment_source in STUDENT_CANDIDATE_FEE_SEQUENCE:
        display_name = 'Application Form Fee' if payment_source == 'Application Form' else payment_source
        if payment_source == 'Acceptance Fee' and not has_valid_admission_offer(user, admission_session, student.course):
            prerequisite_state = payment_state(
                student,
                user,
                admission_session,
                payment_source,
                indigene_type=indigene_type,
            )
            if prerequisite_state['has_successful_payment']:
                continue
            return False, ADMISSION_NOT_OFFERED_MESSAGE

        fee_category = get_application_fee_category(
            student.course,
            admission_session,
            fee_name=payment_source,
            indigene_type=indigene_type,
        )
        if not fee_category:
            return False, f'{display_name} setup was not found for your programme/session. Please contact ICT/Admin.'

        try:
            get_application_fee(fee_category, payment_source, admission_session, indigene_type)
        except FeeConfigurationError:
            return False, f'{display_name} setup was not found for your programme/session. Please contact ICT/Admin.'

        prerequisite_state = payment_state(
            student,
            user,
            admission_session,
            payment_source,
            indigene_type=indigene_type,
        )
        if prerequisite_state['has_successful_payment']:
            continue
        latest_payment = prerequisite_state['latest_payment']
        attempt_state = payment_attempt_state(latest_payment)
        if attempt_state == 'initiated':
            return False, (
                f'{display_name} payment was initiated but checkout was not completed. '
                f'Please complete {display_name} before tuition.'
            )
        if attempt_state == 'failed':
            return False, (
                f'{display_name} payment was not successful. '
                f'Please retry {display_name} before tuition.'
            )
        if attempt_state == 'awaiting_verification':
            return False, f'{display_name} payment is awaiting verification before tuition.'
        return False, f'{display_name} is configured but has not been paid. Please pay {display_name} before tuition.'

    return True, 'Student prerequisite fees are paid and verified.'


def get_late_registration_fee_gate(user, student, academic_session, semester, indigene_type):
    """Enforce the existing second-semester late-registration rule before tuition."""
    semester_name = getattr(semester, 'name', semester)
    if (
        semester_name != 'Second Semester'
        or not academic_session.closing_date
        or date.today() < academic_session.closing_date
    ):
        return True, ''

    level = Level.objects.filter(name=student.current_level).first()
    if level and StudentFeePayment.objects.filter(
        student=user,
        academic_session=academic_session,
        student_level=level,
        course_of_study=student.course,
    ).filter(Q(has_paid_second_semester=True) | Q(has_paid_full_session=True)).exists():
        return True, ''

    late_payment = payment_state(
        student,
        user,
        academic_session,
        'Late Registration',
        indigene_type=indigene_type,
    )
    if late_payment['has_successful_payment']:
        return True, 'Late Registration Fee paid and verified.'

    category = get_application_fee_category(
        student.course,
        academic_session,
        fee_name='Late Registration',
        indigene_type=indigene_type,
    )
    try:
        fee = get_application_fee(category, 'Late Registration', academic_session, indigene_type) if category else None
    except FeeConfigurationError:
        fee = None
    if not fee:
        return False, 'Late Registration Fee setup was not found for your programme/session. Please contact ICT/Admin.'
    return False, 'Late Registration Fee must be paid and verified before School Fees payment.'

    return True, 'All prerequisite student fees are paid and verified.'


def _school_fee_payment_type_for_amount(student, academic_session, indigene_type, amount):
    for semester_name in ('Full Session', 'First Semester', 'Second Semester'):
        try:
            fee = get_tuition_fee_for_option(
                student.current_level,
                semester_name,
                academic_session,
                indigene_type,
                student.course,
            )
        except FeeConfigurationError:
            continue
        if Decimal(str(fee.amount)) == Decimal(str(amount)):
            return semester_name
    return ''


TUITION_RECEIPT_SCOPE_ALIASES = {
    'FIRSTSEMESTER': 'First Semester',
    'FIRSTSEMESTERTUITION': 'First Semester',
    'FIRSTSEMESTERTUITIONFEE': 'First Semester',
    'FIRSTSEMESTERSCHOOLFEES': 'First Semester',
    '1STSEMESTER': 'First Semester',
    'SECONDSEMESTER': 'Second Semester',
    'SECONDSEMESTERTUITION': 'Second Semester',
    'SECONDSEMESTERTUITIONFEE': 'Second Semester',
    'SECONDSEMESTERSCHOOLFEES': 'Second Semester',
    '2NDSEMESTER': 'Second Semester',
    'FULLSESSION': 'Full Session',
    'FULLSESSIONTUITION': 'Full Session',
    'FULLSESSIONTUITIONFEE': 'Full Session',
    'FULLSESSIONSCHOOLFEES': 'Full Session',
}


def normalize_tuition_receipt_scope(value):
    key = re.sub(r'[^A-Za-z0-9]+', '', str(value or '')).upper()
    return TUITION_RECEIPT_SCOPE_ALIASES.get(key, '')


def resolve_school_fee_receipt_scope(payment, student):
    stored_scope = normalize_tuition_receipt_scope(getattr(payment, 'fee_category', ''))
    if stored_scope:
        return stored_scope

    if not student:
        return ''

    matching_scopes = []
    for scope in ('First Semester', 'Second Semester', 'Full Session'):
        try:
            configured_fee = get_tuition_fee_for_option(
                student.current_level,
                scope,
                payment.academic_sess,
                normalize_indigene_type(payment.indigene_type),
                student.course,
            )
        except FeeConfigurationError:
            continue
        if Decimal(str(configured_fee.amount)) == Decimal(str(payment.amount)):
            matching_scopes.append(scope)

    if len(matching_scopes) == 1:
        return matching_scopes[0]
    return ''


def get_tuition_payment_state(user, student, academic_session, indigene_type):
    level_instance = Level.objects.filter(name=student.current_level).first()
    base_state = {
        'can_pay_first_semester': False,
        'can_pay_second_semester': False,
        'can_pay_full_session': False,
        'has_paid_first_semester_fee': False,
        'has_paid_second_semester_fee': False,
        'has_paid_full_session_fee': False,
        'tuition_status': '',
        'first_semester_fee_amount': Decimal('0'),
        'second_semester_fee_amount': Decimal('0'),
        'full_session_fee_amount': Decimal('0'),
        'tuition_payment_message': '',
        'tuition_action_label': 'Proceed with Payment',
        'semester_choices': [],
        'tuition_options': [],
        'student_fee': None,
    }
    if not level_instance:
        base_state['tuition_payment_message'] = 'Student level was not found. Please contact ICT/Admin.'
        return base_state

    fee_errors = []
    for context_key, semester_name in (
        ('first_semester_fee_amount', 'First Semester'),
        ('second_semester_fee_amount', 'Second Semester'),
        ('full_session_fee_amount', 'Full Session'),
    ):
        try:
            base_state[context_key] = get_tuition_fee_for_option(
                student.current_level,
                semester_name,
                academic_session,
                indigene_type,
                student.course,
            ).amount
        except FeeConfigurationError as exc:
            fee_errors.append(str(exc))

    student_fee = (
        StudentFeePayment.objects.filter(
            student=user,
            academic_session=academic_session,
            student_level=level_instance,
            course_of_study=student.course,
            is_indigene=(indigene_type == "INDIGENE"),
        )
        .order_by('id')
        .first()
    )
    base_state['student_fee'] = student_fee

    pending_school_fee = (
        payments_for_fee(
            student,
            user,
            academic_session,
            'School Fees',
            indigene_type=indigene_type,
        )
        .exclude(successful_payment_filter())
        .order_by('-payment_date', '-id')
        .first()
    )
    if pending_school_fee:
        pending_type = _school_fee_payment_type_for_amount(
            student,
            academic_session,
            indigene_type,
            pending_school_fee.amount,
        ) or 'Tuition'
        if pending_school_fee.fee_category in {'First Semester', 'Second Semester', 'Full Session'}:
            pending_type = pending_school_fee.fee_category
        attempt_context = payment_attempt_message(
            f'{pending_type} Tuition',
            payment_attempt_state(pending_school_fee),
        )
        base_state['tuition_status'] = attempt_context['status']
        base_state['tuition_payment_message'] = attempt_context['message']
        if pending_type in {'First Semester', 'Second Semester', 'Full Session'} and attempt_context.get('can_pay'):
            base_state['tuition_action_label'] = attempt_context.get('action_label') or 'Pay Now'
            base_state['semester_choices'].append((pending_type, f'{pending_type} Tuition Fee'))
            if pending_type == 'First Semester':
                base_state['can_pay_first_semester'] = True
            elif pending_type == 'Second Semester':
                base_state['can_pay_second_semester'] = True
            elif pending_type == 'Full Session':
                base_state['can_pay_full_session'] = True
            base_state['tuition_options'].append({
                'name': f'{pending_type} Tuition Fee',
                'amount': pending_school_fee.amount,
                'session': academic_session,
                'programme': student.course,
                'level': student.current_level,
                'semester': pending_type,
                'can_pay': True,
                'action_label': attempt_context.get('action_label') or 'Pay Now',
            })
        return base_state

    verified_school_fee_payments = payments_for_fee(
        student,
        user,
        academic_session,
        'School Fees',
        indigene_type=indigene_type,
        successful_only=True,
    )
    has_verified_first_semester_payment = (
        verified_school_fee_payments.filter(fee_category='First Semester').exists()
    )
    has_verified_second_semester_payment = (
        verified_school_fee_payments.filter(fee_category='Second Semester').exists()
    )
    has_verified_full_session_payment = (
        verified_school_fee_payments.filter(fee_category='Full Session').exists()
        or (bool(base_state['full_session_fee_amount']) and verified_school_fee_payments.filter(amount=base_state['full_session_fee_amount']).exists())
    )

    has_paid_first = bool(student_fee and student_fee.has_paid_first_semester) or has_verified_first_semester_payment or has_verified_full_session_payment
    has_paid_second = bool(student_fee and student_fee.has_paid_second_semester) or has_verified_second_semester_payment or has_verified_full_session_payment
    has_paid_full = bool(student_fee and student_fee.has_paid_full_session) or has_verified_full_session_payment
    base_state.update({
        'has_paid_first_semester_fee': has_paid_first,
        'has_paid_second_semester_fee': has_paid_second,
        'has_paid_full_session_fee': has_paid_full,
    })

    if has_verified_full_session_payment:
        base_state['tuition_status'] = 'full_session_paid'
        base_state['tuition_payment_message'] = 'Full session tuition has already been paid.'
        return base_state
    if has_paid_full or (has_paid_first and has_paid_second):
        base_state['tuition_status'] = 'complete'
        base_state['tuition_payment_message'] = 'Tuition payment for this session is complete.'
        return base_state

    if has_paid_first and not has_paid_second:
        if base_state['second_semester_fee_amount']:
            base_state['tuition_status'] = 'second_semester_due'
            base_state['can_pay_second_semester'] = True
            base_state['semester_choices'].append(('Second Semester', 'Second Semester Tuition Fee'))
            base_state['tuition_options'].append({
                'name': 'Second Semester Tuition Fee',
                'amount': base_state['second_semester_fee_amount'],
                'session': academic_session,
                'programme': student.course,
                'level': student.current_level,
                'semester': 'Second Semester',
                'can_pay': True,
                'action_label': 'Pay Now',
            })
            base_state['tuition_action_label'] = 'Pay Now'
        else:
            base_state['tuition_status'] = 'second_semester_setup_missing'
            base_state['tuition_payment_message'] = 'Second Semester Tuition Fee setup was not found for your programme/session. Please contact ICT/Admin.'
        return base_state

    if base_state['first_semester_fee_amount']:
        base_state['tuition_status'] = 'first_semester_due'
        base_state['can_pay_first_semester'] = True
        base_state['semester_choices'].append(('First Semester', 'First Semester Tuition Fee'))
        base_state['tuition_options'].append({
            'name': 'First Semester Tuition Fee',
            'amount': base_state['first_semester_fee_amount'],
            'session': academic_session,
            'programme': student.course,
            'level': student.current_level,
            'semester': 'First Semester',
            'can_pay': True,
            'action_label': 'Pay Now',
        })
        base_state['tuition_action_label'] = 'Pay Now'
    if base_state['full_session_fee_amount']:
        base_state['can_pay_full_session'] = True
        base_state['semester_choices'].append(('Full Session', 'Full Session Tuition Fee'))
        base_state['tuition_options'].append({
            'name': 'Full Session Tuition Fee',
            'amount': base_state['full_session_fee_amount'],
            'session': academic_session,
            'programme': student.course,
            'level': student.current_level,
            'semester': 'Full Session',
            'can_pay': True,
            'action_label': 'Pay Now',
        })
        base_state['tuition_action_label'] = 'Pay Now'

    if not base_state['semester_choices']:
        base_state['tuition_payment_message'] = ' '.join(fee_errors) or 'Tuition fee setup was not found for your programme/session. Please contact ICT/Admin.'
    elif fee_errors:
        missing_required = [
            message for message in fee_errors
            if ('Full Session' not in message and 'Second Semester' not in message)
        ]
        if missing_required:
            base_state['tuition_payment_message'] = ' '.join(f'{message} Please contact ICT/Admin.' for message in missing_required)

    return base_state


def validate_tuition_payment_choice(user, student, academic_session, indigene_type, selected_semester_name):
    state = get_tuition_payment_state(user, student, academic_session, indigene_type)
    allowed = {
        'First Semester': state['can_pay_first_semester'],
        'Second Semester': state['can_pay_second_semester'],
        'Full Session': state['can_pay_full_session'],
    }
    if selected_semester_name not in allowed:
        return False, state, 'Invalid tuition payment option selected.'
    if not allowed[selected_semester_name]:
        return False, state, state['tuition_payment_message'] or f'{selected_semester_name} Tuition Fee cannot be paid at this stage.'
    return True, state, ''


def build_payment_form(bound_data=None, academic_session=None):
    queryset = AcademicSession.objects.filter(pk=academic_session.pk) if academic_session else AcademicSession.objects.filter(is_active=True).order_by('-start_date', '-end_date', '-name')
    return PaymentForm(bound_data, academic_session_queryset=queryset, initial_session=academic_session)


def get_application_fee_for_session(category, name, academic_session, indigene_type):
    return get_application_fee(category, name, academic_session, indigene_type)


def get_school_fee_for_session(level, semester, academic_session, indigene_type, name=None, course_of_study=None):
    return get_school_fee(level, semester, academic_session, indigene_type, name=name, course_of_study=course_of_study)


def get_school_fee_amount(level, semester, academic_session, indigene_type, name=None, course_of_study=None):
    return get_configured_school_fee_amount(level, semester, academic_session, indigene_type, name=name, course_of_study=course_of_study)

# Search and Pay for Application Fee
@login_required(login_url='accounts_login')
def search_pay_application_form(request):
    logged_user = request.user
    if logged_user.profile.role == 'MIS ADMIN-2' or logged_user.profile.role == 'Super Admin' or logged_user.profile.role == 'Admin':
        # Log user activity
        UserActivityLog.objects.create(user=request.user, action='Visited Search and Pay Application Form')
        form = SearchForm(request.GET or None)
        if request.method == "GET" and form.is_valid():
            applicant_no = form.cleaned_data['search_applicant']
            # Get the first 6 digits (uid)
            uid = applicant_no[:6]

            # Filter for application to check if it ends with the 6 digits UID
            applicant = PreApplicant.objects.filter(application_number__endswith=uid).first()

            if applicant:
                # Redirect to the next view with the applicant details
                return redirect('pay_application_fee', applicant_id=applicant.id)
            else:
                messages.error(request, f'Applicant with application number {applicant_no} not found.')
        else:
            form = SearchForm()
    else:
        messages.warning(request, 'You are Not Authorised')
        return redirect('accounts_dashboard')
    context = {
        'form': form,
        'page_title': 'Search Applicant',
    }
    return render(request, 'payments/search_pay_application_form.html', context)


# Banker View for Application Form Payment
login_required(login_url='accounts_login')
def pay_application_form(request, applicant_id):
    logged_user = request.user

    # Check if logged user is a banker
    if not (logged_user.profile.role == 'MIS ADMIN-2' or logged_user.profile.role =='Super Admin' or logged_user.profile.role == 'Admin'):
        messages.warning(request, 'You are Not Authorised')
        return redirect('accounts_dashboard')
    
    # Log user activity
    UserActivityLog.objects.create(user=request.user, action='Visited Pay Application Form')
    try:
        # Get Applicant from Pre_Application Table and compare with ID
        applicant = get_object_or_404(PreApplicant, id=applicant_id)
       
        student = Student.objects.select_related('course').filter(candidate=applicant.applicant).first()
        applicant_session = get_student_application_session(applicant.applicant) if student else applicant.academic_session
        fee_course = student.course if student else applicant.course_name
        if student and not applicant_session:
            messages.error(request, 'Application Form fee setup could not be checked because the admission session is missing. Please contact ICT/Admin.')
            return redirect('search_applicant')
        indigene_type = get_user_indigene_type(applicant.applicant)
        payment_source = None
        fee_amount = None
        fee_category = get_application_fee_category(
            fee_course,
            applicant_session,
            fee_name='Application Form',
            indigene_type=indigene_type,
        )
        if not fee_category:
            messages.error(request, 'The selected programme does not have a configured application fee category for this academic session.')
            return redirect('search_applicant')

        try:
            application_fee = get_application_fee_for_session(fee_category, 'Application Form', applicant_session, indigene_type)
        except FeeConfigurationError as exc:
            messages.error(request, str(exc))
            return redirect('search_applicant')  # Redirect to search if fee not found
        
        # Set Appplication Fee Amount and payment source initially set to None
        fee_amount = application_fee.amount
        payment_source = application_fee.name       

        form = build_payment_form(request.POST or None, academic_session=applicant_session)

        # Check if the submitted form method is POST and the form is valid
        if request.method == 'POST' and form.is_valid():
            academic_sess = form.cleaned_data['academic_sess']

            # Create Payment instance
            try:
                with transaction.atomic():
                    # Check if payment and applicant_fee already exist for the specified conditions
                    check_payment = Payment.objects.select_related('user').filter(
                        user=applicant.applicant,
                        payment_source=payment_source,
                        academic_sess=academic_sess,
                        fee_category=fee_category,
                        indigene_type=indigene_type,
                    ).first()
                    if not check_payment:
                        raise Payment.DoesNotExist
                    messages.warning(
                            request,
                            f'Payment already made for {payment_source} for the {academic_sess} Academic Session.'
                        )

            except Payment.DoesNotExist:
                with transaction.atomic():
                    payment = Payment.objects.create(
                            user=applicant.applicant,
                            amount=fee_amount,
                            payment_method='Online',
                            status='Successful',
                            payment_source=payment_source,
                            fee_category=fee_category,
                            indigene_type=indigene_type,
                            academic_sess=academic_sess,
                            added_by=logged_user
                        )
                
                    if payment:
                        UserActivityLog.objects.create(user=request.user, action=f'Paid Application Fee for {applicant.first_name} {applicant.last_name} for {academic_sess} Academic Session ')
                        messages.success(
                                request,
                                f'N{fee_amount} paid for {applicant.first_name} {applicant.last_name} for {academic_sess} Academic Session Application Fee'
                            )
                        
                        context = {
                            'applicant': applicant,
                            'payment': payment,
                            'page_title': 'Application Fee Receipt',
                        }
                        return render(request, 'payments/application_receipt.html', context)
            form = build_payment_form(academic_session=applicant_session)

        context = {
            'form': form,
            'applicant': applicant,
            'fee_amount': fee_amount,
            'payment_source': payment_source,
            'page_title': 'Pay Application Form',
        }

        return render(request, 'payments/pay_application_form.html', context)

    except PreApplicant.DoesNotExist:
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')


# Payment Report View for Application Form Payment
login_required(login_url='accounts_login')
def payment_report(request): 
    logged_user = request.user
    if not (logged_user.profile.role == 'MIS ADMIN-2' or logged_user.profile.role == 'Rector'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_profile')
    
    # Log user activity
    UserActivityLog.objects.create(user=request.user, action='Visited Payment Report Page')
    if request.method == 'GET':
        form = PaymentReportForm(request.GET) 
        if form.is_valid():
            academic_session = form.cleaned_data['academic_session']
            payment_source = form.cleaned_data['payment_source']
            indigene_type = form.cleaned_data.get('indigene_type')
            start_date = form.cleaned_data['start_date']
            end_date = form.cleaned_data['end_date']
            current_datetime = datetime.now()

            # Filter transactions by type and date range
            fee_payment = Payment.objects.filter(
                    academic_sess=academic_session,
                    payment_source=payment_source,
                    payment_date__range=[start_date, end_date]
                )
            if indigene_type:
                fee_payment = fee_payment.filter(indigene_type=indigene_type)
             
            # Calculate the total amount for the selected type
            total_amount = fee_payment.values('amount').aggregate(total_amount=Sum('amount'))['total_amount']
            # Total Transactions
            transaction_count = fee_payment.count()
            # Total Number of Accounts Created
            total_pre_applicants = PreApplicant.objects.filter(
                added_date__range=[start_date, end_date]
            ).count()
            # Total Submitted Application Form
            total_submitted_form = ApplicationSubmission.objects.filter(
                submitted_date__range=[start_date, end_date]
            ).count()
            # Total NOT Submitted Forms
            total_unsubmitted_form = total_pre_applicants - total_submitted_form
            # Aggregate the number of payments for each fee category
            fee_category_counts = Payment.objects.filter(
                academic_sess=academic_session,
                payment_source=payment_source,
                payment_date__range=[start_date, end_date]
            )
            if indigene_type:
                fee_category_counts = fee_category_counts.filter(indigene_type=indigene_type)
            fee_category_counts = fee_category_counts.values('fee_category', 'indigene_type').annotate(count=Count('fee_category'), amount_sum=Sum('amount'))
            
            paginator = Paginator(fee_payment, 10)  # Change 2 to your desired number of items per page

            page_number = request.GET.get('page')

            try:
                submitted_applications = paginator.page(page_number)
            except PageNotAnInteger:
                submitted_applications = paginator.page(1)
            except EmptyPage:
                submitted_applications = paginator.page(paginator.num_pages)

            context = {
                'fee_payment': submitted_applications,
                'current_datetime': current_datetime,
                'fee_category_counts': fee_category_counts,
                'total_pre_applicants': total_pre_applicants,
                'page_title': 'Periodic Report',
                'total_submitted_form': total_submitted_form,
                'total_unsubmitted_form': total_unsubmitted_form,
                'total_amount': total_amount,
                'transaction_count': transaction_count,
                'academic_session': academic_session,
                'payment_source': payment_source,
                'indigene_type': indigene_type,
                'start_date': start_date,
                'end_date': end_date,
            }
            # Check for download request
            if request.GET.get('downloaded'):
                pdf_data = generate_payment_report_pdf(request, fee_payment, context)
                if pdf_data:
                    return pdf_data
                else:
                    messages.error(request, 'Error generating PDF')
                    return redirect('payment_report')

    else:
        form = PaymentReportForm()
    context = {
        'page_title': 'Payment Report',
        'form': PaymentReportForm(request.GET),  # Include full GET parameters
    }
    return render(request, 'payments/payment_report.html', context)


# FUNCTION FOR THE GENERATION OF PDF
def generate_payment_report_pdf(request, queryset, context):
    # Render the HTML content of the PDF template with the filtered queryset
    context['fee_payment'] = queryset
    html_content = render_to_string('payments/payment_report_pdf.html', context)

    # Create an in-memory binary stream to store the generated PDF
    result = BytesIO()

    # Generate the PDF using xhtml2pdf
    pdf = pisa.CreatePDF(
        html_content,            # HTML content to convert to PDF
        dest=result,            # Output stream to store the PDF
    )

    # Check if PDF generation was successful
    if not pdf.err:
        # If successful, create an HTTP response with the PDF content
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="payment_report.pdf"'  # Set filename for download
        return response
    else:
        # If PDF generation failed, return None
        return None


# Search and Pay for Screening Fee
@login_required(login_url='accounts_login')
def search_pay_screening_fee(request):
    logged_user = request.user
    if logged_user.profile.role == 'MIS ADMIN-2' or logged_user.profile.role == 'Super Admin':
        # Log user activity
        UserActivityLog.objects.create(user=request.user, action='Visited Search and Pay Screening Fee')
        form = SearchForm(request.GET or None)
        if request.method == "GET" and form.is_valid():
            applicant_no = form.cleaned_data['search_applicant']
            # Get the first 6 digits (uid)
            uid = applicant_no[:6]

            # Filter for application to check if it ends with the 6 digits UID
            applicant = PreApplicant.objects.filter(application_number__endswith=uid).first()

            if applicant:
                # Check if Applicant has submitted application
                candidate = applicant.applicant
                submitted = get_application_submission(candidate)
                if not application_submitted(submitted):
                    messages.warning(request, 'Applicant has NOT Submitted Application Form Yet.')
                else:
                    # Redirect to the next view with the applicant details
                    return redirect('pay_screening_fee', applicant_id=applicant.id)
            else:
                messages.error(request, f'Applicant with application number {applicant_no} not found.')
        else:
            form = SearchForm()
    else:
        return redirect('accounts_dashbaord')
    context = {
        'form': form,
        'page_title': 'Search Applicant',
    }
    return render(request, 'payments/search_pay_application_form.html', context)


# Banker Pay Screening Fee
login_required(login_url='accounts_login')
def pay_screening_fee(request, applicant_id):
    logged_user = request.user

    # Check if logged user is a banker
    if logged_user.profile.role != 'MIS ADMIN-2' or logged_user.profile.role == 'Super Admin':
        return redirect('accounts_dashboard')

    # Log user activity
    UserActivityLog.objects.create(user=request.user, action='Visited Pay Screening Fee')
    try:
        # Get Applicant from Pre_Application Table and compare with ID
        applicant = get_object_or_404(PreApplicant, id=applicant_id)
       
        student = Student.objects.select_related('course').filter(candidate=applicant.applicant).first()
        applicant_session = get_student_application_session(applicant.applicant) if student else applicant.academic_session
        fee_course = student.course if student else applicant.course_name
        if student and not applicant_session:
            messages.error(request, 'Screening Fee setup could not be checked because the admission session is missing. Please contact ICT/Admin.')
            return redirect('search_applicant')
        indigene_type = get_user_indigene_type(applicant.applicant)
        payment_source = None
        fee_amount = None
        fee_category = get_application_fee_category(
            fee_course,
            applicant_session,
            fee_name='Screening Fee',
            indigene_type=indigene_type,
        )
        if not fee_category:
            messages.error(request, 'The selected programme does not have a configured screening fee category for this academic session.')
            return redirect('search_applicant')

        try:
            application_fee = get_application_fee_for_session(fee_category, 'Screening Fee', applicant_session, indigene_type)
        except FeeConfigurationError as exc:
            messages.error(request, str(exc))
            return redirect('search_applicant')  # Redirect to search if fee not found
        
        # Set Appplication Fee Amount and payment source initially set to None
        fee_amount = application_fee.amount
        payment_source = application_fee.name

        form = build_payment_form(request.POST or None, academic_session=applicant_session)

        # Check if the submitted form method is POST and the form is valid
        if request.method == 'POST' and form.is_valid():
            academic_sess = form.cleaned_data['academic_sess']

            # Create Payment instance
            try:
                with transaction.atomic():
                    # Check if payment and applicant_fee already exist for the specified conditions
                    check_payment = Payment.objects.select_related('user').filter(
                        user=applicant.applicant,
                        payment_source=payment_source,
                        academic_sess=academic_sess,
                        fee_category=fee_category,
                        indigene_type=indigene_type,
                    ).first()
                    if not check_payment:
                        raise Payment.DoesNotExist
                    messages.warning(
                            request,
                            f' {payment_source} Payment Already  Made for the {academic_sess} Academic Session.'
                        )

            except Payment.DoesNotExist:
                with transaction.atomic():
                    payment = Payment.objects.create(
                            user=applicant.applicant,
                            amount=fee_amount,
                            payment_method='Online',
                            status='Successful',
                            payment_source=payment_source,
                            fee_category=fee_category,
                            indigene_type=indigene_type,
                            academic_sess=academic_sess,
                            added_by=logged_user
                        )
                
                    if payment:
                        messages.success(
                                request,
                                f'N{fee_amount} paid for {applicant.first_name} {applicant.last_name} for {academic_sess} Academic Session'
                            )
                        context = {
                            'page_title': 'Screening Fee Receipt',
                            'applicant': applicant,
                            'payment': payment,
                        }
                        return render(request, 'payments/application_receipt.html', context)
            form = build_payment_form(academic_session=applicant_session)

        context = {
            'form': form,
            'applicant': applicant,
            'fee_amount': fee_amount,
            'payment_source': payment_source,
            'page_title': 'Pay Screening Fee',
        }

        return render(request, 'payments/pay_application_form.html', context)

    except PreApplicant.DoesNotExist:
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')



# Search and Pay for Acceptance Fee
@login_required(login_url='accounts_login')
def search_pay_acceptance_fee(request):
    logged_user = request.user
    if logged_user.profile.role == 'MIS ADMIN-2' or logged_user.profile.role == 'Super Admin' or logged_user.is_superuser:
        form = SearchForm(request.GET or None)
        # Log user activity
        UserActivityLog.objects.create(user=request.user, action='Visited Search and Pay Acceptance Fee')
        if request.method == "GET" and form.is_valid():
            student_id = form.cleaned_data['search_applicant']
            
            # Filter for student record
            student = Student.objects.filter(student_id=student_id).first()

            if student:
                # Check if Applicant has submitted application
                candidate = student.candidate
                submitted = get_application_submission(candidate)
                if not application_submitted(submitted):
                    messages.warning(request, 'Candidate has NO Application Form.')
                else:
                    # Redirect to the next view with the applicant details
                    return redirect('pay_acceptance_fee', student_id=student.id)
            else:
                messages.error(request, 'Student not found.')
        else:
            form = SearchForm()
    else:
        messages.warning(request, 'You are Not Authorised')
        return redirect('accounts_dashboard')
    context = {
        'form': form,
        'page_title': 'Search Student',
    }
    return render(request, 'payments/search_student.html', context)


# Banker Pay Acceptance Fee
login_required(login_url='accounts_login')
def pay_acceptance_fee(request, student_id):
    logged_user = request.user

    # Check if logged user is a banker
    if not (logged_user.profile.role == 'MIS ADMIN-2' or logged_user.profile.role == 'Super Admin' or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong.')
        return redirect('accounts_dashboard')

    try:
        # Get Student from Table and compare with ID
        student = get_object_or_404(Student, id=student_id)
       
        # Get Student's Form Fee Amount based on Course of Study
        applicant_session = get_student_application_session(student.candidate)
        if not applicant_session:
            messages.error(request, 'Acceptance Fee setup could not be checked because the admission session is missing. Please contact ICT/Admin.')
            return redirect('search_student_pay_acceptance')
        indigene_type = get_user_indigene_type(student.candidate)
        fee_category = get_application_fee_category(
            student.course,
            applicant_session,
            fee_name='Acceptance Fee',
            indigene_type=indigene_type,
        )
        if not fee_category:
            messages.error(request, 'The selected programme does not have a configured acceptance fee category for this academic session.')
            return redirect('search_student_pay_acceptance')

        try:
            acceptance_fee = get_application_fee_for_session(fee_category, 'Acceptance Fee', applicant_session, indigene_type)
        except FeeConfigurationError as exc:
            messages.error(request, str(exc))
            return redirect('search_student_pay_acceptance')  # Redirect to search if fee not found
        
        # Set Candidate Fee Amount and payment source initially set to None
        fee_amount = acceptance_fee.amount
        payment_source = acceptance_fee.name
        

        form = build_payment_form(request.POST or None, academic_session=applicant_session)

        # Check if the submitted form method is POST and the form is valid
        if request.method == 'POST' and form.is_valid():
            academic_sess = form.cleaned_data['academic_sess']

            # Create Payment instance
            try:
                with transaction.atomic():
                    # Check if payment and applicant_fee already exist for the specified conditions
                    check_payment = Payment.objects.select_related('user').filter(
                        user=student.candidate,
                        payment_source=payment_source,
                        academic_sess=academic_sess,
                        fee_category=fee_category,
                        indigene_type=indigene_type,
                    ).first()
                    if not check_payment:
                        raise Payment.DoesNotExist
                    messages.warning(
                            request,
                            f' {payment_source} Payment Already  Made for the {academic_sess} Academic Session.'
                        )

            except Payment.DoesNotExist:
                with transaction.atomic():
                    payment = Payment.objects.create(
                            user=student.candidate,
                            amount=fee_amount,
                            payment_method='Online',
                            status='Successful',
                            payment_source=payment_source,
                            fee_category=fee_category,
                            indigene_type=indigene_type,
                            academic_sess=academic_sess,
                            added_by=logged_user
                        )
                
                    if payment:
                        messages.success(
                                request,
                                f'N{fee_amount} paid for {student.candidate.profile.first_name} {student.candidate.profile.last_name} for {academic_sess} Academic Session'
                            )
                        context = {
                            'page_title': 'Acceptance Fee Receipt',
                            'payment': payment,
                            'student': student,
                        }
                        return render(request, 'payments/acceptance_fee_receipt.html', context)
            form = build_payment_form(academic_session=applicant_session)

        context = {
            'form': form,
            'student': student,
            'fee_amount': fee_amount,
            'payment_source': payment_source,
            'page_title': 'Pay Acceptance Fee',
        }

        return render(request, 'payments/pay_acceptance_fee.html', context)

    except Student.DoesNotExist:
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')


# Search and Pay for School Fee
@login_required(login_url='accounts_login')
def search_pay_school_fee(request):
    logged_user = request.user
    if logged_user.profile.role == 'MIS ADMIN-2':
        form = SearchForm(request.GET or None)
        # Log user activity
        UserActivityLog.objects.create(user=request.user, action='Visited Search and Pay Acceptance Fee')
        
        if request.method == "GET" and form.is_valid():
            student_id = form.cleaned_data['search_applicant']
            
            # Filter for student record
            student = Student.objects.filter(student_id=student_id).first()

            if student:
                # Check if Applicant has submitted application
                candidate = student.candidate
                submitted = get_application_submission(candidate)
                if not application_submitted(submitted):
                    messages.warning(request, 'Candidate has NO Application Form.')
                else:
                    # Redirect to the next view with the applicant details
                    return redirect('student_school_fee', student_id=student.id)
            else:
                messages.error(request, 'Student not found.')
        else:
            form = SearchForm()
    else:
        return redirect('accounts_dashboard')
    context = {
        'form': form,
        'page_title': 'Search Student',
    }
    return render(request, 'payments/search_student.html', context)


# Studend Fee Payment Schedule
@login_required(login_url='accounts_login')
def student_fee_information(request, student_id):
    
    student = get_object_or_404(Student, id=student_id)

    # Initialize variables
    first_semester = False
    second_semester = False
    full_payment = False
    
    # Get Student profile
    profile = student.candidate.profile
    # Retrieve current academic info
    current_level = student.current_level
    
    # Get current Academic Session
    current_session = AcademicSession.get_application_session()
    if not current_session:
        messages.warning(request, 'No Active Session Found')
        return redirect('accounts_login')
    
    # Get student Course/Programme of study
    student_course = student.course
    # Use the indigene_status method
    indigence = profile.indigene_status()
    indigene_type = normalize_indigene_type(indigence)
    student_indigene = indigene_type == "INDIGENE"
    
    # Get Active Semester
    try:
        current_semester = get_active_semester_for_session(current_session)
        if not current_semester:
            messages.warning(request, f'No active semester found for {current_session}.')
            return redirect('search_student_fee')
    except Semester.DoesNotExist:
        messages.warning(request, 'Semester Not Found')
    except Semester.MultipleObjectsReturned:
        messages.warning(request, 'Multiple active Semesters found. Please contact the administrator.')
        return redirect('accounts_login')
    except Exception as e:
        messages.error(request, f'An error occurred: {str(e)}')
        return redirect('accounts_login')

    # Initialize outstanding amount
    outstanding_amount = Decimal('0')

    # Calculate total paid in the current session
    total_paid_current_session = Payment.objects.filter(
        user=student.candidate, academic_sess=current_session, payment_source='School Fees',
        ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')

    form = SchoolFeePaymentForm(request.POST or None)

    # Retrieve fee amount for current level, session, and indigence status
    try:
        fee_full_session = get_school_fee_for_session(current_level, 'Full Session', current_session, indigene_type, course_of_study=student_course)
    except FeeConfigurationError as exc:
        messages.error(request, str(exc))
        return redirect('search_student_fee')
    
    # Retrieve fee amount for current level, session, indigence status, and Current Semester
    if current_level == 'NDI' or current_level == 'NDII' or current_level == 'HNDI':
        
        try:
            fee_current_semester = get_school_fee_for_session(current_level, current_semester.name, current_session, indigene_type, course_of_study=student_course)
        except FeeConfigurationError as exc:
            messages.error(request, str(exc))
            return redirect('search_student_fee')
    
    # Check if Full Session and Current Semester Fee Exists
    if not fee_full_session:
        messages.error(request, 'Fee details not found.')
        return redirect('search_student_fee')
        
    # Check if Full Session Fee amount is NOT
    full_session_fee_amount = fee_full_session.amount if fee_full_session else Decimal('0')
    
   
    # Check if it is Remedial Student
    if current_level == 'CERTIFICATE':
        try:
            fee_current_semester = get_school_fee_for_session(current_level, 'Full Session', current_session, indigene_type, course_of_study=student_course)
        except FeeConfigurationError as exc:
            messages.error(request, str(exc))
            return redirect('search_student_fee')
        
        # Check if Student Fee Schedule (Full and Semester Payment) Exist
        
        if not fee_full_session or not fee_current_semester:
            messages.error(request, 'Fee details not found.')
            return redirect('search_student_fee')
            # Check if Full Session Fee amount is NOT
        full_session_fee_amount = fee_full_session.amount if fee_full_session else Decimal('0')
        # Check if Current Session Fee amount is NOT
        current_semester_fee_amount = fee_current_semester.amount if fee_current_semester else Decimal('0')

        # Calculate total fees for each semester and full session
        if current_level == 'CERTIFICATE':
            total_fee_for_first_semester = get_school_fee_amount(current_level, 'Full Session', current_session, indigene_type, course_of_study=student_course)
            total_fee_for_second_semester = total_fee_for_first_semester

            # Calculate total expected payment
            total_expected_payment = total_fee_for_second_semester

            current_session_balance = total_fee_for_second_semester

            # Find the corresponding Current Student Level instance
            try:
                level_instance = Level.objects.get(name=current_level)
                student_current_level_id = level_instance.id
            except Level.DoesNotExist:
                messages.error(request, 'Level Not Found')
                return redirect('accounts_dashboard')
            
            try:
                level_instance = Level.objects.get(id=student_current_level_id)
            except Level.DoesNotExist:
                messages.error(request, 'Level Not Found')
                return redirect('accounts_dashboard')
            
            if request.method == 'POST' and form.is_valid():
                payment_choice = form.cleaned_data['payment_type']
                pay_reference = form.cleaned_data['pay_reference']
                student_level = student_current_level_id
                student_program = student_course
                current_session = current_session
            
                try:
                    # Check and determine the Payment Choice
                    if payment_choice == 'Semester Fee Payment':
                        paid_amount = current_semester_fee_amount
                        if current_semester.name == 'First Semester':
                            first_semester = True
                        else:
                            second_semester = True
                    elif payment_choice == 'Full Fee Payment':
                        paid_amount = full_session_fee_amount
                        full_payment = True
                except Exception as e:
                    messages.error(request, f'An error occurred during processing: {str(e)}')
            
                try:
                    # Get the Student Level Category
                    level_course_category = student_program.name
                    if 'Remedial Science' in level_course_category:
                        level_category = 'Remedial Science'
                    elif 'Pre-HND' in level_course_category:
                        level_category = 'Pre-HND'
                    elif 'PND' in level_course_category:
                        level_category = 'PND'
                except Exception as e:
                    messages.error(request, f'An error occurred during processing: {str(e)}')
            
                try:
                    student_indigene = indigene_type == "INDIGENE"
                except Exception as e:
                    messages.error(request, f'An error occurred during processing: {str(e)}')
                
                try:
                    # Get Current Semester and Determine name        
                    if current_semester.name == 'Second Semester':
                        second_semester = True
                except Exception as e:
                    messages.error(request, f'An error occurred during processing: {str(e)}')

                # check if student already made payment
                check_rrr = Payment.objects.filter(pay_ref=pay_reference).exists()
                if check_rrr:
                    messages.warning(request, f'Remita Retrieval Refference No: {pay_reference} already exist.')
                else:
                    check_payment = StudentFeePayment.objects.select_related('student').filter(student=student.candidate,
                                                                                            course_of_study=student_program,
                                                                                            academic_session=current_session,
                                                                                            student_level=student_level,
                                                                                            payment_type=payment_choice)
                    if check_payment:
                        messages.warning(request, f'Student Already Paid for {payment_choice} for {current_session} Academic Session.')
                    else:
                        
                        record_payment = Payment.objects.create(user=student.candidate,
                                                                amount=paid_amount,
                                                                status='Successful',
                                                                payment_source='School Fees',
                                                                academic_sess=current_session,
                                                                fee_category=level_category,
                                                                indigene_type=indigene_type,
                                                                pay_ref = pay_reference,
                                                                added_by=request.user
                                                                )
                        record_student_fees = StudentFeePayment.objects.create(student=student.candidate,
                                                                            course_of_study=student_program,
                                                                            academic_session=current_session,
                                                                            student_level=level_instance,
                                                                            payment_type=payment_choice,
                                                                            is_indigene=student_indigene,
                                                                            has_paid_first_semester=first_semester,
                                                                            has_paid_second_semester=second_semester,
                                                                            has_paid_full_session=full_payment)
                        context = {
                            'record_student_fees': record_student_fees,
                            'record_payment': record_payment,
                            'student': student,
                            'page_title': 'School Fee Receipt',
                            'check_payment': check_payment,
                        }
                        
                        messages.success(request, f'N{paid_amount} paid as {payment_choice} for {current_session} Academic Session, {current_level} ')
                        return render(request, 'payments/school_fees_receipt.html', context)
            else:
                form = SchoolFeePaymentForm()

        # Prepare context for the template
        context = {
            'current_session_balance': current_session_balance,
            'total_paid_current_session': total_paid_current_session,
            'total_expected_payment': total_expected_payment,
            'form': form,
            'page_title': 'Student Fee',
            'student': student,
            'indigence': indigence,
            'current_session': current_session,
            'current_semester': current_semester,
            'full_session_fee_amount': full_session_fee_amount,
            'current_semester_fee_amount': current_semester_fee_amount,
            'no_outstanding_fee': outstanding_amount == 0
        }

        return render(request, 'payments/student_fee_information.html', context)
    else:

        total_fee_for_first_semester = get_school_fee_amount(current_level, 'First Semester', current_session, indigene_type, course_of_study=student_course)
        total_fee_for_second_semester = get_school_fee_amount(current_level, 'Second Semester', current_session, indigene_type, course_of_study=student_course)

        # Calculate total expected payment
        total_expected_payment = total_fee_for_first_semester + total_fee_for_second_semester

        current_session_balance = total_expected_payment - total_paid_current_session
        
        # Check if Current Session Fee amount is NOT
        current_semester_fee_amount = fee_current_semester.amount if fee_current_semester else Decimal('0')
        
        # Determine if the student has a previous level
        previous_level = student.get_previous_level()
        
        
        # Determine previous academic session from the Student Current Session
        previous_session = current_session.get_previous_academic_session()

        # Check for Previous Session
        if previous_session:
            # Calculate total paid in the previous session
            total_paid_previous = Payment.objects.filter(user=student.candidate,
                                                         academic_sess=previous_session,
                                                         payment_source='School Fees',
                                                         indigene_type=indigene_type,).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
  
            # Calculate total expected payment for the previous session
            total_fee_previous = Fee.objects.filter(level=previous_level, academic_session=previous_session, semester='Full Session', indigene_type=indigene_type, course_of_study=student.course
                ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')
                
            # Calculate outstanding amount for the previous session
            outstanding_amount = max(total_fee_previous - total_paid_previous, Decimal('0'))
                
            if outstanding_amount:
                messages.warning(request, f'Outstanding payment of N{outstanding_amount} exists from the previous session. Please clear it first.')  
                return redirect('student_previous_session_fee', student_id=student.id)     
            else:
                # Find the corresponding Current Student Level instance
                try:
                    level_instance = Level.objects.get(name=current_level)
                    student_current_level_id = level_instance.id
                except Level.DoesNotExist:
                    messages.error(request, 'Level Not Found')
                    return redirect('accounts_dashboard')
                
                # Get the instance of the level from the LEVEL name
                try:
                    level_id = Level.objects.get(id=student_current_level_id)
                except Level.DoesNotExist:
                    messages.error(request, 'Level Not Found')
                    return redirect('accounts_dashboard')
                    
                if request.method == 'POST' and form.is_valid():
                    payment_choice = form.cleaned_data['payment_type']
                    pay_reference = form.cleaned_data['pay_reference']
                    student_level = student_current_level_id
                    student_program = student_course
                    current_session = current_session
                    
                    
                    
                    # Get the Student Level Category from the Programme Name  
                    try:
                        level_course_category = student_program.name
                        if 'HND' in level_course_category:
                            level_category = 'HND'
                        elif 'ND' in level_course_category:
                            level_category = 'ND'
                           
                    except Exception as e:
                        messages.error(request, f'An error occurred during processing: {str(e)}')
                    
                    # Get Student Indigence and assign True or False accordingly
                    try:
                        student_indigene = indigene_type == "INDIGENE"
                    except Exception as e:
                        messages.error(request, f'An error occurred during Indigence processing: {str(e)}')
                    
                    # Get Current Semester and Determine name   
                    try:        
                        if current_semester.name == 'Second Semester':
                            second_semester = True
                    except Exception as e:
                        messages.error(request, f'An error occurred during Second Semester processing: {str(e)}')

                    # check if Pay Refference already exist in the Payment Model
                    check_rrr = Payment.objects.filter(pay_ref=pay_reference).exists()
                    if check_rrr:
                        messages.warning(request, f'Remita Retrieval Refference No: {pay_reference} already exist.')
                    
                    else: 
                        check_payment = StudentFeePayment.objects.select_related('student').filter(student=student.candidate,
                                                                                                    course_of_study=student_program,
                                                                                                    academic_session=current_session,
                                                                                                    student_level=level_instance
                                                                                                   ).first()
                        
                        if check_payment:
                            
                            if payment_choice == 'Semester Fee Payment':
                                # Check if Student has NOT Made First Semester Payment
                                if not check_payment.has_paid_first_semester:
                                    # Process First Semester Fee Payment
                                    try:
                                        with transaction.atomic():
                                            record_payment = Payment.objects.create(
                                                user=student.candidate,
                                                amount=total_fee_for_first_semester,
                                                status='Successful',
                                                payment_source='School Fees',
                                                academic_sess=current_session,
                                                fee_category=level_category,
                                                indigene_type=indigene_type,
                                                pay_ref=pay_reference,
                                                added_by=request.user
                                            )

                                            record_student_fees = StudentFeePayment.objects.create(
                                                student=student.candidate,
                                                course_of_study=student_program,
                                                academic_session=current_session,
                                                student_level=level_instance,
                                                payment_type=payment_choice,
                                                is_indigene=student_indigene,
                                                has_paid_first_semester=True
                                            )

                                            messages.success(request, f'{current_semester}  School Fee for {current_session} Academic Session Made Successfully.')
                                            
                                            context = {

                                                'student': student,  
                                                'record_payment': record_payment,
                                                'page_title': f'{current_semester} Fee Bal. Receipt',
                                                'record_student_fees': record_student_fees,
                                                'remark': f'{current_semester} School Fees Payment for {current_session} ',

                                            }
                                            return render(request, 'payments/school_receipt.html', context)
                                    except Exception as e:
                                        # Handle exceptions if any of the queries fail
                                        
                                        messages.error(request, f'An error occurred during the transaction.{e}')
                                
                                # Check if Student has NOT Made Second Semester Payment   
                                elif not check_payment.has_paid_second_semester:
                                    try:
                                        with transaction.atomic():
                                            record_payment = Payment.objects.create(
                                                user=student.candidate,
                                                amount=current_session_balance,
                                                status='Successful',
                                                payment_source='School Fees',
                                                academic_sess=current_session,
                                                fee_category=level_category,
                                                indigene_type=indigene_type,
                                                pay_ref=pay_reference,
                                                added_by=request.user
                                            )

                                            record_student_fees = StudentFeePayment.objects.create(
                                                student=student.candidate,
                                                course_of_study=student_program,
                                                academic_session=current_session,
                                                student_level=level_instance,
                                                payment_type=payment_choice,
                                                is_indigene=student_indigene,
                                                has_paid_second_semester=True
                                            )

                                            messages.success(request, f'{current_semester}  School Fee for {current_session} Academic Session Made Successfully.')
                                            
                                            context = {

                                                'student': student,  
                                                'record_payment': record_payment,
                                                'page_title': f'{current_semester} Fee Bal. Receipt',
                                                'record_student_fees': record_student_fees,
                                                'remark': f'School Fees Balance for {current_session}',
                                            }
                                            return render(request, 'payments/school_receipt.html', context)
                                    except Exception as e:
                                        # Handle exceptions if any of the queries fail
                                        
                                        messages.error(request, f'An error occurred during the transaction. {e}')
                                        
                                else:
                                    print("Payment has already been made for the selected semester/session.")
                            # FULL FEE PAYMENT SELECTION OPTION
                            elif payment_choice == 'Full Fee Payment':
                                # Check if Student has NOT made FULL Payment Before
                                if not check_payment.has_paid_full_session:
                                    # Check if Student has Made Part Payment for current session
                                    if check_payment.has_paid_first_semester:
                                        messages.warning(request, 'Student Already Made Part Payment and Needs to Pay Balance.')
                                        
                                    elif check_payment.has_paid_second_semester:
                                        messages.warning(request, 'Student Has Completed School Fee')
                                    # PROCESS FULL PAYMENT SCHOOL FEES  
                                    else:
                                        try:
                                            with transaction.atomic():
                                                record_payment = Payment.objects.create(
                                                    user=student.candidate,
                                                    amount=total_expected_payment,
                                                    status='Successful',
                                                    payment_source='School Fees',
                                                    academic_sess=current_session,
                                                    fee_category=level_category,
                                                    indigene_type=indigene_type,
                                                    pay_ref=pay_reference,
                                                    added_by=request.user
                                                )

                                                record_student_fees = StudentFeePayment.objects.create(
                                                    student=student.candidate,
                                                    course_of_study=student_program,
                                                    academic_session=current_session,
                                                    student_level=level_instance,
                                                    payment_type=payment_choice,
                                                    is_indigene=student_indigene,
                                                    has_paid_full_session=True
                                                )

                                                messages.success(request, f'{current_session} Academic Session Full School Fee Made Successfully.')
                                                
                                                context = {

                                                    'student': student,  
                                                    'record_payment': record_payment,
                                                    'page_title': f'{current_semester} Fee Bal. Receipt',
                                                    'record_student_fees': record_student_fees,
                                                    'remark': f'Complete Payment for {current_session} Academic Session',
                                                }
                                                return render(request, 'payments/school_receipt.html', context)
                                        except Exception as e:
                                            # Handle exceptions if any of the queries fail
                                            
                                            messages.error(request, f'An error occurred during the transaction. {e}')
                                else:
                                    print("Something Went Wrong")                        
                else:
                    form = SchoolFeePaymentForm()

                    # Prepare context for the template
                context = {
                    'current_session_balance': current_session_balance,
                    'total_paid_current_session': total_paid_current_session,
                    'total_expected_payment': total_expected_payment,
                    'form': form,
                    'page_title': 'Student Fee',
                    'student': student,
                    'indigence': indigence,
                    'current_session': current_session,
                    'current_semester': current_semester,
                    'full_session_fee_amount': full_session_fee_amount,
                    'current_semester_fee_amount': current_semester_fee_amount,
                    'no_outstanding_fee': outstanding_amount == 0
                }

                return render(request, 'payments/student_fee_information.html', context)
        else:
            # NDI, and HNDI Students payment logic
            messages.warning(request, 'No Previous Session')
        # Prepare context for the template
        context = {
            'current_session_balance': current_session_balance,
            'total_paid_current_session': total_paid_current_session,
            'total_expected_payment': total_expected_payment,
            'form': form,
            'page_title': 'Student Fee',
            'student': student,
            'indigence': indigence,
            'current_session': current_session,
            'current_semester': current_semester,
            'full_session_fee_amount': full_session_fee_amount,
            'current_semester_fee_amount': current_semester_fee_amount,
            'no_outstanding_fee': outstanding_amount == 0
        }

        return render(request, 'payments/student_fee_information.html', context)


# Studend Fee Payment Schedule
@login_required(login_url='accounts_login')
def student_previous_fee_information(request, student_id):
    
    student = get_object_or_404(Student, id=student_id)

    # Initialize variables
    first_semester = False
    second_semester = False
    full_payment = False
    
    # Get Student profile
    profile = student.candidate.profile
    # Retrieve current academic info
    current_level = student.current_level
    current_session = student.academic_session
    current_semester = student.semester
    student_course = student.course
    # Use the indigene_status method
    indigence = profile.indigene_status()
    indigene_type = normalize_indigene_type(indigence)

    # Initialize outstanding amount
    outstanding_amount = Decimal('0')

    # Determine if the student has a previous level
    previous_level = student.get_previous_level()

    
    # Determine previous academic session from the Student Current Session
    previous_session = current_session.get_previous_academic_session()
    print(previous_session)
    
           
    # Calculate total paid in the previous session
    total_paid_previous = Payment.objects.filter(
        user=student.candidate, academic_sess=previous_session, payment_source='School Fees', indigene_type=indigene_type,
        ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')

            
    # Calculate total expected payment for the previous session
    total_fee_previous = Fee.objects.filter(
            level=previous_level, academic_session=previous_session, semester='Full Session', indigene_type=indigene_type, course_of_study=student.course
            ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')

           
    # Calculate outstanding amount for the previous session
    outstanding_amount = max(total_fee_previous - total_paid_previous, Decimal('0'))

    context = {
        'page_title': f'{previous_level} {previous_session} Sess. Fee Bal.',
        'outstanding_amount': outstanding_amount,
        'total_fee_previous': total_fee_previous,
        'previous_session': previous_session,
        'previous_level': previous_level,
        'total_paid_previous': total_paid_previous,
        'student': student,
        'indigence': indigence,
        'current_level': current_level,

    }
    return render(request, 'payments/student_previous_session_fee.html', context)

# Retrieve Student Acceptance Fee
@login_required(login_url='accounts_login')
def candidate_acceptance_receipt(request):
    logged_user = request.user
    if logged_user.profile.role not in {STUDENT_ROLE, GRADUATE_ROLE, 'Candidate'}:
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    application_session = get_application_academic_session(logged_user)
    student_candidate = get_verified_payment_for_session(logged_user, 'Acceptance Fee', application_session)
    if not student_candidate:
        messages.warning(request, f'No Acceptance Fee Payment Found for {application_session}')
        return redirect('accounts_dashboard')

    payments = get_verified_payments_for_session(
        logged_user,
        application_session,
        ['Acceptance Fee', 'Screening Fee', 'Application Form', 'School Fees'],
    )
    context = {
        'payments': payments,
        'student_candidate': student_candidate,
        'page_title': 'Acceptance Receipt',
        'has_screening_fee_payment': payments.filter(payment_source='Screening Fee').exists(),
        'has_acceptance_fee_payment': payments.filter(payment_source='Acceptance Fee').exists(),
        'has_application_fee_payment': payments.filter(payment_source='Application Form').exists(),
        'has_school_fee_payment': payments.filter(payment_source='School Fees').exists(),
    }
    return render(request, 'payments/student_acceptance_receipt.html', context)
    
# Retrieve Student Caution Fee
@login_required(login_url='accounts_login')
def candidate_caution_fee_receipt(request):
    logged_user = request.user
    if logged_user.profile.role not in {STUDENT_ROLE, GRADUATE_ROLE, 'Candidate'}:
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    application_session = get_application_academic_session(logged_user)
    student_candidate = get_verified_payment_for_session(logged_user, 'Caution Fee', application_session)
    if not student_candidate:
        messages.warning(request, f'No Caution Fee Payment Found for {application_session}')
        return redirect('accounts_dashboard')

    payments = get_verified_payments_for_session(
        logged_user,
        application_session,
        ['Acceptance Fee', 'Caution Fee', 'Screening Fee', 'Application Form', 'School Fees'],
    )
    context = {
        'has_caution_fee_payment': payments.filter(payment_source='Caution Fee').exists(),
        'payments': payments,
        'student_candidate': student_candidate,
        'page_title': 'Caution Receipt',
        'has_screening_fee_payment': payments.filter(payment_source='Screening Fee').exists(),
        'has_acceptance_fee_payment': payments.filter(payment_source='Acceptance Fee').exists(),
        'has_application_fee_payment': payments.filter(payment_source='Application Form').exists(),
        'has_school_fee_payment': payments.filter(payment_source='School Fees').exists(),
    }
    return render(request, 'payments/student_acceptance_receipt.html', context)


@login_required(login_url='accounts_login')
def candidate_other_charges_fee_receipt(request):
    logged_user = request.user
    if logged_user.profile.role not in {STUDENT_ROLE, GRADUATE_ROLE, 'Candidate'}:
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    application_session = get_application_academic_session(logged_user)
    student_candidate = get_verified_payment_for_session(logged_user, 'Other Charges', application_session)
    if not student_candidate:
        messages.warning(request, f'No Other Charges Fee Payment Found for {application_session}')
        return redirect('accounts_dashboard')

    payments = get_verified_payments_for_session(
        logged_user,
        application_session,
        ['Acceptance Fee', 'Other Charges', 'Caution Fee', 'Screening Fee', 'Application Form', 'School Fees'],
    )
    context = {
        'has_caution_fee_payment': payments.filter(payment_source='Caution Fee').exists(),
        'payments': payments,
        'student_candidate': student_candidate,
        'page_title': 'Non-Refundable Fee Receipt',
        'has_other_charge_fee_payment': payments.filter(payment_source='Other Charges').exists(),
        'has_screening_fee_payment': payments.filter(payment_source='Screening Fee').exists(),
        'has_acceptance_fee_payment': payments.filter(payment_source='Acceptance Fee').exists(),
        'has_application_fee_payment': payments.filter(payment_source='Application Form').exists(),
        'has_school_fee_payment': payments.filter(payment_source='School Fees').exists(),
    }
    return render(request, 'payments/other_charges_receipt.html', context)


# Retrieve Student Application Fee Receipt
@login_required(login_url='accounts_login')
def candidate_application_receipt(request):
    logged_user = request.user
    if logged_user.profile.role not in {STUDENT_ROLE, GRADUATE_ROLE, 'Applicant', 'Candidate'}:
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    application_session = get_application_academic_session(logged_user)
    student_candidate = get_verified_payment_for_session(logged_user, 'Application Form', application_session)
    if not student_candidate:
        messages.warning(request, f'No Application Form Payment Found for {application_session}')
        return redirect('accounts_dashboard')

    # Fetch all relevant payments in a single query
    payments = get_verified_payments_for_session(
        logged_user,
        application_session,
        ['Acceptance Fee', 'Screening Fee', 'Application Form', 'School Fees'],
    )
    # Check for the existence of specific fee payments
    has_screening_fee_payment = payments.filter(payment_source='Screening Fee').exists()
    has_acceptance_fee_payment = payments.filter(payment_source='Acceptance Fee').exists()
    has_application_fee_payment = payments.filter(payment_source='Application Form').exists()
    has_school_fee_payment = payments.filter(payment_source='School Fees').exists()
    is_payment_approved = student_candidate.verified and student_candidate.status == 'Approved'
    transaction_reference = student_candidate.pay_ref or student_candidate.transaction_id
    
    context = {
        'payment': student_candidate,
        'payments': payments,
        'student_candidate': student_candidate,
        'transaction_reference': transaction_reference,
        'payment_status': student_candidate.status,
        'next_application_route': 'edit_application_form',
        'show_application_cta': is_payment_approved and logged_user.profile.role != GRADUATE_ROLE,
        'page_title': 'Application Form Receipt',
        'has_screening_fee_payment': has_screening_fee_payment,
        'has_acceptance_fee_payment': has_acceptance_fee_payment,
        'has_application_fee_payment': has_application_fee_payment,
        'has_school_fee_payment': has_school_fee_payment,
    }
    return render(request, 'payments/student_acceptance_receipt.html', context)
    

# Retrieve Student Application Fee Receipt
@login_required(login_url='accounts_login')
def candidate_screening_receipt(request):
    logged_user = request.user
    if logged_user.profile.role not in {STUDENT_ROLE, GRADUATE_ROLE, 'Applicant', 'Candidate'}:
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    application_session = get_application_academic_session(logged_user)
    student_candidate = get_verified_payment_for_session(logged_user, 'Screening Fee', application_session)
    if not student_candidate:
        messages.warning(request, f'No Screening Fee Payment Found for {application_session}')
        return redirect('accounts_dashboard')

    payments = get_verified_payments_for_session(
        logged_user,
        application_session,
        ['Acceptance Fee', 'Screening Fee', 'Application Form', 'School Fees'],
    )
    context = {
        'payments': payments,
        'student_candidate': student_candidate,
        'page_title': 'Screening Fee Receipt',
        'has_screening_fee_payment': payments.filter(payment_source='Screening Fee').exists(),
        'has_acceptance_fee_payment': payments.filter(payment_source='Acceptance Fee').exists(),
        'has_application_fee_payment': payments.filter(payment_source='Application Form').exists(),
        'has_school_fee_payment': payments.filter(payment_source='School Fees').exists(),
    }
    return render(request, 'payments/student_acceptance_receipt.html', context)
    
# Retrieve Student Application Fee Receipt
@login_required(login_url='accounts_login')
def late_registration_receipt(request):
    logged_user = request.user
    if logged_user.profile.role not in {STUDENT_ROLE, GRADUATE_ROLE}:
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    try:
        indigene_type = get_user_indigene_type(logged_user)
        student_candidate = Payment.objects.select_related('user').filter(user=logged_user, verified=True, payment_source='Late Registration', indigene_type=indigene_type).order_by('-payment_date', '-id').first()
        if not student_candidate:
            raise Payment.DoesNotExist
        # Fetch all relevant payments in a single query
        payments = Payment.objects.filter(
            user=logged_user,
            verified=True,
            indigene_type=indigene_type,
            payment_source__in=['Acceptance Fee', 'Screening Fee', 'Application Form', 'School Fee', 'Late Registration']
        )
        # Check for the existence of specific fee payments
        has_screening_fee_payment = payments.filter(payment_source='Screening Fee').exists()
        has_acceptance_fee_payment = payments.filter(payment_source='Acceptance Fee').exists()
        has_application_fee_payment = payments.filter(payment_source='Application Form').exists()
        has_school_fee_payment = payments.filter(payment_source='School Fee').exists()
        has_late_registration_payment = payments.filter(payment_source='Late Registration').exists()
        
        context = {
            'has_late_registration_payment': has_late_registration_payment,
            'payments': payments,
            'student_candidate': student_candidate,
            'page_title': 'Late Registration Receipt',
            'has_screening_fee_payment': has_screening_fee_payment,
            'has_acceptance_fee_payment': has_acceptance_fee_payment,
            'has_application_fee_payment': has_application_fee_payment,
            'has_school_fee_payment': has_school_fee_payment,
        }
        return render(request, 'payments/student_acceptance_receipt.html', context)
    except Payment.DoesNotExist:
        messages.warning(request, 'No Late Registration Fee Payment Found')
        return redirect('accounts_dashboard') 


# Retrieve Student School Fee Receipt
@login_required(login_url='accounts_login')
def student_school_fee_receipts(request):
    logged_user = request.user
    if logged_user.profile.role not in {STUDENT_ROLE, GRADUATE_ROLE}:
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    # Get Student information
    try:
        student = Student.objects.select_related('candidate').get(candidate=logged_user)
    except Student.DoesNotExist:
        messages.warning(request, 'Student Infomation not Found')
        return redirect('student_dashboard')

    current_session, session_message = resolve_student_academic_session(student, logged_user)
    if not current_session:
        messages.error(request, session_message or 'No academic session is linked to your student record.')
        return redirect('student_dashboard')
    active_semester, _semester_name = resolve_student_semester(student, current_session, require_active=False)
    
    indigene_type = get_user_indigene_type(logged_user)
    try:
        payment_session_filter = (
            {} if logged_user.profile.role == GRADUATE_ROLE
            else {'academic_sess': current_session}
        )
        payments = Payment.objects.filter(
            user=logged_user,
            verified=True,
            status='Approved',
            indigene_type=indigene_type,
            payment_source__in=['Acceptance Fee', 'Late Registration', 'Other Charges', 'Screening Fee', 'Application Form', 'School Fees'],
            **payment_session_filter,
        )
        # Fetch School Fees Payment that matches the Student Fee Payment records
        student_fee_payments = StudentFeePayment.objects.filter(
            student=logged_user,
            is_indigene=(indigene_type == "INDIGENE"),
            **(
                {} if logged_user.profile.role == GRADUATE_ROLE
                else {'academic_session': current_session}
            ),
        )
        school_payments = Payment.objects.filter(
            Q(user=logged_user) | Q(user__in=student_fee_payments.values('student')),
            indigene_type=indigene_type,
            payment_source='School Fees', status='Approved', verified=True,
            **payment_session_filter,
        )
        # Check for the existence of specific fee payments
        has_screening_fee_payment = payments.filter(payment_source='Screening Fee').exists()
        has_acceptance_fee_payment = payments.filter(payment_source='Acceptance Fee').exists()
        has_application_fee_payment = payments.filter(payment_source='Application Form').exists()
        has_other_charges_payment = payments.filter(payment_source='Other Charges').exists()
        has_school_fee_payment = payments.filter(payment_source='School Fees').exists()
        has_late_registration_payment = payments.filter(payment_source='Late Registraton').exists()
        
        context = {
            'has_late_registration_payment': has_late_registration_payment,
            'active_semester': active_semester,
            'current_session': current_session,
            'has_other_charges_payment': has_other_charges_payment,
            'school_payments': school_payments,
            'student': student,
            'payments': payments,
            'page_title': 'School Fee Receipts',
            'has_screening_fee_payment': has_screening_fee_payment,
            'has_acceptance_fee_payment': has_acceptance_fee_payment,
            'has_application_fee_payment': has_application_fee_payment,
            'has_school_fee_payment': has_school_fee_payment,
        }
        return render(request, 'payments/school_fee_receipts.html', context) 
    except Payment.DoesNotExist:
        messages.warning(request, 'No School Fees Payment Found')
        return redirect('accounts_dashboard')


@login_required(login_url='accounts_login')
def print_sch_fee_receipt(request, payment_id, program_id):
    logged_user = request.user
    if logged_user.profile.role not in {STUDENT_ROLE, GRADUATE_ROLE}:
        messages.warning(request, 'You are not a Student')
        return redirect('accounts_login')
    
    # Confirm Payment record
    payment = get_object_or_404(
        Payment.objects.select_related('academic_sess', 'user'),
        id=payment_id,
        user=logged_user,
    )
    if normalize_payment_source(payment.payment_source) != 'School Fees':
        messages.warning(request, 'This receipt is only available for School Fees payments.')
        return redirect('student_school_fees_receipts')
    if not is_successful_payment(payment):
        messages.warning(request, 'Receipt can only be printed for a verified or approved School Fees payment.')
        return redirect('student_school_fees_receipts')

    student = (
        Student.objects.select_related('course', 'academic_session')
        .filter(candidate=logged_user)
        .first()
    )
    receipt_session = payment.academic_sess
    
    student_fee_payment = (
        StudentFeePayment.objects.select_related('course_of_study', 'student_level', 'academic_session')
        .filter(
            student=payment.user,
            academic_session=receipt_session,
            is_indigene=(payment.indigene_type == "INDIGENE"),
        )
        .order_by('-paid_date', '-id')
        .first()
    )
    tuition_payment_scope = resolve_school_fee_receipt_scope(payment, student)

    # Fetch all relevant payments in a single query
    payments = Payment.objects.filter(
            user=logged_user,
            verified=True,
            status='Approved',
            academic_sess=receipt_session,
            indigene_type=payment.indigene_type,
            payment_source__in=['Acceptance Fee', 'Other Charges', 'Caution Fee', 'Screening Fee', 'Application Form', 'School Fees']
        )
    # Check for the existence of specific fee payments
    has_screening_fee_payment = payments.filter(payment_source='Screening Fee').exists()
    has_acceptance_fee_payment = payments.filter(payment_source='Acceptance Fee').exists()
    has_application_fee_payment = payments.filter(payment_source='Application Form').exists()
    has_caution_fee_payment = payments.filter(payment_source='Caution Fee').exists()
    has_other_charges_payment = payments.filter(payment_source='Other Charges').exists()
    has_school_fee_payment = payments.filter(payment_source='School Fees').exists()
    
    context = {
        'has_other_charges_payment': has_other_charges_payment,
        'has_caution_fee_payment': has_caution_fee_payment,
        'payments': payments,
        'has_screening_fee_payment': has_screening_fee_payment,
        'has_acceptance_fee_payment': has_acceptance_fee_payment,
        'has_application_fee_payment': has_application_fee_payment,
        'has_school_fee_payment': has_school_fee_payment,
        'page_title': 'School Fees Receipt',
        'payment': payment,
        'student': student,
        'student_fee_payment': student_fee_payment,
        'tuition_payment_scope': tuition_payment_scope or 'School Fees',
    }

    return render(request, 'payments/student_school_fees_receipt.html', context)


@login_required(login_url='accounts_login')
def initiate_payment(request):
    logged_user = request.user
    requested_intent_slug = (
        request.POST.get('payment_source_intent') or request.GET.get('fee') or ''
    ).strip().lower()
    late_registration_requested = requested_intent_slug == 'late-registration'
    if not (logged_user.profile.role == 'Applicant' or logged_user.profile.role == 'Candidate' or logged_user.profile.role == 'Student'):
        messages.warning(request, 'Something went wrong')
        return redirect('accounts_login')

    try:
        applicant_validation = ApplicantValidation.objects.get(applicant=logged_user)
    except ApplicantValidation.DoesNotExist:
        messages.warning(request, 'Applicant Not Validated Email')
        return redirect('accounts_login')
    
    applicant_email = applicant_validation.email

    try:
        pre_applicant = PreApplicant.objects.select_related('applicant', 'academic_session', 'course_name').get(applicant=logged_user)
    except PreApplicant.DoesNotExist:
        messages.warning(request, 'Pre-Application Error Not Found')
        return redirect('accounts_login')
    
    student = Student.objects.select_related('academic_session', 'course').filter(
        candidate=logged_user, graduated=False,
    ).first()
    student_status = student is not None
    fee_course = student.course if student_status else pre_applicant.course_name
    course_name = fee_course.name.upper()
    # Application Session
    application_session = get_student_application_session(logged_user) if student_status else pre_applicant.academic_session
    application_session_name = application_session
    if not application_session:
        messages.error(request, 'Application Session NOT Found. Please contact admin to update your pre-application session.')
        return redirect('accounts_login')

    indigene_type = get_user_indigene_type(logged_user)
    try:
        base_fee_category = get_application_fee_category(
            fee_course, application_session, fee_name='Application Form', indigene_type=indigene_type,
        )
        if not base_fee_category:
            raise FeeConfigurationError('Application Form fee category is not configured.')
    except FeeConfigurationError:
        messages.error(request, 'The selected programme does not have a configured application fee category for this academic session. Please contact the Admissions Office.')
        return redirect('accounts_dashboard')

    payments = Payment.objects.filter( 
        user=logged_user,
        verified=True,
        status='Approved',
        payment_source__in=['Application Form', 'Other Charges', 'Acceptance Fee', 'Caution Fee', 'Screening Fee', 'School Fees'],
        academic_sess=application_session,
        indigene_type=indigene_type,
    )
    candidate_fee_sequence = STUDENT_CANDIDATE_FEE_SEQUENCE if student_status else CANDIDATE_FEE_SEQUENCE
    candidate_payments = Payment.objects.filter(
        user=logged_user,
        verified=True,
        status='Approved',
        payment_source__in=candidate_fee_sequence,
        academic_sess=application_session,
        indigene_type=indigene_type,
    )
    
    payment_flags = {
        'Application Form': bool(has_verified_application_fee(logged_user, application_session)),
        'Acceptance Fee': candidate_payments.filter(payment_source='Acceptance Fee').exists(),
        'Caution Fee': candidate_payments.filter(payment_source='Caution Fee').exists(),
        'Other Charges': candidate_payments.filter(payment_source='Other Charges').exists(),
    }
    if not student_status:
        payment_flags['Screening Fee'] = candidate_payments.filter(payment_source='Screening Fee').exists()

    has_application_form_payment = payment_flags['Application Form']
    has_acceptance_fee_payment = payment_flags['Acceptance Fee']
    has_screening_fee_payment = payment_flags.get('Screening Fee', False)
    has_caution_fee_payment = payment_flags['Caution Fee']
    has_other_charges_payment = payment_flags['Other Charges']
    has_school_fees_payment = payments.filter(payment_source='School Fees').exists()
    

    today = date.today()
    active_session = application_session
    student_payment_session = application_session
    student_payment_semester = None
    student_payment_level = None
    student_payment_course = None
    late_registration_category = None

   

    if student_status:
         # Get STUDENT 
        student, resolved_session, resolved_semester, payment_context_message = get_student_payment_context(logged_user)
        if not student:
            messages.error(request, 'Student Record Not Found')
            return redirect('student_dashboard')
        if not resolved_session:
            messages.error(request, payment_context_message or 'No academic session is linked to your student record.')
            return redirect('student_dashboard')
        if not resolved_semester:
            messages.warning(request, payment_context_message)
            return redirect('student_dashboard')
        if payment_context_message:
            messages.info(request, payment_context_message)
        
        # Get Student Level Instance
        try:
            level_instance = Level.objects.get(name=student.current_level)
        except Level.DoesNotExist:
            messages.warning(request, 'Student Level Not Found')
            return redirect('accounts_dashboard')
        
        active_session = resolved_session
        student_payment_session = resolved_session
        active_semester = resolved_semester
        student_payment_semester = resolved_semester
        student_payment_level = student.current_level
        student_payment_course = student.course
        semester_name = active_semester.name
        
        # Get all relevant fee payments in a single query
        paid_fees = StudentFeePayment.objects.filter(
            student=logged_user,
            academic_session=active_session,
            student_level=level_instance,
            course_of_study=student.course
        )

        # Initialize flags
        has_paid_first = False
        has_paid_second = False

        # Loop through once to set flags
        for fee in paid_fees:
            if fee.has_paid_first_semester:
                has_paid_first = True
            if fee.has_paid_second_semester or fee.has_paid_full_session:
                has_paid_second = True

        has_paid_late_registration = payment_state(
            student,
            logged_user,
            active_session,
            'Late Registration',
            indigene_type=indigene_type,
        )['has_successful_payment']

        if (
            (late_registration_requested or semester_name == 'Second Semester')
            and not has_paid_second
            and not has_paid_late_registration
        ):
            # Determine late Registration Date is reached
            is_late = False
            message = ""
            late_registration_fee_amount = Decimal("0.00")

            if active_session and active_session.closing_date:
                if today >= active_session.closing_date:
                
                    
                    is_late = True
                    # Resolve Late Registration through the category-based ApplicationFee setup.
                    try:
                        late_registration_category = get_application_fee_category(
                            student.course,
                            active_session,
                            fee_name='Late Registration',
                            indigene_type=indigene_type,
                        )
                        if not late_registration_category:
                            raise FeeConfigurationError(
                                f'No Late Registration fee category is configured for this programme, {active_session}, {indigene_type}.'
                            )
                        late_fee_obj = get_application_fee(
                            late_registration_category,
                            'Late Registration',
                            active_session,
                            indigene_type,
                        )
                    except FeeConfigurationError as exc:
                        messages.error(request, str(exc))
                        return redirect('accounts_dashboard')
                    if late_fee_obj:
                        form = LateRegistrationFeeForm(
                            user=logged_user,
                            academic_session=active_session,
                            category=late_registration_category,
                            indigene_type=indigene_type,
                        ) 
                        payment_source = 'Late Registration'
                        
                        if request.method == 'POST':
                            form = form.__class__(
                                request.POST,
                                user=logged_user,
                                academic_session=active_session,
                                category=late_registration_category,
                                indigene_type=indigene_type,
                            )
                            if form.is_valid():
                                payment, created = get_or_create_pending_payment(
                                    form=form,
                                    user=logged_user,
                                    academic_session=active_session,
                                    email=applicant_email,
                                    fee_category=late_registration_category,
                                    payment_source=payment_source,
                                    added_by=logged_user,
                                    indigene_type=indigene_type,
                                )

                                context = { 
                                    'payment_source': payment_source,
                                    'paystack_public_key': settings.PAYSTACT_PUBLIC_KEY,
                                    'applicant_email': applicant_email,
                                    'payment': payment,
                                    'course_name': course_name,
                                    'fee_amount': form.fields['amount'].initial,
                                    'page_title': 'Make Payment',
                                }
                                UserActivityLog.objects.create(user=logged_user, action=f'Successfully initiated Payment of N{form.fields["amount"].initial} as {payment_source} for {course_name} programme, {active_session} Session.')
                                messages.success(request, 'Payment Successfully Initiated')
                                return render(request, 'payments/make_payment.html', context)

                        context = {
                            'has_other_charges_payment': has_other_charges_payment,
                            'payment_source': payment_source,
                            'payment_intent_slug': 'late-registration',
                            'fee_amount': form.fields['amount'].initial,
                            'course_name': course_name,
                            'form': form,
                            'application_session_name': application_session_name,
                            'current_session': active_session,
                            'page_title': 'Initiate Online Payment',
                        }
                        return render(request, 'payments/initiate_payment.html', context)

        # An explicit Late Registration request is terminal: it must either use
        # the branch above or return to the dashboard, never resolve tuition.
        if late_registration_requested:
            if has_paid_late_registration:
                return redirect('select_fee_payment')
            messages.warning(request, 'Late Registration Fee is not currently due or has already been paid.')
            return redirect('student_dashboard')
        
    if student_status:
        has_school_fees_payment = Payment.objects.filter(
            user=logged_user,
            verified=True,
            status='Approved',
            payment_source='School Fees',
            academic_sess=student_payment_session,
            indigene_type=indigene_type,
        ).exists()

    requested_payment_source = get_candidate_fee_intent(request)
    acceptance_fee_available = has_valid_admission_offer(
        logged_user,
        application_session,
        fee_course,
    )
    next_candidate_payment_source = get_next_unpaid_candidate_fee(
        payment_flags,
        candidate_fee_sequence,
        acceptance_fee_available=acceptance_fee_available,
    )
    if (
        not acceptance_fee_available
        and acceptance_fee_stage_reached(payment_flags, candidate_fee_sequence)
    ):
        messages.warning(request, ADMISSION_NOT_OFFERED_MESSAGE)
        return redirect('student_dashboard' if logged_user.profile.role == 'Student' else 'accounts_dashboard')

    if not validate_candidate_fee_request(request, requested_payment_source, next_candidate_payment_source):
        return redirect('accounts_profile')

    payment_source = next_candidate_payment_source
    form = None
    fee_category = base_fee_category
    expected_amount = None

    if payment_source:
        form_class = CANDIDATE_FEE_FORMS[payment_source]
        try:
            fee_category = get_application_fee_category(
                fee_course, application_session, fee_name=payment_source, indigene_type=indigene_type,
            )
            if not fee_category:
                raise FeeConfigurationError(f'{payment_source} fee category is not configured.')
            expected_amount = expected_candidate_fee_amount(
                fee_category,
                payment_source,
                application_session,
                indigene_type,
            )
        except FeeConfigurationError as exc:
            messages.error(request, str(exc))
            return redirect('student_dashboard' if logged_user.profile.role == 'Student' else 'accounts_dashboard')
        form = form_class(
            user=logged_user, academic_session=application_session, course_of_study=fee_course,
        )
    elif not has_school_fees_payment:
        student_current_level = Level.objects.get(name=logged_user.student_profile.current_level)
        
        student_first_semester_payment = StudentFeePayment.objects.filter(student=logged_user, academic_session=student_payment_session, student_level=student_current_level, has_paid_first_semester=True)
        student_second_semester_payment = StudentFeePayment.objects.filter(student=logged_user, academic_session=student_payment_session, student_level=student_current_level, has_paid_second_semester=True)
        # student_full_session_payment = StudentFeePayment.objects.filter(student=logged_user, academic_session=current_session, student_level=student_current_level, has_paid_second_semester=True)
        
        try:
            if not student_first_semester_payment:
                form = FirstSemesterFeesPaymentForm(
                    user=logged_user,
                    academic_session=student_payment_session,
                    level_name=student_payment_level,
                    indigene_type=indigene_type,
                )
                payment_source = 'School Fees' 
            elif not student_second_semester_payment:
                form = SecondSemesterFeesPaymentForm(
                    user=logged_user,
                    academic_session=student_payment_session,
                    level_name=student_payment_level,
                    indigene_type=indigene_type,
                )
                payment_source = 'School Fees'
        except FeeConfigurationError as exc:
            messages.error(request, str(exc))
            return redirect('student_dashboard')
        # else:
        #     form = FullSchoolFeesPaymentForm(user=logged_user)
        #     payment_source = 'School Fees' 
    else:
        messages.success(request, f'You have completed School Fees Payment for {student_payment_session} Academic Session')
        return redirect('student_dashboard' if logged_user.profile.role == 'Student' else 'accounts_dashboard')

    if request.method == 'POST':
        form_kwargs = {'user': logged_user}
        if payment_source == 'School Fees':
            form_kwargs.update({
                'academic_session': student_payment_session,
                'level_name': student_payment_level,
                'indigene_type': indigene_type,
            })
        elif payment_source == 'Late Registration':
            form_kwargs.update({
                'academic_session': student_payment_session,
                'category': late_registration_category,
                'indigene_type': indigene_type,
            })
        elif payment_source in CANDIDATE_FEE_FORMS:
            form_kwargs.update({
                'academic_session': application_session, 'course_of_study': fee_course,
            })
        try:
            form = form.__class__(request.POST, **form_kwargs)
        except FeeConfigurationError as exc:
            messages.error(request, str(exc))
            return redirect('student_dashboard')
        if form.is_valid():
            if expected_amount is not None and not form_amount_matches_configured_fee(form, expected_amount):
                messages.warning(request, f'Invalid {payment_source} payment amount. Please restart the payment.')
                return redirect('accounts_profile')
            payment, created = get_or_create_pending_payment(
                form=form,
                user=logged_user,
                academic_session=student_payment_session if payment_source in {'School Fees', 'Late Registration'} else application_session,
                email=applicant_email,
                fee_category=(late_registration_category if payment_source == 'Late Registration' else student_payment_level) if payment_source in {'School Fees', 'Late Registration'} else fee_category,
                payment_source=payment_source,
                added_by=logged_user,
                indigene_type=indigene_type,
            )

            context = {
                'payment_source': payment_source,
                'paystack_public_key': settings.PAYSTACT_PUBLIC_KEY,
                'applicant_email': applicant_email,
                'payment': payment,
                'course_name': course_name,
                'fee_amount': form.fields['amount'].initial,
                'page_title': 'Make Payment',
                'application_session_name': application_session_name,
                'current_session': student_payment_session if payment_source in {'School Fees', 'Late Registration'} else application_session_name,
            }
            payment_session_label = student_payment_session if payment_source in {'School Fees', 'Late Registration'} else application_session
            UserActivityLog.objects.create(user=logged_user, action=f'Successfully initiated Payment of N{form.fields["amount"].initial} as {payment_source} for {course_name} programme, {payment_session_label} Session.')
            messages.success(request, 'Payment Successfully Initiated')
            return render(request, 'payments/make_payment.html', context)

    context = {
        'has_other_charges_payment': has_other_charges_payment,
        'payment_source': payment_source,
        'payment_intent_slug': CANDIDATE_FEE_SLUGS.get(payment_source, ''),
        'fee_amount': form.fields['amount'].initial,
        'course_name': course_name,
        'form': form,
        'page_title': 'Initiate Online Payment',
        'current_session': student_payment_session if payment_source in {'School Fees', 'Late Registration'} else application_session_name,
    }
    return render(request, 'payments/initiate_payment.html', context)


@login_required(login_url='accounts_login')
def verify_payment(request, ref):
    logged_user = request.user
    if not (logged_user.profile.role == 'Applicant' or logged_user.profile.role == 'Candidate' or logged_user.profile.role == 'Student'):
        messages.warning(request, 'Something went wrong')
        return redirect('accounts_login')

    result = verify_and_finalize_payment(ref, source="callback")
    if not result.success or not result.payment:
        logger.warning("Browser callback failed for ref=%s message=%s", ref, result.message)
        referenced_payment = Payment.objects.filter(
            pay_ref=ref,
            user=logged_user,
            payment_source='School Fees',
        ).select_related('academic_sess').first()
        has_completed_school_fees = referenced_payment and Payment.objects.filter(
            user=logged_user,
            academic_sess=referenced_payment.academic_sess,
            payment_source='School Fees',
            verified=True,
            status='Approved',
        ).exists()
        if not has_completed_school_fees:
            messages.warning(request, 'Payment Verification Failed')
        return redirect('initiate_payment')

    payment = result.payment
    if payment.user_id != logged_user.id:
        logger.warning("User %s attempted callback for payment ref=%s owned by user=%s", logged_user.pk, ref, payment.user_id)
        messages.warning(request, 'Something went wrong')
        return redirect('accounts_login')

    if result.already_verified:
        messages.info(request, 'Payment already verified')

    receipt_routes = {
        'Application Form': ('candidate_application_receipt', 'Payment Verification Successful'),
        'Screening Fee': ('candidate_screening_receipt', 'Screening Fee Payment Verification Successful'),
        'Acceptance Fee': ('candidate_acceptance_receipt', 'Acceptance Fee Payment Verification Successful'),
        'Caution Fee': ('candidate_caution_fee_receipt', 'Caution Fee Payment Verification Successful'),
        'Other Charges': ('candidate_other_charges_fee_receipt', 'Other Charges/Non-Refundable Fee Payment Verification Successful'),
        'Late Registration': ('late_registration_receipt', 'Late Registration Fee Payment Verification Successful'),
        'School Fees': ('student_school_fees_receipts', 'School Fees Payment Verification Successful'),
    }

    route = receipt_routes.get(payment.payment_source)
    if not route:
        logger.warning("Verified payment ref=%s has unknown payment_source=%s", payment.pay_ref, payment.payment_source)
        messages.warning(request, 'Payment was verified but receipt route was not found. Contact ICT')
        return redirect('student_dashboard')

    if not result.already_verified:
        UserActivityLog.objects.create(
            user=logged_user,
            action=f' {payment.amount} Payment with Reference No:{payment.pay_ref} Verified for {payment.payment_source}.',
        )
        messages.success(request, route[1])
    return redirect(route[0])


@csrf_exempt
def paystack_webhook(request):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed"}, status=405)

    signature = request.headers.get("x-paystack-signature")
    expected_signature = hmac.new(
        settings.PAYSTACK_PRIVATE_KEY.encode("utf-8"),
        request.body,
        hashlib.sha512,
    ).hexdigest()

    if not signature or not hmac.compare_digest(signature, expected_signature):
        logger.warning("Rejected Paystack webhook with invalid signature")
        return JsonResponse({"detail": "Invalid signature"}, status=400)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        logger.warning("Rejected Paystack webhook with invalid JSON payload")
        return JsonResponse({"detail": "Invalid payload"}, status=400)

    event = payload.get("event")
    if event != "charge.success":
        logger.info("Ignoring Paystack webhook event=%s", event)
        return JsonResponse({"detail": "Event ignored"}, status=200)

    reference = payload.get("data", {}).get("reference")
    if not reference:
        logger.warning("Paystack charge.success webhook missing reference")
        return JsonResponse({"detail": "Missing reference"}, status=400)

    result = verify_and_finalize_payment(reference, source="webhook")
    if not result.success:
        logger.warning("Paystack webhook failed for ref=%s message=%s", reference, result.message)
        return JsonResponse({"detail": result.message}, status=400)

    return JsonResponse({"detail": "Payment processed"}, status=200)


def _graduate_tuition_context(request, student, indigene_type):
    """Resolve only academic periods evidenced by this graduate's own records."""
    contexts = {}
    for enrollment in CourseEnrollment.objects.filter(
        student=student.candidate,
        programme=student.course,
    ).select_related('academic_session', 'level'):
        contexts[(enrollment.academic_session_id, enrollment.level.name)] = enrollment.academic_session
    admitted = getattr(student.candidate, 'admitted', None)
    if admitted and admitted.academic_session_id and admitted.admitted_level:
        contexts[(admitted.academic_session_id, admitted.admitted_level)] = admitted.academic_session
    if student.academic_session_id:
        contexts[(student.academic_session_id, student.current_level)] = student.academic_session
    if student.graduation_session_id:
        contexts[(student.graduation_session_id, student.current_level)] = student.graduation_session

    requested_key = (
        str(request.GET.get('academic_session') or ''),
        str(request.GET.get('level') or ''),
    )
    period_options = []
    selected = None
    fallback = None
    for (session_id, level_name), academic_session in sorted(
        contexts.items(),
        key=lambda item: (item[1].start_date, item[1].pk, item[0][1]),
    ):
        student_for_period = copy(student)
        student_for_period.current_level = level_name
        state = get_tuition_payment_state(
            student.candidate, student_for_period, academic_session, indigene_type
        )
        querystring = urlencode({'academic_session': session_id, 'level': level_name})
        period = {
            'academic_session': academic_session,
            'level': level_name,
            'url': f'{reverse("select_fee_payment")}?{querystring}',
            'has_outstanding': bool(state['semester_choices']),
        }
        period_options.append(period)
        candidate = (student_for_period, academic_session, state, querystring)
        fallback = fallback or candidate
        if requested_key == (str(session_id), level_name):
            selected = candidate
        elif selected is None and state['semester_choices']:
            selected = candidate

    return selected or fallback, period_options


# View for selecting fee payment
@login_required
def select_fee_payment(request): 
    logged_user = request.user

    if logged_user.profile.role not in {'Candidate', STUDENT_ROLE, GRADUATE_ROLE}:
        messages.warning(request, 'Something went wrong')
        return redirect('accounts_login')

    student = None
    graduate_fee_periods = []
    graduate_payment_querystring = ''
    if logged_user.profile.role in {STUDENT_ROLE, GRADUATE_ROLE}:
        student = Student.objects.select_related(
            'candidate', 'candidate__admitted', 'candidate__admitted__academic_session',
            'course', 'academic_session', 'graduation_session'
        ).filter(candidate=logged_user).first()
        if not student:
            messages.warning(request, 'Student Record Not Found')
            return redirect('student_dashboard')
        if logged_user.profile.role == GRADUATE_ROLE:
            resolved, graduate_fee_periods = _graduate_tuition_context(
                request, student, get_user_indigene_type(logged_user)
            )
            if not resolved:
                messages.warning(request, 'No academic fee period is linked to your student record.')
                return redirect('student_dashboard')
            student, current_session, tuition_state, graduate_payment_querystring = resolved
            context_message = ''
        else:
            current_session, context_message = resolve_student_academic_session(student, logged_user)
        if not current_session:
            messages.warning(request, context_message or 'No academic session is linked to your student record.')
            return redirect('student_dashboard')
        current_semester, _semester_name = resolve_student_semester(student, current_session, require_active=False)
        if context_message:
            messages.info(request, context_message)
        if logged_user.profile.role == STUDENT_ROLE:
            prerequisites_ready, prerequisites_message = get_student_prerequisite_fee_gate(
                logged_user,
                student,
                indigene_type=get_user_indigene_type(logged_user),
            )
            if not prerequisites_ready:
                messages.warning(request, prerequisites_message)
                return redirect('student_dashboard')
            late_registration_ready, late_registration_message = get_late_registration_fee_gate(
                logged_user,
                student,
                current_session,
                current_semester,
                get_user_indigene_type(logged_user),
            )
            if not late_registration_ready:
                messages.warning(request, late_registration_message)
                return redirect(f'{reverse("initiate_payment")}?fee=late-registration')
    else:
        current_session = get_user_academic_session(logged_user)
        if not current_session:
            messages.warning(request, 'No Active Session Found')
            return redirect('accounts_dashboard')
        
        current_semester = get_active_semester_for_session(current_session)
        if not current_semester:
            messages.warning(request, f'No active semester is configured for {current_session}.')
            return redirect('accounts_dashboard')

    indigene_type = get_user_indigene_type(logged_user)
    if student and logged_user.profile.role != GRADUATE_ROLE:
        tuition_state = get_tuition_payment_state(logged_user, student, current_session, indigene_type)
    elif not student:
        tuition_state = {
            'can_pay_first_semester': False,
            'can_pay_second_semester': False,
            'can_pay_full_session': False,
            'first_semester_fee_amount': Decimal('0'),
            'second_semester_fee_amount': Decimal('0'),
            'full_session_fee_amount': Decimal('0'),
            'tuition_payment_message': '',
            'tuition_action_label': 'Proceed with Payment',
            'semester_choices': [('First Semester', 'First Semester Tuition Fee'), ('Full Session', 'Full Session Tuition Fee')],
            'tuition_options': [],
        }
    semester_choices = tuition_state['semester_choices']

    # Initialize the form with dynamic choices
    form = FeePaymentForm()
    form.fields['semester_choice'].choices = semester_choices  

    if request.method == "POST":
        form = FeePaymentForm(request.POST)
        form.fields['semester_choice'].choices = semester_choices  

        if form.is_valid():
            selected_semester_name = form.cleaned_data.get('semester_choice')  
            if student:
                is_valid_choice, tuition_state, tuition_message = validate_tuition_payment_choice(
                    logged_user,
                    student,
                    current_session,
                    indigene_type,
                    selected_semester_name,
                )
                if not is_valid_choice:
                    messages.warning(request, tuition_message)
                    return redirect('select_fee_payment')
            
            if selected_semester_name == 'Full Session':
                selected_semester_id = 0
            else:
                try:
                    selected_semester = Semester.objects.get(
                        academic_session=current_session,
                        name=selected_semester_name,
                    )
                except Semester.DoesNotExist:
                    messages.warning(request, f'{selected_semester_name} was not found for {current_session}.')
                    return redirect('accounts_dashboard')
                except Semester.MultipleObjectsReturned:
                    messages.warning(request, 'Multiple Selected Semesters found. Please contact the administrator.')
                    return redirect('accounts_dashboard')
                selected_semester_id = selected_semester.id

            payment_url = reverse('initiate_school_fees_payment', kwargs={'semester': selected_semester_id})
            if graduate_payment_querystring:
                payment_url = f'{payment_url}?{graduate_payment_querystring}'
            return redirect(payment_url)
        else:
            messages.error(request, "Invalid selection. Please choose a valid semester option.")

    return render(request, 'payments/initiate_payment.html', {
        'form': form, 
        'page_title': 'Tuition Payment',
        'show_payment_form': bool(semester_choices),
        'graduate_fee_periods': graduate_fee_periods,
        **tuition_state,
    })

# Initiate School Fees Payment
@login_required(login_url='accounts_login')
def initiate_school_fees_payment(request, semester):
    logged_user = request.user
    if logged_user.profile.role not in {'Candidate', STUDENT_ROLE, GRADUATE_ROLE}:
        messages.warning(request, 'Something went wrong')
        return redirect('accounts_login')
    
    student = None
    if logged_user.profile.role in {STUDENT_ROLE, GRADUATE_ROLE}:
        student = Student.objects.select_related(
            'candidate', 'candidate__admitted', 'candidate__admitted__academic_session',
            'course', 'academic_session', 'graduation_session'
        ).filter(candidate=logged_user).first()
        if not student:
            messages.warning(request, 'Student Record Not Found')
            return redirect('student_dashboard')
        if logged_user.profile.role == GRADUATE_ROLE:
            resolved, _periods = _graduate_tuition_context(
                request, student, get_user_indigene_type(logged_user)
            )
            if not resolved:
                messages.warning(request, 'No academic fee period is linked to your student record.')
                return redirect('student_dashboard')
            student, current_session, _tuition_state, _querystring = resolved
            context_message = ''
        else:
            current_session, context_message = resolve_student_academic_session(student, logged_user)
        if not current_session:
            messages.warning(request, context_message or 'No academic session is linked to your student record.')
            return redirect('student_dashboard')
        current_semester, _semester_name = resolve_student_semester(student, current_session, require_active=False)
        if logged_user.profile.role == STUDENT_ROLE:
            prerequisites_ready, prerequisites_message = get_student_prerequisite_fee_gate(
                logged_user,
                student,
                get_user_indigene_type(logged_user),
            )
            if not prerequisites_ready:
                messages.warning(request, prerequisites_message)
                return redirect('student_dashboard')
            late_registration_ready, late_registration_message = get_late_registration_fee_gate(
                logged_user,
                student,
                current_session,
                current_semester,
                get_user_indigene_type(logged_user),
            )
            if not late_registration_ready:
                messages.warning(request, late_registration_message)
                return redirect(f'{reverse("initiate_payment")}?fee=late-registration')
    else:
        current_session = get_user_academic_session(logged_user)
        current_semester = get_active_semester_for_session(current_session)
        if not current_session or not current_semester:
            messages.warning(request, 'No valid academic session or semester was found for this payment.')
            return redirect('accounts_dashboard')

    if semester == 0:
        selected_semester = None
        selected_semester_name = 'Full Session'
    else:
        try:
            selected_semester = Semester.objects.get(id=semester, academic_session=current_session)
            selected_semester_name = selected_semester.name
        except Semester.DoesNotExist:
            messages.warning(request, f'Selected semester was not found for {current_session}.')
            return redirect('select_fee_payment')
        except Exception as e:
            logger.exception("Unable to resolve selected semester id=%s for user=%s", semester, logged_user.pk)
            messages.error(request, f'An error occurred: {str(e)}')
            return redirect('accounts_dashboard')
    
    applicant_validation = get_object_or_404(ApplicantValidation, applicant=logged_user)
    applicant_email = applicant_validation.email
    pre_applicant = get_object_or_404(PreApplicant, applicant=logged_user)
    course_name = pre_applicant.course_name
    
    fee_category = student.current_level if student else 'ND' if 'ND' in course_name.name else 'CERTIFICATE' if 'CERTIFICATE' in course_name.name else 'UNKNOWN'
    indigene_type = get_user_indigene_type(logged_user)
    
    student_fee = None
    if student:
        is_valid_choice, tuition_state, tuition_message = validate_tuition_payment_choice(
            logged_user,
            student,
            current_session,
            indigene_type,
            selected_semester_name,
        )
        if not is_valid_choice:
            messages.warning(request, tuition_message)
            return redirect('select_fee_payment')
        student_fee = tuition_state['student_fee']
    else:
        student_fee = StudentFeePayment.objects.filter(student=logged_user, academic_session=current_session, is_indigene=(indigene_type == "INDIGENE")).first()
    form_kwargs = {
        'user': logged_user,
        'academic_session': current_session,
        'level_name': fee_category,
        'indigene_type': indigene_type,
    }
    
    try:
        if selected_semester_name == 'First Semester':
            if student_fee and student_fee.has_paid_first_semester:
                messages.info(request, "You have already paid for First Semester.")
                return redirect('student_dashboard')
            form = FirstSemesterFeesPaymentForm(**form_kwargs)
        elif selected_semester_name == 'Second Semester':
            if student and not tuition_state.get('has_paid_first_semester_fee'):
                messages.warning(request, "First Semester Tuition must be paid and verified before Second Semester Tuition.")
                return redirect('select_fee_payment')
            if student_fee and student_fee.has_paid_second_semester:
                messages.info(request, "You have already paid for Second Semester.")
                return redirect('student_dashboard')
            form = SecondSemesterFeesPaymentForm(**form_kwargs)
        elif selected_semester_name == 'Full Session':
            if student and tuition_state.get('has_paid_first_semester_fee'):
                messages.warning(request, "Full Session Tuition cannot be paid after First Semester Tuition has already been paid.")
                return redirect('select_fee_payment')
            if student_fee and student_fee.has_paid_full_session:
                messages.info(request, "You have already paid for Full Session.")
                return redirect('student_dashboard')
            form = FullSchoolFeesPaymentForm(**form_kwargs)
        else:
            messages.error(request, "Invalid semester selection.")
            return redirect('select_fee_payment')
    except FeeConfigurationError as exc:
        messages.error(request, str(exc))
        return redirect('student_dashboard')
    
    if request.method == 'POST':
        try:
            form = form.__class__(request.POST, **form_kwargs)
        except FeeConfigurationError as exc:
            messages.error(request, str(exc))
            return redirect('student_dashboard')
        if form.is_valid():
            payment, created = get_or_create_pending_payment(
                form=form,
                user=logged_user,
                academic_session=current_session,
                email=applicant_email,
                fee_category=selected_semester_name,
                payment_source='School Fees',
                added_by=logged_user,
                indigene_type=indigene_type,
            )
            context = {
                'selected_semester_name': selected_semester_name,
                'fee_category': selected_semester_name,
                'payment_source': 'School Fees',
                'paystack_public_key': settings.PAYSTACT_PUBLIC_KEY,
                'applicant_email': applicant_email,
                'payment': payment,
                'current_session': current_session,
                'course_name': course_name,
                'fee_amount': form.fields['amount'].initial,
                'page_title': 'Make Payment',
            }
            
            UserActivityLog.objects.create(user=logged_user, action=f'Successfully initiated Payment of N{form.fields["amount"].initial} for {semester} as School Fees for {course_name} programme, {current_session} Session.')
            messages.success(request, 'Payment Successfully Initiated')
            return render(request, 'payments/make_payment.html', context)
    
    context = {
        'current_session': current_session,
        'payment_source': 'School Fees',
        'fee_amount': form.fields['amount'].initial,
        'course_name': course_name,
        'form': form,
        'page_title': 'Initiate School Fees Payment',
    }
    return render(request, 'payments/initiate_payment.html', context)


@login_required(login_url='accounts_login')
def verify_school_fees_payment(request, ref):
    logged_user = request.user
    if logged_user.profile.role not in [STUDENT_ROLE, GRADUATE_ROLE, 'Candidate']:
        messages.warning(request, 'Unauthorized access')
        return redirect('accounts_login')

    result = verify_and_finalize_payment(ref, source="school-fees-callback")
    if not result.success or not result.payment:
        logger.warning("School fees callback failed for ref=%s message=%s", ref, result.message)
        messages.warning(request, 'Payment Verification Failed')
        return redirect('initiate_payment')

    payment = result.payment
    if payment.user_id != logged_user.id:
        logger.warning("User %s attempted school-fee callback for payment ref=%s owned by user=%s", logged_user.pk, ref, payment.user_id)
        messages.warning(request, 'Unauthorized access')
        return redirect('accounts_login')

    if payment.payment_source != 'School Fees':
        messages.warning(request, 'Invalid payment type for this verification.')
        return redirect('student_dashboard')

    if result.already_verified:
        messages.info(request, 'School Fees Payment already verified')
    else:
        messages.success(request, 'School Fees Payment Verified Successfully')

    return redirect('student_school_fees_receipts')

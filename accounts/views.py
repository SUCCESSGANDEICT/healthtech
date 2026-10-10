from django.shortcuts import render, redirect,  get_object_or_404
from django.urls import reverse
from django.contrib.auth import authenticate, update_session_auth_hash
from django.contrib.auth.models import User
from . models import (
    GRADUATE_ROLE, STUDENT_ROLE, AcademicStaff, Profile, StaffPublication,
    StaffRegister, UserActivityLog,
)
from fees.models import Fee, ApplicationFee, StudentFeePayment
from fees.utils import FeeConfigurationError, get_application_fee, get_school_fee_amount, get_user_indigene_type
from . forms import (PasswordChangeForm, UpdateStaffProfileForm,
                     SuperAdminUserCreationForm, UserActivityLogSearchForm,
                     ProfileForm, SearchUserForm, EditUserForm,
                     CandidateProfileForm, SuperUserEditStaffRegisterForm,
                     SuperUserEditStaffAcademicRecordForm, PasswordResetForm
                     )
from datetime import date
from django.views.generic import ListView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.decorators import user_passes_test
from django.views import View
from admission.models import staffAppSubmission
from django.db.models import Q, Count, Sum
from decimal import Decimal
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from payments.models import Payment
from payments.status import (
    FAILED_STATUSES as PAYMENT_FAILED_STATUSES,
    FEE_ALIASES as PAYMENT_FEE_ALIASES,
    PAYMENT_SOURCE_VARIANTS,
    SUCCESS_STATUSES as PAYMENT_SUCCESS_STATUSES,
    AWAITING_VERIFICATION_STATUSES as PAYMENT_AWAITING_VERIFICATION_STATUSES,
    is_successful_payment,
    normalize_payment_source,
    payment_attempt_message,
    payment_attempt_state,
    payment_source_filter,
    payment_state,
    payments_for_fee,
    related_payment_user_ids,
    successful_payment_filter,
)
from admission.models import PreApplicant, ApplicantValidation
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from admission.models import ApplicationSubmission, Jamb
from admission.forms import get_application_fee_category
from academics.models import (AcademicSession, Student,
                              CourseOfStudy, Level, Semester,
                              Faculty, CourseEnrollment,
                              Department, CourseUnit)
from academics.services import (
    determine_graduation_session,
    student_school_fees_settled,
    synchronize_graduate_state,
)

from .helpers import (
    get_profile,
    profile_complete,
    profile_contact_complete,
    application_form_profile_complete,
    
)
from django.contrib.auth import logout
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.contrib.auth.views import LoginView
from django.utils.translation import gettext_lazy as _
from admission.helpers import (
    ADMISSION_NOT_OFFERED_MESSAGE,
    get_sponsor_details,
    sponsor_details_contact_complete,
    get_education,
    applicant_education_complete,
    get_result,
    applicant_result_complete,
    get_application_submission,
    application_submitted,
    has_verified_application_fee,
    get_applicant_academic_session,
    has_valid_admission_offer,
)
from .student_academic import (
    get_student_application_session,
    ordered_academic_sessions,
    resolve_student_academic_session,
    resolve_student_semester,
    student_level_session_offset,
)


def _get_student_application_session(user):
    return get_student_application_session(user)


def _ordered_dashboard_sessions():
    return ordered_academic_sessions()


def _student_level_session_offset(student):
    return student_level_session_offset(student)


def _resolve_student_dashboard_session(student, user):
    return resolve_student_academic_session(student, user)


def _resolve_student_dashboard_semester(student, academic_session):
    return resolve_student_semester(student, academic_session)


def _dashboard_fee_amount(level, semester, academic_session, indigene_type, name=None, course_of_study=None):
    return get_school_fee_amount(level, semester, academic_session, indigene_type, name=name, course_of_study=course_of_study)


def _dashboard_late_registration_fee(student, academic_session, indigene_type):
    category = get_application_fee_category(
        student.course,
        academic_session,
        fee_name='Late Registration',
        indigene_type=indigene_type,
    )
    if not category:
        return None
    try:
        return get_application_fee(category, 'Late Registration', academic_session, indigene_type)
    except FeeConfigurationError:
        return None


def _dashboard_school_fee_amounts(level, academic_session, indigene_type, course_of_study=None):
    first_semester = _dashboard_fee_amount(level, 'First Semester', academic_session, indigene_type, course_of_study=course_of_study)
    second_semester = _dashboard_fee_amount(level, 'Second Semester', academic_session, indigene_type, course_of_study=course_of_study)
    full_session = _dashboard_fee_amount(level, 'Full Session', academic_session, indigene_type, course_of_study=course_of_study)

    if not full_session and (first_semester or second_semester):
        full_session = first_semester + second_semester

    return full_session, first_semester, second_semester, Decimal('0')


DASHBOARD_SUCCESS_STATUSES = PAYMENT_SUCCESS_STATUSES
DASHBOARD_FAILED_STATUSES = PAYMENT_FAILED_STATUSES
DASHBOARD_AWAITING_VERIFICATION_STATUSES = PAYMENT_AWAITING_VERIFICATION_STATUSES
DASHBOARD_FEE_ALIASES = PAYMENT_FEE_ALIASES
DASHBOARD_PAYMENT_SOURCE_VARIANTS = PAYMENT_SOURCE_VARIANTS


def _dashboard_normalize_payment_source(value):
    return normalize_payment_source(value)


def _dashboard_successful_payment_filter():
    return successful_payment_filter()


def _dashboard_payment_source_filter(payment_source):
    return payment_source_filter(payment_source)


def _dashboard_related_payment_users(student, user):
    return related_payment_user_ids(student, user)


def _dashboard_payments_for_fee(student, user, academic_session, payment_source, indigene_type=None, successful_only=False):
    return payments_for_fee(student, user, academic_session, payment_source, indigene_type, successful_only)


def _dashboard_payment_state(student, user, academic_session, payment_source, indigene_type=None):
    return payment_state(student, user, academic_session, payment_source, indigene_type)


def _dashboard_is_successful_payment(payment):
    return is_successful_payment(payment)


def _dashboard_payment_attempt_state(payment):
    return payment_attempt_state(payment)


def _dashboard_payment_attempt_message(display_name, attempt_state):
    return payment_attempt_message(display_name, attempt_state)


def _dashboard_decorate_payment_history(page_obj):
    for payment in page_obj.object_list:
        payment.dashboard_payment_source = _dashboard_normalize_payment_source(payment.payment_source)
        payment.dashboard_is_successful = _dashboard_is_successful_payment(payment)
        payment.dashboard_attempt_state = _dashboard_payment_attempt_state(payment)
        payment.dashboard_status_label = {
            'paid': 'Successful',
            'initiated': 'Payment Not Completed',
            'awaiting_verification': 'Awaiting Verification',
            'failed': 'Failed',
            'not_started': 'Not Completed',
        }.get(payment.dashboard_attempt_state, payment.status or 'Unverified')
    return page_obj


def _dashboard_acceptance_fee_state(student, user, indigene_type):
    admission_session = _get_student_application_session(user)
    base_state = {
        'acceptance_fee_admission_session': admission_session,
        'acceptance_fee_amount': Decimal('0'),
        'acceptance_fee_category': '',
        'acceptance_fee_message': '',
        'has_acceptance_fee_setup': False,
        'has_acceptance_fee_payment': False,
        'has_pending_acceptance_fee_payment': False,
        'acceptance_fee_available': False,
        'acceptance_fee_payment_queryset': Payment.objects.none(),
    }

    if not admission_session:
        base_state['acceptance_fee_message'] = (
            'Acceptance Fee setup could not be checked because your admission session is missing. '
            'Please contact ICT/Admin for resolution.'
        )
        return base_state

    acceptance_payment_state = _dashboard_payment_state(
        student,
        user,
        admission_session,
        'Acceptance Fee',
        indigene_type=indigene_type,
    )
    has_verified = acceptance_payment_state['has_successful_payment']
    has_pending = acceptance_payment_state['has_pending_payment']
    has_admission_offer = has_valid_admission_offer(user, admission_session, student.course)

    if not has_admission_offer:
        message = 'Acceptance Fee paid and verified.' if has_verified else ADMISSION_NOT_OFFERED_MESSAGE
        if has_pending and not has_verified:
            message = f'{ADMISSION_NOT_OFFERED_MESSAGE} Existing Acceptance Fee attempts are locked until admission is granted.'
        base_state.update({
            'acceptance_fee_message': message,
            'has_acceptance_fee_payment': has_verified,
            'has_pending_acceptance_fee_payment': has_pending,
            'acceptance_fee_payment_queryset': acceptance_payment_state['successful_payments'],
        })
        return base_state

    fee_category = get_application_fee_category(
        student.course,
        admission_session,
        fee_name='Acceptance Fee',
        indigene_type=indigene_type,
    )
    if not fee_category:
        base_state['acceptance_fee_message'] = (
            'Acceptance Fee setup was not found for your admission session/programme. '
            'Please contact ICT/Admin for resolution.'
        )
        return base_state

    try:
        acceptance_fee = get_application_fee(
            fee_category,
            'Acceptance Fee',
            admission_session,
            indigene_type,
        )
    except FeeConfigurationError:
        base_state['acceptance_fee_message'] = (
            'Acceptance Fee setup was not found for your admission session/programme. '
            'Please contact ICT/Admin for resolution.'
        )
        return base_state

    if has_verified:
        message = 'Acceptance Fee paid and verified.'
    elif has_pending:
        message = 'Acceptance Fee payment is pending or unverified. Please complete payment verification before paying tuition.'
    else:
        message = 'Acceptance Fee is configured but has not been paid.'

    base_state.update({
        'acceptance_fee_amount': acceptance_fee.amount,
        'acceptance_fee_category': fee_category,
        'acceptance_fee_message': message,
        'has_acceptance_fee_setup': True,
        'has_acceptance_fee_payment': has_verified,
        'has_pending_acceptance_fee_payment': has_pending,
        'acceptance_fee_available': True,
        'acceptance_fee_payment_queryset': acceptance_payment_state['successful_payments'],
    })
    return base_state


STUDENT_DASHBOARD_FEE_SEQUENCE = (
    ('Application Form Fee', 'Application Form', 'application-form'),
    ('Acceptance Fee', 'Acceptance Fee', 'acceptance-fee'),
    ('Caution Fee', 'Caution Fee', 'caution-fee'),
    ('Other Charges', 'Other Charges', 'other-charges'),
)


def _next_fee_context(
    *,
    student,
    user,
    indigene_type,
    current_session,
    current_semester,
    current_semester_instance,
):
    base_context = {
        'next_fee_name': '',
        'next_fee_amount': Decimal('0'),
        'next_fee_status': '',
        'next_fee_status_class': 'secondary',
        'next_fee_payment_url': '',
        'next_fee_action_label': '',
        'next_fee_message': '',
        'can_pay_next_fee': False,
        'tuition_status': '',
        'has_paid_first_semester_fee': False,
        'has_paid_second_semester_fee': False,
        'has_paid_full_session_fee': False,
        'can_pay_second_semester': False,
        'tuition_payment_message': '',
    }

    def stage_context(name, amount, status, message, can_pay=False, url='', status_class='warning', action_label='', extra=None):
        context = base_context.copy()
        context.update({
            'next_fee_name': name,
            'next_fee_amount': amount or Decimal('0'),
            'next_fee_status': status,
            'next_fee_status_class': status_class,
            'next_fee_payment_url': url if can_pay else '',
            'next_fee_action_label': action_label or ('Pay Now' if can_pay else ''),
            'next_fee_message': message,
            'can_pay_next_fee': can_pay,
        })
        if extra:
            context.update(extra)
        return context

    admission_session = _get_student_application_session(user)
    if not admission_session:
        return stage_context(
            'Application Form Fee',
            Decimal('0'),
            'Setup missing',
            'Application Form Fee setup was not found for your programme/session. Please contact ICT/Admin.',
            status_class='danger',
        )

    for display_name, payment_source, slug in STUDENT_DASHBOARD_FEE_SEQUENCE:
        if payment_source == 'Acceptance Fee':
            acceptance_payment_state = _dashboard_payment_state(
                student,
                user,
                admission_session,
                payment_source,
                indigene_type=indigene_type,
            )
            if not has_valid_admission_offer(user, admission_session, student.course):
                return stage_context(
                    display_name,
                    Decimal('0'),
                    'Unavailable',
                    ADMISSION_NOT_OFFERED_MESSAGE,
                    status_class='secondary',
                )
            if acceptance_payment_state['has_successful_payment']:
                continue

        fee_category = get_application_fee_category(
            student.course,
            admission_session,
            fee_name=payment_source,
            indigene_type=indigene_type,
        )
        if not fee_category:
            return stage_context(
                display_name,
                Decimal('0'),
                'Setup missing',
                f'{display_name} setup was not found for your programme/session. Please contact ICT/Admin.',
                status_class='danger',
            )

        try:
            configured_fee = get_application_fee(
                fee_category,
                payment_source,
                admission_session,
                indigene_type,
            )
        except FeeConfigurationError:
            return stage_context(
                display_name,
                Decimal('0'),
                'Setup missing',
                f'{display_name} setup was not found for your programme/session. Please contact ICT/Admin.',
                status_class='danger',
            )

        payment_state = _dashboard_payment_state(
            student,
            user,
            admission_session,
            payment_source,
            indigene_type=indigene_type,
        )
        if payment_state['has_successful_payment']:
            continue
        latest_payment = payment_state['latest_payment']
        latest_attempt_state = _dashboard_payment_attempt_state(latest_payment)
        if latest_attempt_state != 'not_started':
            attempt_context = _dashboard_payment_attempt_message(display_name, latest_attempt_state)
            return stage_context(
                display_name,
                configured_fee.amount,
                attempt_context['status'],
                attempt_context['message'],
                can_pay=attempt_context['can_pay'],
                url=f'{reverse("initiate_payment")}?fee={slug}',
                status_class=attempt_context['status_class'],
                action_label=attempt_context['action_label'],
            )
        return stage_context(
            display_name,
            configured_fee.amount,
            'Not paid',
            f'{display_name} is configured but has not been paid.',
            can_pay=True,
            url=f'{reverse("initiate_payment")}?fee={slug}',
            status_class='danger',
            action_label='Pay Now',
        )

    if not current_session:
        return stage_context(
            'School Fees',
            Decimal('0'),
            'Setup missing',
            'School Fees setup was not found for your programme/session. Please contact ICT/Admin.',
            status_class='danger',
        )

    first_semester_fee_amount = _dashboard_fee_amount(
        student.current_level,
        'First Semester',
        current_session,
        indigene_type,
        course_of_study=student.course,
    )
    second_semester_fee_amount = _dashboard_fee_amount(
        student.current_level,
        'Second Semester',
        current_session,
        indigene_type,
        course_of_study=student.course,
    )
    full_session_fee_amount = _dashboard_fee_amount(
        student.current_level,
        'Full Session',
        current_session,
        indigene_type,
        course_of_study=student.course,
    )

    level_instance = Level.objects.filter(name=student.current_level).first()
    paid_flags = StudentFeePayment.objects.none()
    if level_instance:
        paid_flags = StudentFeePayment.objects.filter(
            student=user,
            academic_session=current_session,
            student_level=level_instance,
            course_of_study=student.course,
            is_indigene=(indigene_type == 'INDIGENE'),
        )

    school_fee_payments = _dashboard_payments_for_fee(
        student,
        user,
        current_session,
        'School Fees',
        indigene_type=indigene_type,
    )
    verified_school_fee_payments = school_fee_payments.filter(_dashboard_successful_payment_filter())
    has_paid_full_session = (
        paid_flags.filter(has_paid_full_session=True).exists()
        or verified_school_fee_payments.filter(fee_category='Full Session').exists()
        or (bool(full_session_fee_amount) and verified_school_fee_payments.filter(amount=full_session_fee_amount).exists())
    )
    has_paid_first_semester = (
        paid_flags.filter(has_paid_first_semester=True).exists()
        or has_paid_full_session
        or verified_school_fee_payments.filter(fee_category='First Semester').exists()
    )
    has_paid_second_semester = (
        paid_flags.filter(has_paid_second_semester=True).exists()
        or has_paid_full_session
        or verified_school_fee_payments.filter(fee_category='Second Semester').exists()
    )
    tuition_complete = has_paid_full_session or (has_paid_first_semester and has_paid_second_semester)
    tuition_extra = {
        'has_paid_first_semester_fee': has_paid_first_semester,
        'has_paid_second_semester_fee': has_paid_second_semester,
        'has_paid_full_session_fee': has_paid_full_session,
        'can_pay_second_semester': has_paid_first_semester and not has_paid_second_semester and not has_paid_full_session,
    }

    pending_school_fee_payment = school_fee_payments.exclude(_dashboard_successful_payment_filter()).order_by('-payment_date', '-id').first()
    if pending_school_fee_payment:
        pending_name = pending_school_fee_payment.fee_category if pending_school_fee_payment.fee_category in {'First Semester', 'Second Semester', 'Full Session'} else 'School Fees'
        display_name = f'{pending_name} School Fees' if pending_name != 'School Fees' else 'School Fees'
        attempt_context = _dashboard_payment_attempt_message(
            display_name,
            _dashboard_payment_attempt_state(pending_school_fee_payment),
        )
        tuition_message = attempt_context['message']
        tuition_extra.update({'tuition_status': 'pending', 'tuition_payment_message': tuition_message})
        return stage_context(
            display_name,
            pending_school_fee_payment.amount,
            attempt_context['status'],
            tuition_message,
            can_pay=attempt_context['can_pay'],
            url=reverse('select_fee_payment'),
            status_class=attempt_context['status_class'],
            action_label=attempt_context['action_label'],
            extra=tuition_extra,
        )

    late_deadline = getattr(current_session, 'closing_date', None)
    paid_before_late_deadline = False
    if late_deadline:
        paid_before_late_deadline = school_fee_payments.filter(
            payment_date__lt=late_deadline,
        ).exists()

    late_registration_due = (
        not tuition_complete
        and not paid_before_late_deadline
        and bool(late_deadline)
        and date.today() >= late_deadline
    )
    if late_registration_due:
        late_fee = _dashboard_late_registration_fee(student, current_session, indigene_type)
        if not late_fee or not late_fee.amount:
            return stage_context(
                'Late Registration Fee',
                Decimal('0'),
                'Setup missing',
                'Late Registration Fee setup was not found for your programme/session. Please contact ICT/Admin.',
                status_class='danger',
            )
        late_payment_state = _dashboard_payment_state(
            student,
            user,
            current_session,
            'Late Registration',
            indigene_type=indigene_type,
        )
        if not late_payment_state['has_successful_payment']:
            latest_late_payment = late_payment_state['latest_payment']
            latest_late_state = _dashboard_payment_attempt_state(latest_late_payment)
            if latest_late_state != 'not_started':
                attempt_context = _dashboard_payment_attempt_message('Late Registration Fee', latest_late_state)
                return stage_context(
                    'Late Registration Fee',
                    late_fee.amount,
                    attempt_context['status'],
                    attempt_context['message'],
                    can_pay=attempt_context['can_pay'],
                    url=f'{reverse("initiate_payment")}?fee=late-registration',
                    status_class=attempt_context['status_class'],
                    action_label=attempt_context['action_label'],
                )
            return stage_context(
                'Late Registration Fee',
                late_fee.amount,
                'Not paid',
                'Late Registration Fee is required before completing School Fees payment.',
                can_pay=True,
                url=f'{reverse("initiate_payment")}?fee=late-registration',
                status_class='danger',
                action_label='Pay Now',
                extra=tuition_extra,
            )

    if has_paid_first_semester and not has_paid_second_semester and not has_paid_full_session:
        if not second_semester_fee_amount:
            tuition_message = 'Second Semester School Fees setup was not found for your programme/session. Please contact ICT/Admin.'
            tuition_extra.update({'tuition_status': 'second_semester_setup_missing', 'tuition_payment_message': tuition_message})
            return stage_context(
                'Second Semester School Fees',
                Decimal('0'),
                'Setup missing',
                tuition_message,
                status_class='danger',
                extra=tuition_extra,
            )
        tuition_message = 'Second Semester School Fees has not been paid.'
        tuition_extra.update({'tuition_status': 'second_semester_due', 'tuition_payment_message': tuition_message})
        return stage_context(
            'Second Semester School Fees',
            second_semester_fee_amount,
            'Not paid',
            tuition_message,
            can_pay=True,
            url=reverse('select_fee_payment'),
            status_class='danger',
            action_label='Pay Now',
            extra=tuition_extra,
        )

    if not tuition_complete:
        if not first_semester_fee_amount:
            tuition_message = 'First Semester School Fees setup was not found for your programme/session. Please contact ICT/Admin.'
            tuition_extra.update({'tuition_status': 'first_semester_setup_missing', 'tuition_payment_message': tuition_message})
            return stage_context(
                'First Semester School Fees',
                Decimal('0'),
                'Setup missing',
                tuition_message,
                status_class='danger',
                extra=tuition_extra,
            )
        tuition_message = 'First Semester School Fees has not been paid.'
        tuition_extra.update({'tuition_status': 'first_semester_due', 'tuition_payment_message': tuition_message})
        return stage_context(
            'First Semester School Fees',
            first_semester_fee_amount,
            'Not paid',
            tuition_message,
            can_pay=True,
            url=reverse('select_fee_payment'),
            status_class='danger',
            action_label='Pay Now',
            extra=tuition_extra,
        )

    tuition_extra.update({
        'tuition_status': 'complete',
        'tuition_payment_message': 'Tuition payment for this session is complete.',
    })
    return stage_context(
        'All required payments completed',
        Decimal('0'),
        'Completed',
        'All required payments for your current student stage have been completed.',
        status_class='success',
        extra=tuition_extra,
    )


# Custom user log out
def custom_logout(request):
    logout(request)
    # Clear user session
    request.session.clear()
    context = {
        'page_title': 'User Logout',
    }
    # Redirect to a specific page after logout (optional)
    return render(request, 'accounts/logout.html', context)


# 404 Error Page.
def custom_page_not_found(request, exception=None):
    return render(request, 'accounts/404.html', status=404)


# 403 Error Page.
def custom_page_forbidden(request, exception=None):
    return render(request, 'accounts/403.html', status=403)


# 500 Error Page.
def custom_server_not_found(request, exception=None):
    return render(request, 'accounts/500.html', status=500)


class CustomLoginView(LoginView):
    template_name = 'accounts/login.html'

    @method_decorator(sensitive_post_parameters())
    @method_decorator(csrf_protect)
    @method_decorator(never_cache)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = _('User Login')  # You can customize the title as needed
        return context


@login_required(login_url='accounts_login')
def index(request):
    logged_user = request.user
    # Check if logged user account is disabled and redirect
    try:
        active_user = logged_user.profile.is_active
        if not active_user:
            messages.warning(request, f'{logged_user.profile.role} Account is Disabled.')
            return redirect('accounts_login')
    except Profile.DoesNotExist:
        profile = None

    # Get the user's profile
    try:
        profile = logged_user.profile
    except Profile.DoesNotExist:
        profile = None

    if profile and profile.role in {STUDENT_ROLE, GRADUATE_ROLE}:
        student_record = Student.objects.filter(candidate=logged_user).first()
        if student_record and synchronize_graduate_state(student_record):
            return redirect('student_dashboard')

    # Check if the user is an applicant (profile role is 'applicant')
    if profile and profile.role == 'Applicant':
        verified_application_fee = has_verified_application_fee(logged_user)
        # Check if the user has verified email, submitted pre-application, and 
        # has NOT made successful payment for Application Form
        if (
            ApplicantValidation.objects.filter(applicant=logged_user).exists()
            and not PreApplicant.objects.filter(applicant=logged_user).exists()
            and not verified_application_fee
        ):
            return redirect('create_application')
        

        # Check if the user has submitted pre-application 
        # but has NOT made a successful payment
        elif PreApplicant.objects.filter(applicant=logged_user).exists() \
                and not verified_application_fee:
            return redirect('returning_pre_app_slip')

        # Check if the Applicant is found in the PreApplicant, ApplicantFee and Payment model with a successful payment
        elif PreApplicant.objects.filter(applicant=logged_user).exists() \
                and verified_application_fee:
            return redirect('accounts_profile')

    elif logged_user.is_superuser or logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.profile.role == 'MIS ADMIN-1' or logged_user.profile.role == 'Admin':
        return redirect('super_admin_home')
 
    elif logged_user.profile.role == 'MIS ADMIN-2' or logged_user.profile.role == 'Rector' or logged_user.profile.role == 'Super Admin':
        return redirect('super_admin_home')
    
    elif logged_user.profile.role == STUDENT_ROLE:
        return redirect('student_dashboard')
    
    elif logged_user.profile.role == 'Staff':
        return redirect('staff_dashboard')
    
    elif logged_user.profile.role == 'Candidate':
        return redirect('post_utme_candidate_profile')

    # Redirect to dashboard if the user is not an applicant
    return redirect('accounts_dashboard')


# Staff Dashboard
@login_required(login_url='accounts_login')
def staff_dashboard(request):
    logged_user = request.user
    if not logged_user.profile.role == 'Staff':
        messages.warning(request, 'You are not a Staff')
        return redirect('accounts_login')
     
    # Check if Staff has completed registration
    if not StaffRegister.objects.filter(user=logged_user).exists():
        messages.warning(request, 'You Need to Complete your Registration First.')
        return redirect('staff_register')
    elif not staffAppSubmission.objects.filter(staff_user=logged_user, confirm=True ).exists():
        messages.warning(request, 'You Need to Submit your Registration First.')
        return redirect('staff_reg_preview')
    try:
        staff_instance = StaffRegister.objects.select_related('user').get(user=logged_user)
    except StaffRegister.DoesNotExist:
        messages.warning(request, 'Staff does Not exist')
        return redirect('accounts_login')
    
    no_staff_publication = StaffPublication.objects.filter(staff=staff_instance).count()
    
    context = {
        'staff_instance': staff_instance,
        'no_staff_publication': no_staff_publication,
        'page_title': 'Staff Dashboard',
    }
    return render(request, 'accounts/staff_dashboard.html', context)


# POST-UTML Candidate Profile
@login_required(login_url='accounts_login')
def post_utme_candidate_profile(request):
    logged_user = request.user

    # Log user activity
    UserActivityLog.objects.create(user=logged_user, action='Visited Profile Page')

    if not logged_user.profile.role == 'Candidate':
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    try:
        candidate = Jamb.objects.select_related('applicant').get(applicant=logged_user)
    except ApplicationSubmission.DoesNotExist:
        messages.warning(request, 'POST-UTME Candidate Not Found')
        return redirect('accounts_login')
    
    context = {
        'candidate': candidate,
        'page_title': 'FEDPOLY WANNUNE POST-UTME Candidate',
    }
    return render(request, 'accounts/post_utme_profile.html', context)


# POST-UTME Candidate Update Profile View
@login_required(login_url='accounts_login')
def candidate_update_profile(request):
    logged_user = request.user
    # Get the profile of the logged user
    profile = logged_user.profile
    if not logged_user.profile.role == 'Candidate':
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    # Check if the profile is complete
    if profile_complete(profile):
        messages.info(request, 'Profile is already complete.')
        return redirect('applicant_contact')

    if request.method == 'POST':
        form = CandidateProfileForm(request.POST, request.FILES, instance=profile)

        if form.is_valid():
            form.save()
            # Log user activity
            UserActivityLog.objects.create(user=logged_user, action='Updated Profile')
            messages.success(request, 'Profile Saved Successfully.')
            return redirect('applicant_sponsor_contact')
    else:
        form = CandidateProfileForm(instance=profile)

    context = {
        'form': form,
        'page_title': 'Update Profile',
    }
    return render(request, 'accounts/applicant_update_profile.html', context)


# Staff edit profile
@login_required(login_url='accounts_login')
def staff_update_profile(request):
    logged_user = request.user
    # Get the profile of the logged user
    profile = logged_user.profile
    if not logged_user.profile.role == 'Staff':
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    if request.method == 'POST':
        form = UpdateStaffProfileForm(request.POST, request.FILES, instance=profile)

        if form.is_valid():
            form.save()
            
            if not staffAppSubmission.objects.select_related('staff_user').filter(staff_user=logged_user, confirm=True):
                messages.success(request, 'Profile Saved Successfully.')
                return redirect('staff_reg_preview')
            else:
                messages.success(request, 'Profile Saved Successfully.')
                return redirect('staff_dashboard')
    else:
        form = UpdateStaffProfileForm(instance=profile)

    context = {
        'form': form,
        'page_title': f'Update {logged_user.profile.first_name} Profile',
    }
    return render(request, 'accounts/applicant_update_profile.html', context)


# Student Profile
@login_required(login_url='accounts_login')
def student_dashboard(request): 
    logged_user = request.user
    try:
        student = Student.objects.select_related(
            'candidate',
            'candidate__profile',
            'academic_session',
            'graduation_session',
            'course',
        ).get(candidate=logged_user)
    except Student.DoesNotExist:
        messages.warning(request, 'Student Not Found')
        return redirect('accounts_login')

    is_graduate = False
    graduation_session = None
    if logged_user.profile.role in {STUDENT_ROLE, GRADUATE_ROLE}:
        is_graduate = synchronize_graduate_state(student)
        if is_graduate:
            student.refresh_from_db()
            logged_user.profile.refresh_from_db()
            graduation_session = student.graduation_session or determine_graduation_session(student)

    if logged_user.profile.role not in {STUDENT_ROLE, GRADUATE_ROLE}:
        messages.warning(request, 'You are not a Student')
        return redirect('accounts_login')

    today = date.today()

    if is_graduate and graduation_session:
        latest_session, dashboard_session_message = graduation_session, ''
    else:
        latest_session, dashboard_session_message = _resolve_student_dashboard_session(student, logged_user)
    if not latest_session:
        messages.warning(request, dashboard_session_message)
        return redirect('accounts_login')

    # Get Student profile
    profile = student.candidate.profile

    # Use the indigene_status method
    indigence = profile.indigene_status()
    indigene_type = get_user_indigene_type(logged_user)

    # Retrieve current academic info
    current_level = student.current_level
    current_session = latest_session 
    current_semester = student.semester
    student_program = student.course
    student_current_level = student.current_level

    current_semester_instance = None
    current_semester_instance, current_semester = _resolve_student_dashboard_semester(student, current_session)
    current_semester_id = current_semester_instance.id if current_semester_instance else None

    dashboard_semester_message = ''
    if not current_semester_instance:
        dashboard_semester_message = (
            'Your dashboard is available, but no semester has been configured for '
            f'{current_session}. Course registration will become available when the '
            'academic semester setup is completed.'
        )

      
    # Get Previous Semester
    previous_semester = current_semester_instance.get_previous_semester() if current_semester_instance else None

    admission_session = _get_student_application_session(logged_user)
    application_payment_state = _dashboard_payment_state(
        student,
        logged_user,
        admission_session,
        'Application Form',
        indigene_type=indigene_type,
    )
    screening_payment_state = _dashboard_payment_state(
        student,
        logged_user,
        admission_session,
        'Screening Fee',
        indigene_type=indigene_type,
    )
    caution_payment_state = _dashboard_payment_state(
        student,
        logged_user,
        admission_session,
        'Caution Fee',
        indigene_type=indigene_type,
    )
    other_charges_payment_state = _dashboard_payment_state(
        student,
        logged_user,
        admission_session,
        'Other Charges',
        indigene_type=indigene_type,
    )
    school_fee_payment_state = _dashboard_payment_state(
        student,
        logged_user,
        current_session,
        'School Fees',
        indigene_type=indigene_type,
    )
    late_registration_payment_state = _dashboard_payment_state(
        student,
        logged_user,
        current_session,
        'Late Registration',
        indigene_type=indigene_type,
    )

    # Get the School Fees payments in one query
    school_fee_payments = school_fee_payment_state['successful_payments']

    total_paid_current_session = (
        school_fee_payments.aggregate(total_paid=Sum("amount"))["total_paid"] or Decimal("0")
    )
    student_payment_history = Payment.objects.filter(
        user_id__in=_dashboard_related_payment_users(student, logged_user),
    ).select_related('academic_sess').order_by('-payment_date', '-id')
    dashboard_payment_message = ''
    if not student_payment_history.exists():
        dashboard_payment_message = 'You have not made any payment yet.'
    acceptance_fee_state = _dashboard_acceptance_fee_state(student, logged_user, indigene_type)
    next_fee_context = _next_fee_context(
        student=student,
        user=logged_user,
        indigene_type=indigene_type,
        current_session=current_session,
        current_semester=current_semester,
        current_semester_instance=current_semester_instance,
    )
    if is_graduate:
        fees_settled, unsettled_periods = student_school_fees_settled(
            student, graduation_session or current_session
        )
        next_fee_context.update({
            'next_fee_name': 'School Fees',
            'next_fee_amount': Decimal('0'),
            'next_fee_status': 'Settled' if fees_settled else 'Outstanding',
            'next_fee_status_class': 'success' if fees_settled else 'danger',
            'next_fee_payment_url': '' if fees_settled else reverse('select_fee_payment'),
            'next_fee_action_label': '' if fees_settled else 'Pay Outstanding Balance',
            'next_fee_message': (
                'All recorded studenthood school-fee periods are settled.'
                if fees_settled else
                f'Outstanding school fees: {", ".join(unsettled_periods)}.'
            ),
            'can_pay_next_fee': not fees_settled,
        })

    dashboard_common_context = {
        'is_graduate': is_graduate,
        'graduation_session': graduation_session,
        'active_semester': current_semester_instance,
        'all_school_fee_payments': school_fee_payments,
        **acceptance_fee_state,
        **next_fee_context,
        'current_semester': current_semester,
        'current_semester_instance': current_semester_instance,
        'current_session': current_session,
        'dashboard_payment_message': dashboard_payment_message,
        'dashboard_session_message': dashboard_session_message,
        'dashboard_semester_message': dashboard_semester_message,
        'has_application_fee_payment': application_payment_state['has_successful_payment'],
        'has_screening_fee_payment': screening_payment_state['has_successful_payment'],
        'has_caution_fee_payment': caution_payment_state['has_successful_payment'],
        'has_other_charges_payment': other_charges_payment_state['has_successful_payment'],
        'has_school_fee_payment': school_fee_payment_state['has_successful_payment'],
        'has_other_school_fee_payments': school_fee_payment_state['has_successful_payment'],
        'has_late_registration_payment': late_registration_payment_state['has_successful_payment'],
        'payments': (
            application_payment_state['successful_payments']
            | screening_payment_state['successful_payments']
            | acceptance_fee_state.get('acceptance_fee_payment_queryset', Payment.objects.none())
            | caution_payment_state['successful_payments']
            | other_charges_payment_state['successful_payments']
            | school_fee_payment_state['successful_payments']
            | late_registration_payment_state['successful_payments']
        ).distinct(),
        'has_paid_first_semester': False,
        'has_paid_second_semester': False,
        'has_paid_full_session': False,
        'student_previous_course_enrollment': CourseEnrollment.objects.none(),
    }
    
    late_fee_obj = " "
    late_payment = False
    # Find the corresponding Current Student Level instance
    try:
        level_instance = Level.objects.get(name=student_current_level)
        student_current_level_id = level_instance.id
        # Get Level Id from the student Level      
        try:
            student_level = Level.objects.get(id=student_current_level_id)
            # Get Registered Courses for Logged in Student
            student_reg_courses = CourseEnrollment.objects.filter(
                                                                Q(student=logged_user) &  # Using Q for better readability and flexibility
                                                                Q(programme=student_program) &
                                                                Q(academic_session=latest_session) &
                                                                Q(level=student_level) &
                                                                Q(semester=current_semester_id)
                                                            ).exists()
            # Check if Student Level is Remedial Science
            if student_current_level == 'CERTIFICATE':
                
                is_remedial_science = student.is_remedial_science()
                
                # The dashboard session is resolved from the student's admission session and level.
                
                if not latest_session:
                    messages.error(request, "No academic session is linked to your dashboard yet.")
                    return redirect("accounts_dashboard")

                # Determine student level
                student_current_level = student.current_level  # Assuming `level` is a ForeignKey to Level

                (
                    total_fee_for_academic_session,
                    total_fee_for_first_semester,
                    total_fee_for_second_semester,
                    late_registration_fee,
                ) = _dashboard_school_fee_amounts(
                    student.current_level,
                    current_session,
                    indigene_type,
                    course_of_study=student.course,
                )
                
                # Determine late Registration Date is reached
                is_late = False
                message = ""
                
                late_registration_fee_amount = Decimal("0.00")

                if latest_session.closing_date:
                    if today >= latest_session.closing_date:
                        is_late = True
                        late_fee_obj = _dashboard_late_registration_fee(student, current_session, indigene_type)
                        if late_fee_obj:
                            late_registration_fee_amount = late_fee_obj.amount
                            messages.warning(request, f"Late registration in effect. Additional fee of ₦{late_registration_fee_amount} applies for late Payment.")
                
                # Calculate total expected payment
                total_expected_payment = total_fee_for_academic_session

                # Get all relevant fee payments in a single query
                paid_fees = StudentFeePayment.objects.filter(
                    student=logged_user,
                    academic_session=latest_session,
                    student_level=student_current_level_id,
                    course_of_study=student.course
                )

                # Initialize flags
                has_paid_first = False
                has_paid_second = False
                has_paid_full_session = False

                # Loop through once to set flags
                for fee in paid_fees:
                    if fee.has_paid_first_semester:
                        has_paid_first = True
                    if fee.has_paid_full_session:
                        has_paid_full_session = True
                    if fee.has_paid_second_semester or fee.has_paid_full_session:
                        has_paid_second = True

                if (
                    current_semester_instance
                    and current_semester_instance.name == 'Second Semester'
                    and not has_paid_second
                    and current_session.closing_date
                    and today >= current_session.closing_date
                ):
                    # Determine late Registration Date is reached
                    late_payment = True 
                

                # Calculate outstanding balance
                current_session_balance = total_expected_payment - total_paid_current_session 
                # Calculate current semester fee amount (if paid per semester)
                current_semester_fee_amount = _dashboard_fee_amount(
                    student.current_level,
                    current_semester,
                    current_session,
                    indigene_type,
                    course_of_study=student.course,
                )

                # Fetch all payments for the student in the current session
                current_session_fee = Payment.objects.filter( 
                    user=logged_user,
                    verified=True,
                    status="Approved",
                    payment_source__in=[
                        "Acceptance Fee", "Other Charges", "Caution Fee",
                        "Screening Fee", "Application Form", "School Fees"
                    ],
                    academic_sess=latest_session
                    , indigene_type=indigene_type
                )

                # Fetch all approved payments for the student
                payments = Payment.objects.filter(
                    user=logged_user,
                    verified=True,
                    status="Approved",
                    payment_source__in=[
                        "Acceptance Fee", "Other Charges", "Screening Fee",
                        "Caution Fee", "Application Form", "School Fees", "Late Registration"
                    ],
                    academic_sess=latest_session,
                    indigene_type=indigene_type,
                )

                # Fetch complete student payment history across all sessions.
                student_payments = student_payment_history

                # Check for specific fee payments
                has_screening_fee_payment = payments.filter(payment_source="Screening Fee").exists()
                has_acceptance_fee_payment = payments.filter(payment_source="Acceptance Fee").exists()
                has_application_fee_payment = payments.filter(payment_source="Application Form").exists()
                has_school_fee_payment = payments.filter(payment_source="School Fees").exists()
                has_caution_fee_payment = payments.filter(payment_source="Caution Fee").exists()
                has_other_charges_payment = payments.filter(payment_source="Other Charges").exists()
                has_other_school_fee_payments = payments.filter(payment_source="School Fees").exists()
                has_late_registration_payment = payments.filter(payment_source='Late Registraton').exists()

                # Get student level ID
                try:
                    level_instance = Level.objects.get(name=student_current_level)
                    student_current_level_id = level_instance.id
                except Level.DoesNotExist:
                    messages.error(request, "Level Not Found")
                    return redirect("accounts_dashboard")

                # Get student's course enrollment
                student_course_enrollment = CourseEnrollment.objects.filter(
                    student=logged_user,
                    semester=previous_semester,
                    academic_session=current_session,
                    programme=student_program
                )

                # Paginate student payments
                paginator = Paginator(student_payments, 4)
                page_number = request.GET.get("page")
                page_obj = _dashboard_decorate_payment_history(paginator.get_page(page_number))
                
                context = {
                    'has_late_registration_payment': has_late_registration_payment,
                    'late_payment': late_payment,
                    'late_registration_fee_amount': late_registration_fee_amount,
                    'total_expected_payment': total_expected_payment,
                    'is_remedial_science': is_remedial_science,
                    "student_payments": page_obj,
                    "has_other_charges_payment": has_other_charges_payment,
                    "has_caution_fee_payment": has_caution_fee_payment,
                    "student_current_level": student_current_level,
                    "student_reg_courses": student_reg_courses,
                    "total_paid_current_session": total_paid_current_session,
                    "current_session_balance": current_session_balance,
                    "has_other_school_fee_payments": has_other_school_fee_payments,
                    "has_school_fee_payment": has_school_fee_payment,
                    "has_application_fee_payment": has_application_fee_payment,
                    "has_acceptance_fee_payment": has_acceptance_fee_payment,
                    "has_screening_fee_payment": has_screening_fee_payment,
                    "payments": payments,
                    "student_course_enrollment": student_course_enrollment,
                    "current_session_fee": current_session_fee,
                    "latest_session": latest_session,
                    "student": student,
                    'current_semester_fee_amount': current_semester_fee_amount,
                    "no_outstanding_fee": current_session_balance == 0,
                    "outstanding_amount": current_session_balance,
                    "full_session_fee_amount": total_fee_for_academic_session,
                    "total_fee_for_first_semester": total_fee_for_first_semester,
                    "total_fee_for_second_semester": total_fee_for_second_semester,
                    "page_title": f"{logged_user.profile.role} Dashboard",
                }
                context.update(dashboard_common_context)
                context.update({
                    'has_paid_first_semester': has_paid_first,
                    'has_paid_second_semester': has_paid_second,
                    'has_paid_full_session': has_paid_full_session,
                })
                return render(request, 'accounts/student_dashboard.html', context) 
            
            elif student_current_level == 'NDI' or student_current_level == 'HNDI': 

                # Determine student level
                student_current_level = student.current_level # Assuming `level` is a ForeignKey to Level

                (
                    total_fee_for_academic_session,
                    total_fee_for_first_semester,
                    total_fee_for_second_semester,
                    late_registration_fee,
                ) = _dashboard_school_fee_amounts(
                    student.current_level,
                    current_session,
                    indigene_type,
                    course_of_study=student.course,
                )
                
                # Determine late Registration Date is reached
                is_late = False
                message = ""
                
                late_registration_fee_amount = Decimal("0.00")

                if latest_session.closing_date: 
                    if today >= latest_session.closing_date :
                        is_late = True
                        late_fee_obj = _dashboard_late_registration_fee(student, current_session, indigene_type)
                        if late_fee_obj:
                            late_registration_fee_amount = late_fee_obj.amount
                            messages.warning(request, f"Late registration in effect. Additional fee of ₦{late_registration_fee_amount} applies for late Payment.")

                # Calculate total expected payment
                total_expected_payment = total_fee_for_academic_session

                # Get all relevant fee payments in a single query
                paid_fees = StudentFeePayment.objects.filter(
                    student=logged_user,
                    academic_session=latest_session,
                    student_level=student_current_level_id,
                    course_of_study=student.course
                )

                # Initialize flags
                has_paid_first_semester = False
                has_paid_second_semester = False
                has_paid_full_session = False
                late_payment = False

                # Loop through the queryset to update status
                for fee in paid_fees:
                    if fee.has_paid_first_semester:
                        has_paid_first_semester = True
                    if fee.has_paid_second_semester:
                        has_paid_second_semester = True
                    if fee.has_paid_full_session:
                        has_paid_full_session = True

                # Example late payment logic
                if (
                    current_semester_instance
                    and current_semester_instance.name == 'Second Semester'
                    and not has_paid_second_semester
                    and current_session.closing_date
                    and today >= current_session.closing_date
                ):
                    late_payment = True
                                        
                                        
                    
                # Calculate outstanding balance
                current_session_balance = total_expected_payment - total_paid_current_session
                # Calculate current semester fee amount (if paid per semester)
                current_semester_fee_amount = _dashboard_fee_amount(
                    student.current_level,
                    current_semester,
                    current_session,
                    indigene_type,
                    course_of_study=student.course,
                )

                # Fetch all payments for the student in the current session
                current_session_fee = Payment.objects.filter(
                    user=logged_user,
                    verified=True,
                    status="Approved",
                    payment_source__in=[
                        "Acceptance Fee", "Other Charges", "Caution Fee",
                        "Screening Fee", "Application Form", "School Fees"
                    ],
                    academic_sess=current_session
                    , indigene_type=indigene_type
                )

                # Fetch all approved payments for the student
                payments = Payment.objects.filter(
                    user=logged_user,
                    verified=True,
                    status="Approved",
                    payment_source__in=[
                        "Acceptance Fee", "Other Charges", "Screening Fee",
                        "Caution Fee", "Application Form", "School Fees", "Late Registration"
                    ],
                    academic_sess=current_session,
                    indigene_type=indigene_type,
                )

                # Fetch complete student payment history across all sessions.
                student_payments = student_payment_history

                # Check for specific fee payments
                has_screening_fee_payment = payments.filter(payment_source="Screening Fee").exists()
                has_acceptance_fee_payment = payments.filter(payment_source="Acceptance Fee").exists()
                has_application_fee_payment = payments.filter(payment_source="Application Form").exists()
                has_school_fee_payment = current_session_fee.filter(payment_source="School Fees").exists()
                has_caution_fee_payment = payments.filter(payment_source="Caution Fee").exists()
                has_other_charges_payment = payments.filter(payment_source="Other Charges").exists()
                has_other_school_fee_payments = payments.filter(payment_source="School Fees").exists()
                has_late_registration_payment = payments.filter(payment_source="Late Registration").exists()

                # Get student level ID
                try:
                    level_instance = Level.objects.get(name=student_current_level)
                    student_current_level_id = level_instance.id
                except Level.DoesNotExist:
                    messages.error(request, "Level Not Found")
                    return redirect("accounts_dashboard")

                # Get student's course enrollment
                student_course_enrollment = CourseEnrollment.objects.filter(
                    student=logged_user,
                    semester=previous_semester,
                    academic_session=current_session,
                    programme=student_program
                )

                # Paginate student payments
                paginator = Paginator(student_payments, 4)
                page_number = request.GET.get("page")
                page_obj = _dashboard_decorate_payment_history(paginator.get_page(page_number))


                context = {
                    'has_paid_first_semester': has_paid_first_semester,
                    'has_paid_full_session': has_paid_full_session,
                    'has_late_registration_payment': has_late_registration_payment,
                    'late_payment': late_payment,
                    'is_late': is_late,
                    'late_fee_obj': late_fee_obj,
                    'late_registration_fee_amount': late_registration_fee_amount,
                    'total_expected_payment': total_expected_payment,
                    "student_payments": page_obj,
                    "has_other_charges_payment": has_other_charges_payment,
                    "has_caution_fee_payment": has_caution_fee_payment,
                    "student_current_level": student_current_level,
                    "student_reg_courses": student_reg_courses,
                    "total_paid_current_session": total_paid_current_session,
                    "current_session_balance": current_session_balance,
                    "has_other_school_fee_payments": has_other_school_fee_payments,
                    "has_school_fee_payment": has_school_fee_payment,
                    "has_application_fee_payment": has_application_fee_payment,
                    "has_acceptance_fee_payment": has_acceptance_fee_payment,
                    "has_screening_fee_payment": has_screening_fee_payment,
                    "payments": payments,
                    "student_course_enrollment": student_course_enrollment,
                    "current_session_fee": current_session_fee,
                    "latest_session": latest_session,
                    "student": student,
                    'current_semester_fee_amount': current_semester_fee_amount,
                    "no_outstanding_fee": current_session_balance == 0,
                    "outstanding_amount": current_session_balance,
                    "full_session_fee_amount": total_fee_for_academic_session,
                    "total_fee_for_first_semester": total_fee_for_first_semester,
                    "total_fee_for_second_semester": total_fee_for_second_semester,
                    "page_title": f"{logged_user.profile.role} Dashboard",
                }
                context.update(dashboard_common_context)
                context.update({
                    'has_paid_first_semester': has_paid_first_semester,
                    'has_paid_second_semester': has_paid_second_semester,
                    'has_paid_full_session': has_paid_full_session,
                })

                return render(request, "accounts/student_dashboard.html", context)
            
            elif student_current_level == 'NDII' or student_current_level == 'HNDII': 

                # Determine student level
                student_current_level = student.current_level # Assuming `level` is a ForeignKey to Level

                (
                    total_fee_for_academic_session,
                    total_fee_for_first_semester,
                    total_fee_for_second_semester,
                    late_registration_fee,
                ) = _dashboard_school_fee_amounts(
                    student.current_level,
                    current_session,
                    indigene_type,
                    course_of_study=student.course,
                )
                
                # Determine late Registration Date is reached
                is_late = False
                message = ""
                
                late_registration_fee_amount = Decimal("0.00")

                if latest_session.closing_date: 
                    if today >= latest_session.closing_date :
                        is_late = True
                        late_fee_obj = _dashboard_late_registration_fee(student, current_session, indigene_type)
                        if late_fee_obj:
                            late_registration_fee_amount = late_fee_obj.amount
                            messages.warning(request, f"Late registration in effect. Additional fee of ₦{late_registration_fee_amount} applies for late Payment.")

                # Calculate total expected payment
                total_expected_payment = total_fee_for_academic_session

                # Get all relevant fee payments in a single query
                paid_fees = StudentFeePayment.objects.filter(
                    student=logged_user,
                    academic_session=latest_session,
                    student_level=student_current_level_id,
                    course_of_study=student.course
                )

                # Initialize flags
                has_paid_first_semester = False
                has_paid_second_semester = False
                has_paid_full_session = False
                late_payment = False

                # Loop through the queryset to update status
                for fee in paid_fees:
                    if fee.has_paid_first_semester:
                        has_paid_first_semester = True
                    if fee.has_paid_second_semester:
                        has_paid_second_semester = True
                    if fee.has_paid_full_session:
                        has_paid_full_session = True

                # Example late payment logic
                if (
                    current_semester_instance
                    and current_semester_instance.name == 'Second Semester'
                    and not has_paid_second_semester
                    and current_session.closing_date
                    and today >= current_session.closing_date
                ):
                    late_payment = True
                                        
                    
                # Calculate outstanding balance
                current_session_balance = total_expected_payment - total_paid_current_session
                # Calculate current semester fee amount (if paid per semester)
                current_semester_fee_amount = _dashboard_fee_amount(
                    student.current_level,
                    current_semester,
                    current_session,
                    indigene_type,
                    course_of_study=student.course,
                )

                # Fetch all payments for the student in the current session
                current_session_fee = Payment.objects.filter( 
                    user=logged_user,
                    verified=True,
                    status="Approved",
                    payment_source__in=[
                        "Acceptance Fee", "Other Charges", "Caution Fee",
                        "Screening Fee", "Application Form", "School Fees"
                    ],
                    academic_sess=current_session
                    , indigene_type=indigene_type
                )

                # Fetch all approved payments for the student
                payments = Payment.objects.filter(
                    user=logged_user,
                    verified=True,
                    status="Approved",
                    payment_source__in=[
                        "Acceptance Fee", "Other Charges", "Screening Fee",
                        "Caution Fee", "Application Form", "School Fees", "Late Registration"
                    ],
                    academic_sess=current_session,
                    indigene_type=indigene_type,
                )

                # Fetch complete student payment history across all sessions.
                student_payments = student_payment_history

                # Check for specific fee payments
                has_screening_fee_payment = payments.filter(payment_source="Screening Fee").exists()
                has_acceptance_fee_payment = payments.filter(payment_source="Acceptance Fee").exists()
                has_application_fee_payment = payments.filter(payment_source="Application Form").exists()
                has_school_fee_payment = current_session_fee.filter(payment_source="School Fees").exists()
                has_caution_fee_payment = payments.filter(payment_source="Caution Fee").exists()
                has_other_charges_payment = payments.filter(payment_source="Other Charges").exists()
                has_other_school_fee_payments = payments.filter(payment_source="School Fees").exists()
                has_late_registration_payment = payments.filter(payment_source="Late Registration").exists()

                # Get student level ID
                try:
                    level_instance = Level.objects.get(name=student_current_level)
                    student_current_level_id = level_instance.id
                except Level.DoesNotExist:
                    messages.error(request, "Level Not Found")
                    return redirect("accounts_dashboard")

                # Get student's course enrollment
                student_course_enrollment = CourseEnrollment.objects.filter(
                    student=logged_user,
                    semester=previous_semester,
                    academic_session=current_session,
                    programme=student_program
                )

                # Paginate student payments
                paginator = Paginator(student_payments, 4)
                page_number = request.GET.get("page")
                page_obj = _dashboard_decorate_payment_history(paginator.get_page(page_number))


                context = {
                    'has_paid_first_semester': has_paid_first_semester,
                    'has_paid_second_semester': has_paid_second_semester,
                    'has_paid_full_session': has_paid_full_session,
                    'has_late_registration_payment': has_late_registration_payment,
                    'late_payment': late_payment,
                    'is_late': is_late,
                    'late_fee_obj': late_fee_obj,
                    'late_registration_fee_amount': late_registration_fee_amount,
                    'total_expected_payment': total_expected_payment,
                    "student_payments": page_obj,
                    "has_other_charges_payment": has_other_charges_payment,
                    "has_caution_fee_payment": has_caution_fee_payment,
                    "student_current_level": student_current_level,
                    "student_reg_courses": student_reg_courses,
                    "total_paid_current_session": total_paid_current_session,
                    "current_session_balance": current_session_balance,
                    "has_other_school_fee_payments": has_other_school_fee_payments,
                    "has_school_fee_payment": has_school_fee_payment,
                    "has_application_fee_payment": has_application_fee_payment,
                    "has_acceptance_fee_payment": has_acceptance_fee_payment,
                    "has_screening_fee_payment": has_screening_fee_payment,
                    "payments": payments,
                    "student_course_enrollment": student_course_enrollment,
                    "current_session_fee": current_session_fee,
                    "latest_session": latest_session,
                    "student": student,
                    'current_semester_fee_amount': current_semester_fee_amount,
                    "no_outstanding_fee": current_session_balance == 0,
                    "outstanding_amount": current_session_balance,
                    "full_session_fee_amount": total_fee_for_academic_session,
                    "total_fee_for_first_semester": total_fee_for_first_semester,
                    "total_fee_for_second_semester": total_fee_for_second_semester,
                    "page_title": f"{logged_user.profile.role} Dashboard",
                }
                context.update(dashboard_common_context)
                context.update({
                    'has_paid_first_semester': has_paid_first_semester,
                    'has_paid_second_semester': has_paid_second_semester,
                    'has_paid_full_session': has_paid_full_session,
                })

                return render(request, "accounts/student_dashboard.html", context)
            else:
                messages.warning(request, 'Level Does Not Exist Yet. Contact ICT Team.')
                return redirect('accounts_login')
        except Level.DoesNotExist:
            messages.error(request, 'Level Not Found')
    except Level.DoesNotExist:
        messages.error(request, 'Level Not Found')
        return redirect('accounts_login')
   

# Student Profile
@login_required(login_url='accounts_login')
def student_profile(request):
    logged_user = request.user
    if not logged_user.profile.role == 'Student':
        messages.warning(request, 'You are NOT a Student')
        return redirect('accounts_login')
    
    # Log user activity
    UserActivityLog.objects.create(user=request.user, action='Visited Student Profile Page')
    try:
        student = Student.objects.select_related('candidate').get(candidate=logged_user)
        latest_academic_session = student.academic_session
        # Fetch all relevant payments in a single query for the current Academic Session
        payments = Payment.objects.filter(
            user=logged_user,
            payment_source__in=['Acceptance Fee', 'Screening Fee', 'Application Form', 'School Fees'],
            academic_sess=latest_academic_session,
        )
        # Fetch all relevant payments in a single query for the current Academic Session
        all_school_fee_payments = Payment.objects.filter(
            user=logged_user,
            payment_source__in=['School Fees'],
            academic_sess=latest_academic_session,
        )
        # Check for the existence of specific fee payments
        has_screening_fee_payment = payments.filter(payment_source='Screening Fee').exists()
        has_acceptance_fee_payment = payments.filter(payment_source='Acceptance Fee').exists()
        has_application_fee_payment = payments.filter(payment_source='Application Form').exists()
        has_school_fee_payment = payments.filter(payment_source='School Fees').exists()
        has_other_school_fee_payments = payments.filter(payment_source='School Fees').exists()

    except Student.DoesNotExist:
        messages.warning(request, 'Student Not Found')
        return redirect('accounts_login')
    
    context = {
        'has_other_school_fee_payments': has_other_school_fee_payments,
        'all_school_fee_payments': all_school_fee_payments,
        'payments': payments,
        'student': student,
        'page_title': 'FEDPOLY Student Profile',
        'has_screening_fee_payment': has_screening_fee_payment,
        'has_acceptance_fee_payment': has_acceptance_fee_payment,
        'has_application_fee_payment': has_application_fee_payment,
        'has_school_fee_payment': has_school_fee_payment,
    }
    return render(request, 'accounts/student_profile.html', context) 


# Applicant Profile View
@login_required(login_url='accounts_login')
def applicant_profile(request):
    logged_user = request.user
    if not logged_user.profile.role == 'Applicant':
        messages.warning(request, 'You are NOT an Applicant')
        return redirect('accounts_login')
    
    # Get User Profile and Check if he/she has completed Profile and Redirect
    profile = get_profile(logged_user)
    sponsor = get_sponsor_details(logged_user)
    education = get_education(logged_user)
    result = get_result(logged_user)
    submitted = get_application_submission(logged_user)
    if not application_form_profile_complete(profile):
        return redirect('edit_application_form')
    elif not profile_contact_complete(profile):
        return redirect('applicant_contact')
    elif not sponsor_details_contact_complete(sponsor):
        return redirect('applicant_sponsor_contact')
    elif not applicant_education_complete(education):
        return redirect('applicant_education')
    elif not applicant_result_complete(result):
        return redirect('applicant_result')
    elif not application_submitted(submitted):
        return redirect('application_preview')
    
    applicant_validation = ApplicantValidation.objects.get(applicant=logged_user)
    
    # Fetch the course from PreApplicant
    pre_applicant = PreApplicant.objects.get(applicant=logged_user)
    course_name = pre_applicant.course_name

    # Extract category from course name
    if 'ND' in course_name.name:
        category = 'ND'
    elif 'CERTIFICATE' in course_name.name:
        category = 'CERTIFICATE'
    else:
        category = 'UNKNOWN'  # Default category or handle appropriately
    
    
    
    applicant_session = pre_applicant.academic_session or get_applicant_academic_session(logged_user)
    if not applicant_session:
        messages.warning(request, 'Academic Session Not Found for this applicant. Please contact admin.')
        return redirect('accounts_dashboard')

    indigene_type = get_user_indigene_type(logged_user)
    # Fetch verified candidate payments by exact fee source for the current Academic Session.
    payments = Payment.objects.filter(
                user=logged_user,
                verified = True,
                status='Approved',
                payment_source__in=['Application Form',  'Screening Fee', 'Acceptance Fee', 'Caution Fee', 'School Fees'], 
                academic_sess=applicant_session,
            )
    # Check for the existence of specific fee payments
    has_application_form_payment = payments.filter(payment_source='Application Form').exists()
    verified_application_fee = has_verified_application_fee(logged_user, applicant_session)
    has_application_form_payment = bool(verified_application_fee)
    has_screening_fee_payment = payments.filter(payment_source='Screening Fee').exists()
    has_acceptance_fee_payment = payments.filter(payment_source='Acceptance Fee').exists()
    has_caution_fee_payment = payments.filter(payment_source='Caution Fee').exists()
    has_school_fees_payment = payments.filter(payment_source='School Fees').exists()
    # CHECK IF APPLICANT HAS PAID APPLICATION FEE
    if not has_application_form_payment:
        try:
            application_fee = get_application_fee(category, 'Application Form', applicant_session, indigene_type)
            fee_amount = application_fee.amount
        except FeeConfigurationError as exc:
            messages.error(request, str(exc))
            fee_amount = Decimal('0')
    elif not has_screening_fee_payment:
        try:
            application_fee = get_application_fee(category, 'Screening Fee', applicant_session, indigene_type)
            fee_amount = application_fee.amount
        except FeeConfigurationError as exc:
            messages.error(request, str(exc))
            fee_amount = Decimal('0')
    else:
        fee_amount = 'Unknown'

    context = {
        'latest_academic_session': applicant_session,
        'pre_applicant': pre_applicant,
        'course_name': course_name,
        'fee_amount': fee_amount,
        'has_application_form_payment': has_application_form_payment,
        'has_acceptance_fee_payment': has_acceptance_fee_payment,
        'has_screening_fee_payment': has_screening_fee_payment,
        'has_caution_fee_payment': has_caution_fee_payment,
        'has_school_fees_payment': has_school_fees_payment,
        'page_title': 'ECOSAHT Applicant Profile',
    }
    return render(request, 'accounts/applicant_profile.html', context)


# Applicant Update Profile View
@login_required(login_url='accounts_login')
def applicant_update_profile(request):
    logged_user = request.user
    # Get the profile of the logged user
    profile = logged_user.profile
    if not logged_user.profile.role == 'Applicant':
        return redirect('accounts_login')
    # Log user activity
    UserActivityLog.objects.create(user=request.user, action=f' {logged_user} Visited Update Profile Page')
    if request.method == 'POST':
        form = ProfileForm(request.POST, request.FILES, instance=profile)

        if form.is_valid():
            form.save()
            messages.success(request, 'Profile Saved Successfully.')
            return redirect('accounts_profile')
    else:
        form = ProfileForm(instance=profile)

    context = {
        'form': form,
        'page_title': 'Update Profile',
    }
    return render(request, 'accounts/applicant_update_profile.html', context)


# Super Admin Login Home
@login_required(login_url='accounts_login')
def user_admin_index(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'MIS ADMIN-2' or logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.profile.role == 'MIS ADMIN-1' or logged_user.is_superuser or logged_user.profile.role == 'Rector' or logged_user.profile.role == 'Admin' or logged_user.profile.role == 'Super Admin'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_profile')

    count_schools = Faculty.objects.count()
    count_departments = Department.objects.count()
    count_programs = CourseOfStudy.objects.count()

    if logged_user.profile.role == 'MIS ADMIN-1':
        # Check for academic objects existance
        if not ApplicationFee.objects.exists():
            messages.warning(request, 'Add Application Fee for Admission First.')
            return redirect('add_app_fee')
        elif not Fee.objects.exists():
            messages.warning(request, 'Add School Fee to Levels First.')
            return redirect('add_school_fees')
        elif not AcademicSession.objects.exists():
            messages.warning(request, 'Add Academic Session First.')
            return redirect('add_session')
        elif not Semester.objects.exists():
            messages.warning(request, 'Add Semester First.')
            return redirect('add_semester')
        elif not Faculty.objects.exists():
            messages.warning(request, 'Add School First.')
            return redirect('add_school')
        elif not Department.objects.exists():
            messages.warning(request, 'Add Department')
            return redirect('add_department')
        elif not CourseOfStudy.objects.exists():
            messages.warning(request, 'Add Programme of Study.')
            return redirect('add_program')
        elif not Level.objects.exists():
            messages.warning(request, 'Add Level of Study First.')
            return redirect('add_level')
        elif not CourseUnit.objects.exists():
            messages.warning(request, 'Add Course Units')
            return redirect('add_course_unit')

    # Get the recently added academic session
    recently_added_session = AcademicSession.get_application_session()

    # Count records with a specific condition, for example, where confirm is True
    confirmed_submissions = ApplicationSubmission.objects.filter(confirm=True, academic_sess=recently_added_session).count()

    if recently_added_session:
        # Call the class method to get the total amount paid for application forms
        total_amount_paid = Payment.total_amount_paid_for_application_form(recently_added_session)
        # Count the number of payments for application forms in the academic session
        payment_count = Payment.objects.filter(
            academic_sess=recently_added_session,
            payment_source='Application Form',
        ).count()
        # Call the class method to get the total amount paid for School Fees in current session
        total_paid_fees = Payment.total_amount_paid_school_fees(recently_added_session)
        # Count the number of payments for application forms in the academic session
        count_fees_payment = Payment.count_users_with_school_fees_payment(academic_sess=recently_added_session)
    else:
        total_paid_fees = 0.00
        count_fees_payment = 0 
        total_amount_paid = 0.00
        payment_count = 0
   
    context = {
        'total_paid_fees': total_paid_fees,
        'count_fees_payment': count_fees_payment,
        'confirmed_submissions': confirmed_submissions,
        'count_schools': count_schools,
        'count_departments': count_departments,
        'count_programs': count_programs,
        'recently_added_session': recently_added_session,
        'total_amount_paid': total_amount_paid,
        'count_applicants': payment_count,
        'page_title': 'ECOSAH Admin Home',
    }
    return render(request, 'accounts/super_admin_dashboard.html', context)


# Create Super Admin User View
@login_required(login_url='accounts_login')
def create_super_admin_user(request):
    logged_user = request.user
   
    try:
        user_profile = Profile.objects.get(user=logged_user)
        user_role = user_profile.role
        # Check if logged in user is super admin or admin else don't allow access
        if logged_user.is_superuser or logged_user.profile.role == 'Super Admin' or logged_user.profile.role == 'Admin':
            if request.method == 'POST':
                form = SuperAdminUserCreationForm(request.POST)
                if form.is_valid():
                    user = form.save()
                    role = form.cleaned_data.get('role')
                    # Get or create the user's profile and update the role
                    profile, created = Profile.objects.get_or_create(user=user)
                    profile.role = role
                    profile.save()
                    messages.success(request, f'{role} Created Successfully.')
            else:
                form = SuperAdminUserCreationForm()
            context = {
                'page_title': 'Create Portal User',
                'form': form
                }
            return render(request, 'accounts/create_super_admin_user.html', context)
        else:
            return redirect('accounts_dashboard')
    except Profile.DoesNotExist:
        # Handle the case when the profile doesn't exist
        return redirect('accounts_dashboard')


@login_required(login_url='accounts_login')
def staff_user_list(request):
    # Ensure the user is a superuser or has the 'Super Admin' role
    if not (request.user.is_superuser or request.user.profile.role in ['Admin', 'Super Admin']):
        messages.warning(request, 'You do not have permission to view this page.')
        return redirect('accounts_login')
    # Log user activity
    UserActivityLog.objects.create(user=request.user, action='Visited Staff List Page')
    # Common base query for staff_list
    staff_list = Profile.objects.filter(
        Q(role="MIS ADMIN-2") | Q(role="Rector") | Q(role="MIS ADMIN-1") | Q(role="Admin") | Q(role="ADMISSION-OFFICER") | Q(role="Super Admin")
    ).order_by('user__username')  # Ordering by username, for example

    number_of_staff = staff_list.count()

    form = SearchUserForm(request.GET or None)

    if request.method == "GET" and form.is_valid():
        search_query = form.cleaned_data['search_query']
        # Apply additional filters based on the search query
        staff_list = staff_list.filter(
            Q(user__username__icontains=search_query) |
            Q(first_name__icontains=search_query)
        )

    paginator = Paginator(staff_list, 5)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Portal Users',
        'staff_list': page_obj,
        'number_of_staff': number_of_staff,
        'form': form,
    }
    return render(request, 'accounts/staff_list.html', context)



# Disable Portal user
@login_required(login_url='accounts_login')
def disable_user(request, pk):
    if request.user.is_superuser or request.user.profile.role in ['Super Admin']:
        # Log user activity
        UserActivityLog.objects.create(user=request.user, action='Visited Disable User Page')
        user_to_disable = get_object_or_404(Profile, pk=pk)
        # Change the USER is_active to False
        user_to_disable.is_active = False
        user_to_disable.save()
        messages.success(request, f'{user_to_disable.user.username} Disabled Successfully.')
        return redirect('staff_user_list')


# Enable Portal User
@login_required(login_url='accounts_login')
def enable_user(request, pk):
    if request.user.is_superuser or request.user.profile.role in ['Super Admin']:
        # Log user activity
        UserActivityLog.objects.create(user=request.user, action='Visited Enable User Page')
        user_to_enable = get_object_or_404(Profile, pk=pk)
        # Change the USER is_active to False
        user_to_enable.is_active = True
        user_to_enable.save()
        messages.success(request, f'{user_to_enable.user.username} Enabled Successfully.')
        return redirect('staff_user_list')


# Change user password view
@login_required(login_url='accounts_login')
def change_password(request):
    # Log user activity
    UserActivityLog.objects.create(user=request.user, action='Visited Change Password Page')
    if request.method == 'POST':
        form = PasswordChangeForm(request.POST)
        if form.is_valid():
            user = authenticate(username=request.user.username, password=form.cleaned_data['old_password'])
            if user is not None:
                user.set_password(form.cleaned_data['new_password'])
                user.save()
                update_session_auth_hash(request, user)  # Update session to prevent logout
                messages.success(request, 'Your Password Changed Successfully.')
                
            else:
                form.add_error('old_password', 'Incorrect old password')
                messages.warning(request, 'Incorrect old Password.')
    else:
        form = PasswordChangeForm()

    context = {
        'form': form,
        'page_title': 'Change Password',
        }
    return render(request, 'accounts/password_change.html', context)


# Super Admin Update Staff Profile
@login_required(login_url='accounts_login')
def super_user_update_staff_profile(request, pk):
    logged_user = request.user
    if not (logged_user.is_superuser or logged_user.profile.role == 'Super Admin' or logged_user.profile.role == 'ADMISSION-OFFICER'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    profile = get_object_or_404(Profile, user_id=pk)
    if request.method == 'POST':
        form = UpdateStaffProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Staff Profile Updated Successfully')
            return redirect('super_admin_staff_reg_preview', profile.user.id)  # Redirect to the profile detail page
    else:
        form = UpdateStaffProfileForm(instance=profile)
    context = {
        'form': form,
        'profile': profile
        }
    return render(request, 'accounts/super_admin_edit_staff_profile.html', context)


# Super User Update Staff Register
@login_required(login_url='accounts_login')
def super_user_update_staff_register(request, pk):
    logged_user = request.user
    if not (logged_user.is_superuser or logged_user.profile.role == 'Super Admin' or logged_user.profile.role == 'ADMISSION-OFFICER'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    staff_user = get_object_or_404(StaffRegister, user_id=pk)
    if request.method == 'POST':
        form = SuperUserEditStaffRegisterForm(request.POST, instance=staff_user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Staff Registration Updated Successfully.')
            return redirect('super_admin_staff_reg_preview', staff_user.user.id)  # Redirect to the staff_user detail page
    else:
        form = SuperUserEditStaffRegisterForm(instance=staff_user)
    context = {
        'form': form,
        'staff_user': staff_user
        }
    return render(request, 'accounts/super_user_edit_staffreg.html', context)


# Super User Update Staff Academic Record
@login_required(login_url='accounts_login')
def super_user_update_staff_academic_record(request, pk):
    logged_user = request.user
    if not (logged_user.is_superuser or logged_user.profile.role == 'Super Admin' or logged_user.profile.role == 'ADMISSION-OFFICER'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    # Check Staff in Register
    try:
        registered_staff = StaffRegister.objects.select_related('user').get(user_id=pk)
    except StaffRegister.DEPARTMENT_CATEGORY:
        messages.warning(request, 'Staff Not Found')
        return redirect('accounts_login')
    # Get the Registered Staff form Academic Staff Record
    academic_staff = get_object_or_404(AcademicStaff, staff=registered_staff)

    if request.method == 'POST':
        form = SuperUserEditStaffAcademicRecordForm(request.POST, request.FILES, instance=academic_staff)
        if form.is_valid():
            form.save()
            messages.success(request, 'Staff Academic Record Updated Successfully.')
            return redirect('super_admin_staff_reg_preview', registered_staff.user.id)  # Redirect to the staff_user detail page
    else:
        form = SuperUserEditStaffAcademicRecordForm(instance=academic_staff)
    context = {
        'form': form,
        'page_title': 'Admin User Edit Staff Record',
        }
    return render(request, 'accounts/super_user_edit_staffrecord.html', context)


# Super Admin Change User Password
@login_required(login_url='accounts_login')
def password_reset_view(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'Super Admin' or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    if request.method == 'POST':
        form = PasswordResetForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            new_password = form.cleaned_data['new_password']
            user = User.objects.get(username=username)
            user.set_password(new_password)
            user.save()
            messages.success(request, f"Password for {username} has been reset successfully.")
    else:
        form = PasswordResetForm()
    context = {
        'form': form,
        'page_title': 'Reset User Password',
    }
    return render(request, 'accounts/super_admin_password_reset.html', context)


# Super Admin view user Activity
@login_required(login_url='accounts_login')
def super_admin_view_user_log(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'Super Admin' or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    # Get all the user activity logs
    user_logs = UserActivityLog.objects.order_by('-timestamp')

    # Search functionality
    search_query = request.GET.get('q')
    if search_query:
        try:
            # Try to convert search_query to an integer (user ID)
            user_id = int(search_query)
            user_logs = user_logs.filter(user_id=user_id)
        except ValueError:
            # If conversion fails, search by username (case-insensitive)
            user_logs = user_logs.filter(user__username__icontains=search_query)

    # Pagination with search query included
    paginator = Paginator(user_logs, 10)

    # Get page number with fallback for missing page parameter
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'User Activities',
        'activities': page_obj,
        'search_query': search_query,
        'search_form': UserActivityLogSearchForm(request.GET),  # Include full GET parameters
    }
    return render(request, 'accounts/user_activities.html', context)


# List of All Portal Users view.
class PortalUserListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = User
    template_name = 'accounts/portal_user_list.html'
    context_object_name = 'users'
    paginate_by = 10

    # function to check if the user is allowed
    def test_func(self):
        return self.request.user.profile.role == 'Super Admin' or self.request.user.is_superuser 
    
    def get_queryset(self):
        # Retrieve the original queryset
        queryset = User.objects.select_related('profile').all()
        
        # Get the search query parameter from the request
        search_query = self.request.GET.get('q')

        # If a search query is provided, filter the queryset by username
        if search_query:
            queryset = queryset.filter(username__icontains=search_query)
        
        # Add ordering to the queryset
        return queryset
    
    def dispatch(self, request, *args, **kwargs):
        # Log user activity before dispatching the view
        UserActivityLog.objects.create(user=request.user, action='Visited All Portal User List Page')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'List of All Users'
        # Add the user search form to the context
        context['form'] = UserActivityLogSearchForm()
        context['messages'] = messages.get_messages(self.request)  # Access messages
        # Add pagination information to the context
        queryset = self.get_queryset()  # Retrieve the original queryset
        paginator = Paginator(queryset, self.paginate_by)  # Paginate before rendering
        page_number = self.request.GET.get('page')

        try:
            page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)

        context['page_obj'] = page_obj
        context['paginator'] = paginator
        return context


# Delete user View
class PortalUserDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = User
    template_name = 'accounts/confirm_delete_portal_user.html'
    success_url = '/list_all_users/'
    context_object_name = 'user'  # the context object name

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'Super Admin' or self.request.user.is_superuser 

    def delete(self, request, *args, **kwargs):
        messages.success(request, 'User successfully Removed.')
        return super().delete(request, *args, **kwargs)
    
    def dispatch(self, request, *args, **kwargs):
        user = self.get_object()  # Get the user instance before deleting it
        # Log user activity before dispatching the view
        UserActivityLog.objects.create(user=request.user, action=f'Removed {user.profile.role} with name; {user.profile.first_name} {user.profile.last_name} from the Database. ')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Remove Portal User'
        return context
    

# Admin User view Student Profile
@login_required(login_url='accounts_login')
def admin_view_student_profile(request, student_id):
    logged_user = request.user
    if not (logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.profile.role == 'Super Admin' or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong with Login')
        return redirect('accounts_login')
    
    try:
        student = Student.objects.select_related('candidate').get(id=student_id)
    except Student.DoesNotExist:
        messages.warning(request, 'Something Went Wrong with Student')
        return redirect('accounts_login')
    context = {
        'student': student,
        'page_title': f' {student.candidate.profile.last_name} Student Profile',
    }
    return render(request, 'accounts/admin_student_profile.html', context)


# Change User Name
@login_required(login_url='accounts_login')
def change_portal_username(request):
    logged_user = request.user
    if not (logged_user.profile.role in ['Super Admin'] or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    if request.method == 'POST':
        if 'search' in request.POST:
            search_form = SearchUserForm(request.POST)
            if search_form.is_valid():
                search_query = search_form.cleaned_data['search_query']
                try:
                    user = User.objects.get(username=search_query)
                    edit_form = EditUserForm(instance=user)
                    return render(request, 'accounts/edit_username.html', {
                        'search_form': search_form,
                        'edit_form': edit_form,
                        'student': user,
                    })
                except User.DoesNotExist:
                    messages.error(request, 'No user matches the given username.')
                    return render(request, 'accounts/edit_username.html', {
                        'search_form': search_form,
                    })
        elif 'edit' in request.POST:
            user_id = request.POST.get('user_id')
            try:
                user = User.objects.get(id=user_id)
                edit_form = EditUserForm(request.POST, instance=user)
                if edit_form.is_valid():
                    new_username = edit_form.cleaned_data['username']
                    if User.objects.filter(username=new_username).exists():
                        messages.error(request, 'The new username already exists. Please choose a different username.')
                        return render(request, 'accounts/edit_username.html', {
                            'search_form': SearchUserForm(),
                            'edit_form': edit_form,
                            'student': user,
                            'page_title': 'Change Username',
                        })
                    edit_form.save()
                    messages.success(request, f'User with Username {user.username} details updated successfully.')
                    return redirect('change_portal_username')
                else:
                    messages.error(request, 'Please correct the error below.')
                    return render(request, 'accounts/edit_username.html', {
                        'search_form': SearchUserForm(),
                        'edit_form': edit_form,
                        'student': user,
                        'page_title': 'Change Username',
                    })
            except User.DoesNotExist:
                messages.error(request, 'No user matches the given ID.')
                return render(request, 'accounts/edit_username.html', {
                    'search_form': SearchUserForm(),
                })
    else:
        search_form = SearchUserForm()

    context = {
        'search_form': search_form,
        'page_title': 'Edit Username',
    }

    return render(request, 'accounts/edit_username.html', context)

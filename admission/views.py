from __future__ import print_function
from django.utils import timezone
from accounts.models import GRADUATE_ROLE, STUDENT_ROLE, UserActivityLog
from datetime import datetime
from django.http import HttpResponse
from django.db.models import Max
from django.template.loader import render_to_string
from xhtml2pdf import pisa
from django.views.generic import DetailView
from django.views import View
from io import BytesIO
import logging
from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from django.template.loader import get_template
import datetime
import xlsxwriter
from datetime import datetime, timedelta
from django.db.models import Exists, OuterRef
from datetime import date
from django.db.models import Max
from .helpers import (
    get_application_submission, get_education,
    application_submitted, get_sponsor_details,
    sponsor_details_contact_complete, get_result,
    applicant_education_complete, applicant_result_complete,
    has_verified_application_fee, get_applicant_academic_session
)
from accounts.helpers import (
    get_profile, profile_complete,
    profile_contact_complete,
    application_form_profile_complete
)
from django.http import JsonResponse
from dateutil.relativedelta import relativedelta
from dateutil import parser
import csv
import difflib
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponse
from django.db.models import Q
from django.db.models import F
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.urls import reverse, reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
# from pprint import pprint
from django.shortcuts import render, redirect, get_object_or_404
from .forms import (PreApplicationForm, StaffRegistrationForm, ApplicationSubmissionForm,
                    ApplicantContactForm, StaffRegSubmissionForm, CandidateEditContactForm,
                    SponsorDetailsForm, UpdateStaffRegistration, CandidateJambForm,
                    ApplicantResultForm, PreApplicationEditForm,
                    ApplicationSubmissionForm, ApplicantProfileForm,
                    ApplicantEditContactForm, SearchPreapplicantForm,
                    AdmitByProgramForm, SearchStudentForm, ApplicationNumberForm,
                    AdmissionListForm, AdminApplicantEditContactForm,
                    CSVUploadForm, SearchSubmitedApplicationForm,
                    ApplicationlistBySessionForm, ApplicationReportByProgrammForm,
                    StudentCreationForm, StudentLevelForm, ProfileImageUploadForm,
                    get_application_fee_category
                    )
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.messages import get_messages
from academics.models import AcademicSession, CourseOfStudy, Student, StaffFile
from accounts.student_academic import resolve_student_academic_session, resolve_student_semester
from accounts.models import Profile, LocalGovernment, StaffRegister, AcademicStaff, State
from fees.models import ApplicationFee, Fee
from fees.utils import get_user_indigene_type
import hashlib
from .models import (ApplicantValidation, staffAppSubmission,
                     PreApplicant,
                     SponsorDetails,
                     EducationRecord,
                     ApplicantResult,
                     ApplicationSubmission,
                     Jamb, Admitted
                     )
from .forms import ApplicantValidationForm, EducationRecordForm, ApplicationReportForm
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode
from django.contrib.auth import login
from django.utils.encoding import force_str
import uuid
from accounts.forms import StaffUserCreation, StudentProfileForm
from academics.forms  import StaffFileActivationForm
from decouple import config
from django.contrib.auth.models import User
from payments.models import Payment
from accounts.models import StaffPublication

SCREENING_FEE_SOURCE = 'Screening Fee'
APPLICATION_FORM_SOURCE = 'Application Form'


def user_can_manage_admissions(user):
    return (
        user.profile.role == 'ADMISSION-OFFICER'
        or user.is_superuser
        or user.profile.role == 'Super Admin'
    )


def admission_screening_payment_queryset(application_queryset=None, category=None):
    payments = Payment.objects.filter(
        user=OuterRef('applicant'),
        academic_sess=OuterRef('academic_sess'),
        payment_source=SCREENING_FEE_SOURCE,
        status='Approved',
        verified=True,
    )
    if category:
        payments = payments.filter(fee_category__iexact=category)
    return payments


def get_admission_candidate_queryset(course_of_study=None, academic_session=None, application_category=None, search_query=None, include_admitted=False):
    screening_payment = admission_screening_payment_queryset(category=application_category)
    application_form_payment = Payment.objects.filter(
        user=OuterRef('applicant'),
        academic_sess=OuterRef('academic_sess'),
        payment_source=APPLICATION_FORM_SOURCE,
        status='Approved',
        verified=True,
    )

    candidates = (
        ApplicationSubmission.objects
        .select_related(
            'applicant',
            'applicant__profile',
            'applicant__pre_applicant',
            'academic_sess',
            'course_name',
            'course_name__department',
        )
        .filter(confirm=True)
        .annotate(
            has_verified_screening_fee=Exists(screening_payment),
            has_verified_application_form=Exists(application_form_payment),
        )
        .filter(has_verified_screening_fee=True)
    )

    if not include_admitted:
        candidates = (
            candidates
            .filter(is_admitted=False)
            .exclude(applicant__student_profile__isnull=False)
            .exclude(applicant__admitted__isnull=False)
        )

    if course_of_study:
        candidates = candidates.filter(course_name=course_of_study)

    if academic_session:
        candidates = candidates.filter(academic_sess=academic_session)

    if search_query:
        search_query = search_query.strip()
        candidates = candidates.filter(
            Q(applicant__username__icontains=search_query)
            | Q(applicant__email__icontains=search_query)
            | Q(applicant__profile__first_name__icontains=search_query)
            | Q(applicant__profile__middle_name__icontains=search_query)
            | Q(applicant__profile__last_name__icontains=search_query)
            | Q(applicant__pre_applicant__application_number__icontains=search_query)
            | Q(course_name__name__icontains=search_query)
            | Q(academic_sess__name__icontains=search_query)
            | Q(applicant__fee_payment__fee_category__icontains=search_query)
        )

    return candidates.order_by(
        'course_name__name',
        'applicant__profile__last_name',
        'applicant__profile__first_name',
        'id',
    ).distinct()


def attach_admission_payment_details(applications):
    application_list = list(applications)
    for application in application_list:
        application.screening_payment = (
            Payment.objects
            .filter(
                user=application.applicant,
                academic_sess=application.academic_sess,
                payment_source=SCREENING_FEE_SOURCE,
                status='Approved',
                verified=True,
            )
            .order_by('-payment_date', '-id')
            .first()
        )
        application.application_form_payment = (
            Payment.objects
            .filter(
                user=application.applicant,
                academic_sess=application.academic_sess,
                payment_source=APPLICATION_FORM_SOURCE,
                status='Approved',
                verified=True,
            )
            .order_by('-payment_date', '-id')
            .first()
        )
    return application_list


def get_admission_review_context(application):
    education_records = EducationRecord.objects.filter(applicant=application.applicant).order_by('-end_year', 'school_name')
    result_records = ApplicantResult.objects.filter(applicant=application.applicant).order_by('result_name', 'subject_name')
    sponsor = SponsorDetails.objects.filter(applicant=application.applicant).first()
    screening_payment = (
        Payment.objects
        .filter(
            user=application.applicant,
            academic_sess=application.academic_sess,
            payment_source=SCREENING_FEE_SOURCE,
            status='Approved',
            verified=True,
        )
        .order_by('-payment_date', '-id')
        .first()
    )
    application_form_payment = (
        Payment.objects
        .filter(
            user=application.applicant,
            academic_sess=application.academic_sess,
            payment_source=APPLICATION_FORM_SOURCE,
            status='Approved',
            verified=True,
        )
        .order_by('-payment_date', '-id')
        .first()
    )

    return {
        'application': application,
        'profile': application.applicant.profile,
        'education_records': education_records,
        'result_records': result_records,
        'sponsor': sponsor,
        'screening_payment': screening_payment,
        'application_form_payment': application_form_payment,
    }
from django.db import transaction

APPLICATION_FEE_REQUIRED_MESSAGE = 'Please complete and verify your application fee payment before continuing.'
PAYMENT_VERIFICATION_FAILED_MESSAGE = 'Payment Verification Failed'


def _remove_stale_payment_verification_failed_message(request):
    preserved_messages = []
    message_storage = get_messages(request)
    for message in message_storage:
        if str(message) != PAYMENT_VERIFICATION_FAILED_MESSAGE:
            preserved_messages.append(message)
    message_storage.used = True
    loaded_messages = getattr(message_storage, '_loaded_messages', None)
    if isinstance(loaded_messages, list):
        loaded_messages.clear()
    queued_messages = getattr(message_storage, '_queued_messages', None)
    if isinstance(queued_messages, list):
        queued_messages.clear()
    for message in preserved_messages:
        messages.add_message(request, message.level, str(message), extra_tags=message.extra_tags)


def _redirect_if_application_fee_unverified(request):
    _remove_stale_payment_verification_failed_message(request)
    applicant_session = get_applicant_academic_session(request.user)
    if has_verified_application_fee(request.user, applicant_session):
        return None
    if applicant_session:
        messages.warning(request, f'{APPLICATION_FEE_REQUIRED_MESSAGE} Academic Session: {applicant_session}.')
    else:
        messages.warning(request, 'Academic Session Not Found for your application. Please contact admin.')
    return redirect('initiate_payment')


def _verified_fee_payments_for_session(user, academic_session):
    return Payment.objects.filter(
        user=user,
        verified=True,
        status='Approved',
        academic_sess=academic_session,
        payment_source__in=['Acceptance Fee', 'Caution Fee', 'Other Charges', 'Screening Fee', 'Application Form', 'School Fees'],
    )


SCHEDULE_APPLICATION_FEE_ORDER = (
    'Application Form',
    'Screening Fee',
    'Acceptance Fee',
    'Caution Fee',
    'Other Charges',
)

SCHEDULE_SCHOOL_FEE_ORDER = {
    'Full Session': 1,
    'First Semester': 2,
    'Second Semester': 3,
}


def _active_fee_filter(model):
    if any(field.name == 'is_active' for field in model._meta.fields):
        return {'is_active': True}
    if any(field.name == 'active' for field in model._meta.fields):
        return {'active': True}
    if any(field.name == 'enabled' for field in model._meta.fields):
        return {'enabled': True}
    return {}


def _fee_amount_filter():
    return {'amount__isnull': False, 'amount__gt': 0}


def _school_fee_context_for_user(user):
    role = getattr(getattr(user, 'profile', None), 'role', None)
    context = {
        'user_type': role,
        'student': None,
        'admitted_record': None,
        'pre_applicant': None,
        'application_submission': None,
        'academic_session': None,
        'application_session': None,
        'programme': None,
        'level': None,
        'semester': None,
        'message': '',
    }

    pre_applicant = (
        PreApplicant.objects
        .select_related('academic_session', 'course_name', 'course_name__department')
        .filter(applicant=user)
        .order_by('-id')
        .first()
    )
    application_submission = (
        ApplicationSubmission.objects
        .select_related('academic_sess', 'course_name', 'course_name__department')
        .filter(applicant=user)
        .order_by('-id')
        .first()
    )
    admitted_record = (
        Admitted.objects
        .select_related('academic_session', 'admitted_course', 'admitted_course__department')
        .filter(applicant=user)
        .order_by('-admitted_at', '-id')
        .first()
    )

    context.update({
        'pre_applicant': pre_applicant,
        'application_submission': application_submission,
        'admitted_record': admitted_record,
    })

    if role == 'Student':
        student = (
            Student.objects
            .select_related('candidate', 'academic_session', 'course', 'course__department')
            .filter(candidate=user, graduated=False)
            .first()
        )
        if not student:
            context['message'] = 'No student record was found for your account.'
            return context

        academic_session, session_message = resolve_student_academic_session(student, user)
        semester, semester_name = resolve_student_semester(student, academic_session, require_active=False)
        context.update({
            'student': student,
            'academic_session': academic_session,
            'application_session': academic_session,
            'programme': student.course,
            'level': student.current_level,
            'semester': semester_name if semester else (student.semester or ''),
            'message': session_message,
        })
        if not academic_session:
            context['message'] = context['message'] or 'No active academic session is linked to your student record.'
        return context

    if role in ['Applicant', 'Candidate']:
        session = None
        programme = None
        if admitted_record and admitted_record.academic_session_id:
            session = admitted_record.academic_session
            programme = admitted_record.admitted_course
            context['level'] = admitted_record.admitted_level
        elif application_submission and application_submission.academic_sess_id:
            session = application_submission.academic_sess
            programme = application_submission.course_name
        elif pre_applicant and pre_applicant.academic_session_id:
            session = pre_applicant.academic_session
            programme = pre_applicant.course_name

        context.update({
            'academic_session': session,
            'application_session': session,
            'programme': programme,
        })
        if not session:
            context['message'] = 'No application or admission academic session is linked to your account.'
        return context

    context['message'] = 'This fee schedule is available only to applicants, candidates, and students.'
    return context


def _application_fee_schedule_rows(user, session, programme):
    if not session or not programme:
        return []

    rows = []
    indigene_type = get_user_indigene_type(user)
    for fee_name in SCHEDULE_APPLICATION_FEE_ORDER:
        category = get_application_fee_category(
            programme,
            academic_session=session,
            fee_name=fee_name,
            indigene_type=indigene_type,
        )
        if not category:
            continue
        fee = (
            ApplicationFee.objects
            .filter(
                academic_session=session,
                category__iexact=category,
                name__iexact=fee_name,
                indigene_type=indigene_type,
                **_fee_amount_filter(),
                **_active_fee_filter(ApplicationFee),
            )
            .order_by('name', 'id')
            .first()
        )
        if fee:
            rows.append({
                'name': fee.name,
                'amount': fee.amount,
                'session': fee.academic_session,
                'programme': programme,
                'category': fee.category,
                'level': '',
                'semester': '',
                'indigene_type': fee.indigene_type,
            })
    return rows


def _school_fee_schedule_rows(user, session, programme, level):
    if not session or not level:
        return []

    fees = (
        Fee.objects
        .filter(
            academic_session=session,
            level=level,
            indigene_type=get_user_indigene_type(user),
            **_fee_amount_filter(),
            **_active_fee_filter(Fee),
        )
        .order_by('name', 'semester', 'id')
    )
    rows = []
    for fee in fees:
        rows.append({
            'name': fee.name,
            'amount': fee.amount,
            'session': fee.academic_session,
            'programme': programme,
            'category': 'School Fees' if fee.name != 'Late Registration' else 'Late Registration',
            'level': fee.level,
            'semester': fee.semester,
            'indigene_type': fee.indigene_type,
            'sort_key': SCHEDULE_SCHOOL_FEE_ORDER.get(fee.semester, 99),
        })
    return sorted(rows, key=lambda row: (row['category'], row.get('sort_key', 99), row['name']))


def _build_fee_schedule_sections(user, schedule_context):
    application_rows = _application_fee_schedule_rows(
        user,
        schedule_context.get('application_session') or schedule_context.get('academic_session'),
        schedule_context.get('programme'),
    )
    school_rows = _school_fee_schedule_rows(
        user,
        schedule_context.get('academic_session'),
        schedule_context.get('programme'),
        schedule_context.get('level'),
    )

    sections = []
    if application_rows:
        sections.append({
            'title': 'Application and Admission Fees',
            'description': 'Fees configured for your application or admission session.',
            'rows': application_rows,
        })
    if school_rows:
        sections.append({
            'title': 'School Fees and Related Charges',
            'description': 'Fees configured for your student academic session and level.',
            'rows': school_rows,
        })
    return sections

# Staff file Validation
@transaction.atomic
def staff_file_activation(request):
    if request.method == 'POST':
        form = StaffFileActivationForm(request.POST)
        if form.is_valid():
            file_number = form.cleaned_data['file_number']

            # Check if the staff file with the given file_number exists
            try:
                staff_file = StaffFile.objects.get(file_number=file_number)

                # Check if the staff file has already been activated
                if staff_file.is_activated:
                    messages.info(request, 'Staff file is already activated.')
                    return redirect('staff_email_validation')  # Update with the appropriate URL path
                else:
                    # Update the is_activated status to True
                    staff_file.is_activated = True
                    staff_file.save()
                    messages.success(request, 'Staff file activated successfully.')
                    return redirect('staff_email_validation')  # Update with the appropriate URL path

            except StaffFile.DoesNotExist:
                messages.error(request, 'Invalid staff file number.')
    else:
        form = StaffFileActivationForm()

    context = {
        'form': form,
        'page_title': 'Staff File Activation',
    }
    return render(request, 'academics/file_validation.html', context)

# Validate Applicant's Email View
@transaction.atomic
def applicant_email_validation(request):
    if request.method == 'POST':
        form = ApplicantValidationForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']

            # Retrieve the user instance with the provided email
            user_instance = User.objects.filter(email=email).first()

            if user_instance:
                # User already exists, handle accordingly
                messages.error(request, f'{email} Already taken. Try another one.')
                return redirect('validate_applicant')
            else:
                try:
                    with transaction.atomic():
                        user_form = form.save()
                        # Create a new user instance with a default password
                        user_instance = User.objects.create_user(username=email, email=email, password='ecosaht')

                        # Check for Existance of User and Update the user's profile role to 'Applicant'
                        try:
                            applicant_validation = User.objects.get(email=email)
                        except User.DoesNotExist:
                            # Handle the case where there is no associated user instance
                            messages.error(request, 'User not found.')
                            return redirect('validate_applicant')

                        # Set the user profile role to 'Applicant'
                        profile, created = Profile.objects.get_or_create(user=user_instance)
                        profile.role = 'Applicant'
                        profile.save()

                        # Associate the User instance with the ApplicantValidation instance
                        user_form.applicant = user_instance
                        user_form.save()

                        # Generate token for the user
                        token = default_token_generator.make_token(user_instance)

                        # Build the activation link
                        uid = urlsafe_base64_encode(force_bytes(user_form.pk))
                        activation_path = reverse(
                            'email_validation_confirmed',
                            kwargs={'uidb64': uid, 'token': token},
                        )
                        activation_link = request.build_absolute_uri(activation_path)

                        # Send activation email
                        # Instantiate the client with the API KEY
                        configuration = sib_api_v3_sdk.Configuration()
                        configuration.api_key['api-key']=config('API_KEY')
                        api_instance = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))
                        # Define the campaign settings\
                        subject = "Email Validation."
                                
                        html_content =f"""
                                    <html>
                                    <body>
                                    <h3>Welcome to Excellent College of Sciences and Health Technology. Gboko. Benue State.</h3>
                                    <p>You are required to activate your account on the Polytechnic Portal
                                      in order to continue with your Application Process.
                                    </p>
                                    <p>
                                    Your Password is <b>ecosaht</b> and your username is your email address. Remember to change your 
                                    default password upon account activation for security reasons.
                                    </p>
                                
                                    <p>Click or copy and paste the link in a new browser tab to activate your account: {activation_link}</p>
                                        <p> The registered email is <b>{email}</b> </p>            
                                                    
                                                    <p> <b> Excellent College of Sciences & Health Technology. Gboko, ICT Team.
                                                    </b> 
                                                    </p>
                                                    <p> <b>Contact 0911 513 3596, 07039031182, 0708 848 4669  </b> </p>
                                                </body>
                                        </html>
                                        """
                                
                        sender = {"name": "Excellent College of Sciences and Health Technology. Gboko.", "email": "inf@ecosaht.com"}
                        to = [{"email": email, "name": 'Applicant'}]
                        cc = [{"email": "engramah2023@gmail.com", "name":"Amah Gideon"}]
                        bcc = [{"name": "Igbeh Gandepuun", "email": "nabem.jude@gmail.com"}]
                        reply_to = {"email": "info@ecosaht.com", "name": "Amah Gideon"}
                        headers = {"Some-Custom-Name": "unique-id-1234"}
                        params = {"parameter": "My param value", "subject": "New Subject"}
                        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(to=to, bcc=bcc, cc=cc, reply_to=reply_to, headers=headers, html_content=html_content, sender=sender, subject=subject) 

                        try:
                            api_response = api_instance.send_transac_email(send_smtp_email)
                            print(api_response)
                        except ApiException:
                            print("Something went wrong while trying to send emil")
                        messages.success(request, f'Check {email} for Validation.')
                        return redirect('validation_email_sent')
                    
                except Exception:
                    # Handle any exceptions that might occur during the transaction
                    messages.error(request, 'Something went wrong. Transaction Fail.')
                    return redirect('validate_applicant')    

    else:
        form = ApplicantValidationForm()

    context = {
        'form': form,
        'page_title': 'Applicant Email Validation',
    }
    return render(request, 'admission/applicant_validation.html', context)


# Validate Applicant's Email View
@transaction.atomic
def staff_email_validation(request):
    if request.method == 'POST':
        form = ApplicantValidationForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']

            # Retrieve the user instance with the provided email
            user_instance = User.objects.filter(email=email).first()

            if user_instance:
                # User already exists, handle accordingly
                messages.error(request, f'{email} Already used. ')
                return redirect('staff_email_validation')
            else:
                try:
                    with transaction.atomic():
                        user_form = form.save()
                        # Create a new user instance with a default password
                        user_instance = User.objects.create_user(username=email, email=email, password='password')

                        # Check for Existance of User and Update the user's profile role to 'Applicant'
                        try:
                            applicant_validation = User.objects.get(email=email)
                        except User.DoesNotExist:
                            # Handle the case where there is no associated user instance
                            messages.error(request, 'User not found.')
                            return redirect('validate_applicant')

                        # Set the user profile role to 'Applicant'
                        profile, created = Profile.objects.get_or_create(user=user_instance)
                        profile.role = 'Staff'
                        profile.save()

                        # Associate the User instance with the ApplicantValidation instance
                        user_form.applicant = user_instance
                        user_form.save()

                        # Generate token for the user
                        token = default_token_generator.make_token(user_instance)

                        # Build the activation link
                        uid = urlsafe_base64_encode(force_bytes(user_form.pk))
                        activation_link = f'http://127.0.0.1:8000/activate/{uid}/{token}/'

                        # Send activation email
                        # Instantiate the client with the API KEY
                        configuration = sib_api_v3_sdk.Configuration()
                        configuration.api_key['api-key']=config('API_KEY')
                        api_instance = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))
                        # Define the campaign settings\
                        subject = "Email Validation."
                                
                        html_content =f"""
                                    <html>
                                    <body>
                                    <h3>Welcome to Excellent College of Sciences and Health Technology. Gboko.</h3>
                                    <p>You will have to verify your email in order to continue with your Application Process.</p>
                                
                                    <p>Click or copy and paste the link in a new browser tab to activate your account: {activation_link}</p>
                                        <p>The Registered email is {email} </p>             
                                                    
                                                    <p>Excellent College of Sciences and Health Technology. Gboko, ICT Team.</p>
                                                </body>
                                        </html>
                                        """
                                
                        sender = {"name": "Excellent College of Sciences and Health Technology. Gboko.", "email": "applications@fedpolywannune.edu.ng"}
                        to = [{"email": email, "name": 'Applicant'}]
                        cc = [{"email": "engramah2023@gmail.com", "name":"Amah Gideon"}]
                        bcc = [{"name": "Apollos Nabem", "email": "nabem.jude@gmail.com"}]
                        reply_to = {"email": "info@ecosaht.com", "name": "Amah Gideon"}
                        headers = {"Some-Custom-Name": "unique-id-1234"}
                        params = {"parameter": "My param value", "subject": "New Subject"}
                        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(to=to, bcc=bcc, cc=cc, reply_to=reply_to, headers=headers, html_content=html_content, sender=sender, subject=subject) 

                        try:
                            api_response = api_instance.send_transac_email(send_smtp_email)
                            print(api_response)
                        except ApiException:
                            print("Something Went Wrong While Trying to Send Email")
                        return redirect('staff_email_validation_sent')
                    
                except Exception:
                    # Handle any exceptions that might occur during the transaction
                    messages.error(request, 'Something Went Wrong. Check your Internet Connection and try again.}')
                    return redirect('validate_applicant')    

    else:
        form = ApplicantValidationForm()

    context = {
        'form': form,
        'page_title': 'Staff File Activation',
    }
    return render(request, 'admission/staff_validation.html', context)


# Staff Email Validation link successfully sent view
def staff_email_validation_sent(request):
    context = {
        'page_title': 'Staff Validation Sent',
    }
    return render(request, 'admission/staff_validation_sent.html', context)


# Applicant's Email Validation link successfully sent view
def email_validation_sent(request):
    context = {
        'page_title': 'Validation Sent',
    }
    return render(request, 'admission/applicant_validation.html', context)


# Applicant Email Validation Confirm view
def confirm_email(request, uidb64, token):
    # Decode the uid to get the user instance
    try:
        uid = int(force_str(urlsafe_base64_decode(uidb64)))
        # Compare the decoded uid with the product key of the user in the
        # applicantvalidation table
        user_instance = get_object_or_404(ApplicantValidation, pk=uid)
        # If there is no Match then its None
    except (TypeError, ValueError, OverflowError, ApplicantValidation.DoesNotExist):
        user_instance = None
        messages.error(request, 'Something went wrong')
        return redirect('validate_applicant')
    # If there is a Match and the user token are ok
    if not user_instance.is_validated and default_token_generator.check_token(user_instance.applicant, token):
        # Validate Applicant's email and save
        user_instance.is_validated = True
        user_instance.save()
        # Log in the user after successful email confirmation
        login(request, user_instance.applicant)
        # Send Success Message and redirect to fill pre-application form
        messages.success(request, 'Email Verified Successfully: Your password: password')
        return redirect('create_application')
    elif user_instance.is_validated:
        # Handle the case where the link is invalid
        messages.warning(request, f'{user_instance.applicant} Already Activated.')
        return redirect('accounts_dashboard')
    else:
        messages.warning(request, 'Something Went Wrong during Activation.')
        return redirect('accounts_login')


# Staff Email Validation Confirm view
def staff_confirm_email(request, uidb64, token):
    # Decode the uid to get the user instance
    try:
        uid = int(force_str(urlsafe_base64_decode(uidb64)))
        # Compare the decoded uid with the product key of the user in the
        # applicantvalidation table
        user_instance = get_object_or_404(ApplicantValidation, pk=uid)
        # If there is no Match then its None
    except (TypeError, ValueError, OverflowError, ApplicantValidation.DoesNotExist):
        user_instance = None
        messages.error(request, 'Something went wrong')
        return redirect('staff_email_validation')
    # If there is a Match and the user token are ok
    if user_instance is not None and default_token_generator.check_token(user_instance.applicant, token):
        # Log in the user after successful email confirmation
        login(request, user_instance.applicant)
        # Send Success Message and redirect to fill pre-application form
        messages.success(request, f'Email Verified Successfully: Your password: password')
        return redirect('staff_register')

    # Handle the case where the link is invalid
    messages.warning(request, f'{user_instance.applicant} Already Activated.')
    return redirect('accounts_login')


# Staff Registration
@login_required(login_url='accounts_login')
def staff_register(request):
    logged_user = request.user
    profile = logged_user.profile

    if not logged_user.profile.role == 'Staff':
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    # Log user activity
    UserActivityLog.objects.create(user=logged_user, action='Visited Staff Registration Page')
    
    # Check if staff record in vailidation table and 
    # and if he/she has not made any payment before giving access
    if ApplicantValidation.objects.filter(applicant=logged_user).exists() \
            and not staffAppSubmission.objects.filter(staff_user=logged_user, confirm=True).exists():
        if request.method == 'POST':
            form = StaffRegistrationForm(request.POST, request.FILES)
            if form.is_valid():
                try:
                    with transaction.atomic():
                       
                        # Capture user form input values
                        department = form.cleaned_data['department']
                        first_name = form.cleaned_data['first_name']
                        middle_name = form.cleaned_data['middle_name']
                        last_name = form.cleaned_data['last_name']
                        date_birth = form.cleaned_data['date_birth']
                        phone_number = form.cleaned_data['phone_number']
                        gender = form.cleaned_data['gender']
                        email = form.cleaned_data['email']
                        marital_status = form.cleaned_data['marital_status']
                        Number_children = form.cleaned_data['Number_children']
                        home_address = form.cleaned_data['home_address']
                        religion = form.cleaned_data['religion']
                        nationality = form.cleaned_data['nationality']
                        state = form.cleaned_data['state']
                        lga = form.cleaned_data['lga']
                        Pay_grade = form.cleaned_data['Pay_grade']
                        dept_type = form.cleaned_data['dept_type']
                        file_number = form.cleaned_data['file_number']
                        nin_number = form.cleaned_data['nin_number']
                        Appointment_type = form.cleaned_data['Appointment_type']
                        Appointment_Mode = form.cleaned_data['Appointment_Mode']
                        Promotion_status = form.cleaned_data['Promotion_status']
                        First_appointment = form.cleaned_data['First_appointment']
                        image = form.cleaned_data['image']

                        try:
                            check_file_number = StaffFile.objects.get(file_number=file_number, is_activated=True)
                            
                            # Update the user profile with data from the form
                            profile.first_name = first_name
                            profile.middle_name = middle_name
                            profile.last_name = last_name
                            profile.date_birth = date_birth
                            profile.phone_number = phone_number
                            profile.gender = gender
                            profile.marital_status = marital_status
                            profile.Number_children = Number_children
                            profile.home_address = home_address
                            profile.religion = religion
                            profile.nationality = nationality
                            profile.state = state
                            profile.lga = lga
                            profile.image = image
                            # Save the updated profile
                            profile.save()
                            # Update Staff file Instance
                            check_file_number.is_activated = True
                            check_file_number.user = logged_user
                            check_file_number.save()
                            # Create Staff Register
                            registered_staff = StaffRegister.objects.create(
                                user=logged_user, first_name=first_name, middle_name=middle_name,
                                last_name=last_name, date_birth=date_birth, phone_number=phone_number,
                                gender=gender, marital_status=marital_status, Number_children=Number_children,
                                home_address=home_address, religion=religion, nationality=nationality,
                                state=state, lga=lga, email=email, Pay_grade=Pay_grade, file_number=file_number,
                                nin_number=nin_number, department=department, dept_type=dept_type,
                                Appointment_type=Appointment_type, Appointment_Mode=Appointment_Mode,
                                Promotion_status=Promotion_status, First_appointment=First_appointment,
                                image=image
                            )
                            if registered_staff:
                                # Log user activity
                                UserActivityLog.objects.create(user=logged_user, action=f'Register Self as Staff with following details, Names; {logged_user.profile.first_name} {logged_user.profile.last_name} ')
                                messages.success(request, f'{logged_user.profile.first_name }Information Saved Successfully.')
                                return redirect('staff_publication')
                            else:
                                messages.warning(request, 'Information was NOT Registered')
                        except StaffFile.DoesNotExist:
                            messages.warning(request, 'File Number Not Found')
                except Exception as e:
                    messages.error(request, f'An error occurred: {str(e)}')
        else:
            form = StaffRegistrationForm()

        context = {
            'form': form,
            'page_title': 'Staff Registration',
        }
        return render(request, 'admission/staff_register.html', context)
    else:
        return redirect('accounts_dashboard')
    
# Staff update employment information
@login_required(login_url='accounts_login')
def update_staff_register(request):
    logged_user = request.user

    if not logged_user.profile.role == 'Staff':
        return redirect('accounts_login')
    
    # Log user activity
    UserActivityLog.objects.create(user=logged_user, action='Visited Update Staff Registration Page')
    try:
        staff_registration_instance = StaffRegister.objects.get(user=logged_user)
        
        if request.method == 'POST':
            form = UpdateStaffRegistration(request.POST, request.FILES, instance=staff_registration_instance)
            if form.is_valid():
                form.save()
                if not staffAppSubmission.objects.select_related('staff_user').filter(staff_user=logged_user, confirm=True):
                    messages.success(request, 'Employment Record Updated Successfully.')
                    return redirect('staff_reg_preview')
                else:
                    # Log user activity
                    UserActivityLog.objects.create(user=logged_user, action=f' Updated Self Staff Registration Details')
                    messages.success(request, 'Employment Record Updated Successfully.')
                    return redirect('staff_dashboard')
        else:
            form = UpdateStaffRegistration(instance=staff_registration_instance)
        context = {
            'page_title': 'Update Employment Record',
            'form': form,
        }
        return render(request, 'admission/staff_register.html', context)

    except StaffRegister.DoesNotExist:
        messages.warning(request, 'Staff Not Found')
        return redirect('accounts_login')



# Preview and Submit Staff Registration Form view
@login_required(login_url='accounts_login')
@transaction.atomic
def staff_reg_preview(request):
    logged_user = request.user
    
    if not logged_user.profile.role == 'Staff':
        return redirect('accounts_login')
    # Log user activity
    UserActivityLog.objects.create(user=logged_user, action='Previewed Self Staff Registration.')
    try:
        staff_registration_instance = StaffRegister.objects.get(user=logged_user)
        staff_department = staff_registration_instance.department
        dept_type = staff_registration_instance.dept_type

        # Get the staff Academic Instance
        try:
            academic_staff = AcademicStaff.objects.select_related('staff').get(staff=staff_registration_instance)
            staff_publication = StaffPublication.objects.select_related('staff').filter(staff=staff_registration_instance).order_by('-submission_date')
      
            if request.method == 'POST':
                # If the form is submitted, process the form data
                form = StaffRegSubmissionForm(request.POST)
                    
                if form.is_valid():
                    # Create a new staff and Submit the Application with 
                    # Academic Session, and Program/Course
                    new_application = form.save(commit=False)
                    # new_application.student_id = applicant_id
                    new_application.staff_user = request.user
                    new_application.department = staff_department
                    new_application.confirm = True 
                    new_application.save()
                    # Redirect to a success page or render a template
                    # Log user activity
                    UserActivityLog.objects.create(user=logged_user, action='Submitted Self Staff Registration Form')
                    messages.success(request, 'Registration Submitted Successfully.')
                    return redirect('staff_registration_slip')
            else:
                form = StaffRegSubmissionForm()
            context = {
                'academic_staff': academic_staff,
                'dept_type': dept_type,
                'staff_publication': staff_publication,
                'form': form,
                'page_title': 'Staff Registration Preview',
            }
            return render(request, 'admission/staff_registration_preview.html', context)
        except AcademicStaff.DoesNotExist:
            messages.warning(request, 'Staff Record Does Not Exist')

    except StaffRegister.DoesNotExist:
        messages.warning(request, 'Staff Not Found')
        return redirect('accounts_login')


# Staff Registration Slip View
@login_required(login_url='accounts_login')
@transaction.atomic
def staff_registration_slip(request):
    logged_user = request.user

    if not logged_user.profile.role == 'Staff':
        messages.warning(request, 'You are not a staff')
        return redirect('accounts_login')
    
    # Log user activity
    UserActivityLog.objects.create(user=logged_user, action='Viewed Staff Registration Slip.')
    # Check if staff has registered and submitted Registration
    try:
        staff_registration_instance = StaffRegister.objects.select_related('user').get(user=logged_user)    
        # Check Staff Academic Record
        try:
            academic_staff = AcademicStaff.objects.select_related('staff').get(staff=staff_registration_instance)
            try:
                check_submission = staffAppSubmission.objects.select_related('staff_user').get(staff_user=staff_registration_instance.user) 
            except staffAppSubmission.DoesNotExist:
                messages.warning(request, 'You have not submitted Registration yet.')
                return redirect('staff_reg_preview')   
            else:
                staff_publication = StaffPublication.objects.select_related('staff').filter(staff=staff_registration_instance).order_by('-submission_date')
                context = {
                    'academic_staff': academic_staff,
                    'staff_registration_instance': staff_registration_instance,
                    'staff_publication': staff_publication,
                    'page_title': 'Staff Registration Slip',
                }
                return render(request, 'admission/staff_registration_slip.html', context)
        except AcademicStaff.DoesNotExist:
            messages.warning(request, 'Academic Record NOT Found')
            return redirect('staff_academic_staff_record')
        
    except StaffRegister.DoesNotExist:
        messages.warning(request, 'You have NOT a Registered Staff Yet')
        return redirect('staff_register')


# Admin User Register Staff view
@login_required(login_url='accounts_login')
def special_staff_register(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'Super Admin' or logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.is_superuser):
        messages.success(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    # Log user activity
    UserActivityLog.objects.create(user=logged_user, action='Visited Staff Registration Page')
    
    if request.method == 'POST':
        staff_reg = StaffUserCreation(request.POST)
        form = StaffRegistrationForm(request.POST, request.FILES)
        if form.is_valid() and staff_reg.is_valid():
            try:
                with transaction.atomic():
                    # Get Form Variables
                    # Capture user form input values
                    
                    first_name = form.cleaned_data['first_name']
                    middle_name = form.cleaned_data['middle_name']
                    last_name = form.cleaned_data['last_name']
                    date_birth = form.cleaned_data['date_birth']
                    phone_number = form.cleaned_data['phone_number']
                    gender = form.cleaned_data['gender']
                    marital_status = form.cleaned_data['marital_status']
                    Number_children = form.cleaned_data['Number_children']
                    home_address = form.cleaned_data['home_address']
                    religion = form.cleaned_data['religion']
                    nationality = form.cleaned_data['nationality']
                    state = form.cleaned_data['state']
                    lga = form.cleaned_data['lga']
                    email = form.cleaned_data['email']
                    Pay_grade = form.cleaned_data['Pay_grade']
                    department = form.cleaned_data['department']
                    directorate = form.cleaned_data['directorate']
                    dept_type = form.cleaned_data['dept_type']
                    file_number = form.cleaned_data['file_number']
                    nin_number = form.cleaned_data['nin_number']
                    Appointment_type = form.cleaned_data['Appointment_type']
                    Appointment_Mode = form.cleaned_data['Appointment_Mode']
                    Promotion_status = form.cleaned_data['Promotion_status']
                    First_appointment = form.cleaned_data['First_appointment']
                    image = form.cleaned_data['image']
                    # Save Staff Reg Form
                    user = staff_reg.save()
                    
                    profile = user.profile
                    profile.first_name = first_name
                    profile.middle_name = middle_name
                    profile.last_name = last_name
                    profile.date_birth = date_birth
                    profile.phone_number = phone_number
                    profile.gender = gender
                    profile.marital_status = marital_status
                    profile.Number_children = Number_children
                    profile.home_address = home_address
                    profile.religion = religion
                    profile.nationality = nationality
                    profile.state = state
                    profile.lga = lga
                    profile.role = 'Staff'
                    profile.image = image
                    profile.save()

                    # Check File Number and Update Accordingly
                    try:
                        check_file_no = StaffFile.objects.get(file_number=file_number)
                        if check_file_no.is_activated:
                            # Raise an exception to rollback the transaction
                            raise Exception(f'{file_number}  File number already activated')
                        else:
                            check_file_no.is_activated = True
                            check_file_no.user = user
                            check_file_no.save()
                            # Create Staff Register
                            registered_staff = StaffRegister.objects.create(
                                user=user, first_name=first_name, middle_name=middle_name,
                                last_name=last_name, date_birth=date_birth, phone_number=phone_number,
                                gender=gender, marital_status=marital_status, Number_children=Number_children,
                                home_address=home_address, religion=religion, nationality=nationality,
                                state=state, lga=lga, email=email, Pay_grade=Pay_grade, file_number=file_number,
                                nin_number=nin_number, department=department, directorate=directorate, dept_type=dept_type,
                                Appointment_type=Appointment_type, Appointment_Mode=Appointment_Mode,
                                Promotion_status=Promotion_status, First_appointment=First_appointment,
                                registered_by=logged_user, image=image
                            )
                            if registered_staff:
                                # Register user activity
                                UserActivityLog.objects.create(user=logged_user, action=f'Register Staff with the following details; Username {user.username}, Names: {first_name} {last_name} ')
                                messages.success(request, 'Staff Information Saved Successfully.')
                                return redirect('superadmin_add_staff_publication', staff_id=user.id)
                            else:
                                # Register user activity
                                UserActivityLog.objects.create(user=logged_user, action=f'Could not Register Staff with the following details; Username {user.username}, Names: {first_name} {last_name} ')
                                messages.warning(request, 'Staff was NOT Registered')
                        
                    except StaffFile.DoesNotExist:
                        messages.warning(request, f'{file_number} does Not Exist')

            except Exception as e:
                messages.error(request, f'An error occurred: {str(e)}')
    else:
        staff_reg = StaffUserCreation()
        form = StaffRegistrationForm()
    context = {
        'staff_reg': staff_reg,
        'form': form,
        'page_title': 'Online Staff Registration',
    }

    return render(request, 'admission/super_admin_staff_register.html', context)
            

# Create Pre-Application View
@login_required(login_url='accounts_login')
def create_applicant(request):
    # Check for existence of Course of study
    application_fee = ApplicationFee.objects.exists()
    programme_existence  = CourseOfStudy.objects.exists()
    logged_user = request.user
    profile = logged_user.profile

    if not logged_user.profile.role == 'Applicant':
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    # Check if applicant's record in vailidation table and 
    # and if he/she has not made any payment before giving access
    if ApplicantValidation.objects.filter(applicant=logged_user, is_validated=True).exists():
        if not PreApplicant.objects.filter(applicant=logged_user).exists() \
                and not has_verified_application_fee(logged_user):
            if request.method == 'POST':
                form = PreApplicationForm(request.POST)
                if form.is_valid():
                    try:
                        with transaction.atomic():
                            # Get the latest academic session
                            selected_session = form.cleaned_data['academic_session']
                            # Save the form data to PreApplicant model
                            pre_applicant = form.save(commit=False)
                            pre_applicant.applicant = logged_user
                                
                            # Generate and save an application number
                            application_number = str(uuid.uuid4())[:6].upper()  
                                
                            # Get the current date in the format YYYYMMDD
                            current_date = datetime.now().strftime("%Y%m%d")
                                
                            # Assign the combined Application Number to the DB field
                            pre_applicant.application_number = f"{current_date}{application_number}"

                            # Get current timestamp as a string
                            timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")

                            # Generate a unique identifier using a secure hashing algorithm
                            unique_identifier = hashlib.sha256(timestamp_str.encode('utf-8')).hexdigest()

                            # Combine the timestamp and unique identifier to form the invoice number
                            invoice_number = f"ECOSHAT_GBOKO-{unique_identifier[:6]}-{timestamp_str}"

                            # Update the invoice number of the PreApplication Model
                            pre_applicant.invoice_number = invoice_number

                            # Save the PreApplicant instance
                            pre_applicant.save()

                            # Update the user profile with data from the form
                            profile.first_name = form.cleaned_data['first_name']
                            profile.middle_name = form.cleaned_data['middle_name'] 
                            profile.last_name = form.cleaned_data['last_name']
                            profile.phone_number = form.cleaned_data['phone_number']
                            # Get form variables
                            first_name = form.cleaned_data['first_name']
                            middle_name = form.cleaned_data['middle_name'] 
                            last_name = form.cleaned_data['last_name']
                            phone_number = form.cleaned_data['phone_number']
                            course_name = form.cleaned_data['course_name']
                                
                            # Save the updated profile
                            profile.save()

                                

                            # Send activation email
                            # Instantiate the client with the API KEY
                            configuration = sib_api_v3_sdk.Configuration()
                            configuration.api_key['api-key']=config('API_KEY')
                            api_instance = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))
                            # Define the campaign settings\
                            subject = "Pre-Application Submission."
                                        
                            html_content =f"""
                                        <html>
                                        <body>
                                        <h3>Pre-Application Form Submission.</h3>
                                        <p>Thank you for successfully submitting your Pre-Application Form for admission 
                                        into The <b>Excellent College of Sciences and Health Technology. Gboko.</b>,
                                        with the following information.
                                        </p>
                                        
                                        <p>Name: <b>{first_name} {middle_name} {last_name}</b> </p>   
                                        <p>Phone Number: <b>{phone_number}</b> </p>  
                                        <p>Email Address: <b>{logged_user.email}</b> </p> 
                                        <p>Programme of Choice: <b>{course_name}</b> </p>           
                                        <p>Academic Session: <b>{selected_session}</b> </p>                 
                                        <p>You are encouraged to contact The 
                                        <b> Excellent College of Sciences and Health Technology. Gboko, ICT Team, </b>
                                        on the following contacts to ease your application process.  
                                        <b> 0911 513 3596, 07039031182, 0708 848 4669 </b>
                                        </p>
                                        </body>
                                        </html>
                                            """
                                        
                            sender = {"name": "Excellent College of Sciences and Health Technology. Gboko.", "email": "info@ecosaht.com"}
                            to = [{"email": logged_user.email, "name": f"{first_name} {last_name}"}]
                            cc = [{"email": "gamahlife@gmail.com", "name":"Amah Gideon"}]
                            bcc = [{"name": "Igbeh Gandepuun", "email": "nabem.jude@gmail.com"}]
                            reply_to = {"email": "info@ecosaht.com", "name": "Igbeh Gande"}
                            headers = {"Some-Custom-Name": "unique-id-1234"}
                            params = {"parameter": "My param value", "subject": "New Subject"}
                            send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(to=to, bcc=bcc, cc=cc, reply_to=reply_to, headers=headers, html_content=html_content, sender=sender, subject=subject) 

                            try:
                                api_response = api_instance.send_transac_email(send_smtp_email)
                                print(api_response)
                            except ApiException:
                                print("Something went wrong while trying to send email")

                            messages.success(request, 'Pre-Application  Form for Admission created Successfully. Proceed for Payment or call  08036588550, 08057510333, 08037248960 for assistance.')
                                
                            return redirect('pre_app_print_out', user_id=pre_applicant.applicant.id)
                            
                    except Exception:
                        messages.error(request, 'An error occurred: Pre-Application Form Transaction Fail.')
            else:
                form = PreApplicationForm()

            context = {
                'application_fee': application_fee, 
                'programme': programme_existence,
                'form': form,
                'page_title': 'Pre-Application',
            }

            return render(request, 'admission/pre_application.html', context)
        else:
            return redirect('accounts_dashboard')
    else:
        messages.error(request, 'Error : Contact ICT Team to Unblock Email.')
        return redirect('accounts_login')


@login_required(login_url='accounts_login')
def pre_application_print_out(request, user_id):
    try:
        pre_applicant = PreApplicant.objects.get(applicant=user_id)
        applicant = pre_applicant.applicant
        # Get Application Form Fee Amount based on Course of Study
        course_of_study = pre_applicant.course_name
    except PreApplicant.DoesNotExist:
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')
    else:
        latest_session = pre_applicant.academic_session
        latest_session_name = latest_session.name if latest_session else ''
        # Get Application Form Fee Amount
        payment_source = None
        fee_amount = None
        fee_category = get_application_fee_category(course_of_study, latest_session)
        if not fee_category:
            messages.error(request, 'The selected programme does not have a configured application fee category for this academic session. Please contact the Admissions Office.')
            return redirect('accounts_dashboard')
        
        # If Applicant Course of Study exists, get his/her Required Fee for payment
        application_fee = ApplicationFee.objects.filter(
            academic_session=latest_session,
            category=fee_category,
            name='Application Form',
        ).first()
        if not application_fee:
            messages.error(request, f'No Application Form fee is configured for {fee_category} in {latest_session}.')
            return redirect('accounts_dashboard')
        application_fee_amount = application_fee.amount
        
        context = {
            'latest_session_name': latest_session_name,
            'application_fee_amount': application_fee_amount,
            'pre_applicant': applicant,
            'page_title': 'Pre-Application Slip', 
        }
        return render(request, 'admission/pre_application_print_out.html', context)


@login_required(login_url='accounts_login')
def returning_applicant(request):
    logged_user = request.user
    if logged_user.profile.role != 'Applicant':
        messages.warning(request, 'Something Went Wrong with Login')
        return redirect('accounts_login')
    
    try:
        pre_applicant = PreApplicant.objects.get(applicant=logged_user)
        # Get Application Form Fee Amount based on Course of Study
        course_of_study = pre_applicant.course_name
    except PreApplicant.DoesNotExist:
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')
    else:
        latest_session = pre_applicant.academic_session
        latest_session_name = latest_session.name if latest_session else ''
        
        fee_category = get_application_fee_category(course_of_study, latest_session)
        if not fee_category:
            messages.error(request, 'The selected programme does not have a configured application fee category for this academic session. Please contact the Admissions Office.')
            return redirect('accounts_dashboard')
        
        # If Applicant Course of Study exists, get his/her Required Fee for payment
        application_fee = ApplicationFee.objects.filter(
            academic_session=latest_session,
            category=fee_category,
            name='Application Form',
        ).first()
        if not application_fee:
            messages.error(request, f'No Application Form fee is configured for {fee_category} in {latest_session}.')
            return redirect('accounts_dashboard')
        application_fee_amount = application_fee.amount
        # GET CANDIDATE PAYMENT
        payments = Payment.objects.filter(
            user=logged_user,
            verified=True,
            status='Approved',
            payment_source__in=['Application Form', 'Acceptance Fee', 'Caution Fee', 'Screening Fee', 'School Fees'],
            academic_sess=latest_session
        )
        has_application_form_payment = payments.filter(payment_source='Application Form').exists()
        has_acceptance_fee_payment = payments.filter(payment_source='Acceptance Fee').exists()
        has_screening_fee_payment = payments.filter(payment_source='Screening Fee').exists()
        has_caution_fee_payment = payments.filter(payment_source='Caution Fee').exists()
        has_school_fees_payment = payments.filter(payment_source='School Fees').exists()
        if has_application_form_payment:
            return redirect('accounts_profile')
        elif not has_application_form_payment:
            context = {
                'has_application_form_payment': has_application_form_payment,
                'has_acceptance_fee_payment': has_acceptance_fee_payment,
                'has_screening_fee_payment': has_screening_fee_payment,
                'has_caution_fee_payment': has_caution_fee_payment,
                'has_school_fees_payment': has_school_fees_payment,
                'application_fee_amount': application_fee_amount,
                'pre_applicant': logged_user,
                'page_title': 'Slip',
                'latest_session_name': latest_session_name,
            }
            return render(request, 'admission/pre_application_print_out.html', context)
        else:
            return redirect('accounts_login')


# Applicant Contact view
@login_required(login_url='accounts_login')
def Applicant_contact(request):
    # Grab the Logged in user
    logged_user = request.user

    # Check if the user has a profile
    try:
        profile = logged_user.profile
    except Profile.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'User profile not found.')
        return redirect('accounts_login')

    # Check if he/she is an applicant and redirect appropriately
    if not (profile.role == 'Applicant' or profile.role == 'Candidate'):
        messages.warning(request, 'Only applicants and candidates can access the applicant contact form.')
        return redirect('accounts_login')

    payment_redirect = _redirect_if_application_fee_unverified(request)
    if payment_redirect:
        return payment_redirect

    if not application_form_profile_complete(profile):
        messages.warning(request, 'Please complete your application form profile before adding contact details.')
        return redirect('edit_application_form')
    
    # Check if the Contact Information is complete
    if profile_contact_complete(profile):
        messages.info(request, 'Your contact details are already complete. Please continue with sponsor details.')
        return redirect('applicant_sponsor_contact')

    # Check if the form request method is POST
    if request.method == 'POST':
        # Get the form and assign it to the logged-in user profile
        form = ApplicantContactForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            # Update profile fields directly using form.cleaned_data
            profile.home_address = form.cleaned_data['home_address']
            profile.state = form.cleaned_data['state']
            profile.nationality = form.cleaned_data['nationality'] 
            profile.lga = form.cleaned_data['lga']

            # Save the changes
            profile.save()
            
            # Display success message
            messages.success(request, 'Contact details saved successfully.')
            return redirect('applicant_sponsor_contact')
        messages.error(request, 'Please correct the highlighted contact details and try again.')

    else:
        form = ApplicantContactForm(instance=profile) if profile.state else ApplicantContactForm()

    context = {
        'form': form,
        'page_title': 'Applicant Contact',
    }
    return render(request, 'admission/applicant_contact.html', context)


# View for JSON response of corresponding state and lga
def get_lgas(request):
    state_id = request.GET.get('state_id')
    lgas = LocalGovernment.objects.filter(state_id=state_id).values('id', 'name')
    return JsonResponse(list(lgas), safe=False)


# Applicant Sponsor's Contact
@login_required(login_url='accounts_login')
def applicant_sponsor(request):
    logged_user = request.user

    if not (logged_user.profile.role == 'Applicant' or logged_user.profile.role == 'Candidate'):
        return redirect('accounts_login')

    payment_redirect = _redirect_if_application_fee_unverified(request)
    if payment_redirect:
        return payment_redirect
    
    # Check if the user already has SponsorDetails
    sponsor_details = get_sponsor_details(logged_user)
    if sponsor_details:
        messages.success(request, 'You have already added sponsor details.')
        return redirect('applicant_education')
    
    form = SponsorDetailsForm(request.POST or None)

    if request.method == 'POST':
        if form.is_valid():
            with transaction.atomic():
                full_name = form.cleaned_data['full_name']
                occupation = form.cleaned_data['occupation']
                phone_number = form.cleaned_data['phone_number']
                email = form.cleaned_data['email']
                home_address = form.cleaned_data['home_address']
                sponsor_relationship = form.cleaned_data['sponsor_relationship']

                # Create Applicant's Sponsor instance
                sponsor = SponsorDetails.objects.create(
                    applicant=logged_user,
                    full_name=full_name,
                    occupation=occupation,
                    phone_number=phone_number,
                    email=email,
                    home_address=home_address,
                    sponsor_relationship=sponsor_relationship
                )

            if sponsor:
                messages.success(request, 'Sponsor Information Added Successfully.')
                return redirect('applicant_education')
            else:
                messages.warning(request, 'No Sponsor Information Added.')
    else:
        form = SponsorDetailsForm()
    context = {
        'page_title': 'Cadidate\'s Sponsor',
        'form': form,  # Include the form in the context for rendering in the template
    }

    return render(request, 'admission/applicant_sponsor.html', context)


# Applicant Education Record
@login_required(login_url='accounts_login')
def applicant_education(request):
    logged_user = request.user

    if not (logged_user.profile.role == 'Applicant' or logged_user.profile.role == 'Candidate'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    payment_redirect = _redirect_if_application_fee_unverified(request)
    if payment_redirect:
        return payment_redirect
    
    # Get the user's education records
    education_records = get_education(logged_user)

    # Check if the user has added at least 2 education records
    # if education_records and education_records.count() >= 2:
    #     messages.success(request, 'You have already added the required number of education records.')
    #     return redirect('applicant_result')  
    
    schools = EducationRecord.objects.select_related('applicant').filter(applicant=logged_user).order_by('-added_date')
    form = EducationRecordForm(request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            with transaction.atomic():
                school_name = form.cleaned_data['school_name']
                certificate = form.cleaned_data['certificate']
                # Convert admitted_year and end_year to integers
                admitted_year = int(form.cleaned_data['admitted_year'])
                end_year = int(form.cleaned_data['end_year'])
                
                education = EducationRecord.objects.create(
                    applicant=logged_user,
                    school_name=school_name,
                    certificate=certificate,
                    admitted_year=admitted_year, end_year=end_year)
                if education:
                    messages.success(request, 'Record Added Successfully.')
    else:
        form = EducationRecordForm()
    context = {
        'schools': schools,
        'form': form,
        'page_title': 'Candidate Education Record',
    }
    return render(request, 'admission/applicant_education.html', context)


# Applicant Result View
@login_required(login_url='accounts_login')
def applicant_result(request):
    logged_user = request.user

    if not (logged_user.profile.role == 'Applicant' or logged_user.profile.role == 'Candidate'):
        messages.success(request, 'Something Went Wrong.')
        return redirect('accounts_login')

    payment_redirect = _redirect_if_application_fee_unverified(request)
    if payment_redirect:
        return payment_redirect
    
    # Get the user's result records
    result_records = get_result(logged_user)

    # Check if the user has added at least 5 result records
    if result_records and result_records.count() >= 10:
        if logged_user.profile.role == 'Applicant':
            messages.info(request, 'You have already added the required number of result records.')
            return redirect('application_preview')
        elif logged_user.profile.role == 'Candidate':
            messages.info(request, 'You have already added the required number of result records.')
            return redirect('candidate_update_jamb_record')
    
    results = ApplicantResult.objects.select_related('applicant').filter(applicant=logged_user).order_by('-added_date')
    form = ApplicantResultForm(request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            with transaction.atomic():
                subject_name = form.cleaned_data['subject_name']
                result_name = form.cleaned_data['result_name']
                subject_grade = form.cleaned_data['subject_grade']
                exam_number = form.cleaned_data['exam_number']
                exam_year = form.cleaned_data['exam_year']
                
                exam_result = ApplicantResult.objects.create(
                    applicant=logged_user,
                    subject_name=subject_name,
                    result_name=result_name,
                    subject_grade=subject_grade,
                    exam_number=exam_number,
                    exam_year=exam_year
                    )
                if exam_result:
                    messages.success(request, 'Record Added Successfully.')
    else:
        form = ApplicantResultForm()
    
    context = {
        'form': form,
        'results': results,
        'page_title': 'Candidate Result',
    }
    return render(request, 'admission/applicant_result.html', context)


# Preview and Submit Application Form view
@login_required(login_url='accounts_login')
def application_preview(request):
    logged_user = request.user
    
    if not logged_user.profile.role == 'Applicant':
        return redirect('accounts_login')

    payment_redirect = _redirect_if_application_fee_unverified(request)
    if payment_redirect:
        return payment_redirect

    # Check if applicant has submitted application
    submission_status = get_application_submission(logged_user)
    if not application_submitted(submission_status):
        schools_attended = EducationRecord.objects.select_related('applicant').filter(applicant=logged_user)
        applicant_result = ApplicantResult.objects.select_related('applicant').filter(applicant=logged_user)
        try:
            applicant_course_of_study = PreApplicant.objects.select_related('applicant').get(applicant=logged_user)
            applicant_program = applicant_course_of_study.course_name
            applicant_id = applicant_course_of_study.application_number
            selected_application_session = applicant_course_of_study.academic_session
            # applicant_department = applicant_program.department.name 
        except PreApplicant.DoesNotExist:
            messages.warning(request, 'Applicant Course of Study Not Found')
            return redirect('accounts_dashboard')
        # Get the verified application fee payment for the applicant
        latest_payment = has_verified_application_fee(logged_user, selected_application_session)

        if latest_payment:
            # Get the academic session from the latest payment
            application_form_sess = latest_payment.academic_sess
        else:
            application_form_sess = selected_application_session

        if request.method == 'POST':
            # If the form is submitted, process the form data
            form = ApplicationSubmissionForm(request.POST)
            
            if form.is_valid():
                # Create a new applicant and Submit the Application with 
                # Academic Session, and Program/Course
                new_application = form.save(commit=False)
                new_application.student_id = applicant_id
                new_application.applicant = request.user
                new_application.academic_sess = application_form_sess 
                new_application.course_name = applicant_program
                new_application.save()

                # Redirect to a success page or render a template
                messages.success(request, 'Application Submitted Successfully.')
                return redirect('application_slip')
        else:
            form = ApplicationSubmissionForm()
        context = {
            'application_form_sess': application_form_sess,
            'applicant_result': applicant_result,
            'schools_attended': schools_attended,
            'form': form,
            'page_title': 'Application Preview',
        }
        return render(request, 'admission/application_preview.html', context)
    else:
        messages.success(request, 'Application Already Submitted Successfully.')
        return redirect('application_slip')


# Preview and Submit POST-UTME Registration view
@login_required(login_url='accounts_login')
@transaction.atomic
def post_utme_reg_preview(request):
    logged_user = request.user
    
    if not logged_user.profile.role == 'Candidate':
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    payment_redirect = _redirect_if_application_fee_unverified(request)
    if payment_redirect:
        return payment_redirect
    
    # Check if applicant has submitted application
    submission_status = get_application_submission(logged_user)
    if not application_submitted(submission_status):
        schools_attended = EducationRecord.objects.select_related('applicant').filter(applicant=logged_user)
        candidate_result = ApplicantResult.objects.select_related('applicant').filter(applicant=logged_user)
        try:
            applicant_course_of_study = Jamb.objects.select_related('applicant').get(applicant=logged_user)
            applicant_program = applicant_course_of_study.admitted_course
            applicant_id = applicant_course_of_study.exam_number
            application_form_sess = applicant_course_of_study.academic_session
            # applicant_department = applicant_program.department.name 
        except Jamb.DoesNotExist:
            messages.warning(request, 'Candidate Course of Study Not Found')
            return redirect('accounts_dashboard')
        
        if request.method == 'POST':
            # If the form is submitted, process the form data
            form = ApplicationSubmissionForm(request.POST)
            
            if form.is_valid():
                # Create a new applicant and Submit the Application with 
                # Academic Session, and Program/Course
                new_application = form.save(commit=False)
                new_application.student_id = applicant_id
                new_application.applicant = request.user
                new_application.academic_sess = application_form_sess 
                new_application.course_name = applicant_program
                new_application.save()
                # GET ACADEMIC SESSION AND COMPARE WITH CANDIDATE'S OWN
                try:
                    application_form_sess = AcademicSession.objects.get(name=application_form_sess.name)
                    # GET EARLIER ACADEMIC SESSION 
                    ealier_academic_session = AcademicSession.objects.order_by('start_date', 'end_date', 'name').first()
                    # GET LATEST ACADEMIC SESSION
                    latest_academic_session = AcademicSession.objects.order_by('-start_date', '-end_date', '-name').first()
                    
                    # CHECK IF CANDIDATE ACADEMIC SESSION EALIER ONE IN DB
                    if application_form_sess == ealier_academic_session:
                        # CHECK IF CANDIDATE ALREADY ADMITTED
                        try:
                            already_admitted = Admitted.objects.get(applicant=logged_user)
                            messages.warning(request, f'{logged_user.profile.last_name}: {already_admitted} already Admitted')
                            return redirect('student_dashboard')
                        except Admitted.DoesNotExist:
                            # CHECK IF STUDENT ALREADY REGISTERED
                            try:
                                nd_ii_student = Student.objects.get(candidate=logged_user)
                                messages.warning(request, f'{logged_user.profile.last_name}: {nd_ii_student} already Admitted')
                                return redirect('student_dashboard')
                            except Student.DoesNotExist:
                                # Create Admitted object
                                Admitted.objects.create(
                                    applicant=logged_user,
                                    academic_session=ealier_academic_session,
                                    admitted_course=applicant_program,
                                    admitted_level='NDII'
                                )
                                # Create Student object
                                Student.objects.create(
                                    student_id=applicant_id,
                                    candidate=logged_user,
                                    academic_session=ealier_academic_session,
                                    course=applicant_program,
                                    admitted_by=logged_user,
                                    current_level='NDII'  # Assuming 'NDI' as entry level
                                )

                                # Update the logged user's profile role to Student
                                logged_user.profile.role = 'Student'
                                logged_user.profile.save()
                                # Redirect to a success page or render a template
                                messages.success(request, 'Candidate Record Submitted Successfully.')
                                return redirect('post_utme_slip')
                    # CHECK CANDIDATE ACADEMIC SESSION WITH LATEST ONE IN DB
                    elif application_form_sess == latest_academic_session:
                        # Create Admitted object
                        Admitted.objects.create(
                            applicant=logged_user,
                            academic_session=latest_academic_session,
                            admitted_course=applicant_program,
                            admitted_level='NDI'
                        )
                        
                        # Create Student object
                        Student.objects.create(
                            student_id=applicant_id,
                            candidate=logged_user,
                            academic_session=latest_academic_session,
                            course=applicant_program,
                            admitted_by=logged_user,
                            current_level='NDI'  # Assuming 'NDI' as entry level
                        )

                        # Update the logged user's profile role to Student
                        logged_user.profile.role = 'Student'
                        logged_user.profile.save()

                        # Redirect to a success page or render a template
                        messages.success(request, 'Candidate Record Submitted Successfully.')
                        return redirect('post_utme_slip')
                except AcademicSession.DoesNotExist:
                    messages.warning(request, 'Academic Session Not Found. Contact the Polytechnic ICT.')
                
        else:
            form = ApplicationSubmissionForm()
        context = {
            'application_form_sess': application_form_sess,
            'candidate_result': candidate_result,
            'schools_attended': schools_attended,
            'form': form,
            'page_title': 'UTME Registration Preview', 
        }
        return render(request, 'admission/post_utme_preview.html', context)
    else:
        return redirect('post_utme_slip')

# Application Slip View
@login_required(login_url='accounts_login')
def post_utme_slip(request):
    logged_user = request.user
   
    # Check if applicant has submitted application
    try:
        check_submission = ApplicationSubmission.objects.select_related('applicant').get(applicant=logged_user)
        schools_attended = EducationRecord.objects.select_related('applicant').filter(applicant=logged_user)
        applicant_result = ApplicantResult.objects.select_related('applicant').filter(applicant=logged_user)
        # Use get instead of filter
        jamb_details = Jamb.objects.select_related('applicant').get(applicant=logged_user)
        # Calculate total score
        
        payments = Payment.objects.filter(
            user=logged_user,
            academic_sess=check_submission.academic_sess,
            verified=True,
            status='Approved',
            payment_source__in=['Acceptance Fee'],
        )

        context = {            
            'check_submission': check_submission,
            'jamb_details': jamb_details,
            'payments': payments,
            'page_title': 'UTME Registration Slip',
            'schools_attended': schools_attended,
            'applicant_result': applicant_result,
        }
        return render(request, 'admission/post_utme_slip.html', context)
        
    except ApplicationSubmission.DoesNotExist:
        messages.warning(request, 'You have NOT submitted POST-UTME Registration Yet')
        return redirect('post_utme_reg_preview')


# Edit Applicant's Profile
@login_required(login_url='accounts_login')
def edit_applicant_profile(request):
    logged_user = request.user
    # Get the profile of the logged user
    profile = logged_user.profile

    if logged_user.profile.role not in ['Applicant', 'Candidate']:
        return redirect('accounts_login')

    payment_redirect = _redirect_if_application_fee_unverified(request)
    if payment_redirect:
        return payment_redirect

    form_class = CandidateEditContactForm if logged_user.profile.role == 'Candidate' else ApplicantEditContactForm
    form = form_class(request.POST, request.FILES, instance=profile)

    if request.method == 'POST':
    
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile Updated Successfully.')
            return redirect('applicant_contact')
            
    else:
        form = form_class(instance=profile)

    context = {
        'form': form,
        'page_title': 'Edit Application Form',
    }
    return render(request, 'admission/edit_applicant_profile.html', context)


# Edit Candidate's Profile
@login_required(login_url='accounts_login')
def edit_candidate_profile(request):
    logged_user = request.user
    # Get the profile of the logged user
    profile = logged_user.profile

    if not logged_user.profile.role == 'Candidate':
        return redirect('accounts_login')

    payment_redirect = _redirect_if_application_fee_unverified(request)
    if payment_redirect:
        return payment_redirect

    form = CandidateEditContactForm(request.POST, request.FILES, instance=profile)

    if request.method == 'POST':
    
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile Updated Successfully.')
            return redirect('post_utme_reg_preview')
            
    else:
        form = CandidateEditContactForm(instance=profile)

    context = {
        'form': form,
        'page_title': 'Edit Candidate Profile',
    }
    return render(request, 'admission/edit_applicant_profile.html', context)


# Applicant Sponsor's Contact
@login_required(login_url='accounts_login')
def edit_applicant_sponsor(request):
    # Get the logged user
    logged_user = request.user
    # Check if he/she is logged in
    if not (logged_user.profile.role == 'Applicant' or logged_user.profile.role == 'Candidate'):
        return redirect('accounts_login')

    payment_redirect = _redirect_if_application_fee_unverified(request)
    if payment_redirect:
        return payment_redirect

    # Get the Applicant Sponsor Details and lock it for update in a Transaction
    try:
        with transaction.atomic():
            applicant_sponsor = SponsorDetails.objects.select_for_update().get(applicant=logged_user)

    except SponsorDetails.DoesNotExist:
        return redirect('accounts_login')

    form = SponsorDetailsForm(request.POST or None, request.FILES or None, instance=applicant_sponsor)

    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
                messages.success(request, 'Sponsor Information Updated Successfully.')
                if logged_user.profile.role == 'Applicant':
                    return redirect('application_preview')
                elif logged_user.profile.role == 'Candidate':
                    return redirect('post_utme_reg_preview')
                else:
                    return redirect('accounts_login')
        except Exception as e:
            # Log the error using Django's logging framework
            messages.warning(request, f'Something went wrong {e} ')
            return redirect('custom_500')

    context = {
        'page_title': 'Update Sponsor Data',
        'form': form,
    }

    return render(request, 'admission/applicant_sponsor.html', context)


# Update School Attended Record view
class VerifiedApplicationFeeRequiredMixin:
    def dispatch(self, request, *args, **kwargs):
        payment_redirect = _redirect_if_application_fee_unverified(request)
        if payment_redirect:
            return payment_redirect
        return super().dispatch(request, *args, **kwargs)


class EducationRecordUpdateView(VerifiedApplicationFeeRequiredMixin, LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = EducationRecord
    template_name = 'admission/applicant_education.html'
    form_class = EducationRecordForm  # Use the form for updating

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'Applicant' or self.request.user.profile.role == 'Candidate'

    def get_queryset(self):
        return super().get_queryset().filter(applicant=self.request.user)

    def form_valid(self, form):
        # Save the form and add a success message
        form.save()
        messages.success(self.request, 'School Attended Update Successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Update Education'
        return context

    success_url = reverse_lazy('applicant_education')


# Delete Education Record View View
class EducationRecordDeleteView(VerifiedApplicationFeeRequiredMixin, LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = EducationRecord
    template_name = 'admission/confirm_education_record_delete.html'
    success_url = '/applicant/education/'
    context_object_name = 'school_record'

    # Define a custom test function to check if the user is a Applicant
    def test_func(self):
        return self.request.user.profile.role == 'Applicant' or self.request.user.profile.role == 'Candidate'

    def get_queryset(self):
        return super().get_queryset().filter(applicant=self.request.user)

    def delete(self, request, *args, **kwargs):
        messages.error(request, 'School Record Deleted successfully.')
        return super().delete(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Delete School Record'
        return context


# Delete Applicant Ordinary Result Data View 
class ResultRecordDeleteView(VerifiedApplicationFeeRequiredMixin, LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = ApplicantResult
    template_name = 'admission/confirm_result_record_delete.html'
    success_url = '/applicant/examination/result/'
    context_object_name = 'school_result'

    # Define a custom test function to check if the user is a Applicant
    def test_func(self):
        return self.request.user.profile.role == 'Applicant' or self.request.user.profile.role == 'Candidate'

    def get_queryset(self):
        return super().get_queryset().filter(applicant=self.request.user)

    def delete(self, request, *args, **kwargs):
        messages.error(request, 'School Result Deleted successfully.')
        return super().delete(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Delete School Result'
        return context
    

# Update School Result view
class ResultRecordUpdateView(VerifiedApplicationFeeRequiredMixin, LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = ApplicantResult
    template_name = 'admission/applicant_result.html'
    form_class = ApplicantResultForm  # Use the form for updating

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'Applicant' or self.request.user.profile.role == 'Candidate'

    def get_queryset(self):
        return super().get_queryset().filter(applicant=self.request.user)

    def form_valid(self, form):
        # Save the form and add a success message
        form.save()
        messages.success(self.request, 'Result Update Successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Update Result'
        return context

    success_url = reverse_lazy('applicant_result')


# Application Slip View
@login_required(login_url='accounts_login')
def application_slip(request):
    logged_user = request.user
   
    # Check if applicant has submitted application
    try:
        check_submission = ApplicationSubmission.objects.select_related('applicant').get(applicant=logged_user)
        schools_attended = EducationRecord.objects.select_related('applicant').filter(applicant=logged_user)
        applicant_result = ApplicantResult.objects.select_related('applicant').filter(applicant=logged_user)
        academic_sess = check_submission.academic_sess
        payments = _verified_fee_payments_for_session(logged_user, academic_sess)

        # Check for the existence of specific fee payments
        has_application_form_payment = payments.filter(payment_source='Application Form').exists()
        has_acceptance_fee_payment = payments.filter(payment_source='Acceptance Fee').exists()
        has_screening_fee_payment = payments.filter(payment_source='Screening Fee').exists()
        has_caution_fee_payment = payments.filter(payment_source='Caution Fee').exists()
        has_school_fees_payment = payments.filter(payment_source='School Fees').exists()
            
        # Get the verified application fee payment for the Application instance
        latest_payment = has_verified_application_fee(logged_user, academic_sess)

        if latest_payment:
            # Get the academic session from the latest payment
            application_form_sess = latest_payment.academic_sess
        else:
            # If there is no payment, set a default academic session or handle it as needed
            application_form_sess = AcademicSession.get_application_session()
        context = {
            'academic_sess': academic_sess,
            'payments': payments,
            'has_screening_fee_payment': has_screening_fee_payment,
            'has_acceptance_fee_payment': has_acceptance_fee_payment,
            'has_application_fee_payment': has_application_form_payment,
            'has_caution_fee_payment': has_caution_fee_payment,
            'has_school_fees_payment': has_school_fees_payment,
            'page_title': 'Application Slip',
            'schools_attended': schools_attended,
            'applicant_result': applicant_result,
            'application_form_sess': application_form_sess,
        }
        return render(request, 'admission/application_slip.html', context) 
        
    except ApplicationSubmission.DoesNotExist:
        messages.warning(request, 'You have NOT submitted Application Yet')
        return redirect('application_preview')


# Application Slip Back Page
@login_required(login_url='accounts_login')
def application_slip_back(request):
    logged_user = request.user

    if logged_user.profile.role not in {'Applicant', STUDENT_ROLE, GRADUATE_ROLE}:
        messages.success(request, 'You are not an Applicant')
        return redirect('accounts_login')
    
    context = {
        'page_title': 'Application Slip Page',
    }
    return render(request, 'admission/application_slip_back.html', context)


# Application List of Successfully paid screening Fee
@login_required(login_url='accounts_login')
def generate_application_list(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.profile.role == 'Super Admin' or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    form = ApplicationReportForm(request.GET or None)
    if request.method == "GET" and form.is_valid():
        academic_session = form.cleaned_data['academic_session']
        course_of_study = form.cleaned_data['course_of_study']
        start_date = form.cleaned_data['start_date']
        end_date = form.cleaned_data['end_date'] 

        # Get the School and Department names for the selected course
        chosen_course = CourseOfStudy.objects.filter(name=course_of_study).first()
        school_name = chosen_course.department.faculty.name
        department_name = chosen_course.department.name

        # Filter transactions by type and date range
        application_list = ApplicationSubmission.objects.filter(
                academic_sess=academic_session,
                course_name=course_of_study,
                submitted_date__gte=start_date,
                submitted_date__lte=end_date
                )
        # Check for successful payment and confirmation
        successful_applications = application_list.filter(
            applicant__payment__payment_source='Screening Fee',
            applicant__payment__status='Successful',  # Assuming 'Success' is the status for successful payment
            applicant__payment__academic_sess=academic_session,
            confirm=True
        )
        count_applicants = successful_applications.count()

        paginator = Paginator(successful_applications, 5)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'school_name': school_name,
            'count_applicants': count_applicants,
            'course_of_study': course_of_study,
            'academic_session': academic_session,
            'application_list': page_obj,
            'page_title': 'Application List',
            'start_date': start_date,
            'end_date': end_date,
        }
        return render(request, 'admission/application_list.html', context)
    else:
        form = ApplicationReportForm()
    context = {
        'form': form,
        'page_title': 'Application Report Form',
    }
    return render(request, 'admission/application_report_form.html', context)


# MIS Admin-1 Admit Candidates into various Levels
@login_required(login_url='accounts_login')
def search_for_admission(request):
    logged_user = request.user
    
    if not user_can_manage_admissions(logged_user):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_dashboard')
    
    # Log user activity
    UserActivityLog.objects.create(user=logged_user, action=' Search Candidate for Admission Page.')
    # Search Form for Course of Study
    form = AdmitByProgramForm(request.GET or None)
    # Check if the Search came through Get Method and the form is valid
    has_filters = any(request.GET.get(key) for key in ('academic_session', 'course_of_study', 'application_category', 'search_query'))
    if request.method == 'GET' and has_filters and form.is_valid():
        academic_session = form.cleaned_data['academic_session']
        course_of_study = form.cleaned_data['course_of_study']
        application_category = form.cleaned_data.get('application_category')
        search_query = form.cleaned_data.get('search_query')
        # Get the name of the Department from the Course of Study
        department = course_of_study.department
        confirmed_applicants_by_program = attach_admission_payment_details(
            get_admission_candidate_queryset(
                course_of_study=course_of_study,
                academic_session=academic_session,
                application_category=application_category,
                search_query=search_query,
            )
        )

        if not confirmed_applicants_by_program:
            messages.info(
                request,
                'No eligible candidates matched the selected filters. Confirm the application was submitted and the Screening Fee is verified for this session.'
            )

        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Searched for Session; {academic_session}, Programme; {course_of_study} with verified Screening Fee ')
        context = {     
            'department': department,
            'course_of_study': course_of_study,
            'academic_session': academic_session,
            'application_category': application_category,
            'search_query': search_query,
            'page_title': 'Admission of Candidate(s)',
            'applicants_by_program': confirmed_applicants_by_program
        }
        return render(request, 'admission/admit_candidate.html', context)
    else:
        form = AdmitByProgramForm()
    context = {
        'form': form,
        'page_title': 'Search By Program',
    }
    return render(request, 'admission/search_applicants_program.html', context)


@login_required(login_url='accounts_login')
def ajax_admission_candidates(request):
    logged_user = request.user
    if not user_can_manage_admissions(logged_user):
        return JsonResponse({'ok': False, 'message': 'You are not authorised to view admission candidates.'}, status=403)

    academic_session_id = request.GET.get('academic_session')
    course_of_study_id = request.GET.get('course_of_study')
    application_category = request.GET.get('application_category') or None
    search_query = request.GET.get('search_query') or None

    academic_session = get_object_or_404(AcademicSession, pk=academic_session_id)
    course_of_study = get_object_or_404(CourseOfStudy, pk=course_of_study_id)
    applicants = attach_admission_payment_details(
        get_admission_candidate_queryset(
            course_of_study=course_of_study,
            academic_session=academic_session,
            application_category=application_category,
            search_query=search_query,
        )
    )
    html = render_to_string(
        'admission/partials/_admission_candidate_rows.html',
        {'applicants_by_program': applicants},
        request=request,
    )
    message = ''
    if not applicants:
        message = 'No eligible candidates matched the selected filters. Only submitted applications with verified Screening Fee are shown.'

    return JsonResponse({'ok': True, 'html': html, 'count': len(applicants), 'message': message})


@login_required(login_url='accounts_login')
def ajax_admission_candidate_summary(request, application_id):
    logged_user = request.user
    if not user_can_manage_admissions(logged_user):
        return JsonResponse({'ok': False, 'message': 'You are not authorised to review admission candidates.'}, status=403)

    application = get_object_or_404(
        get_admission_candidate_queryset(include_admitted=True),
        pk=application_id,
    )
    html = render_to_string(
        'admission/partials/_admission_candidate_summary.html',
        get_admission_review_context(application),
        request=request,
    )
    return JsonResponse({'ok': True, 'html': html})


@login_required(login_url='accounts_login')
def admission_candidate_review(request, application_id):
    logged_user = request.user
    if not user_can_manage_admissions(logged_user):
        messages.warning(request, 'You are not authorised to review admission candidates.')
        return redirect('accounts_dashboard')

    application = get_object_or_404(
        get_admission_candidate_queryset(include_admitted=True),
        pk=application_id,
    )
    context = get_admission_review_context(application)
    context.update({
        'page_title': 'Review Application Summary',
        'can_admit': bool(context['screening_payment']) and not application.is_admitted and not hasattr(application.applicant, 'student_profile'),
    })
    return render(request, 'admission/admission_candidate_review.html', context)


# MIS ADMIN-1 Admit Candidates into various Levels
@login_required(login_url='accounts_login')
def admit_candidates(request): 
    logged_user = request.user
    if not user_can_manage_admissions(logged_user):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_dashboard')

     # Log user activity
    UserActivityLog.objects.create(user=logged_user, action=' Admit Candidate(s) into various Programmes Page ')
    if request.method == 'POST':
        # Get the selected applicant through the checkboxes
        selected_applicant_ids = request.POST.getlist('selected_applicant[]')
        academic_session_id = request.POST.get('academic_session')
        academic_session = get_object_or_404(AcademicSession, pk=academic_session_id)
        # Initiate the admitted candidate counter
        admitted_candidate_count = 0
        # Try to put the process in a transaction
        try:
            with transaction.atomic():
                admitted_candidates = []
                admitted_records = []
                # Query ApplicationSubmission and Payment DB to ensure that 
                # The selected Candidate has submitted Application Form, paid
                # verified Screening Fee for the selected session
                confirmed_applicants = get_admission_candidate_queryset(
                    academic_session=academic_session
                ).filter(
                    id__in=selected_applicant_ids
                )

                for applicant in confirmed_applicants:
                    try:
                        # Get the profile of the Applicant
                        candidate_profile = applicant.applicant.profile

                        # Get the Program of study Instance
                        program = applicant.course_name
                        # Check for Program and Associate to determine the 
                        # Level to be admitted into
                        fee_category = get_application_fee_category(program, academic_session)
                        if fee_category == 'HND':
                            admitted_level = 'HNDI'
                        elif fee_category == 'ND':
                            admitted_level = 'NDI'
                        elif fee_category == 'CERTIFICATE':
                            admitted_level = 'CERTIFICATE'
                        else:
                            messages.error(request, 'The selected programme does not have a configured admission category for this academic session.')
                            raise ValueError('Programme admission category is not configured')

                        # Update the role attribute of the profile to "Student"
                        candidate_profile.role = 'Student'
                        candidate_profile.save()

                        # Update the is_admitted attribute in ApplicationSubmission model
                        applicant.is_admitted = True
                        applicant.save()

                        # Add the applicants to the empty list initially created
                        admitted_candidates.append(Student(
                            student_id=applicant.student_id,
                            candidate=candidate_profile.user,
                            academic_session=academic_session,
                            course=program,
                            current_level=admitted_level,
                            admitted_by=logged_user
                        ))
                        # Create an Admitted instance and add it to the admitted_records list
                        admitted_records.append(Admitted(
                            applicant=candidate_profile.user,
                            academic_session=academic_session,
                            admitted_course=program,
                            admitted_level=admitted_level,
                        ))

                    except PreApplicant.DoesNotExist:
                        messages.error(request, f'Applicant with ID {applicant.applicant.id} does not exist.')
                        # Continue processing other applicants even if one is not found
                        continue

                # Check if Admission Transaction was Success, count and give message
                if admitted_candidates:
                    Student.objects.bulk_create(admitted_candidates)
                    Admitted.objects.bulk_create(admitted_records)
                    admitted_candidate_count += len(admitted_candidates)
                    # Log user activity
                    UserActivityLog.objects.create(user=logged_user, action=f'Admitted; {admitted_candidate_count} candidate(s) into {admitted_level} for {academic_session} academic session')
                    messages.success(request, f'{admitted_candidate_count} Candidate(s) Admitted into {admitted_level} for {academic_session} Academic Session.')
                    return redirect('newly_admitted_students', admitted_level)
                else:
                    messages.error(request, 'No eligible candidate was admitted. Only submitted applications with verified Screening Fee and no existing admission can be admitted.')
                    return redirect('search_for_admission')
        except Exception as e:
            messages.error(request, f'Something went wrong: {str(e)}')

    else:
        messages.error(request, 'Invalid request method. Expected POST.')

    # Redirect back to the search page or an appropriate page
    return redirect('search_for_admission')


# Newly Admitted Student List
@login_required(login_url='accounts_login')
def newly_admitted_students(request, admitted_level):
    logged_user = request.user
    if not (logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.is_superuser or logged_user.profile.role == 'Super Admin'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_dashboard')

    try:
        # Get the latest Academic Session
        latest_academic_session = AcademicSession.objects.order_by('-start_date', '-end_date', '-name').first()
        
        # Query the Student model to get the latest admitted students for the latest academic session and the specified level
        newly_admitted_candidates = Admitted.objects.filter(
            admitted_level=admitted_level,
            academic_session=latest_academic_session,
        ).order_by('-admitted_at')
        paginator = Paginator(newly_admitted_candidates, 10)  # Change 2 to your desired number of items per page

        page_number = request.GET.get('page')

        try:
            recently_admitted = paginator.page(page_number)
        except PageNotAnInteger:
            recently_admitted = paginator.page(1)
        except EmptyPage:
            recently_admitted = paginator.page(paginator.num_pages)
        # Log user activity
            UserActivityLog.objects.create(user=logged_user, action=f'Generate List of {admitted_level} Recently Admitted Students')
        context = {
            'page_title': 'Newly Admitted Students',
            'admitted_students': recently_admitted,
            'admitted_level': admitted_level,
            'latest_academic_session': latest_academic_session,
            'newly_admitted_candidates': newly_admitted_candidates,
        }

        return render(request, 'admission/newly_admitted.html', context)
    
    except AcademicSession.DoesNotExist:
        messages.error(request, 'No Academic Session found.')
        return redirect('admit_candidates')


@login_required(login_url='accounts_login')
def download_newly_admitted_pdf(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.is_superuser or logged_user.profile.role == 'Super Admin'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_dashboard')
    
    admitted_level = request.GET.get('admitted_level')
    academic_session_id = request.GET.get('academic_session')
    
    try:
        academic_session = AcademicSession.objects.get(id=academic_session_id)
        newly_admitted_candidates = Admitted.objects.filter(
            admitted_level=admitted_level,
            academic_session=academic_session,
        ).order_by('-admitted_at')

        counted_admitted = newly_admitted_candidates.count()

        context = {
            'newly_admitted_candidates': newly_admitted_candidates,
            'admitted_level': admitted_level,
            'academic_session': academic_session,
            'page_title': 'Recently Admitted Students',
            'counted_admitted': counted_admitted,
            'current_datetime': timezone.now(),
        }
        
        # Generate the PDF
        response = generate_newly_admitted_pdf(request, newly_admitted_candidates, context)
        
        if response:
            # Log user activity
            UserActivityLog.objects.create(user=logged_user, action=f'Downloaded PDF List of {counted_admitted} {admitted_level} Recently Admitted Students')
            return response
        else:
            messages.error(request, 'Failed to generate PDF.')
            return redirect('newly_admitted_students', admitted_level=admitted_level)
    except AcademicSession.DoesNotExist:
        messages.error(request, 'No Academic Session found.')
        return redirect('newly_admitted_students', admitted_level=admitted_level)


# Generae PDF Document for Newly Admitted Students
def generate_newly_admitted_pdf(request, queryset, context):
    # Render the HTML content of the PDF template with the filtered queryset
    context['newly_admitted_candidates'] = queryset
    html_content = render_to_string('admission/newly_admitted_pdf.html', context)

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
        response['Content-Disposition'] = 'attachment; filename="newly_admitted_students.pdf"'  # Set filename for download
        return response
    else:
        # If PDF generation failed, return None
        return None


# Search for Candidate in order to De-Admit
@login_required(login_url='accounts_login')
def search_and_deadmit(request):
    logged_user = request.user

    # Check if the user is a MIS Admin-1
    if not (logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.is_superuser or logged_user.profile.role == 'Super Admin'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_dashboard')

    form = SearchStudentForm(request.GET or None)

    # Log user activity
    UserActivityLog.objects.create(user=logged_user, action='Visited Search and De-Admit Student')

    if request.method == 'GET' and form.is_valid():
        search_code = form.cleaned_data['search_id']
        
        # Extract the date part from the search code
        uid = search_code[:6]

        applicant = PreApplicant.objects.filter(application_number__endswith=uid).first()
        if applicant:
            return redirect('candidate_detail', candidate_id=applicant.id)
        else:
            messages.warning(request, f'Candidate with Application ID: {uid} was NOT Found')
    else:
        form = SearchStudentForm()

    context = {
        'form': form,
        'page_title': 'Search & De-Admit',
    }

    return render(request, 'admission/search_candidate.html', context)


# Candidate Detail for De-Admission
@login_required(login_url='accounts_login')
def candidate_detail(request, candidate_id):
    logged_user = request.user
    if not (logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.is_superuser or logged_user.profile.role == 'Super Admin'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    try:
        candidate = get_object_or_404(PreApplicant, pk=candidate_id)
        # Confirm the candidate instance with the one in the Student Model
        try:
            student = Student.objects.select_related('candidate').get(candidate=candidate.applicant)
        except Student.DoesNotExist:
            messages.warning(request, 'Student Not Fount')

        context = {
            'student': student,
            'candidate': candidate,
            'page_title': 'Candidate Detail',
        }
        return render(request, 'admission/candidate_detail.html', context)

    except PreApplicant.DoesNotExist:
        messages.error(request, f'No Candidate Found with ID: {candidate_id} ')


# Deadmit Student
@login_required(login_url='accounts_login')
def deadmit_student(request, pk):
    logged_user = request.user
    if not (logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.is_superuser or logged_user.profile.role == 'Super Admin'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    try:
        # Get the student instance or return a 404 response if not found
        student = get_object_or_404(Student, pk=pk)
        
        # Deadmit the student
        student.deadmit()
        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f'De-Admited Student with the following Details: Names; {student.candidate.profile.first_name} {student.candidate.profile.last_name}, Course: {student.course}, Level {student.current_level} ')
        messages.success(request, 'Student De-Admitted Successfully.')
        return redirect('search_for_admission')

    except Student.DoesNotExist:
        messages.error(request, 'Student Not Found')
        return redirect('search_and_deadmit')


# Generate Admission List by Level Program
@login_required(login_url='accounts_login')
def generate_admission_list(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.is_superuser or logged_user.profile.role == 'Super Admin'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    form = AdmissionListForm(request.GET or None)

    if request.method == 'GET' and form.is_valid():
        academic_session = form.cleaned_data['academic_session']
        level = form.cleaned_data['level']
        start_date = form.cleaned_data['start_date']
        end_date = form.cleaned_data['end_date']

        try:
            if level == 'CERTIFICATE' or level == 'NDI':
                remedial_admission_list = Admitted.objects.select_related('academic_session').filter(
                    academic_session=academic_session,
                    admitted_level=level,
                    admitted_date__range=[start_date, end_date]
                ).order_by('id')
            
                count_admission = remedial_admission_list.count()
                            
                # Pagination by 10 records
                paginator = Paginator(remedial_admission_list, 10)
                page_number = request.GET.get('page')

                try:
                    admission_list = paginator.page(page_number)
                except PageNotAnInteger:
                    admission_list = paginator.page(1)
                except EmptyPage:
                    admission_list = paginator.page(paginator.num_pages)

                # Log user activity
                UserActivityLog.objects.create(user=logged_user, action=f'Generated {level} Admission List for {academic_session} Academic session')
                context = {
                    'start_date': start_date,
                    'end_date': end_date,
                    'academic_session': academic_session,
                    'level': level,
                    'count_admission': count_admission,
                    'page_title': f' {level} Admission List',
                    'admission_list': admission_list,
                    'remedial_admission_list': remedial_admission_list,
                }
                # Check for PDF download request
                if request.GET.get('downloaded'):
                    pdf_data = remedial_admission_list_pdf(request, remedial_admission_list, context)
                    if pdf_data:
                        return pdf_data
                    else:
                        messages.error(request, 'Error generating PDF')

                return render(request, 'admission/admission_list.html', context)
            else:
                messages.error(request, 'Invalid level selected')
        
        except Admitted.DoesNotExist:
            messages.error(request, 'No Record Found for Search Query')
    else:
        form = AdmissionListForm()

    context = {
        'page_title': 'Admission List',
        'form': form,
    }
    return render(request, 'admission/generate_admission_list.html', context)


# FUNCTION FOR THE GENERATION OF PDF FOR REMEDIAL PROGRAMME ADMISSION REPORT
def remedial_admission_list_pdf(request, remedial_admission_list, context):
    # Render the HTML content of the PDF template with the filtered queryset
    context['remedial_admission_list'] = remedial_admission_list
    html_content = render_to_string('admission/remedial_admission_pdf.html', context)

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
        response['Content-Disposition'] = 'attachment; filename="remedial_admission_list.pdf"'  # Set filename for download
        return response
    else:
        # If PDF generation failed, return None
        return None


@transaction.atomic
def process_csv_row(row):
    try:
        user = create_or_update_user(row)
        create_or_update_jamb_score(user, row)
    except Exception as e:
        raise Exception(f"Error processing row: {row}, Error: {str(e)}")

@transaction.atomic
def process_csv_file(csv_file):
    try:
        file_data = csv_file.read().decode('utf-8')
        csv_data = csv.DictReader(file_data.splitlines())

        count = 0  # Initialize a counter for the number of processed rows
        for row in csv_data:
            process_csv_row(row)
            count += 1  # Increment the counter for each processed row
        return count  # Return the count
    except Exception as e:
        raise Exception(f"Error in CSV processing: {str(e)}")


# Upload Candidates for Admission from CSV File View
@login_required(login_url='accounts_login')
def jamb_candidates_upload(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.profile.role == 'Super Admin' or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    # Log user activity
    UserActivityLog.objects.create(user=logged_user, action='Visited Jamb Candidate(s) Upload Page')
    if request.method == 'POST':
        form = CSVUploadForm(request.POST, request.FILES)
        csv_file = request.FILES.get('file')
        if not csv_file or not csv_file.name.endswith('.csv'):
            messages.error(request, 'Invalid file type. Please upload a CSV file.')
            return redirect('jamb_candidates_upload')
        
        try:
            candidate_count = process_csv_file(csv_file)  # Get the count of processed candidates
            # Log user activity
            UserActivityLog.objects.create(user=logged_user, action=f'Uploaded {candidate_count} candidate(s) for POST-UTME Admission')
            messages.success(request, f'POST-UTME Candidates Uploaded Successfully. {candidate_count} candidates processed.')
            return redirect('post_utme_admission_list')
        except Exception as e:
            messages.error(request, f"An error occurred during CSV processing: {str(e)}")
            return redirect('jamb_candidates_upload')
   
    else:
        form = CSVUploadForm()

    context = {
        'form': form,
        'page_title': 'POST-UTME Candidates Upload',
    }

    return render(request, 'admission/post_utme_upload_form.html', context)


# Method to parse date into the desired format
def parse_date(date_string):
    try:
        # Parse the date string
        date_obj = parser.parse(date_string, dayfirst=True)

        # Handling years in two-digit format (e.g., '97' for '1997')
        if date_obj.year < 100:
            # Assuming all dates are after 1900
            date_obj = date_obj.replace(year=date_obj.year + 1900)

        # Format the date as 'YYYY-MM-DD'
        return date_obj.strftime('%Y-%m-%d')
    except ValueError as e:
        raise Exception(f"Invalid date format: {date_string} - {str(e)}")


# Method to Create User and Profile record
def create_or_update_user(row):
    username = row['JAMB ID']
    user, created = User.objects.get_or_create(username=username)
 
    if created:
        user.set_password(row['JAMB ID'])
        user.save()

    # Parse and format the date of birth
    formatted_dob = parse_date(row['Date of Birth'])

    # Get State name and compare with the one in Database
    state_name = row['State']
    try:
        state_instance, _ = State.objects.get_or_create(name=state_name)
    except Exception as e:
        raise Exception(f"Error processing state '{state_name}': {str(e)}")

    # Get Local Government name and compare with the one in Database
    lga_name = row['Local Govt.']
    try:
        lga_instance, _ = LocalGovernment.objects.get_or_create(name=lga_name, state=state_instance)
    except Exception as e:
        raise Exception(f"Error processing local government '{lga_name}' in state '{state_name}': {str(e)}")

    Profile.objects.update_or_create(
        user=user,
        defaults={
            'first_name': row['First Name'],
            'middle_name': row['Middle Name'],
            'last_name': row['Last Name'],
            'date_birth': formatted_dob,  # Use the formatted date
            'gender': row['Gender'],
            'phone_number': row['Phone Number'],
            'nationality': 'Nigeria',
            'state': state_instance,
            'lga': lga_instance,
            'role': 'Candidate'
        }
    )

    return user

# A mapping of known CSV course name variations to your CourseOfStudy model names 
COURSE_NAME_MAPPING = {
    "Computer Engineering Technology": "NATIONAL DIPLOMA IN COMPUTER ENGINEERING TECHNOLOGY (ND CET)",
    "Electrical And Electronics Engineering Technology": "NATIONAL DIPLOMA IN ELECTRICAL AND ELECTRONICS ENGINEERING TECHNOLOGY (ND EEET)",
    "Agricultural Technology": "NATIONAL DIPLOMA IN AGRICULTURAL TECHNOLOGY (ND AT)",
    "Computer Science": "NATIONAL DIPLOMA IN COMPUTER SCIENCE (ND CS)",
    "Statistics": "NATIONAL DIPLOMA IN STATISTICS (ND STAT)",
    "Accountancy": "NATIONAL DIPLOMA IN ACCOUNTANCY (ND ACCT)",
    "Business Administration And Management": "NATIONAL DIPLOMA IN BUSINESS ADMINISTRATION AND MANAGEMENT (ND BAM)",
    "Public Administration": "NATIONAL DIPLOMA IN PUBLIC ADMINISTRATION (ND PA)",
    # Add other mappings as needed 
}


# Method to determine Fee Category
def determine_fee_category():
    contains_hnd = any("Higher National Diploma" in course for course in COURSE_NAME_MAPPING.values())
    contains_nd = any("National Diploma" in course for course in COURSE_NAME_MAPPING.values())
    
    if contains_hnd:
        return 'HND'
    elif contains_nd:
        return 'ND'
    else:
        return 'Other'  # Default value or based on your logic


# Method to get the name of Closest Course Name Match
def get_closest_course_name(course_name):
    course_names = CourseOfStudy.objects.values_list('name', flat=True)
    closest_matches = difflib.get_close_matches(course_name, course_names)
    return closest_matches[0] if closest_matches else None


# Method for creating Candidate JAMB Record
def create_or_update_jamb_score(user, row):
    csv_course_name = row.get('Course of Study').strip()
    
    # Check if the course name is in the mapping
    if csv_course_name in COURSE_NAME_MAPPING:
        course_name = COURSE_NAME_MAPPING[csv_course_name]
    else:
        # If not in mapping, find the closest match
        course_name = get_closest_course_name(csv_course_name)
    
    logging.debug(f"Course name resolved to: {course_name}")
    
    if course_name:
        try:
            # Find the latest academic session
            latest_academic_session = AcademicSession.objects.order_by('-start_date', '-end_date', '-name').first()
            if not latest_academic_session:
                logging.error("No academic session found in the database.")
                return
            # Programme of Study
            course_of_study = CourseOfStudy.objects.get(name=course_name)
            
            # Determine Admitted Level
            admitted_level = 'NDI'
           
            # Update or create the Jamb
            jamb_candidates, created = Jamb.objects.update_or_create(
                applicant=user,
                defaults={
                    'exam_name': 'JAMB',
                    'exam_year': row.get('Exam Year'),
                    'exam_number': row.get('JAMB ID'),
                    'total_score': row.get('Total Score'),
                    'academic_session': latest_academic_session,
                    'admitted_course': course_of_study,
                    'admitted_level': admitted_level,
                }
            )
            if created:
                logging.info(f"Candidate record created successfully for user: {user.username}")
            else:
                logging.info(f"Candidate record updated successfully for user: {user.username}")
        except CourseOfStudy.DoesNotExist:
            logging.error(f"Course of study '{course_name}' does not exist in the database.")
        except MultipleObjectsReturned:
            logging.error(f"Multiple records found for course of study '{course_name}'. This should be unique.")
        except Exception as e:
            logging.error(f"An error occurred: {e}")
    else:
        logging.error(f"Course name '{csv_course_name}' not found in mapping or database.")


# Post-UTME Admisssion List
@login_required(login_url='accounts_login')
def post_utme_admission_list(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.profile.role == 'Super Admin' or logged_user.is_superuser):
        return redirect('accounts_login')

    # Find the latest academic session
    latest_academic_session = AcademicSession.objects.order_by('-start_date', '-end_date', '-name').first()

    # Generate List with total score calculation
    post_utme_admission_list = Jamb.objects.select_related('academic_session').filter(academic_session=latest_academic_session).order_by('id')
    count_admission = post_utme_admission_list.count()
    # Pagination by 10 records
    paginator = Paginator(post_utme_admission_list, 10)  

    page_number = request.GET.get('page')

    try:
        admission_list = paginator.page(page_number)
    except PageNotAnInteger:
        admission_list = paginator.page(1)
    except EmptyPage:
        admission_list = paginator.page(paginator.num_pages)

    context = {
        'latest_academic_session': latest_academic_session,
        'count_admission': count_admission,
        'admission_list': admission_list,
        'page_title': 'UTME Admission',
    }
    
    # Check for PDF download request
    if request.GET.get('downloaded'):
        pdf_data = post_utme_list_pdf(request, post_utme_admission_list, context)
        if pdf_data:
            return pdf_data
        else:
            messages.error(request, 'Error generating PDF')
    return render(request, 'admission/post_utme_admission.html', context)


# FUNCTION FOR THE GENERATION OF PDF FOR POST-UTME ADMISSION
def post_utme_list_pdf(request, post_utme_admission_list, context):
    # Render the HTML content of the PDF template with the filtered queryset
    context['post_utme_admission_list'] = post_utme_admission_list
    html_content = render_to_string('admission/post_utme_admission_pdf.html', context)

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
        response['Content-Disposition'] = 'attachment; filename="post_utme_admission_list.pdf"'  # Set filename for download
        return response
    else:
        # If PDF generation failed, return None
        return None
    

# Post-UTME Admisssion Report
@login_required(login_url='accounts_login')
def post_utme_admission_report(request):
    logged_user = request.user
    if not (logged_user.profile.role in ['ADMISSION-OFFICER', 'Admin', 'Super Admin'] or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    # Log user activity
    UserActivityLog.objects.create(user=logged_user, action='Visited Application List by Academic Session Page')

    form = ApplicationlistBySessionForm(request.GET or None)
    if request.method == "GET" and form.is_valid():
        academic_session = form.cleaned_data['academic_session']
        start_date = form.cleaned_data['start_date']
        end_date = form.cleaned_data['end_date']
        
        # Filter Applicants by Session and Date Range
        post_utme_admission_list = Jamb.objects.filter(
            academic_session=academic_session,
            uploaded_date__range=[start_date, end_date]
        ).order_by('id')
        count_admission = post_utme_admission_list.count()

        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Generated POST-UTME Admission list for {academic_session} Academic Session with {count_admission} Applicants')

        # Pagination by 10 records
        paginator = Paginator(post_utme_admission_list, 10)
        page_number = request.GET.get('page')

        try:
            admission_list = paginator.page(page_number)
        except PageNotAnInteger:
            admission_list = paginator.page(1)
        except EmptyPage:
            admission_list = paginator.page(paginator.num_pages)

        context = {
            'latest_academic_session': academic_session,
            'count_admission': count_admission,
            'admission_list': admission_list,
            'start_date': start_date,
            'end_date': end_date,
            'page_title': 'Jamb Admission Report',
        }

        # Check for PDF download request
        if request.GET.get('downloaded'):
            pdf_data = post_utme_report_pdf(request, post_utme_admission_list, context)
            if pdf_data:
                return pdf_data
            else:
                messages.error(request, 'Error generating PDF')

        return render(request, 'admission/post_utme_admission.html', context)
    
    context = {
        'page_title': 'JAMB Admission Report',
        'form': form,
    }
    return render(request, 'admission/post_utme_report.html', context)


# FUNCTION FOR THE GENERATION OF PDF FOR POST-UTME ADMISSION REPORT
def post_utme_report_pdf(request, post_utme_admission_list, context):
    # Render the HTML content of the PDF template with the filtered queryset
    context['post_utme_admission_list'] = post_utme_admission_list
    html_content = render_to_string('admission/post_utme_admission_pdf.html', context)

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
        response['Content-Disposition'] = 'attachment; filename="post_utme_admission_list.pdf"'  # Set filename for download
        return response
    else:
        # If PDF generation failed, return None
        return None


# Student Acceptance Letter
@login_required(login_url='accounts_login')
def acceptance_letter(request):
    logged_user = request.user
    if not logged_user.profile.role == 'Student':
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    try:
        student = Student.objects.select_related('academic_session').get(candidate=logged_user)
    except Student.DoesNotExist:
        messages.warning(request, 'Student Record Not Found')
        return redirect('accounts_login')

    payments = _verified_fee_payments_for_session(logged_user, student.academic_session)
    # Check for the existence of specific fee payments
    has_screening_fee_payment = payments.filter(payment_source='Screening Fee').exists()
    has_acceptance_fee_payment = payments.filter(payment_source='Acceptance Fee').exists()
    has_caution_fee_payment = payments.filter(payment_source='Caution Fee').exists()
    has_application_fee_payment = payments.filter(payment_source='Application Form').exists()
    has_school_fee_payment = payments.filter(payment_source='School Fees').exists()
    # Check for relevant Fee Payment
    if not has_application_fee_payment or not has_screening_fee_payment or not has_acceptance_fee_payment:
        messages.warning(request, 'You have a Pending Fee Payment')
        return redirect('initiate_payment')
    
    context = {
        'payments': payments,
        'has_caution_fee_payment': has_caution_fee_payment,
        'page_title': 'Acceptance Letter',
        'has_screening_fee_payment': has_screening_fee_payment,
        'has_acceptance_fee_payment': has_acceptance_fee_payment,
        'has_application_fee_payment': has_application_fee_payment,
        'has_school_fee_payment': has_school_fee_payment,
    }
    return render(request, 'admission/acceptance_letter.html', context)


# Student Admission Letter
@login_required(login_url='accounts_login')
def admission_letter(request):
    logged_user = request.user
    if not logged_user.profile.role == 'Student':
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
 
    try:
        student = Student.objects.select_related('candidate').get(candidate=logged_user)
        course = student.course.name
    except Student.DoesNotExist:
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    try:
        admitted_record = Admitted.objects.select_related('applicant').get(applicant=logged_user)
    except Admitted.DoesNotExist:
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
  
    # Fetch all relevant payments in a single query
    payments = Payment.objects.filter(user=logged_user,
                                      verified=True,
                                      status='Approved',
                                      academic_sess=student.academic_session,
                                      payment_source__in=['Acceptance Fee', 'Caution Fee', 'Screening Fee', 'Application Form', 'School Fees']
                                      )
    # Check for the existence of specific fee payments
    has_screening_fee_payment = payments.filter(payment_source='Screening Fee').exists()
    has_acceptance_fee_payment = payments.filter(payment_source='Acceptance Fee').exists()
    has_caution_fee_payment = payments.filter(payment_source='Caution Fee').exists()
    has_application_fee_payment = payments.filter(payment_source='Application Form').exists()
    has_school_fee_payment = payments.filter(payment_source='School Fees').exists()
    # Check for relevant Fee Payment
    if not has_application_fee_payment or not has_screening_fee_payment or not has_acceptance_fee_payment:
        messages.warning(request, 'You have a Pending Fee Payment')
        return redirect('initiate_payment')
    
    # Check Duration of Course
    if 'HND' or 'ND' in course:
        course_duration = '4 Semesters'
    else:
        course_duration = '9 Months'
    context = {
        'has_caution_fee_payment': has_caution_fee_payment,
        'admitted_record': admitted_record,
        'student': student,
        'course_duration': course_duration,
        'payments': payments,
        'page_title': 'Admission Letter',
        'has_screening_fee_payment': has_screening_fee_payment,
        'has_acceptance_fee_payment': has_acceptance_fee_payment,
        'has_application_fee_payment': has_application_fee_payment,
        'has_school_fee_payment': has_school_fee_payment,
    }
    return render(request, 'admission/admission_letter.html', context)


# Student Admission Letter
@login_required(login_url='accounts_login')
def medical_form(request):
    logged_user = request.user
    if not logged_user.profile.role == 'Student':
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    try:
        student = Student.objects.select_related('candidate').get(candidate=logged_user)

    except Student.DoesNotExist:
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    try:
        admitted_record = Admitted.objects.select_related('applicant').get(applicant=logged_user)
    except Admitted.DoesNotExist:
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    # Calculate age
    date_of_birth = logged_user.profile.date_birth
    if date_of_birth:
        today = date.today()
        age = today.year - date_of_birth.year - ((today.month, today.day) < (date_of_birth.month, date_of_birth.day))
    else:
        age = None  # Age is not available
  
    # Fetch all relevant payments in a single query
    payments = _verified_fee_payments_for_session(logged_user, student.academic_session)
    # Check for the existence of specific fee payments
    has_screening_fee_payment = payments.filter(payment_source='Screening Fee').exists()
    has_acceptance_fee_payment = payments.filter(payment_source='Acceptance Fee').exists()
    has_caution_fee_payment = payments.filter(payment_source='Caution Fee').exists()
    has_application_fee_payment = payments.filter(payment_source='Application Form').exists()
    has_school_fee_payment = payments.filter(payment_source='School Fees').exists()
    # Check for relevant Fee Payment
    if not has_application_fee_payment or not has_screening_fee_payment or not has_acceptance_fee_payment:
        messages.warning(request, 'You have a Pending Fee Payment')
        return redirect('initiate_payment')
    
    context = {
        'has_caution_fee_payment':has_caution_fee_payment,
        'age': age,
        'admitted_record': admitted_record,
        'student': student,
        'payments': payments,
        'page_title': 'Medical Form',
        'has_screening_fee_payment': has_screening_fee_payment,
        'has_acceptance_fee_payment': has_acceptance_fee_payment,
        'has_application_fee_payment': has_application_fee_payment,
        'has_school_fee_payment': has_school_fee_payment,
    }
    return render(request, 'admission/medical_form.html', context)


# Student Undertaking to be of good behaviour
@login_required(login_url='accounts_login')
def undertaking_form(request):
    logged_user = request.user
    if not logged_user.profile.role == 'Student':
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    try:
        student = Student.objects.select_related('candidate').get(candidate=logged_user)

    except Student.DoesNotExist:
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    try:
        admitted_record = Admitted.objects.select_related('applicant').get(applicant=logged_user)
    except Admitted.DoesNotExist:
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    # Fetch all relevant payments in a single query
    payments = _verified_fee_payments_for_session(logged_user, student.academic_session)
    # Check for the existence of specific fee payments
    has_screening_fee_payment = payments.filter(payment_source='Screening Fee').exists()
    has_acceptance_fee_payment = payments.filter(payment_source='Acceptance Fee').exists()
    has_caution_fee_payment = payments.filter(payment_source='Caution Fee').exists()
    has_application_fee_payment = payments.filter(payment_source='Application Form').exists()
    has_school_fee_payment = payments.filter(payment_source='School Fees').exists()
    # Check for relevant Fee Payment
    if not has_application_fee_payment or not has_screening_fee_payment or not has_acceptance_fee_payment:
        messages.warning(request, 'You have a Pending Fee Payment')
        return redirect('initiate_payment')

    context = {
        'has_caution_fee_payment': has_caution_fee_payment,
        'admitted_record': admitted_record,
        'student': student,
        'payments': payments,
        'page_title': 'Undertaking Form',
        'has_screening_fee_payment': has_screening_fee_payment,
        'has_acceptance_fee_payment': has_acceptance_fee_payment,
        'has_application_fee_payment': has_application_fee_payment,
        'has_school_fee_payment': has_school_fee_payment,
    }
    return render(request, 'admission/undertaking_form.html', context)


# Student School Fee Schedule
@login_required(login_url='accounts_login')
def school_fee_schedule(request):
    logged_user = request.user
    if logged_user.profile.role not in ['Applicant', 'Candidate', 'Student']:
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    # Calculate age
    date_of_birth = logged_user.profile.date_birth
    if date_of_birth:
        today = date.today()
        age = today.year - date_of_birth.year - ((today.month, today.day) < (date_of_birth.month, date_of_birth.day))
    else:
        age = None  # Age is not available

    schedule_context = _school_fee_context_for_user(logged_user)
    schedule_session = schedule_context.get('academic_session')
    fee_schedule_sections = _build_fee_schedule_sections(logged_user, schedule_context)

    payments = _verified_fee_payments_for_session(logged_user, schedule_session) if schedule_session else Payment.objects.none()
    # Check for the existence of specific fee payments
    has_screening_fee_payment = payments.filter(payment_source='Screening Fee').exists()
    has_acceptance_fee_payment = payments.filter(payment_source='Acceptance Fee').exists()
    has_caution_fee_payment = payments.filter(payment_source='Caution Fee').exists()
    has_application_fee_payment = payments.filter(payment_source='Application Form').exists()
    has_school_fee_payment = payments.filter(payment_source='School Fees').exists()

    if schedule_context.get('message') and not fee_schedule_sections:
        messages.info(request, schedule_context['message'])

    context = {
        'has_caution_fee_payment': has_caution_fee_payment,
        'age': age,
        'admitted_record': schedule_context.get('admitted_record'),
        'student': schedule_context.get('student'),
        'pre_applicant': schedule_context.get('pre_applicant'),
        'application_submission': schedule_context.get('application_submission'),
        'payments': payments,
        'fee_schedule_sections': fee_schedule_sections,
        'schedule_context': schedule_context,
        'schedule_empty_message': schedule_context.get('message') or 'No fee schedule has been configured for your session, category, programme, level, semester, or indigene status yet. Please contact the Bursary or ICT Unit.',
        'page_title': 'School Fee Schedule',
        'has_screening_fee_payment': has_screening_fee_payment,
        'has_acceptance_fee_payment': has_acceptance_fee_payment,
        'has_application_fee_payment': has_application_fee_payment,
        'has_school_fee_payment': has_school_fee_payment,
    }
    return render(request, 'admission/school_fee_schedule.html', context)



# Successfully Submitted Application List by Session
@login_required(login_url='accounts_login')
def submitted_application_list(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.profile.role == 'Admin'  or logged_user.profile.role == 'Super Admin' or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    form = ApplicationReportForm(request.GET or None)
    
    # Log user activity
    UserActivityLog.objects.create(user=logged_user, action='Visited Submitted Application List Page')
    if request.method == "GET" and form.is_valid():
        academic_session = form.cleaned_data['academic_session']
        course_of_study = form.cleaned_data['course_of_study']
        start_date = form.cleaned_data['start_date']
        end_date = form.cleaned_data['end_date']

        # Get the School and Department names for the selected course
        chosen_course = CourseOfStudy.objects.filter(name=course_of_study).first()
        school_name = chosen_course.department.faculty.name
        department_name = chosen_course.department.name

        # Filter Application by date range
        submitted_application_list = ApplicationSubmission.objects.filter(
                academic_sess=academic_session,
                course_name=course_of_study,
                submitted_date__gte=start_date,
                submitted_date__lte=end_date
                )
        
        count_submited_applications = submitted_application_list.count()

        paginator = Paginator(submitted_application_list, 10)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f' Generated Report with the following Details; Programme: {course_of_study}, Academic Session: {academic_session}, Date Range: From {start_date} to {end_date} ')
        context = {
            'department_name': department_name,
            'school_name': school_name,
            'count_submited_applications': count_submited_applications,
            'course_of_study': course_of_study,
            'academic_session': academic_session,
            'submitted_application_list': page_obj,
            'page_title': 'Submitted Application List',
            'start_date': start_date,
            'end_date': end_date,
        }
        return render(request, 'admission/submited_application_list.html', context)
    else:
        form = ApplicationReportForm()
    context = {
        'form': form,
        'page_title': 'Application Report Form',
    }
    return render(request, 'admission/application_report_form.html', context)


# RESET SUBMITTED APPLICATION
@login_required(login_url='accounts_login')
def reset_submited_application(request):
    # Ensure the user is a superuser or has the 'Super Admin' role
    if not (request.user.is_superuser or request.user.profile.role in ['Admin', 'Super Admin']):
        messages.warning(request, 'You do not have permission to view this page.')
        return redirect('accounts_login')
    
    form = SearchSubmitedApplicationForm(request.GET or None)

    if request.method == "GET" and form.is_valid():
        search_query = form.cleaned_data['search_query']
        # Apply additional filters based on the search query
        submited_applicant = ApplicationSubmission.objects.filter(student_id__icontains=search_query).first()
        
        if submited_applicant:
            # Log user activity
            UserActivityLog.objects.create(user=request.user, action=f'Searched for {search_query}')
            context = {
                'submited_applicant': submited_applicant,
                'page_title': 'Reset Applicant',
            }
            return render(request, 'admission/application_reset.html', context)
        else:
            messages.error(request, 'No application found with the provided student ID.')
    else:
        form = SearchSubmitedApplicationForm()
    
    # Common base query for Submitted Applications
    submited_applications = ApplicationSubmission.objects.order_by('-submitted_date')[:5]
    
    number_of_submission = submited_applications.count()
  
    context = {
        'page_title': 'Reset Submitted Application',
        'submited_applications': submited_applications,
        'number_of_submission': number_of_submission,
        'form': form,
    }
    return render(request, 'admission/submited_applicants.html', context)


# View Submitted Applicant Details
class ApplicationSubmissionDetailView(LoginRequiredMixin, UserPassesTestMixin, DetailView):
    model = ApplicationSubmission
    template_name = 'admission/application_reset.html'  
    context_object_name = 'submited_applicant'  # Correct context object name

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'Super Admin' or self.request.user.profile.role == 'Admin' or self.request.user.is_superuser 

    def dispatch(self, request, *args, **kwargs):
        # Log user activity
        UserActivityLog.objects.create(user=self.request.user, action='Visited Application Submission Detail View.')
        return super().dispatch(request, *args, **kwargs)
    
    # Optionally, you can override the context data to include additional information
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = 'Application Submission Detail'
        return context

    
# RESET SUBMITTED APPLICATION FORM
class ApplicationResetView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = ApplicationSubmission
    template_name = 'admission/confirm_reset_submission.html'
    success_url = reverse_lazy('reset_submited_application')
    context_object_name = 'applicant'  # the context object name

    # Define a custom test function to check if the user is a superuser or has the role 'Super Admin'
    def test_func(self):
        return self.request.user.profile.role == 'Super Admin' or self.request.user.profile.role == 'Admin' or self.request.user.is_superuser

    def get_queryset(self):
        queryset = super().get_queryset()
        applicant_id = self.kwargs.get('pk')
        if applicant_id:
            applicant = ApplicationSubmission.objects.get(pk=applicant_id)
            academic_sess = applicant.academic_sess
            queryset = queryset.filter(academic_sess=academic_sess)
        return queryset

    def delete(self, request, *args, **kwargs):
        applicant = self.get_object()
        academic_sess = applicant.academic_sess
        
        if AcademicSession.objects.filter(name=academic_sess.name).exists():
            messages.success(request, 'Application Form Reset successfully.')
            # Log user activity
            UserActivityLog.objects.create(user=self.request.user, action=f'Reset Application Form for {applicant.applicant.profile.first_name} {applicant.applicant.profile.last_name}')
            return super().delete(request, *args, **kwargs)
        else:
            messages.error(request, 'Cannot reset application form as it does not belong to a valid academic session.')
            return redirect('submitted_applications_for_reset')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Reset Application Form'
        return context


# RESET SUBMITTED APPLICATION
@login_required(login_url='accounts_login')
def change_pre_application_programme(request):
    # Ensure the user is a superuser or has the 'Super Admin' role
    if not (request.user.is_superuser or request.user.profile.role in ['Admin', 'Super Admin']):
        messages.warning(request, 'You do not have permission to view this page.')
        return redirect('accounts_login')
    
    # Log user activity
    UserActivityLog.objects.create(user=request.user, action='Visited Search for Pre-Applicant in order to Change Programme of Choice Page')
    # Common base query for Submitted Applications
    pre_applications = PreApplicant.objects.order_by('-added_date')
    # EXPLODE NOT SUBMITTED APPLICANTS
    pre_applications = pre_applications.annotate(
        has_submission=Exists(
            ApplicationSubmission.objects.filter(applicant_id=OuterRef('applicant_id'))
        )
    ).filter(has_submission=False)
    
    number_of_applicants = pre_applications.count()

    form = SearchSubmitedApplicationForm(request.GET or None)

    if request.method == "GET" and form.is_valid():
        search_query = form.cleaned_data['search_query']
        # Apply additional filters based on the search query
        pre_applicant = pre_applications.filter(
            application_number__icontains=search_query
            
        )
        # Log user activity
        UserActivityLog.objects.create(user=request.user, action=f'Searched for Applicant with the {search_query} Application ID. ')
        context = {
            'form': form,
            'pre_applicants': pre_applicant,
            'page_title': 'Pre-Applicant Detail',
        }
        return render(request, 'admission/pre_applicants.html', context)

    paginator = Paginator(pre_applications, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Pre-Application List',
        'pre_applicants': page_obj,
        'number_of_submission': number_of_applicants,
        'form': form,
    }
    return render(request, 'admission/pre_applicants.html', context)


# View Submitted Applicant Details
class PreApplicationDetailView(LoginRequiredMixin, UserPassesTestMixin, DetailView):
    model = PreApplicant
    template_name = 'admission/pre_applicant.html'  

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'Super Admin' or self.request.user.profile.role == 'Admin' or self.request.user.is_superuser 

    def dispatch(self, request, *args, **kwargs):
        pre_applicant = self.get_object()  # Retrieve the pre-applicant instance
        # Log user activity
        UserActivityLog.objects.create(user=self.request.user, action=f'Viewed Pre-Application Detail for {pre_applicant.first_name} {pre_applicant.last_name}')
        return super().dispatch(request, *args, **kwargs)
    
    # Optionally, you can override the context data to include additional information
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = 'Pre-Application Detail'
        # Get the most recently added AcademicSession and add it to context
        recent_academic_session = AcademicSession.objects.order_by('-start_date', '-end_date', '-name').first()
        context['recent_academic_session'] = recent_academic_session
        return context

    
# EDIT PRE-APPLICATION FORM
class PreApplicantEditView(View):
    template_name = 'admission/edit_pre_application.html'

    # Restrict access to only Super Admin and superuser
    def test_func(self):
        return self.request.user.profile.role == 'Super Admin' or self.request.user.is_superuser 

    def get(self, request, *args, **kwargs):
        applicant_id = kwargs.get('applicant_id')
        pre_applicant = get_object_or_404(PreApplicant, pk=applicant_id)
        user = pre_applicant.applicant
        initial_email = user.email  # Fetch initial email from user instance
        form = PreApplicationEditForm(instance=pre_applicant, initial={'email': initial_email})
        # Log user activity
        UserActivityLog.objects.create(user=self.request.user, action=f'Visited Edite Pre-Applicant page for: {pre_applicant.first_name} {pre_applicant.last_name}')
        page_title = 'Edit Pre-Applicant'
        return render(request, self.template_name, {'form': form, 'page_title': page_title})

    def post(self, request, *args, **kwargs):
        applicant_id = kwargs.get('applicant_id')
        pre_applicant = get_object_or_404(PreApplicant, pk=applicant_id)
        form = PreApplicationEditForm(request.POST, instance=pre_applicant)
        if form.is_valid():
            user = pre_applicant.applicant
            user.email = form.cleaned_data['email']
            user.username = form.cleaned_data['email']  # Set username as email
            user.save()
            form.save()
            messages.success(request, 'Pre-Applicant Edited Successfully.')
            # Log user activity
            UserActivityLog.objects.create(user=self.request.user, action=f'Edited Pre-Applicant: {pre_applicant.first_name} {pre_applicant.last_name}')
            return redirect('change_pre_application_programme')  # Redirect to success URL
        page_title = 'Edit Pre-Applicant'
        return render(request, self.template_name, {'form': form, 'page_title': page_title})
    

@login_required(login_url='accounts_login')
def super_admin_create_applicant(request): 
    # Check for existence of Course of study
    application_fee = ApplicationFee.objects.exists()
    programme = CourseOfStudy.objects.exists()
    logged_user = request.user
    if not (logged_user.profile.role == 'Super Admin' or logged_user.profile.role == 'ADMISSION-OFFICER1' or  logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.profile.role == 'Admin' or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    # Get all the user activity logs
    UserActivityLog.objects.create(user=logged_user, action='Visited Create Applicant Page')

    if request.method == 'POST':
        register_applicant = StaffUserCreation(request.POST)
        email_form = ApplicantValidationForm(request.POST)
        pre_application_form = PreApplicationForm(request.POST)
        if register_applicant.is_valid() and email_form.is_valid() and pre_application_form.is_valid():
            try:
                with transaction.atomic():
                    
                    # Save the updated profile
                    user = register_applicant.save()
                    # Save the form data to PreApplicant model
                    programme_choice = pre_application_form.cleaned_data['course_name']
                    pre_applicant = pre_application_form.save(commit=False)
                    pre_applicant.applicant = user
                    # Get values for ApplicantValidation model
                    email = email_form.cleaned_data['email']
                    email_validation = ApplicantValidation.objects.create(email=email,
                                                                          is_validated=True,
                                                                          applicant=user)
                    
                    # Generate and save an application number
                    application_number = str(uuid.uuid4())[:6].upper()  
                        
                    # Get the current date in the format YYYYMMDD
                    current_date = datetime.now().strftime("%Y%m%d")
                        
                    # Assign the combined Application Number to the DB field
                    pre_applicant.application_number = f"{current_date}{application_number}"

                    # Get current timestamp as a string
                    timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")

                    # Generate a unique identifier using a secure hashing algorithm
                    unique_identifier = hashlib.sha256(timestamp_str.encode('utf-8')).hexdigest()

                    # Combine the timestamp and unique identifier to form the invoice number
                    invoice_number = f"FEDPOLY-WANNUNE-{unique_identifier[:6]}-{timestamp_str}"

                    # Update the invoice number of the PreApplication Model
                    pre_applicant.invoice_number = invoice_number

                    # Get newly created Applicant's Profile
                    profile = user.profile
                    # Assign profile variable
                    profile.first_name = pre_application_form.cleaned_data['first_name']
                    profile.middle_name = pre_application_form.cleaned_data['middle_name'] 
                    profile.last_name = pre_application_form.cleaned_data['last_name']
                    profile.role = 'Applicant'
                    # Save Pre-Applicant form
                    pre_applicant.save()
                    # Save Profile Form
                    profile.save()
                    # Log user activity
                    UserActivityLog.objects.create(user=logged_user, action=f'Created Pre-Application Form for : {user.profile.first_name} {user.profile.last_name}, Programme of Choice: {programme_choice} ')
                    messages.success(request, 'Application for Admission created Successfully. Proceed to Payment.')
                    return redirect('pre_app_print_out', user_id=pre_applicant.applicant.id)      
            except Exception as e:
                messages.error(request, f'An error occurred: {str(e)}')
    else:
        register_applicant = StaffUserCreation()
        email_form = ApplicantValidationForm()
        pre_application_form = PreApplicationForm()
    context = {
        'register_applicant': register_applicant,
        'programme': programme,
        'application_fee': application_fee,
        'email_form': email_form,
        'pre_application_form': pre_application_form,
        'page_title': 'Special Application Form',
    }

    return render(request, 'admission/admin_create_applicant.html', context)


# Super Admin Create New Student
@login_required(login_url='accounts_login')
def super_admin_create_student(request):
    # Check for the existence of ApplicationFee and CourseOfStudy objects
    application_fee = ApplicationFee.objects.exists()
    programme = CourseOfStudy.objects.exists()
    logged_user = request.user
    
    # Verify if the logged-in user has the appropriate role to access this view
    if not (logged_user.profile.role == 'Super Admin' or 
            logged_user.profile.role == 'ADMISSION-OFFICER1' or  
            logged_user.profile.role == 'ADMISSION-OFFICER' or 
            logged_user.profile.role == 'Admin' or 
            logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    # Log the user's activity of visiting the Create Applicant page
    UserActivityLog.objects.create(user=logged_user, action='Visited Add New Student Page')

    if request.method == 'POST':
        # Initialize forms with POST data
        register_student_application = StaffUserCreation(request.POST)
        email_form = ApplicantValidationForm(request.POST)
        student_creation_form = StudentCreationForm(request.POST)
        student_profile_form = StudentProfileForm(request.POST, request.FILES)
        student_id_level_form = StudentLevelForm(request.POST)
        
        # Validate all forms
        if (register_student_application.is_valid() and 
            student_profile_form.is_valid() and 
            student_id_level_form.is_valid() and 
            email_form.is_valid() and 
            student_creation_form.is_valid()):
            try:
                with transaction.atomic():
                    # Extract cleaned data from forms
                    student_id = student_id_level_form.cleaned_data['student_id']
                    student_level = student_id_level_form.cleaned_data['level']
                    semester = student_id_level_form.cleaned_data['semester']
                    
                    # Save the user (register the student)
                    user = register_student_application.save()
                    
                    # Save PreApplicant model
                    programme_choice = student_creation_form.cleaned_data['course_name']
                    academic_session = student_creation_form.cleaned_data['academic_session']
                    
                    pre_applicant_student = student_creation_form.save(commit=False)
                    pre_applicant_student.applicant = user
                    
                    # Create an ApplicantValidation record
                    email = email_form.cleaned_data['email']
                    ApplicantValidation.objects.create(email=email, is_validated=True, applicant=user)
                    
                    # Generate a unique invoice number
                    timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
                    unique_identifier = hashlib.sha256(timestamp_str.encode('utf-8')).hexdigest()
                    invoice_number = f"FEDPOLY-WANNUNE-{unique_identifier[:6]}-{timestamp_str}"
                    pre_applicant_student.invoice_number = invoice_number
                    pre_applicant_student.application_number = student_id
                    
                    # Update and save the applicant's profile
                    profile = user.profile
                    profile.first_name = student_creation_form.cleaned_data['first_name']
                    profile.middle_name = student_creation_form.cleaned_data['middle_name']
                    profile.last_name = student_creation_form.cleaned_data['last_name']
                    profile.phone_number = student_creation_form.cleaned_data['phone_number']
                    profile.date_birth = student_profile_form.cleaned_data['date_birth']
                    profile.gender = student_profile_form.cleaned_data['gender']
                    profile.religion = student_profile_form.cleaned_data['religion']
                    profile.marital_status = student_profile_form.cleaned_data['marital_status']
                    profile.Number_children = student_profile_form.cleaned_data['Number_children']
                    profile.image = student_profile_form.cleaned_data['image']
                    profile.role = 'Student'
                    
                    pre_applicant_student.save()
                    profile.save()
                    
                    # Create a new Student record
                    Student.objects.create(student_id=student_id,
                                           candidate=user,
                                           academic_session=academic_session,
                                           course=programme_choice,
                                           semester=semester,
                                           current_level=student_level,
                                           admitted_by=logged_user)
                    Admitted.objects.create(applicant=user,
                                            academic_session=academic_session,
                                            admitted_course=programme_choice,
                                            semester=semester,
                                            admitted_level=student_level)
                    # Log the user activity
                    UserActivityLog.objects.create(user=logged_user, action=f'Added New Student : {user.profile.first_name} {user.profile.last_name}, Programme of Choice: {programme_choice} ')
                    messages.success(request, 'Student Added Successfully.')
                    
                    # Redirect to the add student contact view with the correct argument
                    return redirect('admin_add_student_contact', student_id=pre_applicant_student.applicant.id)
            except Exception as e:
                messages.error(request, f'An error occurred: {str(e)}')
    else:
        # Initialize empty forms for GET request
        register_student_application = StaffUserCreation()
        email_form = ApplicantValidationForm()
        student_creation_form = StudentCreationForm()
        student_id_level_form = StudentLevelForm()
        student_profile_form = StudentProfileForm()
        
    context = {
        'register_student_application': register_student_application,
        'programme': programme,
        'application_fee': application_fee,
        'student_profile_form': student_profile_form,
        'email_form': email_form,
        'student_creation_form': student_creation_form,
        'student_id_level_form': student_id_level_form,
        'page_title': f'{logged_user.profile.role} Add Student',
    }

    return render(request, 'admission/admin_create_student.html', context)


# Admin Add Student Contact view
@login_required(login_url='accounts_login')
def admin_add_student_contact(request, student_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    
    # Check if the user has a profile
    try:
        student_application = PreApplicant.objects.select_related('applicant').get(applicant_id=student_id)
        applicant_profile = student_application.applicant.profile
        # Check if student profile contact is completed
        check_profile = profile_contact_complete(applicant_profile)
        if check_profile:
            return redirect('admin_add_student_sponsor', student_id)
        
        # Check if the form request method is POST
        if request.method == 'POST':
            # Get the form and assign it to the logged-in user profile
            form = ApplicantContactForm(request.POST, request.FILES, instance=applicant_profile)
            if form.is_valid():
                # Update profile fields directly using form.cleaned_data
                applicant_profile.home_address = form.cleaned_data['home_address']
                applicant_profile.state = form.cleaned_data['state']
                applicant_profile.nationality = form.cleaned_data['nationality']
                applicant_profile.lga = form.cleaned_data['lga']

                # Save the changes
                applicant_profile.save()
                UserActivityLog.objects.create(user=logged_user, action=f'Added Contact Details for Student:  {student_application.applicant.profile.first_name} {student_application.applicant.profile.last_name} Student.')
                # Display success message
                messages.success(request, f' {student_application.applicant.profile.first_name} {student_application.applicant.profile.last_name} Contact Details Successfully Saved')
                return redirect('admin_add_student_sponsor', student_id=student_application.applicant.id)
        else:
            form = ApplicantContactForm()

        context = {
            'form': form,
            'page_title': f'Admin add {student_application.applicant.profile.last_name} Contact',
        }
        return render(request, 'admission/applicant_contact.html', context)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')

# Admin ADD STUDENT SPONSOR
@login_required(login_url='accounts_login')
def admin_add_student_sponsor(request, student_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')

    # Check if the user has a profile
    try:
        applicant = PreApplicant.objects.select_related('applicant').get(applicant_id=student_id)
        pre_applicant_candidate = applicant.applicant
        
        # Check if Student Sponsor information is completed
        student_sponsor = get_sponsor_details(pre_applicant_candidate)
        if sponsor_details_contact_complete(student_sponsor):
            return redirect('admin_add_student_education', student_id)

        form = SponsorDetailsForm(request.POST or None)

        if request.method == 'POST':
            if form.is_valid():
                with transaction.atomic():
                    full_name = form.cleaned_data['full_name']
                    occupation = form.cleaned_data['occupation']
                    phone_number = form.cleaned_data['phone_number']
                    email = form.cleaned_data['email']
                    home_address = form.cleaned_data['home_address']
                    sponsor_relationship = form.cleaned_data['sponsor_relationship']

                    # Create Applicant's Sponsor instance
                    sponsor = SponsorDetails.objects.create(
                        applicant=pre_applicant_candidate,
                        full_name=full_name,
                        occupation=occupation,
                        phone_number=phone_number,
                        email=email,
                        home_address=home_address,
                        sponsor_relationship=sponsor_relationship
                    )

                    if sponsor:
                        
                        messages.success(request, 'Sponsor Information Added Successfully.')
                        return redirect('admin_add_student_education', student_id)
                    else:
                        UserActivityLog.objects.create(user=logged_user, action=f'Added Contact Details for Student:  {applicant.applicant.profile.first_name} {applicant.applicant.profile.last_name} Student.')
                        messages.success(request, 'Sponsor Information Added Successfully.')
        else:
            form = SponsorDetailsForm()
        context = {
            'page_title': f'Admin Add {applicant.applicant.profile.last_name} Sponsor',
            'form': form,  # Include the form in the context for rendering in the template
        }

        return render(request, 'admission/admin_add_applicant_sponsor.html', context)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')


# Admin add STUDENT Education
@login_required(login_url='accounts_login')
def admin_add_student_education(request, student_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    
    # Check if the user has a profile
    try:
        applicant = PreApplicant.objects.select_related('applicant').get(applicant_id=student_id)
        pre_applicant_candidate = applicant.applicant
        applicant_profile = applicant.applicant.profile
        # Check if student Education Records are filled and redirect
        # check_education = get_education(pre_applicant_candidate)
        # if applicant_education_complete(check_education):
        #     return redirect('admin_add_student_result', student_id)
        
        # Check if the form request method is POST
        schools = EducationRecord.objects.select_related('applicant').filter(applicant=pre_applicant_candidate).order_by('-added_date')
        form = EducationRecordForm(request.POST or None)
        if request.method == 'POST':
            if form.is_valid():
                with transaction.atomic():
                    school_name = form.cleaned_data['school_name']
                    certificate = form.cleaned_data['certificate']
                    # Convert admitted_year and end_year to integers
                    admitted_year = int(form.cleaned_data['admitted_year'])
                    end_year = int(form.cleaned_data['end_year'])
                    
                    education = EducationRecord.objects.create(
                        applicant=pre_applicant_candidate,
                        school_name=school_name,
                        certificate=certificate,
                        admitted_year=admitted_year, end_year=end_year)
                    if education:
                        messages.success(request, 'Record Added Successfully.')
        else:
            form = EducationRecordForm()
        context = {
            'student_id': student_id,
            'schools': schools,
            'form': form,
            'page_title': f'{applicant.applicant.profile.last_name} Education',
        }
        return render(request, 'admission/admin_add_student_edu.html', context)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')


# Admin add STUDENT O LEVEL RESULT
@login_required(login_url='accounts_login')
def admin_add_student_result(request, student_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    
    # Check if the user has a profile
    try:
        applicant = PreApplicant.objects.select_related('applicant').get(applicant_id=student_id)
        pre_applicant_candidate = applicant.applicant
        # Check if Student Exam Result are filled
        # student_result = get_result(pre_applicant_candidate)
        # if applicant_result_complete(student_result):
        #     return redirect('admin_preview_student_form', student_id)
        
        results = ApplicantResult.objects.select_related('applicant').filter(applicant=pre_applicant_candidate).order_by('-added_date')
        form = ApplicantResultForm(request.POST or None)
        if request.method == 'POST':
            if form.is_valid():
                with transaction.atomic():
                    subject_name = form.cleaned_data['subject_name']
                    result_name = form.cleaned_data['result_name']
                    subject_grade = form.cleaned_data['subject_grade']
                    exam_number = form.cleaned_data['exam_number']
                    exam_year = form.cleaned_data['exam_year']
                    
                    exam_result = ApplicantResult.objects.create(
                        applicant=pre_applicant_candidate,
                        subject_name=subject_name,
                        result_name=result_name,
                        subject_grade=subject_grade,
                        exam_number=exam_number,
                        exam_year=exam_year
                        )
                    if exam_result:
                        messages.success(request, 'Record Added Successfully.')
        else:
            form = ApplicantResultForm()
        
        context = {
            'student_id': student_id,
            'form': form,
            'results': results,
            'page_title': f'{applicant.applicant.profile.last_name} O Level Result',
        }
        return render(request, 'admission/admin_add_student_result.html', context)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')


# Admin Search Applicant and Continue Application
@login_required(login_url='accounts_login')
def admin_search_continue_application(request):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    form = SearchPreapplicantForm(request.POST or None)
    # Get the latest academic session
    latest_academic_session = AcademicSession.objects.order_by('-start_date', '-end_date', '-name').first()
    # Latest Academic Session Preapplicants
    pre_applicants = PreApplicant.objects.all().order_by('-added_date')

    for applicant in pre_applicants:
        applicant.has_paid_application_fee = PreApplicant.has_paid_application_form(applicant.applicant)


    if request.method == 'POST':
        
        if form.is_valid():
            search_query = form.cleaned_data['search_query']
            academic_session = form.cleaned_data['academic_session']
            
            # Apply additional filters based on the search query
            filtered_pre_applicants = pre_applicants.filter(
                Q(applicant__username__icontains=search_query) |
                
                    Q(applicant__profile__first_name=search_query) |
                    Q(applicant__profile__middle_name__icontains=search_query) |
                    Q(applicant__username=search_query) |  # Only include middle name if it exists
                    Q(applicant__profile__last_name__icontains=search_query)
               
            ).filter(
                academic_session=academic_session
            )
            UserActivityLog.objects.create(user=logged_user, action=f'Searched for Applicant; {search_query}')  
            if filtered_pre_applicants:
                for applicant in filtered_pre_applicants:
                    applicant.has_paid_application_fee = PreApplicant.has_paid_application_form(applicant.applicant)
                paginator = Paginator(filtered_pre_applicants, 5)
                page_number = request.GET.get('page')
                page_obj = paginator.get_page(page_number)

                context = {
                    'form': form,
                    'pre_applicants': page_obj,
                    'page_title': 'Search Applicant',

                }
                return render(request, 'admission/search_applicant.html', context)
            else:
                messages.error(request, f'{search_query} Candidate for {academic_session} Academic Session not found')
        else:
            messages.warning(request, 'Something Went Wrong with your Search')
    
    else:
        form = SearchPreapplicantForm()
    
    # Pagination with search query included
    paginator = Paginator(pre_applicants, 10)

    # Get page number with fallback for missing page parameter
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    context = {
        'pre_applicants': page_obj,
        'form': form,
        'page_title': 'Search & Continue Application',
    }
    return render(request, 'admission/search_applicant.html', context)


# Admin Search Applicant and Continue STUDENT REGISTRATION
@login_required(login_url='accounts_login')
def admin_search_continue_registration(request):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    
    # Get the latest academic session
    latest_academic_session = AcademicSession.objects.order_by('-start_date', '-end_date', '-name').first()
    # Latest Academic Session Preapplicants
    pre_registration = Student.objects.select_related('candidate').filter(academic_session=latest_academic_session).order_by('-added_date')

   
    if request.method == 'POST':
        form = SearchPreapplicantForm(request.POST)
        if form.is_valid():
            search_query = form.cleaned_data['search_query']
            academic_session = form.cleaned_data['academic_session']
            
            # Apply additional filters based on the search query
            filtered_pre_registration = Student.objects.filter(
                Q(candidate__username__icontains=search_query) |
                
                    Q(candidate__profile__first_name=search_query) |
                    Q(candidate__profile__middle_name__icontains=search_query) |
                    Q(candidate__username=search_query) |  # Only include middle name if it exists
                    Q(candidate__profile__last_name__icontains=search_query)
               
            ).filter(
                academic_session=academic_session
            )
            UserActivityLog.objects.create(user=logged_user, action=f'Searched for Applicant; {search_query}')  
            if filtered_pre_registration:
                paginator = Paginator(filtered_pre_registration, 5)
                page_number = request.GET.get('page')
                page_obj = paginator.get_page(page_number)

                context = {
                    'form': form,
                    'pre_applicants': page_obj,
                    'page_title': 'Search Student',

                }
                return render(request, 'admission/search_continue_reg.html', context)
            else:
                messages.error(request, f'Student; {search_query}  for {academic_session} Academic Session not found')
        else:
            messages.warning(request, 'Something Went Wrong with your Search')
    
    else:
        form = SearchPreapplicantForm()
    
    # Pagination with search query included
    paginator = Paginator(pre_registration, 10)

    # Get page number with fallback for missing page parameter
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    context = {
        'pre_registration': page_obj,
        'form': form,
        'page_title': 'Search & Continue Registration',
    }
    return render(request, 'admission/search_continue_reg.html', context)


# Admin Update Applicant Profile
@login_required(login_url='accounts_login')
def admin_update_applicant_profile(request, applicant_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    
    # Get the latest academic session
    latest_academic_session = AcademicSession.objects.order_by('-start_date', '-end_date', '-name').first()
    

    # Get the Applicant
    try:
        applicant = PreApplicant.objects.select_related('applicant', 'academic_session').get(applicant_id=applicant_id)
        pre_applicant_candidate = applicant.applicant

        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Visited {applicant.applicant.profile.first_name} {applicant.applicant.profile.last_name} Applicant Update Profile Page')
        profile = get_profile(pre_applicant_candidate)
        if profile_complete(profile):
            return redirect('admin_add_applicant_contact', applicant_id)
        # Check for Application Form payment for the current Academic Session
        applicant.has_paid_application_fee = applicant.has_paid_application_form(pre_applicant_candidate)
        if not applicant.has_paid_application_fee:
            messages.warning(request, f'Applicant Has Not Paid for Application Form for {applicant.academic_session} session ')
            return redirect('admin_search_continue_application')
        
        # Get applicant Profile
        profile = applicant.applicant.profile
        if request.method == 'POST':
            form = ApplicantProfileForm(request.POST, request.FILES, instance=profile)
            
            if form.is_valid():
                applicant_first_name = form.cleaned_data['first_name']
                applicant_middle_name = form.cleaned_data['middle_name']
                applicant_last_name = form.cleaned_data['last_name']
                # Update the Pre_applicant Model
                applicant.first_name = applicant_first_name
                applicant.middle_name = applicant_middle_name
                applicant.last_name = applicant_last_name
                applicant.save()
                form.save()
                UserActivityLog.objects.create(user=logged_user, action=f'Updated {applicant.applicant.profile.first_name} {applicant.applicant.profile.last_name} Applicant Profile')
                messages.success(request, f'{applicant_first_name} {applicant_last_name} Profile Saved Successfully.')
                return redirect('admin_add_applicant_contact', applicant_id=applicant.applicant.id )
        else:
            form = ApplicantProfileForm(instance=profile)

        context = {
            'form': form,
            'page_title': 'Admin Update Applicant Profile',
        }
        return render(request, 'accounts/admin_add_app_contact.html', context)

    except PreApplicant.DoesNotExist:
        messages.warning(request, 'Pre-Applicant Does Not Exist')
        return redirect('accounts_login')
   

# Admin Add Applicant Contact view
@login_required(login_url='accounts_login')
def admin_add_applicant_contact(request, applicant_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
        # Check if the user has a profile
    try:
        applicant = PreApplicant.objects.select_related('applicant', 'academic_session').get(applicant_id=applicant_id)
        pre_applicant_candidate = applicant.applicant
        applicant_profile = applicant.applicant.profile
        # Check whether Applicant has already add contact details
        if profile_contact_complete(applicant_profile):
            messages.success(request, 'Applicant has Already Add Contact Details')
            return redirect('admin_add_applicant_education', applicant_id=applicant.applicant.id)
        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Visited {applicant.applicant.profile.first_name} {applicant.applicant.profile.last_name} Applicant Add Contact Details Page')
        # Check for Application Form payment for the current Academic Session
        applicant.has_paid_application_fee = applicant.has_paid_application_form(pre_applicant_candidate)
        if not applicant.has_paid_application_fee:
            messages.warning(request, f'Applicant Has Not Paid for Application Form for {applicant.academic_session} session ')
            return redirect('admin_search_continue_application')
        # Check if the form request method is POST
        if request.method == 'POST':
            # Get the form and assign it to the logged-in user profile
            form = ApplicantContactForm(request.POST, request.FILES, instance=applicant_profile)
            if form.is_valid():
                # Update profile fields directly using form.cleaned_data
                applicant_profile.home_address = form.cleaned_data['home_address']
                applicant_profile.state = form.cleaned_data['state']
                applicant_profile.nationality = form.cleaned_data['nationality']
                applicant_profile.lga = form.cleaned_data['lga']

                # Save the changes
                applicant_profile.save()
                UserActivityLog.objects.create(user=logged_user, action=f'Added Contact Details for  {applicant.applicant.profile.first_name} {applicant.applicant.profile.last_name} Applicant.')
                # Display success message
                messages.success(request, 'Contact Details Successfully Saved')
                return redirect('admin_add_applicant_education', applicant_id=applicant.applicant.id)
        else:
            form = ApplicantContactForm()

        context = {
            'form': form,
            'page_title': 'Admin add Applicant Contact',
        }
        return render(request, 'admission/applicant_contact.html', context)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')
    

# Admin add Applicant Education
@login_required(login_url='accounts_login')
def admin_add_applicant_education(request, applicant_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
        # Check if the user has a profile
    try:
        applicant = PreApplicant.objects.select_related('applicant', 'academic_session').get(applicant_id=applicant_id)
        pre_applicant_candidate = applicant.applicant
        applicant_profile = applicant.applicant.profile
        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Visited {applicant.applicant.profile.first_name} {applicant.applicant.profile.last_name} Applicant Add Education Record Page')
        # Check for Application Form payment for the current Academic Session
        applicant.has_paid_application_fee = applicant.has_paid_application_form(pre_applicant_candidate)
        if not applicant.has_paid_application_fee:
            messages.warning(request, f'Applicant Has Not Paid for Application Form for {applicant.academic_session} session ')
            return redirect('admin_search_continue_application')
        # Check if the form request method is POST
        schools = EducationRecord.objects.select_related('applicant').filter(applicant=pre_applicant_candidate).order_by('-added_date')
        form = EducationRecordForm(request.POST or None)
        if request.method == 'POST':
            if form.is_valid():
                with transaction.atomic():
                    school_name = form.cleaned_data['school_name']
                    certificate = form.cleaned_data['certificate']
                    # Convert admitted_year and end_year to integers
                    admitted_year = int(form.cleaned_data['admitted_year'])
                    end_year = int(form.cleaned_data['end_year'])
                    
                    education = EducationRecord.objects.create(
                        applicant=pre_applicant_candidate,
                        school_name=school_name,
                        certificate=certificate,
                        admitted_year=admitted_year, end_year=end_year)
                    if education:
                        messages.success(request, 'Record Added Successfully.')
        else:
            form = EducationRecordForm()
        context = {
            'applicant_id': applicant_id,
            'schools': schools,
            'form': form,
            'page_title': 'Admin Add Applicant Education',
        }
        return render(request, 'admission/admin_add_app_edu.html', context)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')


# Admin UPDATE Applicant Education RECORDS
@login_required(login_url='accounts_login')
def admin_update_applicant_education_records(request, applicant_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    
    # Check if the user has a profile
    try:
        applicant = PreApplicant.objects.select_related('applicant').get(applicant_id=applicant_id)
        pre_applicant_candidate = applicant.applicant
        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Visited {applicant.applicant.profile.first_name} {applicant.applicant.profile.last_name} Applicant Update Education Records Page')
        
        # Check if the form request method is POST
        schools = EducationRecord.objects.select_related('applicant').filter(applicant=pre_applicant_candidate).order_by('-added_date')
        
        context = {
            'applicant_id': applicant_id,
            'schools': schools,
            'page_title': 'Admin Update Applicant Education Record',
        }
        return render(request, 'admission/admin_add_app_edu.html', context)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')


# Admin Update Applicant Education
@login_required(login_url='accounts_login')
def admin_update_applicant_education(request, id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    # Check if the user has a profile
    try:
        applicant_education = EducationRecord.objects.select_related('applicant').get(id=id)
        
        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Visited {applicant_education.applicant.profile.first_name} {applicant_education.applicant.last_name} Applicant Add Education Record Page')
        
        
        form = EducationRecordForm(request.POST, request.FILES, instance =applicant_education )
        if request.method == 'POST':
            if form.is_valid():
                with transaction.atomic():
                    school_name = form.cleaned_data['school_name']
                    certificate = form.cleaned_data['certificate']
                    # Convert admitted_year and end_year to integers
                    admitted_year = int(form.cleaned_data['admitted_year'])
                    end_year = int(form.cleaned_data['end_year'])
                    
                    applicant_education.school_name=school_name
                    applicant_education.certificate=certificate
                    applicant_education.admitted_year=admitted_year
                    applicant_education.end_year=end_year
                    applicant_education.save()
                    
                    messages.success(request, f' {applicant_education.applicant.profile.first_name} {applicant_education.applicant.profile.last_name} Education Updated Successfully.')
                    return redirect('admin_add_applicant_education', applicant_id=applicant_education.applicant.id)
        else:
            form = EducationRecordForm(instance =applicant_education)
        context = {
            
            'form': form,
            'page_title': 'Admin Update Applicant Education',
        }
        return render(request, 'admission/admin_add_app_edu.html', context)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')

# Admin Update Applicant Education
@login_required(login_url='accounts_login')
def admin_update_student_education(request, student_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    # Check if the user has a profile
    try:
        applicant_education = EducationRecord.objects.select_related('applicant').get(id=student_id)
        
        
        form = EducationRecordForm(request.POST, request.FILES, instance =applicant_education )
        if request.method == 'POST':
            if form.is_valid():
                with transaction.atomic():
                    school_name = form.cleaned_data['school_name']
                    certificate = form.cleaned_data['certificate']
                    # Convert admitted_year and end_year to integers
                    admitted_year = int(form.cleaned_data['admitted_year'])
                    end_year = int(form.cleaned_data['end_year'])
                    
                    applicant_education.school_name=school_name
                    applicant_education.certificate=certificate
                    applicant_education.admitted_year=admitted_year
                    applicant_education.end_year=end_year
                    applicant_education.save()
                    
                    messages.success(request, f' {applicant_education.applicant.profile.first_name} {applicant_education.applicant.profile.last_name} Education Updated Successfully.')
                    return redirect('admin_add_student_education', student_id=applicant_education.applicant.id)
        else:
            form = EducationRecordForm(instance =applicant_education)
        context = {
            
            'form': form,
            'page_title': f'Update {applicant_education.applicant.profile.last_name} Education',
        }
        return render(request, 'admission/admin_add_student_edu.html', context)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')


# Admin Delete Applicant Education
@login_required(login_url='accounts_login')
def admin_delete_applicant_education(request, id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    # Fetch the education record to be deleted or return 404 if not found
    applicant_education = get_object_or_404(EducationRecord, id=id)
    
    try:
        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Visited {applicant_education.applicant.profile.first_name} {applicant_education.applicant.last_name} Applicant Add Education Record Page')
        
        # Delete the education record
        applicant_education.delete()
        
        UserActivityLog.objects.create(user=logged_user, action=f'Deleted {applicant_education.applicant.profile.first_name} {applicant_education.applicant.last_name} Education Application Record.')
        
        messages.success(request, f'{applicant_education.applicant.profile.first_name} {applicant_education.applicant.profile.last_name} Education Record Deleted Successfully.')
        
        # Redirect to the admin_add_applicant_education view with the applicant's ID
        return redirect('admin_add_applicant_education', applicant_id=applicant_education.applicant.id)
    
    except Exception as e:
        # Handle the case where an exception occurs during deletion
        messages.error(request, f'Something went wrong: {str(e)}')
        return redirect('accounts_login')
    

# Admin Delete Applicant Education
@login_required(login_url='accounts_login')
def admin_delete_student_education(request, id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    # Fetch the education record to be deleted or return 404 if not found
    applicant_education = get_object_or_404(EducationRecord, id=id)
    
    try:
        
        # Delete the education record
        applicant_education.delete()
        
        UserActivityLog.objects.create(user=logged_user, action=f'Deleted {applicant_education.applicant.profile.first_name} {applicant_education.applicant.last_name} Education Application Record.')
        
        messages.success(request, f'{applicant_education.applicant.profile.first_name} {applicant_education.applicant.profile.last_name} Education Record Deleted Successfully.')
        
        # Redirect to the admin_add_applicant_education view with the applicant's ID
        return redirect('admin_add_student_education', student_id=applicant_education.applicant.id)
    
    except Exception as e:
        # Handle the case where an exception occurs during deletion
        messages.error(request, f'Something went wrong: {str(e)}')
        return redirect('accounts_login')
    

# Admin add O LEVEL RESULT
@login_required(login_url='accounts_login')
def admin_add_o_level_result(request, applicant_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    
    # Check if the user has a profile
    try:
        applicant = PreApplicant.objects.select_related('applicant').get(applicant_id=applicant_id)
        pre_applicant_candidate = applicant.applicant
        
        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Visited {applicant.applicant.profile.first_name} {applicant.applicant.profile.last_name} Applicant Add O Level Result Page')
        results = ApplicantResult.objects.select_related('applicant').filter(applicant=pre_applicant_candidate).order_by('-added_date')
        form = ApplicantResultForm(request.POST or None)
        if request.method == 'POST':
            if form.is_valid():
                with transaction.atomic():
                    subject_name = form.cleaned_data['subject_name']
                    result_name = form.cleaned_data['result_name']
                    subject_grade = form.cleaned_data['subject_grade']
                    exam_number = form.cleaned_data['exam_number']
                    exam_year = form.cleaned_data['exam_year']
                    
                    exam_result = ApplicantResult.objects.create(
                        applicant=pre_applicant_candidate,
                        subject_name=subject_name,
                        result_name=result_name,
                        subject_grade=subject_grade,
                        exam_number=exam_number,
                        exam_year=exam_year
                        )
                    if exam_result:
                        messages.success(request, 'Record Added Successfully.')
        else:
            form = ApplicantResultForm()
        
        context = {
            'applicant_id': applicant_id,
            'form': form,
            'results': results,
            'page_title': 'Admin Add Applicant Result',
        }
        return render(request, 'admission/admin_add_applicant_result.html', context)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')


# Admin UPDATE APPLICANT'S RESULT
@login_required(login_url='accounts_login')
def admin_update_applicant_result(request, applicant_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    
    # Check if the user has a profile
    try:
        applicant = PreApplicant.objects.select_related('applicant').get(applicant_id=applicant_id)
        pre_applicant_candidate = applicant.applicant
        
        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Visited {applicant.applicant.profile.first_name} {applicant.applicant.profile.last_name} Applicant Add O Level Result Page')
        results = ApplicantResult.objects.select_related('applicant').filter(applicant=pre_applicant_candidate).order_by('-added_date')
        
        
        context = {
            'applicant_id': applicant_id,
            
            'results': results,
            'page_title': 'Admin Update Applicant Result',
        }
        return render(request, 'admission/admin_add_applicant_result.html', context)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')


# Admin Update O LEVEL RESULT
@login_required(login_url='accounts_login')
def admin_update_o_level_result(request, id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    # Check if the user has a profile
    try:
        applicant_result = ApplicantResult.objects.select_related('applicant').get(id=id)
        
        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Visited {applicant_result.applicant.profile.first_name} {applicant_result.applicant.last_name} Applicant Update O Level Result Page')
        
        form = ApplicantResultForm(request.POST, request.FILES, instance =applicant_result )
        if request.method == 'POST':
            if form.is_valid():
                with transaction.atomic():
                    subject_name = form.cleaned_data['subject_name']
                    result_name = form.cleaned_data['result_name']
                    subject_grade = form.cleaned_data['subject_grade']
                    exam_number = form.cleaned_data['exam_number']
                    exam_year = form.cleaned_data['exam_year']
                    # UPDATE RESULT
                    applicant_result.subject_name=subject_name
                    applicant_result.result_name=result_name
                    applicant_result.subject_grade=subject_grade
                    applicant_result.exam_number=exam_number
                    applicant_result.exam_year=exam_year
                    applicant_result.save()
                    # Log user activity
                    UserActivityLog.objects.create(user=logged_user, action=f'Visited {applicant_result.applicant.profile.first_name} {applicant_result.applicant.last_name} Applicant Update {subject_name} with exam number {exam_number} ')
                    messages.success(request, f' {applicant_result.applicant.profile.first_name} {applicant_result.applicant.profile.last_name} {subject_name} O Level Result Updated Successfully.')
                    return redirect('admin_add_o_level_result', applicant_id=applicant_result.applicant.id)
        else:
            form = ApplicantResultForm(instance =applicant_result)
        context = {
            
            'form': form,
            'page_title': 'Admin Update Applicant O Level Result',
        }
        return render(request, 'admission/admin_add_applicant_result.html', context)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')


# Admin Update STUDENT RESULT
@login_required(login_url='accounts_login')
def admin_update_student_result(request, id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    # Check if the user has a profile
    try:
        applicant_result = ApplicantResult.objects.select_related('applicant').get(id=id)
    
        form = ApplicantResultForm(request.POST, request.FILES, instance =applicant_result )
        if request.method == 'POST':
            if form.is_valid():
                with transaction.atomic():
                    subject_name = form.cleaned_data['subject_name']
                    result_name = form.cleaned_data['result_name']
                    subject_grade = form.cleaned_data['subject_grade']
                    exam_number = form.cleaned_data['exam_number']
                    exam_year = form.cleaned_data['exam_year']
                    # UPDATE RESULT
                    applicant_result.subject_name=subject_name
                    applicant_result.result_name=result_name
                    applicant_result.subject_grade=subject_grade
                    applicant_result.exam_number=exam_number
                    applicant_result.exam_year=exam_year
                    applicant_result.save()
                    # Log user activity
                    UserActivityLog.objects.create(user=logged_user, action=f'Updated {applicant_result.applicant.profile.first_name} {applicant_result.applicant.last_name}  {subject_name} with exam number {exam_number} ')
                    messages.success(request, f' {applicant_result.applicant.profile.first_name} {applicant_result.applicant.profile.last_name} {subject_name} O Level Result Updated Successfully.')
                    return redirect('admin_add_student_result', student_id=applicant_result.applicant.id)
        else:
            form = ApplicantResultForm(instance =applicant_result)
        context = {
            
            'form': form,
            'page_title': f'Admin Update {applicant_result.applicant.profile.last_name} O Level Result',
        }
        return render(request, 'admission/admin_add_student_result.html', context)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')


# Admin Delete O LEVEL SUBJECT RESULT
@login_required(login_url='accounts_login')
def admin_delete_student_result(request, id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    # Fetch the education record to be deleted or return 404 if not found
    applicant_result = get_object_or_404(ApplicantResult, id=id)
    
    try:
        
        # Delete the education record
        applicant_result.delete()
        
        UserActivityLog.objects.create(user=logged_user, action=f'Deleted {applicant_result.applicant.profile.first_name} {applicant_result.applicant.last_name} Deleted {applicant_result.subject_name} ')
        
        messages.success(request, f'{applicant_result.applicant.profile.first_name} {applicant_result.applicant.profile.last_name} {applicant_result.subject_name} Deleted Successfully.')
        
        # Redirect to the admin_add_o_level_result view with the applicant's ID
        return redirect('admin_add_student_result', student_id=applicant_result.applicant.id)
    
    except Exception as e:
        # Handle the case where an exception occurs during deletion
        messages.error(request, f'Something went wrong: {str(e)}')
        return redirect('accounts_login')
    

# Admin Delete O LEVEL SUBJECT RESULT
@login_required(login_url='accounts_login')
def admin_delete_o_level(request, id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    # Fetch the education record to be deleted or return 404 if not found
    applicant_result = get_object_or_404(ApplicantResult, id=id)
    
    try:
        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Visited {applicant_result.applicant.profile.first_name} {applicant_result.applicant.last_name} Applicant Delete O Level Subject Result')
        
        # Delete the education record
        applicant_result.delete()
        
        UserActivityLog.objects.create(user=logged_user, action=f'Deleted {applicant_result.applicant.profile.first_name} {applicant_result.applicant.last_name} Deleted {applicant_result.subject_name} ')
        
        messages.success(request, f'{applicant_result.applicant.profile.first_name} {applicant_result.applicant.profile.last_name} {applicant_result.subject_name} Deleted Successfully.')
        
        # Redirect to the admin_add_o_level_result view with the applicant's ID
        return redirect('admin_add_o_level_result', applicant_id=applicant_result.applicant.id)
    
    except Exception as e:
        # Handle the case where an exception occurs during deletion
        messages.error(request, f'Something went wrong: {str(e)}')
        return redirect('accounts_login')


# Admin APPLICANT SPONSOR
@login_required(login_url='accounts_login')
def admin_add_applicant_sponsor(request, applicant_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')

    # Check if the user has a profile
    try:
        applicant = PreApplicant.objects.select_related('applicant').get(applicant_id=applicant_id)
        pre_applicant_candidate = applicant.applicant
        
        # Check Applicant Sponsor Details Existence
        profile = get_sponsor_details(pre_applicant_candidate)
        if sponsor_details_contact_complete(profile):
            messages.success(request, 'Sponsor Already Added')
            return redirect('admin_preview_applicant_form', applicant_id)

        form = SponsorDetailsForm(request.POST or None)

        if request.method == 'POST':
            if form.is_valid():
                with transaction.atomic():
                    full_name = form.cleaned_data['full_name']
                    occupation = form.cleaned_data['occupation']
                    phone_number = form.cleaned_data['phone_number']
                    email = form.cleaned_data['email']
                    home_address = form.cleaned_data['home_address']
                    sponsor_relationship = form.cleaned_data['sponsor_relationship']

                    # Create Applicant's Sponsor instance
                    sponsor = SponsorDetails.objects.create(
                        applicant=pre_applicant_candidate,
                        full_name=full_name,
                        occupation=occupation,
                        phone_number=phone_number,
                        email=email,
                        home_address=home_address,
                        sponsor_relationship=sponsor_relationship
                    )

                    if sponsor:
                        
                        messages.success(request, 'Sponsor Information Added Successfully.')
                        return redirect('admin_preview_applicant_form', applicant_id)
                    else:
                        messages.success(request, 'Sponsor Information Added Successfully.')
        else:
            form = SponsorDetailsForm()
        context = {
            'page_title': 'Admin Add Applicant\'s Sponsor',
            'form': form,  # Include the form in the context for rendering in the template
        }

        return render(request, 'admission/admin_add_applicant_sponsor.html', context)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')


# Admin PREVIEW STUDENT REGISTRATION FORM
@login_required(login_url='accounts_login')
def admin_preview_student_form(request, student_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    
    # Check if the user has a profile
    try:
        applicant = PreApplicant.objects.select_related('applicant').get(applicant_id=student_id)
        pre_applicant_candidate = applicant.applicant
        pre_applicant_session = applicant.academic_session
        # Check if student has submitted registration
        submission_status = get_application_submission(pre_applicant_candidate)
        if not application_submitted(submission_status):
            schools_attended = EducationRecord.objects.select_related('applicant').filter(applicant=pre_applicant_candidate)
            applicant_result = ApplicantResult.objects.select_related('applicant').filter(applicant=pre_applicant_candidate)
            # GET STUDENT PROGRAMME AND APPLICATION ID
            applicant_program = applicant.course_name
            application_form_id = applicant.application_number
            
            # Get Academic Session
            try:
                academic_session = pre_applicant_session
                academic_session_instance = academic_session
            except AcademicSession.DoesNotExist:
                messages.warning(request, 'Academic Session Not Found')         
           
            if request.method == 'POST':
                # If the form is submitted, process the form data
                form = ApplicationSubmissionForm(request.POST)
                    
                if form.is_valid():
                    # Create a new applicant and Submit the Application with 
                    # Academic Session, and Program/Course
                    new_student = form.save(commit=False)
                    new_student.student_id = application_form_id
                    new_student.applicant = pre_applicant_candidate
                    new_student.academic_sess = academic_session_instance 
                    new_student.course_name = applicant_program
                    new_student.save()

                    # Redirect to a success page or render a template
                    # Log user activity
                    UserActivityLog.objects.create(user=logged_user, action=f'Submitted New Student Registration: {applicant.applicant.profile.first_name} {applicant.applicant.last_name}')
                    messages.success(request, 'Admin Submitted Applicant Form Successfully.')
                    return redirect('admin_student_registration_slip', student_id)
            else:
                form = ApplicationSubmissionForm()
            context = {
                'applicant': applicant,
                'application_form_sess': academic_session,
                'applicant_result': applicant_result,
                'schools_attended': schools_attended,
                'form': form,
                'page_title': 'Student Registration Form Preview',
            }
            return render(request, 'admission/admin_student_reg_preview.html', context)    
        else:
            messages.success(request, 'Application Form Already Submitted')
            return redirect('admin_student_registration_slip', student_id)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')


@login_required(login_url='accounts_login')
def admin_student_registration_slip(request, student_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    # Check if the user has a profile
    try:
        applicant = PreApplicant.objects.select_related('applicant').get(applicant_id=student_id)
        pre_applicant_candidate = applicant.applicant
        pre_applicant_session = applicant.academic_session
        # Check if student has submitted registration
        submission_status = get_application_submission(pre_applicant_candidate)
        if application_submitted(submission_status):
            schools_attended = EducationRecord.objects.select_related('applicant').filter(applicant=pre_applicant_candidate)
            applicant_result = ApplicantResult.objects.select_related('applicant').filter(applicant=pre_applicant_candidate)
            student_sponsor = SponsorDetails.objects.select_related('applicant').filter(applicant=pre_applicant_candidate)
            # GET STUDENT PROGRAMME AND APPLICATION ID
            applicant_program = applicant.course_name
            application_form_id = applicant.application_number
            
            # Get Academic Session
            try:
                academic_session = pre_applicant_session
            except AcademicSession.DoesNotExist:
                messages.warning(request, 'Academic Session Not Found')         
           
            
            context = {
                'student_id': student_id,
                'student_sponsor': student_sponsor,
                'applicant': applicant,
                'application_form_sess': academic_session,
                'applicant_result': applicant_result,
                'schools_attended': schools_attended,
                'page_title': 'Student Registration Slip',
            }
            return render(request, 'admission/admin_student_reg_slip.html', context)    
        else:
            messages.warning(request, 'Student Registration Yet to be Submitted')
            return redirect('admin_preview_student_form', student_id)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')
    

# Admin PREVIEW APPLICANT FORM
@login_required(login_url='accounts_login')
def admin_preview_applicant_form(request, applicant_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
        # Check if the user has a profile
    try:
        applicant = PreApplicant.objects.select_related('applicant').get(applicant_id=applicant_id)
        pre_applicant_candidate = applicant.applicant
        
        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Visited {applicant.applicant.profile.first_name} {applicant.applicant.profile.last_name} Applicant Add Sponsor Details Page')
        # Check if applicant has submitted application
        submission_status = get_application_submission(pre_applicant_candidate)
        if not application_submitted(submission_status):
            schools_attended = EducationRecord.objects.select_related('applicant').filter(applicant=pre_applicant_candidate)
            applicant_result = ApplicantResult.objects.select_related('applicant').filter(applicant=pre_applicant_candidate)
            # GET APPLICANT PROGRAMME AND APPLICATION ID
            applicant_program = applicant.course_name
            application_form_id = applicant.application_number
            
            # Get the latest payment for the applicant
            applicant_session = applicant.academic_session
            latest_payment = Payment.objects.filter(user=pre_applicant_candidate, academic_sess=applicant_session, payment_source='Application Form').order_by('-payment_date').first()

            if latest_payment:
                if request.method == 'POST':
                    # If the form is submitted, process the form data
                    form = ApplicationSubmissionForm(request.POST)
                    
                    if form.is_valid():
                        # Create a new applicant and Submit the Application with 
                        # Academic Session, and Program/Course
                        new_application = form.save(commit=False)
                        new_application.student_id = application_form_id
                        new_application.applicant = pre_applicant_candidate
                        new_application.academic_sess = applicant_session
                        new_application.course_name = applicant_program
                        new_application.save()

                        # Redirect to a success page or render a template
                        messages.success(request, 'Admin Submitted Applicant Form Successfully.')
                        return redirect('admin_view_applicant_slip', applicant_id)
                else:
                    form = ApplicationSubmissionForm()
                context = {
                    'applicant': applicant,
                    'application_form_sess': applicant_session,
                    'applicant_result': applicant_result,
                    'schools_attended': schools_attended,
                    'form': form,
                    'page_title': 'Admin Applicant Form Preview',
                }
                return render(request, 'admission/admin_applicant_form_preview.html', context)
            else:
                messages.success(request, 'No Application Fee Found')
                
        else:
            messages.success(request, 'Application Form Already Submitted')
            return redirect('admin_view_applicant_slip', applicant_id)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')


# Admin Edit Applicant's Profile
@login_required(login_url='accounts_login')
def admin_update_applicant_details(request, applicant_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    try:
        applicant = PreApplicant.objects.select_related('applicant').get(applicant_id=applicant_id)
        
        applicant_profile = applicant.applicant.profile
        form = AdminApplicantEditContactForm(request.POST, request.FILES, instance=applicant_profile)

        if request.method == 'POST':
        
            if form.is_valid():
                form.save()
                messages.success(request, 'Applicant Profile Updated Successfully.')
                return redirect('admin_preview_applicant_form', applicant_id)
        else:
            form = AdminApplicantEditContactForm(instance=applicant_profile)

        context = {
            'form': form,
            'page_title': 'Admin Edit Applicant Profile',
        }
        return render(request, 'admission/admin_edit_applicant_profile.html', context)
    except PreApplicant.DoesNotExist:
        messages.warning(request, 'Applicant Does Not Exist')
        return redirect('accounts_login')

# Admin Edit STUDENT Profile
@login_required(login_url='accounts_login')
def admin_update_student_details(request, student_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    try:
        applicant = PreApplicant.objects.select_related('applicant').get(applicant_id=student_id)
        
        applicant_profile = applicant.applicant.profile
        form = AdminApplicantEditContactForm(request.POST, request.FILES, instance=applicant_profile)

        if request.method == 'POST':
        
            if form.is_valid():
                form.save()
                messages.success(request, 'Student Profile Updated Successfully.')
                return redirect('admin_preview_student_form', student_id)
        else:
            form = AdminApplicantEditContactForm(instance=applicant_profile)

        context = {
            'form': form,
            'page_title': 'Admin Edit Student Profile',
        }
        return render(request, 'admission/admin_edit_applicant_profile.html', context)
    except PreApplicant.DoesNotExist:
        messages.warning(request, 'Student Does Not Exist')
        return redirect('accounts_login')


# Admin Edit Applicant's Sponsor
@login_required(login_url='accounts_login')
def admin_update_applicant_sponsor(request, applicant_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    try:
        applicant = PreApplicant.objects.select_related('applicant').get(applicant_id=applicant_id)
        # Get the Applicant Profile
        applicant_profile = applicant.applicant.profile
        try:
            applicant_sponsor = SponsorDetails.objects.select_related('applicant').get(applicant=applicant.applicant)
            form = SponsorDetailsForm(request.POST, request.FILES, instance=applicant_sponsor)

            if request.method == 'POST':
            
                if form.is_valid():
                    full_name = form.cleaned_data['full_name']
                    occupation = form.cleaned_data['occupation']
                    phone_number = form.cleaned_data['phone_number']
                    email = form.cleaned_data['email']
                    home_address = form.cleaned_data['home_address']
                    sponsor_relationship = form.cleaned_data['sponsor_relationship']
                    form.save()
                    messages.success(request, 'Applicant Sponsor Updated Successfully.')
                    # Verify if Student or Applicant and redirect
                    if applicant.applicant.profile.role == 'Applicant':
                        return redirect('admin_preview_applicant_form', applicant_id)
                    elif applicant.applicant.profile.role == 'Student':
                         return redirect('admin_preview_student_form', applicant_id)
            else:
                form = SponsorDetailsForm(instance=applicant_sponsor)

            context = {
                'form': form,
                'page_title': 'Admin Edit Applicant Sponsor',
            }
            return render(request, 'admission/admin_add_applicant_sponsor.html', context)
        except SponsorDetails.DoesNotExist:
            messages.warning(request, 'Something Went Wrong')

    except PreApplicant.DoesNotExist:
        messages.warning(request, 'Applicant Does Not Exist')
        return redirect('accounts_login')
    

def resolve_applicant_slip_session(pre_applicant, submission_status):
    applicant_user = pre_applicant.applicant
    applicant_session = None
    if submission_status and submission_status.academic_sess_id:
        applicant_session = submission_status.academic_sess
    elif pre_applicant.academic_session_id:
        applicant_session = pre_applicant.academic_session

    payment_filters = {
        'user': applicant_user,
        'payment_source': 'Application Form',
    }
    if applicant_session:
        payment_filters['academic_sess'] = applicant_session

    application_payment = (
        Payment.objects.select_related('academic_sess')
        .filter(**payment_filters)
        .order_by('-verified', '-payment_date', '-id')
        .first()
    )

    if applicant_session:
        return applicant_session, application_payment

    if application_payment and application_payment.academic_sess_id:
        return application_payment.academic_sess, application_payment

    return None, application_payment


# Admin PREVIEW APPLICANT FORM
@login_required(login_url='accounts_login')
def admin_view_applicant_slip(request, applicant_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    # Get the Latest Session
    # latest_academic_session = AcademicSession.objects.order_by('-added_date').first()
    # Check if the user has a profile
    try:
        applicant = get_object_or_404(
            PreApplicant.objects.select_related('applicant', 'academic_session', 'course_name'),
            applicant_id=applicant_id,
        )
        pre_applicant_candidate = applicant.applicant
        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Visited {applicant.applicant.profile.first_name} {applicant.applicant.profile.last_name} Applicant Add Sponsor Details Page')
        # Check if applicant has submitted application
        submission_status = get_application_submission(pre_applicant_candidate)
        if application_submitted(submission_status):
            schools_attended = EducationRecord.objects.select_related('applicant').filter(applicant=pre_applicant_candidate)
            applicant_result = ApplicantResult.objects.select_related('applicant').filter(applicant=pre_applicant_candidate)
            # GET APPLICANT PROGRAMME AND APPLICATION ID
            applicant_program = applicant.course_name
            application_form_id = applicant.application_number
            applicant_session, application_payment = resolve_applicant_slip_session(applicant, submission_status)

            if not applicant_session:
                messages.warning(request, 'Academic Session Not Found for this applicant. Please update the applicant academic session before printing the slip.')
                return redirect('admin_preview_applicant_form', applicant_id)

            
            context = {
                    'applicant_id': applicant_id,
                    'applicant': applicant,
                    'application_form_sess': applicant_session,
                    'applicant_session': applicant_session,
                    'academic_session': applicant_session,
                    'application_submission': submission_status,
                    'application_payment': application_payment,
                    'applicant_result': applicant_result,
                    'schools_attended': schools_attended,
                    'applicant_program': applicant_program,
                    'application_form_id': application_form_id,
                    'page_title': 'Admin Applicant Slip',
                }
            return render(request, 'admission/admin_view_applicant_slip.html', context)
        else:
            messages.success(request, 'Application Form Submitted')
            return redirect('admin_preview_applicant_form', applicant_id)
    except PreApplicant.DoesNotExist:
        # Handle the case where there is no associated profile
        messages.error(request, 'Something Went Wrong')
        return redirect('accounts_login')


@login_required(login_url='accounts_login')
def admin_view_applicant_slip_back(request, applicant_id):
    logged_user = request.user
    # Check user permissions
    allowed_roles = ['Super Admin', 'Admin', 'ADMISSION-OFFICER', 'ADMISSION-OFFICER1']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.warning(request, 'Unauthorized Access')
        return redirect('accounts_login')
    
    # Check if the user has a profile
    try:
        applicant = PreApplicant.objects.select_related('applicant').get(applicant_id=applicant_id)
        
        context = {
            'applicant': applicant,
            'page_title': 'Application Slip Page',
        }
        return render(request, 'admission/admin_view_applicant_slip_back.html', context)
    except PreApplicant.DoesNotExist:
        messages.warning(request, 'Applicant Not Found')
        return redirect('accounts_login')


# GENERATE APPLICATION LIST BY SESSION
@login_required(login_url='accounts_login')
def application_list_by_session(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.profile.role == 'Admin' or logged_user.profile.role == 'Super Admin' or logged_user.is_superuser):
        messages.warning(request, 'Access Denied')
        return redirect('accounts_login')

    # Log user activity
    UserActivityLog.objects.create(user=logged_user, action='Visited Application List by Academic Session Page')

    form = ApplicationlistBySessionForm(request.GET or None)
    if request.method == "GET" and form.is_valid():
        academic_session = form.cleaned_data['academic_session']
        start_date = form.cleaned_data['start_date']
        end_date = form.cleaned_data['end_date']
        # Filter Applicants by Session and Date Range
        submitted_application_list = ApplicationSubmission.objects.filter(academic_sess=academic_session,
                                                                          submitted_date__range=[start_date, end_date])
        count_submited_applications = submitted_application_list.count()

        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f' Generated Application list for {academic_session} Academic Session with {count_submited_applications} Applicants ')

        paginator = Paginator(submitted_application_list, 10)

        page = request.GET.get('page')

        try:
            submitted_applications = paginator.page(page)
        except PageNotAnInteger:
            submitted_applications = paginator.page(1)
        except EmptyPage:
            submitted_applications = paginator.page(paginator.num_pages)
        
        context = {
            'count_submited_applications': count_submited_applications,
            'academic_session': academic_session,
            'submitted_applications': submitted_applications,
            'page_title': f'List of Applicants for {academic_session} Session',
            'start_date': start_date,
            'end_date': end_date,
        }

        # Check for download request
        if request.GET.get('download'):
            pdf_data = generate_pdf(request, submitted_application_list, context)
            if pdf_data:
                return pdf_data
            else:
                messages.error(request, 'Error generating PDF')
                return redirect('application_list_by_session')

        return render(request, 'admission/filtered_applicant_by_session.html', context)
    else:
        form = ApplicationlistBySessionForm()

    submitted_application_list = ApplicationSubmission.objects.order_by('-submitted_date')
    paginator = Paginator(submitted_application_list, 10)

    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'List of Applicants',
        'submitted_applications': page_obj,
        'form': ApplicationlistBySessionForm(request.GET),  # Include full GET parameters
        'current_page': page_number,
        'has_more_pages': page_obj.has_next(),  # Check for next page
    }
    return render(request, 'admission/filter_applicants_by_session_date.html', context)


# FUNCTION FOR THE GENERATION OF PDF
def generate_pdf(request, queryset, context):
    # Render the HTML content of the PDF template with the filtered queryset
    context['submitted_application_list'] = queryset
    html_content = render_to_string('admission/application_list_by_session_pdf.html', context)

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
        response['Content-Disposition'] = 'attachment; filename="application_list.pdf"'  # Set filename for download
        return response
    else:
        # If PDF generation failed, return None
        return None


# GENERATE APPLICATION LIST BY SESSION & PROGRAMME
@login_required(login_url='accounts_login')
def application_list_by_session_programme(request):
    logged_user = request.user
    if not (logged_user.profile.role in ['ADMISSION-OFFICER', 'Admin', 'Super Admin'] or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    # Log user activity
    UserActivityLog.objects.create(user=logged_user, action='Visited Application List by Academic Session and Programme Page')

    form = ApplicationReportByProgrammForm(request.GET or None)
    if request.method == "GET" and form.is_valid():
        academic_session = form.cleaned_data['academic_session']
        course_of_study = form.cleaned_data['course_of_study']
        start_date = form.cleaned_data['start_date']
        end_date = form.cleaned_data['end_date']

        # Filter applications by academic session and course of study
        submitted_application_by_programme = ApplicationSubmission.objects.filter(academic_sess=academic_session, course_name=course_of_study,
                                                                                  submitted_date__range=[start_date, end_date]).order_by(
            'applicant__profile__first_name',
            'applicant__profile__middle_name',
            'applicant__profile__last_name'
            )
        count_submited_applications = submitted_application_by_programme.count()

        # Log user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Generated Application list for {academic_session} Academic Session with {count_submited_applications} for {course_of_study} Applicants')

        paginator = Paginator(submitted_application_by_programme, 10)  # Change 2 to your desired number of items per page

        page_number = request.GET.get('page')

        try:
            submitted_applications = paginator.page(page_number)
        except PageNotAnInteger:
            submitted_applications = paginator.page(1)
        except EmptyPage:
            submitted_applications = paginator.page(paginator.num_pages)

        context = {
            'count_submited_applications': count_submited_applications,
            'academic_session': academic_session,
            'course_of_study': course_of_study,
            'submitted_applications': submitted_applications,
            'page_title': 'List of Applicants by Programme',
            'start_date': start_date,
            'end_date': end_date,
        }

        # Check for download request
        if request.GET.get('downloaded'):
            pdf_data = generate_applicants_pdf(request, submitted_application_by_programme, context)
            if pdf_data:
                return pdf_data
            else:
                messages.error(request, 'Error generating PDF')
                return redirect('application_list_by_session_programme')
        # if request.GET.get('download') == 'excel':
        #     excel_data = generate_applicants_excel(request, submitted_application_by_programme, context)
        #     if excel_data:
        #         return excel_data
        #     else:
        #         messages.error(request, 'Error generating Excel')
        #         return redirect('application_list_by_session_programme')

        return render(request, 'admission/applicants_search_by_programme.html', context)
    else:
        form = ApplicationReportByProgrammForm()

    # List all applicants with pagination
    submitted_application_by_programme = ApplicationSubmission.objects.order_by('-submitted_date')
    paginator = Paginator(submitted_application_by_programme, 10)  # Change 10 to your desired number of items per page

    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'List of Applicants',
        'submitted_applications': page_obj,
        'form': ApplicationReportByProgrammForm(request.GET),  # Include full GET parameters
        'current_page': page_number,
        'has_more_pages': page_obj.has_next(),  # Check for next page
    }
    return render(request, 'admission/application_list_by_programme.html', context)

# FUNCTION FOR THE GENERATION OF PDF
def generate_applicants_pdf(request, queryset, context):
    # Render the HTML content of the PDF template with the filtered queryset
    context['submitted_applications'] = queryset
    html_content = render_to_string('admission/application_list_by_programme_pdf.html', context)

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
        response['Content-Disposition'] = 'attachment; filename="applicants_programme_list.pdf"'  # Set filename for download
        return response
    else:
        # If PDF generation failed, return None
        return None


# Function for Generating EXCEL Document
# def generate_applicants_excel(request, queryset, context):
#     context['submitted_applications'] = queryset
#     # Create a new Excel workbook and add a worksheet
#     workbook = xlsxwriter.Workbook('applicants_programme_list.xlsx')
#     worksheet = workbook.add_worksheet()

#     # Define column headers
#     headers = ['#', 'Applicant ID', 'Full Name', 'Gender', 'State', 'Programme Choice']
#     for col, header in enumerate(headers):
#         worksheet.write(0, col, header)

#     # Set cell widths
#     widths = [6, 15, 30, 10, 13, 70]  # Adjust widths as needed
#     for col, width in enumerate(widths):
#         worksheet.set_column(col, col, width)

#     # Write data to the Excel worksheet
#     for row, applicant in enumerate(queryset, start=1):
#         worksheet.write(row, 0, row)  # Serial number
#         worksheet.write(row, 1, applicant.student_id)
#         full_name = f"{applicant.applicant.profile.first_name} {applicant.applicant.profile.middle_name} {applicant.applicant.profile.last_name}"
#         worksheet.write(row, 2, full_name)
#         worksheet.write(row, 3, applicant.applicant.profile.gender)
#         worksheet.write(row, 4, applicant.applicant.profile.state.name)
#         worksheet.write(row, 5, applicant.course_name.name)

#     # Close the workbook
#     workbook.close()

#     # Create an HTTP response with the Excel file
#     with open('applicants_programme_list.xlsx', 'rb') as excel_file:
#         response = HttpResponse(excel_file.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
#         response['Content-Disposition'] = 'attachment; filename="applicants_programme_list.xlsx"'
#     return response


def check_post_utme_status(request):
    latest_academic_session = AcademicSession.objects.order_by('-start_date', '-end_date', '-name').first()
    if request.method == 'POST':
        form = ApplicationNumberForm(request.POST)
        if form.is_valid():
            application_number = form.cleaned_data['application_number']
            try:
                applicant = ApplicationSubmission.objects.get(
                    student_id=application_number,
                    academic_sess=latest_academic_session,
                    is_admitted=True,
                )
                messages.success(request, f"{applicant.applicant.profile.first_name} {applicant.applicant.profile.last_name}, You have been offered Admission into the Federal Polytechnic, Wannune. Benue State in {applicant.course_name}. for the {applicant.academic_sess} Academic Session. Proceed to the Polytechnic ICT for onward processing. Thanks.")
            except ApplicationSubmission.DoesNotExist:
                messages.warning(request, "Sorry! You were not given Admission. Or contact the Polytechnic ICT. Thanks")
    else:
        form = ApplicationNumberForm()
    context = {
        'form': form,
        'page_title': 'Check POST-UTME Admission'
    }
    return render(request, 'admission/check_admission_status.html', context)


def check_remedial_status(request):
    latest_academic_session = AcademicSession.objects.order_by('-start_date', '-end_date', '-name').first()
    if request.method == 'POST':
        form = ApplicationNumberForm(request.POST)
        if form.is_valid():
            application_number = form.cleaned_data['application_number']
            try:
                applicant = ApplicationSubmission.objects.get(
                    student_id=application_number,
                    academic_sess=latest_academic_session,
                    course_name__name='Remedial Programme',
                    is_admitted=True,
                )
                messages.success(request, f"{applicant.applicant.profile.first_name} {applicant.applicant.profile.last_name}, You have been offered Admission into the Federal Polytechnic, Wannune. Benue State in {applicant.course_name}. for the {applicant.academic_sess} Academic Session. Proceed to the Polytechnic ICT for onward processing. Thanks.")
            except ApplicationSubmission.DoesNotExist:
                messages.warning(request, "Sorry! You were not given Admission. You may wait for another Batch. Thanks")
    else:
        form = ApplicationNumberForm()

    context = {
        'form': form,
        'page_title': 'Check Remedial Admission',
    }
    return render(request, 'admission/check_admission_status.html', context)



# Upload NDII Candidates from CSV File View
@login_required(login_url='accounts_login')
def custom_candidate_upload(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.profile.role == 'Super Admin' or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    # Log user activity
    UserActivityLog.objects.create(user=logged_user, action='Visited Jamb Candidate(s) Upload Page')
    if request.method == 'POST':
        form = CSVUploadForm(request.POST, request.FILES)
        csv_file = request.FILES.get('file')
        if not csv_file or not csv_file.name.endswith('.csv'):
            messages.error(request, 'Invalid file type. Please upload a CSV file.')
            return redirect('custom_candidate_upload')
        
        try:
            candidate_count = process_csv_file(csv_file)  # Get the count of processed candidates
            # Log user activity
            UserActivityLog.objects.create(user=logged_user, action=f'Uploaded {candidate_count} candidate(s) for POST-UTME Admission')
            messages.success(request, f'POST-UTME Candidates Uploaded Successfully. {candidate_count} candidates processed.')
            return redirect('custom_uploaded_list')
        except Exception as e:
            messages.error(request, f"An error occurred during CSV processing: {str(e)}")
            return redirect('custom_candidate_upload')
   
    else:
        form = CSVUploadForm()

    context = {
        'form': form,
        'page_title': 'NDII Candidates Upload',
    }

    return render(request, 'admission/post_utme_upload_form.html', context)


# Method to parse date into the desired format
def parse_date(date_string):
    try:
        # Parse the date string
        date_obj = parser.parse(date_string, dayfirst=True)

        # Handling years in two-digit format (e.g., '97' for '1997')
        if date_obj.year < 100:
            # Assuming all dates are after 1900
            date_obj = date_obj.replace(year=date_obj.year + 1900)

        # Format the date as 'YYYY-MM-DD'
        return date_obj.strftime('%Y-%m-%d')
    except ValueError as e:
        raise Exception(f"Invalid date format: {date_string} - {str(e)}")


# Method to Create User and Profile record
def create_or_update_user(row):
    username = row['JAMB ID']
    user, created = User.objects.get_or_create(username=username)
 
    if created:
        user.set_password(row['JAMB ID'])
        user.save()

    # Parse and format the date of birth
    formatted_dob = parse_date(row['Date of Birth'])

    # Get State name and compare with the one in Database
    state_name = row['State']
    try:
        state_instance, _ = State.objects.get_or_create(name=state_name)
    except Exception as e:
        raise Exception(f"Error processing state '{state_name}': {str(e)}")

    # Get Local Government name and compare with the one in Database
    lga_name = row['Local Govt.']
    try:
        lga_instance, _ = LocalGovernment.objects.get_or_create(name=lga_name, state=state_instance)
    except Exception as e:
        raise Exception(f"Error processing local government '{lga_name}' in state '{state_name}': {str(e)}")

    Profile.objects.update_or_create(
        user=user,
        defaults={
            'first_name': row['First Name'],
            'middle_name': row['Middle Name'],
            'last_name': row['Last Name'],
            'date_birth': formatted_dob,  # Use the formatted date
            'gender': row['Gender'],
            'phone_number': row['Phone Number'],
            'nationality': 'Nigeria',
            'state': state_instance,
            'lga': lga_instance,
            'role': 'Candidate'
        }
    )

    return user

# A mapping of known CSV course name variations to your CourseOfStudy model names 
COURSE_NAME_MAPPING = {
    "Computer Engineering Technology": "NATIONAL DIPLOMA IN COMPUTER ENGINEERING TECHNOLOGY (ND CET)",
    "Electrical And Electronics Engineering Technology": "NATIONAL DIPLOMA IN ELECTRICAL AND ELECTRONICS ENGINEERING TECHNOLOGY (ND EEET)",
    "Agricultural Technology": "NATIONAL DIPLOMA IN AGRICULTURAL TECHNOLOGY (ND AT)",
    "Computer Science": "NATIONAL DIPLOMA IN COMPUTER SCIENCE (ND CS)",
    "Statistics": "NATIONAL DIPLOMA IN STATISTICS (ND STAT)",
    "Accountancy": "NATIONAL DIPLOMA IN ACCOUNTANCY (ND ACCT)",
    "Business Administration And Management": "NATIONAL DIPLOMA IN BUSINESS ADMINISTRATION AND MANAGEMENT (ND BAM)",
    "Public Administration": "NATIONAL DIPLOMA IN PUBLIC ADMINISTRATION (ND PA)",
    # Add other mappings as needed 
}


# Method to determine Fee Category
def determine_fee_category():
    contains_hnd = any("Higher National Diploma" in course for course in COURSE_NAME_MAPPING.values())
    contains_nd = any("National Diploma" in course for course in COURSE_NAME_MAPPING.values())
    
    if contains_hnd:
        return 'HND'
    elif contains_nd:
        return 'ND'
    else:
        return 'Other'  # Default value or based on your logic


# Method to get the name of Closest Course Name Match
def get_closest_course_name(course_name):
    course_names = CourseOfStudy.objects.values_list('name', flat=True)
    closest_matches = difflib.get_close_matches(course_name, course_names)
    return closest_matches[0] if closest_matches else None


# Method for creating Candidate JAMB Record
def create_or_update_jamb_score(user, row):
    csv_course_name = row.get('Course of Study').strip()
    
    # Check if the course name is in the mapping
    if csv_course_name in COURSE_NAME_MAPPING:
        course_name = COURSE_NAME_MAPPING[csv_course_name]
    else:
        # If not in mapping, find the closest match
        course_name = get_closest_course_name(csv_course_name)
    
    logging.debug(f"Course name resolved to: {course_name}")
    
    if course_name:
        try:
            # Find the latest academic session
            earliest_academic_session = AcademicSession.objects.order_by('start_date', 'end_date', 'name').first()
            if not earliest_academic_session:
                logging.error("No academic session found in the database.")
                return
            # Programme of Study
            course_of_study = CourseOfStudy.objects.get(name=course_name)
            
            # Determine Admitted Level
            admitted_level = 'NDII'
           
            # Update or create the Jamb
            jamb_candidates, created = Jamb.objects.update_or_create(
                applicant=user,
                defaults={
                    'exam_name': 'JAMB',
                    'exam_year': row.get('Exam Year'),
                    'exam_number': row.get('JAMB ID'),
                    'total_score': row.get('Total Score'),
                    'academic_session': earliest_academic_session,
                    'admitted_course': course_of_study,
                    'admitted_level': admitted_level,
                }
            )
            if created:
                logging.info(f"Candidate record created successfully for user: {user.username}")
            else:
                logging.info(f"Candidate record updated successfully for user: {user.username}")
        except CourseOfStudy.DoesNotExist:
            logging.error(f"Course of study '{course_name}' does not exist in the database.")
        except MultipleObjectsReturned:
            logging.error(f"Multiple records found for course of study '{course_name}'. This should be unique.")
        except Exception as e:
            logging.error(f"An error occurred: {e}")
    else:
        logging.error(f"Course name '{csv_course_name}' not found in mapping or database.")


# Post-UTME Admisssion List
@login_required(login_url='accounts_login')
def custom_uploaded_list(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.profile.role == 'Super Admin' or logged_user.is_superuser):
        return redirect('accounts_login')

    # Find the latest academic session
    earliest_academic_session = AcademicSession.objects.order_by('start_date', 'end_date', 'name').first()

    # Generate List with total score calculation
    post_utme_admission_list = Jamb.objects.select_related('academic_session').filter(academic_session=earliest_academic_session ).order_by('id')
    count_admission = post_utme_admission_list.count()
    # Pagination by 10 records
    paginator = Paginator(post_utme_admission_list, 10)  

    page_number = request.GET.get('page')

    try:
        admission_list = paginator.page(page_number)
    except PageNotAnInteger:
        admission_list = paginator.page(1)
    except EmptyPage:
        admission_list = paginator.page(paginator.num_pages)

    context = {
        'latest_academic_session': earliest_academic_session,
        'count_admission': count_admission,
        'admission_list': admission_list,
        'page_title': 'NDII Portal Admission List',
    }
    
    # Check for PDF download request
    if request.GET.get('downloaded'):
        context = {
        'latest_academic_session': earliest_academic_session,
        'count_admission': count_admission,
        'admission_list': admission_list,
        'page_title': 'NDII Portal Admission List',
        }
        # Log user activity
        UserActivityLog.objects.create(user=request.user, action=f'Downloaded PDF NDII Report for {earliest_academic_session} Academic Session from')
        pdf_data = post_utme_list_pdf(request, post_utme_admission_list, context)
        if pdf_data:
            return pdf_data
        else:
            messages.error(request, 'Error generating PDF')
    return render(request, 'admission/post_utme_admission.html', context)


# FUNCTION FOR THE GENERATION OF PDF FOR POST-UTME ADMISSION
def post_utme_list_pdf(request, post_utme_admission_list, context):
    # Render the HTML content of the PDF template with the filtered queryset
    context['post_utme_admission_list'] = post_utme_admission_list
    html_content = render_to_string('admission/post_utme_admission_pdf.html', context)

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
        response['Content-Disposition'] = 'attachment; filename="uploaded_candidates_list.pdf"'  # Set filename for download
        return response
    else:
        # If PDF generation failed, return None
        return None

# Edit Candidate's Profile
@login_required(login_url='accounts_login')
def candidate_update_jamb_record(request):
    logged_user = request.user
    # Get the profile of the logged user
    jamb_candidate = logged_user.jamb_candidate

    if not logged_user.profile.role == 'Candidate':
        return redirect('accounts_login')

    payment_redirect = _redirect_if_application_fee_unverified(request)
    if payment_redirect:
        return payment_redirect

    form = CandidateJambForm(request.POST, request.FILES, instance=jamb_candidate)

    if request.method == 'POST':
    
        if form.is_valid():
            form.save()
            messages.success(request, 'Jamb Record Updated Successfully.')
            return redirect('post_utme_reg_preview')
            
    else:
        form = CandidateJambForm(instance=jamb_candidate)

    context = {
        'form': form,
        'page_title': 'Update Candidate JAMB',
    }
    return render(request, 'admission/candidate_update_jamb.html', context)

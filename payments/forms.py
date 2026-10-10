from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Field
from academics.models import AcademicSession, Student
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from admission.forms import get_application_fee_category
from admission.models import ApplicantValidation, PreApplicant
from fees.models import ApplicationFee, Fee
from fees.models import INDIGENE_TYPE_CHOICES
from fees.utils import FeeConfigurationError, get_application_fee, get_school_fee, get_user_indigene_type
from payments.models import Payment
from django.contrib import messages
from django.core.validators import RegexValidator, MinLengthValidator, MaxLengthValidator
import re


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
    if tuition_fees:
        return tuition_fees[0]

    non_late_fees = [
        fee for fee in fees
        if _normalize_fee_name(fee.name) not in NON_TUITION_FEE_NAME_ALIASES
    ]
    if non_late_fees:
        return non_late_fees[0]

    raise FeeConfigurationError(
        f'{semester} Tuition Fee setup was not found. Existing {semester} fee record(s) are not tuition fees.'
    )


def _school_fee_form_context(user, academic_session=None, level_name=None, indigene_type=None):
    applicant_validation = get_object_or_404(ApplicantValidation, applicant=user)
    student = Student.objects.select_related('academic_session', 'course').filter(candidate=user).first()
    pre_applicant = PreApplicant.objects.select_related('academic_session', 'course_name').filter(applicant=user).first()

    if student:
        return {
            'email': applicant_validation.email,
            'academic_session': academic_session or student.academic_session,
            'level_name': level_name or student.current_level,
            'course_of_study': student.course,
            'indigene_type': indigene_type or get_user_indigene_type(user),
        }

    if pre_applicant:
        course_name = pre_applicant.course_name.name.upper()
        if 'CERTIFICATE' in course_name:
            fallback_level = 'CERTIFICATE'
        elif 'HND' in course_name:
            fallback_level = 'HNDI'
        elif 'ND' in course_name:
            fallback_level = 'NDI'
        else:
            fallback_level = level_name

        return {
            'email': applicant_validation.email,
            'academic_session': academic_session or pre_applicant.academic_session,
            'level_name': level_name or fallback_level,
            'course_of_study': pre_applicant.course_name,
            'indigene_type': indigene_type or get_user_indigene_type(user),
        }

    raise forms.ValidationError("No student or pre-application record was found for this payment.")


def _initialize_school_fee_amount(form, user, semester, academic_session=None, level_name=None, indigene_type=None, name=None):
    context = _school_fee_form_context(
        user,
        academic_session=academic_session,
        level_name=level_name,
        indigene_type=indigene_type,
    )
    form.fields['email'].initial = context['email']

    if not context['academic_session']:
        raise forms.ValidationError("No academic session was found for this payment.")
    if not context['level_name']:
        raise forms.ValidationError("No student level was found for this payment.")

    if name:
        school_fees = get_school_fee(
            context['level_name'],
            semester,
            context['academic_session'],
            context['indigene_type'],
            name=name,
            course_of_study=context['course_of_study'],
        )
    else:
        school_fees = get_tuition_fee_for_option(
            context['level_name'],
            semester,
            context['academic_session'],
            context['indigene_type'],
            context['course_of_study'],
        )
    form.fields['amount'].initial = school_fees.amount


class SearchForm(forms.Form):
    search_applicant = forms.CharField(label='Candidate ID', max_length=30)


class PaymentForm(forms.Form):
    academic_sess = forms.ModelChoiceField(queryset=AcademicSession.objects.all(), label='Select Academic Session')

    def __init__(self, *args, **kwargs):
        academic_session_queryset = kwargs.pop('academic_session_queryset', None)
        initial_session = kwargs.pop('initial_session', None)
        super().__init__(*args, **kwargs)
        if academic_session_queryset is not None:
            self.fields['academic_sess'].queryset = academic_session_queryset
        if initial_session is not None:
            self.fields['academic_sess'].initial = initial_session


def get_pre_applicant_fee_category(pre_applicant, fee_name, user, academic_session=None, course_of_study=None):
    return get_application_fee_category(
        course_of_study or pre_applicant.course_name,
        academic_session or pre_applicant.academic_session,
        fee_name=fee_name,
        indigene_type=get_user_indigene_type(user),
    )


class SchoolFeePaymentForm(forms.Form):
    TRANSACTION_CHOICES = [
        ('Semester Fee Payment', 'Semester Fee Payment'),
        ('Full Fee Payment', 'Full Fee Payment'),
    ]
    payment_type = forms.ChoiceField(choices=TRANSACTION_CHOICES, required=True)
    pay_reference = forms.CharField(
        validators=[
            RegexValidator(
                regex='^[0-9]*$',
                message='Enter only digits.',
            ),
            MinLengthValidator(
                limit_value=12,
                message='Enter at least 12 digits.',
            ),
            MaxLengthValidator(
                limit_value=12,
                message='Enter at most 12 digits.',
            ),
        ],
        required=True
    )
    
    def __init__(self, *args, **kwargs):
        super(SchoolFeePaymentForm, self).__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Field('payment_type'),
            Field('pay_reference'),
        )


# Application Form Report By Session
class PaymentReportForm(forms.Form):
    TRANSACTION_CHOICES = [
        ('Application Form', 'Application Form'),
        ('Screening Fee', 'Screening Fee'),
        ('Acceptance Fee', 'Acceptance Fee'), 
        ('School Fees', 'School Fees'),
    ]

    academic_session = forms.ModelChoiceField(queryset=AcademicSession.objects.all(), required=True)
    payment_source = forms.ChoiceField(choices=TRANSACTION_CHOICES, required=True)
    indigene_type = forms.ChoiceField(choices=[('', 'All')] + INDIGENE_TYPE_CHOICES, required=False)
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), initial=timezone.now().date())
    end_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), initial=timezone.now().date())


class ApplicationFormPaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ('amount', 'email')
        widgets = {
            'amount': forms.TextInput(attrs={'readonly': 'readonly'}),
            'email': forms.EmailInput(attrs={'readonly': 'readonly'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        academic_session = kwargs.pop('academic_session', None)
        course_of_study = kwargs.pop('course_of_study', None)
        super(ApplicationFormPaymentForm, self).__init__(*args, **kwargs)

        if user:
            applicant_validation = get_object_or_404(ApplicantValidation, applicant=user)
            self.fields['email'].initial = applicant_validation.email

            pre_applicant = get_object_or_404(PreApplicant, applicant=user)
            fee_pay_category = get_pre_applicant_fee_category(pre_applicant, 'Application Form', user, academic_session, course_of_study)
            
            if fee_pay_category:
                try:
                    application_fee = get_application_fee(
                        fee_pay_category,
                        'Application Form',
                        academic_session or pre_applicant.academic_session,
                        get_user_indigene_type(user),
                    )
                    self.fields['amount'].initial = application_fee.amount
                except FeeConfigurationError:
                    self.fields['amount'].initial = 0
            else:
                self.fields['amount'].initial = 'Fee not available'


class ScreeningFeePaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ('amount', 'email')
        widgets = {
            'amount': forms.TextInput(attrs={'readonly': 'readonly'}),
            'email': forms.EmailInput(attrs={'readonly': 'readonly'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        academic_session = kwargs.pop('academic_session', None)
        course_of_study = kwargs.pop('course_of_study', None)
        super(ScreeningFeePaymentForm, self).__init__(*args, **kwargs)

        if user:
            applicant_validation = get_object_or_404(ApplicantValidation, applicant=user)
            self.fields['email'].initial = applicant_validation.email

            pre_applicant = get_object_or_404(PreApplicant, applicant=user)
            fee_pay_category = get_pre_applicant_fee_category(pre_applicant, 'Screening Fee', user, academic_session, course_of_study)

            if fee_pay_category:
                screening_fee = get_application_fee(fee_pay_category, 'Screening Fee', academic_session or pre_applicant.academic_session, get_user_indigene_type(user))
                self.fields['amount'].initial = screening_fee.amount
            else:
                self.fields['amount'].initial = 0


class CautionFeePaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ('amount', 'email')
        widgets = {
            'amount': forms.TextInput(attrs={'readonly': 'readonly'}),
            'email': forms.EmailInput(attrs={'readonly': 'readonly'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        academic_session = kwargs.pop('academic_session', None)
        course_of_study = kwargs.pop('course_of_study', None)
        super(CautionFeePaymentForm, self).__init__(*args, **kwargs)

        if user:
            applicant_validation = get_object_or_404(ApplicantValidation, applicant=user)
            self.fields['email'].initial = applicant_validation.email

            pre_applicant = get_object_or_404(PreApplicant, applicant=user)
            fee_pay_category = get_pre_applicant_fee_category(pre_applicant, 'Caution Fee', user, academic_session, course_of_study)

            if fee_pay_category:
                screening_fee = get_application_fee(fee_pay_category, 'Caution Fee', academic_session or pre_applicant.academic_session, get_user_indigene_type(user))
                self.fields['amount'].initial = screening_fee.amount
            else:
                self.fields['amount'].initial = 0


class AcceptanceFeePaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ('amount', 'email')
        widgets = {
            'amount': forms.TextInput(attrs={'readonly': 'readonly'}),
            'email': forms.EmailInput(attrs={'readonly': 'readonly'}),
        }

    def __init__(self, *args, academic_session=None, course_of_study=None, **kwargs):
        user = kwargs.pop('user', None)
        super(AcceptanceFeePaymentForm, self).__init__(*args, **kwargs)

        if user:
            applicant_validation = get_object_or_404(ApplicantValidation, applicant=user)
            self.fields['email'].initial = applicant_validation.email

            pre_applicant = get_object_or_404(PreApplicant, applicant=user)
            fee_pay_category = get_pre_applicant_fee_category(pre_applicant, 'Acceptance Fee', user, academic_session, course_of_study)

            if fee_pay_category:
                acceptance_fee = get_application_fee(fee_pay_category, 'Acceptance Fee', academic_session or pre_applicant.academic_session, get_user_indigene_type(user))
                self.fields['amount'].initial = acceptance_fee.amount
            else:
                self.fields['amount'].initial = 0


class OtherChargesFeePaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ('amount', 'email')
        widgets = {
            'amount': forms.TextInput(attrs={'readonly': 'readonly'}),
            'email': forms.EmailInput(attrs={'readonly': 'readonly'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        academic_session = kwargs.pop('academic_session', None)
        course_of_study = kwargs.pop('course_of_study', None)
        request = kwargs.pop('request', None)  # ✅ Extract request object
        super().__init__(*args, **kwargs)

        if user:
            try:
                applicant_validation = get_object_or_404(ApplicantValidation, applicant=user)
                self.fields['email'].initial = applicant_validation.email

                pre_applicant = PreApplicant.objects.select_related('applicant', 'course_name').get(applicant=user)
                fee_pay_category = get_pre_applicant_fee_category(pre_applicant, 'Other Charges', user, academic_session, course_of_study)

                # Ensure that 'Other Charges' exists for that category
                if fee_pay_category:
                    acceptance_fee = get_application_fee(fee_pay_category, 'Other Charges', academic_session or pre_applicant.academic_session, get_user_indigene_type(user))
                    self.fields['amount'].initial = acceptance_fee.amount
                else:
                    self.fields['amount'].initial = 0

            except PreApplicant.DoesNotExist:
                if request:
                    messages.error(request, 'Pre-Application Record NOT Found')
                # Optional: Clear fields or handle fallback
                self.fields['amount'].initial = 0
                self.fields['email'].initial = ''


class FirstSemesterFeesPaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ('amount', 'email')
        widgets = {
            'amount': forms.TextInput(attrs={'readonly': 'readonly'}),
            'email': forms.EmailInput(attrs={'readonly': 'readonly'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        academic_session = kwargs.pop('academic_session', None)
        level_name = kwargs.pop('level_name', None)
        indigene_type = kwargs.pop('indigene_type', None)
        super(FirstSemesterFeesPaymentForm, self).__init__(*args, **kwargs)

        if user:
            _initialize_school_fee_amount(
                self,
                user,
                'First Semester',
                academic_session=academic_session,
                level_name=level_name,
                indigene_type=indigene_type,
            )



class SecondSemesterFeesPaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ('amount', 'email')
        widgets = {
            'amount': forms.TextInput(attrs={'readonly': 'readonly'}),
            'email': forms.EmailInput(attrs={'readonly': 'readonly'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        academic_session = kwargs.pop('academic_session', None)
        level_name = kwargs.pop('level_name', None)
        indigene_type = kwargs.pop('indigene_type', None)
        super(SecondSemesterFeesPaymentForm, self).__init__(*args, **kwargs)

        if user:
            _initialize_school_fee_amount(
                self,
                user,
                'Second Semester',
                academic_session=academic_session,
                level_name=level_name,
                indigene_type=indigene_type,
            )



class FullSchoolFeesPaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ('amount', 'email')
        widgets = {
            'amount': forms.TextInput(attrs={'readonly': 'readonly'}),
            'email': forms.EmailInput(attrs={'readonly': 'readonly'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        academic_session = kwargs.pop('academic_session', None)
        level_name = kwargs.pop('level_name', None)
        indigene_type = kwargs.pop('indigene_type', None)
        super(FullSchoolFeesPaymentForm, self).__init__(*args, **kwargs)

        if user:
            _initialize_school_fee_amount(
                self,
                user,
                'Full Session',
                academic_session=academic_session,
                level_name=level_name,
                indigene_type=indigene_type,
            )
            

class FeePaymentForm(forms.Form):
    SEMESTER_CHOICES = [
        ('First Semester', 'First Semester'),
        ('Second Semester', 'Second Semester'),
        ('Full Session', 'Full Session')
    ]
    
    semester_choice = forms.ChoiceField(
        choices=SEMESTER_CHOICES, 
        widget=forms.Select(attrs={'class': 'form-control'}) 
    )

class LateRegistrationFeeForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ('amount', 'email')
        widgets = {
            'amount': forms.TextInput(attrs={'readonly': 'readonly'}),
            'email': forms.EmailInput(attrs={'readonly': 'readonly'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        academic_session = kwargs.pop('academic_session', None)
        category = kwargs.pop('category', None)
        indigene_type = kwargs.pop('indigene_type', None)
        super(LateRegistrationFeeForm, self).__init__(*args, **kwargs)

        if user:
            fee = get_application_fee(category, 'Late Registration', academic_session, indigene_type)
            self.fields['amount'].initial = fee.amount
            self.fields['email'].initial = user.email

    def clean_amount(self):
        # The configured ApplicationFee is authoritative; a readonly HTML input
        # must not make the amount client-controlled.
        return self.fields['amount'].initial

from django.utils import timezone
from academics.models import AcademicSession, CourseOfStudy, CourseUnit, Directorate
from accounts.models import StaffRegister
from .models import (ApplicantValidation, Department,
                     PreApplicant, staffAppSubmission,
                     EducationRecord, Jamb,
                     ApplicantResult,
                     ApplicationSubmission
                     )
from accounts.models import Profile, State, LocalGovernment, AcademicStaff
from admission.models import SponsorDetails
from fees.models import ApplicationFee
from django.core.validators import FileExtensionValidator
from django import forms
from django.core.exceptions import ValidationError
from datetime import datetime
from django.utils.translation import gettext_lazy as _


def _normalize_course_name(course_of_study):
    if not course_of_study:
        return None

    return getattr(course_of_study, 'name', str(course_of_study)).upper()


def _application_fee_categories(academic_session=None, fee_name='Application Form', indigene_type=None):
    fees = ApplicationFee.objects.filter(name=fee_name)
    if academic_session:
        fees = fees.filter(academic_session=academic_session)
    if indigene_type:
        fees = fees.filter(indigene_type=indigene_type)
    return list(fees.values_list('category', flat=True).distinct())


def get_application_fee_category(course_of_study, academic_session=None, fee_name='Application Form', indigene_type=None):
    if not course_of_study:
        return None

    course_name = _normalize_course_name(course_of_study)
    configured_categories = _application_fee_categories(academic_session, fee_name, indigene_type)

    if isinstance(course_of_study, CourseOfStudy):
        course_units = CourseUnit.objects.filter(course_of_study=course_of_study)
        if academic_session:
            course_units = course_units.filter(academic_session=academic_session)

        level_names = {level.upper() for level in course_units.values_list('level__name', flat=True)}
        if {'HNDI', 'HNDII'} & level_names and 'HND' in configured_categories:
            return 'HND'
        if 'CERTIFICATE' in level_names and 'CERTIFICATE' in configured_categories:
            return 'CERTIFICATE'
        if {'NDI', 'NDII'} & level_names and 'ND' in configured_categories:
            return 'ND'

    if 'HND' in course_name or 'HIGHER NATIONAL DIPLOMA' in course_name:
        return 'HND' if not configured_categories or 'HND' in configured_categories else None
    if 'CERTIFICATE' in course_name:
        return 'CERTIFICATE' if not configured_categories or 'CERTIFICATE' in configured_categories else None
    if 'ND' in course_name or 'NATIONAL DIPLOMA' in course_name:
        return 'ND' if not configured_categories or 'ND' in configured_categories else None

    if len(configured_categories) == 1:
        return configured_categories[0]

    if 'CERTIFICATE' in configured_categories:
        return 'CERTIFICATE'

    return None


def get_recent_application_sessions(limit=2):
    fee_session_ids = list(
        ApplicationFee.objects.filter(
            academic_session__isnull=False,
            name='Application Form',
        )
        .order_by(
            '-academic_session__start_date',
            '-academic_session__end_date',
            '-academic_session__name',
        )
        .values_list('academic_session_id', flat=True)
        .distinct()[:limit]
    )

    if fee_session_ids:
        return AcademicSession.objects.filter(pk__in=fee_session_ids).order_by(
            '-start_date',
            '-end_date',
            '-name',
        )

    fallback_session_ids = list(
        AcademicSession.objects.all()
        .order_by('-start_date', '-end_date', '-name')
        .values_list('pk', flat=True)[:limit]
    )
    return AcademicSession.objects.filter(pk__in=fallback_session_ids).order_by(
        '-start_date',
        '-end_date',
        '-name',
    )



class ApplicantValidationForm(forms.ModelForm):
    class Meta:
        model = ApplicantValidation
        fields = ['email']


class StaffProfileDateField(forms.DateInput):
    """Prepare Profiel Date Field"""
    input_type = 'date' 


class StaffAppointmentDateField(forms.DateInput):
    """Prepare Profiel Date Field"""
    input_type = 'date' 


class StaffRegistrationForm(forms.ModelForm):
    first_name = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'Enter your First Name', 'class': 'custom-class red-asterisk'}),
        label='First Name:'
    )
    middle_name = forms.CharField(
        required=False,  # Set as required or not based on your model
        widget=forms.TextInput(attrs={'placeholder': 'Enter your Middle Name', 'class': 'custom-class'}),
        label='Middle Name:'
    )
    last_name = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'Enter your Surname', 'class': 'custom-class'}),
        label='Surname:'
    )
    department = forms.ModelChoiceField(
        required=False,
        queryset=Department.objects.all(),
        widget=forms.Select(attrs={'class': 'custom-class'}),
        label='Staff Department:'
    )
    directorate = forms.ModelChoiceField(
        required=False,
        queryset=Directorate.objects.all(),
        widget=forms.Select(attrs={'class': 'custom-class'}),
        label='Staff Directorate/Department:'
    )
    Pay_grade = forms.ChoiceField(
        required=True,
        choices=[],  # Initially empty, will be populated dynamically
        widget=forms.Select(attrs={'class': 'custom-class'}),
        label='Pay Grade:'
    )

    class Meta:
        widgets = {'date_birth': StaffProfileDateField(), 'First_appointment': StaffAppointmentDateField()}
        
        model = StaffRegister
        fields = ['first_name', 'middle_name', 'last_name', 'date_birth',
                  'phone_number', 'gender', 'marital_status',
                  'Number_children', 'home_address', 'religion',
                  'nationality', 'state', 'lga', 'email', 'department', 'directorate',
                  'dept_type', 'Pay_grade', 'file_number','nin_number',
                  'Appointment_type', 'Appointment_Mode', 'Promotion_status',
                  'First_appointment', 'image'] 
        labels = {
            'First_appointment': 'Date of First Appointment'
        }
    
    def __init__(self, *args, **kwargs):
        super(StaffRegistrationForm, self).__init__(*args, **kwargs)
        # Dynamically populate choices for Pay_grade based on dept_type
        self.fields['Pay_grade'].choices = self.fields['Pay_grade'].choices or self.get_pay_grade_choices()

    def get_pay_grade_choices(self):
        # Initialize PAY_CATEGORY based on dept_type
        dept_type = self.data.get('dept_type')
        if dept_type == 'Academic':
            return [('CONPCASS 1', 'CONPCASS 1'), ('CONPCASS 2', 'CONPCASS 2'),
                    ('CONPCASS 3', 'CONPCASS 3'), ('CONPCASS 4', 'CONPCASS 4'),
                    ('CONPCASS 5', 'CONPCASS 5'), ('CONPCASS 6', 'CONPCASS 6'),
                    ('CONPCASS 7', 'CONPCASS 7'), ('CONPCASS 8', 'CONPCASS 8'),
                    ('CONPCASS 9', 'CONPCASS 9')]
        elif dept_type == 'Non-Academic':
            return [('CONTEDISS 1', 'CONTEDISS 1'), ('CONTEDISS 2', 'CONTEDISS 2'),
                    ('CONTEDISS 3', 'CONTEDISS 3'), ('CONTEDISS 4', 'CONTEDISS 4'),
                    ('CONTEDISS 5', 'CONTEDISS 5'), ('CONTEDISS 6', 'CONTEDISS 6'),
                    ('CONTEDISS 7', 'CONTEDISS 7'), ('CONTEDISS 8', 'CONTEDISS 8'),
                    ('CONTEDISS 9', 'CONTEDISS 9'), ('CONTEDISS 10', 'CONTEDISS 10'),
                    ('CONTEDISS 11', 'CONTEDISS 11'), ('CONTEDISS 12', 'CONTEDISS 12'),
                    ('CONTEDISS 13', 'CONTEDISS 13'), ('CONTEDISS 14', 'CONTEDISS 14'),
                    ('CONTEDISS 15', 'CONTEDISS 15')]
        else:
            return []  # Return empty list if dept_type is not set or recognized
    
    def clean_Pay_grade(self):
        pay_grade = self.cleaned_data.get('Pay_grade')
        choices = dict(self.fields['Pay_grade'].choices)
        if pay_grade not in choices:
            raise forms.ValidationError("Invalid choice for Pay_grade.")
        return pay_grade


class AcademicStaffForm(forms.ModelForm):
    qualification_date = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'Enter Qualifications with Dates & Area Specializaion', 'class': 'custom-class red-asterisk'}),
        label='Qualifications & Area Specializaion:'
    )
    current_rank = forms.CharField(
        required=False,  # Set as required or not based on your model
        widget=forms.TextInput(attrs={'placeholder': 'Enter Your Current Rank', 'class': 'custom-class'}),
        label='Current Staff Rank:'
    )
    work_experience = forms.CharField(
        required=False,  # Set as required or not based on your model
        widget=forms.TextInput(attrs={'placeholder': 'Enter Industrial Experience', 'class': 'custom-class'}),
        label='work Experience:'
    )
    courses_taught = forms.CharField(
        required=False,  # Set as required or not based on your model
        widget=forms.TextInput(attrs={'placeholder': 'Enter Courses Taught', 'class': 'custom-class'}),
        label='Courses Taught:'
    )

    class Meta:
        model = AcademicStaff
        fields = ['qualification_date', 'staff_type', 'current_rank', 'internal_appointment', 'work_experience',  'courses_taught', 'upload_cv']


class UpdateStaffRegistration(forms.ModelForm):

    class Meta:
        model = StaffRegister
        fields = ['Pay_grade', 'file_number', 'nin_number', 'department', 'Appointment_type', 'Appointment_Mode', 'Promotion_status', 'First_appointment'] 


class PreApplicationForm(forms.ModelForm):
    first_name = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'Enter your First Name', 'class': 'custom-class red-asterisk'}),
        label='First Name:'
    )
    middle_name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Enter your Middle Name', 'class': 'custom-class'}),
        label='Middle Name:'
    )
    last_name = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'Enter your Surname', 'class': 'custom-class'}),
        label='Surname:'
    )
    course_name = forms.ModelChoiceField(
        required=True,
        queryset=CourseOfStudy.objects.all(),
        widget=forms.Select(attrs={'class': 'custom-class'}),
        label='Program of Choice:'
    )
    academic_session = forms.ModelChoiceField(
        required=True,
        queryset=AcademicSession.objects.none(),
        widget=forms.Select(attrs={'class': 'custom-class'}),
        label='Academic Session:'
    )

    class Meta:
        model = PreApplicant
        fields = ['first_name', 'middle_name', 'last_name', 'phone_number', 'course_name', 'academic_session']

    def __init__(self, *args, **kwargs):
        super(PreApplicationForm, self).__init__(*args, **kwargs)
        self.fields['academic_session'].queryset = get_recent_application_sessions()

    def clean(self):
        cleaned_data = super().clean()
        course_name = cleaned_data.get('course_name')
        academic_session = cleaned_data.get('academic_session')

        if not course_name or not academic_session:
            return cleaned_data

        fee_category = get_application_fee_category(course_name, academic_session)
        if not fee_category:
            self.add_error('course_name', _('The selected programme does not have a configured application fee category for this academic session. Please contact the Admissions Office.'))
            return cleaned_data

        application_fee_exists = ApplicationFee.objects.filter(
            academic_session=academic_session,
            category=fee_category,
            name='Application Form',
        ).exists()
        if not application_fee_exists:
            self.add_error(
                'academic_session',
                _(
                    'No Application Form fee is configured for the selected programme '
                    'and academic session.'
                ),
            )

        return cleaned_data
            

class StudentCreationForm(forms.ModelForm):
    first_name = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'Enter your First Name', 'class': 'custom-class red-asterisk'}),
        label='First Name:'
    )
    middle_name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Enter your Middle Name', 'class': 'custom-class'}),
        label='Middle Name:'
    )
    last_name = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'Enter your Surname', 'class': 'custom-class'}),
        label='Surname:'
    )
    course_name = forms.ModelChoiceField(
        required=True,
        queryset=CourseOfStudy.objects.all(),
        widget=forms.Select(attrs={'class': 'custom-class'}),
        label='Program of Choice:'
    )
    academic_session = forms.ModelChoiceField(
        required=True,
        queryset=AcademicSession.objects.filter(is_active=True).order_by('-start_date', '-end_date', '-name'),
        widget=forms.Select(attrs={'class': 'custom-class'}),
        label='Academic Session:'
    )

    class Meta:
        model = PreApplicant
        fields = ['first_name', 'middle_name', 'last_name', 'phone_number', 'course_name', 'academic_session']

    def __init__(self, *args, **kwargs):
        super(StudentCreationForm, self).__init__(*args, **kwargs)
        active_sessions = AcademicSession.objects.filter(is_active=True).order_by('-start_date', '-end_date', '-name')
        self.fields['academic_session'].queryset = active_sessions if active_sessions.exists() else AcademicSession.objects.all().order_by('-start_date', '-end_date', '-name')


class StudentLevelForm(forms.Form):
    STUDENT_LEVEL_CHOICES = [
        ('Remedial Programme', 'Remedial Programme'),
        ('NDI', 'NDI'),
        ('NDII', 'NDII'),
    ]
    STUDENT_SEMESTER_CHOICES = [
        ('First Semester', 'First Semester'),
        ('Second Semester', 'Second Semester'),
    ]

    student_id = forms.CharField(
        max_length=40,
        label='Student ID',
        widget=forms.TextInput(attrs={'placeholder': 'Enter Student ID'})
    )
    level = forms.ChoiceField(
        choices=STUDENT_LEVEL_CHOICES,
        label='Level'
    )
    semester = forms.ChoiceField(
        choices=STUDENT_SEMESTER_CHOICES,
        label='Semester'
    )


class ProfileImageUploadForm(forms.Form):
    profile_image = forms.ImageField(label='Upload Student Profile Image')


class ApplicantContactForm(forms.ModelForm):
    home_address = forms.CharField(label='Home Address:', max_length=120, widget=forms.TextInput(attrs={'placeholder': 'Enter your Home Town Address.'}))

    class Meta:
        model = Profile
        fields = ['nationality', 'home_address', 'state', 'lga']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['lga'].queryset = LocalGovernment.objects.none()

        if 'state' in self.data:
            try:
                state_id = int(self.data.get('state'))
                self.fields['lga'].queryset = LocalGovernment.objects.filter(state_id=state_id)
            except (ValueError, TypeError):
                pass 
        elif self.instance.pk and self.instance.state:
            self.fields['lga'].queryset = self.instance.state.localgovernment_set


class ApplicantProfileDateField(forms.DateInput):
    """Prepare Profiel Date Field"""
    input_type = 'date' 


class ApplicantEditContactForm(forms.ModelForm):
    first_name = forms.CharField(label='First Name:', disabled=True, max_length=30, widget=forms.TextInput(attrs={'placeholder': 'Enter  First Name'}))
    middle_name = forms.CharField(label='Middle Name:', disabled=True, max_length=30, required=False, widget=forms.TextInput(attrs={'placeholder': 'Enter  Middle Name'}))
    last_name = forms.CharField(label='Surname:', max_length=30, disabled=True, widget=forms.TextInput(attrs={'placeholder': 'Enter Your Last Name'}))
    phone_number = forms.CharField(label='Phone Number:', max_length=11)
    home_address = forms.CharField(label='Home Address:', max_length=120, widget=forms.TextInput(attrs={'placeholder': 'Enter your Home Town Address.'}))
        
    class Meta:
        widgets = {'date_birth': ApplicantProfileDateField()}
        model = Profile
        fields = ['first_name', 'middle_name', 'last_name', 'date_birth', 'phone_number', 'gender', 'marital_status', 'religion', 'nationality', 'home_address', 'state', 'lga', 'image']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['lga'].queryset = LocalGovernment.objects.none()

        if 'state' in self.data:
            try:
                state_id = int(self.data.get('state'))
                self.fields['lga'].queryset = LocalGovernment.objects.filter(state_id=state_id)
            except (ValueError, TypeError):
                pass 
        elif self.instance.pk and self.instance.state:
            self.fields['lga'].queryset = self.instance.state.localgovernment_set


class CandidateEditContactForm(forms.ModelForm):
    first_name = forms.CharField(
        label='First Name:', 
        disabled=True, 
        max_length=30, 
        widget=forms.TextInput(attrs={'placeholder': 'Enter First Name'})
    )
    middle_name = forms.CharField(
        label='Middle Name:', 
        disabled=True, 
        max_length=30, 
        required=False, 
        widget=forms.TextInput(attrs={'placeholder': 'Enter Middle Name'})
    )
    last_name = forms.CharField(
        label='Surname:', 
        max_length=30, 
        disabled=True, 
        widget=forms.TextInput(attrs={'placeholder': 'Enter Your Last Name'})
    )
    phone_number = forms.CharField(
        label='Phone Number:', 
        max_length=11
    )
    home_address = forms.CharField(
        label='Home Address:', 
        max_length=120, 
        widget=forms.TextInput(attrs={'placeholder': 'Enter your Home Town Address.'})
    )

    date_birth = forms.CharField(
        label='Date of Birth:',
        disabled=True,
        )
    
    class Meta:
       
        model = Profile
        fields = [
            'first_name', 'middle_name', 'last_name', 'date_birth', 'phone_number', 
            'gender', 'marital_status', 'religion', 'nationality', 'home_address', 
            'state', 'lga', 'image'
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['lga'].queryset = LocalGovernment.objects.none()

        if 'state' in self.data:
            try:
                state_id = int(self.data.get('state'))
                self.fields['lga'].queryset = LocalGovernment.objects.filter(state_id=state_id)
            except (ValueError, TypeError):
                pass 
        elif self.instance.pk and self.instance.state:
            self.fields['lga'].queryset = self.instance.state.localgovernment_set



class SponsorDetailsForm(forms.ModelForm):
    full_name = forms.CharField(label='Full Name:', max_length=30, widget=forms.TextInput(attrs={'placeholder': 'Enter  Sponsor\'s Full Name'}))
    phone_number = forms.CharField(label='Phone Number:', max_length=30, required=True, widget=forms.TextInput(attrs={'placeholder': 'Enter  Sponsor\'s Phone Number'}))
    email = forms.EmailField(label='Email Address:', max_length=36, required=False, widget=forms.TextInput(attrs={'placeholder': 'Enter Sponsor\'s Email Address'}))
    home_address = forms.CharField(label='Contact Address:', max_length=200, required=True, widget=forms.TextInput(attrs={'placeholder': 'Enter Sponsor\'s Contact Address'}))

    class Meta:
        model = SponsorDetails
        fields = ['full_name', 'occupation', 'phone_number', 'email', 'home_address', 'sponsor_relationship']


class EducationRecordForm(forms.ModelForm):
    school_name = forms.CharField(label='School Name in Full:', max_length=225, widget=forms.TextInput(attrs={'placeholder': 'Enter Name of Shool in Full, including Place. No Abbreviations.'}))
        
    class Meta:
        model = EducationRecord
        fields = ['school_name', 'certificate', 'admitted_year', 'end_year'] 
    
    def clean_school_name(self):
        school_name = self.cleaned_data.get('school_name')
        # Check if user input is an abbreviation
        if len(school_name) <= 4:
            raise ValidationError('Please enter the Full Name of the School.')
        return school_name


class ApplicantResultForm(forms.ModelForm):
    current_year = datetime.now().year
    YEAR_CHOICES = [(str(year), str(year)) for year in range(1900, current_year + 1)]

    exam_year = forms.ChoiceField(
        label='Year of Examination:',
        choices=YEAR_CHOICES,
        required=True,
        widget=forms.Select(attrs={'placeholder': 'Enter Year of Examination'})
    )

    class Meta:
        model = ApplicantResult
        fields = ['result_name', 'subject_name', 'subject_grade', 'exam_number', 'exam_year']

    def clean_exam_number(self):
        result_name = self.cleaned_data.get('result_name')
        exam_number = self.cleaned_data.get('exam_number')

        if result_name == 'WAEC':
            # Check if exam_number is numeric and has at least 10 digits
            if not exam_number.isdigit() or len(exam_number) < 10:
                raise ValidationError('For WAEC, exam_number must be numeric and have at least 10 digits.')

        elif result_name == 'NECO':
            # Check if exam_number has 8-10 digits followed by 2 alphabets
            if not (8 <= len(exam_number[:-2]) <= 10) or not exam_number[:-2].isdigit() or not exam_number[-2:].isalpha():
                raise ValidationError('For NECO, examination number must have 8 to 10 numeric digits followed by 2 alphabets.')
            else:
                # Forcefully convert the last two characters to uppercase
                exam_number = exam_number[:-2] + exam_number[-2:].upper()

        elif result_name == 'NABTEB':
            # Check if exam_number is 12 digits and numeric
            if not exam_number.isdigit() or len(exam_number) != 12:
                raise ValidationError('For NABTEB, exam_number must be 12 numeric digits.')

        return exam_number


class ApplicationSubmissionForm(forms.ModelForm):
    confirm = forms.BooleanField()
    
    class Meta:
        model = ApplicationSubmission
        fields = ['confirm'] 


class StaffRegSubmissionForm(forms.ModelForm):
    confirm = forms.BooleanField()

    class Meta:
        model = staffAppSubmission
        fields = ['confirm']


class ApplicationReportForm(forms.Form):
    academic_session = forms.ModelChoiceField(queryset=AcademicSession.objects.all(), required=True)
    course_of_study = forms.ModelChoiceField(queryset=CourseOfStudy.objects.all(), required=True)
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), initial=timezone.now().date())
    end_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), initial=timezone.now().date())


class ApplicationReportByProgrammForm(forms.Form):
    academic_session = forms.ModelChoiceField(queryset=AcademicSession.objects.all(), required=True)
    course_of_study = forms.ModelChoiceField(queryset=CourseOfStudy.objects.all(), required=True)
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), initial=timezone.now().date())
    end_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), initial=timezone.now().date()) 

class ApplicationlistBySessionForm(forms.Form):
    academic_session = forms.ModelChoiceField(queryset=AcademicSession.objects.all(), required=True)
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), initial=timezone.now().date())
    end_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), initial=timezone.now().date())

class AdmitByProgramForm(forms.Form):
    academic_session = forms.ModelChoiceField(
        queryset=AcademicSession.objects.all().order_by('-start_date', '-end_date', '-name'),
        required=True,
        empty_label='Select Academic Session...',
    )
    course_of_study = forms.ModelChoiceField(
        queryset=CourseOfStudy.objects.all().order_by('name'),
        required=True,
        empty_label='Select Programme...',
    )
    application_category = forms.ChoiceField(required=False, choices=[])
    search_query = forms.CharField(
        max_length=100,
        required=False,
        label='Search',
        help_text='Optional: application number, candidate name, programme, category, or session',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        categories = (
            ApplicationFee.objects
            .exclude(category__isnull=True)
            .exclude(category='')
            .values_list('category', flat=True)
            .distinct()
            .order_by('category')
        )
        self.fields['application_category'].choices = [('', 'All Categories')] + [
            (category, category) for category in categories
        ]


class SearchStudentForm(forms.Form): 
    search_id = forms.CharField(max_length=6, required=True, label='Search by Application ID')


class AdmissionListForm(forms.Form):
    academic_session = forms.ModelChoiceField(queryset=AcademicSession.objects.all(), required=True, empty_label="Select Academic Session...")
    level = forms.ChoiceField(choices=[('Remedial Programme', 'Remedial Programme'), ('NDI', 'NDI')], widget=forms.Select(), required=True)
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), initial=timezone.now().date())
    end_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), initial=timezone.now().date())

class CSVUploadForm(forms.Form):
    file = forms.FileField(
        label='Select a CSV file',
        help_text='Max. 42 megabytes',
        validators=[FileExtensionValidator(allowed_extensions=['csv'])]
    )


class SearchSubmitedApplicationForm(forms.Form): #Normal Submitted Application Search Form
    search_query = forms.CharField(max_length=100, required=False, label='Search by Application ID')


class PreApplicationEditForm(forms.ModelForm):
    academic_session = forms.ModelChoiceField(required=True, queryset=AcademicSession.objects.all().order_by('-start_date', '-end_date', '-name'), widget=forms.Select(attrs={'class': 'custom-class'}), label='Academic Session:')
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'placeholder': 'Enter your Email/Username', 'class': 'custom-class red-asterisk'}),
        label='Email/Username:'
    )
    first_name = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'Enter your First Name', 'class': 'custom-class red-asterisk'}),
        label='First Name:'
    )
    middle_name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Enter your Middle Name', 'class': 'custom-class'}),
        label='Middle Name:'
    )
    last_name = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'Enter your Surname', 'class': 'custom-class'}),
        label='Surname:'
    )
    course_name = forms.ModelChoiceField(
        required=True,
        queryset=CourseOfStudy.objects.all(),
        widget=forms.Select(attrs={'class': 'custom-class'}),
        label='Program of Choice:'
    )

    class Meta:
        model = PreApplicant
        fields = ['email', 'first_name', 'middle_name', 'last_name', 'phone_number', 'course_name', 'academic_session']


class ApplicantProfileDateField(forms.DateInput):
    """Prepare Profiel Date Field"""
    input_type = 'date'


class ApplicantProfileForm(forms.ModelForm):
    first_name = forms.CharField(label='First Name:', max_length=30, widget=forms.TextInput(attrs={'placeholder': 'Enter  First Name'}))
    middle_name = forms.CharField(label='Middle Name:', max_length=30, required=False, widget=forms.TextInput(attrs={'placeholder': 'Enter  Middle Name'}))
    last_name = forms.CharField(label='Surname:', max_length=30, widget=forms.TextInput(attrs={'placeholder': 'Enter Your Last Name'}))
    phone_number = forms.CharField(label='Phone Number:', max_length=11)

    class Meta:
        widgets = {'date_birth': ApplicantProfileDateField()}
        model = Profile
        fields = ['first_name', 'middle_name', 'last_name', 'phone_number', 'date_birth', 'gender', 'marital_status', 'Number_children', 'religion', 'image']


class SearchPreapplicantForm(forms.Form): #Normal Staff User Search Form
    academic_session = forms.ModelChoiceField(
        required=True,
        queryset=AcademicSession.objects.all(),
        widget=forms.Select(attrs={'class': 'custom-class'}),
        label='Academic Session:'
    )
    search_query = forms.CharField(max_length=100, required=False, label='Search by Names')


class AdminApplicantEditContactForm(forms.ModelForm):
    first_name = forms.CharField(label='First Name:', max_length=30, widget=forms.TextInput(attrs={'placeholder': 'Enter  First Name'}))
    middle_name = forms.CharField(label='Middle Name:', max_length=30, required=False, widget=forms.TextInput(attrs={'placeholder': 'Enter  Middle Name'}))
    last_name = forms.CharField(label='Surname:', max_length=30, widget=forms.TextInput(attrs={'placeholder': 'Enter Your Last Name'}))
    phone_number = forms.CharField(label='Phone Number:', max_length=11)
    home_address = forms.CharField(label='Home Address:', max_length=120, widget=forms.TextInput(attrs={'placeholder': 'Enter your Home Town Address.'}))
        
    class Meta:
        widgets = {'date_birth': ApplicantProfileDateField()}
        model = Profile
        fields = ['first_name', 'middle_name', 'last_name', 'date_birth', 'phone_number', 'gender', 'marital_status', 'Number_children', 'religion', 'nationality', 'home_address', 'state', 'lga', 'image']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['lga'].queryset = LocalGovernment.objects.none()

        if 'state' in self.data:
            try:
                state_id = int(self.data.get('state'))
                self.fields['lga'].queryset = LocalGovernment.objects.filter(state_id=state_id)
            except (ValueError, TypeError):
                pass 
        elif self.instance.pk:
            self.fields['lga'].queryset = self.instance.state.localgovernment_set


class ApplicationNumberForm(forms.Form):
    application_number = forms.CharField(max_length=20, label='Application Number')


class ApplicationSubmissionForm(forms.ModelForm):
    confirm = forms.BooleanField()
    
    class Meta:
        model = ApplicationSubmission
        fields = ['confirm']


class CandidateJambForm(forms.ModelForm):
    exam_name = forms.CharField(label='Examination Name:', max_length=30, disabled=True)
    exam_year = forms.CharField(label='Examination Year:', max_length=30, disabled=True)
    exam_number = forms.CharField(label='JAMB Reg. Number:', max_length=30, widget=forms.TextInput(attrs={'placeholder': 'Enter  JAMB Registration Number'}))
    total_score = forms.IntegerField(label='JAMB Scores:', required=True)

    class Meta:
        model = Jamb
        fields = ['exam_name', 'exam_year', 'exam_number', 'total_score']

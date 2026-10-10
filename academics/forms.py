from django import forms
# from django.contrib.auth.models import User
# from django.contrib.auth.forms import UserCreationForm
from .models import (Faculty, Postutmlformat,
                     Department, StaffFile,
                     CourseOfStudy,
                     AcademicSession, Student,
                     Semester, CourseUnit, Level
                     )
import re
from django.core.validators import FileExtensionValidator 
from accounts.models import StaffPublication
from accounts.models import Profile

class SemesterstartDateField(forms.DateInput):
    """Prepare Profiel Date Field"""
    input_type = 'date'


class SemesterclostDateField(forms.DateInput):
    """Prepare Profiel Date Field"""
    input_type = 'date'


class SemesterCreationForm(forms.ModelForm):
    name = forms.CharField(max_length=60, required=True, label='Name of Academic Semester:')
    academic_session = forms.ModelChoiceField(queryset=AcademicSession.objects.all().order_by('-start_date', '-end_date', '-name'), required=True, empty_label='Select Academic Session...')

    class Meta:
        model = Semester
        fields = ['academic_session', 'name', 'date_resumption', 'closing_date', 'is_active']
        widgets = {
            'date_resumption': SemesterstartDateField(),
            'closing_date': SemesterclostDateField(),
        }

    def clean_name(self):
        name = self.cleaned_data.get('name')
        return ' '.join(name.split()).title() if name else name

    def clean(self):
        cleaned_data = super().clean()
        academic_session = cleaned_data.get('academic_session')
        name = cleaned_data.get('name')
        if academic_session and name:
            duplicate = Semester.objects.filter(academic_session=academic_session, name__iexact=name)
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise forms.ValidationError('This semester already exists for the selected academic session.')
        return cleaned_data


class LevelCreationForm(forms.ModelForm):

    class Meta:
        model = Level
        fields = ['name']


class SessionCreationForm(forms.ModelForm):
    name = forms.CharField(max_length=60, required=True, label='Name of Academic Session:')

    class Meta:
        model = AcademicSession
        fields = ['name', 'start_date', 'end_date', 'resumption_date', 'closing_date', 'admission_date', 'is_active']
        widgets = {
            'start_date': SemesterclostDateField(),
            'end_date': SemesterclostDateField(),
            'resumption_date': SemesterclostDateField(),
            'closing_date': SemesterclostDateField(),
            'admission_date': SemesterclostDateField(),
        }



class SchoolCreationForm(forms.ModelForm):
    name = forms.CharField(max_length=100, required=True, label='Name of School:')

    def clean_name(self):
        # Ensure the first letter is capitalized
        name = self.cleaned_data.get('name')
        if name:
            return ' '.join(word.capitalize() for word in name.split())
        return name

    class Meta:
        model = Faculty
        fields = ['name']


class DepartmentCreationForm(forms.ModelForm):
    name = forms.CharField(max_length=100, required=True, label='Name of Department:')
    faculty = forms.ModelChoiceField(queryset=Faculty.objects.all(), required=True, label='Name of School/Faculty:')

    def clean_name(self):
        # Ensure the first letter is capitalized
        name = self.cleaned_data.get('name')
        if name:
            return ' '.join(word.capitalize() for word in name.split())
        return name
    
    class Meta:
        model = Department
        fields = ['name', 'dept_type', 'faculty']


class FacultyFilterForm(forms.Form):
    school = forms.ModelChoiceField(queryset=Faculty.objects.all(), empty_label="Select School...")


class ProgramCreationForm(forms.ModelForm):
    name = forms.CharField(max_length=100, required=True, label='Name of Program:')

    def clean_name(self):
        name = self.cleaned_data.get('name')
        if name:
            # Use re.sub directly to uppercase every word
            uppercased_name = re.sub(r"\b[\w'-]+\b", lambda match: match.group().upper(), name)
            return uppercased_name

        return name

    class Meta:
        model = CourseOfStudy
        fields = ['name', 'department']


class DepartmentFilterForm(forms.Form):
    department_name = forms.ModelChoiceField(queryset=Department.objects.all(), empty_label="Select Department...")


class CourseCreationUnitForm(forms.ModelForm):
    academic_session = forms.ModelChoiceField(
        queryset=AcademicSession.objects.all().order_by('-start_date', '-end_date', '-name'),
        required=True,
        empty_label='Select Academic Session...'
    )

    class Meta:
        model = CourseUnit
        fields = ['academic_session', 'semester', 'course_of_study', 'level', 'course_code', 'course_title', 'credit_load', 'is_core_course', 'is_elective_course']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        session_id = None
        if self.data.get('academic_session'):
            session_id = self.data.get('academic_session')
        elif self.instance.pk and self.instance.academic_session_id:
            session_id = self.instance.academic_session_id

        self.fields['semester'].queryset = Semester.objects.none()
        if session_id:
            self.fields['semester'].queryset = Semester.objects.filter(academic_session_id=session_id).order_by('name')
        elif self.instance.pk and self.instance.semester_id:
            self.fields['semester'].queryset = Semester.objects.filter(pk=self.instance.semester_id)

    def clean_course_code(self):
        course_code = self.cleaned_data.get('course_code')
        return course_code.strip().upper() if course_code else course_code

    def clean_course_title(self):
        course_title = self.cleaned_data.get('course_title')
        return ' '.join(course_title.split()).title() if course_title else course_title

    def clean(self):
        cleaned_data = super().clean()
        academic_session = cleaned_data.get('academic_session')
        semester = cleaned_data.get('semester')
        course_of_study = cleaned_data.get('course_of_study')
        level = cleaned_data.get('level')
        course_code = cleaned_data.get('course_code')
        course_title = cleaned_data.get('course_title')

        if academic_session and semester and semester.academic_session_id != academic_session.id:
            raise forms.ValidationError('Select a semester that belongs to the selected academic session.')

        if all([academic_session, semester, course_of_study, level, course_code, course_title]):
            duplicate = CourseUnit.objects.filter(
                academic_session=academic_session,
                semester=semester,
                course_of_study=course_of_study,
                level=level,
                course_code__iexact=course_code,
                course_title__iexact=course_title,
            )
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise forms.ValidationError('This course unit already exists for the selected session, semester, programme, and level.')

        return cleaned_data
    

class CourseUnitFilterForm(forms.Form):
    academic_session = forms.ModelChoiceField(queryset=AcademicSession.objects.all().order_by('-start_date', '-end_date', '-name'), required=True, empty_label="Select Academic Session...")
    level = forms.ModelChoiceField(queryset=Level.objects.all(), required=True, empty_label="Select Level...")
    program = forms.ModelChoiceField(queryset=CourseOfStudy.objects.all(), required=True, empty_label="Select Program...")
    semester = forms.ModelChoiceField(queryset=Semester.objects.none(), required=True, empty_label="Select Semester...")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        session_id = self.data.get('academic_session') if self.data else None
        if session_id:
            self.fields['semester'].queryset = Semester.objects.filter(academic_session_id=session_id).order_by('name')


class StudentsPromotionForm(forms.Form):
    # Get all CourseOfStudy objects excluding "Remedial Science"
    course_of_study_queryset = CourseOfStudy.objects.exclude(name='Remedial Sciences')

    course_of_study = forms.ModelChoiceField(queryset=course_of_study_queryset, required=True)
    level = forms.ChoiceField(choices=[('NDI', 'NDI'), ('HNDI', 'HNDI')], widget=forms.Select(), required=True)


class StudentGraduationFilterForm(forms.Form):
    course = forms.ModelChoiceField(
        queryset=CourseOfStudy.objects.all().order_by('name', 'pk'),
        empty_label='Select course of study...',
    )
    level = forms.ModelChoiceField(
        queryset=Level.objects.all().order_by('name', 'pk'),
        empty_label='Select level...',
    )
    session = forms.ModelChoiceField(
        queryset=AcademicSession.objects.all().order_by('-start_date', '-end_date', '-name'),
        empty_label='Select graduating session...',
    )
    search = forms.CharField(required=False, max_length=100)


class SearchStudentForm(forms.Form):
    student_id = forms.CharField(max_length=100, required=True, label='Student ID:')


class StudentAcademicCorrectionForm(forms.Form):
    level = forms.ModelChoiceField(
        queryset=Level.objects.all().order_by('name', 'pk'),
        empty_label='Select current level...',
        label='New current level',
    )
    admitted_session = forms.ModelChoiceField(
        queryset=AcademicSession.objects.all().order_by('-start_date', '-end_date', '-name'),
        empty_label='Select admitted academic session...',
        label='New admitted academic session',
    )
    confirm = forms.BooleanField(
        label='I confirm these changes are correct and should be saved.',
        widget=forms.HiddenInput(),
    )


class EditStudentNamesForm(forms.ModelForm):
    first_name = forms.CharField(max_length=30, required=False)
    middle_name = forms.CharField(max_length=30, required=False)
    last_name = forms.CharField(max_length=30, required=False)

    class Meta:
        model = Profile
        fields = ['first_name', 'middle_name', 'last_name']


class CSVSampleUploadForm(forms.ModelForm):
    csv_file = forms.FileField(
        label='Select a CSV file',
        help_text='Max. 42 megabytes',
        validators=[FileExtensionValidator(allowed_extensions=['csv'])]
    )

    class Meta:
        model = Postutmlformat
        fields = ['name', 'csv_file']


class StaffPublicationCreationForm(forms.ModelForm):
    publication_reference = forms.CharField(max_length=255, required=True, label='Publication Reference:')

    class Meta:
        model = StaffPublication
        fields = ['publication_existence', 'publication_platform', 'publication_reference', 'publication_url']


class StaffFileActivationForm(forms.ModelForm):

    class Meta:
        model = StaffFile
        fields = ['file_number']


class StaffSearchForm(forms.Form):
    file_number = forms.CharField(label='File Number', max_length=100, required=True)


class LevelSearchForm(forms.Form):
    LEVEL_CHOICES = [
        ('Remedial Programme', 'Remedial Programme'),
        ('NDI', 'NDI'),
        ('NDII', 'NDII'),
        ('HNDI', 'HNDI'),
        ('HNDII', 'HNDII'),
    ]
    academic_session = forms.ModelChoiceField(queryset=AcademicSession.objects.all(), required=True, empty_label="Select Academic Session...")
    admitted_level = forms.ChoiceField(choices=LEVEL_CHOICES, required=True, label='Select Level')


class EditStudentProgrammeForm(forms.ModelForm):
    student_id = forms.CharField(max_length=60, required=True, disabled=True)
    academic_session = forms.ModelChoiceField(queryset=AcademicSession.objects.all(), required=True, empty_label="Select Session...")
    current_level = forms.CharField(max_length=30, required=True)
    course = forms.ModelChoiceField(queryset=CourseOfStudy.objects.all(), required=True, empty_label="Select Program...")

    class Meta:
        model = Student
        fields = ['student_id', 'current_level', 'course', 'academic_session']


class StudentLevelProgrammeSessionForm(forms.Form):
    LEVEL_CHOICES = [
        ('Remedial Programme', 'Remedial Programme'),
        ('NDI', 'NDI'),
        ('NDII', 'NDII'),
        ('HNDI', 'HNDI'),
        ('HNDII', 'HNDII'),
    ]
    academic_session = forms.ModelChoiceField(queryset=AcademicSession.objects.all(), required=True, empty_label="Select Academic Session...")
    admitted_level = forms.ChoiceField(choices=LEVEL_CHOICES, required=True, label='Select Level')
    programme_study = forms.ModelChoiceField(queryset=CourseOfStudy.objects.all(), required=True, empty_label="Select Program...")


class SearchStudentCourseReg(forms.Form): #Normal Submitted Application Search Form
    search_query = forms.CharField(max_length=100, required=False, label='Search by Student ID')


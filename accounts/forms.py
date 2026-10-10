from django import forms
# from .models import Profile
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import Profile, LocalGovernment, AcademicStaff, StaffRegister
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit


class ProfileDateField(forms.DateInput):
    """Prepare Profiel Date Field"""
    input_type = 'date'


class ProfileForm(forms.ModelForm):
    first_name = forms.CharField(label='First Name:', max_length=30, disabled=True, widget=forms.TextInput(attrs={'placeholder': 'Enter  First Name'}))
    middle_name = forms.CharField(label='Middle Name:', max_length=30, disabled=True, required=False, widget=forms.TextInput(attrs={'placeholder': 'Enter  Middle Name'}))
    last_name = forms.CharField(label='Surname:', max_length=30, disabled=True, widget=forms.TextInput(attrs={'placeholder': 'Enter Your Last Name'}))
    phone_number = forms.CharField(label='Phone Number:', max_length=11)

    class Meta:
        widgets = {'date_birth': ProfileDateField()}
        model = Profile
        fields = ['first_name', 'middle_name', 'last_name', 'phone_number', 'date_birth', 'gender', 'marital_status', 'Number_children', 'religion', 'image']


class UpdateStaffProfileForm(forms.ModelForm):
    first_name = forms.CharField(label='First Name:', max_length=30, widget=forms.TextInput(attrs={'placeholder': 'Enter  First Name'}))
    middle_name = forms.CharField(label='Middle Name:', max_length=30, required=False, widget=forms.TextInput(attrs={'placeholder': 'Enter  Middle Name'}))
    last_name = forms.CharField(label='Surname:', max_length=30, widget=forms.TextInput(attrs={'placeholder': 'Enter Your Last Name'}))
    phone_number = forms.CharField(label='Phone Number:', max_length=11)

    class Meta:
        widgets = {'date_birth': ProfileDateField()}
        model = Profile
        fields = ['first_name', 'middle_name', 'last_name', 'phone_number', 'date_birth', 'gender', 'marital_status', 'Number_children', 'religion', 'home_address', 'nationality', 'state', 'lga', 'image']

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


class CandidateProfileForm(forms.ModelForm):
    phone_number = forms.CharField(label='Phone Number:', max_length=11)

    class Meta:
        model = Profile
        fields = ['phone_number', 'religion', 'marital_status', 'Number_children', 'image']


class StudentProfileForm(forms.ModelForm):
    phone_number = forms.CharField(label='Phone Number:', max_length=11)

    class Meta:
        widgets = {'date_birth': ProfileDateField()}
        model = Profile
        fields = ['date_birth', 'gender', 'religion', 'marital_status', 'Number_children', 'image']


class SuperAdminUserCreationForm(UserCreationForm):
    role = forms.ChoiceField(choices=[('MIS ADMIN-1', 'MIS ADMIN-1'), ('Admin', 'Admin'), ('ADMISSION-OFFICER', 'ADMISSION-OFFICER'), ('ADMISSION-OFFICER1', 'ADMISSION-OFFICER1'), ('MIS ADMIN-2', 'MIS ADMIN-2'), ('Rector', 'Rector'), ('Super Admin', 'Super Admin')], widget=forms.Select())

    class Meta:
        model = User
        fields = ['username', 'password1', 'password2', 'role']


class StaffUserCreation(UserCreationForm):

    class Meta:
        model = User
        fields = ['username', 'password1', 'password2']


class PasswordChangeForm(forms.Form): # User Change Password Form
    old_password = forms.CharField(widget=forms.PasswordInput)
    new_password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)


class SearchUserForm(forms.Form): #Normal Staff User Search Form
    search_query = forms.CharField(max_length=100, required=False, label='Search by Username')


class SuperAdminAcademicStaffCreationForm(forms.ModelForm):
    qualification_date = forms.CharField(label='Qualification with Dates:', widget=forms.Textarea(attrs={'placeholder': 'Enter Staff Qualifications with dates, separated with comma. E.g. B.Eng – Computer Engineering (2010)'}))
    current_rank = forms.CharField(label='Current Rank:', max_length=100, widget=forms.TextInput(attrs={'placeholder': 'Enter Staff Current Rank'}))
    courses_taught = forms.CharField(max_length=200, required=False, label='Course(s) Taught:', widget=forms.TextInput(attrs={'class': 'courses-taught-field'}))
    internal_appointment = forms.CharField(max_length=200, required=False, label='Internal Appointment:', widget=forms.TextInput(attrs={'class': 'internal-appointment-field'}))
    
    class Meta:
        model = AcademicStaff
        fields = ['qualification_date', 'staff_type',  'current_rank', 'internal_appointment', 'work_experience', 'courses_taught', 'upload_cv']


class SuperUserEditStaffRegisterForm(forms.ModelForm): 
    
    file_number = forms.CharField(label='Staff File No::', max_length=120, disabled=True)
    
    Pay_grade = forms.ChoiceField(
        required=True,
        choices=[],  # Initially empty, will be populated dynamically
        widget=forms.Select(attrs={'class': 'custom-class'}),
        label='Pay Grade:'
    )

    class Meta:
        model = StaffRegister
        fields = ['file_number', 'Number_children', 'email', 'phone_number', 'Pay_grade', 'nin_number',
                  'department', 'directorate', 'dept_type', 'Appointment_type', 'Appointment_Mode',
                  'Promotion_status', 'First_appointment']
        labels = {
            'First_appointment': 'Date of First Appointment'
        }
    
    def __init__(self, *args, **kwargs):
        super(SuperUserEditStaffRegisterForm, self).__init__(*args, **kwargs)
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


class SuperUserEditStaffAcademicRecordForm(forms.ModelForm):
    qualification_date = forms.CharField(label='Qualification with Dates:', widget=forms.Textarea(attrs={'placeholder': 'Enter Staff Qualifications with dates, separated with comma. E.g. B.Eng – Computer Engineering (2010)'}))
    courses_taught = forms.CharField(max_length=200, required=False, label='Course(s) Taught:', widget=forms.TextInput(attrs={'class': 'courses-taught-field'}))
    internal_appointment = forms.CharField(max_length=200, required=False, label='Internal Appointment:', widget=forms.TextInput(attrs={'class': 'internal-appointment-field'}))

    class Meta:
        model = AcademicStaff
        fields = ['qualification_date', 'staff_type', 'current_rank', 
                  'work_experience', 'internal_appointment',
                  'courses_taught', 'upload_cv']
        

# Super Admin Change User Password Form
class PasswordResetForm(forms.Form):
    username = forms.CharField(label='Username')
    new_password = forms.CharField(label='New Password', widget=forms.PasswordInput)

    def clean_username(self):
        username = self.cleaned_data['username']
        if not User.objects.filter(username=username).exists():
            raise forms.ValidationError("Invalid username.")
        return username


class UserLogSearchForm(forms.Form):
    username = forms.CharField(label='Username', max_length=100, required=False)


class UserActivityLogSearchForm(forms.Form):
    q = forms.CharField(label='Search', required=False)

    def __init__(self, *args, **kwargs):
        super(UserActivityLogSearchForm, self).__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'GET'
        self.helper.layout = Layout(
            'q',
            Submit('submit', 'Search')
        )
    

class EditUserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username']
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from PIL import Image
from phonenumber_field.modelfields import PhoneNumberField
from imagekit.processors import ResizeToFill, Transpose
from academics.models import Department
from django.utils.deconstruct import deconstructible
from django.core.validators import FileExtensionValidator, MaxValueValidator
from django.utils.translation import gettext_lazy as _
from academics.models import Directorate


STUDENT_ROLE = 'Student'
GRADUATE_ROLE = 'Graduate'


# Image file upload validator
def validate_image_size(value):
    max_size = 25 * 1024  # 60 KB in bytes
    if value.size > max_size:
        raise ValidationError("File size must NOT be more than 25KB.")
    

def validate_cv_size(value):
    max_size = 1 * 1024 * 1024  # 1 MB
    if value.size > max_size:
        raise ValidationError("File size must NOT be more than 1 MB.")


class State(models.Model):
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name


class LocalGovernment(models.Model):
    state = models.ForeignKey(State, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name


class Profile(models.Model):
    RELIGION_CHOICES = [
        ('Christianity', 'Christianity'),
        ('Muslim', 'Muslim'),
        ('Traditional', 'Traditional'),
    ]
    NATION_CHOICES = [
        ('Nigeria', 'Nigeria'),
        ('Non Nigerian', 'Non Nigerian'),
    ]
    STATUS_CHOICES = [
        ('Single', 'Single'),
        ('Married', 'Married'),
        ('Divorced', 'Divorced'),
        ('Widowed', 'Widowed'),
    ]
    GENDER_CHOICES = [
        ('Male', 'Male'),
        ('Female', 'Female'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    first_name = models.CharField(max_length=30, blank=True, null=True)
    middle_name = models.CharField(max_length=30, blank=True, null=True)
    last_name = models.CharField(max_length=30, blank=True, null=True)
    date_birth = models.DateField(blank=False, null=True)
    phone_number = models.CharField(max_length=11, blank=False, null=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    marital_status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    Number_children = models.IntegerField(default=0)
    home_address = models.CharField(max_length=200, blank=True, null=True)
    religion = models.CharField(max_length=40, choices=RELIGION_CHOICES)
    nationality = models.CharField(max_length=40, choices=NATION_CHOICES)
    state = models.ForeignKey(State, on_delete=models.CASCADE, null=True)
    lga = models.ForeignKey(LocalGovernment, on_delete=models.CASCADE, null=True)
    role = models.CharField(max_length=30, blank=True, null=True)
    image = models.ImageField(
        default='avatar.jpg',
        upload_to='profile_images',
        validators=[
            FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png']),
            validate_image_size,
        ],
    )
    last_updated = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    # Indigene method
    def indigene_status(self):
        if self.state and self.state.name == 'Benue': 
            return True
        else:
            return False
    
    def __str__(self):
        return f'{self.user.username}'


class UserActivityLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=255)

    def __str__(self):
        return f'{self.user}'


class StaffRegister(models.Model):
  
    APPOINTMENT_CATEGORY = [
        ('Full Time', 'Full Time'),
        ('Part Time', 'Part Time'),
    ]

    TYPE_CATEGORY = [
        ('Permanent & Pensionable', 'Permanent & Pensionable'),
        ('Temporary', 'Temporary'),
        ('contract', 'contract'),
    ]

    PROMOTION_CATEGORY = [
        ('Promoted', 'Promoted'),
        ('Not Promoted', 'Not Promoted')
    ]

    RELIGION_CHOICES = [
        ('Christianity', 'Christianity'),
        ('Muslim', 'Muslim'),
        ('Traditional', 'Traditional'),
    ]
    NATION_CHOICES = [
        ('Nigeria', 'Nigeria'),
        ('Non Nigerian', 'Non Nigerian'),
    ]
    STATUS_CHOICES = [
        ('Single', 'Single'),
        ('Married', 'Married'),
        ('Divorced', 'Divorced'),
        ('Widowed', 'Widowed'),
    ]
    GENDER_CHOICES = [
        ('Male', 'Male'),
        ('Female', 'Female'),
    ]
    DEPARTMENT_CATEGORY = [
        ('Academic', 'Academic'),
        ('Non-Academic', 'Non-Academic')
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='staff_profile')
    first_name = models.CharField(max_length=30, blank=True, null=True)
    middle_name = models.CharField(max_length=30, blank=True, null=True)
    last_name = models.CharField(max_length=30, blank=True, null=True)
    date_birth = models.DateField(blank=False, null=True)
    phone_number = models.CharField(max_length=11, blank=False, null=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    marital_status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    Number_children = models.IntegerField(default=0, blank=False)
    home_address = models.CharField(max_length=200, blank=False, null=True)
    religion = models.CharField(max_length=40, choices=RELIGION_CHOICES)
    nationality = models.CharField(max_length=40, choices=NATION_CHOICES)
    state = models.ForeignKey(State, on_delete=models.CASCADE, null=True)
    lga = models.ForeignKey(LocalGovernment, on_delete=models.CASCADE, null=True)
    email = models.EmailField(max_length=254, null=True, blank=False)
    Pay_grade = models.CharField(max_length=100)
    file_number = models.CharField(max_length=100, blank=False, null=True)
    nin_number = models.CharField(max_length=100, blank=False, null=True)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, blank=True, null=True, related_name='staff_department')
    directorate = models.ForeignKey(Directorate, on_delete=models.CASCADE, blank=True, null=True)
    dept_type = models.CharField(max_length=30, choices=DEPARTMENT_CATEGORY)
    Appointment_type = models.CharField(max_length=100, choices=TYPE_CATEGORY)
    Appointment_Mode = models.CharField(max_length=100, choices=APPOINTMENT_CATEGORY)
    Promotion_status = models.CharField(max_length=100, choices=PROMOTION_CATEGORY)
    First_appointment = models.DateField(blank=False, null=True)
    submitted_date = models.DateField(auto_now_add=True)
    registered_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='added_by')
    image = models.ImageField(
        default='avatar.jpg',
        upload_to='profile_images',
        validators=[
            FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png']),
            validate_image_size,
        ],
    )

    def __str__(self):
        return f'{self.user}'
    

class StaffPublication(models.Model):
    PUBLICATION_OPTIONS = [
        ('ONLINE', 'ONLINE'),
        ('OFFLINE', 'OFFLINE'),
    ]
    PUBLICATION_EXIST = [
        ('YES', 'YES'),
        ('NO', 'NO'),
    ]
    publication_existence = models.CharField(max_length=3, choices=PUBLICATION_EXIST)
    staff = models.ForeignKey(StaffRegister, on_delete=models.CASCADE, related_name='staff_publication')
    publication_platform = models.CharField(max_length=255, choices=PUBLICATION_OPTIONS, blank=True, null=True)
    publication_reference = models.CharField(max_length=255, blank=True, null=True)
    publication_url = models.URLField(max_length=200, blank=True, null=True)
    staff2 = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='staff_profile')
    submission_date = models.DateField(auto_now_add=True)
    submitted_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='published_by')
    
    def __str__(self):
        return f'{self.staff}'


class AcademicStaff(models.Model):
    STAFF_CATEGORY = [
        ('Academic', 'Academic'),
        ('Non-Academic', 'Non-Academic'),
    ]
    staff = models.OneToOneField(StaffRegister, on_delete=models.CASCADE, related_name='staff_qualification')
    qualification_date = models.CharField(max_length=255, blank=False, null=False)
    current_rank = models.CharField(max_length=60, blank=False, null=False)
    work_experience = models.CharField(max_length=255, blank=False, null=False)
    internal_appointment = models.CharField(max_length=60,  blank=False, null=False)
    staff_type = models.CharField(max_length=255, choices=STAFF_CATEGORY)
    courses_taught = models.CharField(max_length=255, blank=True, null=True)
    staff2 = models.OneToOneField(Profile, on_delete=models.CASCADE, related_name='academic_staff_profile')
    staff_publications = models.ManyToManyField(StaffPublication, related_name='academic_staff')
    upload_cv = models.FileField(
        upload_to='supporting_documents',
        validators=[
            FileExtensionValidator(allowed_extensions=['pdf', 'doc', 'docx', 'jpg', 'jpeg']),
            validate_cv_size,
        ], blank=False
    )
    submission_date = models.DateField(auto_now_add=True)
    recorded_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='recorded_by')

    def __str__(self):
        return f'Academic Staff: {self.id}'

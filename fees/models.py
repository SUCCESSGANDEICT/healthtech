from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth.models import User
from academics.models import CourseOfStudy, AcademicSession, Student, Level
from django.utils import timezone
from datetime import timedelta


INDIGENE = "INDIGENE"
NON_INDIGENE = "NON-INDIGENE"
INDIGENE_TYPE_CHOICES = [
    (INDIGENE, "INDIGENE"),
    (NON_INDIGENE, "NON-INDIGENE"),
]


class ApplicationFee(models.Model):
    LEVEL_CATEGORY_CHOICES = [('ND', 'ND'), ('HND', 'HND'), ('CERTIFICATE', 'CERTIFICATE')]
    FEE_CHOICES = [('Application Form', 'Application Form'), ('Screening Fee', 'Screening Fee'), ('Acceptance Fee', 'Acceptance Fee'), ('Caution Fee', 'Caution Fee'), ('Other Charges', 'Other Charges'), ('Late Registration', 'Late Registration')]
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name='application_fees', null=True, blank=True)
    category = models.CharField(max_length=100, choices=LEVEL_CATEGORY_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    name = models.CharField(max_length=60, choices=FEE_CHOICES)
    indigene_type = models.CharField(max_length=20, choices=INDIGENE_TYPE_CHOICES, default=INDIGENE)
    added_by = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    added_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['academic_session', 'category', 'name', 'indigene_type'], name='unique_application_fee_session_category_indigene')]

    def __str__(self):
        if self.academic_session:
            return f'{self.name} - {self.category} - {self.indigene_type} - {self.academic_session}: {self.amount}'
        return f'{self.name} - {self.category} - {self.indigene_type}: {self.amount}'


class Fee(models.Model):
    LEVEL_CHOICES = [('NDI', 'NDI'), ('NDII', 'NDII'), ('CERTIFICATE', 'CERTIFICATE'), ('HNDI', 'HNDI'), ('HNDII', 'HNDII')]
    SEMESTER_CHOICES = [('First Semester', 'First Semester'), ('Second Semester', 'Second Semester'), ('Full Session', 'Full Session')]
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name='fees', null=True, blank=True)
    course_of_study = models.ForeignKey(CourseOfStudy, on_delete=models.PROTECT, related_name='fees', null=True, blank=True)
    name = models.CharField(max_length=50)
    level = models.CharField(max_length=30, choices=LEVEL_CHOICES)
    semester = models.CharField(max_length=30, choices=SEMESTER_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    indigene_type = models.CharField(max_length=20, choices=INDIGENE_TYPE_CHOICES, default=INDIGENE)
    added_by = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    added_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['academic_session', 'course_of_study', 'name', 'level', 'semester', 'indigene_type'],
                name='unique_fee_session_course_level_sem_ind',
            )
        ]

    @staticmethod
    def normalize_fee_name(value):
        return ''.join(ch for ch in str(value or '').upper() if ch.isalnum())

    @classmethod
    def is_tuition_name(cls, value):
        return cls.normalize_fee_name(value) in {
            'SCHOOLFEES',
            'SCHOOLFEE',
            'TUITION',
            'TUITIONFEE',
            'TUITIONFEES',
        }

    @property
    def is_tuition_fee(self):
        return self.is_tuition_name(self.name)

    def clean(self):
        super().clean()
        if self.is_tuition_fee and not self.course_of_study_id:
            raise ValidationError({
                'course_of_study': 'Course of Study/Programme is required for tuition or school fee records.'
            })

        duplicate = Fee.objects.filter(
            academic_session=self.academic_session,
            name__iexact=self.name,
            level=self.level,
            semester=self.semester,
            indigene_type=self.indigene_type,
            course_of_study=self.course_of_study,
        )
        if self.pk:
            duplicate = duplicate.exclude(pk=self.pk)
        if duplicate.exists():
            raise ValidationError(
                'A fee already exists for this session, programme, name/category, level, payment option, and indigene type.'
            )

    def __str__(self):
        if self.academic_session:
            programme = f" - {self.course_of_study}" if self.course_of_study_id else ""
            return f"{self.name}{programme} - {self.level} - {self.semester} - {self.indigene_type} - {self.academic_session}: {self.amount}"
        return f"{self.name} - {self.level} - {self.semester} - {self.indigene_type}: {self.amount}"


class StudentFeePayment(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tuition_payment')
    course_of_study = models.ForeignKey(CourseOfStudy, on_delete=models.CASCADE)
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE)
    student_level = models.ForeignKey(Level, on_delete=models.CASCADE)
    payment_type = models.CharField(max_length=30, blank=True)
    is_indigene = models.BooleanField()
    has_paid_first_semester = models.BooleanField(default=False)
    has_paid_second_semester = models.BooleanField(default=False)
    has_paid_full_session = models.BooleanField(default=False)
    paid_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.student}'


class ScholarshipPin(models.Model):
    PLAN_CHOICES = (('standard', 'Standard'), ('premium', 'Premium'))
    PLAN_VALIDITY = {'standard': 365, 'premium': 730}
    code = models.CharField(max_length=12, unique=True)
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    @property
    def validity_days(self):
        return self.PLAN_VALIDITY.get(self.plan, 0)

    @property
    def expiry_date(self):
        if self.used_at:
            return self.used_at + timedelta(days=self.validity_days)
        return None

    @property
    def is_expired(self):
        if not self.used_at:
            return False
        return timezone.now() > self.expiry_date

    def __str__(self):
        return f"{self.code} - {self.plan}"


class RechargePin(models.Model):
    code = models.CharField(max_length=20, unique=True)
    plan = models.CharField(max_length=50, choices=[('standard', 'Standard'), ('premium', 'Premium')])
    duration_in_days = models.PositiveIntegerField()
    is_used = models.BooleanField(default=False)
    created_on = models.DateTimeField(auto_now_add=True)
    used_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    used_on = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.code

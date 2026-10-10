from django.db import models
from django.contrib.auth.models import User
from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from datetime import date, timedelta


def default_end_date():
    return date.today() + timedelta(days=90)


class Level(models.Model):
    LEVEL_CHOICES = [
        ('NDI', 'NDI'),
        ('NDII', 'NDII'),
        ('CERTIFICATE', 'CERTIFICATE'),
        ('HNDI', 'HNDI'),
        ('HNDII', 'HNDII'),
    ]
    name = models.CharField(max_length=100, choices=LEVEL_CHOICES)
    added_date = models.DateField(auto_now_add=True)
    added_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True)

    def __str__(self):
        return self.name


class AcademicSession(models.Model):
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=False)
    start_date = models.DateField(default=date.today)
    end_date = models.DateField(default=default_end_date)
    resumption_date = models.DateField(default=date.today)
    closing_date = models.DateField(default=default_end_date)
    admission_date = models.DateField(default=date.today)
    added_date = models.DateField(auto_now_add=True)
    added_by = models.ForeignKey(User, on_delete=models.CASCADE, null=True)

    class Meta:
        ordering = ('start_date', 'end_date', 'name')

    def __str__(self):
        return self.name

    def get_previous_academic_session(self):
        previous_sessions = AcademicSession.objects.filter(
            start_date__lt=self.start_date,
            is_active=False,
        ).exclude(id=self.id)
        return previous_sessions.order_by('-start_date', '-end_date', '-name').first()

    @classmethod
    def get_application_session(cls):
        """Return the academically latest session explicitly marked active."""
        return cls.objects.filter(is_active=True).order_by(
            '-start_date', '-end_date', '-name'
        ).first()


class Semester(models.Model):
    name = models.CharField(max_length=100)
    academic_session = models.ForeignKey(
        AcademicSession,
        on_delete=models.CASCADE,
        related_name='semesters',
        null=True,
        blank=True,
    )
    added_date = models.DateField(auto_now_add=True)
    added_by = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    is_active = models.BooleanField(default=False)
    date_resumption = models.DateField(blank=True, null=True)
    closing_date = models.DateField(blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['academic_session', 'name'],
                name='unique_semester_name_per_session',
            )
        ]

    def __str__(self):
        if self.academic_session:
            return f'{self.name} - {self.academic_session}'
        return self.name

    def get_previous_semester(self):
        previous_semesters = Semester.objects.filter(
            added_date__lt=self.added_date,
            is_active=False,
        ).exclude(id=self.id)
        if self.academic_session_id:
            previous_semesters = previous_semesters.filter(academic_session=self.academic_session)
        return previous_semesters.order_by('-added_date').first()

    @classmethod
    def get_active_for_session(cls, academic_session):
        if not academic_session:
            return None
        return cls.objects.filter(academic_session=academic_session, is_active=True).order_by('-added_date').first()


class Faculty(models.Model):
    name = models.CharField(max_length=100)
    added_date = models.DateField(auto_now_add=True)
    added_by = models.ForeignKey(User, on_delete=models.CASCADE, null=True)

    def __str__(self):
        return self.name


class Department(models.Model):
    DEPARTMENT_CATEGORY = [
        ('Academic', 'Academic'),
        ('Non-Academic', 'Non-Academic')
    ]
    name = models.CharField(max_length=100)
    dept_type = models.CharField(max_length=30, default='Academic', choices=DEPARTMENT_CATEGORY)
    faculty = models.ForeignKey(Faculty, on_delete=models.CASCADE, related_name='departments')
    added_date = models.DateField(auto_now_add=True)
    added_by = models.ForeignKey(User, on_delete=models.CASCADE, null=True)

    def __str__(self):
        return self.name


class Directorate(models.Model):
    name = models.CharField(max_length=100)
    added_date = models.DateField(auto_now_add=True)
    added_by = models.ForeignKey(User, on_delete=models.CASCADE, null=True)

    def __str__(self):
        return self.name


class Unit(models.Model):
    name = models.CharField(max_length=100)
    directorate = models.ForeignKey(Directorate, on_delete=models.CASCADE)
    added_date = models.DateField(auto_now_add=True)

    def __str__(self):
        return self.name


class CourseOfStudy(models.Model):
    name = models.CharField(max_length=160)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='courses_of_study')
    added_date = models.DateField(auto_now_add=True)
    added_by = models.ForeignKey(User, on_delete=models.CASCADE, null=True)

    def __str__(self):
        return self.name


class CourseUnit(models.Model):
    academic_session = models.ForeignKey(
        AcademicSession,
        on_delete=models.CASCADE,
        related_name='course_units',
        null=True,
        blank=True,
    )
    course_title = models.CharField(max_length=100)
    course_code = models.CharField(max_length=100)
    credit_load = models.FloatField()
    course_of_study = models.ForeignKey(CourseOfStudy, on_delete=models.CASCADE, related_name='course_units')
    is_core_course = models.BooleanField(default=False)
    is_elective_course = models.BooleanField(default=False)
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT)
    level = models.ForeignKey(Level, on_delete=models.CASCADE)
    added_date = models.DateField(auto_now_add=True)
    added_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['academic_session', 'semester', 'course_of_study', 'level', 'course_code', 'course_title'],
                name='unique_course_unit_per_session_semester_program_level',
            )
        ]

    def save(self, *args, **kwargs):
        self.credit_load = float(self.credit_load)
        if self.semester_id and not self.academic_session_id:
            self.academic_session = self.semester.academic_session
        super().save(*args, **kwargs)

    def __str__(self):
        return self.course_title


class Student(models.Model):
    LEVEL_PROGRESS = {
        'NDII': 'NDI',
        'HNDII': 'HNDI',
    }
    student_id = models.CharField(max_length=20, blank=True, null=True)
    candidate = models.OneToOneField(User, on_delete=models.CASCADE, related_name='student_profile')
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE)
    course = models.ForeignKey(CourseOfStudy, on_delete=models.CASCADE)
    semester = models.CharField(max_length=20, default='First Semester')
    current_level = models.CharField(max_length=30, blank=True)
    graduated = models.BooleanField(default=False)
    graduation_session = models.ForeignKey(
        AcademicSession,
        on_delete=models.PROTECT,
        related_name='graduates',
        null=True,
        blank=True,
    )
    graduated_at = models.DateTimeField(null=True, blank=True)
    graduated_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='graduated_students',
        null=True,
        blank=True,
    )
    admitted_by = models.ForeignKey(User, on_delete=models.CASCADE)
    added_date = models.DateField(auto_now_add=True)

    def is_remedial_science(self):
        return self.course.name == 'Remedial Programme'

    def deadmit(self):
        self.candidate.profile.role = 'Applicant'
        self.candidate.profile.save()

        ApplicationSubmission = apps.get_model('admission', 'ApplicationSubmission')
        application_submission = ApplicationSubmission.objects.get(
            applicant=self.candidate,
            academic_sess=self.academic_session,
            course_name=self.course,
        )
        application_submission.is_admitted = False
        application_submission.save()

        if Student.objects.filter(pk=self.pk).exists():
            self.delete()

    def get_previous_level(self):
        return self.LEVEL_PROGRESS.get(self.current_level)

    def __str__(self):
        return f"{self.candidate} - {self.current_level} - {self.academic_session}"


class AppendOnlyCorrectionHistoryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Student academic correction history is append-only.')

    def delete(self):
        raise ValidationError('Student academic correction history is append-only.')


class StudentAcademicCorrectionHistory(models.Model):
    student = models.ForeignKey(
        Student, on_delete=models.PROTECT, related_name='academic_correction_history'
    )
    previous_level = models.CharField(max_length=30, blank=True)
    new_level = models.CharField(max_length=30, blank=True)
    previous_admitted_session = models.ForeignKey(
        AcademicSession, on_delete=models.PROTECT, related_name='+',
    )
    new_admitted_session = models.ForeignKey(
        AcademicSession, on_delete=models.PROTECT, related_name='+',
    )
    changed_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='+')
    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    objects = AppendOnlyCorrectionHistoryQuerySet.as_manager()

    class Meta:
        ordering = ('-changed_at', '-pk')

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Student academic correction history is append-only.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Student academic correction history is append-only.')

    def __str__(self):
        return f'{self.student} corrected by {self.changed_by} at {self.changed_at}'


class CourseEnrollment(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='enrollments')
    course_unit = models.ForeignKey(CourseUnit, on_delete=models.CASCADE)
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE)
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE)
    programme = models.ForeignKey(CourseOfStudy, on_delete=models.CASCADE)
    level = models.ForeignKey(Level, on_delete=models.CASCADE)
    is_core_course = models.BooleanField(default=False)
    is_elective_course = models.BooleanField(default=False)
    added_date = models.DateField(auto_now_add=True)
    added_by = models.ForeignKey(User, on_delete=models.CASCADE, null=True)

    @classmethod
    def calculate_total_credit_load(cls, user, current_session, current_semester, program, level):
        enrollments = cls.objects.filter(
            student=user,
            academic_session=current_session,
            semester=current_semester,
            programme=program,
            level=level,
        ).select_related('course_unit')
        return sum(enrollment.course_unit.credit_load for enrollment in enrollments)

    def __str__(self):
        return f"{self.student} - {self.course_unit} - {self.semester}"


class CourseCarryover(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='carryovers')
    course_unit = models.ForeignKey(CourseUnit, on_delete=models.CASCADE)
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE)
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name='carryover_courses')
    level = models.CharField(max_length=16, blank=True)
    added_date = models.DateField(auto_now_add=True)

    @classmethod
    def calculate_carryover_credit_hours(cls, student, semester, academic_session, level):
        carryovers = cls.objects.filter(
            student=student,
            semester=semester,
            academic_session=academic_session,
            level=level,
        )
        return sum(carryover.course_unit.credit_load for carryover in carryovers)

    def __str__(self):
        return f"{self.student} - {self.course_unit} - Carryover from {self.semester}"


def validate_document_size(value):
    max_size = 1 * 1024 * 1024
    if value.size > max_size:
        raise ValidationError("File size must be no more than 1 MB.")


class Postutmlformat(models.Model):
    name = models.CharField(max_length=255, default='POST UTME CSV File Template')
    csv_file = models.FileField(
        upload_to='post_utme_sample',
        validators=[
            FileExtensionValidator(allowed_extensions=['csv']),
            validate_document_size,
        ], blank=True
    )
    uploaded_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.name}'


class StaffFile(models.Model):
    file_number = models.CharField(max_length=255, blank=False, null=True)
    is_activated = models.BooleanField(default=False)
    activated_date = models.DateField(auto_now_add=True)
    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='staff_file')

    def __str__(self):
        return f'{self.file_number}'

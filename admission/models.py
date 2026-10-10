from django.contrib.auth.models import User
from django.db import models
from payments.models import Payment
from phonenumber_field.modelfields import PhoneNumberField
from academics.models import CourseOfStudy, AcademicSession, Department, Directorate


class ApplicantValidation(models.Model):
    email = models.EmailField()
    is_validated = models.BooleanField(default=False)
    added_date = models.DateField(auto_now_add=True)
    applicant = models.OneToOneField(User, null=True, blank=True, on_delete=models.CASCADE, related_name="new_validation")

    def __str__(self):
        return f"{self.email}"


class PreApplicant(models.Model):
    applicant = models.OneToOneField(User, on_delete=models.CASCADE, null=True, related_name='pre_applicant')
    first_name = models.CharField(max_length=30, blank=True, null=True)
    middle_name = models.CharField(max_length=30, blank=True, null=True)
    last_name = models.CharField(max_length=30, blank=True, null=True)
    phone_number = models.CharField(max_length=11, blank=False, null=True)
    course_name = models.ForeignKey(CourseOfStudy, on_delete=models.CASCADE, blank=True)
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.PROTECT, related_name='pre_applicants', null=True, blank=True)
    application_number = models.CharField(max_length=20, blank=True, null=True)
    invoice_number = models.CharField(max_length=100, blank=True, null=True)
    added_date = models.DateField(auto_now_add=True)

    def __str__(self):
        if self.academic_session:
            return f"{self.applicant} - {self.academic_session}"
        return f"{self.applicant}"

    def has_application_submission(self):
        return ApplicationSubmission.objects.filter(applicant=self.applicant, academic_sess=self.academic_session).exists()

    @classmethod
    def has_paid_application_form(cls, user):
        pre_applicant = cls.objects.select_related('academic_session').filter(applicant=user).first()
        if not pre_applicant or not pre_applicant.academic_session:
            return False
        return Payment.objects.filter(
            user=user,
            academic_sess=pre_applicant.academic_session,
            payment_source='Application Form',
            verified=True,
            status='Approved',
        ).exists()


class EducationRecord(models.Model):
    CERTIFICATE_CHOICES = [
        ('First School Leaving Certificate (FSLC)', 'First School Leaving Certificate (FSLC)'),
        ('Senior School Certificate Examination (SSCE)', 'Senior School Certificate Examination (SSCE)'),
        ('National Diploma (ND)', 'National Diploma (ND)'),
        ('Ordinary National Diploma (OND)', 'Ordinary National Diploma (OND)'),
        ('Nigeria Certificate in Education (NCE)', 'Nigeria Certificate in Education (NCE)')
    ]
    applicant = models.ForeignKey(User, on_delete=models.CASCADE)
    school_name = models.CharField(max_length=255, blank=True, null=True)
    certificate = models.CharField(max_length=120, choices=CERTIFICATE_CHOICES, blank=True, null=True)
    admitted_year = models.CharField(max_length=4, blank=True, null=True)
    end_year = models.CharField(max_length=4, blank=True, null=True)
    added_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.applicant}'


class ApplicantResult(models.Model):
    RESULT_CHOICES = [
        ('WAEC', 'WAEC'),
        ('GCE', 'GCE'),
        ('NECO', 'NECO'),
        ('NABTEB', 'NABTEB'),
    ]
    GRAGE_CHOICES = [
        ('A1', 'A1'), ('B2', 'B2'), ('B3', 'B3'), ('C4', 'C4'), ('C5', 'C5'), ('C6', 'C6'), ('D7', 'D7'), ('E8', 'E8'), ('F9', 'F9'), ('A2', 'A2'), ('A3', 'A3'), ('P7', 'P7'), ('P8', 'P8'),
    ]
    SUBJECT_CHOICES = [
        ('GENERAL MATHEMATICS', 'GENERAL MATHEMATICS'), ('ENGLISH LANGUAGE', 'ENGLISH LANGUAGE'), ('ECONOMICS', 'ECONOMICS'), ('GOVERNMENT', 'GOVERNMENT'), ('GEOGRAPHY', 'GEOGRAPHY'), ('Introduction to Building Engineering Drawing', 'Introduction to Building Engineering Drawing'), ('Basic Electricity', 'Basic Electricity'), ('Computer Craft Studies', 'Computer Craft Studies'), ('CHEMISTRY', 'CHEMISTRY'), ('PHYSICS', 'PHYSICS'), ('CIVIL EDUCATION', 'CIVIL EDUCATION'), ('INFORMATION TECHNOLOGY', 'INFORMATION TECHNOLOGY'), ('COMPUTER STUDIES', 'COMPUTER STUDIES'), ('HOME ECONOMICS', 'HOME ECONOMICS'), ('FURTHER MATHEMATICS', 'FURTHER MATHEMATICS'), ('AGRICULTURAL SCIENCE', 'AGRICULTURAL SCIENCE'), ('ANIMAL HUSBANDRY', 'ANIMAL HUSBANDRY'), ('METAL WORK', 'METAL WORK'), ('WOOD WORK', 'WOOD WORK'), ('TOURISM', 'TOURISM'), ('BOOK KEEPING', 'BOOK KEEPING'), ('BIOLOGY', 'BIOLOGY'), ('CHRISTIAN RELIGIOUS STUDIES', 'CHRISTIAN RELIGIOUS STUDIES'), ('DATA PROCESSING', 'DATA PROCESSING'), ('ELECTRICAL INSTALLATION AND MAINTENANCE', 'ELECTRICAL INSTALLATION AND MAINTENANCE'), ('FINANCIAL ACCOUNTING', 'FINANCIAL ACCOUNTING'), ('FISHERIES', 'FISHERIES'), ('TECHNICAL DRAWING', 'TECHNICAL DRAWING'), ('MARKETING', 'MARKETING'), ('ISLAMIC RELIGIOUS STUDIES', 'ISLAMIC RELIGIOUS STUDIES'), ('LITERATURE IN ENGLISH', 'LITERATURE IN ENGLISH'), ('COMMERCE', 'COMMERCE'), ('HISTORY', 'HISTORY'), ('WELDING AND FABRICATION', 'WELDING AND FABRICATION'),
    ]
    applicant = models.ForeignKey(User, on_delete=models.CASCADE)
    subject_name = models.CharField(max_length=255, choices=SUBJECT_CHOICES)
    result_name = models.CharField(max_length=100, choices=RESULT_CHOICES)
    subject_grade = models.CharField(max_length=20, choices=GRAGE_CHOICES)
    exam_number = models.CharField(max_length=20, null=True, blank=True)
    exam_year = models.CharField(max_length=4, blank=True, null=True)
    added_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.applicant}'


class SponsorDetails(models.Model):
    RELATIONSHIP_CHOICES = [('Father', 'Father'), ('Mother', 'Mother'), ('Uncle', 'Uncle'), ('Aunty', 'Aunty'), ('Nephew', 'Nephew'), ('Cousin', 'Cousin'), ('Brother', 'Brother'), ('Sister', 'Sister'), ('Self', 'Self'), ('Husband', 'Husband'), ('Wife', 'Wife'), ('Friend', 'Friend'), ('Others', 'Others')]
    OCCUPATION_CHOICES = [('Farmer', 'Farmer'), ('Traditional Ruler', 'Traditional Ruler'), ('Clergy', 'Clergy'), ('Herbalist', 'Herbalist'), ('Business', 'Business'), ('Civil Servant', 'Civil Servant'), ('Others', 'Others')]
    applicant = models.OneToOneField(User, on_delete=models.CASCADE, related_name='candidate_sponsorship')
    full_name = models.CharField(max_length=70, null=True, blank=True)
    occupation = models.CharField(max_length=80, choices=OCCUPATION_CHOICES)
    phone_number = models.CharField(max_length=11, blank=True, null=True)
    email = models.EmailField(max_length=36, null=True, blank=True)
    home_address = models.CharField(max_length=120, null=True, blank=True)
    sponsor_relationship = models.CharField(max_length=100, choices=RELATIONSHIP_CHOICES)
    added_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.applicant}'


class ApplicationSubmission(models.Model):
    student_id = models.CharField(max_length=20, blank=True, null=True)
    applicant = models.OneToOneField(User, on_delete=models.CASCADE, related_name='submitted_application')
    confirm = models.BooleanField()
    academic_sess = models.ForeignKey(AcademicSession, on_delete=models.CASCADE)
    course_name = models.ForeignKey(CourseOfStudy, on_delete=models.CASCADE, blank=True)
    is_admitted = models.BooleanField(default=False)
    submitted_date = models.DateField(auto_now_add=True)

    def __str__(self):
        return f"Submission status for {self.applicant}"


class staffAppSubmission(models.Model):
    staff_user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='staff_registration')
    confirm = models.BooleanField()
    department = models.ForeignKey(Department, on_delete=models.CASCADE, blank=True, null=True)
    directorate = models.ForeignKey(Directorate, on_delete=models.CASCADE, blank=True, null=True)
    submitted_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Submission status for {self.staff_user}"


class Jamb(models.Model):
    applicant = models.OneToOneField(User, on_delete=models.CASCADE, related_name='jamb_candidate')
    exam_name = models.CharField(max_length=10, blank=True)
    exam_year = models.PositiveIntegerField(default=1)
    exam_number = models.CharField(max_length=100, blank=True)
    total_score = models.PositiveIntegerField(default=0)
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE)
    admitted_course = models.ForeignKey(CourseOfStudy, on_delete=models.CASCADE)
    admitted_level = models.CharField(max_length=16, blank=True)
    uploaded_date = models.DateField(auto_now_add=True)

    def __str__(self):
        return f'{self.applicant}'


class Admitted(models.Model):
    applicant = models.OneToOneField(User, on_delete=models.CASCADE)
    academic_session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE)
    admitted_course = models.ForeignKey(CourseOfStudy, on_delete=models.CASCADE)
    semester = models.CharField(max_length=20, default='First Semester')
    admitted_level = models.CharField(max_length=100, blank=True)
    admitted_date = models.DateField(auto_now_add=True)
    admitted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.applicant}'

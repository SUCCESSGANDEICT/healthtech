from django.contrib import admin
from .models import (EducationRecord, staffAppSubmission, ApplicantValidation, PreApplicant, ApplicantResult, SponsorDetails, ApplicationSubmission, Admitted, Jamb)


class PreApplicantAdmin(admin.ModelAdmin):
    list_display = ('applicant', 'course_name', 'academic_session', 'application_number', 'invoice_number', 'added_date')
    list_per_page = 10


class JambAdmin(admin.ModelAdmin):
    list_display = ('applicant', 'exam_name', 'exam_year', 'exam_number', 'total_score', 'academic_session', 'admitted_course', 'admitted_level', 'uploaded_date')
    list_per_page = 6


class ApplicationSubmissionAdmin(admin.ModelAdmin):
    list_display = ('student_id', 'applicant', 'confirm', 'academic_sess', 'course_name', 'submitted_date')
    list_per_page = 6


class SponsorDetailsAdmin(admin.ModelAdmin):
    list_display = ('applicant', 'full_name', 'occupation', 'email', 'phone_number', 'home_address', 'sponsor_relationship', 'added_date')
    list_per_page = 6


admin.site.register(PreApplicant, PreApplicantAdmin)
admin.site.register(EducationRecord)
admin.site.register(Jamb, JambAdmin)
admin.site.register(Admitted)
admin.site.register(ApplicationSubmission, ApplicationSubmissionAdmin)
admin.site.register(ApplicantValidation)
admin.site.register(ApplicantResult)
admin.site.register(SponsorDetails, SponsorDetailsAdmin)
admin.site.register(staffAppSubmission)

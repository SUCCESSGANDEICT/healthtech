from django.contrib import admin
from .models import Fee, StudentFeePayment, ApplicationFee, ScholarshipPin, RechargePin


class FeeAdmin(admin.ModelAdmin):
    list_display = ('name', 'academic_session', 'course_of_study', 'level', 'semester', 'indigene_type', 'amount')
    list_filter = ('academic_session', 'course_of_study', 'level', 'semester', 'indigene_type')
    search_fields = ('name', 'course_of_study__name')
    list_per_page = 10


class StudentFeePaymentAdmin(admin.ModelAdmin):
    list_display = ('student', 'course_of_study', 'academic_session', 'student_level', 'payment_type', 'is_indigene', 'has_paid_first_semester', 'has_paid_second_semester', 'has_paid_full_session')
    list_per_page = 10


class ApplicationFeeAdmin(admin.ModelAdmin):
    list_display = ('academic_session', 'category', 'name', 'indigene_type', 'amount')
    list_filter = ('academic_session', 'category', 'name', 'indigene_type')
    list_per_page = 10


class ScholarshipPinAdmin(admin.ModelAdmin):
    list_display = ('code', 'plan', 'is_used', 'created_at', 'used_at')
    list_per_page = 10


class RechargePinAdmin(admin.ModelAdmin):
    list_display = ('code', 'plan', 'duration_in_days', 'is_used', 'created_on', 'used_by', 'used_on')
    list_per_page = 10


admin.site.register(Fee, FeeAdmin)
admin.site.register(StudentFeePayment, StudentFeePaymentAdmin)
admin.site.register(ApplicationFee, ApplicationFeeAdmin)
admin.site.register(ScholarshipPin, ScholarshipPinAdmin)
admin.site.register(RechargePin, RechargePinAdmin)

from django.contrib import admin
from .models import (AcademicSession, StaffFile, Semester, Faculty, Postutmlformat, Department, CourseOfStudy, CourseUnit, Student, CourseEnrollment, Unit, CourseCarryover, Level, Directorate)


class AcademicSessionAdmin(admin.ModelAdmin):
    list_display = ('name', 'start_date', 'end_date', 'resumption_date', 'closing_date', 'admission_date', 'is_active', 'added_date', 'added_by')
    list_per_page = 6


class SemesterAdmin(admin.ModelAdmin):
    list_display = ('name', 'academic_session', 'date_resumption', 'closing_date', 'is_active', 'added_date', 'added_by')
    list_per_page = 10


class CourseEnrollmentAdmin(admin.ModelAdmin):
    list_display = ('student', 'course_unit', 'semester', 'academic_session', 'programme', 'level', 'is_core_course', 'is_elective_course')
    list_per_page = 6


admin.site.register(AcademicSession, AcademicSessionAdmin)
admin.site.register(Semester, SemesterAdmin)
admin.site.register(Unit)
admin.site.register(Faculty)
admin.site.register(Department)
admin.site.register(CourseOfStudy)


class CourseUnitAdmin(admin.ModelAdmin):
    list_display = ('academic_session', 'semester', 'course_of_study', 'level', 'course_code', 'course_title', 'credit_load')
    list_per_page = 10


admin.site.register(CourseUnit, CourseUnitAdmin)
admin.site.register(Student)
admin.site.register(Directorate)
admin.site.register(StaffFile)
admin.site.register(CourseEnrollment, CourseEnrollmentAdmin)
admin.site.register(CourseCarryover)
admin.site.register(Level)
admin.site.register(Postutmlformat)

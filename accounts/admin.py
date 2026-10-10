from django.contrib import admin
from .models import (Profile, StaffPublication,
                     UserActivityLog,
                     State, AcademicStaff,
                     LocalGovernment, StaffRegister)
# Register your models here.


class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'first_name', 'first_name', 'last_name', 'gender', 'date_birth', 'phone_number', 'state', 'lga', 'religion', 'role', 'image')
    list_per_page = 6


admin.site.register(Profile, ProfileAdmin)


admin.site.register(State)


class LocalGovernmentAdmin(admin.ModelAdmin):
    list_display = ('state', 'name')
    list_per_page = 6


admin.site.register(LocalGovernment, LocalGovernmentAdmin)


class UserActivityLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'action', 'timestamp')
    list_per_page = 6


admin.site.register(UserActivityLog, UserActivityLogAdmin)


admin.site.register(StaffRegister)


admin.site.register(StaffPublication)


admin.site.register(AcademicStaff)





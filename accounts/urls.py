from django.urls import path
from .views import CustomLoginView, PortalUserListView, PortalUserDeleteView
from . import views
from django.contrib.auth import views as auth_view

urlpatterns = [
    path('login/', CustomLoginView.as_view(), name='accounts_login'),
    path('Dashboard/', views.index, name='accounts_dashboard'),
    path('Student/Dashboard/', views.student_dashboard, name='student_dashboard'),
    path('staff/Dashboard/', views.staff_dashboard, name='staff_dashboard'),
    path('account/profile/', views.applicant_profile, name='accounts_profile'),
    path('student/account/profile/', views.student_profile, name='student_profile'),
    path('post_utme/candidate/profile/', views.post_utme_candidate_profile, name='post_utme_candidate_profile'),
    path('post_utme/candidate/edit/profile/', views.candidate_update_profile, name='candidate_edit_profile'),
    path('account/profile/update/', views.applicant_update_profile, name='accounts_profile_update'),
    path('staff/profile/update/', views.staff_update_profile, name='staff_update_profile'),
    path('super/admin/Dashboard/', views.user_admin_index, name='super_admin_home'),
    path('create/super/admin/user/', views.create_super_admin_user, name='create_super_admin_user'),
    path('list/portal/users/', views.staff_user_list, name='staff_user_list'),
    path('disable_user/<int:pk>/', views.disable_user, name='accounts_disable_user'),
    path('enable_user/<int:pk>/', views.enable_user, name='accounts_enable_user'),
    path('update_profile/<int:pk>/', views.super_user_update_staff_profile, name='super_user_update_staff_profile'),
    path('update_staff/register/<int:pk>/', views.super_user_update_staff_register, name='super_user_update_staff_register'),
    path('update_staff/academic_record/<int:pk>/', views.super_user_update_staff_academic_record, name='super_user_update_staff_academic_record'),
    path('password_reset/', auth_view.PasswordResetView.as_view(template_name='accounts/password_reset.html'), name = 'password_reset'),
    path('password_reset_done/', auth_view.PasswordResetDoneView.as_view(template_name='accounts/password_reset_done.html'), name ='password_reset_done'),
    path('password_reset_confirm/<uidb64>/<token>/', auth_view.PasswordResetConfirmView.as_view(template_name='accounts/password_reset_confirm.html'), name = 'password_reset_confirm'),
    path('reset_password_complete/', auth_view.PasswordResetCompleteView.as_view(template_name='accounts/password_reset_complete.html'), name = 'password_reset_complete'),
    path('change/password/', views.change_password, name='change_password'), 
    path('super_admin/reset_user/password/', views.password_reset_view, name='super_admin_password_reset'),
    path('super/admin/view_activity/', views.super_admin_view_user_log, name='user_activities'),
    path('list_all_users/', PortalUserListView.as_view(), name='portal_user_list'),
    path('delete/portal_user/<int:pk>/', PortalUserDeleteView.as_view(), name='delete_portal_user'),
    path('logout/', views.custom_logout, name='accounts_logout'),
    path('admin_user/view_student/profile/<int:student_id>/', views.admin_view_student_profile, name='admin_view_student_profile'),
    path('admin_useer/edit_username/', views.change_portal_username, name='change_portal_username'),
] 

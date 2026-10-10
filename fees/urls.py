from django.urls import path
from . import views
from .views import (
    ApplicationFeeCreateView,
    ApplicationFeeListView,
    ApplicationFeeDeleteView,
    ApplicationFeeUpdateView,
    SchoolFeeListView,
    FeeCreateView,
    SchoolFeeDeleteView,
    SchoolFeeUpdateView,
    )


urlpatterns = [
    path('add/app_fee/', ApplicationFeeCreateView.as_view(), name='add_app_fee'),
    path('add/app_fees/', ApplicationFeeListView.as_view(), name='app_fees'),
    path('delete/app_fee/<int:pk>/', ApplicationFeeDeleteView.as_view(), name='delete_app_fee'),
    path('update/app_fee/<int:pk>/', ApplicationFeeUpdateView.as_view(), name='update_app_fee'),
    path('delete/fee/<int:pk>/', ApplicationFeeDeleteView.as_view(), name='delete_fee'),
    path('update/fee/<int:pk>/', ApplicationFeeUpdateView.as_view(), name='update_fee'),
    path('add/school_fees/', FeeCreateView.as_view(), name='add_school_fees'),
    path('school_fees/list/', SchoolFeeListView.as_view(), name='school_fees_list'),
    path('delete/school/fee/<int:pk>/', SchoolFeeDeleteView.as_view(), name='delete_school_fee'),
    path('update/fee/school/<int:pk>/', SchoolFeeUpdateView.as_view(), name='update_school_fee'),
    path('generate/scholarship_pins/', views.generate_scholarship_pin_view, name='generate_scholarship_pins'),
    path('view_generated/scholarship_pins/', views.generated_scholarship_pins, name='generated_scholarship_pins'),
]

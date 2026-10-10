from django.urls import path
from . import views


urlpatterns = [
    path('search/applicant/', views.search_pay_application_form, name='search_applicant'),
    path('pay/application/form/<int:applicant_id>/', views.pay_application_form, name='pay_application_fee'),
    path('payment/report/', views.payment_report, name='payment_report'),
    path('search/pay/screening/fee/', views.search_pay_screening_fee, name='search_pay_screening_fee'),
    path('pay/screening/fee/<int:applicant_id>/', views.pay_screening_fee, name='pay_screening_fee'),
    path('search/pay/acceptance/fee/', views.search_pay_acceptance_fee, name='search_student_pay_acceptance'),
    path('pay/acceptance/fee/<int:student_id>/', views.pay_acceptance_fee, name='pay_acceptance_fee'),
    path('search/pay/school/fee/', views.search_pay_school_fee, name='search_student_fee'),
    path('pay/current_session/school/fee/<int:student_id>/', views.student_fee_information, name='student_school_fee'),
    path('pay/previous_session/fee/<int:student_id>/', views.student_previous_fee_information, name='student_previous_session_fee'),
    path('candidate/acceptance/fee/receipt/', views.candidate_acceptance_receipt, name='candidate_acceptance_receipt'),
    path('candidate/caution/fee/receipt/', views.candidate_caution_fee_receipt, name='candidate_caution_fee_receipt'),
    path('candidate/other_charges/fee/receipt/', views.candidate_other_charges_fee_receipt, name='candidate_other_charges_fee_receipt'),
    path('candidate/application/fee/receipt/', views.candidate_application_receipt, name='candidate_application_receipt'),
    path('candidate/screening/fee/receipt/', views.candidate_screening_receipt, name='candidate_screening_receipt'),
    path('student/school_fee/receipts/', views.student_school_fee_receipts, name='student_school_fees_receipts'),
    path('print/sch_fee_receipt/<int:payment_id>/<int:program_id>/', views.print_sch_fee_receipt, name='print_sch_fee_receipt'),
    path('initiate/paystact_payment/', views.initiate_payment, name='initiate_payment'),
    path('select_fee/payment_option/', views.select_fee_payment, name='select_fee_payment'),
    path('initiate/tuition_fee_payment/<int:semester>/', views.initiate_school_fees_payment, name='initiate_school_fees_payment'),
    path('paystack/webhook/', views.paystack_webhook, name='paystack_webhook'),
    path('<str:ref>/', views.verify_payment, name='verify_payment'),
    path('student/late_registration/fee/receipt/', views.late_registration_receipt, name='late_registration_receipt'), 
]

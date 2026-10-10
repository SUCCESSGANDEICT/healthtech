from django.contrib import admin
from .models import FeePayment, Payment


# Register your models here.
admin.site.register(FeePayment)


class PaymentAdmin(admin.ModelAdmin):
    list_display = ('user', 'payment_source', 'amount', 'payment_method', 'status', 'verified', 'pay_ref', 'transaction_id', 'academic_sess', 'payment_date', 'added_by')
    list_per_page = 50


admin.site.register(Payment, PaymentAdmin)

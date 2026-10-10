from django.db import models
from fees.models import Fee, ApplicationFee, INDIGENE, INDIGENE_TYPE_CHOICES
from django.contrib.auth.models import User
from academics.models import Student, AcademicSession, Semester
from django.utils import timezone
from django.db.models import Sum
from decimal import Decimal
from .paystack import PayStack


# Create your models here.
class FeePayment(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    fee = models.ForeignKey(Fee, on_delete=models.CASCADE)
    payment_status = models.CharField(max_length=20, blank=True)
    payment_date = models.DateField(auto_now_add=True)

    def __str__(self):
        return f"{self.student} - {self.fee}"


# Payment Model
class Payment(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='fee_payment')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=30, default='Online Payment')
    status = models.CharField(max_length=10, default='No Payment')
    transaction_id = models.CharField(max_length=255, db_index=True, blank=True, null=True)
    payment_source = models.CharField(max_length=20)
    pay_ref = models.CharField(max_length=255, db_index=True, blank=True, null=True)
    verified = models.BooleanField(default=False)
    email = models.EmailField()
    fee_category = models.CharField(max_length=20, default='ND')
    indigene_type = models.CharField(max_length=20, choices=INDIGENE_TYPE_CHOICES, default=INDIGENE)
    academic_sess = models.ForeignKey(AcademicSession, on_delete=models.CASCADE)
    payment_date = models.DateField(auto_now_add=True)
    added_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payments_added_by')

   
    class Meta:
        indexes = [
            models.Index(
                fields=['user', 'academic_sess', 'payment_source', 'indigene_type'],
                name='pay_usr_sess_ind_idx'
            ),
        ]

    def amount_value(self) -> int:
        return int(self.amount * 100)  # Correctly returning the integer value

    def verify_payment(self):
        paystack = PayStack()
        status, result = paystack.verify_payment(self.pay_ref)
        if status:
            if result['amount'] == self.amount_value():
                self.verified = True
                self.status = 'Approved'
                self.save()
        return self.verified
    
    @classmethod
    def total_amount_paid_for_application_form(cls, academic_sess):
        total_amount = cls.objects.filter(
            academic_sess=academic_sess,
            payment_source='Application Form',
        ).aggregate(Sum('amount'))['amount__sum']

        return total_amount if total_amount is not None else Decimal('0.00')
    
    @classmethod
    def total_amount_paid_school_fees(cls, academic_sess):
        total_amount = cls.objects.filter(
            academic_sess=academic_sess,
            payment_source='School Fees',
        ).aggregate(Sum('amount'))['amount__sum']

        return total_amount if total_amount is not None else Decimal('0.00')
    
    @classmethod
    def count_users_with_school_fees_payment(cls, academic_sess):
        count_users = cls.objects.filter(
            academic_sess=academic_sess,
            payment_source='School Fees',
        ).values('user').distinct().count()

        return count_users

    def save(self, *args, **kwargs):
        # Set payment_date to current time only when creating a new instance
        if not self.id:
            self.payment_date = timezone.now()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} - {self.amount} - {self.payment_source} - {self.academic_sess}"





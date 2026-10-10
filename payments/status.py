import re

from django.db.models import Q

from .models import Payment


SUCCESS_STATUSES = {'APPROVED', 'SUCCESSFUL', 'SUCCESS', 'PAID', 'VERIFIED'}
FAILED_STATUSES = {'FAILED', 'CANCELLED', 'CANCELED', 'DECLINED', 'ABANDONED', 'EXPIRED'}
AWAITING_VERIFICATION_STATUSES = {
    'AWAITING VERIFICATION',
    'PROCESSING',
    'SUBMITTED',
    'COMPLETED',
}

FEE_ALIASES = {
    'APPLICATIONFORM': 'Application Form',
    'APPLICATIONFEE': 'Application Form',
    'APPLICATIONFORMFEE': 'Application Form',
    'FORMFEE': 'Application Form',
    'SCREENING': 'Screening Fee',
    'SCREENINGFEE': 'Screening Fee',
    'ACCEPTANCE': 'Acceptance Fee',
    'ACCEPTANCEFEE': 'Acceptance Fee',
    'CAUTION': 'Caution Fee',
    'CAUTIONFEE': 'Caution Fee',
    'OTHERCHARGE': 'Other Charges',
    'OTHERCHARGES': 'Other Charges',
    'OTHERCHARGESFEE': 'Other Charges',
    'SCHOOLFEE': 'School Fees',
    'SCHOOLFEES': 'School Fees',
    'TUITION': 'School Fees',
    'TUITIONFEE': 'School Fees',
    'TUITIONFEES': 'School Fees',
    'LATEREGISTRATION': 'Late Registration',
    'LATEREGISTRATIONFEE': 'Late Registration',
    'LATEREGISTRATON': 'Late Registration',
}

PAYMENT_SOURCE_VARIANTS = {
    'Application Form': ['Application Form', 'Application form', 'Application Form Fee', 'Application Fee', 'Form Fee'],
    'Screening Fee': ['Screening Fee', 'Screening fee', 'Screening'],
    'Acceptance Fee': ['Acceptance Fee', 'Acceptance fee', 'Acceptance'],
    'Caution Fee': ['Caution Fee', 'Caution fee', 'Caution'],
    'Other Charges': ['Other Charges', 'Other charges', 'Other Charges Fee', 'Other Charge'],
    'School Fees': ['School Fees', 'School fees', 'School Fee', 'School fee', 'Tuition Fee', 'Tuition Fees'],
    'Late Registration': ['Late Registration', 'Late registration', 'Late Registration Fee', 'Late Registraton'],
}


def normalize_payment_source(value):
    normalized = re.sub(r'[^A-Za-z0-9]+', '', str(value or '')).upper()
    return FEE_ALIASES.get(normalized, str(value or '').strip())


def successful_payment_filter():
    return (
        Q(verified=True)
        | Q(status__iexact='Approved')
        | Q(status__iexact='Successful')
        | Q(status__iexact='Success')
        | Q(status__iexact='Paid')
        | Q(status__iexact='Verified')
    )


def payment_source_filter(payment_source):
    canonical_source = normalize_payment_source(payment_source)
    source_q = Q(payment_source__iexact=canonical_source)
    for variant in PAYMENT_SOURCE_VARIANTS.get(canonical_source, []):
        source_q |= Q(payment_source__iexact=variant)
    return source_q


def related_payment_user_ids(student, user):
    user_ids = {user.pk}
    if student and student.candidate_id:
        user_ids.add(student.candidate_id)
    return user_ids


def payments_for_fee(student, user, academic_session, payment_source, indigene_type=None, successful_only=False):
    if not academic_session:
        return Payment.objects.none()

    payments = Payment.objects.filter(
        user_id__in=related_payment_user_ids(student, user),
        academic_sess=academic_session,
    ).filter(payment_source_filter(payment_source))

    if indigene_type:
        exact_indigene = payments.filter(indigene_type=indigene_type)
        if exact_indigene.exists():
            payments = exact_indigene

    if successful_only:
        payments = payments.filter(successful_payment_filter())

    return payments


def payment_state(student, user, academic_session, payment_source, indigene_type=None):
    payments = payments_for_fee(
        student,
        user,
        academic_session,
        payment_source,
        indigene_type=indigene_type,
    )
    successful_payments = payments.filter(successful_payment_filter())
    pending_payments = payments.exclude(successful_payment_filter())
    return {
        'payments': payments,
        'successful_payments': successful_payments,
        'pending_payments': pending_payments,
        'has_successful_payment': successful_payments.exists(),
        'has_pending_payment': pending_payments.exists(),
        'latest_payment': payments.order_by('-payment_date', '-id').first(),
    }


def is_successful_payment(payment):
    return bool(getattr(payment, 'verified', False)) or str(getattr(payment, 'status', '')).strip().upper() in SUCCESS_STATUSES


def payment_attempt_state(payment):
    if not payment:
        return 'not_started'
    if is_successful_payment(payment):
        return 'paid'

    status = str(getattr(payment, 'status', '') or '').strip().upper()
    payment_method = str(getattr(payment, 'payment_method', '') or '').strip().upper()
    has_reference = bool(getattr(payment, 'pay_ref', None))
    has_processor_transaction = bool(getattr(payment, 'transaction_id', None))

    if status in FAILED_STATUSES:
        return 'failed'
    if status in AWAITING_VERIFICATION_STATUSES:
        return 'awaiting_verification'
    if status in {'PENDING', 'NO PAYMENT', ''}:
        if payment_method == 'PAYSTACK ONLINE' and has_reference:
            return 'initiated'
        if has_processor_transaction and payment_method != 'PAYSTACK ONLINE':
            return 'awaiting_verification'
        return 'not_started'
    if has_reference and payment_method == 'PAYSTACK ONLINE':
        return 'initiated'
    return 'awaiting_verification'


def payment_attempt_message(display_name, attempt_state):
    if attempt_state == 'initiated':
        return {
            'status': 'Payment Not Completed',
            'status_class': 'warning',
            'message': f'{display_name} payment was initiated but checkout was not completed.',
            'action_label': 'Complete Payment',
            'can_pay': True,
        }
    if attempt_state == 'failed':
        return {
            'status': 'Failed',
            'status_class': 'danger',
            'message': f'{display_name} payment was not successful. You can try again safely.',
            'action_label': 'Retry Payment',
            'can_pay': True,
        }
    if attempt_state == 'awaiting_verification':
        return {
            'status': 'Awaiting Verification',
            'status_class': 'warning',
            'message': f'{display_name} payment has been submitted and is awaiting confirmation.',
            'action_label': '',
            'can_pay': False,
        }
    return {
        'status': 'Not paid',
        'status_class': 'danger',
        'message': f'{display_name} is configured but has not been paid.',
        'action_label': 'Pay Now',
        'can_pay': True,
    }

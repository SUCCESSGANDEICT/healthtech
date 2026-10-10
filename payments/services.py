import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from django.db import transaction

from academics.models import Level
from accounts.models import UserActivityLog
from fees.models import StudentFeePayment
from fees.utils import (
    FeeConfigurationError,
    get_application_fee,
    get_tuition_fee_for_option,
    get_user_indigene_type,
    normalize_indigene_type,
)

from .models import Payment
from .paystack import PayStack

logger = logging.getLogger(__name__)


@dataclass
class PaymentFinalizationResult:
    success: bool
    payment: Optional[Payment] = None
    already_verified: bool = False
    message: str = ""


def generate_payment_reference() -> str:
    reference = uuid.uuid4().hex
    while Payment.objects.filter(pay_ref=reference).exists():
        reference = uuid.uuid4().hex
    return reference


def get_or_create_pending_payment(
    *,
    form,
    user,
    academic_session,
    email,
    fee_category,
    payment_source,
    added_by,
    indigene_type=None,
):
    amount = form.cleaned_data.get("amount") or form.fields["amount"].initial
    indigene_type = normalize_indigene_type(indigene_type or get_user_indigene_type(user))
    with transaction.atomic():
        user.__class__.objects.select_for_update().get(pk=user.pk)
        pending_payment = (
            Payment.objects.filter(
                user=user,
                academic_sess=academic_session,
                payment_source=payment_source,
                fee_category=fee_category,
                indigene_type=indigene_type,
                amount=amount,
                verified=False,
                status="Pending",
                payment_method="Paystack Online",
            )
            .exclude(pay_ref__isnull=True)
            .exclude(pay_ref="")
            .order_by("-id")
            .first()
        )
        if pending_payment:
            logger.info(
                "Reusing pending Paystack payment for user=%s source=%s ref=%s",
                user.pk,
                payment_source,
                pending_payment.pay_ref,
            )
            return pending_payment, False

        payment = form.save(commit=False)
        payment.user = user
        payment.status = "Pending"
        payment.academic_sess = academic_session
        payment.email = email
        payment.fee_category = fee_category
        payment.indigene_type = indigene_type
        payment.pay_ref = generate_payment_reference()
        payment.payment_source = payment_source
        payment.added_by = added_by
        payment.payment_method = "Paystack Online"
        payment.save()
        return payment, True


def verify_and_finalize_payment(reference, source="callback"):
    if not reference:
        logger.warning("Paystack %s verification failed: missing reference", source)
        return PaymentFinalizationResult(False, message="Missing payment reference")

    paystack = PayStack()
    status, result = paystack.verify_payment(reference)
    if not status:
        logger.warning(
            "Paystack %s verification failed for ref=%s result=%s",
            source,
            reference,
            result,
        )
        return PaymentFinalizationResult(False, message="Payment verification failed")

    if result.get("status") != "success":
        logger.warning(
            "Paystack %s returned non-success status for ref=%s status=%s",
            source,
            reference,
            result.get("status"),
        )
        return PaymentFinalizationResult(False, message="Payment was not successful")

    with transaction.atomic():
        try:
            payment = Payment.objects.select_for_update().select_related("user", "academic_sess").get(pay_ref=reference)
        except Payment.DoesNotExist:
            logger.warning("Paystack %s verification matched no payment ref=%s", source, reference)
            return PaymentFinalizationResult(False, message="Payment record not found")
        except Payment.MultipleObjectsReturned:
            logger.error("Paystack %s verification matched multiple payments ref=%s", source, reference)
            return PaymentFinalizationResult(False, message="Duplicate payment reference")

        if payment.verified and payment.status == "Approved":
            logger.info("Ignoring duplicate Paystack %s for already verified ref=%s", source, reference)
            return PaymentFinalizationResult(True, payment=payment, already_verified=True, message="Payment already verified")

        if result.get("amount") != payment.amount_value():
            logger.warning(
                "Paystack %s amount mismatch for ref=%s expected=%s actual=%s",
                source,
                reference,
                payment.amount_value(),
                result.get("amount"),
            )
            return PaymentFinalizationResult(False, payment=payment, message="Payment amount mismatch")

        if not _payment_matches_configured_fee(payment):
            return PaymentFinalizationResult(False, payment=payment, message="Payment fee configuration mismatch")

        payment.verified = True
        payment.status = "Approved"
        payment.save(update_fields=["verified", "status"])

        if payment.payment_source == "School Fees":
            _finalize_school_fees_payment(payment)

        logger.info("Finalized Paystack %s payment ref=%s source=%s", source, reference, payment.payment_source)
        return PaymentFinalizationResult(True, payment=payment, message="Payment verified")


def _finalize_school_fees_payment(payment):
    user = payment.user
    try:
        student_profile = user.student_profile
        student_level = Level.objects.get(name=student_profile.current_level)
    except Level.DoesNotExist:
        logger.warning("Cannot finalize school fees ref=%s: student level not found", payment.pay_ref)
        return
    except Exception:
        logger.exception("Cannot finalize school fees ref=%s: student profile error", payment.pay_ref)
        return

    student_fee = (
        StudentFeePayment.objects.select_for_update()
        .filter(
            student=user,
            academic_session=payment.academic_sess,
            student_level=student_level,
            course_of_study=student_profile.course,
        )
        .order_by("id")
        .first()
    )
    if not student_fee:
        student_fee = StudentFeePayment.objects.create(
            student=user,
            academic_session=payment.academic_sess,
            student_level=student_level,
            course_of_study=student_profile.course,
            is_indigene=normalize_indigene_type(payment.indigene_type) == "INDIGENE",
            payment_type=payment.payment_source,
            has_paid_first_semester=False,
            has_paid_second_semester=False,
            has_paid_full_session=False,
        )

    tuition_type = _school_fees_payment_type(payment, student_profile.current_level)
    if tuition_type == "Full Session":
        if student_fee.has_paid_first_semester or student_fee.has_paid_second_semester or student_fee.has_paid_full_session:
            logger.info("Duplicate full-session school fees finalization skipped for ref=%s user=%s", payment.pay_ref, user.pk)
            return
        student_fee.has_paid_first_semester = True
        student_fee.has_paid_second_semester = True
        student_fee.has_paid_full_session = True
    elif tuition_type == "First Semester":
        if student_fee.has_paid_first_semester or student_fee.has_paid_full_session:
            logger.info("Duplicate first-semester school fees finalization skipped for ref=%s user=%s", payment.pay_ref, user.pk)
            return
        student_fee.has_paid_first_semester = True
    elif tuition_type == "Second Semester":
        has_verified_first_semester_payment = Payment.objects.filter(
            user=user,
            academic_sess=payment.academic_sess,
            payment_source="School Fees",
            fee_category="First Semester",
            indigene_type=payment.indigene_type,
            verified=True,
            status="Approved",
        ).exclude(pk=payment.pk).exists()
        if has_verified_first_semester_payment:
            student_fee.has_paid_first_semester = True
        if not student_fee.has_paid_first_semester:
            logger.warning("Rejected second-semester finalization before first-semester payment ref=%s user=%s", payment.pay_ref, user.pk)
            return
        if student_fee.has_paid_second_semester or student_fee.has_paid_full_session:
            logger.info("Duplicate second-semester school fees finalization skipped for ref=%s user=%s", payment.pay_ref, user.pk)
            return
        student_fee.has_paid_second_semester = True
        student_fee.has_paid_full_session = True
    else:
        logger.warning("Cannot finalize school fees ref=%s: fee amount does not match a configured tuition option", payment.pay_ref)
        return

    student_fee.payment_type = payment.payment_source
    student_fee.is_indigene = normalize_indigene_type(payment.indigene_type) == "INDIGENE"
    student_fee.save(
        update_fields=[
            "payment_type",
            "is_indigene",
            "has_paid_first_semester",
            "has_paid_second_semester",
            "has_paid_full_session",
        ]
    )

    UserActivityLog.objects.create(
        user=user,
        action=f"{payment.amount} School Fees Payment Verified, Ref: {payment.pay_ref}.",
    )


def _school_fees_payment_type(payment, level_name):
    if payment.fee_category in {"First Semester", "Second Semester", "Full Session"}:
        return payment.fee_category

    for semester_name in ("Full Session", "First Semester", "Second Semester"):
        try:
            fee = get_tuition_fee_for_option(
                level_name,
                semester_name,
                payment.academic_sess,
                payment.indigene_type,
                payment.user.student_profile.course,
            )
        except FeeConfigurationError:
            continue
        if fee.amount == payment.amount:
            return semester_name
    return ""


def _payment_matches_configured_fee(payment):
    try:
        if payment.payment_source in {
            "Application Form",
            "Screening Fee",
            "Acceptance Fee",
            "Caution Fee",
            "Other Charges",
        }:
            fee = get_application_fee(
                payment.fee_category,
                payment.payment_source,
                payment.academic_sess,
                payment.indigene_type,
            )
            return fee.amount == payment.amount

        if payment.payment_source == "Late Registration":
            fee = get_application_fee(
                payment.fee_category,
                "Late Registration",
                payment.academic_sess,
                payment.indigene_type,
            )
            return fee.amount == payment.amount

        if payment.payment_source == "School Fees":
            student_profile = payment.user.student_profile
            if payment.fee_category in {"First Semester", "Second Semester", "Full Session"}:
                try:
                    fee = get_tuition_fee_for_option(
                        student_profile.current_level,
                        payment.fee_category,
                        payment.academic_sess,
                        payment.indigene_type,
                        student_profile.course,
                    )
                    return fee.amount == payment.amount
                except FeeConfigurationError:
                    return False

            for semester in ("Full Session", "First Semester", "Second Semester"):
                try:
                    fee = get_tuition_fee_for_option(
                        student_profile.current_level,
                        semester,
                        payment.academic_sess,
                        payment.indigene_type,
                        student_profile.course,
                    )
                    if fee.amount == payment.amount:
                        return True
                except FeeConfigurationError:
                    continue
            return False
    except Exception:
        logger.exception("Payment fee validation failed for ref=%s", payment.pay_ref)
        return False

    return True

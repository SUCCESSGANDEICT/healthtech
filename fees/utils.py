from decimal import Decimal
import re

from .models import ApplicationFee, Fee, INDIGENE, NON_INDIGENE


class FeeConfigurationError(Exception):
    pass


TUITION_FEE_NAME_ALIASES = {
    'SCHOOLFEES',
    'SCHOOLFEE',
    'TUITION',
    'TUITIONFEE',
    'TUITIONFEES',
}
NON_TUITION_FEE_NAME_ALIASES = {
    'LATEREGISTRATION',
    'LATEREGISTRATIONFEE',
    'LATEREGISTRATON',
}


def _normalize_fee_name(value):
    return re.sub(r'[^A-Za-z0-9]+', '', str(value or '')).upper()


def _active_fee_filter(model):
    if any(field.name == 'is_active' for field in model._meta.fields):
        return {'is_active': True}
    if any(field.name == 'active' for field in model._meta.fields):
        return {'active': True}
    if any(field.name == 'enabled' for field in model._meta.fields):
        return {'enabled': True}
    return {}


def normalize_indigene_type(value=None):
    if value is None or value == "":
        return INDIGENE
    if isinstance(value, bool):
        return INDIGENE if value else NON_INDIGENE

    normalized = str(value).strip().upper().replace("_", "-")
    if normalized in {"INDIGENE", "BENUE", "TRUE", "1", "YES"}:
        return INDIGENE
    if normalized in {"NON-INDIGENE", "NON INDIGENE", "NONINDIGENE", "FALSE", "0", "NO"}:
        return NON_INDIGENE
    return INDIGENE


def get_user_indigene_type(user):
    profile = getattr(user, "profile", None)
    if profile and hasattr(profile, "indigene_status"):
        return normalize_indigene_type(profile.indigene_status())
    return INDIGENE


def get_application_fee(category, name, academic_session, indigene_type=INDIGENE):
    indigene_type = normalize_indigene_type(indigene_type)
    qs = ApplicationFee.objects.filter(
        academic_session=academic_session,
        category__iexact=category,
        name__iexact=name,
        indigene_type=indigene_type,
    )
    count = qs.count()
    if count == 0:
        raise FeeConfigurationError(
            f"No {name} fee is configured for {category}, {academic_session}, {indigene_type}."
        )
    if count > 1:
        raise FeeConfigurationError(
            f"Multiple {name} fees are configured for {category}, {academic_session}, {indigene_type}."
        )
    return qs.get()


def get_student_course_of_study(user):
    student_profile = getattr(user, "student_profile", None)
    if student_profile:
        return getattr(student_profile, "course", None)
    return None


def get_school_fee(level, semester, academic_session, indigene_type=INDIGENE, name=None, course_of_study=None):
    indigene_type = normalize_indigene_type(indigene_type)
    qs = Fee.objects.filter(
        academic_session=academic_session,
        level=level,
        semester=semester,
        indigene_type=indigene_type,
    )
    if name:
        qs = qs.filter(name__iexact=name)

    if course_of_study and (not name or Fee.is_tuition_name(name)):
        programme_qs = qs.filter(course_of_study=course_of_study)
        count = programme_qs.count()
        label = name or "School Fees"
        if count == 1:
            return programme_qs.get()
        if count > 1:
            raise FeeConfigurationError(
                f"Multiple {label} fees are configured for {level}, {semester}, {academic_session}, {course_of_study}, {indigene_type}."
            )
        if not name:
            raise FeeConfigurationError(
                f"No programme-specific School Fees fee is configured for {level}, {semester}, {academic_session}, {course_of_study}, {indigene_type}."
            )

    qs = qs.filter(course_of_study__isnull=True)
    count = qs.count()
    label = name or "School Fees"
    if count == 0:
        raise FeeConfigurationError(
            f"No {label} fee is configured for {level}, {semester}, {academic_session}, {indigene_type}."
        )
    if count > 1:
        raise FeeConfigurationError(
            f"Multiple {label} fees are configured for {level}, {semester}, {academic_session}, {indigene_type}."
        )
    return qs.get()


def get_school_fee_amount(level, semester, academic_session, indigene_type=INDIGENE, name=None, course_of_study=None):
    try:
        return get_school_fee(level, semester, academic_session, indigene_type, name=name, course_of_study=course_of_study).amount
    except FeeConfigurationError:
        return Decimal("0")


def get_tuition_fee_for_option(level, semester, academic_session, indigene_type=INDIGENE, course_of_study=None):
    if not level or not semester or not academic_session:
        raise FeeConfigurationError('Tuition fee setup cannot be checked because level, semester, or session is missing.')
    if not course_of_study:
        raise FeeConfigurationError('Course of Study/Programme is required to select tuition fees.')

    fees = list(
        Fee.objects.filter(
            academic_session=academic_session,
            course_of_study=course_of_study,
            level=level,
            semester=semester,
            indigene_type=normalize_indigene_type(indigene_type),
            amount__isnull=False,
            amount__gt=0,
            **_active_fee_filter(Fee),
        ).order_by('name', '-added_date', '-id')
    )
    if not fees:
        raise FeeConfigurationError(
            f'{semester} Tuition Fee setup was not found for {level}, {academic_session}, {course_of_study}, {normalize_indigene_type(indigene_type)}.'
        )

    tuition_fees = [
        fee for fee in fees
        if _normalize_fee_name(fee.name) in TUITION_FEE_NAME_ALIASES
    ]
    if len(tuition_fees) == 1:
        return tuition_fees[0]
    if len(tuition_fees) > 1:
        raise FeeConfigurationError(
            f'Multiple {semester} Tuition Fee records are configured for {level}, {academic_session}, {course_of_study}, {normalize_indigene_type(indigene_type)}.'
        )

    non_late_fees = [
        fee for fee in fees
        if _normalize_fee_name(fee.name) not in NON_TUITION_FEE_NAME_ALIASES
    ]
    if len(non_late_fees) == 1:
        return non_late_fees[0]
    if non_late_fees:
        configured_names = ', '.join(sorted({fee.name for fee in non_late_fees}))
        raise FeeConfigurationError(
            f'Multiple possible tuition fee records are configured for {semester}: {configured_names}. Use School Fees or Tuition as the fee name.'
        )

    raise FeeConfigurationError(
        f'{semester} Tuition Fee setup was not found. Existing {semester} fee record(s) are not tuition fees.'
    )

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from academics.models import (
    AcademicSession,
    CourseEnrollment,
    CourseOfStudy,
    CourseUnit,
    Department,
    Faculty,
    Level,
    Semester,
    Student,
)
from academics.services import correct_student_level_and_admitted_session
from accounts.student_academic import get_student_application_session
from fees.models import Fee, ApplicationFee, StudentFeePayment
from admission.models import Admitted, ApplicantValidation, ApplicationSubmission, PreApplicant
from payments.models import Payment
from payments.services import _payment_matches_configured_fee, verify_and_finalize_payment
from payments.views import get_late_registration_fee_gate, get_student_prerequisite_fee_gate
from accounts.views import _dashboard_late_registration_fee, _next_fee_context


class StudentDashboardSemesterResolutionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='test-pass')
        self.faculty = Faculty.objects.create(name='Health Sciences', added_by=self.admin)
        self.department = Department.objects.create(
            name='Community Health',
            faculty=self.faculty,
            added_by=self.admin,
        )
        self.course = CourseOfStudy.objects.create(
            name='Community Health Extension',
            department=self.department,
            added_by=self.admin,
        )
        self.admission_session = AcademicSession.objects.create(
            name='2024/2025',
            is_active=True,
            start_date=date(2024, 9, 1),
            end_date=date(2025, 8, 31),
            added_by=self.admin,
        )
        self.progressive_session = AcademicSession.objects.create(
            name='2025/2026',
            is_active=True,
            start_date=date(2025, 9, 1),
            end_date=date(2026, 8, 31),
            added_by=self.admin,
        )
        for level_name in ('NDI', 'NDII', 'HNDI', 'HNDII'):
            Level.objects.create(name=level_name, added_by=self.admin)

    def create_student_user(self, level_name, semester_name='First Semester'):
        user = User.objects.create_user(
            username=f'{level_name.lower()}-student',
            email=f'{level_name.lower()}@example.com',
            password='test-pass',
        )
        user.profile.role = 'Student'
        user.profile.first_name = level_name
        user.profile.last_name = 'Student'
        user.profile.save()
        Student.objects.create(
            candidate=user,
            academic_session=self.admission_session,
            course=self.course,
            semester=semester_name,
            current_level=level_name,
            admitted_by=self.admin,
        )
        ApplicationSubmission.objects.create(
            applicant=user, confirm=False, academic_sess=self.admission_session,
            course_name=self.course, is_admitted=False,
        )
        return user

    def admit_student_user(self, user, session=None, course=None):
        session = session or self.admission_session
        course = course or self.course
        ApplicationSubmission.objects.update_or_create(
            applicant=user,
            defaults={
                'confirm': True,
                'academic_sess': session,
                'course_name': course,
                'is_admitted': True,
            },
        )
        return Admitted.objects.update_or_create(
            applicant=user,
            defaults={
                'academic_session': session,
                'admitted_course': course,
                'semester': 'First Semester',
                'admitted_level': 'NDI',
            },
        )[0]

    def assert_dashboard_loads_for(self, user):
        self.client.force_login(user)
        response = self.client.get(reverse('student_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('current_semester_instance', response.context)
        self.assertIn('current_semester', response.context)
        self.assertIn('current_session', response.context)
        return response

    def test_dashboard_loads_for_supported_student_levels(self):
        for academic_session in (self.admission_session, self.progressive_session):
            Semester.objects.create(
                name='First Semester',
                academic_session=academic_session,
                is_active=True,
                added_by=self.admin,
            )

        expected_sessions = {
            'NDI': self.admission_session,
            'HNDI': self.admission_session,
            'NDII': self.progressive_session,
            'HNDII': self.progressive_session,
        }
        for level_name, expected_session in expected_sessions.items():
            with self.subTest(level=level_name):
                user = self.create_student_user(level_name)
                response = self.assert_dashboard_loads_for(user)
                self.assertEqual(response.context['current_semester'], 'First Semester')
                self.assertEqual(response.context['current_session'], expected_session)
                self.assertIsNotNone(response.context['current_semester_instance'])

    def test_dashboard_loads_with_missing_semester_setup(self):
        user = self.create_student_user('NDI')

        response = self.assert_dashboard_loads_for(user)

        self.assertIsNone(response.context['current_semester_instance'])
        self.assertEqual(response.context['current_semester'], 'First Semester')
        self.assertContains(response, 'no semester has been configured')

    def test_dashboard_payment_history_includes_all_student_sessions(self):
        Semester.objects.create(
            name='First Semester',
            academic_session=self.admission_session,
            is_active=True,
            added_by=self.admin,
        )
        Semester.objects.create(
            name='First Semester',
            academic_session=self.progressive_session,
            is_active=True,
            added_by=self.admin,
        )
        user = self.create_student_user('NDII')
        Payment.objects.create(
            user=user,
            amount=Decimal('10000.00'),
            status='Approved',
            payment_source='Application Form',
            verified=True,
            email=user.email,
            academic_sess=self.admission_session,
            added_by=self.admin,
        )
        Payment.objects.create(
            user=user,
            amount=Decimal('25000.00'),
            status='Approved',
            payment_source='School Fees',
            verified=True,
            email=user.email,
            academic_sess=self.progressive_session,
            added_by=self.admin,
        )

        response = self.assert_dashboard_loads_for(user)

        self.assertEqual(response.context['current_session'], self.progressive_session)
        self.assertContains(response, 'Application Form')
        self.assertContains(response, 'School Fees')

    def test_dashboard_shows_friendly_message_without_payments(self):
        Semester.objects.create(
            name='First Semester',
            academic_session=self.admission_session,
            is_active=True,
            added_by=self.admin,
        )
        user = self.create_student_user('NDI')

        response = self.assert_dashboard_loads_for(user)

        self.assertContains(response, 'You have not made any payment yet.')

    def create_course_registration_dashboard_state(self):
        semester = Semester.objects.create(
            name='First Semester',
            academic_session=self.admission_session,
            is_active=True,
            added_by=self.admin,
        )
        self.create_application_prerequisite_fees()
        user = self.create_student_user('NDI')
        self.create_paid_prerequisites(user)
        self.create_application_payment(
            user,
            source='School Fees',
            status='Approved',
            verified=True,
        ).amount = Decimal('40000.00')
        school_payment = Payment.objects.filter(
            user=user, payment_source='School Fees'
        ).get()
        school_payment.amount = Decimal('40000.00')
        school_payment.save(update_fields=['amount'])
        level = Level.objects.get(name='NDI')
        StudentFeePayment.objects.create(
            student=user,
            course_of_study=self.course,
            academic_session=self.admission_session,
            student_level=level,
            payment_type='First Semester',
            has_paid_first_semester=True,
        )
        course_unit = CourseUnit.objects.create(
            academic_session=self.admission_session,
            course_title='Introduction to Community Health',
            course_code='CHE 101',
            credit_load=3,
            course_of_study=self.course,
            semester=semester,
            level=level,
            added_by=self.admin,
        )
        return user, semester, level, course_unit

    def test_dashboard_course_registration_card_prints_active_semester_registration(self):
        user, semester, level, course_unit = self.create_course_registration_dashboard_state()
        CourseEnrollment.objects.create(
            student=user,
            course_unit=course_unit,
            semester=semester,
            academic_session=self.admission_session,
            programme=self.course,
            level=level,
            added_by=user,
        )

        response = self.assert_dashboard_loads_for(user)

        self.assertTrue(response.context['student_reg_courses'])
        self.assertContains(response, 'Print Course(s)')
        self.assertContains(response, reverse('student_semester_registered_courses'))
        self.assertNotContains(response, 'Register Course(s)')

    def test_dashboard_course_registration_card_registers_when_active_semester_has_none(self):
        user, _semester, _level, _course_unit = self.create_course_registration_dashboard_state()

        response = self.assert_dashboard_loads_for(user)

        self.assertFalse(response.context['student_reg_courses'])
        self.assertContains(response, 'Register Course(s)')
        self.assertNotContains(response, 'Print Course(s)')

    def create_application_fee(self, session=None, name='Application Form'):
        return ApplicationFee.objects.create(
            academic_session=session or self.admission_session,
            category='ND',
            name=name,
            amount=Decimal('5000.00'),
            indigene_type='NON-INDIGENE',
            added_by=self.admin,
        )

    def create_application_prerequisite_fees(self):
        for name in ('Application Form', 'Acceptance Fee', 'Caution Fee', 'Other Charges'):
            ApplicationFee.objects.create(
                academic_session=self.admission_session,
                category='ND',
                name=name,
                amount=Decimal('5000.00'),
                indigene_type='NON-INDIGENE',
                added_by=self.admin,
            )

    def create_first_semester_fee(self):
        Fee.objects.create(
            academic_session=self.admission_session,
            name='School Fees',
            level='NDI',
            semester='First Semester',
            amount=Decimal('20000.00'),
            indigene_type='NON-INDIGENE',
            course_of_study=self.course,
            added_by=self.admin,
        )

    def create_paid_prerequisites(self, user):
        self.admit_student_user(user)
        self.create_application_payment(user, source='Application Form', status='Approved', verified=True)
        self.create_application_payment(user, source='Acceptance Fee', status='Approved', verified=True)
        self.create_application_payment(user, source='Caution Fee', status='Approved', verified=True)
        self.create_application_payment(user, source='Other Charges', status='Approved', verified=True)
        Fee.objects.create(
            academic_session=self.admission_session,
            name='School Fees',
            level='NDI',
            semester='Full Session',
            amount=Decimal('40000.00'),
            indigene_type='NON-INDIGENE',
            course_of_study=self.course,
            added_by=self.admin,
        )

    def create_application_payment(self, user, session=None, source='Application Form', status='Approved', verified=True):
        return Payment.objects.create(
            user=user,
            amount=Decimal('5000.00'),
            payment_method='Paystack Online',
            status=status,
            payment_source=source,
            pay_ref=f'{source}-{user.pk}-{status}' if status == 'Pending' and not verified else '',
            verified=verified,
            email=user.email or 'student@example.com',
            fee_category='ND',
            academic_sess=session or self.admission_session,
            added_by=self.admin,
        )

    def test_dashboard_summary_recognizes_verified_application_form_payment_in_admission_session(self):
        Semester.objects.create(
            name='First Semester',
            academic_session=self.admission_session,
            is_active=True,
            added_by=self.admin,
        )
        self.create_application_fee()
        user = self.create_student_user('NDI')
        self.create_application_payment(user)

        response = self.assert_dashboard_loads_for(user)

        self.assertTrue(response.context['has_application_fee_payment'])
        self.assertNotContains(response, 'Application Form Fee is configured but has not been paid.')
        self.assertContains(response, 'Application Form')

    def test_dashboard_summary_still_marks_unpaid_application_form_as_unpaid(self):
        Semester.objects.create(
            name='First Semester',
            academic_session=self.admission_session,
            is_active=True,
            added_by=self.admin,
        )
        self.create_application_fee()
        user = self.create_student_user('NDI')

        response = self.assert_dashboard_loads_for(user)

        self.assertFalse(response.context['has_application_fee_payment'])
        self.assertEqual(response.context['next_fee_name'], 'Application Form Fee')
        self.assertContains(response, 'Application Form Fee is configured but has not been paid.')

    def test_dashboard_recognizes_manual_successful_application_payment_name_variant(self):
        Semester.objects.create(
            name='First Semester',
            academic_session=self.admission_session,
            is_active=True,
            added_by=self.admin,
        )
        self.create_application_fee()
        user = self.create_student_user('NDI')
        self.create_application_payment(
            user,
            source='Application form',
            status='Successful',
            verified=False,
        )

        response = self.assert_dashboard_loads_for(user)

        self.assertTrue(response.context['has_application_fee_payment'])
        self.assertNotContains(response, 'Application Form Fee is configured but has not been paid.')
        self.assertContains(response, 'Application Form')
        self.assertContains(response, 'Successful')

    def test_dashboard_does_not_count_application_payment_from_wrong_session(self):
        Semester.objects.create(
            name='First Semester',
            academic_session=self.admission_session,
            is_active=True,
            added_by=self.admin,
        )
        self.create_application_fee()
        user = self.create_student_user('NDI')
        self.create_application_payment(user, session=self.progressive_session)

        response = self.assert_dashboard_loads_for(user)

        self.assertFalse(response.context['has_application_fee_payment'])
        self.assertEqual(response.context['next_fee_name'], 'Application Form Fee')

    def test_initiated_incomplete_payment_shows_complete_payment_not_pending_verification(self):
        Semester.objects.create(
            name='First Semester',
            academic_session=self.admission_session,
            is_active=True,
            added_by=self.admin,
        )
        self.create_application_fee()
        user = self.create_student_user('NDI')
        self.create_application_payment(
            user,
            source='Application Form',
            status='Pending',
            verified=False,
        )

        response = self.assert_dashboard_loads_for(user)

        self.assertEqual(response.context['next_fee_status'], 'Payment Not Completed')
        self.assertEqual(response.context['next_fee_action_label'], 'Complete Payment')
        self.assertTrue(response.context['can_pay_next_fee'])
        self.assertNotContains(response, 'Payment pending verification')
        self.assertContains(response, 'Payment Not Completed')
        self.assertContains(response, 'Complete Payment')

    def test_failed_payment_allows_retry(self):
        Semester.objects.create(
            name='First Semester',
            academic_session=self.admission_session,
            is_active=True,
            added_by=self.admin,
        )
        self.create_application_fee()
        user = self.create_student_user('NDI')
        self.create_application_payment(
            user,
            source='Application Fee',
            status='Failed',
            verified=False,
        )

        response = self.assert_dashboard_loads_for(user)

        self.assertEqual(response.context['next_fee_status'], 'Failed')
        self.assertEqual(response.context['next_fee_action_label'], 'Retry Payment')
        self.assertTrue(response.context['can_pay_next_fee'])
        self.assertContains(response, 'Retry Payment')

    def test_non_admitted_student_does_not_see_acceptance_fee_payment_link(self):
        Semester.objects.create(
            name='First Semester',
            academic_session=self.admission_session,
            is_active=True,
            added_by=self.admin,
        )
        self.create_application_prerequisite_fees()
        user = self.create_student_user('NDI')
        self.create_application_payment(user, source='Application Form', status='Approved', verified=True)

        response = self.assert_dashboard_loads_for(user)

        self.assertEqual(response.context['next_fee_name'], 'Acceptance Fee')
        self.assertEqual(response.context['next_fee_status'], 'Unavailable')
        self.assertFalse(response.context['can_pay_next_fee'])
        self.assertEqual(response.context['next_fee_payment_url'], '')
        self.assertContains(response, 'Admission has not yet been offered')
        self.assertNotContains(response, 'fee=acceptance-fee')

    def test_admitted_student_sees_acceptance_fee_payment_link(self):
        Semester.objects.create(
            name='First Semester',
            academic_session=self.admission_session,
            is_active=True,
            added_by=self.admin,
        )
        self.create_application_prerequisite_fees()
        user = self.create_student_user('NDI')
        self.admit_student_user(user)
        self.create_application_payment(user, source='Application Form', status='Approved', verified=True)

        response = self.assert_dashboard_loads_for(user)

        self.assertEqual(response.context['next_fee_name'], 'Acceptance Fee')
        self.assertTrue(response.context['can_pay_next_fee'])
        self.assertIn('fee=acceptance-fee', response.context['next_fee_payment_url'])

    def test_acceptance_fee_uses_admitted_session_on_dashboard_and_payment(self):
        Semester.objects.create(
            name='First Semester', academic_session=self.admission_session,
            is_active=True, added_by=self.admin,
        )
        self.create_application_prerequisite_fees()
        older_fee = ApplicationFee.objects.get(
            academic_session=self.admission_session, name='Acceptance Fee',
            category='ND', indigene_type='NON-INDIGENE',
        )
        older_fee.amount = Decimal('12000.00')
        older_fee.save(update_fields=['amount'])
        ApplicationFee.objects.create(
            academic_session=self.admission_session, category='ND', name='Screening Fee',
            amount=Decimal('7000.00'), indigene_type='NON-INDIGENE', added_by=self.admin,
        )
        for name in ('Application Form', 'Screening Fee', 'Acceptance Fee'):
            ApplicationFee.objects.create(
                academic_session=self.progressive_session, category='ND', name=name,
                amount=Decimal('99000.00'), indigene_type='NON-INDIGENE', added_by=self.admin,
            )

        user = self.create_student_user('NDI')
        self.admit_student_user(user)
        self.assertEqual(get_student_application_session(user), self.admission_session)
        ApplicantValidation.objects.create(
            applicant=user, email='admitted@example.com', is_validated=True,
        )
        pre_applicant = PreApplicant.objects.create(
            applicant=user, course_name=self.course,
            academic_session=self.progressive_session, phone_number='08012345678',
        )
        self.create_application_payment(user, source='Application Form')

        dashboard = self.assert_dashboard_loads_for(user)
        self.assertEqual(dashboard.context['acceptance_fee_admission_session'], self.admission_session)
        self.assertEqual(dashboard.context['acceptance_fee_amount'], older_fee.amount)
        self.assertEqual(dashboard.context['next_fee_amount'], older_fee.amount)
        self.assertTrue(dashboard.context['has_acceptance_fee_setup'])

        initiation = self.client.get(reverse('initiate_payment'), {'fee': 'acceptance-fee'})
        self.assertEqual(initiation.status_code, 200)
        self.assertEqual(initiation.context['current_session'], self.admission_session)
        self.assertEqual(initiation.context['fee_amount'], older_fee.amount)
        self.assertEqual(initiation.context['form'].fields['amount'].initial, older_fee.amount)

        posted = self.client.post(reverse('initiate_payment'), {
            'payment_source_intent': 'acceptance-fee',
            'amount': str(older_fee.amount), 'email': 'admitted@example.com',
        })
        self.assertEqual(posted.status_code, 200)
        payment = Payment.objects.get(user=user, payment_source='Acceptance Fee', status='Pending')
        self.assertEqual(payment.academic_sess, self.admission_session)
        self.assertEqual(payment.amount, older_fee.amount)
        self.assertEqual(payment.fee_category, 'ND')
        self.assertTrue(_payment_matches_configured_fee(payment))

        verified_acceptance = self.create_application_payment(user, source='Acceptance Fee')
        verified_acceptance.indigene_type = 'NON-INDIGENE'
        verified_acceptance.save(update_fields=['indigene_type'])
        receipt = self.client.get(reverse('candidate_acceptance_receipt'))
        self.assertEqual(receipt.status_code, 200)
        self.assertEqual(receipt.context['student_candidate'].academic_sess, self.admission_session)

        self.admin.profile.role = 'MIS ADMIN-2'
        self.admin.profile.save(update_fields=['role'])
        self.client.force_login(self.admin)
        manual_application = self.client.get(reverse('pay_application_fee', args=[pre_applicant.pk]))
        manual_screening = self.client.get(reverse('pay_screening_fee', args=[pre_applicant.pk]))
        self.assertEqual(manual_application.status_code, 200)
        self.assertEqual(manual_screening.status_code, 200)
        self.assertEqual(manual_application.context['fee_amount'], Decimal('5000.00'))
        self.assertEqual(manual_screening.context['fee_amount'], Decimal('7000.00'))

    def _assert_promoted_acceptance_fee_uses_admission(self, starting_level, category, course):
        for session in (self.admission_session, self.progressive_session):
            Semester.objects.create(
                name='First Semester', academic_session=session,
                is_active=True, added_by=self.admin,
            )
            for name, amount in (
                ('Application Form', Decimal('5000.00')),
                ('Acceptance Fee', Decimal('12000.00') if session == self.admission_session else Decimal('99000.00')),
            ):
                ApplicationFee.objects.create(
                    academic_session=session, category=category, name=name,
                    amount=amount, indigene_type='NON-INDIGENE', added_by=self.admin,
                )
        user = self.create_student_user(starting_level)
        student = user.student_profile
        student.course = course
        student.save(update_fields=['course'])
        self.admit_student_user(user, course=course)
        submission = ApplicationSubmission.objects.get(applicant=user)
        submission.course_name = course
        submission.save(update_fields=['course_name'])
        ApplicantValidation.objects.create(
            applicant=user, email=user.email, is_validated=True,
        )
        PreApplicant.objects.create(
            applicant=user, course_name=course,
            academic_session=self.admission_session, phone_number='08012345678',
        )
        self.create_application_payment(user, source='Application Form')

        self.admin.is_superuser = True
        self.admin.save(update_fields=['is_superuser'])
        self.client.force_login(self.admin)
        promoted = self.client.post(
            reverse('promote_students_next_level'), {'selected_students[]': [str(student.pk)]},
        )
        self.assertEqual(promoted.status_code, 302)
        student.refresh_from_db()
        self.assertEqual(student.academic_session, self.progressive_session)
        self.assertEqual(student.current_level, f'{starting_level[:-1]}II')
        self.assertEqual(get_student_application_session(user), self.admission_session)

        dashboard = self.assert_dashboard_loads_for(user)
        self.assertEqual(dashboard.context['acceptance_fee_admission_session'], self.admission_session)
        self.assertEqual(dashboard.context['acceptance_fee_amount'], Decimal('12000.00'))
        self.assertEqual(dashboard.context['next_fee_amount'], Decimal('12000.00'))
        self.assertEqual(dashboard.context['current_session'], self.progressive_session)

        initiation = self.client.get(reverse('initiate_payment'), {'fee': 'acceptance-fee'})
        self.assertEqual(initiation.status_code, 200)
        self.assertEqual(initiation.context['fee_amount'], Decimal('12000.00'))
        self.assertEqual(initiation.context['form'].fields['amount'].initial, Decimal('12000.00'))

        posted = self.client.post(reverse('initiate_payment'), {
            'payment_source_intent': 'acceptance-fee',
            'amount': '12000.00', 'email': user.email,
        })
        self.assertEqual(posted.status_code, 200)
        payment = Payment.objects.get(user=user, payment_source='Acceptance Fee', status='Pending')
        self.assertEqual(payment.academic_sess, self.admission_session)
        self.assertEqual(payment.amount, Decimal('12000.00'))
        self.assertEqual(payment.fee_category, category)
        self.assertTrue(_payment_matches_configured_fee(payment))

        self.client.force_login(self.admin)
        banker = self.client.get(reverse('pay_acceptance_fee', args=[student.pk]))
        self.assertEqual(banker.status_code, 200)
        self.assertEqual(banker.context['fee_amount'], Decimal('12000.00'))

    def test_promoted_ndii_acceptance_uses_original_admission_session(self):
        self._assert_promoted_acceptance_fee_uses_admission('NDI', 'ND', self.course)

    def test_promoted_hndii_acceptance_uses_original_admission_session(self):
        hnd_course = CourseOfStudy.objects.create(
            name='HND Community Health', department=self.department, added_by=self.admin,
        )
        self._assert_promoted_acceptance_fee_uses_admission('HNDI', 'HND', hnd_course)

    def test_progression_session_change_does_not_change_admission_fee_session(self):
        user = self.create_student_user('NDI')
        self.admit_student_user(user)
        student = user.student_profile
        original_session = get_student_application_session(user)

        student.academic_session = self.progressive_session
        student.save(update_fields=['academic_session'])

        self.assertEqual(original_session, self.admission_session)
        self.assertEqual(get_student_application_session(user), original_session)
        self.assertEqual(Admitted.objects.get(applicant=user).academic_session, original_session)

    def test_programme_edit_after_promotion_keeps_admission_session(self):
        user = self.create_student_user('NDI')
        self.admit_student_user(user)
        student = user.student_profile
        student.student_id = 'REG-ADMISSION-1'
        student.current_level = 'NDII'
        student.academic_session = self.progressive_session
        student.save(update_fields=['student_id', 'current_level', 'academic_session'])
        new_course = CourseOfStudy.objects.create(
            name='ND Public Health', department=self.department, added_by=self.admin,
        )
        self.admin.is_superuser = True
        self.admin.save(update_fields=['is_superuser'])
        self.client.force_login(self.admin)

        response = self.client.post(reverse('search_and_change_programme'), {
            'edit': '1', 'student_id': student.student_id,
            'current_level': 'NDII', 'academic_session': self.progressive_session.pk,
            'course': new_course.pk,
        })

        self.assertEqual(response.status_code, 302)
        student.refresh_from_db()
        self.assertEqual(student.course, new_course)
        self.assertEqual(student.academic_session, self.progressive_session)
        self.assertEqual(get_student_application_session(user), self.admission_session)
        self.assertEqual(
            ApplicationSubmission.objects.get(applicant=user).academic_sess,
            self.admission_session,
        )

    def test_student_without_admission_lineage_does_not_guess_from_current_session(self):
        user = self.create_student_user('NDI')
        ApplicationSubmission.objects.filter(applicant=user).delete()
        student = user.student_profile
        student.academic_session = self.progressive_session
        student.save(update_fields=['academic_session'])

        self.assertIsNone(get_student_application_session(user))

    def test_late_registration_keeps_current_session_resolution(self):
        user = self.create_student_user('NDI')
        self.admit_student_user(user)
        student = user.student_profile
        student.current_level = 'NDII'
        student.academic_session = self.progressive_session
        student.save(update_fields=['current_level', 'academic_session'])
        for session, amount in ((self.admission_session, '2500.00'), (self.progressive_session, '7500.00')):
            ApplicationFee.objects.create(
                academic_session=session, category='ND', name='Late Registration',
                amount=Decimal(amount), indigene_type='NON-INDIGENE', added_by=self.admin,
            )

        self.assertEqual(get_student_application_session(user), self.admission_session)
        self.assertEqual(
            _dashboard_late_registration_fee(student, self.progressive_session, 'NON-INDIGENE').amount,
            Decimal('7500.00'),
        )

    def test_corrected_admitted_session_changes_application_fee_session(self):
        for session, amount in ((self.admission_session, '12000.00'), (self.progressive_session, '99000.00')):
            ApplicationFee.objects.create(
                academic_session=session, category='ND', name='Acceptance Fee',
                amount=Decimal(amount), indigene_type='NON-INDIGENE', added_by=self.admin,
            )
        ApplicationFee.objects.create(
            academic_session=self.progressive_session, category='ND', name='Application Form',
            amount=Decimal('5000.00'), indigene_type='NON-INDIGENE', added_by=self.admin,
        )
        Semester.objects.create(
            name='First Semester', academic_session=self.progressive_session,
            is_active=True, added_by=self.admin,
        )
        user = self.create_student_user('NDI')
        self.admit_student_user(user)
        student = user.student_profile
        self.assertEqual(get_student_application_session(user), self.admission_session)

        correct_student_level_and_admitted_session(
            student_id=student.pk, level=Level.objects.get(name='NDI'),
            admitted_session=self.progressive_session, changed_by=self.admin,
        )
        student.refresh_from_db()
        self.assertEqual(get_student_application_session(user), self.progressive_session)
        self.assertEqual(
            ApplicationSubmission.objects.get(applicant=user).academic_sess,
            self.progressive_session,
        )
        self.create_application_payment(user, session=self.progressive_session, source='Application Form')
        dashboard = self.assert_dashboard_loads_for(user)
        self.assertEqual(dashboard.context['acceptance_fee_admission_session'], self.progressive_session)
        self.assertEqual(dashboard.context['acceptance_fee_amount'], Decimal('99000.00'))
        self.assertEqual(dashboard.context['next_fee_amount'], Decimal('99000.00'))

    def test_missing_acceptance_fee_for_admitted_session_keeps_warning(self):
        Semester.objects.create(
            name='First Semester', academic_session=self.admission_session,
            is_active=True, added_by=self.admin,
        )
        self.create_application_fee()
        ApplicationFee.objects.create(
            academic_session=self.progressive_session, category='ND',
            name='Acceptance Fee', amount=Decimal('99000.00'),
            indigene_type='NON-INDIGENE', added_by=self.admin,
        )
        user = self.create_student_user('NDI')
        self.admit_student_user(user)
        self.create_application_payment(user, source='Application Form')

        dashboard = self.assert_dashboard_loads_for(user)
        self.assertFalse(dashboard.context['has_acceptance_fee_setup'])
        self.assertEqual(dashboard.context['next_fee_status'], 'Setup missing')
        self.assertContains(dashboard, 'Acceptance Fee setup was not found')

    def test_paid_acceptance_fee_no_longer_displays_as_pending_for_admitted_student(self):
        Semester.objects.create(
            name='First Semester',
            academic_session=self.admission_session,
            is_active=True,
            added_by=self.admin,
        )
        self.create_application_prerequisite_fees()
        user = self.create_student_user('NDI')
        self.admit_student_user(user)
        self.create_application_payment(user, source='Application Form', status='Approved', verified=True)
        self.create_application_payment(user, source='Acceptance Fee', status='Approved', verified=True)

        response = self.assert_dashboard_loads_for(user)

        self.assertTrue(response.context['has_acceptance_fee_payment'])
        self.assertNotEqual(response.context['next_fee_name'], 'Acceptance Fee')
        self.assertNotContains(response, 'Acceptance Fee is configured but has not been paid.')

    def test_first_semester_guard_accepts_verified_application_fee_name_variant(self):
        Semester.objects.create(
            name='First Semester',
            academic_session=self.admission_session,
            is_active=True,
            added_by=self.admin,
        )
        self.create_application_prerequisite_fees()
        self.create_first_semester_fee()
        user = self.create_student_user('NDI')
        self.admit_student_user(user)
        self.create_application_payment(user, source='Application Fee', status='Approved', verified=True)
        self.create_application_payment(user, source='Acceptance Fee', status='Approved', verified=True)
        self.create_application_payment(user, source='Caution Fee', status='Approved', verified=True)
        self.create_application_payment(user, source='Other Charges', status='Approved', verified=True)

        ready, message = get_student_prerequisite_fee_gate(user, user.student_profile, 'NON-INDIGENE')

        self.assertTrue(ready, message)
        self.client.force_login(user)
        response = self.client.get(reverse('select_fee_payment'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'First Semester Tuition Fee')

    def test_select_fee_displays_full_session_when_tuition_and_late_registration_share_full_session(self):
        Semester.objects.create(
            name='First Semester',
            academic_session=self.admission_session,
            is_active=True,
            added_by=self.admin,
        )
        self.create_application_prerequisite_fees()
        self.create_first_semester_fee()
        Fee.objects.create(
            academic_session=self.admission_session,
            name='Late Registration',
            level='NDI',
            semester='Full Session',
            amount=Decimal('2000.00'),
            indigene_type='NON-INDIGENE',
            course_of_study=self.course,
            added_by=self.admin,
        )
        user = self.create_student_user('NDI')
        self.create_paid_prerequisites(user)

        self.client.force_login(user)
        response = self.client.get(reverse('select_fee_payment'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'First Semester Tuition Fee')
        self.assertContains(response, 'Full Session Tuition Fee')
        self.assertNotContains(response, 'Full Session Tuition Fee setup was not found')

    def test_late_registration_activates_on_closing_date_and_uses_application_fee_amount(self):
        self.create_application_prerequisite_fees()
        user = self.create_student_user('NDI')
        self.create_paid_prerequisites(user)
        ApplicationFee.objects.create(
            academic_session=self.admission_session,
            category='ND',
            name='Late Registration',
            amount=Decimal('5000.00'),
            indigene_type='NON-INDIGENE',
            added_by=self.admin,
        )
        student = user.student_profile
        closing_date = date(2026, 6, 15)
        self.admission_session.closing_date = closing_date
        self.admission_session.save(update_fields=['closing_date'])

        def resolve_for(day):
            with patch('accounts.views.date') as mocked_date:
                mocked_date.today.return_value = day
                return _next_fee_context(
                    student=student,
                    user=user,
                    indigene_type='NON-INDIGENE',
                    current_session=self.admission_session,
                    current_semester='First Semester',
                    current_semester_instance=None,
                )

        before = resolve_for(date(2026, 6, 14))
        on_date = resolve_for(closing_date)
        after = resolve_for(date(2026, 6, 16))

        self.assertNotEqual(before['next_fee_name'], 'Late Registration Fee')
        self.assertEqual(on_date['next_fee_name'], 'Late Registration Fee')
        self.assertEqual(on_date['next_fee_amount'], Decimal('5000.00'))
        self.assertEqual(after['next_fee_name'], 'Late Registration Fee')

    def test_dashboard_late_registration_requires_matching_indigene_type(self):
        self.create_application_prerequisite_fees()
        user = self.create_student_user('NDI')
        self.create_paid_prerequisites(user)
        ApplicationFee.objects.create(
            academic_session=self.admission_session,
            category='ND',
            name='Late Registration',
            amount=Decimal('2750.00'),
            indigene_type='INDIGENE',
            added_by=self.admin,
        )
        self.admission_session.closing_date = date(2026, 1, 1)
        self.admission_session.save(update_fields=['closing_date'])

        context = _next_fee_context(
            student=user.student_profile,
            user=user,
            indigene_type='NON-INDIGENE',
            current_session=self.admission_session,
            current_semester='First Semester',
            current_semester_instance=None,
        )

        self.assertEqual(context['next_fee_name'], 'Late Registration Fee')
        self.assertEqual(context['next_fee_status'], 'Setup missing')

    def _create_late_registration_student(self, username='late-registration-student'):
        Semester.objects.create(
            name='Second Semester',
            academic_session=self.admission_session,
            is_active=True,
            added_by=self.admin,
        )
        self.create_application_prerequisite_fees()
        user = self.create_student_user('NDI', semester_name='Second Semester')
        user.username = username
        user.email = f'{username}@example.com'
        user.save(update_fields=['username', 'email'])
        ApplicantValidation.objects.create(
            applicant=user,
            email=user.email,
            is_validated=True,
        )
        PreApplicant.objects.create(
            applicant=user,
            first_name='Late',
            last_name='Student',
            phone_number='08012345678',
            course_name=self.course,
            academic_session=self.admission_session,
            application_number=f'LATE-{user.pk}',
        )
        self.create_paid_prerequisites(user)
        ApplicationFee.objects.create(
            academic_session=self.admission_session,
            category='ND',
            name='Late Registration',
            amount=Decimal('5000.00'),
            indigene_type='NON-INDIGENE',
            added_by=self.admin,
        )
        self.admission_session.closing_date = date(2026, 1, 1)
        self.admission_session.save(update_fields=['closing_date'])
        return user

    def test_dashboard_late_registration_link_uses_explicit_payment_intent(self):
        user = self._create_late_registration_student()
        self.client.force_login(user)

        response = self.client.get(reverse('student_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'{reverse("initiate_payment")}?fee=late-registration')
        self.assertEqual(response.context['late_registration_fee_amount'], Decimal('5000.00'))

    def test_explicit_late_registration_intent_never_falls_through_to_school_fees(self):
        user = self._create_late_registration_student()
        self.client.force_login(user)

        response = self.client.get(reverse('initiate_payment'), {'fee': 'late-registration'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['payment_source'], 'Late Registration')
        self.assertEqual(response.context['fee_amount'], Decimal('5000.00'))
        self.assertEqual(response.context['current_session'], self.admission_session)
        self.assertContains(response, f'{self.admission_session} Session')
        self.assertNotEqual(response.context['payment_source'], 'School Fees')

    def test_late_registration_post_creates_late_registration_payment_at_configured_amount(self):
        user = self._create_late_registration_student()
        self.client.force_login(user)

        response = self.client.post(
            reverse('initiate_payment'),
            {
                'payment_source_intent': 'late-registration',
                'amount': '70200.00',
                'email': user.email,
            },
        )

        self.assertEqual(response.status_code, 200)
        payment = Payment.objects.get(user=user, status='Pending')
        self.assertEqual(payment.payment_source, 'Late Registration')
        self.assertEqual(payment.amount, Decimal('5000.00'))
        self.assertEqual(payment.fee_category, 'ND')
        self.assertEqual(response.context['fee_amount'], Decimal('5000.00'))

    def test_late_registration_verification_uses_configured_application_fee(self):
        user = self._create_late_registration_student()
        payment = Payment.objects.create(
            user=user,
            amount=Decimal('5000.00'),
            payment_method='Paystack Online',
            status='Pending',
            payment_source='Late Registration',
            pay_ref='late-registration-paystack-ref',
            verified=False,
            email=user.email,
            fee_category='ND',
            indigene_type='NON-INDIGENE',
            academic_sess=self.admission_session,
            added_by=self.admin,
        )

        with patch(
            'payments.services.PayStack.verify_payment',
            return_value=(True, {'status': 'success', 'amount': 500000}),
        ):
            result = verify_and_finalize_payment(payment.pay_ref)

        payment.refresh_from_db()
        self.assertTrue(result.success, result.message)
        self.assertTrue(payment.verified)
        self.assertEqual(payment.status, 'Approved')
        self.assertEqual(payment.payment_source, 'Late Registration')

    def test_school_fees_is_blocked_until_late_registration_is_verified(self):
        user = self._create_late_registration_student()
        self.client.force_login(user)

        response = self.client.get(reverse('select_fee_payment'))

        self.assertRedirects(
            response,
            f'{reverse("initiate_payment")}?fee=late-registration',
            fetch_redirect_response=False,
        )

    def test_school_fees_is_allowed_after_late_registration_is_verified(self):
        user = self._create_late_registration_student()
        Payment.objects.create(
            user=user,
            amount=Decimal('5000.00'),
            payment_method='Paystack Online',
            status='Approved',
            payment_source='Late Registration',
            pay_ref='verified-late-registration',
            verified=True,
            email=user.email or 'student@example.com',
            fee_category='ND',
            indigene_type='NON-INDIGENE',
            academic_sess=self.admission_session,
            added_by=self.admin,
        )
        self.client.force_login(user)

        response = self.client.get(reverse('select_fee_payment'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Tuition Payment')

    def test_late_registration_gate_does_not_change_pre_deadline_school_fees(self):
        user = self._create_late_registration_student()
        self.admission_session.closing_date = date(2099, 1, 1)
        self.admission_session.save(update_fields=['closing_date'])

        ready, message = get_late_registration_fee_gate(
            user,
            user.student_profile,
            self.admission_session,
            'Second Semester',
            'NON-INDIGENE',
        )

        self.assertTrue(ready, message)

    def test_first_semester_guard_blocks_incomplete_application_fee(self):
        Semester.objects.create(
            name='First Semester',
            academic_session=self.admission_session,
            is_active=True,
            added_by=self.admin,
        )
        self.create_application_prerequisite_fees()
        user = self.create_student_user('NDI')
        self.create_application_payment(user, source='Application Form', status='Pending', verified=False)

        ready, message = get_student_prerequisite_fee_gate(user, user.student_profile, 'NON-INDIGENE')

        self.assertFalse(ready)
        self.assertIn('checkout was not completed', message)

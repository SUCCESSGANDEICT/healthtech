from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

from academics.models import AcademicSession, CourseOfStudy, Department, Faculty
from admission.forms import PreApplicationForm, get_recent_application_sessions
from admission.models import PreApplicant
from admission.helpers import has_verified_application_fee
from fees.models import ApplicationFee
from payments.models import Payment


class PreApplicationSessionSelectionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='pass')
        self.faculty = Faculty.objects.create(name='Health Sciences', added_by=self.admin)
        self.department = Department.objects.create(
            name='Community Health',
            faculty=self.faculty,
            dept_type='Academic',
            added_by=self.admin,
        )
        self.course = CourseOfStudy.objects.create(
            name='NATIONAL DIPLOMA IN COMMUNITY HEALTH',
            department=self.department,
            added_by=self.admin,
        )
        self.old_session = self.create_session('2023/2024', date(2023, 1, 1), is_active=False)
        self.previous_session = self.create_session('2024/2025', date(2024, 1, 1), is_active=False)
        self.latest_session = self.create_session('2025/2026', date(2025, 1, 1), is_active=True)

        for session in (self.old_session, self.previous_session, self.latest_session):
            ApplicationFee.objects.create(
                academic_session=session,
                category='ND',
                name='Application Form',
                amount='5000.00',
                added_by=self.admin,
            )

    def create_session(self, name, start_date, is_active=True):
        return AcademicSession.objects.create(
            name=name,
            is_active=is_active,
            start_date=start_date,
            end_date=date(start_date.year, 12, 31),
            resumption_date=start_date,
            closing_date=date(start_date.year, 12, 31),
            admission_date=start_date,
            added_by=self.admin,
        )

    def form_data(self, session):
        return {
            'first_name': 'Ada',
            'middle_name': '',
            'last_name': 'Applicant',
            'phone_number': '08012345678',
            'course_name': str(self.course.pk),
            'academic_session': str(session.pk),
        }

    def test_pre_application_form_lists_two_most_recent_fee_configured_sessions(self):
        form = PreApplicationForm()

        self.assertQuerySetEqual(
            form.fields['academic_session'].queryset,
            [self.latest_session, self.previous_session],
            transform=lambda session: session,
        )

    def test_applicant_can_select_either_recent_session_and_save_selection(self):
        user = User.objects.create_user(username='applicant', password='pass')

        form = PreApplicationForm(data=self.form_data(self.previous_session))

        self.assertTrue(form.is_valid(), form.errors)
        pre_applicant = form.save(commit=False)
        pre_applicant.applicant = user
        pre_applicant.save()

        saved_pre_applicant = PreApplicant.objects.get(applicant=user)
        self.assertEqual(saved_pre_applicant.academic_session, self.previous_session)

    def test_selected_session_must_have_application_fee_for_programme_category(self):
        no_fee_session = self.create_session('2026/2027', date(2026, 1, 1), is_active=True)

        form = PreApplicationForm(data=self.form_data(no_fee_session))

        self.assertFalse(form.is_valid())
        self.assertIn('academic_session', form.errors)

    def test_single_session_fallback_still_works(self):
        ApplicationFee.objects.exclude(academic_session=self.latest_session).delete()

        sessions = list(get_recent_application_sessions())

        self.assertEqual(sessions, [self.latest_session])

    def create_pre_applicant(self, user, session):
        return PreApplicant.objects.create(
            applicant=user,
            first_name='Ada',
            last_name='Applicant',
            phone_number='08012345678',
            course_name=self.course,
            academic_session=session,
            application_number='ABC123',
        )

    def create_application_payment(self, user, session):
        return Payment.objects.create(
            user=user,
            amount='5000.00',
            payment_method='Paystack Online',
            status='Approved',
            payment_source='Application Form',
            pay_ref=f'ref-{session.pk}',
            verified=True,
            email='ada@example.com',
            fee_category='ND',
            academic_sess=session,
            added_by=self.admin,
        )

    def test_verified_application_fee_is_limited_to_applicant_session(self):
        user = User.objects.create_user(username='session_applicant', password='pass')
        self.create_pre_applicant(user, self.previous_session)
        self.create_application_payment(user, self.latest_session)

        self.assertIsNone(has_verified_application_fee(user))
        self.assertFalse(PreApplicant.has_paid_application_form(user))

    def test_verified_application_fee_returns_payment_for_matching_session(self):
        user = User.objects.create_user(username='matching_applicant', password='pass')
        self.create_pre_applicant(user, self.previous_session)
        payment = self.create_application_payment(user, self.previous_session)

        self.assertEqual(has_verified_application_fee(user), payment)
        self.assertTrue(PreApplicant.has_paid_application_form(user))

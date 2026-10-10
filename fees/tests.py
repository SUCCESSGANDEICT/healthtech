from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from academics.models import AcademicSession, CourseOfStudy, Department, Faculty, Level, Student
from accounts.models import LocalGovernment, State
from fees.forms import ApplicationFeeCreationForm, SchoolFeeCreationForm
from fees.models import ApplicationFee, Fee, StudentFeePayment
from fees.utils import FeeConfigurationError, get_application_fee, get_school_fee, get_tuition_fee_for_option
from payments.models import Payment
from payments.forms import LateRegistrationFeeForm
from payments.services import _finalize_school_fees_payment, _payment_matches_configured_fee
from payments.views import get_tuition_payment_state
from admission.forms import get_application_fee_category


class ProgrammeTuitionFeeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='pass')
        self.admin.profile.role = 'MIS ADMIN-1'
        self.admin.profile.save()
        self.state = State.objects.create(name='Benue')
        self.other_state = State.objects.create(name='Lagos')
        self.lga = LocalGovernment.objects.create(name='Makurdi', state=self.state)
        self.faculty = Faculty.objects.create(name='Health Sciences', added_by=self.admin)
        self.department = Department.objects.create(
            name='Community Health',
            faculty=self.faculty,
            dept_type='Academic',
            added_by=self.admin,
        )
        self.community_health = CourseOfStudy.objects.create(
            name='NATIONAL DIPLOMA IN COMMUNITY HEALTH',
            department=self.department,
            added_by=self.admin,
        )
        self.public_health = CourseOfStudy.objects.create(
            name='NATIONAL DIPLOMA IN PUBLIC HEALTH TECHNOLOGY',
            department=self.department,
            added_by=self.admin,
        )
        self.session = AcademicSession.objects.create(
            name='2026/2027',
            is_active=True,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            resumption_date=date(2026, 1, 1),
            closing_date=date(2026, 12, 31),
            admission_date=date(2026, 1, 1),
            added_by=self.admin,
        )
        self.level = Level.objects.create(name='NDI', added_by=self.admin)

    def create_student(self, username='student', course=None, state=None):
        user = User.objects.create_user(username=username, password='pass')
        user.profile.role = 'Student'
        user.profile.state = state or self.state
        user.profile.lga = self.lga
        user.profile.save()
        Student.objects.create(
            candidate=user,
            academic_session=self.session,
            course=course or self.community_health,
            current_level='NDI',
            admitted_by=self.admin,
        )
        return user

    def create_tuition(self, course, semester, amount, indigene_type='INDIGENE'):
        return Fee.objects.create(
            academic_session=self.session,
            course_of_study=course,
            name='School Fees',
            level='NDI',
            semester=semester,
            amount=amount,
            indigene_type=indigene_type,
            added_by=self.admin,
        )

    def test_course_specific_tuition_fee_selection(self):
        self.create_tuition(self.community_health, 'First Semester', '120000.00')
        self.create_tuition(self.public_health, 'First Semester', '150000.00')

        fee = get_tuition_fee_for_option(
            'NDI',
            'First Semester',
            self.session,
            'INDIGENE',
            self.public_health,
        )

        self.assertEqual(fee.course_of_study, self.public_health)
        self.assertEqual(fee.amount, Decimal('150000.00'))

    def test_indigene_and_non_indigene_tuition_difference(self):
        self.create_tuition(self.community_health, 'Full Session', '200000.00', indigene_type='INDIGENE')
        self.create_tuition(self.community_health, 'Full Session', '250000.00', indigene_type='NON-INDIGENE')

        indigene_fee = get_tuition_fee_for_option('NDI', 'Full Session', self.session, 'INDIGENE', self.community_health)
        non_indigene_fee = get_tuition_fee_for_option('NDI', 'Full Session', self.session, 'NON-INDIGENE', self.community_health)

        self.assertEqual(indigene_fee.amount, Decimal('200000.00'))
        self.assertEqual(non_indigene_fee.amount, Decimal('250000.00'))

    def test_first_second_and_full_session_options_progress_correctly(self):
        user = self.create_student()
        self.create_tuition(self.community_health, 'First Semester', '100000.00')
        self.create_tuition(self.community_health, 'Second Semester', '90000.00')
        self.create_tuition(self.community_health, 'Full Session', '190000.00')

        initial_state = get_tuition_payment_state(user, user.student_profile, self.session, 'INDIGENE')
        self.assertTrue(initial_state['can_pay_first_semester'])
        self.assertTrue(initial_state['can_pay_full_session'])
        self.assertFalse(initial_state['can_pay_second_semester'])

        StudentFeePayment.objects.create(
            student=user,
            course_of_study=self.community_health,
            academic_session=self.session,
            student_level=self.level,
            payment_type='School Fees',
            is_indigene=True,
            has_paid_first_semester=True,
        )

        next_state = get_tuition_payment_state(user, user.student_profile, self.session, 'INDIGENE')
        self.assertTrue(next_state['can_pay_second_semester'])
        self.assertEqual(next_state['second_semester_fee_amount'], Decimal('90000.00'))
        self.assertFalse(next_state['can_pay_full_session'])

    def test_duplicate_programme_tuition_fee_validation(self):
        self.create_tuition(self.community_health, 'First Semester', '100000.00')

        form = SchoolFeeCreationForm(data={
            'academic_session': self.session.pk,
            'course_of_study': self.community_health.pk,
            'name': 'School Fees',
            'level': 'NDI',
            'semester': 'First Semester',
            'indigene_type': 'INDIGENE',
            'amount': '100000.00',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('already exists', str(form.errors))

    def test_tuition_requires_programme_but_non_tuition_still_works(self):
        tuition = Fee(
            academic_session=self.session,
            name='School Fees',
            level='NDI',
            semester='First Semester',
            amount='100000.00',
            indigene_type='INDIGENE',
            added_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            tuition.clean()

        late_fee = Fee.objects.create(
            academic_session=self.session,
            name='Late Registration',
            level='NDI',
            semester='Full Session',
            amount='10000.00',
            indigene_type='INDIGENE',
            added_by=self.admin,
        )
        resolved = get_school_fee('NDI', 'Full Session', self.session, 'INDIGENE', name='Late Registration', course_of_study=self.community_health)
        self.assertEqual(resolved, late_fee)

    def test_non_tuition_application_fee_still_resolves(self):
        ApplicationFee.objects.create(
            academic_session=self.session,
            category='ND',
            name='Acceptance Fee',
            amount='20000.00',
            indigene_type='INDIGENE',
            added_by=self.admin,
        )

        fee = get_application_fee('ND', 'Acceptance Fee', self.session, 'INDIGENE')

        self.assertEqual(fee.amount, Decimal('20000.00'))

    def test_application_fee_form_exposes_late_registration_choice(self):
        choices = dict(ApplicationFeeCreationForm().fields['name'].choices)

        self.assertEqual(choices['Late Registration'], 'Late Registration')

    def test_late_registration_category_resolution_supports_nd_hnd_and_certificate(self):
        hnd_course = CourseOfStudy.objects.create(
            name='HND IN COMMUNITY HEALTH', department=self.department, added_by=self.admin
        )
        certificate_course = CourseOfStudy.objects.create(
            name='CERTIFICATE IN COMMUNITY HEALTH', department=self.department, added_by=self.admin
        )
        for category in ('ND', 'HND', 'CERTIFICATE'):
            ApplicationFee.objects.create(
                academic_session=self.session,
                category=category,
                name='Late Registration',
                amount='10000.00',
                indigene_type='INDIGENE',
                added_by=self.admin,
            )

        self.assertEqual(get_application_fee_category(self.community_health, self.session, 'Late Registration', 'INDIGENE'), 'ND')
        self.assertEqual(get_application_fee_category(hnd_course, self.session, 'Late Registration', 'INDIGENE'), 'HND')
        self.assertEqual(get_application_fee_category(certificate_course, self.session, 'Late Registration', 'INDIGENE'), 'CERTIFICATE')

    def test_late_registration_payment_form_and_verification_use_application_fee(self):
        user = self.create_student()
        configured_fee = ApplicationFee.objects.create(
            academic_session=self.session,
            category='ND',
            name='Late Registration',
            amount='12000.00',
            indigene_type='INDIGENE',
            added_by=self.admin,
        )
        form = LateRegistrationFeeForm(
            user=user,
            academic_session=self.session,
            category='ND',
            indigene_type='INDIGENE',
        )
        tampered_form = LateRegistrationFeeForm(
            {'amount': '1.00', 'email': 'student@example.com'},
            user=user,
            academic_session=self.session,
            category='ND',
            indigene_type='INDIGENE',
        )
        payment = Payment.objects.create(
            user=user,
            amount=configured_fee.amount,
            payment_method='Paystack Online',
            status='Pending',
            payment_source='Late Registration',
            pay_ref='late-registration-ref',
            email=user.email,
            fee_category='ND',
            indigene_type='INDIGENE',
            academic_sess=self.session,
            added_by=self.admin,
        )
        payment.refresh_from_db()

        self.assertEqual(form.fields['amount'].initial, Decimal('12000.00'))
        self.assertTrue(tampered_form.is_valid(), tampered_form.errors)
        self.assertEqual(tampered_form.cleaned_data['amount'], Decimal('12000.00'))
        self.assertTrue(_payment_matches_configured_fee(payment))

    def test_existing_application_fee_charge_resolution_is_unchanged(self):
        for name in ('Acceptance Fee', 'Caution Fee', 'Other Charges'):
            with self.subTest(name=name):
                configured = ApplicationFee.objects.create(
                    academic_session=self.session,
                    category='ND',
                    name=name,
                    amount='15000.00',
                    indigene_type='INDIGENE',
                    added_by=self.admin,
                )
                self.assertEqual(
                    get_application_fee('ND', name, self.session, 'INDIGENE'),
                    configured,
                )

    def test_successful_payment_finalization_updates_without_active_semester(self):
        user = self.create_student()
        self.create_tuition(self.community_health, 'First Semester', '100000.00')
        payment = Payment.objects.create(
            user=user,
            amount='100000.00',
            payment_method='Paystack Online',
            status='Approved',
            payment_source='School Fees',
            pay_ref='first-semester-ref',
            verified=True,
            email='student@example.com',
            fee_category='First Semester',
            indigene_type='INDIGENE',
            academic_sess=self.session,
            added_by=self.admin,
        )

        _finalize_school_fees_payment(payment)

        student_fee = StudentFeePayment.objects.get(student=user, academic_session=self.session)
        self.assertTrue(student_fee.has_paid_first_semester)
        self.assertFalse(student_fee.has_paid_second_semester)

    def test_missing_programme_tuition_fails_closed(self):
        self.create_tuition(self.community_health, 'First Semester', '100000.00')

        with self.assertRaises(FeeConfigurationError):
            get_tuition_fee_for_option('NDI', 'First Semester', self.session, 'INDIGENE', self.public_health)

    def fee_formset_data(self, rows, total_forms=4):
        data = {
            'fees-TOTAL_FORMS': str(total_forms),
            'fees-INITIAL_FORMS': '0',
            'fees-MIN_NUM_FORMS': '0',
            'fees-MAX_NUM_FORMS': '1000',
        }
        for index in range(total_forms):
            row = rows.get(index, {})
            data.update({
                f'fees-{index}-academic_session': row.get('academic_session', ''),
                f'fees-{index}-course_of_study': row.get('course_of_study', ''),
                f'fees-{index}-name': row.get('name', ''),
                f'fees-{index}-level': row.get('level', ''),
                f'fees-{index}-semester': row.get('semester', ''),
                f'fees-{index}-indigene_type': row.get('indigene_type', ''),
                f'fees-{index}-amount': row.get('amount', ''),
            })
        return data

    def tuition_row(self, course=None, semester='First Semester', indigene_type='INDIGENE', amount='100000.00'):
        return {
            'academic_session': str(self.session.pk),
            'course_of_study': str((course or self.community_health).pk),
            'name': 'School Fees',
            'level': 'NDI',
            'semester': semester,
            'indigene_type': indigene_type,
            'amount': amount,
        }

    def test_add_school_fees_creates_multiple_tuition_fees(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('add_school_fees'), self.fee_formset_data({
            0: self.tuition_row(self.community_health, 'First Semester', 'INDIGENE', '100000.00'),
            1: self.tuition_row(self.public_health, 'First Semester', 'INDIGENE', '120000.00'),
            2: self.tuition_row(self.community_health, 'Second Semester', 'INDIGENE', '90000.00'),
            3: self.tuition_row(self.public_health, 'Second Semester', 'NON-INDIGENE', '130000.00'),
        }))

        self.assertRedirects(response, reverse('school_fees_list'))
        self.assertEqual(Fee.objects.count(), 4)
        messages = [str(message) for message in response.wsgi_request._messages]
        self.assertTrue(any('4 tuition fee records created successfully' in message for message in messages))

    def test_add_school_fees_ignores_blank_rows(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('add_school_fees'), self.fee_formset_data({
            0: self.tuition_row(amount='100000.00'),
        }))

        self.assertRedirects(response, reverse('school_fees_list'))
        self.assertEqual(Fee.objects.count(), 1)

    def test_add_school_fees_supports_dynamically_added_rows(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('add_school_fees'), self.fee_formset_data({
            4: self.tuition_row(self.public_health, 'Full Session', 'INDIGENE', '200000.00'),
        }, total_forms=5))

        self.assertRedirects(response, reverse('school_fees_list'))
        self.assertEqual(Fee.objects.count(), 1)
        fee = Fee.objects.get()
        self.assertEqual(fee.course_of_study, self.public_health)
        self.assertEqual(fee.semester, 'Full Session')

    def test_add_school_fees_allows_same_programme_with_different_semester(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('add_school_fees'), self.fee_formset_data({
            0: self.tuition_row(self.community_health, 'First Semester', 'INDIGENE', '100000.00'),
            1: self.tuition_row(self.community_health, 'Second Semester', 'INDIGENE', '90000.00'),
        }))

        self.assertRedirects(response, reverse('school_fees_list'))
        self.assertEqual(Fee.objects.filter(course_of_study=self.community_health).count(), 2)

    def test_add_school_fees_allows_same_programme_with_different_indigene_type(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('add_school_fees'), self.fee_formset_data({
            0: self.tuition_row(self.community_health, 'Full Session', 'INDIGENE', '180000.00'),
            1: self.tuition_row(self.community_health, 'Full Session', 'NON-INDIGENE', '220000.00'),
        }))

        self.assertRedirects(response, reverse('school_fees_list'))
        self.assertEqual(Fee.objects.filter(course_of_study=self.community_health).count(), 2)

    def test_add_school_fees_prevents_existing_exact_duplicate(self):
        self.create_tuition(self.community_health, 'First Semester', '100000.00')
        self.client.force_login(self.admin)

        response = self.client.post(reverse('add_school_fees'), self.fee_formset_data({
            0: self.tuition_row(self.community_health, 'First Semester', 'INDIGENE', '100000.00'),
        }))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Fee.objects.count(), 1)
        self.assertContains(response, 'already exists')

    def test_add_school_fees_prevents_duplicate_rows_in_same_batch(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('add_school_fees'), self.fee_formset_data({
            0: self.tuition_row(self.community_health, 'First Semester', 'INDIGENE', '100000.00'),
            1: self.tuition_row(self.community_health, 'First Semester', 'INDIGENE', '110000.00'),
        }))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Fee.objects.count(), 0)
        self.assertContains(response, 'duplicates another submitted row')

    def test_add_school_fees_permission_is_preserved(self):
        ordinary_user = User.objects.create_user(username='ordinary', password='pass')
        ordinary_user.profile.role = 'Student'
        ordinary_user.profile.save()

        login_response = self.client.get(reverse('add_school_fees'))
        self.assertEqual(login_response.status_code, 302)

        self.client.force_login(ordinary_user)
        denied_response = self.client.get(reverse('add_school_fees'))
        self.assertEqual(denied_response.status_code, 403)

    def list_search_results(self, query):
        self.client.force_login(self.admin)
        return self.client.get(reverse('school_fees_list'), {'q': query})

    def assert_search_matches(self, query, expected_fees):
        response = self.list_search_results(query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['School_fees']), expected_fees)
        return response

    def test_school_fee_list_searches_by_programme_name(self):
        community_fee = self.create_tuition(self.community_health, 'First Semester', '100000.00')
        self.create_tuition(self.public_health, 'First Semester', '120000.00')

        self.assert_search_matches('community health', [community_fee])

    def test_school_fee_list_searches_by_academic_session(self):
        fee = self.create_tuition(self.community_health, 'First Semester', '100000.00')

        self.assert_search_matches('2026/2027', [fee])

    def test_school_fee_list_searches_by_level(self):
        hndi_fee = Fee.objects.create(
            academic_session=self.session,
            course_of_study=self.community_health,
            name='School Fees',
            level='HNDI',
            semester='First Semester',
            amount='100000.00',
            indigene_type='INDIGENE',
            added_by=self.admin,
        )
        Fee.objects.create(
            academic_session=self.session,
            course_of_study=self.public_health,
            name='School Fees',
            level='NDII',
            semester='First Semester',
            amount='110000.00',
            indigene_type='INDIGENE',
            added_by=self.admin,
        )

        self.assert_search_matches('HNDI', [hndi_fee])

    def test_school_fee_list_searches_by_semester_payment_option(self):
        first_fee = self.create_tuition(self.community_health, 'First Semester', '100000.00')
        self.create_tuition(self.community_health, 'Second Semester', '90000.00')

        self.assert_search_matches('First Semester', [first_fee])

    def test_school_fee_list_searches_by_indigene_type(self):
        indigene_fee = self.create_tuition(self.community_health, 'Full Session', '180000.00', indigene_type='INDIGENE')
        self.create_tuition(self.community_health, 'Full Session', '220000.00', indigene_type='NON-INDIGENE')

        self.assert_search_matches('INDIGENE', [indigene_fee])

    def test_school_fee_list_empty_search_returns_full_list(self):
        fee_one = self.create_tuition(self.community_health, 'First Semester', '100000.00')
        fee_two = self.create_tuition(self.public_health, 'First Semester', '120000.00')

        response = self.assert_search_matches('', [fee_one, fee_two])
        self.assertContains(response, 'Add New Tuition')

    def test_school_fee_list_no_result_search_displays_empty_state(self):
        self.create_tuition(self.community_health, 'First Semester', '100000.00')

        response = self.list_search_results('does-not-exist')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No tuition fee records found.')
        self.assertContains(response, 'Clear Search')

    def test_school_fee_list_uses_single_reusable_view_modal(self):
        fee = self.create_tuition(self.community_health, 'First Semester', '100000.00')
        self.client.force_login(self.admin)

        response = self.client.get(reverse('school_fees_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="tuitionDetailsModal"', count=1)
        self.assertContains(response, 'Tuition Fee Details')
        self.assertContains(response, 'class="btn btn-secondary view-tuition-details"')
        self.assertContains(response, f'data-edit-url="{reverse("update_school_fee", args=[fee.pk])}"')
        self.assertNotContains(response, f'id="tuitionDetails{fee.pk}"')

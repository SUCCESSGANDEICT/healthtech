from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from academics.models import AcademicSession, CourseOfStudy, Department, Faculty
from accounts.models import LocalGovernment, State
from admission.models import Admitted, ApplicantValidation, ApplicationSubmission, PreApplicant
from fees.models import ApplicationFee
from payments.models import Payment


class CandidateOnlinePaymentRoutingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='pass')
        self.state = State.objects.create(name='Benue')
        self.lga = LocalGovernment.objects.create(name='Makurdi', state=self.state)
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
        self.session = AcademicSession.objects.create(
            name='2025/2026',
            is_active=True,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            resumption_date=date(2025, 1, 1),
            closing_date=date(2025, 12, 31),
            admission_date=date(2025, 1, 1),
            added_by=self.admin,
        )
        ApplicationFee.objects.create(
            academic_session=self.session,
            category='ND',
            name='Application Form',
            amount='5000.00',
            added_by=self.admin,
        )
        ApplicationFee.objects.create(
            academic_session=self.session,
            category='ND',
            name='Screening Fee',
            amount='7000.00',
            added_by=self.admin,
        )
        ApplicationFee.objects.create(
            academic_session=self.session,
            category='ND',
            name='Acceptance Fee',
            amount='9000.00',
            added_by=self.admin,
        )

    def create_applicant(self, username):
        user = User.objects.create_user(username=username, password='pass')
        user.profile.role = 'Applicant'
        user.profile.first_name = 'Ada'
        user.profile.last_name = 'Applicant'
        user.profile.date_birth = date(2000, 1, 1)
        user.profile.phone_number = '08012345678'
        user.profile.gender = 'Female'
        user.profile.marital_status = 'Single'
        user.profile.religion = 'Christianity'
        user.profile.nationality = 'Nigeria'
        user.profile.home_address = '1 Health Road'
        user.profile.state = self.state
        user.profile.lga = self.lga
        user.profile.image = 'profile_images/test.jpg'
        user.profile.save()
        ApplicantValidation.objects.create(
            applicant=user,
            email=f'{username}@example.com',
            is_validated=True,
        )
        PreApplicant.objects.create(
            applicant=user,
            first_name='Ada',
            last_name='Applicant',
            phone_number='08012345678',
            course_name=self.course,
            academic_session=self.session,
            application_number=f'APP-{username}'[:20],
        )
        return user

    def create_payment(self, user, payment_source, amount, status='Approved', verified=True):
        return Payment.objects.create(
            user=user,
            amount=amount,
            payment_method='Paystack Online',
            status=status,
            payment_source=payment_source,
            pay_ref=f'{payment_source}-{user.pk}-{status}',
            verified=verified,
            email=f'{user.username}@example.com',
            fee_category='ND',
            academic_sess=self.session,
            added_by=self.admin,
        )

    def admit_applicant(self, user):
        ApplicationSubmission.objects.create(
            applicant=user,
            confirm=True,
            academic_sess=self.session,
            course_name=self.course,
            is_admitted=True,
        )
        return Admitted.objects.create(
            applicant=user,
            academic_session=self.session,
            admitted_course=self.course,
            semester='First Semester',
            admitted_level='NDI',
        )

    def login(self, user):
        self.client.force_login(user)

    def test_verified_application_unpaid_screening_loads_screening_fee(self):
        user = self.create_applicant('screening_due')
        self.create_payment(user, 'Application Form', '5000.00')
        self.login(user)

        response = self.client.get(reverse('initiate_payment'), {'fee': 'screening-fee'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['payment_source'], 'Screening Fee')
        self.assertEqual(str(response.context['fee_amount']), '7000.00')

    def test_screening_post_creates_screening_payment_only(self):
        user = self.create_applicant('screening_post')
        self.create_payment(user, 'Application Form', '5000.00')
        self.login(user)

        response = self.client.post(
            reverse('initiate_payment'),
            {
                'payment_source_intent': 'screening-fee',
                'amount': '7000.00',
                'email': 'screening_post@example.com',
            },
        )

        self.assertEqual(response.status_code, 200)
        payment = Payment.objects.get(user=user, status='Pending')
        self.assertEqual(payment.payment_source, 'Screening Fee')
        self.assertEqual(str(payment.amount), '7000.00')
        self.assertEqual(payment.fee_category, 'ND')
        self.assertFalse(
            Payment.objects.filter(user=user, payment_source='Application Form', status='Pending').exists()
        )

    def test_paid_application_and_screening_do_not_prompt_screening_again(self):
        user = self.create_applicant('all_paid')
        self.create_payment(user, 'Application Form', '5000.00')
        self.create_payment(user, 'Screening Fee', '7000.00')
        self.login(user)

        response = self.client.get(reverse('initiate_payment'), {'fee': 'screening-fee'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('accounts_dashboard'))

    def test_screening_link_is_rejected_when_application_is_unpaid(self):
        user = self.create_applicant('application_due')
        self.login(user)

        response = self.client.get(reverse('initiate_payment'), {'fee': 'screening-fee'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('accounts_profile'))
        self.assertFalse(Payment.objects.filter(user=user, status='Pending').exists())

    def test_non_admitted_candidate_cannot_initialize_acceptance_fee_directly(self):
        user = self.create_applicant('not_admitted_acceptance')
        self.create_payment(user, 'Application Form', '5000.00')
        self.create_payment(user, 'Screening Fee', '7000.00')
        self.login(user)

        response = self.client.get(reverse('initiate_payment'), {'fee': 'acceptance-fee'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('accounts_dashboard'))
        self.assertFalse(Payment.objects.filter(user=user, payment_source='Acceptance Fee').exists())

    def test_admitted_candidate_can_initialize_acceptance_fee(self):
        user = self.create_applicant('admitted_acceptance')
        self.create_payment(user, 'Application Form', '5000.00')
        self.create_payment(user, 'Screening Fee', '7000.00')
        self.admit_applicant(user)
        self.login(user)

        response = self.client.get(reverse('initiate_payment'), {'fee': 'acceptance-fee'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['payment_source'], 'Acceptance Fee')
        self.assertEqual(str(response.context['fee_amount']), '9000.00')

    def test_acceptance_form_accepts_resolved_session_on_get_and_post(self):
        user = self.create_applicant('acceptance_session_kwargs')
        self.admit_applicant(user)
        self.create_payment(user, 'Application Form', '5000.00')
        self.create_payment(user, 'Screening Fee', '7000.00')
        newer_session = AcademicSession.objects.create(
            name='2026/2027', is_active=True,
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), added_by=self.admin,
        )
        ApplicationFee.objects.create(
            academic_session=newer_session, category='ND', name='Acceptance Fee',
            amount='99000.00', added_by=self.admin,
        )
        self.login(user)

        response = self.client.get(reverse('initiate_payment'), {'fee': 'acceptance-fee'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['current_session'], self.session)
        self.assertEqual(str(response.context['fee_amount']), '9000.00')
        self.assertEqual(str(response.context['form'].fields['amount'].initial), '9000.00')

        response = self.client.post(reverse('initiate_payment'), {
            'payment_source_intent': 'acceptance-fee', 'amount': '9000.00',
            'email': 'acceptance_session_kwargs@example.com',
        })
        self.assertEqual(response.status_code, 200)
        payment = Payment.objects.get(user=user, payment_source='Acceptance Fee', status='Pending')
        self.assertEqual(payment.academic_sess, self.session)
        self.assertEqual(str(payment.amount), '9000.00')

    def test_admitted_candidate_post_creates_acceptance_fee_payment(self):
        user = self.create_applicant('admitted_acceptance_post')
        self.create_payment(user, 'Application Form', '5000.00')
        self.create_payment(user, 'Screening Fee', '7000.00')
        self.admit_applicant(user)
        self.login(user)

        response = self.client.post(
            reverse('initiate_payment'),
            {
                'payment_source_intent': 'acceptance-fee',
                'amount': '9000.00',
                'email': 'admitted_acceptance_post@example.com',
            },
        )

        self.assertEqual(response.status_code, 200)
        payment = Payment.objects.get(user=user, status='Pending')
        self.assertEqual(payment.payment_source, 'Acceptance Fee')
        self.assertEqual(str(payment.amount), '9000.00')

    def test_failed_application_payment_does_not_unlock_screening_fee(self):
        user = self.create_applicant('failed_application')
        self.create_payment(user, 'Application Form', '5000.00', status='Pending', verified=False)
        self.login(user)

        response = self.client.get(reverse('initiate_payment'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['payment_source'], 'Application Form')
        self.assertEqual(str(response.context['fee_amount']), '5000.00')

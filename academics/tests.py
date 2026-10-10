from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages import constants, get_messages
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from fees.models import StudentFeePayment
from fees.utils import get_user_indigene_type
from payments.models import Payment
from admission.models import Admitted, ApplicationSubmission, Jamb, PreApplicant

from .models import (
    AcademicSession,
    CourseOfStudy,
    CourseEnrollment,
    CourseCarryover,
    CourseUnit,
    Department,
    Faculty,
    Level,
    Semester,
    Student,
    StudentAcademicCorrectionHistory,
)


class AcademicSessionChronologyTests(TestCase):
    def _create_session(self, start_year, *, is_active=False):
        return AcademicSession.objects.create(
            name=f'{start_year}/{start_year + 1}',
            start_date=date(start_year, 9, 1),
            end_date=date(start_year + 1, 8, 31),
            resumption_date=date(start_year, 9, 1),
            closing_date=date(start_year + 1, 8, 31),
            admission_date=date(start_year, 8, 1),
            is_active=is_active,
        )

    def test_historical_insertion_preserves_chronology_and_application_session(self):
        self._create_session(2023)
        self._create_session(2024)
        intended_application_session = self._create_session(2026, is_active=True)

        historical_session = self._create_session(2025)
        actual_added_date = historical_session.added_date

        self.assertQuerySetEqual(
            AcademicSession.objects.all(),
            ['2023/2024', '2024/2025', '2025/2026', '2026/2027'],
            transform=str,
        )
        historical_session.refresh_from_db()
        self.assertEqual(historical_session.added_date, actual_added_date)
        self.assertFalse(historical_session.is_active)
        self.assertEqual(
            AcademicSession.get_application_session(),
            intended_application_session,
        )

    def test_latest_active_session_uses_academic_dates_not_insertion_order(self):
        intended_application_session = self._create_session(2026, is_active=True)
        self._create_session(2025, is_active=True)

        self.assertEqual(
            AcademicSession.get_application_session(),
            intended_application_session,
        )


class StudentSearchTests(TestCase):
    ajax_headers = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}
    administrative_roles = (
        'Developer', 'Super Admin', 'Admin', 'ICT DIRECTOR',
        'ADMISSION-OFFICER', 'ADMISSION-OFFICER1',
        'MIS ADMIN-1', 'MIS ADMIN-2', 'Rector',
    )

    def setUp(self):
        self.staff = User.objects.create_user(username='searcher@example.com', password='test-pass')
        self.staff.profile.role = 'Super Admin'
        self.staff.profile.save(update_fields=['role'])
        faculty = Faculty.objects.create(name='Medical Sciences', added_by=self.staff)
        department = Department.objects.create(
            name='Public Health', faculty=faculty, added_by=self.staff
        )
        self.programme = CourseOfStudy.objects.create(
            name='Public Health Technology', department=department, added_by=self.staff
        )
        self.session = AcademicSession.objects.create(
            name='2026/2027', start_date=date(2026, 9, 1), end_date=date(2027, 8, 31),
            added_by=self.staff,
        )
        self.primary = self.create_student(
            username='amina.bello@example.com', first='Amina', middle='Grace', last='Bello',
            application='APP-26001', matric='MAT-26001',
        )
        self.no_matric = self.create_student(
            username='amina.yusuf@example.com', first='Amina', middle='Zainab', last='Bello',
            application='APP-26002', matric=None,
        )
        self.client.force_login(self.staff)

    def create_student(self, *, username, first, middle, last, application, matric):
        user = User.objects.create_user(username=username, email=username, password='test-pass')
        user.profile.first_name = first
        user.profile.middle_name = middle
        user.profile.last_name = last
        user.profile.role = 'Student'
        user.profile.save(update_fields=['first_name', 'middle_name', 'last_name', 'role'])
        PreApplicant.objects.create(
            applicant=user, first_name=first, middle_name=middle, last_name=last,
            course_name=self.programme, academic_session=self.session,
            application_number=application,
        )
        return Student.objects.create(
            student_id=matric, candidate=user, academic_session=self.session,
            course=self.programme, current_level='NDI', admitted_by=self.staff,
        )

    def search(self, query):
        return self.client.get(
            reverse('student_search_results'), {'q': query}, **self.ajax_headers
        )

    def result_ids(self, response):
        return [item['id'] for item in response.json()['results']]

    def test_dedicated_page_uses_isolated_assets_and_ajax_endpoints(self):
        response = self.client.get(reverse('student_search'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'academics/student_search.html')
        self.assertContains(response, 'academics/student_search.css')
        self.assertContains(response, 'academics/student_search.js')
        self.assertContains(response, reverse('student_search_results'))
        self.assertContains(response, reverse('student_search_selection'))

    def test_every_administrative_role_can_see_and_open_search_student(self):
        for role in self.administrative_roles:
            with self.subTest(role=role):
                self.staff.profile.role = role
                self.staff.profile.save(update_fields=['role'])
                response = self.client.get(reverse('student_search'))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '>Students Report<')
                self.assertContains(
                    response,
                    f'href="{reverse("student_search")}">Search Student</a>',
                    count=1,
                )
                body = response.content.decode()
                self.assertLess(
                    body.index('>Students Report<'),
                    body.index(f'href="{reverse("student_search")}">Search Student</a>'),
                )

    def test_superuser_can_see_and_open_search_student(self):
        self.staff.is_superuser = True
        self.staff.profile.role = 'Developer'
        self.staff.save(update_fields=['is_superuser'])
        self.staff.profile.save(update_fields=['role'])
        response = self.client.get(reverse('student_search'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '>Students Report<')
        self.assertContains(response, '>Search Student</a>')

    def test_non_admin_cannot_see_or_access_student_search(self):
        for role in ('Student', 'Applicant', 'Staff'):
            with self.subTest(role=role):
                self.staff.profile.role = role
                self.staff.profile.save(update_fields=['role'])
                page_response = self.client.get(reverse('student_search'))
                self.assertRedirects(
                    page_response, reverse('accounts_login'), fetch_redirect_response=False
                )
                self.assertNotContains(page_response, 'Search Student', status_code=302)
                self.assertEqual(self.search('Amina').status_code, 403)
                selection_response = self.client.post(
                    reverse('student_search_selection'), {'student_id': self.primary.pk},
                    **self.ajax_headers,
                )
                self.assertEqual(selection_response.status_code, 403)

    def test_searches_first_middle_and_last_names_case_insensitively(self):
        for query in ('amiNA', 'graCE', 'beLLo'):
            with self.subTest(query=query):
                self.assertIn(self.primary.pk, self.result_ids(self.search(query)))

    def test_searches_email(self):
        self.assertEqual(self.result_ids(self.search('BELLO@EXAMPLE')), [self.primary.pk])

    def test_searches_application_number(self):
        self.assertEqual(self.result_ids(self.search('app-26001')), [self.primary.pk])

    def test_searches_matriculation_number(self):
        self.assertEqual(self.result_ids(self.search('mat-26001')), [self.primary.pk])

    def test_partial_typeahead_query_returns_matches(self):
        self.assertCountEqual(
            self.result_ids(self.search('APP-26')), [self.primary.pk, self.no_matric.pk]
        )

    def test_student_without_matriculation_number_is_searchable_by_application(self):
        result = self.search('APP-26002').json()['results'][0]
        self.assertEqual(result['id'], self.no_matric.pk)
        self.assertEqual(result['matriculation_number'], '')
        self.assertEqual(result['application_number'], 'APP-26002')

    def test_incomplete_student_record_is_returned_without_server_error(self):
        user = User.objects.create_user(username='incomplete@example.com', password='test-pass')
        incomplete = Student.objects.create(
            student_id='MAT-INCOMPLETE', candidate=user, academic_session=self.session,
            course=self.programme, current_level='', admitted_by=self.staff,
        )
        result = self.search('mat-incomplete').json()['results'][0]
        self.assertEqual(result['id'], incomplete.pk)
        self.assertEqual(result['name'], 'Name unavailable')
        self.assertEqual(result['application_number'], '')
        self.assertEqual(result['level'], '')

    def test_similar_names_are_unique_and_include_distinguishing_information(self):
        results = self.search('Amina').json()['results']
        self.assertEqual(len(results), 2)
        self.assertEqual(len({item['id'] for item in results}), 2)
        self.assertEqual(
            {item['application_number'] for item in results}, {'APP-26001', 'APP-26002'}
        )
        self.assertTrue(all('email' in item and 'programme' in item for item in results))

    def test_empty_and_no_result_queries_return_clear_empty_responses(self):
        empty = self.search('   ')
        missing = self.search('not-a-real-student')
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()['results'], [])
        self.assertIn('Enter', empty.json()['message'])
        self.assertEqual(missing.json()['results'], [])
        self.assertIn('No students', missing.json()['message'])

    def test_ajax_selection_returns_selected_student_without_modifying_it(self):
        before = (self.primary.student_id, self.primary.current_level, self.primary.course_id)
        response = self.client.post(
            reverse('student_search_selection'), {'student_id': self.primary.pk},
            **self.ajax_headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['student']['id'], self.primary.pk)
        self.primary.refresh_from_db()
        self.assertEqual(
            (self.primary.student_id, self.primary.current_level, self.primary.course_id), before
        )

    def test_invalid_non_ajax_and_invalid_selection_requests_are_rejected(self):
        self.assertEqual(self.client.get(reverse('student_search_results'), {'q': 'Amina'}).status_code, 400)
        self.assertEqual(
            self.client.post(
                reverse('student_search_selection'), {'student_id': 'invalid'}, **self.ajax_headers
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                reverse('student_search_selection'), {'student_id': '999999'}, **self.ajax_headers
            ).status_code,
            404,
        )

    def test_existing_exact_student_search_still_operates(self):
        response = self.client.get(reverse('search_student'), {'student_id': 'MAT-26001'})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'academics/student_detail.html')
        self.assertEqual(response.context['student'], self.primary)


class StudentAcademicCorrectionTests(TestCase):
    administrative_roles = StudentSearchTests.administrative_roles

    def setUp(self):
        self.admin = User.objects.create_user(username='corrections@example.com', password='pass')
        self.admin.profile.role = 'ICT DIRECTOR'
        self.admin.profile.save(update_fields=['role'])
        self.student_user = User.objects.create_user(
            username='student-correction@example.com', email='student-correction@example.com'
        )
        self.student_user.profile.role = 'Student'
        self.student_user.profile.first_name = 'Ada'
        self.student_user.profile.last_name = 'Okoro'
        self.student_user.profile.save(update_fields=['role', 'first_name', 'last_name'])

        faculty = Faculty.objects.create(name='Correction Faculty', added_by=self.admin)
        department = Department.objects.create(
            name='Correction Department', faculty=faculty, added_by=self.admin
        )
        self.programme = CourseOfStudy.objects.create(
            name='Correction Programme', department=department, added_by=self.admin
        )
        self.old_session = AcademicSession.objects.create(
            name='2024/2025', start_date=date(2024, 9, 1), end_date=date(2025, 8, 31),
            added_by=self.admin,
        )
        self.new_session = AcademicSession.objects.create(
            name='2025/2026', start_date=date(2025, 9, 1), end_date=date(2026, 8, 31),
            added_by=self.admin,
        )
        self.old_level = Level.objects.create(name='NDI', added_by=self.admin)
        self.new_level = Level.objects.create(name='NDII', added_by=self.admin)
        self.student = Student.objects.create(
            student_id='COR-001', candidate=self.student_user,
            academic_session=self.old_session, course=self.programme,
            current_level='NDI', admitted_by=self.admin,
        )
        self.pre_applicant = PreApplicant.objects.create(
            applicant=self.student_user, first_name='Ada', last_name='Okoro',
            course_name=self.programme, academic_session=self.old_session,
            application_number='APP-COR-001',
        )
        self.application = ApplicationSubmission.objects.create(
            applicant=self.student_user, confirm=True, academic_sess=self.old_session,
            course_name=self.programme, is_admitted=True,
        )
        self.admitted = Admitted.objects.create(
            applicant=self.student_user, academic_session=self.old_session,
            admitted_course=self.programme, admitted_level='NDI',
        )
        self.jamb = Jamb.objects.create(
            applicant=self.student_user, exam_name='JAMB', exam_year=2024,
            exam_number='JAMB-COR-1', academic_session=self.old_session,
            admitted_course=self.programme, admitted_level='NDI',
        )

        semester = Semester.objects.create(
            name='First Semester', academic_session=self.old_session, is_active=True,
            added_by=self.admin,
        )
        course_unit = CourseUnit.objects.create(
            academic_session=self.old_session, course_title='Historical Course',
            course_code='HIS 101', credit_load=3, course_of_study=self.programme,
            semester=semester, level=self.old_level, added_by=self.admin,
        )
        self.enrollment = CourseEnrollment.objects.create(
            student=self.student_user, course_unit=course_unit, semester=semester,
            academic_session=self.old_session, programme=self.programme,
            level=self.old_level, added_by=self.admin,
        )
        self.payment = Payment.objects.create(
            user=self.student_user, amount='1000.00', status='Approved',
            payment_source='School Fees', pay_ref='historical-correction-payment',
            verified=True, email=self.student_user.email,
            academic_sess=self.old_session, added_by=self.admin,
        )
        self.fee_payment = StudentFeePayment.objects.create(
            student=self.student_user, course_of_study=self.programme,
            academic_session=self.old_session, student_level=self.old_level,
            payment_type='First Semester', is_indigene=True,
            has_paid_first_semester=True,
        )
        self.url = reverse('student_academic_correction', args=[self.student.pk])
        self.result_url = reverse('student_academic_correction_result', args=[self.student.pk])
        self.client.force_login(self.admin)

    def submit(self, level=None, session=None, confirm=True):
        data = {
            'level': str((level or self.old_level).pk),
            'admitted_session': str((session or self.old_session).pk),
        }
        if confirm:
            data['confirm'] = 'on'
        return self.client.post(self.url, data)

    def refresh_authoritative_records(self):
        for record in (
            self.student, self.pre_applicant, self.application, self.admitted, self.jamb
        ):
            record.refresh_from_db()

    def test_search_result_exposes_correction_action_and_form_shows_current_values(self):
        search = self.client.get(
            reverse('student_search_results'), {'q': 'COR-001'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(search.json()['results'][0]['correction_url'], self.url)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Change Level / Admitted Session')
        self.assertContains(response, 'NDI')
        self.assertContains(response, '2024/2025')
        self.assertContains(response, 'Review changes')
        self.assertContains(response, 'Confirm academic record change')
        self.assertContains(response, 'id="confirm-correction"')

    def test_level_only_change_updates_student_and_creates_audit(self):
        response = self.submit(level=self.new_level)
        self.assertRedirects(response, self.result_url, fetch_redirect_response=False)
        self.refresh_authoritative_records()
        self.assertEqual(self.student.current_level, 'NDII')
        self.assertEqual(self.admitted.admitted_level, 'NDI')
        self.assertEqual(self.jamb.admitted_level, 'NDI')
        history = StudentAcademicCorrectionHistory.objects.get()
        self.assertEqual(history.previous_level, 'NDI')
        self.assertEqual(history.new_level, 'NDII')
        self.assertEqual(history.previous_admitted_session, self.old_session)
        self.assertEqual(history.new_admitted_session, self.old_session)
        self.assertEqual(history.changed_by, self.admin)

    def test_admitted_session_only_change_synchronizes_authoritative_records(self):
        response = self.submit(session=self.new_session)
        self.assertRedirects(response, self.result_url, fetch_redirect_response=False)
        self.refresh_authoritative_records()
        self.assertEqual(self.student.current_level, 'NDI')
        self.assertEqual(self.student.academic_session, self.new_session)
        self.assertEqual(self.admitted.academic_session, self.new_session)
        self.assertEqual(self.application.academic_sess, self.new_session)
        self.assertEqual(self.pre_applicant.academic_session, self.new_session)
        self.assertEqual(self.jamb.academic_session, self.new_session)

    def test_level_and_session_can_change_together(self):
        response = self.submit(level=self.new_level, session=self.new_session)
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        history = StudentAcademicCorrectionHistory.objects.get()
        self.assertEqual(self.student.current_level, 'NDII')
        self.assertEqual(self.student.academic_session, self.new_session)
        self.assertEqual(history.previous_level, 'NDI')
        self.assertEqual(history.new_level, 'NDII')
        self.assertEqual(history.previous_admitted_session, self.old_session)
        self.assertEqual(history.new_admitted_session, self.new_session)

    def test_confirm_redirects_to_read_only_result_and_refresh_does_not_resubmit(self):
        response = self.submit(level=self.new_level, session=self.new_session)
        self.assertRedirects(response, self.result_url, fetch_redirect_response=False)
        self.assertEqual(StudentAcademicCorrectionHistory.objects.count(), 1)

        result = self.client.get(self.result_url)
        self.assertEqual(result.status_code, 200)
        self.assertTemplateUsed(result, 'academics/student_academic_correction_result.html')
        self.assertContains(result, 'Ada')
        self.assertContains(result, 'COR-001')
        self.assertContains(result, 'NDI &rarr; NDII', html=False)
        self.assertContains(result, '2024/2025')
        self.assertContains(result, '2025/2026')
        self.assertContains(result, 'Change Another Student')
        self.assertNotContains(result, '<form')

        refreshed = self.client.get(self.result_url)
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(StudentAcademicCorrectionHistory.objects.count(), 1)

    def test_cancelled_modal_payload_cannot_update_without_server_confirmation(self):
        response = self.client.post(self.url, {
            'level': self.new_level.pk,
            'admitted_session': self.new_session.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.assertEqual(self.student.current_level, 'NDI')
        self.assertEqual(self.student.academic_session, self.old_session)
        self.assertFalse(StudentAcademicCorrectionHistory.objects.exists())

    def test_result_view_preserves_existing_authorization(self):
        unauthorized = User.objects.create_user(username='result-view-staff', password='pass')
        unauthorized.profile.role = 'Staff'
        unauthorized.profile.save(update_fields=['role'])
        self.client.force_login(unauthorized)
        self.assertRedirects(
            self.client.get(self.result_url),
            reverse('accounts_login'),
            fetch_redirect_response=False,
        )

    def test_invalid_level_session_and_missing_confirmation_are_rejected(self):
        invalid_payloads = (
            {'level': '999999', 'admitted_session': self.old_session.pk, 'confirm': 'on'},
            {'level': self.old_level.pk, 'admitted_session': '999999', 'confirm': 'on'},
            {'level': self.new_level.pk, 'admitted_session': self.old_session.pk},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post(self.url, payload)
                self.assertEqual(response.status_code, 200)
                self.student.refresh_from_db()
                self.assertEqual(self.student.current_level, 'NDI')
                self.assertEqual(self.student.academic_session, self.old_session)
                self.assertFalse(StudentAcademicCorrectionHistory.objects.exists())

    def test_unauthorized_user_cannot_open_or_submit_correction(self):
        unauthorized = User.objects.create_user(username='ordinary-staff', password='pass')
        unauthorized.profile.role = 'Staff'
        unauthorized.profile.save(update_fields=['role'])
        self.client.force_login(unauthorized)
        self.assertRedirects(
            self.client.get(self.url), reverse('accounts_login'), fetch_redirect_response=False
        )
        self.assertRedirects(
            self.client.post(self.url, {
                'level': self.new_level.pk,
                'admitted_session': self.new_session.pk,
                'confirm': 'on',
            }),
            reverse('accounts_login'), fetch_redirect_response=False,
        )
        self.student.refresh_from_db()
        self.assertEqual(self.student.current_level, 'NDI')
        self.assertFalse(StudentAcademicCorrectionHistory.objects.exists())

    def test_every_student_search_admin_role_can_open_correction(self):
        for role in self.administrative_roles:
            with self.subTest(role=role):
                self.admin.profile.role = role
                self.admin.profile.save(update_fields=['role'])
                self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_historical_registrations_and_payments_remain_unchanged(self):
        self.submit(level=self.new_level, session=self.new_session)
        self.enrollment.refresh_from_db()
        self.payment.refresh_from_db()
        self.fee_payment.refresh_from_db()
        self.assertEqual(self.enrollment.academic_session, self.old_session)
        self.assertEqual(self.enrollment.level, self.old_level)
        self.assertEqual(self.payment.academic_sess, self.old_session)
        self.assertEqual(self.fee_payment.academic_session, self.old_session)
        self.assertEqual(self.fee_payment.student_level, self.old_level)

    def test_history_records_cannot_be_rewritten_or_deleted(self):
        self.submit(level=self.new_level)
        history = StudentAcademicCorrectionHistory.objects.get()
        history.new_level = 'HNDI'
        with self.assertRaises(ValidationError):
            history.save()
        with self.assertRaises(ValidationError):
            history.delete()
        with self.assertRaises(ValidationError):
            StudentAcademicCorrectionHistory.objects.filter(pk=history.pk).update(
                new_level='HNDI'
            )
        with self.assertRaises(ValidationError):
            StudentAcademicCorrectionHistory.objects.filter(pk=history.pk).delete()


class StudentCourseRegistrationSessionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='student@example.com', password='test-pass')
        self.user.profile.role = 'Student'
        self.user.profile.save(update_fields=['role'])

        self.ndi = Level.objects.create(name='NDI')
        self.ndii = Level.objects.create(name='NDII')
        faculty = Faculty.objects.create(name='Health Sciences')
        department = Department.objects.create(name='Community Health', faculty=faculty)
        self.programme = CourseOfStudy.objects.create(name='Community Health', department=department)

        self.session_2023 = AcademicSession.objects.create(
            name='2023/2024',
            is_active=True,
            start_date=date(2023, 9, 1),
            end_date=date(2024, 8, 31),
        )
        self.session_2024 = AcademicSession.objects.create(
            name='2024/2025',
            is_active=True,
            start_date=date(2024, 9, 1),
            end_date=date(2025, 8, 31),
        )
        self.first_2023 = Semester.objects.create(
            name='First Semester', academic_session=self.session_2023, is_active=False
        )
        self.first_2024 = Semester.objects.create(
            name='First Semester', academic_session=self.session_2024, is_active=True
        )
        self.second_2024 = Semester.objects.create(
            name='Second Semester', academic_session=self.session_2024, is_active=False
        )
        self.student = Student.objects.create(
            student_id='STU001',
            candidate=self.user,
            academic_session=self.session_2023,
            course=self.programme,
            semester='First Semester',
            current_level='NDII',
            admitted_by=self.user,
        )
        self.ndii_first = self.create_course(
            self.session_2024, self.first_2024, self.ndii, 'CHT 201'
        )
        self.ndi_first = self.create_course(
            self.session_2023, self.first_2023, self.ndi, 'CHT 101'
        )
        self.ndi_current_first = self.create_course(
            self.session_2024, self.first_2024, self.ndi, 'CHT 102'
        )
        self.create_course(self.session_2024, self.second_2024, self.ndii, 'CHT 202')
        self.client.force_login(self.user)

    def create_course(self, session, semester, level, code):
        return CourseUnit.objects.create(
            academic_session=session,
            semester=semester,
            level=level,
            course_of_study=self.programme,
            course_code=code,
            course_title=f'{code} Course',
            credit_load=3,
        )

    def pay_first_semester(self, session=None, level=None):
        return StudentFeePayment.objects.create(
            student=self.user,
            course_of_study=self.programme,
            academic_session=session or self.session_2024,
            student_level=level or self.ndii,
            payment_type='First Semester',
            is_indigene=True,
            has_paid_first_semester=True,
        )

    def test_confirmation_uses_progressed_session_with_concurrent_active_sessions(self):
        response = self.client.get(reverse('confirm_student_carryover'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['current_semester'], self.first_2024)
        self.assertEqual(response.context['student_current_level_name'], 'NDII')

    def test_confirmation_without_active_semester_redirects_without_no_reverse_match(self):
        self.first_2024.is_active = False
        self.first_2024.save(update_fields=['is_active'])

        response = self.client.get(reverse('confirm_student_carryover'))

        self.assertRedirects(
            response, reverse('accounts_dashboard'), fetch_redirect_response=False
        )

    def test_paid_ndii_student_gets_only_progressed_session_first_semester_courses(self):
        self.pay_first_semester()

        response = self.client.get(reverse('semester_course_registration'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['latest_academic_session'], self.session_2024)
        self.assertEqual(response.context['current_semester'], self.first_2024)
        self.assertQuerySetEqual(
            response.context['student_course_units'], [self.ndii_first], transform=lambda item: item
        )

    def test_course_registration_back_page_accepts_approved_payment_for_applicable_session(self):
        Payment.objects.create(
            user=self.user,
            amount='1000.00',
            status='Approved',
            payment_source='School Fees',
            pay_ref='approved-back-page-payment',
            verified=True,
            email=self.user.username,
            academic_sess=self.session_2024,
            indigene_type=get_user_indigene_type(self.user),
            added_by=self.user,
        )

        response = self.client.get(reverse('course_reg_back_page'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_paid_current_session'], 1000)
        self.assertNotContains(response, 'Has Not Paid')

    def test_course_registration_back_page_rejects_incomplete_payment(self):
        Payment.objects.create(
            user=self.user,
            amount='1000.00',
            status='Pending',
            payment_source='School Fees',
            pay_ref='pending-back-page-payment',
            verified=False,
            email=self.user.username,
            academic_sess=self.session_2024,
            indigene_type=get_user_indigene_type(self.user),
            added_by=self.user,
        )

        response = self.client.get(reverse('course_reg_back_page'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_paid_current_session'], 0)
        self.assertContains(response, 'Has Not Paid')

    def test_course_registration_back_page_supports_every_defined_student_level(self):
        for level_name in ('HNDI', 'HNDII', 'CERTIFICATE'):
            Level.objects.create(name=level_name)
        for session, reference in (
            (self.session_2023, 'back-page-levels-2023'),
            (self.session_2024, 'back-page-levels-2024'),
        ):
            Payment.objects.create(
                user=self.user,
                amount='1000.00',
                status='Approved',
                payment_source='School Fees',
                pay_ref=reference,
                verified=True,
                email=self.user.username,
                academic_sess=session,
                indigene_type=get_user_indigene_type(self.user),
                added_by=self.user,
            )

        for level_name in ('NDI', 'NDII', 'HNDI', 'HNDII', 'CERTIFICATE'):
            with self.subTest(level=level_name):
                self.student.current_level = level_name
                self.student.save(update_fields=['current_level'])

                response = self.client.get(reverse('course_reg_back_page'))

                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'No Level Set for Course Template')
                self.assertContains(response, 'A. BURSARY DEPARTMENT')

    def test_paid_ndii_student_registers_course_in_progressed_session(self):
        self.pay_first_semester()

        response = self.client.post(
            reverse('register_course_units'),
            {'selected_course_units[]': [str(self.ndii_first.pk)]},
            follow=True,
        )

        self.assertRedirects(response, reverse('student_semester_registered_courses'))
        self.assertEqual(response.context['latest_academic_session'], self.session_2024)
        self.assertEqual(response.context['current_semester'], self.first_2024)
        self.assertQuerySetEqual(
            response.context['student_reg_courses'],
            [CourseEnrollment.objects.get(student=self.user)],
            transform=lambda item: item,
        )
        response_messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(response_messages), 1)
        self.assertEqual(response_messages[0].level_tag, 'success')
        self.assertIn('2024/2025 Academic Session', str(response_messages[0]))
        enrollment = CourseEnrollment.objects.get(student=self.user)
        self.assertEqual(enrollment.academic_session, self.session_2024)
        self.assertEqual(enrollment.semester, self.first_2024)
        self.assertEqual(enrollment.level, self.ndii)
        self.assertEqual(enrollment.course_unit, self.ndii_first)

    def test_registration_error_rolls_back_all_selected_courses(self):
        self.pay_first_semester()

        response = self.client.post(
            reverse('register_course_units'),
            {'selected_course_units[]': [str(self.ndii_first.pk), '999999']},
            follow=True,
        )

        self.assertEqual(CourseEnrollment.objects.count(), 0)
        response_messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(response_messages), 1)
        self.assertEqual(response_messages[0].level, constants.ERROR)

    def test_repeated_registration_does_not_create_duplicate_and_redirects_to_registered_courses(self):
        CourseEnrollment.objects.create(
            student=self.user,
            course_unit=self.ndii_first,
            semester=self.first_2024,
            academic_session=self.session_2024,
            programme=self.programme,
            level=self.ndii,
            added_by=self.user,
        )

        response = self.client.post(
            reverse('register_course_units'),
            {'selected_course_units[]': [str(self.ndii_first.pk)]},
        )

        self.assertRedirects(
            response,
            reverse('student_semester_registered_courses'),
            fetch_redirect_response=False,
        )
        self.assertEqual(CourseEnrollment.objects.count(), 1)

    def test_payment_from_admission_session_does_not_unlock_progressed_session(self):
        self.pay_first_semester(session=self.session_2023, level=self.ndi)

        response = self.client.get(reverse('semester_course_registration'))

        self.assertRedirects(
            response, reverse('accounts_dashboard'), fetch_redirect_response=False
        )

    def test_ndi_registration_stays_in_its_linked_session(self):
        self.student.current_level = 'NDI'
        self.student.save(update_fields=['current_level'])
        self.first_2023.is_active = True
        self.first_2023.save(update_fields=['is_active'])
        self.pay_first_semester(session=self.session_2023, level=self.ndi)

        response = self.client.get(reverse('semester_course_registration'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['latest_academic_session'], self.session_2023)
        self.assertEqual(list(response.context['student_course_units'].values_list('course_code', flat=True)), ['CHT 101'])

    def create_previous_level_enrollment(self):
        return CourseEnrollment.objects.create(
            student=self.user,
            course_unit=self.ndi_first,
            semester=self.first_2023,
            academic_session=self.session_2023,
            programme=self.programme,
            level=self.ndi,
            added_by=self.user,
        )

    def test_carryover_display_without_active_progressed_semester_has_no_no_reverse_match(self):
        self.first_2024.is_active = False
        self.first_2024.save(update_fields=['is_active'])

        response = self.client.get(reverse('student_carryover_courses'))

        self.assertRedirects(
            response, reverse('accounts_dashboard'), fetch_redirect_response=False
        )

    @patch('payments.views.verify_and_finalize_payment')
    def test_completed_school_fees_does_not_queue_failed_verification_warning(
        self, verify_and_finalize_payment
    ):
        verify_and_finalize_payment.return_value = SimpleNamespace(
            success=False, payment=None, message='Provider verification unavailable'
        )
        Payment.objects.create(
            user=self.user,
            amount='1000.00',
            status='Pending',
            payment_source='School Fees',
            pay_ref='stale-school-fee-reference',
            verified=False,
            email=self.user.username,
            academic_sess=self.session_2024,
            added_by=self.user,
        )
        Payment.objects.create(
            user=self.user,
            amount='1000.00',
            status='Approved',
            payment_source='School Fees',
            pay_ref='completed-school-fee-reference',
            verified=True,
            email=self.user.username,
            academic_sess=self.session_2024,
            added_by=self.user,
        )

        response = self.client.get(
            reverse('verify_payment', args=['stale-school-fee-reference'])
        )

        self.assertRedirects(response, reverse('initiate_payment'), fetch_redirect_response=False)
        self.assertEqual(list(get_messages(response.wsgi_request)), [])

    @patch('payments.views.verify_and_finalize_payment')
    def test_failed_school_fee_verification_still_queues_warning(
        self, verify_and_finalize_payment
    ):
        verify_and_finalize_payment.return_value = SimpleNamespace(
            success=False, payment=None, message='Provider verification failed'
        )
        Payment.objects.create(
            user=self.user,
            amount='1000.00',
            status='Pending',
            payment_source='School Fees',
            pay_ref='failed-school-fee-reference',
            verified=False,
            email=self.user.username,
            academic_sess=self.session_2024,
            added_by=self.user,
        )

        response = self.client.get(
            reverse('verify_payment', args=['failed-school-fee-reference'])
        )

        response_messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(response_messages), 1)
        self.assertEqual(response_messages[0].level, constants.WARNING)
        self.assertEqual(str(response_messages[0]), 'Payment Verification Failed')

    def test_carryover_display_uses_previous_level_course_in_current_session_and_semester(self):
        response = self.client.get(reverse('student_carryover_courses'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['latest_academic_session'], self.session_2024)
        self.assertEqual(response.context['current_semester'], self.first_2024)
        self.assertQuerySetEqual(
            response.context['previous_level_courses'],
            [self.ndi_current_first],
            transform=lambda item: item,
        )

    def test_student_can_register_previous_level_course_from_current_session_and_semester(self):
        response = self.client.post(
            reverse('register_carryover_courses'),
            {'selected_carryover_courses[]': [str(self.ndi_current_first.pk)]},
        )

        self.assertRedirects(
            response, reverse('semester_course_registration'), fetch_redirect_response=False
        )
        carryover = CourseCarryover.objects.get(student=self.user)
        self.assertEqual(carryover.course_unit, self.ndi_current_first)
        self.assertEqual(carryover.academic_session, self.session_2024)
        self.assertEqual(carryover.semester, self.first_2024)
        self.assertEqual(carryover.level, 'NDI')

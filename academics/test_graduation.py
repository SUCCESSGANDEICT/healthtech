from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from admission.models import Admitted, ApplicationSubmission, PreApplicant
from accounts.models import GRADUATE_ROLE, STUDENT_ROLE
from fees.models import Fee, StudentFeePayment
from payments.models import Payment

from .models import (
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
from .services import determine_graduation_session


class StudentGraduationTests(TestCase):
    ajax = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}

    def setUp(self):
        self.admin = User.objects.create_user(username='graduation-admin', password='pass')
        self.admin.profile.role = 'Admin'
        self.admin.profile.save(update_fields=['role'])
        self.faculty = Faculty.objects.create(name='Health Sciences', added_by=self.admin)
        self.department = Department.objects.create(
            name='Community Health', faculty=self.faculty, added_by=self.admin
        )
        self.course = CourseOfStudy.objects.create(
            name='Community Health', department=self.department, added_by=self.admin
        )
        self.other_course = CourseOfStudy.objects.create(
            name='Health Information', department=self.department, added_by=self.admin
        )
        self.ndi = Level.objects.create(name='NDI', added_by=self.admin)
        self.ndii = Level.objects.create(name='NDII', added_by=self.admin)
        self.session_2023 = self.make_session(2023)
        self.session_2024 = self.make_session(2024, active=True)
        self.session_2025 = self.make_session(2025, active=True)
        self.semesters = {}
        self.units = {}
        for session in (self.session_2023, self.session_2024, self.session_2025):
            semester = Semester.objects.create(
                name='Second Semester', academic_session=session, added_by=self.admin
            )
            self.semesters[session.pk] = semester
            self.units[session.pk] = CourseUnit.objects.create(
                academic_session=session,
                course_title=f'Professional Practice {session.name}',
                course_code=f'CHT{session.start_date.year}',
                credit_load=3,
                course_of_study=self.course,
                semester=semester,
                level=self.ndii,
                added_by=self.admin,
            )
        self.client.force_login(self.admin)

    def make_session(self, year, active=False):
        return AcademicSession.objects.create(
            name=f'{year}/{year + 1}',
            start_date=date(year, 9, 1),
            end_date=date(year + 1, 8, 31),
            resumption_date=date(year, 9, 1),
            closing_date=date(year + 1, 8, 31),
            admission_date=date(year, 8, 1),
            is_active=active,
            added_by=self.admin,
        )

    def make_student(
        self, suffix, *, course=None, level='NDII', admitted_session=None,
        current_session=None, admitted_level='NDI', first_name='Ada',
    ):
        course = course or self.course
        admitted_session = admitted_session or self.session_2023
        current_session = current_session or self.session_2024
        user = User.objects.create_user(
            username=f'student-{suffix}@example.com',
            email=f'student-{suffix}@example.com',
            password='pass',
        )
        user.profile.first_name = first_name
        user.profile.middle_name = 'Test'
        user.profile.last_name = f'Student{suffix}'
        user.profile.role = 'Student'
        user.profile.save(update_fields=['first_name', 'middle_name', 'last_name', 'role'])
        PreApplicant.objects.create(
            applicant=user,
            first_name=first_name,
            middle_name='Test',
            last_name=f'Student{suffix}',
            course_name=course,
            academic_session=admitted_session,
            application_number=f'APP-{suffix}',
        )
        Admitted.objects.create(
            applicant=user,
            academic_session=admitted_session,
            admitted_course=course,
            admitted_level=admitted_level,
        )
        student = Student.objects.create(
            student_id=f'MAT-{suffix}',
            candidate=user,
            academic_session=current_session,
            course=course,
            current_level=level,
            admitted_by=self.admin,
        )
        self.mark_school_fees_paid(user, admitted_session, admitted_level, course)
        self.mark_school_fees_paid(user, current_session, level, course)
        return student

    def mark_school_fees_paid(self, user, session, level_name, course=None):
        level = Level.objects.get(name=level_name)
        payment, _created = StudentFeePayment.objects.get_or_create(
            student=user,
            academic_session=session,
            student_level=level,
            course_of_study=course or self.course,
            is_indigene=False,
            defaults={
                'payment_type': 'Full Fee Payment',
                'has_paid_first_semester': True,
                'has_paid_second_semester': True,
                'has_paid_full_session': True,
            },
        )
        if not payment.has_paid_full_session:
            payment.has_paid_first_semester = True
            payment.has_paid_second_semester = True
            payment.has_paid_full_session = True
            payment.save(update_fields=[
                'has_paid_first_semester', 'has_paid_second_semester',
                'has_paid_full_session',
            ])
        return payment

    def add_final_level_enrollment(self, student, session):
        enrollment = CourseEnrollment.objects.create(
            student=student.candidate,
            course_unit=self.units[session.pk],
            semester=self.semesters[session.pk],
            academic_session=session,
            programme=self.course,
            level=self.ndii,
            added_by=self.admin,
        )
        self.mark_school_fees_paid(student.candidate, session, self.ndii.name)
        return enrollment

    def filter_data(self, *, session=None, course=None, level=None, search=''):
        return {
            'course': (course or self.course).pk,
            'level': (level or self.ndii).pk,
            'session': (session or self.session_2024).pk,
            'search': search,
        }

    def graduation_post(self, students, **context):
        data = self.filter_data(**context)
        data['student_ids'] = [str(student.pk) for student in students]
        return self.client.post(reverse('graduate_student_selection'), data, **self.ajax)

    def test_only_existing_graduation_admin_roles_and_superuser_have_access(self):
        for role in ('ADMISSION-OFFICER', 'Super Admin', 'Developer', 'Admin'):
            with self.subTest(role=role):
                self.admin.profile.role = role
                self.admin.profile.save(update_fields=['role'])
                response = self.client.get(reverse('student_graduation'))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, reverse('student_graduation'))

        self.admin.profile.role = 'Student'
        self.admin.profile.save(update_fields=['role'])
        self.assertEqual(self.client.get(reverse('student_graduation')).status_code, 403)
        self.assertEqual(
            self.client.get(reverse('student_graduation_results'), self.filter_data(), **self.ajax).status_code,
            403,
        )
        self.admin.is_superuser = True
        self.admin.save(update_fields=['is_superuser'])
        self.assertEqual(self.client.get(reverse('student_graduation')).status_code, 200)

    def test_normal_and_repeated_progression_use_actual_final_level_session(self):
        normal = self.make_student('NORMAL')
        self.add_final_level_enrollment(normal, self.session_2024)
        repeated = self.make_student('REPEAT', current_session=self.session_2025)
        self.add_final_level_enrollment(repeated, self.session_2024)
        self.add_final_level_enrollment(repeated, self.session_2025)

        self.assertEqual(determine_graduation_session(normal), self.session_2024)
        self.assertEqual(determine_graduation_session(repeated), self.session_2025)

    def test_admitted_session_progression_is_only_a_safe_fallback(self):
        legacy = self.make_student(
            'LEGACY', current_session=self.session_2023, admitted_session=self.session_2023
        )
        direct_entry = self.make_student(
            'DIRECT', current_session=self.session_2023,
            admitted_session=self.session_2023, admitted_level='NDII',
        )

        self.assertEqual(determine_graduation_session(legacy), self.session_2024)
        self.assertEqual(determine_graduation_session(direct_entry), self.session_2023)

    def test_ajax_filter_search_and_pagination_are_scoped(self):
        target = self.make_student('TARGET', first_name='UniqueAda')
        self.add_final_level_enrollment(target, self.session_2024)
        self.make_student('OTHER-COURSE', course=self.other_course)
        for number in range(21):
            self.make_student(f'PAGE-{number:02d}', admitted_level='NDII')

        response = self.client.get(
            reverse('student_graduation_results'),
            self.filter_data(search='UniqueAda'),
            **self.ajax,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        self.assertIn('MAT-TARGET', response.json()['html'])
        self.assertNotIn('MAT-OTHER-COURSE', response.json()['html'])

        full_name_response = self.client.get(
            reverse('student_graduation_results'),
            self.filter_data(search='UniqueAda Test StudentTARGET'),
            **self.ajax,
        )
        self.assertEqual(full_name_response.json()['count'], 1)

        response = self.client.get(
            reverse('student_graduation_results'), self.filter_data(), **self.ajax
        )
        self.assertEqual(response.json()['count'], 22)
        self.assertEqual(response.json()['pages'], 2)
        self.assertEqual(response.json()['html'].count('class="student-select"'), 20)
        self.assertIn('id="select-current-page"', response.json()['html'])
        self.assertContains(self.client.get(reverse('student_graduation')), 'student_graduation.js')

    def test_individual_bulk_and_duplicate_protection_with_audit_fields(self):
        first = self.make_student('ONE')
        second = self.make_student('TWO')
        self.add_final_level_enrollment(first, self.session_2024)
        self.add_final_level_enrollment(second, self.session_2024)

        individual = self.graduation_post([first])
        self.assertEqual(individual.status_code, 200)
        self.assertEqual(individual.json()['graduated_count'], 1)
        first.refresh_from_db()
        self.assertTrue(first.graduated)
        self.assertEqual(first.graduation_session, self.session_2024)
        self.assertEqual(first.graduated_by, self.admin)
        self.assertIsNotNone(first.graduated_at)
        self.assertEqual(first.candidate.profile.role, GRADUATE_ROLE)

        mixed = self.graduation_post([first, second])
        self.assertEqual(mixed.status_code, 200)
        self.assertEqual(mixed.json()['graduated_count'], 1)
        self.assertEqual(mixed.json()['skipped_count'], 1)
        duplicate = self.graduation_post([first])
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()['graduated_count'], 0)

    def test_outstanding_fees_block_individual_and_are_reported(self):
        student = self.make_student('UNPAID')
        StudentFeePayment.objects.filter(
            student=student.candidate,
            academic_session=self.session_2024,
            student_level=self.ndii,
        ).delete()

        response = self.graduation_post([student])

        self.assertEqual(response.status_code, 409)
        self.assertIn('outstanding school fee balance', response.json()['skipped'][0]['reason'])
        student.refresh_from_db()
        student.candidate.profile.refresh_from_db()
        self.assertFalse(student.graduated)
        self.assertEqual(student.candidate.profile.role, STUDENT_ROLE)

    def test_bulk_graduates_paid_student_and_skips_unpaid_student(self):
        paid = self.make_student('BULK-PAID')
        unpaid = self.make_student('BULK-UNPAID')
        StudentFeePayment.objects.filter(
            student=unpaid.candidate,
            academic_session=self.session_2024,
            student_level=self.ndii,
        ).delete()

        response = self.graduation_post([paid, unpaid])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['graduated_count'], 1)
        self.assertEqual(response.json()['skipped_count'], 1)
        paid.refresh_from_db()
        unpaid.refresh_from_db()
        self.assertTrue(paid.graduated)
        self.assertFalse(unpaid.graduated)

    def test_existing_graduate_action_idempotently_synchronizes_profile(self):
        student = self.make_student('EXISTING')
        student.graduated = True
        student.save(update_fields=['graduated'])
        self.assertEqual(student.candidate.profile.role, STUDENT_ROLE)

        response = self.graduation_post([student])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['graduated_count'], 0)
        self.assertEqual(response.json()['synchronized_count'], 1)
        student.candidate.profile.refresh_from_db()
        self.assertEqual(student.candidate.profile.role, GRADUATE_ROLE)
        self.assertIsNone(student.graduated_at)

    def test_graduate_login_and_dashboard_sync_legacy_state_and_are_read_only(self):
        student = self.make_student('LEGACY-GRAD')
        student.graduation_session = self.session_2024
        student.save(update_fields=['graduation_session'])
        self.client.force_login(student.candidate)

        landing = self.client.get(reverse('accounts_dashboard'))
        self.assertRedirects(
            landing, reverse('student_dashboard'), fetch_redirect_response=False
        )
        student.refresh_from_db()
        student.candidate.profile.refresh_from_db()
        self.assertTrue(student.graduated)
        self.assertEqual(student.candidate.profile.role, GRADUATE_ROLE)

        dashboard = self.client.get(reverse('student_dashboard'))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, 'Graduate Dashboard')
        self.assertContains(dashboard, 'Academic status:</strong> Graduate')
        self.assertContains(dashboard, 'Course Registration')
        self.assertContains(dashboard, 'Print Course(s)')
        self.assertContains(dashboard, 'Application Form')
        self.assertContains(dashboard, 'Payment Receipts')
        self.assertNotContains(dashboard, 'Register Course(s)')
        self.assertNotContains(dashboard, 'Pay Now')
        self.assertRedirects(
            self.client.get(reverse('semester_course_registration')),
            reverse('accounts_login'),
            fetch_redirect_response=False,
        )

    def test_graduate_historical_courses_are_selectable_and_mutations_stay_blocked(self):
        student = self.make_student('HISTORY')
        ApplicationSubmission.objects.create(
            applicant=student.candidate,
            confirm=True,
            academic_sess=self.session_2023,
            course_name=self.course,
            is_admitted=True,
        )
        first = self.add_final_level_enrollment(student, self.session_2024)
        second = self.add_final_level_enrollment(student, self.session_2025)
        student.graduated = True
        student.graduation_session = self.session_2025
        student.save(update_fields=['graduated', 'graduation_session'])
        student.candidate.profile.role = GRADUATE_ROLE
        student.candidate.profile.save(update_fields=['role'])
        self.client.force_login(student.candidate)

        response = self.client.get(reverse('student_semester_registered_courses'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Historical course registration')
        self.assertContains(response, second.course_unit.course_code)
        earlier = self.client.get(reverse('student_semester_registered_courses'), {
            'session': self.session_2024.pk,
            'semester': first.semester_id,
            'level': first.level_id,
        })
        self.assertEqual(earlier.status_code, 200)
        self.assertContains(earlier, first.course_unit.course_code)
        self.assertNotContains(earlier, second.course_unit.course_code)
        back_page = self.client.get(reverse('course_reg_back_page'), {
            'session': self.session_2024.pk,
            'semester': first.semester_id,
            'level': first.level_id,
        })
        self.assertEqual(back_page.status_code, 200)
        application = self.client.get(reverse('application_slip'))
        self.assertEqual(application.status_code, 200)
        self.assertContains(application, 'Application Slip')
        self.assertEqual(self.client.get(reverse('application_slip_back_page')).status_code, 200)
        self.assertRedirects(
            self.client.get(reverse('edit_application_form')),
            reverse('accounts_login'),
            fetch_redirect_response=False,
        )
        self.assertRedirects(
            self.client.get(reverse('confirm_student_carryover')),
            reverse('accounts_login'),
            fetch_redirect_response=False,
        )

    def test_graduate_can_review_outstanding_tuition_and_own_receipts(self):
        student = self.make_student('PAYMENTS')
        enrollment = self.add_final_level_enrollment(student, self.session_2024)
        student.graduated = True
        student.graduation_session = self.session_2024
        student.save(update_fields=['graduated', 'graduation_session'])
        student.candidate.profile.role = GRADUATE_ROLE
        student.candidate.profile.save(update_fields=['role'])
        StudentFeePayment.objects.filter(
            student=student.candidate,
            academic_session=self.session_2024,
            student_level=self.ndii,
        ).delete()
        for semester, amount in (
            ('First Semester', '50000.00'),
            ('Second Semester', '50000.00'),
            ('Full Session', '100000.00'),
        ):
            Fee.objects.create(
                academic_session=self.session_2024,
                course_of_study=self.course,
                name='School Fees',
                level='NDII',
                semester=semester,
                amount=amount,
                indigene_type='NON-INDIGENE',
                added_by=self.admin,
            )
        payment = Payment.objects.create(
            user=student.candidate,
            amount='50000.00',
            status='Approved',
            payment_source='School Fees',
            verified=True,
            email=student.candidate.email,
            fee_category='First Semester',
            indigene_type='NON-INDIGENE',
            academic_sess=self.session_2023,
            added_by=student.candidate,
        )
        other_user = User.objects.create_user(username='other-receipt', password='pass')
        other_user.profile.role = GRADUATE_ROLE
        other_user.profile.save(update_fields=['role'])
        self.client.force_login(student.candidate)

        tuition = self.client.get(reverse('select_fee_payment'), {
            'academic_session': self.session_2024.pk,
            'level': 'NDII',
        })
        self.assertEqual(tuition.status_code, 200)
        self.assertContains(tuition, 'Studenthood fee periods')
        self.assertContains(tuition, 'Outstanding')
        receipts = self.client.get(reverse('student_school_fees_receipts'))
        self.assertEqual(receipts.status_code, 200)
        self.assertContains(receipts, self.session_2023.name)
        printed = self.client.get(reverse('print_sch_fee_receipt', args=[payment.pk, self.course.pk]))
        self.assertEqual(printed.status_code, 200)
        self.client.force_login(other_user)
        self.assertEqual(
            self.client.get(reverse('print_sch_fee_receipt', args=[payment.pk, self.course.pk])).status_code,
            404,
        )

    def test_normal_student_login_and_dashboard_remain_active(self):
        student = self.make_student('ACTIVE')
        self.client.force_login(student.candidate)

        landing = self.client.get(reverse('accounts_dashboard'))
        self.assertRedirects(
            landing, reverse('student_dashboard'), fetch_redirect_response=False
        )
        student.refresh_from_db()
        student.candidate.profile.refresh_from_db()
        self.assertFalse(student.graduated)
        self.assertEqual(student.candidate.profile.role, STUDENT_ROLE)
        dashboard = self.client.get(reverse('student_dashboard'))
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotContains(dashboard, 'Academic status:</strong> Graduate')

    def test_tampered_ids_context_and_non_ajax_requests_are_rejected_or_skipped(self):
        valid = self.make_student('VALID')
        wrong_course = self.make_student('WRONG', course=self.other_course)
        response = self.graduation_post([valid, wrong_course])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['graduated_count'], 1)
        self.assertEqual(response.json()['skipped_count'], 1)
        wrong_course.refresh_from_db()
        self.assertFalse(wrong_course.graduated)

        data = self.filter_data()
        data['student_ids'] = ['99999999']
        self.assertEqual(
            self.client.post(reverse('graduate_student_selection'), data, **self.ajax).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(reverse('graduate_student_selection'), data).status_code,
            400,
        )

    def test_bulk_transaction_rolls_back_on_genuine_failure(self):
        first = self.make_student('ROLLBACK-1')
        second = self.make_student('ROLLBACK-2')
        self.add_final_level_enrollment(first, self.session_2024)
        self.add_final_level_enrollment(second, self.session_2024)
        original_save = Student.save

        def fail_second(instance, *args, **kwargs):
            if instance.pk == second.pk and kwargs.get('update_fields'):
                raise RuntimeError('simulated database failure')
            return original_save(instance, *args, **kwargs)

        with patch.object(Student, 'save', new=fail_second):
            response = self.graduation_post([first, second])

        self.assertEqual(response.status_code, 500)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.graduated)
        self.assertFalse(second.graduated)
        first.candidate.profile.refresh_from_db()
        second.candidate.profile.refresh_from_db()
        self.assertEqual(first.candidate.profile.role, STUDENT_ROLE)
        self.assertEqual(second.candidate.profile.role, STUDENT_ROLE)

    def test_client_has_current_page_selection_and_double_submit_guards(self):
        student = self.make_student('CLIENT')
        response = self.client.get(
            reverse('student_graduation_results'), self.filter_data(), **self.ajax
        )
        self.assertIn('Select all eligible students on this page', response.json()['html'])
        self.assertIn(f'value="{student.pk}"', response.json()['html'])
        with open('static/academics/student_graduation.js', encoding='utf-8') as script_file:
            script = script_file.read()
        self.assertIn("results.querySelectorAll('.student-select:not(:disabled)')", script)
        self.assertIn('if (submitting || selectedIds.length === 0) return;', script)
        self.assertIn('confirmButton.disabled = true;', script)

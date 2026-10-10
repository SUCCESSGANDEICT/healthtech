from django.shortcuts import render, redirect
from django.db import IntegrityError
from io import BytesIO
from urllib.parse import urlencode
from django.template.loader import render_to_string
from xhtml2pdf import pisa
from .models import (Faculty, Department, Postutmlformat,
                     AcademicSession, CourseCarryover,
                     Semester, CourseOfStudy,
                     Level, CourseUnit, Student, CourseEnrollment,
                     StudentAcademicCorrectionHistory
                     )
from fees.models import StudentFeePayment
from django.core.exceptions import MultipleObjectsReturned
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.exceptions import ValidationError
from fees.models import Fee
from fees.utils import get_school_fee_amount, get_user_indigene_type
from django.http import HttpResponseForbidden, JsonResponse
from decimal import Decimal
from datetime import datetime
from django.http import HttpResponse
from django.db import transaction
from django.contrib.auth.decorators import login_required
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib import messages
from .forms import (SchoolCreationForm, CSVSampleUploadForm,
                    DepartmentCreationForm, SearchStudentCourseReg,
                    FacultyFilterForm, LevelCreationForm,
                    ProgramCreationForm, StaffPublicationCreationForm,
                    DepartmentFilterForm, StudentLevelProgrammeSessionForm,
                    SessionCreationForm, EditStudentNamesForm,
                    SemesterCreationForm, CourseCreationUnitForm,
                    CourseUnitFilterForm, StudentsPromotionForm,
                    SearchStudentForm, StaffSearchForm,
                    LevelSearchForm, EditStudentProgrammeForm,
                    EditStudentNamesForm, StudentAcademicCorrectionForm,
                    StudentGraduationFilterForm
                    )
from django.urls import reverse, reverse_lazy
from django.shortcuts import get_object_or_404
from django.db.models import CharField, F, OuterRef, Prefetch, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, Concat
from payments.models import Payment
from accounts.forms import SuperAdminAcademicStaffCreationForm
from accounts.models import (
    GRADUATE_ROLE, STUDENT_ROLE, StaffRegister, StaffPublication,
    AcademicStaff, UserActivityLog,
)
from accounts.student_academic import resolve_student_academic_session, resolve_student_semester
from admission.models import staffAppSubmission, Admitted, ApplicationSubmission, Jamb
from admission.forms import StaffRegSubmissionForm
from .services import (
    correct_student_level_and_admitted_session,
    determine_graduation_session,
    graduate_students,
    has_graduation_status,
)
import logging

# Setup the logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


STUDENT_SEARCH_ADMIN_ROLES = {
    'Developer',
    'Super Admin',
    'Admin',
    'ICT DIRECTOR',
    'ADMISSION-OFFICER',
    'ADMISSION-OFFICER1',
    'MIS ADMIN-1',
    'MIS ADMIN-2',
    'Rector',
}

STUDENT_GRADUATION_ADMIN_ROLES = {
    'Developer',
    'Super Admin',
    'Admin',
    'ADMISSION-OFFICER',
}


def _can_search_students(user):
    return user.is_superuser or user.profile.role in STUDENT_SEARCH_ADMIN_ROLES


def _can_manage_graduation(user):
    return user.is_superuser or user.profile.role in STUDENT_GRADUATION_ADMIN_ROLES


def _graduation_student_row(student, expected_session):
    profile = getattr(student.candidate, 'profile', None)
    application = getattr(student.candidate, 'pre_applicant', None)
    admitted = getattr(student.candidate, 'admitted', None)
    name_parts = []
    for field in ('first_name', 'middle_name', 'last_name'):
        value = getattr(profile, field, None) or getattr(application, field, None)
        if value:
            name_parts.append(value.strip())
    return {
        'student': student,
        'is_graduate': has_graduation_status(student),
        'full_name': ' '.join(name_parts) or 'Name unavailable',
        'application_number': getattr(application, 'application_number', None) or '—',
        'admitted_session': getattr(getattr(admitted, 'academic_session', None), 'name', None) or '—',
        'expected_session': expected_session,
    }


@login_required(login_url='accounts_login')
def student_graduation_view(request):
    if not _can_manage_graduation(request.user):
        return HttpResponseForbidden('You are not authorized to manage student graduation.')
    return render(request, 'academics/student_graduation.html', {
        'page_title': 'Student Graduation',
        'form': StudentGraduationFilterForm(),
    })


@login_required(login_url='accounts_login')
def student_graduation_results(request):
    if not _can_manage_graduation(request.user):
        return JsonResponse({'error': 'You are not authorized to manage student graduation.'}, status=403)
    if request.method != 'GET' or request.headers.get('x-requested-with') != 'XMLHttpRequest':
        return JsonResponse({'error': 'A valid AJAX GET request is required.'}, status=400)

    form = StudentGraduationFilterForm(request.GET)
    if not form.is_valid():
        return JsonResponse({
            'error': 'Select a valid course, level, and graduating session.',
            'errors': form.errors,
        }, status=400)

    course = form.cleaned_data['course']
    level = form.cleaned_data['level']
    session = form.cleaned_data['session']
    search = form.cleaned_data['search'].strip()
    enrollment_history = (
        CourseEnrollment.objects.filter(programme=course, level__name=level.name)
        .select_related('academic_session')
        .order_by(
            '-academic_session__start_date', '-academic_session__end_date',
            '-academic_session__name', '-pk',
        )
    )
    latest_final_session = (
        CourseEnrollment.objects.filter(
            student_id=OuterRef('candidate_id'),
            programme=course,
            level__name=level.name,
        )
        .order_by(
            '-academic_session__start_date', '-academic_session__end_date',
            '-academic_session__name', '-pk',
        )
        .values('academic_session_id')[:1]
    )
    students = (
        Student.objects.filter(
            course=course,
            current_level=level.name,
            candidate__admitted__admitted_course=course,
        )
        .annotate(latest_final_session_id=Subquery(latest_final_session))
        .select_related(
            'candidate', 'candidate__profile', 'candidate__pre_applicant',
            'candidate__admitted', 'candidate__admitted__academic_session',
            'course', 'academic_session', 'graduation_session',
        )
        .prefetch_related(Prefetch(
            'candidate__enrollments', queryset=enrollment_history,
            to_attr='_graduation_enrollments',
        ))
        .order_by('candidate__profile__last_name', 'candidate__profile__first_name', 'pk')
    )
    if search:
        students = students.annotate(
            profile_full_name=Concat(
                Coalesce('candidate__profile__first_name', Value('')), Value(' '),
                Coalesce('candidate__profile__middle_name', Value('')), Value(' '),
                Coalesce('candidate__profile__last_name', Value('')),
                output_field=CharField(),
            ),
            application_full_name=Concat(
                Coalesce('candidate__pre_applicant__first_name', Value('')), Value(' '),
                Coalesce('candidate__pre_applicant__middle_name', Value('')), Value(' '),
                Coalesce('candidate__pre_applicant__last_name', Value('')),
                output_field=CharField(),
            ),
        ).filter(
            Q(candidate__profile__first_name__icontains=search)
            | Q(candidate__profile__middle_name__icontains=search)
            | Q(candidate__profile__last_name__icontains=search)
            | Q(candidate__pre_applicant__first_name__icontains=search)
            | Q(candidate__pre_applicant__middle_name__icontains=search)
            | Q(candidate__pre_applicant__last_name__icontains=search)
            | Q(profile_full_name__icontains=search)
            | Q(application_full_name__icontains=search)
            | Q(candidate__email__icontains=search)
            | Q(candidate__username__icontains=search)
            | Q(candidate__pre_applicant__application_number__icontains=search)
            | Q(student_id__icontains=search)
        ).distinct()

    progression = {'NDII': 'NDI', 'HNDII': 'HNDI'}
    predecessor = progression.get(level.name)
    no_recorded_session = Q(graduation_session__isnull=True, latest_final_session_id__isnull=True)
    if predecessor:
        legacy_progression = Q(
            academic_session_id=F('candidate__admitted__academic_session_id'),
            candidate__admitted__admitted_level=predecessor,
        )
        ordered_sessions = list(AcademicSession.objects.all())
        inferred_admitted_ids = []
        for admitted_session in ordered_sessions:
            next_session = next(
                (
                    candidate_session for candidate_session in ordered_sessions
                    if candidate_session.start_date > admitted_session.start_date
                ),
                None,
            )
            if next_session and next_session.pk == session.pk:
                inferred_admitted_ids.append(admitted_session.pk)
        fallback_match = (
            (Q(academic_session=session) & ~legacy_progression)
            | (legacy_progression & Q(candidate__admitted__academic_session_id__in=inferred_admitted_ids))
        )
    else:
        fallback_match = Q(academic_session=session)

    students = students.filter(
        Q(graduation_session=session)
        | Q(graduation_session__isnull=True, latest_final_session_id=session.pk)
        | (no_recorded_session & fallback_match)
    )
    paginator = Paginator(students, 20)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    page_obj.object_list = [
        _graduation_student_row(student, determine_graduation_session(student))
        for student in page_obj.object_list
    ]
    html = render_to_string('academics/partials/_student_graduation_results.html', {
        'page_obj': page_obj,
        'course': course,
        'level': level,
        'session': session,
    }, request=request)
    return JsonResponse({
        'html': html,
        'count': paginator.count,
        'page': page_obj.number,
        'pages': paginator.num_pages,
    })


@login_required(login_url='accounts_login')
def graduate_student_selection(request):
    if not _can_manage_graduation(request.user):
        return JsonResponse({'error': 'You are not authorized to manage student graduation.'}, status=403)
    if request.method != 'POST' or request.headers.get('x-requested-with') != 'XMLHttpRequest':
        return JsonResponse({'error': 'A valid AJAX POST request is required.'}, status=400)

    form = StudentGraduationFilterForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'error': 'The graduation context is invalid or incomplete.'}, status=400)

    raw_ids = request.POST.getlist('student_ids')
    if not raw_ids or len(raw_ids) > 20 or any(not value.isdigit() for value in raw_ids):
        return JsonResponse({
            'error': 'Select between 1 and 20 valid students from the current page.'
        }, status=400)

    try:
        with transaction.atomic():
            graduated, skipped = graduate_students(
                student_ids=[int(value) for value in raw_ids],
                course=form.cleaned_data['course'],
                level=form.cleaned_data['level'],
                graduation_session=form.cleaned_data['session'],
                graduated_by=request.user,
            )
            if graduated:
                UserActivityLog.objects.create(
                    user=request.user,
                    action=(
                        f'Graduated {len(graduated)} student(s) from '
                        f'{form.cleaned_data["course"]}, {form.cleaned_data["level"]}, '
                        f'{form.cleaned_data["session"]}.'
                    )[:255],
                )
    except Exception:
        logger.exception('Student graduation transaction failed.')
        return JsonResponse({
            'error': 'Graduation could not be completed. No partial changes were saved.'
        }, status=500)
    return JsonResponse({
        'graduated_count': len(graduated),
        'synchronized_count': sum(1 for item in skipped if item.get('synchronized')),
        'skipped_count': len(skipped),
        'skipped': skipped,
        'message': (
            f'{len(graduated)} student(s) graduated successfully.'
            if graduated else (
                'Existing graduate status was synchronized.'
                if any(item.get('synchronized') for item in skipped)
                else 'No students were graduated.'
            )
        ),
    }, status=200 if graduated or any(item.get('synchronized') for item in skipped) else 409)


def _student_search_payload(student):
    """Return the identifying fields exposed by the student-search API."""
    profile = getattr(student.candidate, 'profile', None)
    application = getattr(student.candidate, 'pre_applicant', None)

    def preferred(profile_value, application_value):
        return (profile_value or application_value or '').strip()

    first_name = preferred(
        getattr(profile, 'first_name', None), getattr(application, 'first_name', None)
    )
    middle_name = preferred(
        getattr(profile, 'middle_name', None), getattr(application, 'middle_name', None)
    )
    last_name = preferred(
        getattr(profile, 'last_name', None), getattr(application, 'last_name', None)
    )
    full_name = ' '.join(part for part in (first_name, middle_name, last_name) if part)

    return {
        'id': student.pk,
        'name': full_name or 'Name unavailable',
        'first_name': first_name,
        'middle_name': middle_name,
        'last_name': last_name,
        'email': student.candidate.email or student.candidate.username or '',
        'application_number': getattr(application, 'application_number', None) or '',
        'matriculation_number': student.student_id or '',
        'programme': getattr(student.course, 'name', '') or '',
        'level': student.current_level or '',
        'academic_session': getattr(student.academic_session, 'name', '') or '',
        'correction_url': reverse('student_academic_correction', args=[student.pk]),
    }


@login_required(login_url='accounts_login')
def student_search_view(request):
    """Dedicated, read-only student search interface."""
    if not _can_search_students(request.user):
        messages.warning(request, 'Something Went Wrong.')
        return redirect('accounts_login')
    return render(request, 'academics/student_search.html', {'page_title': 'Student Search'})


@login_required(login_url='accounts_login')
def student_search_results(request):
    """Type-ahead JSON endpoint for the dedicated student search interface."""
    if not _can_search_students(request.user):
        return JsonResponse({'error': 'You are not authorized to search students.'}, status=403)
    if request.method != 'GET' or request.headers.get('x-requested-with') != 'XMLHttpRequest':
        return JsonResponse({'error': 'A valid AJAX GET request is required.'}, status=400)

    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'results': [], 'message': 'Enter a name, email, application number, or matriculation number.'})

    students = (
        Student.objects.select_related(
            'candidate', 'candidate__profile', 'candidate__pre_applicant',
            'course', 'academic_session',
        )
        .filter(
            Q(candidate__profile__first_name__icontains=query)
            | Q(candidate__profile__middle_name__icontains=query)
            | Q(candidate__profile__last_name__icontains=query)
            | Q(candidate__pre_applicant__first_name__icontains=query)
            | Q(candidate__pre_applicant__middle_name__icontains=query)
            | Q(candidate__pre_applicant__last_name__icontains=query)
            | Q(candidate__email__icontains=query)
            | Q(candidate__username__icontains=query)
            | Q(candidate__pre_applicant__application_number__icontains=query)
            | Q(student_id__icontains=query)
        )
        .distinct()
        .order_by('candidate__profile__last_name', 'candidate__profile__first_name', 'pk')[:20]
    )
    results = [_student_search_payload(student) for student in students]
    return JsonResponse({
        'results': results,
        'message': '' if results else 'No students matched your search.',
    })


@login_required(login_url='accounts_login')
def student_search_selection(request):
    """Validate a type-ahead selection and return its current read-only details."""
    if not _can_search_students(request.user):
        return JsonResponse({'error': 'You are not authorized to select students.'}, status=403)
    if request.method != 'POST' or request.headers.get('x-requested-with') != 'XMLHttpRequest':
        return JsonResponse({'error': 'A valid AJAX POST request is required.'}, status=400)

    student_id = request.POST.get('student_id', '').strip()
    if not student_id.isdigit():
        return JsonResponse({'error': 'Select a valid student.'}, status=400)

    student = (
        Student.objects.select_related(
            'candidate', 'candidate__profile', 'candidate__pre_applicant',
            'course', 'academic_session',
        )
        .filter(pk=student_id)
        .first()
    )
    if student is None:
        return JsonResponse({'error': 'The selected student was not found.'}, status=404)
    return JsonResponse({'student': _student_search_payload(student)})


@login_required(login_url='accounts_login')
def student_academic_correction(request, student_id):
    """Confirm and apply a student's current-level/admitted-session correction."""
    if not _can_search_students(request.user):
        messages.warning(request, 'Something Went Wrong.')
        return redirect('accounts_login')

    student = get_object_or_404(
        Student.objects.select_related(
            'candidate', 'candidate__profile', 'academic_session', 'course'
        ),
        pk=student_id,
    )
    initial_level = Level.objects.filter(name=student.current_level).order_by('pk').first()
    initial = {
        'level': initial_level,
        'admitted_session': student.academic_session,
    }

    if request.method == 'POST':
        form = StudentAcademicCorrectionForm(request.POST)
        if form.is_valid():
            try:
                student, _history = correct_student_level_and_admitted_session(
                    student_id=student.pk,
                    level=form.cleaned_data['level'],
                    admitted_session=form.cleaned_data['admitted_session'],
                    changed_by=request.user,
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(
                    request,
                    'Student current level and admitted academic session were updated successfully.',
                )
                return redirect('student_academic_correction_result', student_id=student.pk)
    else:
        form = StudentAcademicCorrectionForm(initial=initial)

    return render(request, 'academics/student_academic_correction.html', {
        'page_title': 'Change Level / Admitted Session',
        'student': student,
        'form': form,
    })


@login_required(login_url='accounts_login')
def student_academic_correction_result(request, student_id):
    """Show the current values and append-only correction history after an update."""
    if not _can_search_students(request.user):
        messages.warning(request, 'Something Went Wrong.')
        return redirect('accounts_login')

    student = get_object_or_404(
        Student.objects.select_related(
            'candidate', 'candidate__profile', 'academic_session', 'course'
        ),
        pk=student_id,
    )
    history = StudentAcademicCorrectionHistory.objects.filter(
        student=student
    ).select_related(
        'previous_admitted_session', 'new_admitted_session', 'changed_by'
    )
    return render(request, 'academics/student_academic_correction_result.html', {
        'page_title': 'Student Academic Update Result',
        'student': student,
        'history': history,
    })


def get_active_semester_for_session(academic_session):
    return Semester.get_active_for_session(academic_session)


def require_active_semester(request, academic_session):
    current_semester = get_active_semester_for_session(academic_session)
    if not current_semester:
        messages.warning(request, f'No active semester found for {academic_session}.')
    return current_semester


def get_previous_level_enrollments(student, current_session, current_semester, previous_level):
    return CourseEnrollment.objects.filter(
        student=student.candidate,
        academic_session__start_date__lt=current_session.start_date,
        semester__name__iexact=current_semester.name,
        level__name=previous_level,
        programme=student.course,
    )

# Level Creation view.
class LevelCreationView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Faculty
    template_name = 'academics/add_level.html'
    form_class = LevelCreationForm  

    # Define a custom test function to check if the user 
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'
 
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Add Level'
        return context

    def form_valid(self, form):
        # Check if a School with the same name already exists
        level_name = form.cleaned_data['name'] 
        if Level.objects.filter(name=level_name).exists():
            messages.error(self.request, 'Level already exists.')
            return self.form_invalid(form)
    
        # Set the added_by field to the logged-in user before saving
        form.instance.added_by = self.request.user
        form.save()

        # Save the form and add a success message
        form.save()
        messages.success(self.request, 'Level Added Successfully.')
        return super().form_valid(form)

    success_url = reverse_lazy('list_levels')


# List of Level view.
class LevelListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = Level
    template_name = 'academics/level_list.html'
    context_object_name = 'levels'
    paginate_by = 5

    # function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'
    
    def get_queryset(self):
        # Add ordering to the queryset
        return Level.objects.order_by('-added_date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'List of Levels'
        # Add pagination information to the context
        queryset = self.get_queryset()  # Retrieve the original queryset
        paginator = Paginator(queryset, self.paginate_by)  # Paginate before rendering
        page_number = self.request.GET.get('page')

        try:
            page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)

        context['page_obj'] = page_obj
        return context


# School Creation view.
class SchoolCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Faculty
    template_name = 'academics/add_school.html'
    form_class = SchoolCreationForm  

    # Define a custom test function to check if the user 
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'
 
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Add College'
        return context

    def form_valid(self, form):
        # Check if a School with the same name already exists
        faculty_name = form.cleaned_data['name'] 
        if Faculty.objects.filter(name=faculty_name).exists():
            messages.error(self.request, 'College with this name already exists.')
            return self.form_invalid(form)
    
        # Set the added_by field to the logged-in user before saving
        form.instance.added_by = self.request.user
        form.save()

        # Save the form and add a success message
        form.save()
        messages.success(self.request, 'College Added Successfully.')
        return super().form_valid(form)

    success_url = reverse_lazy('school_list')


# List of Schools view.
class SchoolListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = Faculty
    template_name = 'academics/school_list.html'
    context_object_name = 'schools'
    paginate_by = 10

    # function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'
    
    def get_queryset(self):
        # Add ordering to the queryset
        return Faculty.objects.order_by('-added_date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'List of Colleges'
        # Add pagination information to the context
        queryset = self.get_queryset()  # Retrieve the original queryset
        paginator = Paginator(queryset, self.paginate_by)  # Paginate before rendering
        page_number = self.request.GET.get('page')

        try:
            page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)

        context['page_obj'] = page_obj
        return context


# Delete School View
class SchoolDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Faculty
    template_name = 'academics/confirm_delete_school.html'
    success_url = '/school/list/'
    context_object_name = 'school'  # the context object name

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'

    def delete(self, request, *args, **kwargs):
        messages.error(request, 'College Deleted successfully.')
        return super().delete(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Delete College'
        return context


# Update School view
class SchoolUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Faculty
    template_name = 'academics/add_school.html'
    form_class = SchoolCreationForm  # Use the form for updating

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'

    def form_valid(self, form):
        # Save the form and add a success message
        form.save()
        messages.success(self.request, 'College Update Successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Update College'
        return context

    success_url = reverse_lazy('school_list')


# Department Creation view.
@login_required(login_url='accounts_login')
def department_create_view(request):
    if not (request.user.profile.role == 'MIS ADMIN-1' or request.user.is_superuser or request.user.profile.role == 'Super Admin'):
        return redirect('accounts_dashboard')
    
    # Check if School exist before allowing access to add Department
    if not Faculty.objects.exists():
        messages.warning(request, 'Add College First before Department')
        return redirect('add_school')

    if request.method == 'POST':
        form = DepartmentCreationForm(request.POST)
        if form.is_valid():
            department_name = form.cleaned_data['name']
            dept_type = form.cleaned_data['dept_type']
            faculty_name = form.cleaned_data['faculty']

            if Department.objects.filter(name=department_name, dept_type=dept_type, faculty=faculty_name).exists():
                messages.error(request, f'{department_name} already exists in {faculty_name}')
            else:
                # Set the added_by field to the logged-in user before saving
                department = form.save(commit=False)
                department.added_by = request.user
                department.save()
                messages.success(request, f'{department_name} Added Successfully.')
                return redirect('department_list')
    else:
        form = DepartmentCreationForm()

    context = {
        'form': form,
        'page_title': 'Add Department'
        }
    return render(request, 'academics/add_department.html', context)


@login_required(login_url='accounts_login')
def same_department_list_view(request, faculty_name):
    if not (request.user.profile.role == 'MIS ADMIN-1' or request.user.is_superuser or request.user.profile.role == 'Super Admin'):
        return redirect('accounts_dashboard')

    # Assuming Faculty is the related model for the faculty field in the Department model
    # faculty_instance = get_object_or_404(Faculty, name=faculty_name)
    try:
        faculty_instance = Faculty.objects.get(name=faculty_name)
        queryset = Department.objects.filter(faculty=faculty_instance).order_by('-added_date')

        paginator = Paginator(queryset, 5)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'departments': page_obj,
            'page_title': 'List of Departments',
            }
        return render(request, 'academics/department_list.html', context)
    except Faculty.DoesNotExist:
        messages.warning(request, f'{faculty_name} Does Not Exist')
        return redirect('filter_departments_by_school')

    


# List Departments by School view
@login_required(login_url='accounts_login')
def department_list(request):

    if not (request.user.profile.role == 'MIS ADMIN-1' or request.user.is_superuser or request.user.profile.role == 'Super Admin'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_dashboard')
    
    departments = Department.objects.all().order_by('-added_date')
    form = FacultyFilterForm(request.GET or None)

    if form.is_valid():
        selected_faculty = form.cleaned_data['school']
        if selected_faculty:
            departments = departments.filter(faculty=selected_faculty)

    paginator = Paginator(departments, 5)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'departments': page_obj,
        'form': form,
        'page_title': 'List of Departments',
        }
    return render(request, 'academics/filter_by_school.html', context)


# Delete Department View
class DepartmentDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Department
    template_name = 'academics/confirm_delete_department.html'
    success_url = '/filter/departments/by/school/'
    context_object_name = 'department'

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'

    def delete(self, request, *args, **kwargs):
        messages.error(request, 'Department Deleted successfully.')
        return super().delete(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Delete Department'
        return context


# Update Department view
class DepartmentUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Department
    template_name = 'academics/add_department.html'
    form_class = DepartmentCreationForm

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'

    def form_valid(self, form):
        # Save the form and add a success message
        form.save()
        messages.success(self.request, 'Department Update Successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Update Department'
        return context

    success_url = reverse_lazy('filter_departments_by_school')


# Program of Study Creation view.
@login_required(login_url='accounts_login')
def create_program_view(request):
    if not (request.user.profile.role == 'MIS ADMIN-1' or request.user.is_superuser or request.user.profile.role == 'Super Admin'):
        return redirect('accounts_dashboard')

    if request.method == 'POST':
        form = ProgramCreationForm(request.POST)
        if form.is_valid():
            program_name = form.cleaned_data['name']
            department_name = form.cleaned_data['department']
            # Check if Program already exist in the Department with same name
            if CourseOfStudy.objects.filter(name=program_name, department=department_name).exists():
                messages.error(request, f'{program_name} already exists in {department_name} Department.')
            else:
                # Set the added_by field to the logged-in user before saving
                program = form.save(commit=False)
                program.added_by = request.user
                program.save()
                messages.success(request, f'{program_name} Program Added Successfully in {department_name} Department.')
                return redirect('program_list_by_department')
    else:
        form = ProgramCreationForm()

    context = {
        'form': form,
        'page_title': 'Add Program',
        }
    return render(request, 'academics/add_program_of_study.html', context)


# List Programs in Department.
@login_required(login_url='accounts_login')
def same_department_program_list_view(request, department_name):
    if not (request.user.profile.role == 'MIS ADMIN-1' or request.user.is_superuser or request.user.profile.role == 'Super Admin'):
        return redirect('accounts_dashboard')

    # Get Department and compare
    department_instance = get_object_or_404(Department, name=department_name)

    queryset = CourseOfStudy.objects.filter(department=department_instance).order_by('-added_date')

    form = DepartmentFilterForm(request.GET or None)

    if form.is_valid():
        selected_department = form.cleaned_data['department_name']
        if selected_department:
            queryset = queryset.filter(course_of_study__department=selected_department)

    paginator = Paginator(queryset, 5)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'form': form,
        'programs': page_obj,
        'page_title': 'List of Programs',
        }
    return render(request, 'academics/program_list.html', context)


# List Program by Department view
@login_required(login_url='accounts_login')
def program_list(request):

    if not (request.user.profile.role == 'MIS ADMIN-1' or request.user.is_superuser or request.user.profile.role == 'Super Admin'):
        return redirect('accounts_dashboard')

    programs = CourseOfStudy.objects.all().order_by('-added_date')
    form = DepartmentFilterForm(request.GET or None)

    if form.is_valid():
        selected_department = form.cleaned_data['department_name']
        if selected_department:
            programs = programs.filter(department=selected_department)

    paginator = Paginator(programs, 5)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'programs': page_obj,
        'form': form,
        'page_title': 'List Programs',
        }
    return render(request, 'academics/program_list.html', context)


# Delete Program View
class ProgramDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = CourseOfStudy
    template_name = 'academics/confirm_delete_program.html'
    success_url = '/program/list/by/department/'
    context_object_name = 'program'

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'

    def delete(self, request, *args, **kwargs):
        messages.error(request, 'Program Deleted successfully.')
        return super().delete(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Delete Program'
        return context


# Update Program view
class ProgramUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = CourseOfStudy
    template_name = 'academics/add_program_of_study.html'
    form_class = ProgramCreationForm

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'

    def form_valid(self, form):
        # Save the form and add a success message
        form.save()
        messages.success(self.request, 'Program Update Successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Update Program'
        return context

    success_url = reverse_lazy('program_list_by_department')


# Academic Session Creation view.
class SessionCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = AcademicSession
    template_name = 'academics/add_session.html'
    form_class = SessionCreationForm 

    # Define a custom test function to check if the user 
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'
 
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Add Academic Session'
        return context

    def form_valid(self, form):
        # Check if a School with the same name already exists
        session_name = form.cleaned_data['name'] 
        if AcademicSession.objects.filter(name=session_name).exists():
            messages.error(self.request, 'Academic Session with this name already exists.')
            return self.form_invalid(form)
    
        # Set the added_by field to the logged-in user before saving
        form.instance.added_by = self.request.user
        form.save()

        # Save the form and add a success message
        form.save()
        messages.success(self.request, 'Academic Session Added Successfully.')
        return super().form_valid(form)

    success_url = reverse_lazy('session_list')


# Delete Session View
class SessionDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = AcademicSession
    template_name = 'academics/confirm_delete_session.html'
    success_url = '/session/list/'
    context_object_name = 'academic_session'

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Delete Academic Session'
        return context

    def delete(self, request, *args, **kwargs):
        response = super().delete(request, *args, **kwargs)
        messages.success(request, 'Academic Session deleted successfully.')
        return response


# List of Academic Session view.
class SessionListView(LoginRequiredMixin, UserPassesTestMixin, ListView): 
    model = AcademicSession 
    template_name = 'academics/session_list.html'
    context_object_name = 'academic_sessions' 
    paginate_by = 5

    # function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'List of Session'
        return context

    def delete(self, request, *args, **kwargs):
        messages.success(request, 'Academic Session deleted successfully.')
        return super().delete(request, *args, **kwargs)


# Update Academic Session view
class SessionUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = AcademicSession
    template_name = 'academics/add_session.html'
    form_class = SessionCreationForm

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'

    def form_valid(self, form):
        # Save the form and add a success message
        form.save()
        messages.success(self.request, 'Academic Session Update Successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Update Session'
        return context

    success_url = reverse_lazy('session_list')


# Academic Semester Creation view.
class SemesterCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Semester
    template_name = 'academics/add_semester.html'
    form_class = SemesterCreationForm

    # Define a custom test function to check if the user 
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Add Academic Semester'
        return context

    def form_valid(self, form):
        semester_name = form.cleaned_data['name'] 
        session = form.cleaned_data['academic_session']
        if Semester.objects.filter(name__iexact=semester_name, academic_session=session).exists():
            messages.error(self.request, 'Academic Semester with this name already exists for the selected session.')
            return self.form_invalid(form)
    
        # Set the added_by field to the logged-in user before saving
        form.instance.added_by = self.request.user
        form.save()
        messages.success(self.request, 'Academic Semester Added Successfully.')
        return super().form_valid(form)

    success_url = reverse_lazy('semester_list')


# List of Academic Semester view.
class SemesterListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = Semester
    template_name = 'academics/semester_list.html'
    context_object_name = 'academic_semesters'
    paginate_by = 5

    # function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'

    def get_queryset(self):
        return Semester.objects.select_related('academic_session', 'added_by').order_by('-academic_session__start_date', 'name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'List of Semesters'
        return context

    def delete(self, request, *args, **kwargs):
        messages.success(request, 'Academic Semester deleted successfully.')
        return super().delete(request, *args, **kwargs)


# Update Academic Semester view
class SemesterUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Semester
    template_name = 'academics/add_semester.html'
    form_class = SemesterCreationForm

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'

    def form_valid(self, form):
        semester_name = form.cleaned_data['name']
        session = form.cleaned_data['academic_session']
        duplicate = Semester.objects.filter(name__iexact=semester_name, academic_session=session).exclude(pk=self.object.pk)
        if duplicate.exists():
            messages.error(self.request, 'Academic Semester with this name already exists for the selected session.')
            return self.form_invalid(form)
        form.save()
        messages.success(self.request, 'Academic Semester Update Successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Update Semester'
        return context

    success_url = reverse_lazy('semester_list')


# Delete Semester View
class SemesterDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Semester
    template_name = 'academics/confirm_delete_semester.html'
    success_url = '/semester/list/'
    context_object_name = 'academic_semester'

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Delete Academic Semester'
        return context

    def delete(self, request, *args, **kwargs):
        response = super().delete(request, *args, **kwargs)
        messages.success(request, 'Academic Semester deleted successfully.')
        return response


# Create Course Unit View
@login_required(login_url='accounts_login')
def create_course_unit(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'MIS ADMIN-1' or logged_user.is_superuser or logged_user.profile.role == 'Super Admin'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    if request.method == 'POST':
        form = CourseCreationUnitForm(request.POST)
        if form.is_valid():
            course_unit = form.save(commit=False)
            course_unit.added_by = request.user
            course_unit.save()

            messages.success(request, 'Course Unit Added Successfully')

            # Use IDs instead of model objects for redirect parameters
            return redirect(
                'same_program_course_units',
                program_id=course_unit.course_of_study.id,
                semester_id=course_unit.semester.id,
                level_id=course_unit.level.id
            )
        messages.error(request, 'Please correct the highlighted course unit details.')
    else:
        form = CourseCreationUnitForm()

    context = {
        'form': form,
        'page_title': 'Add Course Unit'
    }
    return render(request, 'academics/create_course_unit.html', context)


@login_required(login_url='accounts_login')
def get_semesters_by_session(request):
    session_id = request.GET.get('academic_session')
    semesters = Semester.objects.none()
    if session_id:
        semesters = Semester.objects.filter(academic_session_id=session_id).order_by('name')
    data = [{'id': semester.id, 'name': semester.name} for semester in semesters]
    return JsonResponse(data, safe=False)



# View for JSON response of corresponding Level
def get_level(request):
    level_name = request.GET.get('course_of_study')  # Assuming 'course_of_study' is what you want to filter on
    levels = Level.objects.filter(name=level_name).values('id', 'name')
    return JsonResponse(list(levels), safe=False)


# List Course Units in same Program of Study.
@login_required(login_url='accounts_login')
def same_program_course_list_view(request, program_id, semester_id, level_id):
    # Check user role permission
    if not (request.user.profile.role in ['MIS ADMIN-1', 'Super Admin'] or request.user.is_superuser):
        return redirect('accounts_dashboard')

    # Fetch related instances safely using IDs
    programe_instance = get_object_or_404(CourseOfStudy, id=program_id)
    semester_instance = get_object_or_404(Semester, id=semester_id)
    level_instance = get_object_or_404(Level, id=level_id)

    # Filter course units for this program, semester, and level
    queryset = (
        CourseUnit.objects
        .select_related('academic_session', 'course_of_study', 'semester', 'level')
        .filter(
            academic_session=semester_instance.academic_session,
            course_of_study=programe_instance,
            semester=semester_instance,
            level=level_instance
        )
        .order_by('-added_date')
    )

    # Optional department filter
    form = DepartmentFilterForm(request.GET or None)
    if form.is_valid():
        selected_department = form.cleaned_data['department_name']
        if selected_department:
            queryset = queryset.filter(department=selected_department)

    # Paginate results
    paginator = Paginator(queryset, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'form': form,
        'course_units': page_obj,
        'program': programe_instance,
        'semester': semester_instance,
        'level': level_instance,
        'page_title': f'Course Units for {programe_instance.name} - {semester_instance.name} - {level_instance.name}',
    }

    return render(request, 'academics/course_unit_list.html', context)



# Filter Course Units by Level, Program and Semester.
@login_required(login_url='accounts_login')
def level_course_list_view(request):
    if not (request.user.profile.role == 'MIS ADMIN-1' or request.user.is_superuser or request.user.profile.role == 'Super Admin'):
        return redirect('accounts_dashboard')

    form = CourseUnitFilterForm(request.GET or None)
    if form.is_valid():
        academic_session = form.cleaned_data['academic_session']
        level = form.cleaned_data['level']
        program = form.cleaned_data['program']
        semester = form.cleaned_data['semester']

        course_units = CourseUnit.objects.select_related('academic_session', 'semester', 'level', 'course_of_study').filter(
            academic_session=academic_session, course_of_study=program, semester=semester, level=level).order_by('course_code')

        if course_units:
            messages.success(request, f'List of Courses for {program} - {level} - {semester} - {academic_session}')
            paginator = Paginator(course_units, 3)
            page_number = request.GET.get('page')
            page_obj = paginator.get_page(page_number)

            query_params = request.GET.copy()
            query_params.pop('page', None)  # Remove the existing 'page' parameter

            context = {
                'page_title': 'List of Course Units',
                'course_units': page_obj,
                'query_params': query_params,
            }
            return render(request, 'academics/list_course_units_program.html', context)
        else:
            messages.warning(request, 'No Course Unit Found')

    context = {
        'form': form,
        'page_title': 'Filter Course Units',
    }
    return render(request, 'academics/filter_course_unit.html', context)


# Update Course Unit View
class CourseUnitUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = CourseUnit
    template_name = 'academics/create_course_unit.html'
    form_class = CourseCreationUnitForm

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'

    def form_valid(self, form):
        course_unit = form.save(commit=False)
        course_unit.added_by = course_unit.added_by or self.request.user
        course_unit.save()
        messages.success(self.request, 'Course Unit Updated Successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Update Course Unit'
        return context

    success_url = reverse_lazy('level_course_list_view')


# Delete Course Unit View
class CourseUnitDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = CourseUnit
    template_name = 'academics/confirm_delete_course_unit.html'
    success_url = '/add/course_unit/'
    context_object_name = 'course_unit'

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser or self.request.user.profile.role == 'Super Admin'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Delete Course Unit'
        return context

    def delete(self, request, *args, **kwargs):
        response = super().delete(request, *args, **kwargs)
        messages.success(request, 'Course Unit deleted successfully.')
        return response

# Ask Student whether they have Carry over
@login_required(login_url='accounts_login')
def confirm_student_carryover(request):
    logged_user = request.user
    if not logged_user.profile.role == 'Student':
        messages.warning(request, 'You are not a Student')
        return redirect('accounts_login')
    
    try:
        student = Student.objects.select_related('candidate', 'course').get(candidate=logged_user)
        student_current_level_name = student.current_level
        student_current_semester = student.semester
        latest_academic_session, session_message = resolve_student_academic_session(student, logged_user)
        # Find the corresponding Level instance
        try:
            level_instance = Level.objects.get(name=student_current_level_name)
            student_current_level_id = level_instance.id
        except Level.DoesNotExist:
            messages.warning(request, 'Level Does Not Exist.')
            

        student_program = student.course  # This is a CourseOfStudy instance
        
    except Student.DoesNotExist:
        messages.error(request, 'Student Not Found')
        return redirect('accounts_dashboard')
   
    if not latest_academic_session:
        messages.warning(request, session_message)
        return redirect('accounts_dashboard')

    current_semester, _semester_name = resolve_student_semester(
        student, latest_academic_session, require_active=True
    )
    if not current_semester:
        messages.warning(request, f'No active semester found for {latest_academic_session}.')
        return redirect('accounts_dashboard')
    
    if student_current_level_name == 'HNDII' or student_current_level_name == 'NDII':
        # Get Previous Level using the Student instance
        previous_level = student.get_previous_level()
    
        # Get Previous Level Instance ID
        try:
            previous_level_instance = Level.objects.get(name=previous_level)
        except Level.DoesNotExist:
            messages.warning(request, 'Level NOT Found')
            return redirect('student_carryover_courses')
    
        context = {
            'student': student,
            'current_semester': current_semester,
            'student_current_level_name': student_current_level_name, 
            'previous_level': previous_level,
            'page_title': 'Confirm Course Reg.',
            'student_current_semester': student_current_semester,
        }
        return render(request, 'academics/confirm_course_reg.html', context) 
    else:
        context = {
            'student': student,
            'student_current_level_name': student_current_level_name, 
            'student_current_semester': student_current_semester,
            'page_title': 'Confirm Course Reg.',
            'current_semester': current_semester, 
        }
        return render(request, 'academics/confirm_course_reg.html', context)


# Display List of Student Previous Semester Course Units for Registration
@login_required(login_url='accounts_login')
def student_carryover_course_reg(request):
    logged_user = request.user
    if not logged_user.profile.role == 'Student':
        messages.warning(request, 'You are not a Student')
        return redirect('accounts_login')

    try:
        student_instance = Student.objects.select_related('candidate', 'course').get(candidate=logged_user)
        student_current_level_name = student_instance.current_level
        latest_academic_session, session_message = resolve_student_academic_session(
            student_instance, logged_user
        )

        # Find the corresponding Level instance
        try:
            level_instance = Level.objects.get(name=student_current_level_name)
            student_current_level_id = level_instance.id
        except Level.DoesNotExist:
            messages.error(request, 'Level Not Found')
            return redirect('accounts_dashboard')

        student_program = student_instance.course  # This is a CourseOfStudy instance
        
    except Student.DoesNotExist:
        messages.error(request, 'Student Not Found')
        return redirect('accounts_dashboard')
   
    if not latest_academic_session:
        messages.warning(request, session_message)
        return redirect('accounts_dashboard')

    current_semester, _semester_name = resolve_student_semester(
        student_instance, latest_academic_session, require_active=True
    )
    if not current_semester:
        messages.warning(request, f'No active semester found for {latest_academic_session}.')
        return redirect('accounts_dashboard')
    
    # Determine Student Previous Level
    previous_level = student_instance.get_previous_level()
    if not previous_level:
        messages.error(request, 'Previous Level Not Found')
        return redirect('accounts_dashboard')
    
    course_units = CourseUnit.objects.filter(
        academic_session=latest_academic_session,
        semester=current_semester,
        level__name=previous_level,
        course_of_study=student_program,
    )

    context = {
        'student_instance': student_instance,
        'current_semester': current_semester,
        'latest_academic_session': latest_academic_session,
        'previous_level_courses': course_units,
        'previous_level': previous_level,
        'page_title': 'Carryover Regisration',
        
    }
    return render(request, 'academics/carryover_course_enrollment.html', context) 


# Register Student Carryover Course Units
@login_required(login_url='accounts_login')
def register_carryover_courses(request):
    logged_user = request.user
    if not logged_user.profile.role == 'Student':
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_dashboard')

    # Get student instance
    try:
        student_instance = Student.objects.select_related('candidate').get(candidate=logged_user.id)
        student_programme = student_instance.course
        student_level = student_instance.current_level
        latest_academic_session, session_message = resolve_student_academic_session(
            student_instance, logged_user
        )
        # student = student_instance.user
    except Student.DoesNotExist:
        messages.warning(request, 'Student Does Not Exist')
        return redirect('accounts_login')

    if not latest_academic_session:
        messages.warning(request, session_message)
        return redirect('semester_course_registration')

    current_semester, _semester_name = resolve_student_semester(
        student_instance, latest_academic_session, require_active=True
    )
    if not current_semester:
        messages.warning(request, f'No active semester found for {latest_academic_session}.')
        return redirect('semester_course_registration')
    print('Student Level:', student_level)
    # Find the corresponding Current Student Level instance
    try:
        level_instance = Level.objects.get(name=student_level)
        student_current_level_id = level_instance.id
    except Level.DoesNotExist:
        messages.error(request, 'Level Not Found')
        return redirect('semester_course_registration')
    
    # Get Level Id from the student Level      
    try:
        current_student_level_id = Level.objects.get(id=student_current_level_id)
    except Level.DoesNotExist:
        messages.error(request, 'Level Not Found')
        return redirect('semester_course_registration')
    
    # Get Previous Level using the Student instance
    previous_level = student_instance.get_previous_level()
    
    # Get Previous Level Instance ID
    try:
        previous_level_instance = Level.objects.get(name=previous_level)
    except Level.DoesNotExist:
        messages.warning(request, 'Level NOT Found')
        return redirect('student_carryover_courses')

    eligible_course_units = CourseUnit.objects.filter(
        academic_session=latest_academic_session,
        semester=current_semester,
        level=previous_level_instance,
        course_of_study=student_programme,
    )
    
    if request.method == 'POST':
        
        # Get the selected applicant through the checkboxes
        selected_course_units_ids = request.POST.getlist('selected_carryover_courses[]')
        # Initiate the admitted candidate counter
        registered_course_units_count = 0
        # Try to put the process in a transaction
        try:
            with transaction.atomic():
                registered_course_units = []
                total_credit_hours = 0  # Initialize total credit hours
                # Query CourseCarryover to check if Carryover Courses already registered
                # for the previous level
                confirmed_course_units_registration = CourseCarryover.objects.filter(
                    course_unit_id__in=selected_course_units_ids,
                    student=logged_user,
                    academic_session=latest_academic_session.id,
                    semester=current_semester,
                    level=previous_level_instance
                   
                ).distinct()
                
                if confirmed_course_units_registration:
                    messages.warning(request, f' {selected_course_units_ids} Already Registered for Carryover for {current_semester} {previous_level} . Check appropriately')

                else:
                    # Iterate through selected_course_units_ids and create CourseEnrollment instances
                    for course_unit_id in selected_course_units_ids:
                        course_unit_instance = eligible_course_units.get(id=course_unit_id)
                        registered_course_units.append(CourseCarryover(
                            student=logged_user,
                            course_unit=course_unit_instance,
                            academic_session=latest_academic_session,
                            semester=current_semester,
                            level=previous_level_instance
                        ))
                        # Increment total credit hours by the credit load of the current course unit
                        total_credit_hours += course_unit_instance.credit_load
                    
                    # Check if Admission Transaction was Success, count, and give a message
                    if registered_course_units:
                        try:
                            with transaction.atomic():
                                CourseCarryover.objects.bulk_create(registered_course_units)
                        except IntegrityError as e:
                            messages.warning(request, f'Some Course Units were not registered due to a database error: {str(e)}')
                            return redirect('student_carryover_courses')

                        registered_course_units_count += len(registered_course_units)
                        messages.success(request, f'You Registered {registered_course_units_count} Carryover Course Unit(s) with {total_credit_hours} Credit Hour(s) for {previous_level} for the {latest_academic_session} Academic Session.')
                        return redirect('semester_course_registration')
                    else:
                        messages.error(request, 'No Course Unit was Registered')
                        return redirect('student_carryover_courses')
                   
        except Exception as e:
            messages.error(request, f'Something went wrong: {str(e)}')

    else:
        messages.error(request, 'Invalid request method. Expected POST.')

    # Redirect back to the search page or an appropriate page
    return redirect('semester_course_registration')


# Display List of Course Units for Semester Course Unit Registration
@login_required(login_url='accounts_login')
def student_course_registration(request):
    logged_user = request.user
    
    # Ensure only 'Student' role can access this view
    if logged_user.profile.role != 'Student':
        messages.warning(request, 'You are not a Student')
        return redirect('accounts_login')

    try:
        student_instance = Student.objects.select_related('candidate', 'course').get(candidate=logged_user)
        student_programme = student_instance.course  # CourseOfStudy instance
        student_current_level_name = student_instance.current_level
        latest_academic_session, session_message = resolve_student_academic_session(
            student_instance, logged_user
        )
    except Student.DoesNotExist:
        messages.error(request, 'Student profile not found')
        return redirect('accounts_dashboard')
    
    # Get student level
    try:
        level_instance = Level.objects.get(name=student_current_level_name)
    except Level.DoesNotExist:
        messages.error(request, 'Level not found')
        return redirect('accounts_dashboard')
    
    if not latest_academic_session:
        messages.warning(request, session_message)
        return redirect('accounts_dashboard')

    current_semester, _semester_name = resolve_student_semester(
        student_instance, latest_academic_session, require_active=True
    )
    if not current_semester:
        messages.warning(request, f'No active semester found for {latest_academic_session}.')
        return redirect('accounts_dashboard')
    
    # Get Semester Name
    semester_name = current_semester.name
    # Get Student Fee
    
    paid_fees = StudentFeePayment.objects.filter(
        student=logged_user,
        academic_session=latest_academic_session,
        student_level=level_instance,
        course_of_study=student_programme
    )

    # Initialize flags
    has_paid_first = False
    has_paid_second = False

    # Loop through once to set flags
    for fee in paid_fees:
        if fee.has_paid_first_semester:
            has_paid_first = True
        if fee.has_paid_second_semester or fee.has_paid_full_session:
            has_paid_second = True

    # Conditional checks for School fees payments
    if semester_name == 'First Semester' and not has_paid_first:
        messages.warning(request, f'You have not paid {semester_name} School Fees for {latest_academic_session}')
        return redirect('accounts_dashboard')

    elif semester_name == 'Second Semester' and not has_paid_second:
        messages.warning(request, f'You have not paid {semester_name} School Fees for {latest_academic_session}')
        return redirect('accounts_dashboard')

    else:
        # Get course units based on the current level, program, and semester
        student_course_units = CourseUnit.objects.filter(
            academic_session=latest_academic_session,
            course_of_study=student_programme,
            semester=current_semester,
            level=level_instance
        )

        # Handle carryover courses for NDII/HNDII
        if student_current_level_name in ['NDII', 'HNDII']:
            previous_level_name = student_instance.get_previous_level() 
            
            try:
                previous_level_instance = Level.objects.get(name=previous_level_name)
            except Level.DoesNotExist:
                messages.error(request, 'Previous level not found')
                return redirect('accounts_dashboard')

            check_carryover = CourseCarryover.objects.filter(
                student=logged_user,
                academic_session=latest_academic_session,
                semester=current_semester,
                level=previous_level_instance
            )

            carryover_credit_hours = CourseCarryover.calculate_carryover_credit_hours(
                logged_user, current_semester, latest_academic_session, previous_level_instance
            )

            context = {
                'carryover_credit_hours': carryover_credit_hours,
                'previous_level': previous_level_name,
                'check_carryover': check_carryover,
                'page_title': 'Course Registration',
                'student': student_instance,
                'current_semester': current_semester,
                'latest_academic_session': latest_academic_session,
                'student_course_units': student_course_units,
            }
        else:
            context = {
                'page_title': 'Course Registration',
                'student': student_instance,
                'current_semester': current_semester,
                'latest_academic_session': latest_academic_session,
                'student_course_units': student_course_units,
            }

        # Render the template with context
        return render(request, 'academics/course_unit_enrollment.html', context) 



# Student Semester Course Units Registration List view
@login_required(login_url='accounts_login')
def register_course_units(request):
    logged_user = request.user
    if not logged_user.profile.role == 'Student':
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_dashboard')

    # Get student instance
    try:
        student_instance = Student.objects.select_related('candidate').get(candidate=logged_user.id)
        student_programme = student_instance.course
        student_level = student_instance.current_level
        latest_academic_session, session_message = resolve_student_academic_session(
            student_instance, logged_user
        )
        # student = student_instance.user
    except Student.DoesNotExist:
        messages.warning(request, 'Student Does Not Exist')
        return redirect('accounts_login')

    if not latest_academic_session:
        messages.warning(request, session_message)
        return redirect('semester_course_registration')

    current_semester, _semester_name = resolve_student_semester(
        student_instance, latest_academic_session, require_active=True
    )
    if not current_semester:
        messages.warning(request, f'No active semester found for {latest_academic_session}.')
        return redirect('semester_course_registration')
    
    # Find the corresponding Current Student Level instance
    try:
        level_instance = Level.objects.get(name=student_level)
        student_current_level_id = level_instance.id
    except Level.DoesNotExist:
        messages.error(request, 'Level Not Found')
        return redirect('semester_course_registration')
    
    # Get Level Id from the student Level      
    try:
        student_level = Level.objects.get(id=student_current_level_id)
    except Level.DoesNotExist:
        messages.error(request, 'Level Not Found')
        return redirect('semester_course_registration')

    if request.method == 'POST':
        # Get the selected applicant through the checkboxes
        selected_course_units_ids = request.POST.getlist('selected_course_units[]')
        # Initiate the admitted candidate counter
        registered_course_units_count = 0
        # Try to put the process in a transaction
        try:
            with transaction.atomic():
                registered_course_units = []
                total_credit_hours = 0  # Initialize total credit hours
                # Query CourseEnrollment to check if Courses already registered
                # for the academic session by the user
                confirmed_course_units_registration = CourseEnrollment.objects.filter(
                    course_unit_id__in=selected_course_units_ids,
                    student=logged_user,
                    academic_session=latest_academic_session,
                    semester=current_semester,
                    programme=student_programme,
                    level=student_level,
                ).exists()

                if confirmed_course_units_registration:
                    messages.warning(request, f' {selected_course_units_ids} Already Registered. Check appropriately')
                    return redirect('student_semester_registered_courses')

                else:
                    # Iterate through selected_course_units_ids and create CourseEnrollment instances
                    for course_unit_id in selected_course_units_ids:
                        course_unit_instance = CourseUnit.objects.get(id=course_unit_id, academic_session=latest_academic_session)
                        registered_course_units.append(CourseEnrollment(
                            student=logged_user,
                            course_unit=course_unit_instance,
                            academic_session=latest_academic_session,
                            semester=current_semester,
                            level=student_level,
                            programme=student_programme,
                            added_by=logged_user
                        ))
                        # Increment total credit hours by the credit load of the current course unit
                        total_credit_hours += course_unit_instance.credit_load
                    
                    # Check if Admission Transaction was Success, count, and give a message
                    if registered_course_units:
                        CourseEnrollment.objects.bulk_create(registered_course_units)
                        registered_course_units_count += len(registered_course_units)
                        messages.success(request, f'You Registered {registered_course_units_count} Course Units with {total_credit_hours} Credit Hours, {student_level} for the {latest_academic_session} Academic Session.')
                        return redirect('student_semester_registered_courses')
                    else:
                        messages.error(request, 'No Course Unit was Registered')
                        return redirect('semester_course_registration')
                   
        except Exception as e:
            messages.error(request, f'Something went wrong: {str(e)}')

    else:
        messages.error(request, 'Invalid request method. Expected POST.')

    # Redirect back to the search page or an appropriate page
    return redirect('semester_course_registration')


# Display List of Course Units for Student Semester Course Unit for Update
@login_required(login_url='accounts_login')
def student_course_update(request):
    logged_user = request.user
    if not logged_user.profile.role == 'Student':
        messages.warning(request, 'You are not a Student')
        return redirect('accounts_login')

    try:
        student_instance = Student.objects.select_related('candidate', 'course').get(candidate=logged_user)
        student_current_level_name = student_instance.current_level
        latest_academic_session = student_instance.academic_session

        # Find the corresponding Level instance
        try:
            level_instance = Level.objects.get(name=student_current_level_name)
            student_current_level_id = level_instance.id
        except Level.DoesNotExist:
            messages.error(request, 'Level Not Found')
            return redirect('accounts_dashboard')

        student_program = student_instance.course  # This is a CourseOfStudy instance
        student_program_id = student_program.id 
    except Student.DoesNotExist:
        messages.error(request, 'Student Not Found')
        return redirect('accounts_dashboard')

    current_semester = require_active_semester(request, latest_academic_session)
    if not current_semester:
        return redirect('accounts_dashboard')

    # Get Student Course Units
    student_course_units = CourseUnit.objects.filter(
        academic_session=latest_academic_session,
        course_of_study_id=student_program_id, 
        semester=current_semester, 
        level_id=student_current_level_id
    )

    context = {
        'page_title': 'Course Reg. Update',
        'student_instance': student_instance,
        'current_semester': current_semester,
        'latest_academic_session': latest_academic_session,
        'student_course_units': student_course_units,
    }
    return render(request, 'academics/student_course_enrollment_update.html', context)


# Student Update course Enrollment
@login_required(login_url='accounts_login')
def update_course_enrollment(request):
    logged_user = request.user
    if not logged_user.profile.role == 'Student':
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_dashboard')

    # Get student instance
    try:
        student_instance = Student.objects.select_related('candidate').get(candidate=logged_user.id)
        student_programme = student_instance.course
        student_level = student_instance.current_level
        latest_academic_session = student_instance.academic_session
    except Student.DoesNotExist:
        messages.warning(request, 'Student Does Not Exist')
        return redirect('accounts_login')

    current_semester = require_active_semester(request, latest_academic_session)
    if not current_semester:
        return redirect('semester_course_registration')
    
    # Find the corresponding Current Student Level instance
    try:
        level_instance = Level.objects.get(name=student_level)
        student_current_level_id = level_instance.id
    except Level.DoesNotExist:
        messages.error(request, 'Level Not Found')
        return redirect('semester_course_registration')
    
    # Get Level Id from the student Level      
    try:
        student_level = Level.objects.get(id=student_current_level_id)
    except Level.DoesNotExist:
        messages.error(request, 'Level Not Found')
        return redirect('semester_course_registration')

    if request.method == 'POST':
        selected_course_units_ids = request.POST.getlist('selected_course_units[]')

        try:
            with transaction.atomic():
                updated_course_units = []
                new_course_units = []
                total_credit_hours = 0
                # Loop through the selected course units
                for course_unit_id in selected_course_units_ids:
                    course_unit_instance = CourseUnit.objects.get(id=course_unit_id, academic_session=latest_academic_session)

                    # Check if the course unit is already enrolled
                    existing_enrollment, created = CourseEnrollment.objects.get_or_create(
                        student=logged_user,
                        course_unit=course_unit_instance,
                        academic_session=latest_academic_session,
                        semester=current_semester,
                        level=student_level,
                        programme=student_programme,
                    )

                    if created:
                        new_course_units.append(existing_enrollment)
                    else:
                        # Update the existing enrollment other fields as needed
                        existing_enrollment.save()
                        updated_course_units.append(existing_enrollment)

                    total_credit_hours += course_unit_instance.credit_load

                if updated_course_units or new_course_units:
                    messages.success(request, f'You updated {len(updated_course_units)} Course Unit(s) and added {len(new_course_units)} new Course Units with {total_credit_hours} in {student_level} for the {latest_academic_session} Academic Session.')
                else:
                    messages.warning(request, 'No Course Unit was updated or added.')

        except Exception as e:
            messages.error(request, f'Something went wrong: {str(e)}')

    else:
        messages.error(request, 'Invalid request method. Expected POST.')

    return redirect('semester_course_registration')


@login_required(login_url='accounts_login')
def student_semester_registered_courses(request):
    logged_user = request.user
    if logged_user.profile.role not in {STUDENT_ROLE, GRADUATE_ROLE}:
        messages.warning(request, 'You are not a Student')
        return redirect('accounts_login')
    
    # Get student instance
    try:
        student_instance = Student.objects.select_related('candidate').get(candidate=logged_user.id)
        student_programme = student_instance.course
        student_current_level = student_instance.current_level
        latest_academic_session, session_message = resolve_student_academic_session(
            student_instance, logged_user
        )
        # student = student_instance.user
    except Student.DoesNotExist:
        messages.warning(request, 'Student Does Not Exist')
        return redirect('accounts_login')

    if logged_user.profile.role == GRADUATE_ROLE:
        enrollments = CourseEnrollment.objects.filter(
            student=logged_user,
            programme=student_programme,
        ).select_related('academic_session', 'semester', 'level', 'course_unit')
        selected_enrollment = None
        session_id = request.GET.get('session')
        semester_id = request.GET.get('semester')
        level_id = request.GET.get('level')
        if session_id and semester_id and level_id:
            selected_enrollment = enrollments.filter(
                academic_session_id=session_id,
                semester_id=semester_id,
                level_id=level_id,
            ).order_by('course_unit__course_code', 'id').first()
            if not selected_enrollment:
                messages.warning(request, 'The selected course-registration record was not found.')
        if not selected_enrollment:
            selected_enrollment = enrollments.order_by(
                '-academic_session__start_date', '-semester__date_resumption', '-id'
            ).first()

        history_options = list(
            enrollments.values(
                'academic_session_id', 'academic_session__name',
                'semester_id', 'semester__name', 'level_id', 'level__name',
            ).distinct().order_by(
                '-academic_session__start_date', '-semester__date_resumption', '-level__name'
            )
        )
        for option in history_options:
            option['key'] = (
                f"{option['academic_session_id']}:{option['semester_id']}:{option['level_id']}"
            )
        if not selected_enrollment:
            return render(request, 'academics/registered_semester_courses.html', {
                'student': student_instance,
                'student_instance': student_instance,
                'student_programme': student_programme,
                'page_title': 'Registered Courses',
                'student_reg_courses': CourseEnrollment.objects.none(),
                'history_options': history_options,
                'is_graduate': True,
            })

        latest_academic_session = selected_enrollment.academic_session
        current_semester = selected_enrollment.semester
        student_level = selected_enrollment.level
        student_reg_courses = enrollments.filter(
            academic_session=latest_academic_session,
            semester=current_semester,
            level=student_level,
        ).order_by('course_unit__course_code', 'id')
        check_carryover = CourseCarryover.objects.filter(
            student=logged_user,
            academic_session=latest_academic_session,
            semester=current_semester,
        ).select_related('course_unit')
        total_credit_load = (
            student_reg_courses.aggregate(total=Sum('course_unit__credit_load'))['total'] or 0
        )
        carryover_credit_hours = (
            check_carryover.aggregate(total=Sum('course_unit__credit_load'))['total'] or 0
        )
        historical_querystring = urlencode({
            'session': latest_academic_session.pk,
            'semester': current_semester.pk,
            'level': student_level.pk,
        })
        return render(request, 'academics/registered_semester_courses.html', {
            'student': student_instance,
            'student_instance': student_instance,
            'total_credit_hours': total_credit_load + carryover_credit_hours,
            'carryover_credit_hours': carryover_credit_hours,
            'check_carryover': check_carryover,
            'total_credit_load': total_credit_load,
            'current_semester': current_semester,
            'student_level': student_level,
            'student_programme': student_programme,
            'latest_academic_session': latest_academic_session,
            'page_title': 'Registered Courses',
            'student_reg_courses': student_reg_courses,
            'history_options': history_options,
            'historical_querystring': historical_querystring,
            'selected_history_key': (
                f'{latest_academic_session.pk}:{current_semester.pk}:{student_level.pk}'
            ),
            'is_graduate': True,
        })

    if not latest_academic_session:
        messages.warning(request, session_message)
        return redirect('semester_course_registration')

    current_semester, _semester_name = resolve_student_semester(
        student_instance, latest_academic_session, require_active=True
    )
    if not current_semester:
        messages.warning(request, f'No active semester found for {latest_academic_session}.')
        return redirect('semester_course_registration')
    current_semester_id = current_semester.id
    
    # Find the corresponding Current Student Level instance
    try:
        level_instance = Level.objects.get(name=student_current_level)
        student_current_level_id = level_instance.id
    except Level.DoesNotExist:
        messages.error(request, 'Level Not Found')
        return redirect('semester_course_registration')
    
    # Get Level Id from the student Level      
    try:
        student_level = Level.objects.get(id=student_current_level_id)
    except Level.DoesNotExist:
        messages.error(request, 'Level Not Found')
        return redirect('semester_course_registration')
    
    # Check if Student is NOT HNDI or NDI and get the previous level
    if student_current_level == 'HNDII' or student_current_level == 'NDII':
        # Get Previous Level using the Student instance
        previous_level = student_instance.get_previous_level()
        
        # Get Previous Level Instance ID
        try:
            previous_level_instance = Level.objects.get(name=previous_level)
        except Level.DoesNotExist:
            messages.warning(request, 'Level NOT Found')
            return redirect('student_carryover_courses')
    
        # Check if Student has Registered for Carry Over Course
        try:
            check_carryover = CourseCarryover.objects.select_related('student').filter(student=logged_user,
                                                                                    academic_session=latest_academic_session,
                                                                                    semester=current_semester,
                                                                                    level=previous_level_instance)
        except CourseCarryover.DoesNotExist:
            messages.warning(request, 'You did NOT Register for any Carryover')
        
        
        # Get Registered Courses for Logged in Student
        student_reg_courses = CourseEnrollment.objects.select_related('student').filter(student=logged_user,
                                                                                        programme=student_programme,
                                                                                        academic_session=latest_academic_session,
                                                                                        level=student_level,
                                                                                        semester=current_semester_id)
        
        # Get the total credit load for the current session and semester
        total_credit_load = CourseEnrollment.calculate_total_credit_load(
            user=logged_user,
            current_session=latest_academic_session,
            current_semester=current_semester,
            program=student_programme,
            level=student_level
        )
        # Calculate Total Credit Hours of registered Carryover course units
        carryover_credit_hours = CourseCarryover.calculate_carryover_credit_hours(logged_user,
                                                                                current_semester_id,
                                                                                latest_academic_session,
                                                                                previous_level_instance)

        # Add the carryover and the current course reg. credit hours
        total_credit_hours = total_credit_load + carryover_credit_hours

        context = {
            'student': student_instance,
            'previous_level': previous_level,
            'total_credit_hours': total_credit_hours,
            'carryover_credit_hours': carryover_credit_hours,
            'check_carryover': check_carryover,
            'total_credit_load': total_credit_load,
            'current_semester': current_semester,
            'student_level': student_level, 
            'student_programme': student_programme,
            'latest_academic_session': latest_academic_session,
            'page_title': 'Registered Courses',
            'student_reg_courses': student_reg_courses,
        }
        return render(request, 'academics/registered_semester_courses.html', context)
    else:
        # Get Registered Courses for Logged in Student
        student_reg_courses = CourseEnrollment.objects.select_related('student').filter(student=logged_user,
                                                                                        programme=student_programme,
                                                                                        academic_session=latest_academic_session,
                                                                                        level=student_level,
                                                                                        semester=current_semester_id)
        
        # Get the total credit load for the current session and semester
        total_credit_load = CourseEnrollment.calculate_total_credit_load(
            user=logged_user,
            current_session=latest_academic_session,
            current_semester=current_semester,
            program=student_programme,
            level=student_level
        )
        context = {
            'student': student_instance,
            'total_credit_load': total_credit_load,
            'current_semester': current_semester,
            'student_level': student_level, 
            'student_programme': student_programme,
            'latest_academic_session': latest_academic_session,
            'page_title': 'Registered Courses',
            'student_reg_courses': student_reg_courses,
        }
        return render(request, 'academics/registered_semester_courses.html', context)


# Student Semester Course Registration Back page view
@login_required(login_url='accounts_login')
def student_course_reg_backpage(request):
    logged_user = request.user
    if logged_user.profile.role not in {STUDENT_ROLE, GRADUATE_ROLE}:
        messages.warning(request, 'You are not a Student')
        return redirect('accounts_login')

    if logged_user.profile.role == GRADUATE_ROLE:
        student = get_object_or_404(Student.objects.select_related('course'), candidate=logged_user)
        owned_enrollments = CourseEnrollment.objects.filter(
            student=logged_user,
            programme=student.course,
        ).select_related('academic_session', 'semester', 'level', 'course_unit')
        selected = owned_enrollments.filter(
            academic_session_id=request.GET.get('session'),
            semester_id=request.GET.get('semester'),
            level_id=request.GET.get('level'),
        ).first()
        if not selected:
            messages.warning(request, 'The selected historical registration record was not found.')
            return redirect('student_semester_registered_courses')
        student_course_enrollment = owned_enrollments.filter(
            academic_session=selected.academic_session,
            semester=selected.semester,
            level=selected.level,
        )
        return render(request, 'academics/course_reg_back_page.html', {
            'student': student,
            'student_course_enrollment': student_course_enrollment,
            'current_session': selected.academic_session,
            'current_semester': selected.semester,
            'student_level': selected.level,
            'page_title': 'Course Reg. Back Page',
            'is_graduate': True,
        })
    
    # Get Recent Academic Session
    latest_session = AcademicSession.objects.order_by('-start_date', '-end_date', '-name').first()

    try:
        # Retrieve the student profile
        student = get_object_or_404(Student, candidate=logged_user)
        
    except Student.DoesNotExist:
        messages.warning(request, 'Student Not Found')
        return redirect('accounts_login')
    # Get Student profile
    profile = student.candidate.profile

    # Use the indigene_status method
    indigence = profile.indigene_status()
    indigene_type = get_user_indigene_type(logged_user)

    # Retrieve current academic info
    current_level = student.current_level
    current_session = student.academic_session
    current_semester = student.semester
    student_program = student.course
    student_level = student.current_level

    # Retrieve Current Semester Instance
    try:
        current_semester_instance = Semester.objects.get(name=current_semester, academic_session=current_session)
        semester_instance = current_semester_instance.id
    except Semester.DoesNotExist:
        messages.warning(request, 'Semester Does Not Exist')

    payment_session, _session_message = resolve_student_academic_session(student, logged_user)

    # Calculate approved school fees paid in the applicable registration session
    total_paid_current_session = Payment.objects.filter(
        user=student.candidate,
        academic_sess=payment_session,
        payment_source='School Fees',
        indigene_type=indigene_type,
        verified=True,
        status='Approved',
    ).aggregate(Sum('amount'))['amount__sum'] or Decimal('0')

    # Calculate outstanding fees
    total_paid = Payment.objects.filter(user=student.candidate, academic_sess=current_session,
                                        payment_source='School Fees', indigene_type=indigene_type).aggregate(Sum('amount'))['amount__sum'] or 0

    # Get Student Full Session School Fee
    full_session_fee_amount = get_school_fee_amount(
        current_level,
        'Full Session',
        current_session,
        indigene_type,
        course_of_study=student.course,
    )
    
    # Get Student Semester Fee
    current_semester_fee_amount = get_school_fee_amount(
        current_level,
        current_semester,
        current_session,
        indigene_type,
        course_of_study=student.course,
    )

    # Calculate Student Outstanding amount
    outstanding_amount = max(full_session_fee_amount - total_paid, Decimal('0'))

    # Find the corresponding Current Student Level instance
    try:
        level_instance = Level.objects.get(name=student_level)
        student_current_level_id = level_instance.id
    except Level.DoesNotExist:
        messages.error(request, 'Level Not Found')
        return redirect('semester_course_registration')
    
    # Get Level Id from the student Level      
    try:
        student_level = Level.objects.get(id=student_current_level_id)
    except Level.DoesNotExist:
        messages.error(request, 'Level Not Found')
        return redirect('semester_course_registration')

    # Get Registered Courses for Logged in Student
    student_reg_courses = CourseEnrollment.objects.filter(
                                                        Q(student=logged_user) &  # Using Q for better readability and flexibility
                                                        Q(programme=student_program) &
                                                        Q(academic_session=latest_session) &
                                                        Q(level=student_level) &
                                                        Q(semester=student_current_level_id)
                                                    ).exists()
    payments = Payment.objects.filter(
                user=logged_user,
                payment_source__in=['Acceptance Fee', 'Screening Fee', 'Application Form', 'School Fees'],
                academic_sess=current_session,
                indigene_type=indigene_type,
            )
    # Check for the existence of specific fee payments
    has_screening_fee_payment = payments.filter(payment_source='Screening Fee').exists()
    has_acceptance_fee_payment = payments.filter(payment_source='Acceptance Fee').exists()
    has_application_fee_payment = payments.filter(payment_source='Application Form').exists()
    has_school_fee_payment = payments.filter(payment_source='School Fee').exists()
    has_other_school_fee_payments = payments.filter(payment_source='School Fee').exists()
    
    # Find the corresponding Current Student Level instance
    try:
        level_instance = Level.objects.get(name=current_level)
        student_current_level_id = level_instance.id
    except Level.DoesNotExist:
        messages.error(request, 'Level Not Found')
        return redirect('accounts_dashboard')

    # Filter Student current Semester and Level Course Enrollment
    student_course_enrollment = CourseEnrollment.objects.filter(
                                                            student=logged_user,
                                                            semester=semester_instance, academic_session=current_session,
                                                            programme=student_program, level=student_current_level_id
                                                        )
    
    context = {
        'student': student,
        'student_reg_courses': student_reg_courses,
        'total_paid_current_session': total_paid_current_session,
        'has_other_school_fee_payments': has_other_school_fee_payments,
        'has_school_fee_payment': has_school_fee_payment,
        'has_application_fee_payment': has_application_fee_payment,
        'has_acceptance_fee_payment': has_acceptance_fee_payment,
        'has_screening_fee_payment': has_screening_fee_payment,
        'payments': payments,
        'student_course_enrollment': student_course_enrollment,
        'page_title': 'Course Reg. Back Page',
    }
    return render(request, 'academics/course_reg_back_page.html', context)

# SEARCH STUDENT SEMESTER COURSE REGISTRATION FORM
@login_required(login_url='accounts_login')
def admin_search_registered_semester_student(request):
    # Ensure the user is a superuser or has the 'Admin' or 'Super Admin' role
    if not (request.user.is_superuser or request.user.profile.role in ['Admin', 'Super Admin']):
        messages.warning(request, 'You do not have permission to view this page.')
        return redirect('accounts_login')

    form = SearchStudentCourseReg(request.GET or None)

    if request.method == "GET" and form.is_valid():
        student_id = form.cleaned_data['search_query']

        # Search for the student by Student ID in the Student model and check course enrollment
        try:
            student = Student.objects.get(student_id__icontains=student_id)
        except Student.DoesNotExist:
            messages.warning(request, 'No student found with the provided Student ID.')
            return redirect('admin_search_registered_semester_student')

        current_session = student.academic_session
        current_semester = require_active_semester(request, current_session)
        if not current_semester:
            return redirect('accounts_login')
        
        registered_students = CourseEnrollment.objects.filter(
            student=student.candidate,  # Link from Student model to User model via 'candidate'
            academic_session=current_session,
            semester=current_semester
        ).select_related('course_unit', 'student')

        if registered_students.exists():
            # Log user activity
            UserActivityLog.objects.create(user=request.user, action=f'Searched for Student with ID: {student_id}')
            return redirect('admin_view_registered_student_courses', student_id=student.student_id)
        else:
            messages.warning(request, 'No Course Registration for the Current Academic Session and Semester Found.')

    # If not GET or invalid form, reload the form and show recent registrations
    form = SearchStudentCourseReg()

    registered_students = CourseEnrollment.objects.order_by('-added_date')[:5]
    number_of_registration = registered_students.count()

    context = {
        'page_title': 'Registered Students',
        'registered_students': registered_students,
        'number_of_registration': number_of_registration,
        'form': form,
    }
    return render(request, 'academics/search_registered_student.html', context)

@login_required(login_url='accounts_login')
def admin_view_registered_student_courses(request, student_id):
    logged_user = request.user
    if not (logged_user.profile.role == 'Super Admin' or logged_user.is_superuser or logged_user.profile.role == 'Developer' or logged_user.profile.role == 'Admin'):
        messages.warning(request, 'You are not a Student')
        return redirect('accounts_login')
    
    # Get student instance
    try:
        student_instance = Student.objects.select_related('candidate').get(student_id=student_id)
        student_programme = student_instance.course
        student_current_level = student_instance.current_level
        current_session = student_instance.academic_session
        # student = student_instance.user
    except Student.DoesNotExist:
        messages.warning(request, 'Student Does Not Exist')
        return redirect('accounts_login')

    current_semester = require_active_semester(request, current_session)
    if not current_semester:
        return redirect('semester_course_registration')
    current_semester_id = current_semester.id
    
    # Find the corresponding Current Student Level instance
    try:
        level_instance = Level.objects.get(name=student_current_level)
        student_current_level_id = level_instance.id
    except Level.DoesNotExist:
        messages.error(request, 'Level Not Found')
        return redirect('semester_course_registration')
    
    # Get Level Id from the student Level      
    try:
        student_level = Level.objects.get(id=student_current_level_id)
    except Level.DoesNotExist:
        messages.error(request, 'Level Not Found')
        return redirect('semester_course_registration')
    
    # Check if Student is NOT HNDI or NDI and get the previous level
    if student_current_level == 'HNDII' or student_current_level == 'NDII':
        # Get Previous Level using the Student instance
        previous_level = student_instance.get_previous_level()
        
        # Get Previous Level Instance ID
        try:
            previous_level_instance = Level.objects.get(name=previous_level)
        except Level.DoesNotExist:
            messages.warning(request, 'Level NOT Found')
            return redirect('student_carryover_courses')
    
        # Check if Student has Registered for Carry Over Course
        try:
            check_carryover = CourseCarryover.objects.select_related('student').filter(student=student_instance.candidate,
                                                                                    academic_session=current_session,
                                                                                    semester=current_semester,
                                                                                    level=previous_level_instance)
        except CourseCarryover.DoesNotExist:
            messages.warning(request, 'You did NOT Register for any Carryover')
        
        
        # Get Registered Courses for Logged in Student
        student_reg_courses = CourseEnrollment.objects.select_related('student').filter(student=student_instance.candidate,
                                                                                        programme=student_programme,
                                                                                        academic_session=current_session,
                                                                                        level=student_level,
                                                                                        semester=current_semester_id)
        
        # Get the total credit load for the current session and semester
        total_credit_load = CourseEnrollment.calculate_total_credit_load(
            user=student_instance.candidate,
            current_session=current_session,
            current_semester=current_semester,
            program=student_programme,
            level=student_level
        )
        # Calculate Total Credit Hours of registered Carryover course units
        carryover_credit_hours = CourseCarryover.calculate_carryover_credit_hours(student_instance.candidate,
                                                                                current_semester_id,
                                                                                current_session,
                                                                                previous_level_instance)

        # Add the carryover and the current course reg. credit hours
        total_credit_hours = total_credit_load + carryover_credit_hours

        context = {
            'student_id': student_id,
            'student_instance': student_instance,
            'previous_level': previous_level,
            'total_credit_hours': total_credit_hours,
            'carryover_credit_hours': carryover_credit_hours,
            'check_carryover': check_carryover,
            'total_credit_load': total_credit_load,
            'current_semester': current_semester,
            'student_level': student_level, 
            'student_programme': student_programme,
            'latest_academic_session': current_session,
            'page_title': 'Registered Courses',
            'student_reg_courses': student_reg_courses, 
        }
        return render(request, 'academics/admin_student_registration.html', context)
    else:
        # Get Registered Courses for Logged in Student
        student_reg_courses = CourseEnrollment.objects.select_related('student').filter(student=student_instance.candidate,
                                                                                        programme=student_programme,
                                                                                        academic_session=current_session,
                                                                                        level=student_level,
                                                                                        semester=current_semester_id)
        
        # Get the total credit load for the current session and semester
        total_credit_load = CourseEnrollment.calculate_total_credit_load(
            user=student_instance.candidate,
            current_session=current_session,
            current_semester=current_semester,
            program=student_programme,
            level=student_level
        )
        context = {
            'student_id': student_id,
            'student_instance': student_instance,
            'total_credit_load': total_credit_load,
            'current_semester': current_semester,
            'student_level': student_level, 
            'student_programme': student_programme,
            'latest_academic_session': current_session,
            'page_title': 'Admin Student Courses',
            'student_reg_courses': student_reg_courses,
        }
        return render(request, 'academics/admin_student_registration.html', context)

# ADMIN RESET STUDENT COURSE REGISTRATION FORM
@login_required(login_url='accounts_login')
def delete_student_semester_course_reg(request, student_id):
    # Ensure only authorized users can access the view
    if not (request.user.profile.role in ['Super Admin', 'Admin'] or request.user.is_superuser):
        messages.warning(request, 'You do not have permission to perform this action.')
        return redirect('accounts_login')
    
    # Get the student instance
    student = get_object_or_404(Student, student_id=student_id)
    current_session = student.academic_session
    current_semester = require_active_semester(request, current_session)
    if not current_semester:
        return redirect('admin_search_registered_semester_student')

    # Filter and delete the student's courses for the current session and semester
    deleted_courses = CourseEnrollment.objects.filter(
        student=student.candidate,
        academic_session=current_session,
        semester=current_semester
    ).delete()

    if deleted_courses[0] > 0:
        # Log user activity
        UserActivityLog.objects.create(user=request.user, action=f'Deleted all courses for {student.candidate.profile.first_name} {student.candidate.profile.last_name} in current session and semester')
        messages.success(request, f'All courses for {student.candidate.profile.first_name} {student.candidate.profile.last_name}  for the current Session and Semester have been successfully deleted.')
    else:
        messages.warning(request, f'No courses found for {student.candidate.profile.first_name} {student.candidate.profile.last_name} in the current session and semester.')

    return redirect('admin_search_registered_semester_student')



# Promote Student to Next Level view
@login_required(login_url='accounts_login')
def search_promote_student_level(request):
    logged_user = request.user
    allowed_roles = ['ADMISSION-OFFICER', 'Super Admin', 'Developer']
    if not (logged_user.profile.role in allowed_roles  or logged_user.is_superuser ):
        messages.warning(request, 'Something Went Wrong.')
        return redirect('accounts_login')
    
    form = StudentsPromotionForm(request.GET or None)
    if request.method == 'GET' and form.is_valid():
        programme = form.cleaned_data['course_of_study']
        level = form.cleaned_data['level']

        display_students = Student.objects.filter(course=programme, current_level=level)

        context = {
            'page_title': f'{level} Students',
            'display_students': display_students,
            'programme': programme,
            'level': level,
        }
        return render(request, 'academics/display_students_level.html', context)

    context = {
        'form': form,
        'page_title': 'Students Promotion',
    }
    return render(request, 'academics/search_students_bylevel.html', context)


# Student Update Record for Next Level Promotion
@login_required(login_url='accounts_login')
def promote_students(request):
    logged_user = request.user
    allowed_roles = ['ADMISSION-OFFICER', 'Super Admin', 'Developer']
    if not (logged_user.profile.role in allowed_roles  or logged_user.is_superuser ):
        messages.warning(request, 'Something Went Wrong.')
        return redirect('accounts_login')

    # Get the latest Academic Session
    latest_academic_session = AcademicSession.objects.order_by('-start_date', '-end_date', '-name').first()

    if request.method == 'POST':
        selected_student_ids = request.POST.getlist('selected_students[]')

        try:
            with transaction.atomic():
                promoted_students = []
                
                for student_id in selected_student_ids:
                    student_instance = Student.objects.get(id=student_id)
                    
                    # Determine the next level based on the student's current level
                    if student_instance.current_level == 'NDI':
                        next_level = 'NDII'
                    else:
                        next_level = 'HNDII'

                    # Check if the student is already promoted in the current session
                    is_promoted = Student.objects.filter(
                        candidate=student_instance.candidate,
                        academic_session=latest_academic_session,
                        current_level=next_level
                    ).exists()

                    if not is_promoted:
                        # Update the existing student instance with new level
                        student_instance.current_level = next_level
                        student_instance.academic_session = latest_academic_session
                        student_instance.save()
                        promoted_students.append(student_instance)

                if promoted_students:
                    messages.success(request, f'You Promoted {len(promoted_students)} students to the {next_level}.')
                    
                    return redirect('recently_promoted', new_level=next_level, academic_sess=latest_academic_session.id)
                else:
                    messages.warning(request, 'No student was promoted.')

        except Exception as e:
            messages.error(request, f'Something went wrong: {str(e)}')

    else:
        messages.error(request, 'Invalid request method. Expected POST.')

    return redirect('search_promote_student_level')


# Newly Promoted Students Level
@login_required(login_url='accounts_login')
def recently_promoted_students(request, new_level, academic_sess):
    logged_user = request.user
    allowed_roles = ['ADMISSION-OFFICER', 'Super Admin', 'Developer']
    if not (logged_user.profile.role in allowed_roles  or logged_user.is_superuser ):
        messages.warning(request, 'Something Went Wrong.')
        return redirect('accounts_login')
    
    try:
        # Get Program and compare
        students_instance = Student.objects.filter(current_level=new_level, academic_session=academic_sess)
    except Student.DoesNotExist:
        messages.warning(request, 'Students Does Not Exist')
        return redirect('search_promote_student_level')
    
    context = {
        'page_title': f'{new_level} Students',
        'students_instance': students_instance,
        'new_level': new_level,
    }
    return render(request, 'academics/promoted_students.html', context)


# Search and Demote Student level
@login_required(login_url='accounts_login')
def search_student(request):
    logged_user = request.user
    allowed_roles = ['ADMISSION-OFFICER', 'Super Admin', 'Developer']
    if not (logged_user.profile.role in allowed_roles  or logged_user.is_superuser ):
        messages.warning(request, 'Something Went Wrong.')
        return redirect('accounts_login')
    
    form = SearchStudentForm(request.GET or None)
    if request.method == 'GET' and form.is_valid():
        student_id = form.cleaned_data['student_id']

        try:
            student = Student.objects.select_related('candidate').get(student_id=student_id)
            if student:
                context = {
                    'student': student,
                    'page_title': 'Student Details',
                }
                return render(request, 'academics/student_detail.html', context)
        except Student.DoesNotExist:
            messages.warning(request, 'Student Does Not Exists')  
    else:
        form = SearchStudentForm()

    context = {
        'form': form,
        'page_title': 'Search Student',
    }
    return render(request, 'academics/search_student.html', context)


# Demote Student
@login_required(login_url='accounts_login')
def demote_student(request, student_id):
    logged_user = request.user
    allowed_roles = ['ADMISSION-OFFICER', 'Super Admin', 'Developer']
    if not (logged_user.profile.role in allowed_roles  or logged_user.is_superuser ):
        messages.warning(request, 'Something Went Wrong.')
        return redirect('accounts_login')
    
    try:
        student_instance = Student.objects.get(id=student_id)
        if student_instance:
            # Determine Previous Level for Demotion
            if student_instance.current_level == 'HNDII':
                previous_level = 'HNDI'
            else:
                previous_level = 'NDI'

            student_instance.current_level = previous_level
            student_instance.save()
            messages.success(request, f'{student_instance.candidate.profile.last_name} Demoted Successfully.')
            context = {
                'student':student_instance,
                'page_title': 'Demotion Details',
            }
            return render(request, 'academics/student_detail.html', context)
      
    except Student.DoesNotExist:
        messages.warning(request, 'Student NOT Found')
        return redirect('search_student')
    

# STUDENT PREVIOUS ACADEMIC RECORDS
@login_required(login_url='accounts_login')
def student_previous_academic_record(request):
    logged_user = request.user
    if not logged_user.profile.role == 'Student':
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_dashboard')

    # Get student instance
    try:
        student_instance = Student.objects.select_related('candidate').get(candidate=logged_user.id)
        student_programme = student_instance.course
        student_current_level = student_instance.current_level
        latest_academic_session = student_instance.academic_session
        # student = student_instance.user
    except Student.DoesNotExist:
        messages.warning(request, 'Student Does Not Exist')
        return redirect('accounts_login')

    current_semester = require_active_semester(request, latest_academic_session)
    if not current_semester:
        return redirect('semester_course_registration')
    current_semester_id = current_semester.id
    
    # Find the corresponding Current Student Level instance
    try:
        level_instance = Level.objects.get(name=student_current_level)
        student_current_level_id = level_instance.id
    except Level.DoesNotExist:
        messages.error(request, 'Level Not Found')
        return redirect('accounts_dashboard')

    
    # Check if Student is NOT HNDI or NDI and get the previous level
    if student_current_level == 'HNDII' or student_current_level == 'NDII':
        # Get Previous Level using the Student instance
        previous_level = student_instance.get_previous_level()

        # Determine previous academic session from the latest Session
        previous_session = latest_academic_session.get_previous_academic_session()
        
        # Determine Previous Semester
        previous_semester = current_semester.get_previous_semester()

        # Get Previous Level Instance ID
        try:
            previous_level_instance = Level.objects.get(name=previous_level)
            
            # Get Previously Registered Courses for Logged in Student
            previous_student_reg_courses = CourseEnrollment.objects.select_related('student').filter(student=logged_user,
                                                                                        programme=student_programme,
                                                                                        academic_session=previous_session,
                                                                                        level=previous_level_instance,
                                                                                        semester=previous_semester.id)
        except Level.DoesNotExist:
            messages.warning(request, 'Level NOT Found')
            return redirect('accounts_dashboard')
            
        context = {
            'latest_academic_session': latest_academic_session,
            'student': student_instance,
            'previous_level': previous_level,
            'current_semester': current_semester,
            'student_programme': student_programme,
            'previous_session': previous_session,
            'page_title': 'Registered Courses',
            'previous_student_reg_courses': previous_student_reg_courses,
        }
        return render(request, 'academics/student_previous_course_records.html', context)
    

# POST-UTME Template File Upload View
@login_required(login_url='accounts_login')
def upload_postutme_file(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'Admin' or logged_user.profile.role == 'Super Admin' or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    if request.method == 'POST':
        form = CSVSampleUploadForm(request.POST, request.FILES)
        try:
            if form.is_valid():
                name = form.cleaned_data['name']
                csv_file = form.cleaned_data['csv_file']

                # Check if a template with the same name already exists
                try:
                    existing_template = Postutmlformat.objects.get(name=name)
                    messages.warning(request, 'A template with this name already exists.')
                except Postutmlformat.DoesNotExist:
                    # Save the form data
                    form.save()
                    messages.success(request, 'POST-UTME Template Uploaded Successfully.')
                    return redirect('post_utme_templates')
        except ValidationError as e:
            messages.error(request, f'Validation error: {e}')

    else:
        form = CSVSampleUploadForm()

    context = {
        'form': form,
        'page_title': 'POST-UTME Template Upload',
    }

    return render(request, 'academics/post_utme_template.html', context)


# POST-UTME Candidate Upload View
@login_required(login_url='accounts_login')
def post_utme_templates(request):
    logged_user = request.user
    if not (logged_user.is_superuser or logged_user.profile.role == 'Super Admin' or logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.profile.role == 'Admin'):
        messages.warning(request, 'Something went wrong')
        return redirect('accounts_login')
    
    post_utme_templates = Postutmlformat.objects.order_by('-uploaded_date')[:1]
    context = {
        'templates': post_utme_templates,
        'page_title': 'POST-UTME Candidate Templates',
    }
    return render(request, 'academics/post_utme_templates.html', context)


# UPDATE POST-UTME TEMPLATE
@login_required(login_url='accounts_login')
def update_post_utme_template(request, id):
    logged_user = request.user
    if not (logged_user.is_superuser or logged_user.profile.role in ['Super Admin', 'ADMISSION-OFFICER', 'Admin']):
        messages.warning(request, 'Something went wrong')
        return redirect('accounts_login')

    try:
        # Retrieve the object by ID
        post_utme_template = get_object_or_404(Postutmlformat, id=id)
        
        if request.method == 'POST':
            # Create a form instance with the submitted data
            form = CSVSampleUploadForm(request.POST, request.FILES, instance=post_utme_template)
            
            # Validate the form
            if form.is_valid():
                # Save the updated form data to the database
                form.save()
                # Display a success message
                messages.success(request, 'Template updated successfully.')
                # Redirect to the list view
                return redirect('post_utme_templates')
            else:
                # Display an error message if the form is not valid
                messages.error(request, 'Failed to update template. Please ensure the file is valid.')
        else:
            # Create a form instance with the existing data
            form = CSVSampleUploadForm(instance=post_utme_template)
        context = {
            'form': form,
            'template': post_utme_template,
        }
        # Render the template with the form
        return render(request, 'academics/post_utme_template.html', context)
    except Postutmlformat.DoesNotExist:
        # Display an error message if the object does not exist
        messages.error(request, 'Template not found.')
        return redirect('post_utme_templates')


# Download POST-UTME Candidate Upload Template
@login_required(login_url='accounts_login')
def download_csv_file(request, template_id):
    logged_user = request.user
    if not (logged_user.profile.role == 'Admin' or logged_user.profile.role == 'Super Admin' or logged_user.is_superuser or logged_user.profile.role == 'ADMISSION-OFFICER'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    template = get_object_or_404(Postutmlformat, pk=template_id)

    # Set the file response headers
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{template.name}.csv"'

    # Write the file content to the response
    response.write(template.csv_file.read())

    # Add success message
    messages.success(request, 'Template Downloaded Successfully.')

    return response
    

# Staff Publications view
@login_required(login_url='accounts_login')
def staff_publication(request): 
    logged_user = request.user
    profile = logged_user.profile
    if not (logged_user.profile.role == 'Staff'):
        messages.warning(request, 'You are not a Staff')
        return redirect('accounts_login')
    # Check if Staff has completed registration
    if not StaffRegister.objects.filter(user=logged_user).exists():
        messages.warning(request, 'You Need to Complete your Registration First.')
        return redirect('staff_register')
    
    # Get Staff user instance
    staff_register_instance = StaffRegister.objects.get(user=logged_user)
    
    # Get Staff publications using Staff user instance
    staff_publication = StaffPublication.objects.select_related('staff').filter(staff=staff_register_instance).order_by('-submission_date')
    if request.method == 'POST':
        form = StaffPublicationCreationForm(request.POST)
        if form.is_valid():
            publication_existence = form.cleaned_data['publication_existence']
            publication_platform = form.cleaned_data['publication_platform']
            publication_reference = form.cleaned_data['publication_reference']
            publication_url = form.cleaned_data['publication_url']

            pubulication = StaffPublication.objects.create(
                    staff=staff_register_instance,
                    publication_existence=publication_existence,
                    publication_platform=publication_platform,
                    publication_reference=publication_reference,
                    staff2 = profile,
                    publication_url=publication_url
                   )
            
            if pubulication:
                messages.success(request, 'Publication Added Successfully.')
    else:
        form = StaffPublicationCreationForm()
    context = {
        'staff_publication': staff_publication,
        'form': form,
        'page_title': 'Add Staff Publication',
    }
    return render(request, 'academics/staff_publication.html', context)


# Staff Add Qualification Record
@login_required(login_url='accounts_login')
def staff_academic_staff_record(request):
    logged_user = request.user
    # Get Logged user Profile
    profile = logged_user.profile
    if not logged_user.profile.role == 'Staff':
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    # Get Registered Staff
    try:
        register_staff = StaffRegister.objects.select_related('user').get(user_id=logged_user.id)
    except StaffRegister.DoesNotExist:
        messages.warning(request, 'Registered Staff Not Found')
        return redirect('accounts-login')

    # Check if Staff has added Academic Record before
    try:
        academic_staff = AcademicStaff.objects.select_related('staff').get(staff=register_staff)
        messages.warning(request, 'Staff already Added Academic Record')
        return redirect('staff_reg_preview')
    except AcademicStaff.DoesNotExist:
    
        if request.method == 'POST':
            form = SuperAdminAcademicStaffCreationForm(request.POST, request.FILES)
            if form.is_valid():
                qualification_date = form.cleaned_data['qualification_date']
                current_rank = form.cleaned_data['current_rank']
                internal_appointment = form.cleaned_data['internal_appointment']
                staff_type = form.cleaned_data['staff_type']
                work_experience = form.cleaned_data['work_experience']
                courses_taught = form.cleaned_data['courses_taught']
                upload_cv = form.cleaned_data['upload_cv']
                    
                # Fetch Staff Publications for the staff member
                staff_publications = StaffPublication.objects.filter(staff=register_staff)
                    
                academic_staff_record = AcademicStaff.objects.create(
                        staff=register_staff,
                        qualification_date=qualification_date,
                        current_rank=current_rank, internal_appointment=internal_appointment,
                        staff_type=staff_type,
                        work_experience=work_experience,
                        courses_taught=courses_taught,
                        staff2=profile,
                        upload_cv=upload_cv
                    )
                    
                # Add publications to academic staff record
                academic_staff_record.staff_publications.set(staff_publications)
                    
                messages.success(request, 'Academic Staff Record Added Successfully.')
                return redirect('staff_reg_preview')
        else:
            form = SuperAdminAcademicStaffCreationForm()
            
        context = {
                'page_title': 'Staff Add Academic Record',
                'form': form,
        }
        return render(request, 'academics/academic_staff_record.html', context)
    

# Super Admin Add Staff Publications view
@login_required(login_url='accounts_login')
def superadmin_add_staff_publication(request, staff_id):  
    logged_user = request.user
    if not (logged_user.profile.role == 'Super Admin' or logged_user.profile.role == 'ADMISSION-OFFICER'  or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    try:
        staff = StaffRegister.objects.select_related('user').get(user_id=staff_id)
        staff_instance = staff.user
        profile = staff_instance.profile
        # Register user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Visited Add Staff Publication Page for {staff.user.profile.first_name} {staff.user.profile.last_name} .')
        # Check if Staff has completed registration
        if not StaffRegister.objects.filter(user=staff_instance).exists():
            messages.warning(request, 'You Need to Complete Staff Registration First.')
            return redirect('special_staff_register')
        
        # Get Staff publications using Staff user instance
        staff_publication = StaffPublication.objects.select_related('staff').filter(staff=staff).order_by('-submission_date')
        if request.method == 'POST':
            form = StaffPublicationCreationForm(request.POST)
            if form.is_valid():
                publication_existence = form.cleaned_data['publication_existence']
                publication_platform = form.cleaned_data['publication_platform']
                publication_reference = form.cleaned_data['publication_reference']
                publication_url = form.cleaned_data['publication_url']

                pubulication = StaffPublication.objects.create(
                        staff=staff,
                        publication_existence=publication_existence,
                        publication_platform=publication_platform,
                        publication_reference=publication_reference,
                        staff2=profile,
                        publication_url=publication_url, submitted_by=logged_user
                    )
                
                if pubulication:
                    # Register user activity
                    UserActivityLog.objects.create(user=logged_user, action=f'Added {publication_platform} Publication for {staff.user.profile.first_name} {staff.user.profile.last_name}  ')
                    messages.success(request, f'Publication Added Successfully for {staff.user.profile.first_name} .')
        else:
            form = StaffPublicationCreationForm()
        context = {
            'staff_id': staff_id,
            'staff_publication': staff_publication,
            'form': form,
            'page_title': 'Add Staff Publication',
        }
        return render(request, 'academics/super_admin_publication.html', context)
    except StaffRegister.DoesNotExist:
        messages.warning(request, 'Staff Does Not Exist')
        return redirect('accounts_login')


# Super User View List of Staff Publication for Updating
@login_required(login_url='accounts_login')
def super_admin_edit_staff_publications(request, staff_id):
    logged_user = request.user
    if not (logged_user.is_superuser or logged_user.profile.role == 'Super Admin' or logged_user.profile.role == 'ADMISSION-OFFICER'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    # Check Staff Register
    try:
        registered_staff = StaffRegister.objects.select_related('user').get(user_id=staff_id)
        #Filter the Staff Publication Table
        staff_publications = StaffPublication.objects.select_related('staff').filter(staff=registered_staff).order_by('-submission_date')
        # Register user activity
        UserActivityLog.objects.create(user=logged_user, action=f'Visited Edit Staff Publication for {registered_staff.user.profile.first_name} {registered_staff.user.profile.last_name} ')
        
        context = {
            'staff_id': staff_id,
            'page_title': 'Edit Staff Publication',
            'staff_publication': staff_publications,
        }
        return render(request, 'academics/super_admin_publication.html', context)
    
    except StaffRegister.DoesNotExist:
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')


# Super User Update Staff Publication
@login_required(login_url='accounts_login')
def super_admin_update_staff_publication(request, publication_id):
    logged_user = request.user
    if not (logged_user.is_superuser or logged_user.profile.role == 'Super Admin' or logged_user.profile.role == 'ADMISSION-OFFICER'):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    # Retrieve the publication or return 404 if not found
    publication = get_object_or_404(StaffPublication, id=publication_id)

    # Assuming you have a form for editing StaffPublication
    form = StaffPublicationCreationForm(request.POST or None, instance=publication)

    if request.method == 'POST':
        if form.is_valid():
            form.save()
            # Register user activity
            UserActivityLog.objects.create(user=logged_user, action=f'Edited {publication.staff.user.profile.first_name} {publication.staff.user.profile.last_name} Staff Publication ')
            # Get Staff ID from the publication relationship with user
            staff_id = publication.staff.user.id
            
            messages.success(request, 'Publication updated successfully.')
            
            return redirect('super_admin_edit_staff_publications', staff_id)  # Redirect to a success URL after updating publication
        else:
            messages.error(request, 'There was an error. Please correct the form.')
    else:
        form = StaffPublicationCreationForm(instance=publication)
    context = {
        'form': form,
        'page_title': 'Update Staff Publication',
        }
    # Render the template with the form
    return render(request, 'academics/super_admin_publication.html', context)

# Super User Delete Staff publication
@login_required(login_url='accounts_login')
def delete_staff_publication(request, publication_id):
    # Ensure the user is an admin
    if not (request.user.is_superuser or request.user.profile.role == 'Super Admin' or request.user.profile.role == 'ADMISSION-OFFICER'):
        messages.warning(request, 'You do not have permission to perform this action.')
        return redirect('accounts_login')

    
    # Retrieve the publication or return 404 if not found
    publication = get_object_or_404(StaffPublication, id=publication_id)
    # Register user activity
    UserActivityLog.objects.create(user=request.user, action=f'Visited Delete Staff Publication for {publication.staff.user.profile.first_name} {publication.staff.user.profile.last_name} ')
    # Get the staff ID before deletion
    staff_id = publication.staff.user.id

    # Delete the publication
    publication.delete()
    # Register user activity
    UserActivityLog.objects.create(user=request.user, action=f'Deleted {publication.publication_platform} publication for {publication.staff.user.profile.first_name} {publication.staff.user.profile.last_name} Staff ')
    messages.success(request, 'Publication Deleted Successfully')
    # Redirect to the staff edit page with staff ID
    return redirect('superadmin_add_staff_publication', staff_id=staff_id)


# Super Admin Add Academic staff Qualification Record
@login_required(login_url='accounts_login')
def super_admin_academic_staff_record(request, staff_id):
    logged_user = request.user
    if not (logged_user.profile.role == 'Super Admin' or logged_user.profile.role == 'ADMISSION-OFFICER'  or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    # Check Registered user

    try:
        registered_staff = get_object_or_404(StaffRegister, user_id=staff_id)
        user_profile = registered_staff.user.profile
        # Register user activity
        UserActivityLog.objects.create(user=request.user, action=f'Visited Add Staff Academic Record for {registered_staff.user.profile.first_name}  {registered_staff.user.profile.last_name} ')
        # Check if Staff has Academic Record
        try:
            academic_record = AcademicStaff.objects.select_related('staff').get(staff=registered_staff)
            messages.warning(request, 'Staff Already Added Academic Record')
            return redirect('super_admin_staff_reg_preview', staff_id)
        except AcademicStaff.DoesNotExist:
        
            if request.method == 'POST':
                form = SuperAdminAcademicStaffCreationForm(request.POST, request.FILES, instance=user_profile)
                if form.is_valid():
                    qualification_date = form.cleaned_data['qualification_date']
                    current_rank = form.cleaned_data['current_rank']
                    internal_appointment = form.cleaned_data['internal_appointment']
                    staff_type = form.cleaned_data['staff_type']
                    work_experience = form.cleaned_data['work_experience']
                    courses_taught = form.cleaned_data['courses_taught']
                    upload_cv = form.cleaned_data['upload_cv']
                    
                    # Fetch Staff Publications for the staff member
                    staff_publications = StaffPublication.objects.filter(staff=registered_staff)
                    
                    academic_staff_record = AcademicStaff.objects.create(
                        staff=registered_staff,
                        qualification_date=qualification_date,
                        current_rank=current_rank, internal_appointment=internal_appointment,
                        staff_type=staff_type,
                        work_experience=work_experience,
                        courses_taught=courses_taught,
                        staff2=user_profile,
                        upload_cv=upload_cv, recorded_by=logged_user
                    )
                    
                    # Add publications to academic staff record
                    academic_staff_record.staff_publications.set(staff_publications)
                    # Register user activity
                    UserActivityLog.objects.create(user=request.user, action=f'Added Staff Academic Record for {registered_staff.user.profile.first_name}  {registered_staff.user.profile.last_name} ')
                    messages.success(request, 'Academic Staff Record Added Successfully.')
                    return redirect('super_admin_staff_reg_preview', staff_id=staff_id)
            else:
                form = SuperAdminAcademicStaffCreationForm(instance=user_profile)
            
            context = {
                'page_title': 'Super Admin Staff Records',
                'form': form,
            }
            return render(request, 'academics/academic_staff_record.html', context)
    except StaffRegister.DoesNotExist:
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')


# Super Admin Preview and Submit Staff Registration Form view
@login_required(login_url='accounts_login')
@transaction.atomic
def super_admin_staff_reg_preview(request, staff_id):
    logged_user = request.user
    
    if not (logged_user.profile.role == 'Super Admin' or logged_user.profile.role == 'ADMISSION-OFFICER'  or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong.')
        return redirect('accounts_login')
    
    try:
        staff_registration_instance = StaffRegister.objects.get(user_id=staff_id)
        staff_department = staff_registration_instance.department
        staff_directorate = staff_registration_instance.directorate
        staff_category = staff_registration_instance.dept_type
        staff_employment_type = staff_registration_instance.Appointment_type
        staff_employment_mode = staff_registration_instance.Appointment_Mode
        staff_salary_grade = staff_registration_instance.Pay_grade
        staff_file_number = staff_registration_instance.file_number
        staff_email = staff_registration_instance.email
        staff_nin_number = staff_registration_instance.nin_number
        staff_Promotion_status = staff_registration_instance.Promotion_status
        staff_First_appointment = staff_registration_instance.First_appointment
        staff_instance = staff_registration_instance.user

         # Register user activity
        UserActivityLog.objects.create(user=request.user, action=f'Previewed Staff Registration for {staff_registration_instance.user.profile.first_name}  {staff_registration_instance.user.profile.last_name} ')
        # Check if the academic staff record exists
        try:
            academic_staff = AcademicStaff.objects.select_related('staff').get(staff=staff_registration_instance)
        except AcademicStaff.DoesNotExist:
            messages.warning(request, 'Complete Staff Academic Record first')
            return redirect('super_admin_academic_staff_record', staff_id=staff_id)

        # Fetch staff publications
        staff_publication = StaffPublication.objects.select_related('staff').filter(staff=staff_registration_instance).order_by('-submission_date')

        if request.method == 'POST':
            # If the form is submitted, process the form data
            form = StaffRegSubmissionForm(request.POST, instance=staff_instance)
            if form.is_valid():
                # Create a new staffAppSubmission instance
                new_application = staffAppSubmission.objects.create(
                    staff_user=staff_instance,
                    department=staff_department,
                    directorate=staff_directorate,
                    confirm=True
                )
                # Register user activity
                UserActivityLog.objects.create(user=request.user, action=f'Submitted Staff Registration Form for {staff_registration_instance.user.profile.first_name}  {staff_registration_instance.user.profile.last_name} ')
                messages.success(request, 'Registration Submitted Successfully.')
                return redirect('super_admin_staff_registration_slip', staff_id=staff_id)
        else:
            form = StaffRegSubmissionForm(instance=staff_instance)

        context = {
            'staff_email': staff_email,
            'staff_First_appointment': staff_First_appointment,
            'staff_Promotion_status': staff_Promotion_status,
            'staff_nin_number': staff_nin_number,
            'staff_file_number': staff_file_number,
            'staff_salary_grade': staff_salary_grade,
            'staff_employment_mode': staff_employment_mode,
            'staff_employment_type': staff_employment_type,
            'staff_category': staff_category,
            'staff_id': staff_id,
            'staff_department': staff_department,
            'staff_directorate': staff_directorate,
            'academic_staff': academic_staff,
            'staff_instance': staff_instance,
            'staff_publication': staff_publication,
            'form': form,
            'page_title': 'Staff Registration Preview',
        }
        return render(request, 'admission/super_admin_staff_reg_preview.html', context)
        
    except StaffRegister.DoesNotExist:
        messages.warning(request, 'Staff Not Found')
        return redirect('accounts_login')


# Super Admin Staff Registration Slip View
@login_required(login_url='accounts_login')
@transaction.atomic
def super_admin_staff_registration_slip(request, staff_id):
    logged_user = request.user

    if not (logged_user.profile.role == 'Super Admin' or logged_user.profile.role == 'ADMISSION-OFFICER'  or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    # Check if staff has registered and submitted Registration
    try:
        registered_staff = StaffRegister.objects.select_related('user').get(user_id=staff_id)    
       
        # Check Staff Academic Record
        try:
            academic_staff = AcademicStaff.objects.select_related('staff').get(staff=registered_staff)
            
            try:
                check_submission = staffAppSubmission.objects.select_related('staff_user').get(staff_user=registered_staff.user) 
                # Register user activity
                UserActivityLog.objects.create(user=request.user, action=f'Visited Staff Registration Slip for {registered_staff.user.profile.first_name}  {registered_staff.user.profile.last_name} ')
            except staffAppSubmission.DoesNotExist:
                messages.warning(request, 'Application yet to be submitted.')
                return redirect('super_admin_staff_reg_preview', staff_id)   
            else:
                staff_publication = StaffPublication.objects.select_related('staff').filter(staff=registered_staff).order_by('-submission_date')
                context = {
                    'registered_staff': registered_staff,
                    'academic_staff': academic_staff,
                    'staff_publication': staff_publication,
                    'page_title': 'Super Admin Staff Reg. Slip',
                }
                return render(request, 'admission/super_admin_staff_reg_slip.html', context)
        except AcademicStaff.DoesNotExist:
            messages.warning(request, 'Academic Record Not Added Yet')
            return redirect('super_admin_academic_staff_record', staff_id='staff_id')        
    except StaffRegister.DoesNotExist:
        messages.warning(request, 'You are NOT a Registered Staff Yet')
        return redirect('staff_register')
    

# Delete Staff Publication View
class StaffDeletePublicationView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = StaffPublication
    template_name = 'academics/confirm_delete_publication.html'
    success_url = '/staff/publications/'
    context_object_name = 'staff_publication'  # the context object name

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'Staff'

    def delete(self, request, *args, **kwargs):
        staff_publication = self.get_object()
        messages.error(request, 'Publication Deleted successfully.')
        UserActivityLog.objects.create(user=self.request.user, action=f'Deleted {staff_publication.publication_platform} for {staff_publication.staff.user.profile.first_name} {staff_publication.staff.user.profile.last_name}')
        return super().delete(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Delete Publication'
        return context


# Staff Update Publication
class StaffUpdatePublicationView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = StaffPublication
    template_name = 'academics/staff_publication.html'
    form_class = StaffPublicationCreationForm  # Use the form for updating

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'Staff'
    
    def dispatch(self, request, *args, **kwargs):
        staff = self.get_object()  # Retrieve the pre-applicant instance
        # Log user activity
        UserActivityLog.objects.create(user=self.request.user, action=f'Visited Update Staff Publication for {staff.staff.user.profile.first_name} {staff.staff.user.profile.last_name}')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        staff = self.get_object()  # Retrieve the pre-applicant instance
        # Save the form and add a success message
        form.save()
        UserActivityLog.objects.create(user=self.request.user, action=f' Updated Staff Publication for {staff.staff.user.profile.first_name} {staff.staff.user.profile.last_name}')
        messages.success(self.request, 'Publication Updated Successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Update Publication'
        return context

    success_url = reverse_lazy('staff_publication')


# Super Admin Delete Staff  View
class AcademicStaffDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = StaffRegister
    template_name = 'academics/confirm_delete_staff.html'
    success_url = reverse_lazy('staff_list')  # Use reverse_lazy to avoid import issues
    context_object_name = 'staff'

    def test_func(self):
        return self.request.user.profile.role == 'Super Admin' or self.request.user.is_superuser 

    def delete(self, request, *args, **kwargs):
        staff = self.get_object()  # Retrieve the pre-applicant instance
        # Get the StaffRegister object
        staff = self.get_object()
        
        # Get the user associated with the staff
        user = staff.user

        # Delete the user first
        user.delete()

        # Now delete the StaffRegister object
        staff.delete()
        # Log user activity
        UserActivityLog.objects.create(user=self.request.user, action=f'Deleted {staff.user.profile.first_name} {staff.user.profile.last_name}')
        messages.success(request, 'Staff and associated user deleted successfully.')
        return super().delete(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = 'Delete Staff'
        return context


# List of Staff view.
class StaffListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = StaffRegister
    template_name = 'academics/academic_staff_list.html'
    context_object_name = 'staff'
    paginate_by = 10

    # function to check if querysetthe user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'Super Admin' or self.request.user.profile.role == 'ADMISSION-OFFICER' or self.request.user.is_superuser

    def get_queryset(self):
       
        queryset = StaffRegister.objects.order_by('-submitted_date')
        file_number = self.request.GET.get('file_number')
        if file_number:
            queryset = queryset.filter(file_number__icontains=file_number)
        return queryset
    
    def dispatch(self, request, *args, **kwargs):
        # Log user activity
        UserActivityLog.objects.create(user=self.request.user, action=f'Visited View Staff List Page')
        return super().dispatch(request, *args, **kwargs)
        
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'List of Staff'
        context['form'] = StaffSearchForm(self.request.GET)
        # Add pagination information to the context
        queryset = self.get_queryset()  # Retrieve the original queryset
        paginator = Paginator(queryset, self.paginate_by)  # Paginate before rendering
        page_number = self.request.GET.get('page')

        try:
            page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)

        context['page_obj'] = page_obj
        return context

    def delete(self, request, *args, **kwargs):
        messages.success(request, 'Staff deleted successfully.')
        return super().delete(request, *args, **kwargs)


# Super Admin Reset Staff Registration View
class AcademicStaffResetView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = staffAppSubmission
    template_name = 'academics/confirm_reset_staff.html'
    success_url = '/staff/list/'
    context_object_name = 'staff'  # the context object name

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'Super Admin' or self.request.user.profile.role == 'ADMISSION-OFFICER'  or self.request.user.is_superuser 

    def delete(self, request, *args, **kwargs):
        staff = self.get_object()
        # Log user activity
        UserActivityLog.objects.create(user=self.request.user, action=f'Reset {staff.staff_user.profile.first_name} {staff.staff_user.profile.last_name}')
        messages.error(request, 'Staff Reset successfully.')
        return super().delete(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Reset Staff'
        return context


# Search Students By Level View
@login_required(login_url='accounts_login')
def search_students_level(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.profile.role == 'Admin' or logged_user.profile.role == 'Super Admin' or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    search_data = request.POST if request.method == 'POST' else request.GET
    form = LevelSearchForm(search_data or None)
    students = None
    context = {
        'form': form,
        'page_title': 'Search Students by Level'
    }

    if search_data:
        if form.is_valid():
            student_level = form.cleaned_data['admitted_level']
            academic_session = form.cleaned_data['academic_session']
            list_students = Student.objects.filter(
                current_level=student_level,
                academic_session=academic_session,
            ).order_by('student_id', 'id')
            if list_students:
                count_students = list_students.count()
                paginator = Paginator(list_students, 10)  # Change 10 to your desired number of items per page

                page_number = request.GET.get('page')

                try:
                    students = paginator.page(page_number)
                except PageNotAnInteger:
                    students = paginator.page(1)
                except EmptyPage:
                    students = paginator.page(paginator.num_pages)

                # Log user activity
                UserActivityLog.objects.create(user=logged_user, action=f'Generated List of {count_students} {student_level} Students')
                
                context.update({
                    'form': form,
                    'page_title': f'List of {student_level} Students',
                    'students': students,
                    'count_students': count_students,
                    'admitted_level': student_level,
                    'academic_session': academic_session,
                    'querystring': urlencode({
                        'admitted_level': student_level,
                        'academic_session': academic_session.id,
                    }),
                    'list_students': list_students,
                })

                return render(request, 'academics/students_list.html', context)
            else:
                messages.warning(request, 'No Student Found')

    return render(request, 'academics/search_students.html', context)


# FUNCTION FOR THE GENERATION OF PDF
def generate_student_level_pdf(request, queryset, context):
    # Render the HTML content of the PDF template with the filtered queryset
    context['list_students'] = queryset
    html_content = render_to_string('academics/students_level_list_pdf.html', context)

    # Create an in-memory binary stream to store the generated PDF
    result = BytesIO()

    # Generate the PDF using xhtml2pdf
    pdf = pisa.CreatePDF(
        html_content,            # HTML content to convert to PDF
        dest=result,            # Output stream to store the PDF
    )

    # Check if PDF generation was successful
    if not pdf.err:
        # If successful, create an HTTP response with the PDF content
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="students_level_list.pdf"'  # Set filename for download
        return response
    else:
        # If PDF generation failed, return None
        return None


def download_students_pdf(request):
    logged_user = request.user
    if not (logged_user.profile.role in ['ADMISSION-OFFICER', 'Admin', 'Super Admin'] or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')

    student_level = request.GET.get('admitted_level')
    academic_session_str = request.GET.get('academic_session')
    # Get Academic Session and compare 
    try:
        academic_session = AcademicSession.objects.get(name=academic_session_str)
    except AcademicSession.DoesNotExist:
        messages.warning(request, 'Invalid Academic Session')
        return redirect('search_students_level')

    list_students = Student.objects.filter(current_level=student_level, academic_session=academic_session.id)

    context = {
        'page_title': f'List of {student_level} Students',
        'admitted_level': student_level,
        'academic_session': academic_session,
        'list_students': list_students,
    }

    pdf_data = generate_student_level_pdf(request, list_students, context)
    if pdf_data:
        return pdf_data
    else:
        messages.error(request, 'Error generating PDF')
        return redirect('search_students_level')


# SEARCH AND EDIT STUDENT NAMES
@login_required(login_url='accounts_login')
def search_and_edit_student(request):
    logged_user = request.user
    if not (logged_user.profile.role in ['Super Admin', 'Admin'] or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    if request.method == 'POST':
        search_form = SearchStudentForm(request.POST)
        if 'search' in request.POST and search_form.is_valid():
            student_id = search_form.cleaned_data['student_id']
            try:
                student = Student.objects.get(student_id=student_id)
                edit_form = EditStudentNamesForm(instance=student.candidate.profile)
                return render(request, 'academics/edit_student_names.html', {
                    'search_form': search_form,
                    'edit_form': edit_form,
                    'student': student,
                })
            except Student.DoesNotExist:
                messages.error(request, 'No student matches the given ID.')
                return render(request, 'academics/edit_student_names.html', {
                    'search_form': search_form,
                })
        elif 'edit' in request.POST:
            student_id = request.POST.get('student_id')
            try:
                student = Student.objects.get(student_id=student_id)
                edit_form = EditStudentNamesForm(request.POST, instance=student.candidate.profile)
                if edit_form.is_valid():
                    edit_form.save()
                    messages.success(request, f'Student with ID {student.student_id} details updated successfully.')
                    return redirect('search_and_edit_student')
                else:
                    messages.error(request, 'Please correct the error below.')
                    return render(request, 'academics/edit_student_names.html', {
                        'search_form': SearchStudentForm(),
                        'edit_form': edit_form,
                        'student': student,
                        'page_title': f'Edit {student.candidate.profile.first_name} Name',
                    })
            except Student.DoesNotExist:
                messages.error(request, 'No student matches the given ID.')
                return render(request, 'academics/edit_student_names.html', {
                    'search_form': SearchStudentForm(),
                })
    else:
        search_form = SearchStudentForm()

    context = {
        'search_form': search_form,
        'page_title': 'Edit Student Name',
    }

    return render(request, 'academics/edit_student_names.html', context)

@login_required(login_url='accounts_login')
def search_and_change_programme(request):
    logged_user = request.user
    if not (logged_user.profile.role in ['Super Admin', 'Admin'] or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    if request.method == 'POST':
        search_form = SearchStudentForm(request.POST)
        if 'search' in request.POST and search_form.is_valid():
            student_id = search_form.cleaned_data['student_id']
            try:
                student = Student.objects.get(student_id=student_id)
                edit_form = EditStudentProgrammeForm(instance=student)
                return render(request, 'academics/change_student_programme.html', {
                    'search_form': search_form,
                    'edit_form': edit_form,
                    'student': student,
                    'page_title': 'Change Student Programme',
                })
            except Student.DoesNotExist:
                messages.error(request, 'No student matches the given ID.')
                return render(request, 'academics/change_student_programme.html', {
                    'search_form': search_form,
                })
        elif 'edit' in request.POST:
            student_id = request.POST.get('student_id')
            try:
                student = Student.objects.get(student_id=student_id)
                edit_form = EditStudentProgrammeForm(request.POST, instance=student)
                if edit_form.is_valid():
                    admission_session_changed = 'academic_session' in edit_form.changed_data
                    with transaction.atomic():
                        # Save student instance with updated data 
                        student = edit_form.save(commit=False)
                        
                        # Update ApplicationSubmission model
                        application_submission = ApplicationSubmission.objects.get(applicant=student.candidate)
                        if admission_session_changed:
                            application_submission.academic_sess = student.academic_session
                        application_submission.course_name = student.course
                        application_submission.save()

                        # Update Admitted model
                        admitted = Admitted.objects.get(applicant=student.candidate)
                        if admission_session_changed:
                            admitted.academic_session = student.academic_session
                        admitted.admitted_course = student.course
                        admitted.admitted_level = student.current_level
                        admitted.save()

                        # Update Jamb model if it exists
                        try:
                            jamb = Jamb.objects.get(applicant=student.candidate)
                            if admission_session_changed:
                                jamb.academic_session = student.academic_session
                            jamb.admitted_course = student.course
                            jamb.admitted_level = student.current_level
                            jamb.save()
                        except Jamb.DoesNotExist:
                            pass

                        # Commit the changes within the transaction
                        student.save()  # Save the student instance after updates

                    messages.success(request, f'Student with ID {student.student_id} Course Programme Change successfully.')
                    return redirect('search_and_change_programme')
                else:
                    messages.error(request, 'Please correct the error below.')
                    return render(request, 'academics/change_student_programme.html', {
                        'search_form': SearchStudentForm(),
                        'edit_form': edit_form,
                        'student': student,
                        'page_title': f'Change {student.candidate.profile.first_name} Programme',
                    })
            except Student.DoesNotExist:
                messages.error(request, 'No student matches the given ID.')
                return render(request, 'academics/change_student_programme.html', {
                    'search_form': SearchStudentForm(),
                })
    else:
        search_form = SearchStudentForm()

    context = {
        'search_form': search_form,
        'page_title': 'Change Student Programme',
    }

    return render(request, 'academics/change_student_programme.html', context)

@login_required(login_url='accounts_login')
def students_bysession_level_course_report(request):
    logged_user = request.user
    if not (logged_user.profile.role == 'ADMISSION-OFFICER' or logged_user.profile.role == 'Admin' or logged_user.profile.role == 'Super Admin' or logged_user.is_superuser):
        messages.warning(request, 'Something Went Wrong')
        return redirect('accounts_login')
    
    form = StudentLevelProgrammeSessionForm()
    students = None
    
    if request.method == 'GET':
        form = StudentLevelProgrammeSessionForm(request.GET)
        if form.is_valid():
            student_level = form.cleaned_data['admitted_level']
            academic_session = form.cleaned_data['academic_session']
            course_of_study = form.cleaned_data['programme_study']
            current_datetime = datetime.now()

            list_students = Student.objects.filter(current_level=student_level, academic_session=academic_session, course=course_of_study).order_by('id')
            if list_students:
                count_students = list_students.count()
                paginator = Paginator(list_students, 10)  # Change 10 to your desired number of items per page

                page_number = request.GET.get('page')

                try:
                    students = paginator.page(page_number)
                except PageNotAnInteger:
                    students = paginator.page(1)
                except EmptyPage:
                    students = paginator.page(paginator.num_pages)

                # Log user activity
                UserActivityLog.objects.create(user=logged_user, action=f'Generated List of {count_students} {course_of_study} {student_level} Students')
                
                context = {
                    'current_datetime': current_datetime,
                    'course_of_study': course_of_study,
                    'form': form,
                    'page_title': f'List of {student_level} Students',
                    'students': students,
                    'count_students': count_students,
                    'admitted_level': student_level,
                    'academic_session': academic_session,
                    'list_students': list_students,
                    }
                
                if request.GET.get('downloaded'):
                    context = {
                        'current_datetime': current_datetime,
                        'course_of_study': course_of_study,
                        'form': form,
                        'page_title': f'List of {student_level} Students',
                        'students': students,
                        'count_students': count_students,
                        'admitted_level': student_level,
                        'academic_session': academic_session,
                        'list_students': list_students,
                    }
                    # Log user activity
                    UserActivityLog.objects.create(user=request.user, action=f'Downloaded PDF List of {student_level}, {course_of_study} Report for {academic_session} Academic Session.')
                    pdf_data = generate_studentby_program_level_report_pdf(request, list_students, context)
                    if pdf_data:
                        return pdf_data
                    else:
                        messages.error(request, 'Error generating PDF')
                        return redirect('students_bysession_level_course_report')
                    
                return render(request, 'academics/students_by_session_level_program.html', context)
    else:
        form = StudentLevelProgrammeSessionForm()

    context = {
        'page_title': 'Student(s) Detail List',
        'form': StudentLevelProgrammeSessionForm(request.GET),  # Include full GET parameters
    }
    return render(request, 'academics/search_students_by_categories.html', context)

# FUNCTION FOR THE GENERATION OF PDF FROM ABOVE VIEW
def generate_studentby_program_level_report_pdf(request, queryset, context):
    # Render the HTML content of the PDF template with the filtered queryset
    context['list_students'] = queryset
    html_content = render_to_string('academics/students_by_session_level_program_pdf.html', context)

    # Create an in-memory binary stream to store the generated PDF
    result = BytesIO()

    # Generate the PDF using xhtml2pdf
    pdf = pisa.CreatePDF(
        html_content,            # HTML content to convert to PDF
        dest=result,            # Output stream to store the PDF
    )

    # Check if PDF generation was successful
    if not pdf.err:
        # If successful, create an HTTP response with the PDF content
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="students Detail list.pdf"'  # Set filename for download
        return response
    else:
        # If PDF generation failed, return None
        return None

# ADMIN RESET STUDENT COURSE REGISTRATION FORM
@login_required(login_url='accounts_login')
def delete_student_semester_course_reg(request, student_id):
    # Ensure only authorized users can access the view
    if not (request.user.profile.role in ['Super Admin', 'Admin'] or request.user.is_superuser):
        messages.warning(request, 'You do not have permission to perform this action.')
        return redirect('accounts_login')
    
    # Get the student instance
    student = get_object_or_404(Student, student_id=student_id)
    current_session = student.academic_session
    current_semester = require_active_semester(request, current_session)
    if not current_semester:
        return redirect('admin_search_registered_semester_student')

    # Filter and delete the student's courses for the current session and semester
    deleted_courses = CourseEnrollment.objects.filter(
        student=student.candidate,
        academic_session=current_session,
        semester=current_semester
    ).delete()

    if deleted_courses[0] > 0:
        # Log user activity
        UserActivityLog.objects.create(user=request.user, action=f'Deleted all courses for {student.candidate.profile.first_name} {student.candidate.profile.last_name} in current session and semester')
        messages.success(request, f'All courses for {student.candidate.profile.first_name} {student.candidate.profile.last_name}  for the current Session and Semester have been successfully deleted.')
    else:
        messages.warning(request, f'No courses found for {student.candidate.profile.first_name} {student.candidate.profile.last_name} in the current session and semester.')

    return redirect('admin_search_registered_semester_student')


# SEARCH AND REGISTER STUDENT SEMESTER COURSES
@login_required(login_url='accounts_login')
def admin_search_and_register_student(request):
    # Ensure the user is a superuser or has the 'Super Admin' role
    if not (request.user.is_superuser or request.user.profile.role in ['Admin', 'Super Admin']):
        messages.warning(request, 'You do not have permission to view this page.')
        return redirect('accounts_login')
    
    form = SearchStudentCourseReg(request.GET or None)

    if request.method == "GET" and form.is_valid():
        student_id = form.cleaned_data['search_query']
        
        # Searching for the student by ID or another field in the User model
        try:
            student = Student.objects.get(student_id__icontains=student_id)
            current_session = student.academic_session
            current_semester = require_active_semester(request, current_session)
            if not current_semester:
                return redirect('admin_search_registered_semester_student')
            check_student_registration = CourseEnrollment.objects.filter(student__student__student_id__icontains=student_id, academic_session=current_session, semester=current_semester).select_related('course_unit', 'student')
            if check_student_registration:
                messages.warning(request, 'Student has Already Registered for the Current Academic Session and Semester')
                return redirect('accounts_dashboard')
            else:
                admitted_student = Student.objects.select_related('candidate').get(student_id=student_id)
                UserActivityLog.objects.create(user=request.user, action=f'Searched for Student with Name: {admitted_student.candidate.profile.first_name} {admitted_student.candidate.profile.last_name} for Course Registration')
                return redirect('admin_student_course_registration', student_id) 
        except Student.DoesNotExist:
            messages.error(request, 'No student found with the provided ID.')

    # If not GET or no valid form submission
    form = SearchStudentCourseReg()

    # Common base query for recently registered students
    admitted_student = Student.objects.order_by('-added_date')[:5]
    number_of_registration = admitted_student.count()

    context = {
        'page_title': 'Search Student',
        'admitted_student': admitted_student,
        'number_of_registration': number_of_registration,
        'form': form,
    }
    return render(request, 'academics/search_registered_student.html', context)

# ADMIN REGISTER STUDENT SEMESTER COURSES
@login_required(login_url='accounts_login')
def admin_student_course_registration(request, student_id):
    logged_user = request.user
    if not (logged_user.profile.role in ['Super Admin', 'Admin'] or logged_user.is_superuser):
        messages.warning(request, 'You do not have permission to perform this action.')
        return redirect('accounts_login')

    try:
        student_instance = Student.objects.select_related('candidate', 'course').get(student_id=student_id)
        student_current_level_name = student_instance.current_level
        student_programme= student_instance.course
        current_session = student_instance.academic_session

        # Find the corresponding Level instance
        try:
            level_instance = Level.objects.get(name=student_current_level_name)
            student_current_level_id = level_instance.id
        except Level.DoesNotExist:
            messages.error(request, 'Level Not Found')
            return redirect('accounts_dashboard')

        student_program = student_instance.course  # This is a CourseOfStudy instance
        student_program_id = student_program.id 
    except Student.DoesNotExist:
        messages.error(request, 'Student Not Found')
        return redirect('accounts_dashboard')

    current_semester = require_active_semester(request, current_session)
    if not current_semester:
        return redirect('accounts_dashboard')
    
    if not (student_current_level_name == 'HNDI' or student_current_level_name == 'NDI' or student_current_level_name == 'Remedial Programme' or student_current_level_name == 'Pre-HND' or student_current_level_name == 'PND'):
        # Get Previous Level using the Student instance
        previous_level = student_instance.get_previous_level()
        
    
        # Get Previous Level Instance ID
        try:
            previous_level_instance = Level.objects.get(name=previous_level)
        except Level.DoesNotExist:
            messages.warning(request, 'Level NOT Found')
            return redirect('student_carryover_courses')

        # Get Student Course Units
        student_course_units = CourseUnit.objects.filter(
            academic_session=current_session,
            course_of_study_id=student_program_id, 
            semester=current_semester, 
            level_id=student_current_level_id
        )
        # Check if Student has Registered for Carry Over Course
        try:
            check_carryover = CourseCarryover.objects.select_related('student').filter(student=student_instance.candidate,
                                                                                    academic_session=current_session,
                                                                                    semester=current_semester,
                                                                                    level=previous_level_instance)
        except CourseCarryover.DoesNotExist:
            messages.warning(request, 'You did NOT Register for any Carryover')

        
        # Calculate Total Credit Hours of registered Carryover course units
        carryover_credit_hours = CourseCarryover.calculate_carryover_credit_hours(student_instance.candidate,
                                                                                current_semester,
                                                                                current_session,
                                                                                previous_level_instance)
  
        context = {
            'carryover_credit_hours': carryover_credit_hours,
            'previous_level': previous_level,
            'check_carryover': check_carryover,
            'page_title': 'Admin Student Course Registration',
            'student_instance': student_instance,
            'current_semester': current_semester,
            'latest_academic_session': current_session,
            'student_course_units': student_course_units,
        }
        return render(request, 'academics/admin_course_unit_enrollment.html', context)
    else:
        # Get Student Course Units
        student_course_units = CourseUnit.objects.filter(
            academic_session=current_session,
            course_of_study_id=student_program_id, 
            semester=current_semester, 
            level_id=student_current_level_id
        )
        context = {
            'page_title': 'Admin Student Course Registration',
            'student_instance': student_instance,
            'current_semester': current_semester,
            'latest_academic_session': current_session,
            'student_course_units': student_course_units,
        }
        return render(request, 'academics/admin_course_unit_enrollment.html', context)


# ADMIN CONFIRM AND SUBMIT STUDENT COURSE REGISTRATION
@login_required(login_url='accounts_login')
def admin_register_student_course_units(request, student_id):
    logged_user = request.user
    logger.debug('Admin course registration started.')
    messages.info(request, 'Admin course registration started.')

    # Check user permissions
    if not (logged_user.profile.role in ['Super Admin', 'Admin'] or logged_user.is_superuser):
        logger.warning('Unauthorized access attempt.')
        messages.warning(request, 'You do not have permission to perform this action.')
        return redirect('accounts_login')

    # Get the student instance
    try:
        student_instance = Student.objects.select_related('candidate').get(student_id=student_id)
        student_programme = student_instance.course
        student_level = student_instance.current_level
        latest_academic_session = student_instance.academic_session
        logger.debug(f'Student found: {student_instance.candidate.profile.first_name} {student_instance.candidate.profile.last_name}')
        messages.info(request, f'Student found: {student_instance.candidate.profile.first_name} {student_instance.candidate.profile.last_name}')
    except Student.DoesNotExist:
        logger.error('Student does not exist.')
        messages.warning(request, 'Student does not exist.')
        return redirect('accounts_login')

    current_semester = require_active_semester(request, latest_academic_session)
    if not current_semester:
        return redirect('semester_course_registration')

    # Find the corresponding Current Student Level instance
    try:
        level_instance = Level.objects.get(name=student_level)
        logger.debug(f'Student level found: {level_instance}')
        messages.info(request, f'Student level found: {level_instance}')
    except Level.DoesNotExist:
        logger.error('Level not found.')
        messages.error(request, 'Level not found.')
        return redirect('semester_course_registration')

    if request.method == 'POST':
        logger.debug('Processing POST request...')
        messages.info(request, 'Processing POST request...')

        # Get the selected course units from the form
        selected_course_units_ids = request.POST.getlist('selected_course_units[]')
        if not selected_course_units_ids:
            logger.warning('No course units selected.')
            messages.warning(request, 'No course units selected.')
            return redirect('admin_search_and_register_student')

        logger.debug(f'Selected course units: {selected_course_units_ids}')
        messages.info(request, f'Selected course units: {selected_course_units_ids}')

        # Initialize course registration tracking
        registered_course_units_count = 0
        total_credit_hours = 0

        try:
            with transaction.atomic():
                registered_course_units = []
                
                # Check if the student has already registered these course units
                existing_registration = CourseEnrollment.objects.filter(
                    student=student_instance.candidate,
                    semester=current_semester,
                    academic_session=latest_academic_session,
                    programme=student_programme,
                    level=level_instance
                ).filter(course_unit_id__in=selected_course_units_ids).exists()
                
                if existing_registration:
                    logger.warning('Student already registered for courses.')
                    messages.warning(request, f'{student_instance.candidate.profile.first_name} {student_instance.candidate.profile.last_name} already registered for courses in {current_semester} during the {latest_academic_session} academic session.')
                    return redirect('admin_search_and_register_student')

                

                # Register selected course units
                for course_unit_id in selected_course_units_ids:
                    try:
                        course_unit_instance = CourseUnit.objects.get(id=course_unit_id, academic_session=latest_academic_session)
                        logger.debug(f'Course unit found: {course_unit_instance.course_title}')
                        messages.info(request, f'Course unit found: {course_unit_instance.course_title}')
                    except CourseUnit.DoesNotExist:
                        logger.error(f'Course unit with ID {course_unit_id} does not exist.')
                        messages.error(request, f'Course unit with ID {course_unit_id} does not exist.')
                        continue

                    # Add course unit to registration list
                    registered_course_units.append(CourseEnrollment(
                        student=student_instance.candidate,
                        course_unit=course_unit_instance,
                        academic_session=latest_academic_session,
                        semester=current_semester,
                        level=level_instance,
                        programme=student_programme,
                        added_by=logged_user
                    ))

                    # Track total credit hours
                    total_credit_hours += course_unit_instance.credit_load
                    logger.debug(f'Credit load for {course_unit_instance.course_title}: {course_unit_instance.credit_load}')
                    messages.info(request, f'Credit load for {course_unit_instance.course_title}: {course_unit_instance.credit_load}')

                # Check the registered course units before bulk creation
                if registered_course_units:
                    logger.debug(f'Registering {len(registered_course_units)} course units...')
                    CourseEnrollment.objects.bulk_create(registered_course_units)
                    registered_course_units_count = len(registered_course_units)
                    messages.success(request, f'{registered_course_units_count} course units registered with {total_credit_hours} credit hours for {student_level} in the {latest_academic_session} academic session.')
                else:
                    logger.error('No valid course units were registered.')
                    messages.error(request, 'No valid course units were registered.')
                    return redirect('admin_search_and_register_student')

                return redirect('admin_view_registered_student_courses', student_id=student_id)

        except Exception as e:
            logger.error(f'Error inside transaction: {str(e)}')
            messages.error(request, f'Error inside transaction: {str(e)}')
            return redirect('admin_search_and_register_student')

    else:
        logger.error('Invalid request method. Expected POST.')
        messages.error(request, 'Invalid request method. Expected POST.')

    return redirect('semester_course_registration')



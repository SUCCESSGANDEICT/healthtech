from django.shortcuts import render, redirect
from accounts.models import StaffPublication, StaffRegister, AcademicStaff
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from academics.models import Department, Faculty, Directorate, Unit
from django.contrib import messages
from accounts.models import StaffRegister
from django.db.models import Exists, OuterRef
from admission.models import staffAppSubmission
from django.http import JsonResponse
from django.http import HttpResponse
# Create your views here.


# Staff publications view
def publications(request):
    # Get all publications for the selected staff
    staff_publications = StaffPublication.objects.order_by('-submission_date')

    paginator = Paginator(staff_publications, 8)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_title': 'Staff Publications',
        'staff_publications': page_obj,
    }
    return render(request, 'pages/staff_publications.html', context)


def index(request):
   # Get all staff publications with related staff information and count of publications
    staff_publications = (
        StaffPublication.objects
        .select_related('staff', 'staff2')
        .values('staff__first_name', 'staff__last_name', 'staff__department__name', 'staff__directorate__name', 'staff2__image', 'staff_id')
        .annotate(publication_count=Count('id'))
        .order_by('-submission_date')[:4]
    )
    
    context = {
        'staff_publications': staff_publications,
        'page_title': 'ECOSAHT Home',
        
    }

    return render(request, 'pages/index.html', context)


def brief_about_us(request):
    context = {
        'page_title': 'About Us',
    }
    return render(request, 'pages/brief_about_us.html', context) 


def full_history(request):
    context = {
        'page_title': 'History',
    }
    return render(request, 'pages/history.html', context)


def community_phealth(request):
    context = {
        'page_title': 'Community and Public Health',
    }
    return render(request, 'pages/community_phealth.html', context)


def computer_info_tech(request):
    context = {
        'page_title': 'Computer and Information Technology',
    }
    return render(request, 'pages/computer_info_tech.html', context)



# Staff Publications view
def staff_publications(request, staff_id):
    # Get the staff object based on the staff_id parameter
    staff = get_object_or_404(StaffRegister, id=staff_id)

    # Get all publications for the selected staff
    staff_publications = StaffPublication.objects.filter(staff=staff)

    paginator = Paginator(staff_publications, 4)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'staff': staff,
        'staff_publications': page_obj,
        'page_title': 'Staff Publications',
    }
    return render(request, 'pages/staff_publications.html', context)


# ICT DIRECTORATE VIEW
def ict_directorate(request):
    try:
        check_directorate = Directorate.objects.get(name='ICT')
        directorate_name = check_directorate.id
        
    except Directorate.DoesNotExist:
        messages.warning(request, 'No Such Directorate Found')
        context = {
           
            'page_title': 'ICT Directorate',
        }
        return render(request, 'pages/ict_directorate.html', context)
    else:
        list_units = Unit.objects.filter(directorate=directorate_name)
        paginator = Paginator(list_units, 3)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        context = {
            'list_units': page_obj,
            'page_title': 'ICT Directorate',
        }
        return render(request, 'pages/ict_directorate.html', context)


# School of Business And Managment view
def school_bam_departments(request):
    try:
        check_school = Faculty.objects.get(name='School Of Business and Management')
        school_name = check_school.id
        
    except Faculty.DoesNotExist:
        messages.warning(request, 'No School Found')
        context = {
           
            'page_title': 'School of Business & Mgt.',
        }
        return render(request, 'pages/schools.html', context)
    else:
        list_departments = Department.objects.filter(faculty=school_name)
        paginator = Paginator(list_departments, 3)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        context = {
            'list_departments': page_obj,
            'page_title': 'School of Business & Mgt.',
        }
        return render(request, 'pages/schools.html', context)


# Accountancy Staff Categories
def accountancy_staff_categories(request):
    context = {
        'page_title': 'Accountancy Dept. Categories',
    }
    return render(request, 'pages/acct_staff_categories.html', context)


# Accountancy Academic Staff 
def accountancy_staff(request):
    try:
        check_department = Department.objects.get(name='Accountancy')
        department_name = check_department
    except Faculty.DoesNotExist:
        messages.warning(request, 'No Department Found')
        context = {
            'page_title': '  Accountancy Dept. Staff',
        }
        return render(request, 'pages/academic_staff.html', context)
    else:
        # Query to filter academic staff who have submitted their registration
        query_result = (
            AcademicStaff.objects
            .select_related('staff', 'staff2')
            .filter(
                staff__department=department_name,
                staff__dept_type='Academic',
                staff__user__staff_registration__isnull=False  # Check if staff submission exists
            )
            .annotate(publication_count=Count('staff_publications__id'))
            .values('staff__dept_type', 'qualification_date', 'internal_appointment', 'staff_publications', 'current_rank', 'work_experience', 'courses_taught', 'staff__Appointment_Mode', 'staff2__first_name', 'staff2__last_name', 'staff2__image', 'staff_id', 'publication_count', 'upload_cv')
            .order_by('-submission_date')
        )

        paginator = Paginator(query_result, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'staff_list': page_obj,
            'page_title': 'Accountancy Staff List',
        }
        return render(request, 'pages/academic_staff.html', context)


# Public Admin Staff Categories
def public_admin_staff_categories(request):
    context = {
        'page_title': 'Public Admin. Dept. Categories',
    }
    return render(request, 'pages/pub_staff_categories.html', context)


# Public Admin Academic Staff 
def public_admin_academic_staff(request):
    try:
        check_department = Department.objects.get(name='Public Administration')
        department_name = check_department
    except Faculty.DoesNotExist:
        messages.warning(request, 'No Department Found')
        context = {
            'page_title': '  Public Dept. Staff',
        }
        return render(request, 'pages/academic_staff.html', context)
    else:
        # Query to filter academic staff who have submitted their registration
        query_result = (
            AcademicStaff.objects
            .select_related('staff', 'staff2')
            .filter(
                staff__department=department_name,
                staff__dept_type='Academic',
                staff__user__staff_registration__isnull=False  # Check if staff submission exists
            )
            .annotate(publication_count=Count('staff_publications__id'))
            .values('staff__dept_type', 'qualification_date', 'internal_appointment', 'staff_publications', 'current_rank', 'work_experience', 'courses_taught', 'staff__Appointment_Mode', 'staff2__first_name', 'staff2__last_name', 'staff2__image', 'staff_id', 'publication_count', 'upload_cv')
            .order_by('-submission_date')
        )

        paginator = Paginator(query_result, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'staff_list': page_obj,
            'page_title': 'Public Admin. Staff List',
        }
        return render(request, 'pages/academic_staff.html', context)


# BAM Staff Categories
def bam_staff_categories(request):
    context = {
        'page_title': 'BAM Dept. Categories',
    }
    return render(request, 'pages/bam_staff_categories.html', context)


# BAM Academic Staff Categories
def bam_academic_staff(request):
    try:
        check_department = Department.objects.get(name='Business Administration And Management')
        department_name = check_department
    except Faculty.DoesNotExist:
        messages.warning(request, 'No Department Found')
        context = {
            'page_title': '  BAM Dept. Acad. Staff',
        }
        return render(request, 'pages/academic_staff.html', context)
    else:
        # Query to filter academic staff who have submitted their registration
        query_result = (
            AcademicStaff.objects
            .select_related('staff', 'staff2')
            .filter(
                staff__department=department_name,
                staff__dept_type='Academic',
                staff__user__staff_registration__isnull=False  # Check if staff submission exists
            )
            .annotate(publication_count=Count('staff_publications__id'))
            .values('staff__dept_type', 'qualification_date', 'internal_appointment', 'staff_publications', 'current_rank', 'work_experience', 'courses_taught', 'staff__Appointment_Mode', 'staff2__first_name', 'staff2__last_name', 'staff2__image', 'staff_id', 'publication_count', 'upload_cv')
            .order_by('-submission_date')
        )

        paginator = Paginator(query_result, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'staff_list': page_obj,
            'page_title': 'BAM Academic Staff List',
        }
        return render(request, 'pages/academic_staff.html', context)


# School of Business And Managment view
def school_engineering_departments(request):
    try:
        check_school = Faculty.objects.get(name='School Of Engineering Technology')
        school_name = check_school.id
        
    except Faculty.DoesNotExist:
        messages.warning(request, 'No School Found')
        context = {
           
            'page_title': 'School of Engineering Tech.',
        }
        return render(request, 'pages/schools.html', context)
    else:
        list_departments = Department.objects.filter(faculty=school_name)
        paginator = Paginator(list_departments, 3)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        context = {
            'list_departments': page_obj,
            'page_title': 'School of Engineering Tech.',
        }
        return render(request, 'pages/schools.html', context)


# School of Sciences view
def school_sciences_departments(request):
    try:
        check_school = Faculty.objects.get(name='School OF Sciences')
        school_name = check_school.id
        
    except Faculty.DoesNotExist:
        messages.warning(request, 'No School Found')
        context = {
           
            'page_title': 'School of Sciences',
        }
        return render(request, 'pages/schools.html', context)
    else:
        list_departments = Department.objects.filter(faculty=school_name)
        paginator = Paginator(list_departments, 3)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        context = {
            'list_departments': page_obj,
            'page_title': 'School of Sciences',
        }
        return render(request, 'pages/schools.html', context)


# Compt. Engr. Staff Categories
def compt_engr_staff_categories(request):
    context = {
        'page_title': 'Compt. Engr. Dept. Staff',
    }
    return render(request, 'pages/compt_engr_staff_cat.html', context)


# Compt. Emgr. Academic Staff Categories
def compt_engineering_academic_staff(request):
    try:
        check_department = Department.objects.get(name='Computer Engineering Technology')
        department_name = check_department
    except Faculty.DoesNotExist:
        messages.warning(request, 'No Department Found')
        context = {
            'page_title': 'Compt. Engr. Dept. Acad. Staff',
        }
        return render(request, 'pages/academic_staff.html', context)
    else:
        # Query to filter academic staff who have submitted their registration
        query_result = (
            AcademicStaff.objects
            .select_related('staff', 'staff2')
            .filter(
                staff__department=department_name,
                staff__dept_type='Academic',
                staff__user__staff_registration__isnull=False  # Check if staff submission exists
            )
            .annotate(publication_count=Count('staff_publications__id'))
            .values('staff__dept_type', 'qualification_date', 'internal_appointment', 'staff_publications', 'current_rank', 'work_experience', 'courses_taught', 'staff__Appointment_Mode', 'staff2__first_name', 'staff2__last_name', 'staff2__image', 'staff_id', 'publication_count', 'upload_cv')
            .order_by('-submission_date')
        )

        paginator = Paginator(query_result, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'staff_list': page_obj,
            'page_title': 'Compt. Engr. Academic Staff List',
        }
        return render(request, 'pages/academic_staff.html', context)


# Electrical Electronics Engr. Staff Categories
def elect_elect_staff_categories(request):
    context = {
        'page_title': 'Elect-Elect Dept. Staff',
    }
    return render(request, 'pages/elect_elect_staff_cat.html', context)


# Elect Elect Academic Staff Categories
def elect_elect_engineering_academic_staff(request):
    try:
        check_department = Department.objects.get(name='Electrical And Electronics Engineering Technology')
        department_name = check_department
    except Faculty.DoesNotExist:
        messages.warning(request, 'No Department Found')
        context = {
            'page_title': 'Elect. Elect. Academic Staff List',
        }
        return render(request, 'pages/academic_staff.html', context)
    else:
        # Query to filter academic staff who have submitted their registration
        query_result = (
            AcademicStaff.objects
            .select_related('staff', 'staff2')
            .filter(
                staff__department=department_name,
                staff__dept_type='Academic',
                staff__user__staff_registration__isnull=False  # Check if staff submission exists
            )
            .annotate(publication_count=Count('staff_publications__id'))
            .values('staff__dept_type', 'qualification_date', 'internal_appointment', 'staff_publications', 'current_rank', 'work_experience', 'courses_taught', 'staff__Appointment_Mode', 'staff2__first_name', 'staff2__last_name', 'staff2__image', 'staff_id', 'publication_count', 'upload_cv')
            .order_by('-submission_date')
        )

        paginator = Paginator(query_result, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'staff_list': page_obj,
            'page_title': 'Elect. Elect. Academic Staff List',
        }
        return render(request, 'pages/academic_staff.html', context)


# Agricultural Technology Staff Categories
def agric_tech_staff_categories(request):
    context = {
        'page_title': 'Agric. Tech. Dept. Staff',
    }
    return render(request, 'pages/agric_tech_staff_cat.html', context)


# Agric. Technology Academic Staff Categories
def agric_tech_academic_staff(request):
    try:
        check_department = Department.objects.get(name='Agricultural Technology')
        department_name = check_department
    except Faculty.DoesNotExist:
        messages.warning(request, 'No Department Found')
        context = {
            'page_title': 'Agric. Tech. Academic Staff List',
        }
        return render(request, 'pages/academic_staff.html', context)
    else:
        # Query to filter academic staff who have submitted their registration
        query_result = (
            AcademicStaff.objects
            .select_related('staff', 'staff2')
            .filter(
                staff__department=department_name,
                staff__dept_type='Academic',
                staff__user__staff_registration__isnull=False  # Check if staff submission exists
            )
            .annotate(publication_count=Count('staff_publications__id')) 
            .values('staff__dept_type', 'qualification_date', 'internal_appointment', 'staff_publications', 'current_rank', 'work_experience', 'courses_taught', 'staff__Appointment_Mode', 'staff2__first_name', 'staff2__last_name', 'staff2__image', 'staff_id', 'publication_count', 'upload_cv')
            .order_by('-submission_date')
        )

        paginator = Paginator(query_result, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'staff_list': page_obj,
            'page_title': 'Agric. Tech. Academic Staff List',
        }
        return render(request, 'pages/academic_staff.html', context)


# Computer Science Staff Categories
def compt_sc_staff_categories(request):
    context = {
        'page_title': 'Compt. Sc. Dept. Staff',
    }
    return render(request, 'pages/compt_sc_staff_cat.html', context)


# Computer Science Academic Staff view
def compt_sc_academic_staff(request):
    try:
        check_department = Department.objects.get(name='Computer Science')
        department_name = check_department
    except Faculty.DoesNotExist:
        messages.warning(request, 'No Department Found')
        context = {
            'page_title': 'Compt. Sc. Academic Staff List',
        }
        return render(request, 'pages/academic_staff.html', context)
    else:
        # Query to filter academic staff who have submitted their registration
        query_result = (
            AcademicStaff.objects
            .select_related('staff', 'staff2')
            .filter(
                staff__department=department_name,
                staff__dept_type='Academic',
                staff__user__staff_registration__isnull=False  # Check if staff submission exists
            )
            .annotate(publication_count=Count('staff_publications__id'))
            .values('staff__dept_type', 'qualification_date', 'internal_appointment', 'staff_publications', 'current_rank', 'work_experience', 'courses_taught', 'staff__Appointment_Mode', 'staff2__first_name', 'staff2__last_name', 'staff2__image', 'staff_id', 'publication_count', 'upload_cv')
            .order_by('-submission_date')
        )

        paginator = Paginator(query_result, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'staff_list': page_obj,
            'page_title': 'Compt. Sc. Staff List',
        }
        return render(request, 'pages/academic_staff.html', context)


# Statistics Staff Categories
def stat_staff_categories(request):
    context = {
        'page_title': 'Compt. Sc. Dept. Staff',
    }
    return render(request, 'pages/stat_staff_cat.html', context)


# Statistics Academic Staff view
def stat_academic_staff(request):
    try:
        check_department = Department.objects.get(name='Statistics')
        department_name = check_department
    except Faculty.DoesNotExist:
        messages.warning(request, 'No Department Found')
        context = {
            'page_title': 'Statistics Academic Staff List',
        }
        return render(request, 'pages/academic_staff.html', context)
    else:
        # Query to filter academic staff who have submitted their registration
        query_result = (
            AcademicStaff.objects
            .select_related('staff', 'staff2')
            .filter(
                staff__department=department_name,
                staff__dept_type='Academic',
                staff__user__staff_registration__isnull=False  # Check if staff submission exists
            )
            .annotate(publication_count=Count('staff_publications__id'))
            .values('staff__dept_type', 'qualification_date', 'internal_appointment', 'staff_publications', 'current_rank', 'work_experience', 'courses_taught', 'staff__Appointment_Mode', 'staff2__first_name', 'staff2__last_name', 'staff2__image', 'staff_id', 'publication_count', 'upload_cv')
            .order_by('-submission_date')
        )

        paginator = Paginator(query_result, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'staff_list': page_obj,
            'page_title': 'Statistics Academic Staff List',
        }
        return render(request, 'pages/academic_staff.html', context)


# BAM Non-Academic Staff Categories
def bam_none_academic_staff(request):
    try:
        check_department = Department.objects.get(name='Business Administration And Management')
        department_name = check_department
    except Faculty.DoesNotExist:
        messages.warning(request, 'No Department Found')
        context = {
            'page_title': 'BAM Non-Academic Staff List',
        }
        return render(request, 'pages/non_academic_staff.html', context)
    else:
        # Query to filter academic staff who have submitted their registration
        query_result = (
            AcademicStaff.objects
            .select_related('staff', 'staff2')
            .filter(
                staff__department=department_name,
                staff__dept_type='Non-Academic',
                staff__user__staff_registration__isnull=False  # Check if staff submission exists
            )
            .annotate(publication_count=Count('staff_publications__id'))
            .values('staff__dept_type', 'qualification_date', 'internal_appointment', 'staff_publications', 'current_rank', 'work_experience', 'courses_taught', 'staff__Appointment_Mode', 'staff2__first_name', 'staff2__last_name', 'staff2__image', 'staff_id', 'publication_count', 'upload_cv')
            .order_by('-submission_date')
        )

        paginator = Paginator(query_result, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'staff_list': page_obj,
            'page_title': 'BAM Non-Academic Staff List',
        }
        return render(request, 'pages/non_academic_staff.html', context)


# Accountancy Department Non-Academic Staff 
def accountancy_non_academic_staff(request):
    try:
        check_department = Department.objects.get(name='Accountancy')
        department_name = check_department
    except Faculty.DoesNotExist:
        messages.warning(request, 'No Department Found')
        context = {
            'page_title': 'Acct. Dept. Non-Academic Staff',
        }
        return render(request, 'pages/non_academic_staff.html', context)
    else:
        # Query to filter academic staff who have submitted their registration
        query_result = (
            AcademicStaff.objects
            .select_related('staff', 'staff2')
            .filter(
                staff__department=department_name,
                staff__dept_type='Non-Academic',
                staff__user__staff_registration__isnull=False  # Check if staff submission exists
            )
            .annotate(publication_count=Count('staff_publications__id'))
            .values('staff__dept_type', 'qualification_date', 'internal_appointment', 'staff_publications', 'current_rank', 'work_experience', 'courses_taught', 'staff__Appointment_Mode', 'staff2__first_name', 'staff2__last_name', 'staff2__image', 'staff_id', 'publication_count', 'upload_cv')
            .order_by('-submission_date')
        )

        paginator = Paginator(query_result, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'staff_list': page_obj,
            'page_title': 'Acct. Dept. Non-Academic Staff',
        }
        return render(request, 'pages/non_academic_staff.html', context)


# Publica Admin Dept. Non-Academic Staff 
def public_admin_non_academic_staff(request):
    try:
        check_department = Department.objects.get(name='Public Administration')
        department_name = check_department
    except Faculty.DoesNotExist:
        messages.warning(request, 'No Department Found')
        context = {
            'page_title': '  Public Dept. Non-Academic Staff',
        }
        return render(request, 'pages/academic_staff.html', context)
    else:
        # Query to filter academic staff who have submitted their registration
        query_result = (
            AcademicStaff.objects
            .select_related('staff', 'staff2')
            .filter(
                staff__department=department_name,
                staff__dept_type='Non-Academic',
                staff__user__staff_registration__isnull=False  # Check if staff submission exists
            )
            .annotate(publication_count=Count('staff_publications__id'))
            .values('staff__dept_type', 'qualification_date', 'internal_appointment', 'staff_publications', 'current_rank', 'work_experience', 'courses_taught', 'staff__Appointment_Mode', 'staff2__first_name', 'staff2__last_name', 'staff2__image', 'staff_id', 'publication_count', 'upload_cv')
            .order_by('-submission_date')
        )

        paginator = Paginator(query_result, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'staff_list': page_obj,
            'page_title': 'Public Admin. Non-Academic Staff List',
        }
        return render(request, 'pages/non_academic_staff.html', context)


# Compt. Emgr. Academic Staff Categories
def compt_engineering_non_academic_staff(request):
    try:
        check_department = Department.objects.get(name='Computer Engineering Technology')
        department_name = check_department
    except Faculty.DoesNotExist:
        messages.warning(request, 'No Department Found')
        context = {
            'page_title': 'Compt. Engr. Tech. Dept None-Academic Staff',
        }
        return render(request, 'pages/academic_staff.html', context)
    else:
        # Query to filter academic staff who have submitted their registration
        query_result = (
            AcademicStaff.objects
            .select_related('staff', 'staff2')
            .filter(
                staff__department=department_name,
                staff__dept_type='Non-Academic',
                staff__user__staff_registration__isnull=False  # Check if staff submission exists
            )
            .annotate(publication_count=Count('staff_publications__id'))
            .values('staff__dept_type', 'qualification_date', 'internal_appointment', 'staff_publications', 'current_rank', 'work_experience', 'courses_taught', 'staff__Appointment_Mode', 'staff2__first_name', 'staff2__last_name', 'staff2__image', 'staff_id', 'publication_count', 'upload_cv')
            .order_by('-submission_date')
        )

        paginator = Paginator(query_result, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'staff_list': page_obj,
            'page_title': 'Compt. Engr. Tech. Dept None-Academic Staff',
        }
        return render(request, 'pages/non_academic_staff.html', context)


# Elect Elect Non-Academic Staff Categories
def elect_elect_engineering_non_academic_staff(request):
    try:
        check_department = Department.objects.get(name='Electrical And Electronics Engineering Technology')
        department_name = check_department
    except Faculty.DoesNotExist:
        messages.warning(request, 'No Department Found')
        context = {
            'page_title': 'Elect. Elect. Academic Staff List',
        }
        return render(request, 'pages/academic_staff.html', context)
    else:
        # Query to filter academic staff who have submitted their registration
        query_result = (
            AcademicStaff.objects
            .select_related('staff', 'staff2')
            .filter(
                staff__department=department_name,
                staff__dept_type='Non-Academic',
                staff__user__staff_registration__isnull=False  # Check if staff submission exists
            )
            .annotate(publication_count=Count('staff_publications__id'))
            .values('staff__dept_type', 'qualification_date', 'internal_appointment', 'staff_publications', 'current_rank', 'work_experience', 'courses_taught', 'staff__Appointment_Mode', 'staff2__first_name', 'staff2__last_name', 'staff2__image', 'staff_id', 'publication_count', 'upload_cv')
            .order_by('-submission_date')
        )

        paginator = Paginator(query_result, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'staff_list': page_obj,
            'page_title': 'Elect. Elect. Non-Academic Staff List',
        }
        return render(request, 'pages/non_academic_staff.html', context)


# Agric. Technology Academic Staff Categories
def agric_tech_non_academic_staff(request):
    try:
        check_department = Department.objects.get(name='Agricultural Technology')
        department_name = check_department
    except Faculty.DoesNotExist:
        messages.warning(request, 'No Department Found')
        context = {
            'page_title': 'Agric. Tech. Non-Academic Staff',
        }
        return render(request, 'pages/academic_staff.html', context)
    else:
        # Query to filter academic staff who have submitted their registration
        query_result = (
            AcademicStaff.objects
            .select_related('staff', 'staff2')
            .filter(
                staff__department=department_name,
                staff__dept_type='Non-Academic',
                staff__user__staff_registration__isnull=False  # Check if staff submission exists
            )
            .annotate(publication_count=Count('staff_publications__id'))
            .values('staff__dept_type', 'qualification_date', 'internal_appointment', 'staff_publications', 'current_rank', 'work_experience', 'courses_taught', 'staff__Appointment_Mode', 'staff2__first_name', 'staff2__last_name', 'staff2__image', 'staff_id', 'publication_count', 'upload_cv')
            .order_by('-submission_date')
        )

        paginator = Paginator(query_result, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'staff_list': page_obj,
            'page_title': 'Agric. Tech. Non-Academic Staff List',
        }
        return render(request, 'pages/non_academic_staff.html', context)
    

# Computer Science Non-Academic Staff view
def compt_sc_non_academic_staff(request):
    try:
        check_department = Department.objects.get(name='Computer Science')
        department_name = check_department
    except Faculty.DoesNotExist:
        messages.warning(request, 'No Department Found')
        context = {
            'page_title': 'Compt. Sc. None-Academic Staff',
        }
        return render(request, 'pages/academic_staff.html', context)
    else:
        # Query to filter academic staff who have submitted their registration
        query_result = (
            AcademicStaff.objects
            .select_related('staff', 'staff2')
            .filter(
                staff__department=department_name,
                staff__dept_type='Non-Academic',
                staff__user__staff_registration__isnull=False  # Check if staff submission exists
            )
            .annotate(publication_count=Count('staff_publications__id'))
            .values('staff__dept_type', 'qualification_date', 'internal_appointment', 'staff_publications', 'current_rank', 'work_experience', 'courses_taught', 'staff__Appointment_Mode', 'staff2__first_name', 'staff2__last_name', 'staff2__image', 'staff_id', 'publication_count', 'upload_cv')
            .order_by('-submission_date')
        )

        paginator = Paginator(query_result, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'staff_list': page_obj,
            'page_title': 'Compt. Sc. Non-Academic Staff',
        }
        return render(request, 'pages/non_academic_staff.html', context)


# Statistics Academic Staff view
def stat_non_academic_staff(request):
    try:
        check_department = Department.objects.get(name='Statistics')
        department_name = check_department
    except Faculty.DoesNotExist:
        messages.warning(request, 'No Department Found')
        context = {
            'page_title': 'Statistics Non-Academic Staff',
        }
        return render(request, 'pages/academic_staff.html', context)
    else:
        # Query to filter academic staff who have submitted their registration
        query_result = (
            AcademicStaff.objects
            .select_related('staff', 'staff2')
            .filter(
                staff__department=department_name,
                staff__dept_type='Non-Academic',
                staff__user__staff_registration__isnull=False  # Check if staff submission exists
            )
            .annotate(publication_count=Count('staff_publications__id'))
            .values('staff__dept_type', 'qualification_date', 'internal_appointment', 'staff_publications', 'current_rank', 'work_experience', 'courses_taught', 'staff__Appointment_Mode', 'staff2__first_name', 'staff2__last_name', 'staff2__image', 'staff_id', 'publication_count', 'upload_cv')
            .order_by('-submission_date')
        )

        paginator = Paginator(query_result, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'department_name': department_name,
            'staff_list': page_obj,
            'page_title': 'Statistics Non-Academic Staff',
        }
        return render(request, 'pages/non_academic_staff.html', context)


# Download Staff CV View
def download_supporting_document(request, staff_id):
    staff_worker = get_object_or_404(AcademicStaff, staff_id=staff_id)
    if staff_worker.upload_cv:
        file_path = staff_worker.upload_cv.path
        with open(file_path, 'rb') as file:
            response = HttpResponse(file.read(), content_type='application/octet-stream')
            response['Content-Disposition'] = f'attachment; filename="{staff_worker.upload_cv.name}"'
            return response
    else:
        # Handle case where there is no supporting document
        messages.warning(request, "No CV document available.")


# Admission Status view
def check_admission(request):

    context = {
        'page_title': 'Check Admission',
    }
    return render(request, 'pages/check_admission.html', context)

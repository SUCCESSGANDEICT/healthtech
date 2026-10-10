from .models import AcademicSession, Faculty, Department, Semester, CourseOfStudy, CourseUnit
from django.contrib import messages
from django.shortcuts import redirect

def check_objects_exist(request):
    if not AcademicSession.objects.exists():
        messages.warning(request, 'Add Academic Session First.')
        return redirect('add_session')
    elif not Faculty.objects.exists():
        messages.warning(request, 'Add School First.')
        return redirect('add_school')
    elif not CourseOfStudy.objects.exists():
        messages.warning(request, 'Add Programme of Study First.')
        return redirect('add_program')
    elif not Department.objects.exists():
        messages.warning(request, 'Add Department before adding Programme to it.')
        return redirect('add_department')
    else:
        return None  # No issues, all required objects exist

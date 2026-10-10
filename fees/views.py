from typing import Any
from django.db import IntegrityError, transaction
from django.db import models
from django.db.models import Q
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, View
from .models import (Fee, ApplicationFee, ScholarshipPin)
from .forms import ApplicationFeeCreationForm, SchoolFeeCreationForm, SchoolFeeFormSet, PinGenerationForm
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib import messages
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.urls import reverse_lazy
from django.http import HttpResponse
from xhtml2pdf import pisa 
from django.template.loader import get_template
import random
import string


# Application Fee Creation view.
class ApplicationFeeCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = ApplicationFee
    template_name = 'fees/add_application_fee.html'
    form_class = ApplicationFeeCreationForm

    # Define a custom test function to check if the user 
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Add Application & Other Fee'
        return context

    def form_valid(self, form):
        fee_name = form.cleaned_data['name']
        category = form.cleaned_data['category']
        academic_session = form.cleaned_data['academic_session']
        indigene_type = form.cleaned_data['indigene_type']
        if ApplicationFee.objects.filter(academic_session=academic_session, category=category, name=fee_name, indigene_type=indigene_type).exists():
            messages.error(self.request, 'Application Fee with the same session, level category, fee name, and indigene type already exists.')
            return self.form_invalid(form)

        # Set the added_by field to the logged-in user before saving
        form.instance.added_by = self.request.user
        # Save the form and add a success message
        form.save()
        messages.success(self.request, f'{fee_name} for {category} added for {academic_session} successfully.')
        return super().form_valid(form)

    success_url = reverse_lazy('app_fees')


# List of Application view.
class ApplicationFeeListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = ApplicationFee
    template_name = 'fees/application_fee_list.html'
    context_object_name = 'application_fees'
    paginate_by = 10

    # function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser
    
    def get_queryset(self):
        return ApplicationFee.objects.select_related('academic_session', 'added_by').order_by('-academic_session__start_date', 'category', 'name', 'indigene_type')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Application & Other Fees'
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
        


# Delete Application View
class ApplicationFeeDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = ApplicationFee
    template_name = 'fees/confirm_delete_fee.html'
    success_url = reverse_lazy('app_fees')
    context_object_name = 'fee'  # the context object name

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser

    def form_valid(self, form):
        fee = self.get_object()
        messages.success(self.request, f'{fee.name} for {fee.category} deleted successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Delete Fee'
        return context


# Update Application Fee view
class ApplicationFeeUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = ApplicationFee
    template_name = 'fees/add_application_fee.html'
    form_class = ApplicationFeeCreationForm  # Use the form for updating

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser

    def form_valid(self, form):
        # Save the form and add a success message
        fee = form.save()
        messages.success(self.request, f'{fee.name} for {fee.category} updated for {fee.academic_session} successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Update Application & Other Fee'
        return context

    success_url = reverse_lazy('app_fees')


# School Fee Creation View
class FeeCreateView(LoginRequiredMixin, UserPassesTestMixin, View):
    model = Fee
    template_name = 'fees/add_fee.html'  # Update this path as per your templates directory
    form_class = SchoolFeeCreationForm
    formset_class = SchoolFeeFormSet
    success_url = reverse_lazy('school_fees_list')

    def test_func(self):
        # Assuming there is a 'role' field in the user profile to check for permissions
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser

    def get_formset(self, data=None):
        return self.formset_class(
            data=data,
            queryset=Fee.objects.none(),
            prefix='fees',
        )

    def get_context_data(self, **kwargs):
        context = {
            'page_title': 'Add Fee',
            'formset': kwargs.get('formset') or self.get_formset(),
        }
        return context

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self.get_context_data())

    def post(self, request, *args, **kwargs):
        formset = self.get_formset(data=request.POST)
        if formset.is_valid():
            try:
                with transaction.atomic():
                    fees = formset.save(commit=False)
                    for fee in fees:
                        fee.added_by = request.user
                        fee.save()
            except IntegrityError:
                messages.error(request, 'One or more tuition fee records already exist. Please review the highlighted rows and try again.')
                return render(request, self.template_name, self.get_context_data(formset=formset))

            created_count = len(fees)
            messages.success(request, f'{created_count} tuition fee record{"s" if created_count != 1 else ""} created successfully.')
            return redirect(self.success_url)

        messages.error(request, 'Please correct the highlighted tuition fee rows and submit again.')
        return render(request, self.template_name, self.get_context_data(formset=formset))


class SchoolFeeListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = Fee
    template_name = 'fees/school_fee_list.html'
    context_object_name = 'School_fees'
    paginate_by = 15

    # function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser
    
    def get_queryset(self):
        search_query = self.request.GET.get('q', '').strip()
        queryset = Fee.objects.select_related('academic_session', 'course_of_study', 'added_by').order_by(
            '-academic_session__start_date',
            'course_of_study__name',
            'level',
            'semester',
            'name',
            'indigene_type',
        )
        if search_query:
            normalized_query = search_query.upper()
            search_filter = (
                Q(course_of_study__name__icontains=search_query)
                | Q(academic_session__name__icontains=search_query)
                | Q(semester__icontains=search_query)
                | Q(name__icontains=search_query)
                | Q(amount__icontains=search_query)
                | Q(added_by__username__icontains=search_query)
            )
            level_values = {value for value, _ in Fee.LEVEL_CHOICES}
            indigene_values = {value for value, _ in Fee._meta.get_field('indigene_type').choices}
            if normalized_query in level_values:
                search_filter |= Q(level__iexact=search_query)
            else:
                search_filter |= Q(level__icontains=search_query)
            if normalized_query in indigene_values:
                search_filter |= Q(indigene_type__iexact=search_query)
            else:
                search_filter |= Q(indigene_type__icontains=search_query)
            queryset = queryset.filter(search_filter)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'School Fees'
        context['search_query'] = self.request.GET.get('q', '').strip()
        context['is_search'] = bool(context['search_query'])
        return context

    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return render(self.request, 'fees/partials/_school_fee_results.html', context)
        return super().render_to_response(context, **response_kwargs)


# Delete School Fee View
class SchoolFeeDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Fee
    template_name = 'fees/confirm_delete_fee.html'
    success_url = '/school_fees/list/'
    context_object_name = 'School_fee'  # the context object name

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser

    def delete(self, request, *args, **kwargs):
        messages.error(request, 'School Fee Deleted successfully.')
        return super().delete(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Delete School Fee'
        return context


# Update Application Fee view
class SchoolFeeUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Fee
    template_name = 'fees/add_fee.html' 
    form_class = SchoolFeeCreationForm  # Use the form for updating

    # Define a custom test function to check if the user is a superuser
    def test_func(self):
        return self.request.user.profile.role == 'MIS ADMIN-1' or self.request.user.is_superuser

    def form_valid(self, form):
        # Save the form and add a success message
        form.save()
        messages.success(self.request, 'School Fee Update Successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add additional context data here
        context['page_title'] = 'Update School Fee'
        return context

    success_url = reverse_lazy('school_fees_list')

# Generate Recharge PIN View
@login_required(login_url='accounts_login')
def generate_scholarship_pin_view(request):
    logged_user = request.user
    allowed_roles = ['MIS ADMIN-1', 'Super Admin']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.error(request, 'Access Denied')
        return redirect('accounts_login')
    
    form = PinGenerationForm()
    new_pins = []

    if request.method == 'POST':
        form = PinGenerationForm(request.POST)
        if form.is_valid():
            plan = form.cleaned_data['plan']
            quantity = form.cleaned_data['quantity']

            for _ in range(quantity):
                code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
                pin = ScholarshipPin.objects.create(code=code, plan=plan)
                new_pins.append(pin)

            # If export is requested
            if 'export_pdf' in request.POST:
                return export_pins_pdf(new_pins)
            
    new_pins = ScholarshipPin.objects.filter(is_used=False).order_by('-created_at')
    all_pins = ScholarshipPin.objects.all().order_by('-created_at')

    # Paginate new pins
    new_pin_page_number = request.GET.get('new_pin_page', 1)
    new_pins_paginator = Paginator(new_pins, 6)
    new_pins_page = new_pins_paginator.get_page(new_pin_page_number)

    # Paginate all pins
    pin_page_number = request.GET.get('pin_page', 1)
    all_pins_paginator = Paginator(all_pins, 6)
    pins_page = all_pins_paginator.get_page(pin_page_number)

    return render(request, 'fees/generate_pin.html', { 
        'form': form,
        'page_title': 'Generate PINS',
        'new_pins_page': new_pins_page,
        'pins_page': pins_page
    }) 



# Generated Recharge PIN View
@login_required(login_url='accounts_login')
def generated_scholarship_pins(request):
    logged_user = request.user
    allowed_roles = ['MIS ADMIN-1', 'Super Admin']
    if not (logged_user.profile.role in allowed_roles or logged_user.is_superuser):
        messages.error(request, 'Access Denied')
        return redirect('accounts_login')
    
    new_pins = ScholarshipPin.objects.filter(is_used=False).order_by('-created_at')
    all_pins = ScholarshipPin.objects.all().order_by('-created_at')

    # Paginate new pins
    new_pin_page_number = request.GET.get('new_pin_page', 1)
    new_pins_paginator = Paginator(new_pins, 10)
    new_pins_page = new_pins_paginator.get_page(new_pin_page_number)

    # Paginate all pins
    pin_page_number = request.GET.get('pin_page', 1)
    all_pins_paginator = Paginator(all_pins, 10)
    pins_page = all_pins_paginator.get_page(pin_page_number)
    
    context = {
        'page_title': 'Generated PINs',
        'new_pins_page': new_pins_page,
        'pins_page': pins_page,
    }
    return render(request, 'fees/generated_pins.html', context)



def export_pins_pdf(pins):
    template_path = 'fees/pin_pdf_template.html'
    context = {'pins': pins}

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="generated_pins.pdf"'

    template = get_template(template_path)
    html = template.render(context)

    pisa_status = pisa.CreatePDF(html, dest=response)
    if pisa_status.err:
        return HttpResponse('We had some errors with PDF generation <pre>' + html + '</pre>')
    return response

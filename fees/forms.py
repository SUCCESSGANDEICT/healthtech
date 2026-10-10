from django import forms
from django.forms import BaseModelFormSet, modelformset_factory
from academics.models import AcademicSession, CourseOfStudy
from .models import ApplicationFee, Fee


class ApplicationFeeCreationForm(forms.ModelForm):
    academic_session = forms.ModelChoiceField(queryset=AcademicSession.objects.all().order_by('-start_date', '-added_date'), required=True, empty_label='Select Academic Session...')

    class Meta:
        model = ApplicationFee
        fields = ['academic_session', 'category', 'name', 'indigene_type', 'amount']

    def clean(self):
        cleaned_data = super().clean()
        academic_session = cleaned_data.get('academic_session')
        category = cleaned_data.get('category')
        name = cleaned_data.get('name')
        indigene_type = cleaned_data.get('indigene_type')
        if academic_session and category and name and indigene_type:
            duplicate = ApplicationFee.objects.filter(academic_session=academic_session, category=category, name=name, indigene_type=indigene_type)
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise forms.ValidationError('This application fee already exists for the selected academic session, category, fee name, and indigene type.')
        return cleaned_data


class SchoolFeeCreationForm(forms.ModelForm):
    academic_session = forms.ModelChoiceField(queryset=AcademicSession.objects.all().order_by('-start_date', '-added_date'), required=True, empty_label='Select Academic Session...')
    course_of_study = forms.ModelChoiceField(
        queryset=CourseOfStudy.objects.select_related('department').all().order_by('name'),
        required=False,
        empty_label='Select Programme for tuition fees...',
        help_text='Required for School Fees/Tuition records. Leave blank only for non-tuition charges such as Late Registration.',
    )

    class Meta:
        model = Fee
        fields = ['academic_session', 'course_of_study', 'name', 'level', 'semester', 'indigene_type', 'amount']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            css_class = 'form-control'
            if field_name == 'amount':
                field.widget.attrs.update({'min': '0', 'step': '0.01'})
            if field.widget.__class__.__name__ == 'CheckboxInput':
                css_class = 'form-check-input'
            existing_classes = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = f'{existing_classes} {css_class}'.strip()

    def has_changed(self):
        if self.is_bound:
            meaningful_fields = ['academic_session', 'course_of_study', 'name', 'level', 'semester', 'amount']
            has_meaningful_value = any(
                str(self.data.get(self.add_prefix(field_name), '')).strip()
                for field_name in meaningful_fields
            )
            if not has_meaningful_value:
                return False
        return super().has_changed()

    def clean_name(self):
        name = self.cleaned_data.get('name')
        return ' '.join(name.split()).title() if name else name

    def clean(self):
        cleaned_data = super().clean()
        academic_session = cleaned_data.get('academic_session')
        name = cleaned_data.get('name')
        course_of_study = cleaned_data.get('course_of_study')
        level = cleaned_data.get('level')
        semester = cleaned_data.get('semester')
        indigene_type = cleaned_data.get('indigene_type')
        if name and Fee.is_tuition_name(name) and not course_of_study:
            raise forms.ValidationError('Course of Study/Programme is required for School Fees or Tuition records.')
        if academic_session and name and level and semester and indigene_type:
            duplicate = Fee.objects.filter(
                academic_session=academic_session,
                course_of_study=course_of_study,
                name__iexact=name,
                level=level,
                semester=semester,
                indigene_type=indigene_type,
            )
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise forms.ValidationError('This fee already exists for the selected academic session, programme, level, payment type, fee name, and indigene type.')
        return cleaned_data


class BaseSchoolFeeFormSet(BaseModelFormSet):
    def validate_unique(self):
        # The form validates existing database duplicates, and clean() below
        # adds clearer row-specific errors for duplicates within the batch.
        return

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        seen_rows = {}
        completed_rows = 0
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get('DELETE'):
                continue
            if not form.has_changed():
                continue

            completed_rows += 1
            academic_session = form.cleaned_data.get('academic_session')
            course_of_study = form.cleaned_data.get('course_of_study')
            name = form.cleaned_data.get('name')
            level = form.cleaned_data.get('level')
            semester = form.cleaned_data.get('semester')
            indigene_type = form.cleaned_data.get('indigene_type')

            if not all([academic_session, name, level, semester, indigene_type]):
                continue

            key = (
                academic_session.pk,
                course_of_study.pk if course_of_study else None,
                Fee.normalize_fee_name(name),
                level,
                semester,
                indigene_type,
            )
            if key in seen_rows:
                form.add_error(
                    None,
                    'This row duplicates another submitted row with the same session, programme, fee type, level, payment option, and indigene type.',
                )
            else:
                seen_rows[key] = form

        if completed_rows == 0:
            raise forms.ValidationError('Enter at least one tuition fee record before submitting.')


SchoolFeeFormSet = modelformset_factory(
    Fee,
    form=SchoolFeeCreationForm,
    formset=BaseSchoolFeeFormSet,
    extra=4,
    can_delete=True,
)


class PinGenerationForm(forms.Form):
    PLAN_CHOICES = (
        ('standard', 'Standard'),
        ('premium', 'Premium'),
    )
    plan = forms.ChoiceField(choices=PLAN_CHOICES, widget=forms.Select(attrs={'class': 'form-control'}))
    quantity = forms.IntegerField(min_value=1, max_value=100, initial=1, widget=forms.NumberInput(attrs={'class': 'form-control'}))

from django import forms
from django.conf.global_settings import LANGUAGES
import zoneinfo

from .models import Project

SORTED_LANGUAGES = sorted(LANGUAGES, key=lambda x: x[1])
TIMEZONE_CHOICES = [(tz, tz) for tz in sorted(zoneinfo.available_timezones())]


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'input input-bordered w-full',
                'placeholder': 'Project name',
            }),
        }


class ProjectTimezoneForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['timezone']
        widgets = {
            'timezone': forms.Select(
                choices=TIMEZONE_CHOICES,
                attrs={'class': 'select select-bordered w-full'},
            ),
        }


class ProjectPublishTimeForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['default_publish_time']
        widgets = {
            'default_publish_time': forms.TimeInput(
                attrs={
                    'class': 'input input-bordered w-full',
                    'type': 'time',
                },
                format='%H:%M',
            ),
        }


class ProjectSettingsForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['enable_linkedin', 'enable_facebook', 'enable_instagram', 'enable_tiktok', 'enable_autopost']
        widgets = {
            'enable_linkedin': forms.CheckboxInput(attrs={'class': 'checkbox checkbox-primary'}),
            'enable_facebook': forms.CheckboxInput(attrs={'class': 'checkbox checkbox-primary'}),
            'enable_instagram': forms.CheckboxInput(attrs={'class': 'checkbox checkbox-primary'}),
            'enable_tiktok': forms.CheckboxInput(attrs={'class': 'checkbox checkbox-primary'}),
            'enable_autopost': forms.CheckboxInput(attrs={'class': 'checkbox checkbox-primary'}),
        }


class ProjectLanguageForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['language']
        widgets = {
            'language': forms.Select(
                choices=SORTED_LANGUAGES,
                attrs={'class': 'select select-bordered w-full'},
            ),
        }


class ProjectProvisioningForm(forms.Form):
    name = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'input input-bordered w-full',
            'placeholder': 'My Project',
        }),
        label='Project Name',
    )
    domain = forms.URLField(
        widget=forms.URLInput(attrs={
            'class': 'input input-bordered w-full',
            'placeholder': 'https://yourbrand.com',
        }),
        label='Website URL',
    )
    language = forms.ChoiceField(
        choices=SORTED_LANGUAGES,
        initial='en',
        widget=forms.Select(attrs={'class': 'select select-bordered w-full'}),
        label='Content Language',
    )

import json
from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import ProjectForm, ProjectLanguageForm, ProjectProvisioningForm, ProjectPublishTimeForm, ProjectSettingsForm, ProjectTimezoneForm
from .models import Project


@require_POST
@login_required
def project_create_quick(request):
    """
    Immediately create a project with a default name, switch to it,
    store a session flag so the home view auto-opens the provision modal.
    """
    project = Project.objects.create(
        name='New Project',
        owner=request.user,
    )
    request.session['active_project_id'] = project.pk
    request.session['auto_provision_project_id'] = project.pk
    return redirect('home')


@require_POST
@login_required
def switch_project(request):
    project_id = request.POST.get('project_id')
    project = get_object_or_404(Project, pk=project_id, owner=request.user)
    request.session['active_project_id'] = project.pk
    response = redirect('home')
    response['X-Up-Accept-Layer'] = 'current'
    return response


@login_required
def project_create(request):
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.owner = request.user
            project.save()
            request.session['active_project_id'] = project.pk
            from django.urls import reverse
            provision_url = reverse('projects:project_provision') + f'?project_id={project.pk}'
            return redirect(provision_url)
    else:
        form = ProjectForm()
    return render(request, 'projects/project_form.html', {
        'form': form,
        'is_edit': False,
    })


@login_required
def project_edit(request, pk):
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    if request.method == 'POST':
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            form.save()
            response = HttpResponse(status=200)
            response['X-Up-Accept-Layer'] = 'null'
            response['X-Up-Events'] = '[{"type": "project:updated"}]'
            return response
    else:
        form = ProjectForm(instance=project)
    return render(request, 'projects/project_form.html', {
        'form': form,
        'is_edit': True,
        'project': project,
    })


@login_required
def project_settings(request):
    project = request.project
    name_form = ProjectForm(instance=project)
    settings_form = ProjectSettingsForm(instance=project)
    language_form = ProjectLanguageForm(instance=project)
    publish_time_form = ProjectPublishTimeForm(instance=project)
    timezone_form = ProjectTimezoneForm(instance=project)

    if request.method == 'POST':
        name_form = ProjectForm(request.POST, instance=project)
        settings_form = ProjectSettingsForm(request.POST, instance=project)
        language_form = ProjectLanguageForm(request.POST, instance=project)
        publish_time_form = ProjectPublishTimeForm(request.POST, instance=project)
        timezone_form = ProjectTimezoneForm(request.POST, instance=project)
        if all([
            name_form.is_valid(),
            settings_form.is_valid(),
            language_form.is_valid(),
            publish_time_form.is_valid(),
            timezone_form.is_valid(),
        ]):
            name_form.save()
            settings_form.save()
            language_form.save()
            publish_time_form.save()
            timezone_form.save()
            messages.success(request, 'Project settings saved.')
            return redirect('projects:project_settings')

    return render(request, 'projects/project_settings.html', {
        'name_form': name_form,
        'settings_form': settings_form,
        'language_form': language_form,
        'publish_time_form': publish_time_form,
        'timezone_form': timezone_form,
        'project': project,
    })


@login_required
@require_POST
def project_delete(request, pk):
    project = get_object_or_404(Project, pk=pk, owner=request.user)

    if request.user.projects.count() <= 1:
        messages.error(request, 'You cannot delete your only project.')
        return redirect('projects:project_settings')

    project.delete()

    another = request.user.projects.first()
    if another:
        request.session['active_project_id'] = another.pk
    else:
        request.session.pop('active_project_id', None)

    return redirect('home')


@login_required
def project_provision(request):
    """
    Provisioning modal for a project. Starts brand scrape and product import.
    Used both after onboarding and after creating a new project from the app.
    """
    project_id = request.GET.get('project_id') or request.POST.get('project_id')
    if project_id:
        project = get_object_or_404(Project, pk=project_id, owner=request.user)
    else:
        project = request.project

    if request.method == 'POST':
        form = ProjectProvisioningForm(request.POST)
        if form.is_valid():
            url = form.cleaned_data['domain']
            language = form.cleaned_data['language']

            # Update project language
            project.language = language
            custom_name = form.cleaned_data.get('name', '').strip()
            if custom_name:
                project.name = custom_name
            else:
                parsed = urlparse(url)
                domain_name = parsed.netloc or parsed.path
                domain_name = domain_name.removeprefix('www.')
                project.name = domain_name
            import zoneinfo
            tz = request.POST.get('timezone', '').strip()
            if tz and tz in zoneinfo.available_timezones():
                project.timezone = tz
            project.save()

            # Start brand scrape task
            from brand.models import Brand
            brand, _ = Brand.objects.get_or_create(project=project, defaults={'user': request.user})
            if brand.processing_status != Brand.ProcessingStatus.SCRAPING:
                Brand.objects.filter(pk=brand.pk).update(
                    processing_status=Brand.ProcessingStatus.SCRAPING,
                    scrape_error='',
                    website_url=url,
                )
                from brand.tasks import scrape_brand_task
                from django_q.tasks import async_task
                async_task(
                    scrape_brand_task, brand.pk, url,
                    user_id=request.user.id,
                    q_options={'task_name': 'scrape_brand'},
                )

            # Start product import task
            if not project.product_import_in_progress:
                Project.objects.filter(pk=project.pk).update(product_import_in_progress=True)
                from django_q.tasks import async_task
                from media_library.tasks import import_products_task
                async_task(
                    import_products_task, project.pk, url,
                    user_id=request.user.id,
                    q_options={'task_name': 'import_products'},
                )

            response = render(request, 'projects/provision_modal.html', {
                'form': form,
                'project': project,
                'provisioning_started': True,
            })
            events = json.dumps([{'type': 'project:provisioning_started', 'project_name': project.name}])
            response['X-Up-Events'] = events
            return response
    else:
        form = ProjectProvisioningForm()

    return render(request, 'projects/provision_modal.html', {
        'form': form,
        'project': project,
    })

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from credits.constants import IMAGE_GENERATION_COST, VIDEO_GENERATION_COST
from credits.models import available_credits

from brand.models import Brand
from integrations.models import IntegrationConnection
from media_library.models import Media
from services.ai_services import edit_text
from .forms import SocialMediaPostForm
from .models import (
    PLATFORM_CHOICES,
    SocialMediaPost,
    SocialMediaPostMedia,
    SocialMediaPostPlatform,
    SocialMediaPostSeedImage,
    SocialMediaPlatformMedia,
)


def _get_platform_label(key):
    return dict(PLATFORM_CHOICES).get(key, key)


@login_required
def post_list(request):
    posts = list(
        SocialMediaPost.objects.filter(project=request.project)
        .prefetch_related('platforms', 'shared_media__media')
    )
    for post in posts:
        all_media = list(post.shared_media.all())
        post.preview_media = all_media[:3]
        post.extra_media_count = max(0, len(all_media) - 3)
    return render(request, 'social_media/post_list.html', {'posts': posts})


@login_required
def post_form(request, pk=None):
    user_media = Media.objects.filter(media_group__project=request.project).select_related('media_group')

    if pk:
        post = get_object_or_404(SocialMediaPost, pk=pk, project=request.project)
        form = SocialMediaPostForm(instance=post)
        platform_variants = list(post.platforms.all())
        enabled_platforms = [pv.platform for pv in platform_variants]
        platforms_data = [
            {
                'platform': pv.platform,
                'use_shared_text': pv.use_shared_text,
                'override_text': pv.override_text or '',
                'use_shared_media': pv.use_shared_media,
            }
            for pv in platform_variants
        ]
        selected_shared_media = [
            {'media_id': m.id, 'media': m.media.id, 'url': m.media.url, 'is_video': m.media.is_video}
            for m in post.shared_media.order_by('sort_order')
        ]
        selected_platform_media = {}
        for pv in post.platforms.prefetch_related('override_media__media').all():
            selected_platform_media[pv.platform] = [
                {'media_id': m.id, 'media': m.media.id, 'url': m.media.url, 'is_video': m.media.is_video}
                for m in pv.override_media.order_by('sort_order')
            ]
        selected_seed_media = [
            {'media': s.media.id, 'url': s.media.url, 'is_video': s.media.is_video}
            for s in post.seed_media.select_related('media').order_by('sort_order')
        ]
        has_content = bool(post.shared_text.strip()) or bool(selected_shared_media)
        initial_mode = 'ai' if not has_content else 'editor'
        extra_ctx = {'post': post, 'is_edit': True, 'initial_mode': initial_mode}
    else:
        post = None
        form = SocialMediaPostForm()
        enabled_platforms = request.project.get_enabled_platforms()
        platforms_data = [
            {'platform': p, 'use_shared_text': True, 'override_text': '', 'use_shared_media': True}
            for p in enabled_platforms
        ]
        selected_shared_media = []
        selected_platform_media = {}
        prefill_seed_media_ids_raw = request.GET.get('seed_media_ids', '')
        selected_seed_media = []
        if prefill_seed_media_ids_raw:
            try:
                id_list = [int(x) for x in prefill_seed_media_ids_raw.split(',') if x.strip()][:8]
                selected_seed_media = [
                    {'media': m.id, 'url': m.url, 'is_video': m.is_video}
                    for m in Media.objects.filter(id__in=id_list, media_group__project=request.project)
                ][:8]
            except (ValueError, TypeError):
                pass
        extra_ctx = {
            'is_edit': False,
            'prefill_topic': request.GET.get('topic', ''),
            'prefill_mode': request.GET.get('mode', ''),
            'auto_suggest': request.GET.get('auto_suggest', '') == '1',
        }

    platform_labels = {p: _get_platform_label(p) for p in enabled_platforms}

    return render(request, 'social_media/post_form.html', {
        'form': form,
        'platforms_data': platforms_data,
        'enabled_platforms': enabled_platforms,
        'platform_labels': platform_labels,
        'user_media': user_media,
        'selected_shared_media': selected_shared_media,
        'selected_platform_media': selected_platform_media,
        'selected_seed_media': selected_seed_media,
        'brand': _get_project_brand(request.project),
        **extra_ctx,
    })






@login_required
@require_POST
def post_delete(request, pk):
    post = get_object_or_404(SocialMediaPost, pk=pk, project=request.project)
    post.delete()
    response = redirect(reverse('social_media:post_list'))
    response['X-Up-Events'] = '[{"type":"social_media:changed"}]'
    return response


logger = logging.getLogger(__name__)


def _get_project_brand(project):
    try:
        return Brand.objects.get(project=project)
    except Brand.DoesNotExist:
        return None


def _validate_post_for_publish(post):
    """Validate per-platform media constraints before publishing or scheduling.

    Returns a list of error strings (empty list means valid).
    """
    PLATFORM_LABELS = dict(PLATFORM_CHOICES)
    errors = []

    for platform in (
        post.platforms
        .filter(is_enabled=True)
        .prefetch_related('override_media__media', 'post__shared_media__media')
    ):
        label = PLATFORM_LABELS.get(platform.platform, platform.platform)
        media_qs = platform.get_effective_media().select_related('media')
        media = list(media_qs)
        text = platform.get_effective_text()

        videos = [m for m in media if m.media.is_video]
        images = [m for m in media if not m.media.is_video]

        if not text.strip() and not media:
            errors.append(f'{label}: Post must have either text or media.')
            continue

        if videos and images:
            errors.append(f'{label}: Cannot mix images and videos in the same post.')
            continue

        if len(videos) > 1:
            errors.append(f'{label}: Only one video is allowed per post.')

        if len(images) > 4:
            errors.append(f'{label}: Maximum 4 images are allowed per post.')

    return errors


def _enqueue_generation(request, post):
    """Validate credits, mark the post as generating and enqueue the task.
    Returns a JsonResponse if an error occurs, otherwise None."""
    brand = _get_project_brand(request.project)
    if not brand:
        return JsonResponse({'error': 'Brand not configured'})

    if post.media_type == 'video':
        cost = VIDEO_GENERATION_COST
    else:
        cost = IMAGE_GENERATION_COST

    if available_credits(request.user) < cost:
        return JsonResponse(
            {
            'error': 'Insufficient credits',
            'credits_required': cost,
            },
            status=402,
        )

    post.processing_status = 'generating'
    post.save(update_fields=['processing_status'])

    from django_q.tasks import async_task
    if post.media_type == 'video':
        async_task(
            'social_media.tasks.generate_video_post_task',
            post.pk,
            brand.pk,
        )
        return None

    platforms = list(post.platforms.values_list('platform', flat=True))
    seed_media_ids = list(post.seed_media.order_by('sort_order').values_list('media_id', flat=True))

    async_task(
        'social_media.tasks.generate_post_task',
        post.pk,
        brand.pk,
        post.topic,
        post.post_type,
        seed_media_ids,
        platforms,
    )
    return None


def _assign_default_scheduled_at(post, project):
    """Assign a default scheduled_at if none provided (next day at project default publish time)."""
    from django.utils import timezone
    import datetime
    import zoneinfo

    publish_time = project.default_publish_time
    project_tz = zoneinfo.ZoneInfo(project.timezone)
    latest = (
        SocialMediaPost.objects.filter(project=project)
        .exclude(scheduled_at=None)
        .order_by('-scheduled_at')
        .values_list('scheduled_at', flat=True)
        .first()
    )
    if latest:
        base_date = latest.astimezone(project_tz).date()
    else:
        base_date = timezone.now().astimezone(project_tz).date()
    next_date = base_date + datetime.timedelta(days=1)
    post.scheduled_at = timezone.make_aware(
        datetime.datetime.combine(next_date, publish_time),
        project_tz,
    )


@login_required
@require_POST
def post_save(request):
    """Unified create/update endpoint. Accepts JSON body, returns {post_id}."""
    data = _parse_json_body(request)
    if not data:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    post_id = data.get('post_id')
    action = data.get('action', 'draft')

    # Load or create the post
    if post_id:
        post = get_object_or_404(SocialMediaPost, pk=post_id, project=request.project)
    else:
        post = SocialMediaPost(user=request.user, project=request.project)

    # Scalar fields
    post.title = data.get('title', '') or 'Untitled'
    post.shared_text = data.get('shared_text', '')
    post.topic = data.get('topic', '')
    post.post_type = data.get('post_type', '')
    post.media_type = data.get('media_type', 'image')
    post.video_type = data.get('video_type', '')
    post.video_brief = data.get('video_brief') or None
    post.video_suggestions = data.get('video_suggestions') or None
    post.ai_instruction = data.get('ai_instruction', '')

    # Status
    if action == 'schedule' and post.scheduled_at:
        post.status = 'scheduled'
    elif not post_id:
        post.status = 'draft'

    # Default scheduled_at for new posts without one
    if not post.scheduled_at and not post_id:
        _assign_default_scheduled_at(post, request.project)

    post.save()

    # ── Platforms ──────────────────────────────────────────────────────────
    platforms_data = data.get('platforms', [])
    valid_platform_keys = dict(PLATFORM_CHOICES).keys()
    existing_platforms = {p.platform: p for p in post.platforms.all()}

    incoming_platform_keys = set()
    for pdata in platforms_data:
        platform_key = pdata.get('platform', '')
        if platform_key not in valid_platform_keys:
            continue
        incoming_platform_keys.add(platform_key)

        if platform_key in existing_platforms:
            pv = existing_platforms[platform_key]
        else:
            pv = SocialMediaPostPlatform(post=post, platform=platform_key)

        pv.use_shared_text = pdata.get('use_shared_text', True)
        pv.override_text = pdata.get('override_text', '')
        pv.use_shared_media = pdata.get('use_shared_media', True)
        pv.is_enabled = True
        pv.save()

    # Remove platforms no longer in the list
    post.platforms.exclude(platform__in=incoming_platform_keys).delete()

    # ── Shared media (replace all) ────────────────────────────────────────
    shared_media_ids = data.get('shared_media', [])
    post.shared_media.all().delete()
    for sort_order, media_id in enumerate(shared_media_ids):
        try:
            media = Media.objects.get(pk=media_id, media_group__project=request.project)
        except Media.DoesNotExist:
            continue
        SocialMediaPostMedia.objects.create(post=post, media=media, sort_order=sort_order)

    # ── Platform override media (replace all) ─────────────────────────────
    platform_override_media = data.get('platform_override_media', {})
    for pv in post.platforms.all():
        pv.override_media.all().delete()
        media_ids = platform_override_media.get(pv.platform, [])
        for sort_order, media_id in enumerate(media_ids):
            try:
                media = Media.objects.get(pk=media_id, media_group__project=request.project)
            except Media.DoesNotExist:
                continue
            SocialMediaPlatformMedia.objects.create(
                platform_variant=pv, media=media, sort_order=sort_order,
            )

    # ── Seed media (replace all) ──────────────────────────────────────────
    seed_media_ids = data.get('seed_media', [])
    post.seed_media.all().delete()
    for sort_order, media_id in enumerate(seed_media_ids):
        try:
            media = Media.objects.get(pk=media_id, media_group__project=request.project)
        except Media.DoesNotExist:
            continue
        SocialMediaPostSeedImage.objects.create(post=post, media=media, sort_order=sort_order)

    # ── Action handling ───────────────────────────────────────────────────
    if action == 'generate':
        err = _enqueue_generation(request, post)
        if err:
            return err

    return JsonResponse({
        'post_id': post.pk,
        'status': post.status,
        'scheduled_at': post.scheduled_at.isoformat() if post.scheduled_at else '',
    })


def _parse_json_body(request):
    try:
        return json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return None


@login_required
@require_POST
def ai_suggest_topic(request):
    data = _parse_json_body(request)
    if not data:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    brand = _get_project_brand(request.project)
    if not brand:
        return JsonResponse({'error': 'Brand not configured'}, status=400)

    seed_media_ids = data.get('seed_media_ids', [])
    media_type = data.get('media_type', 'image')
    video_type = data.get('video_type', 'teaser')

    from django_q.tasks import async_task
    async_task(
        'social_media.tasks.suggest_topic_task',
        request.user.pk,
        brand.pk,
        seed_media_ids,
        media_type,
        video_type,
    )
    return JsonResponse({'queued': True})


@login_required
@require_POST
def ai_edit_text(request):
    data = _parse_json_body(request)
    if not data:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    brand = _get_project_brand(request.project)
    if not brand:
        return JsonResponse({'error': 'Brand not configured'}, status=400)

    action = data.get('action', '')
    text = data.get('text', '')
    platform = data.get('platform')
    instruction = data.get('instruction')
    system_prompt = data.get('system_prompt') or None
    # field_name and result_mode accepted for forward-compatibility; not yet used server-side
    _ = data.get('field_name')
    _ = data.get('result_mode')

    if not action or not text:
        return JsonResponse({'error': 'action and text are required'}, status=400)

    try:
        edited = edit_text(action, text, brand, platform=platform, instruction=instruction, system_prompt_key=system_prompt)
        return JsonResponse({'text': edited})
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except Exception:
        logger.exception('Failed to edit text')
        return JsonResponse({'error': 'Failed to edit text'}, status=500)

@login_required
@require_POST
def post_publish(request, pk):
    """Enqueue an async task to publish the post to all connected platforms."""
    post = get_object_or_404(SocialMediaPost, pk=pk, project=request.project)

    errors = _validate_post_for_publish(post)
    if errors:
        return JsonResponse({'error': errors[0], 'validation_errors': errors}, status=400)

    base_url = request.build_absolute_uri('/').rstrip('/')
    from django_q.tasks import async_task
    async_task('social_media.tasks.publish_post_task', post.pk, base_url)
    return JsonResponse({'queued': True})


@login_required
@require_POST
def post_unschedule(request, pk):
    """Return a post from scheduled back to draft status."""
    post = get_object_or_404(SocialMediaPost, pk=pk, project=request.project)
    post.status = 'draft'
    post.save(update_fields=['status'])
    return JsonResponse({'status': 'draft'})


@login_required
@require_POST
def post_schedule(request, pk):
    """Validate datetime and schedule a post."""
    from django.utils import timezone
    from datetime import datetime as dt_class

    post = get_object_or_404(SocialMediaPost, pk=pk, project=request.project)
    scheduled_at_str = request.POST.get('scheduled_at', '').strip()

    if not scheduled_at_str:
        return JsonResponse({'error': 'Please enter a date and time.'}, status=400)
    try:
        scheduled_at = dt_class.fromisoformat(scheduled_at_str)
        if timezone.is_naive(scheduled_at):
            import zoneinfo
            project_tz = zoneinfo.ZoneInfo(post.project.timezone)
            scheduled_at = timezone.make_aware(scheduled_at, project_tz)
        if scheduled_at <= timezone.now():
            return JsonResponse({'error': 'Scheduled time must be in the future.'}, status=400)

        errors = _validate_post_for_publish(post)
        if errors:
            return JsonResponse({'error': errors[0], 'validation_errors': errors}, status=400)

        post.scheduled_at = scheduled_at
        post.status = 'scheduled'
        post.save(update_fields=['scheduled_at', 'status'])
        return JsonResponse({
            'status': 'scheduled',
            'scheduled_at': post.scheduled_at.isoformat(),
        })
    except ValueError:
        return JsonResponse({'error': 'Invalid date/time format.'}, status=400)


@login_required
@require_POST
def post_save_scheduled_at(request, pk):
    """Save scheduled_at datetime without changing post status."""
    from django.utils import timezone
    from datetime import datetime as dt_class

    post = get_object_or_404(SocialMediaPost, pk=pk, project=request.project)
    scheduled_at_str = request.POST.get('scheduled_at', '').strip()
    if not scheduled_at_str:
        post.scheduled_at = None
        post.save(update_fields=['scheduled_at'])
        return JsonResponse({'saved': True, 'scheduled_at': None})
    try:
        scheduled_at = dt_class.fromisoformat(scheduled_at_str)
        if timezone.is_naive(scheduled_at):
            import zoneinfo
            project_tz = zoneinfo.ZoneInfo(post.project.timezone)
            scheduled_at = timezone.make_aware(scheduled_at, project_tz)
        post.scheduled_at = scheduled_at
        post.save(update_fields=['scheduled_at'])
        return JsonResponse({'saved': True, 'scheduled_at': post.scheduled_at.isoformat()})
    except ValueError:
        return JsonResponse({'error': 'Invalid date/time format.'}, status=400)


@login_required
def post_publish_panel(request, pk):
    """Render the publish panel fragment (opened as Unpoly modal)."""
    post = get_object_or_404(SocialMediaPost, pk=pk, project=request.project)
    platforms = post.platforms.filter(is_enabled=True).order_by('platform')
    has_integrations = IntegrationConnection.objects.filter(
        project=request.project,
        provider_category=IntegrationConnection.ProviderCategory.SOCIAL_MEDIA,
        status=IntegrationConnection.ConnectionStatus.ACTIVE,
    ).exists()
    return render(request, 'social_media/post_publish_panel.html', {
        'post': post,
        'platforms': platforms,
        'has_integrations': has_integrations,
    })


@login_required
def post_card(request, pk):
    """Render a single post card fragment (used for per-card SSE-triggered reload)."""
    post = get_object_or_404(SocialMediaPost, pk=pk, project=request.project)
    all_media = list(post.shared_media.select_related('media').all())
    post.preview_media = all_media[:3]
    post.extra_media_count = max(0, len(all_media) - 3)
    return render(request, 'social_media/post_card.html', {'post': post})


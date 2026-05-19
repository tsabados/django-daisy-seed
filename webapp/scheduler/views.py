import json
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from social_media.models import (
    PLATFORM_CHOICES,
    STATUS_CHOICES,
    SocialMediaPost,
)


@login_required
def scheduler_view(request):
    enabled_platforms = request.project.get_enabled_platforms()

    platform_labels = dict(PLATFORM_CHOICES)
    platforms_for_filter = [
        {'key': p, 'label': platform_labels[p]}
        for p in enabled_platforms
    ]

    filtered_post = None
    post_id = request.GET.get('post')
    if post_id:
        try:
            filtered_post = SocialMediaPost.objects.get(
                pk=int(post_id),
                project=request.project,
            )
        except (SocialMediaPost.DoesNotExist, ValueError):
            pass

    return render(request, 'scheduler/scheduler.html', {
        'platforms': platforms_for_filter,
        'status_choices': STATUS_CHOICES,
        'project_timezone': request.project.timezone,
        'filtered_post': filtered_post,
    })


@login_required
@require_GET
def scheduler_events(request):
    start = request.GET.get('start')
    end = request.GET.get('end')

    qs = SocialMediaPost.objects.filter(
        project=request.project,
        scheduled_at__isnull=False,
    )

    if start:
        qs = qs.filter(scheduled_at__gte=start)
    if end:
        qs = qs.filter(scheduled_at__lt=end)

    platform_filter = request.GET.get('platform')
    if platform_filter:
        qs = qs.filter(
            platforms__platform=platform_filter,
            platforms__is_enabled=True,
        )

    status_filter = request.GET.get('status')
    if status_filter:
        qs = qs.filter(status=status_filter)

    post_filter = request.GET.get('post')
    if post_filter:
        qs = qs.filter(pk=post_filter)

    qs = qs.distinct().prefetch_related('shared_media__media', 'platforms')

    events = []
    for post in qs:
        first_media = post.shared_media.first()
        thumbnail = None
        is_video = False
        if first_media:
            thumbnail = first_media.media.url
            is_video = first_media.media.is_video

        platform_order = ['instagram', 'facebook', 'linkedin']
        enabled_platforms = sorted(
            [p.platform for p in post.platforms.all() if p.is_enabled],
            key=lambda p: platform_order.index(p) if p in platform_order else len(platform_order),
        )

        events.append({
            'id': post.pk,
            'title': post.title,
            'start': post.scheduled_at.isoformat(),
            'extendedProps': {
                'status': post.status,
                'processingStatus': post.processing_status,
                'caption': (post.shared_text or '')[:120],
                'platforms': enabled_platforms,
                'thumbnail': thumbnail,
                'isVideo': is_video,
                'editUrl': reverse('social_media:post_form', args=[post.pk]),
            },
        })

    return JsonResponse(events, safe=False)


@login_required
@require_GET
def scheduler_event_detail(request, pk):
    post = get_object_or_404(
        SocialMediaPost.objects.prefetch_related('shared_media__media', 'platforms'),
        pk=pk,
        project=request.project,
        scheduled_at__isnull=False,
    )

    first_media = post.shared_media.first()
    thumbnail = None
    is_video = False
    if first_media:
        thumbnail = first_media.media.url
        is_video = first_media.media.is_video

    enabled_platforms = [
        p.platform
        for p in post.platforms.all()
        if p.is_enabled
    ]

    return JsonResponse({
        'id': post.pk,
        'title': post.title,
        'start': post.scheduled_at.isoformat(),
        'extendedProps': {
            'status': post.status,
            'processingStatus': post.processing_status,
            'caption': (post.shared_text or '')[:120],
            'platforms': enabled_platforms,
            'thumbnail': thumbnail,
            'isVideo': is_video,
            'editUrl': reverse('social_media:post_form', args=[post.pk]),
        },
    })


@login_required
@require_POST
def scheduler_reschedule(request, pk):
    post = get_object_or_404(SocialMediaPost, pk=pk, project=request.project)

    try:
        body = json.loads(request.body)
        new_dt_str = body.get('scheduled_at')
        if not new_dt_str:
            return JsonResponse({'error': 'Missing scheduled_at'}, status=400)

        new_dt = datetime.fromisoformat(new_dt_str)
        if timezone.is_naive(new_dt):
            import zoneinfo
            project_tz = zoneinfo.ZoneInfo(post.project.timezone)
            new_dt = timezone.make_aware(new_dt, project_tz)

        if new_dt < timezone.now():
            return JsonResponse({'error': 'Cannot schedule in the past'}, status=400)

        post.scheduled_at = new_dt
        post.save(update_fields=['scheduled_at', 'updated_at'])

        return JsonResponse({'ok': True})
    except (json.JSONDecodeError, ValueError) as e:
        return JsonResponse({'error': str(e)}, status=400)

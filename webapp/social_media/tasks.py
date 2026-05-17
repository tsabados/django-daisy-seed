"""
Django-Q2 tasks for social media publishing.
"""

import logging

from django.utils import timezone
from django_q.tasks import async_task
from django_eventstream import send_event

from .models import SocialMediaPost
from .publisher import publish_post

logger = logging.getLogger(__name__)


def _notify_publish_done(post_id, post_status, results):
    """Send SSE event so the browser knows publishing has completed."""
    successes = [p for p, r in results.items() if r['success']]
    failures = {p: r['error'] for p, r in results.items() if not r['success']}

    # Resolve user_id from the post
    try:
        user_id = SocialMediaPost.objects.values_list('user_id', flat=True).get(pk=post_id)
    except SocialMediaPost.DoesNotExist:
        logger.error('_notify_publish_done: post %d not found', post_id)
        return

    try:
        send_event(f'user-{user_id}', 'message', {
            'type': 'publish-done',
            'post_id': post_id,
            'status': post_status,
            'successes': successes,
            'failures': failures,
        })
    except Exception:
        logger.exception('Failed to send SSE publish-done event for post %d', post_id)


def publish_post_task(post_id, base_url=''):
    """
    Async task: marks post as 'publishing', calls publish_post(),
    then sets status to 'published' (any success) or 'failed' (all failed).
    """
    try:
        post = SocialMediaPost.objects.select_related('project').get(pk=post_id)
    except SocialMediaPost.DoesNotExist:
        logger.error('publish_post_task: post %d not found', post_id)
        return

    post.status = 'publishing'
    post.save(update_fields=['status'])

    try:
        results = publish_post(post, post.project, base_url=base_url)
    except Exception:
        logger.exception('publish_post_task: unexpected error for post %d', post_id)
        post.status = 'failed'
        post.save(update_fields=['status'])
        _notify_publish_done(post_id, 'failed', {})
        return

    all_failed = results and all(not r['success'] for r in results.values())
    if all_failed:
        post.status = 'failed'
        post.save(update_fields=['status'])

    _notify_publish_done(post_id, post.status, results)


def check_scheduled_posts():
    """
    Periodic task (every minute): enqueue publish_post_task for all scheduled
    posts whose scheduled_at is in the past.
    """
    now = timezone.now()
    posts = SocialMediaPost.objects.filter(
        status='scheduled',
        scheduled_at__lte=now,
    ).select_related('project')

    for post in posts:
        # Mark as publishing immediately to prevent double-queuing on next tick
        post.status = 'publishing'
        post.save(update_fields=['status'])
        async_task('social_media.tasks.publish_post_task', post.pk)
        logger.info('Enqueued publish task for scheduled post %d (%s)', post.pk, post.title)


def _notify_generation_done(post_id, processing_status, error='', shared_text='', media=None):
    """Send SSE event so the browser knows generation has completed."""
    try:
        user_id = SocialMediaPost.objects.values_list('user_id', flat=True).get(pk=post_id)
    except SocialMediaPost.DoesNotExist:
        logger.error('_notify_generation_done: post %d not found', post_id)
        return

    payload = {
        'type': 'generation-done',
        'post_id': post_id,
        'processing_status': processing_status,
        'error': error,
    }
    if processing_status == 'completed':
        payload['shared_text'] = shared_text
        payload['media'] = media or []

    try:
        send_event(f'user-{user_id}', 'message', payload)
    except Exception:
        logger.exception('Failed to send SSE generation-done event for post %d', post_id)


def generate_post_task(post_id, brand_id, topic, post_type, seed_media_ids, platforms, skip_credits=False):
    """
    Async task: generates post text and media via AI, then saves results to the post.
    Sets processing_status to 'generating' -> 'completed' or 'error'.

    When skip_credits=True, media generation always runs but no credits are deducted.
    """
    from brand.models import Brand
    from media_library.models import Media
    from credits.models import available_credits, spend_credits
    from credits.constants import IMAGE_GENERATION_COST
    from services.ai_services import generate_post_text, generate_post_media

    try:
        post = SocialMediaPost.objects.select_related('project', 'user').get(pk=post_id)
    except SocialMediaPost.DoesNotExist:
        logger.error('generate_post_task: post %d not found', post_id)
        return

    try:
        brand = Brand.objects.get(pk=brand_id)
    except Brand.DoesNotExist:
        logger.error('generate_post_task: brand %d not found', brand_id)
        post.processing_status = 'error'
        post.save(update_fields=['processing_status'])
        _notify_generation_done(post_id, 'error', 'Brand not found')
        return

    seed_media = list(
        Media.objects.filter(
            id__in=seed_media_ids,
            media_group__project=post.project,
        )
    ) if seed_media_ids else []

    try:
        # Generate text
        text = generate_post_text(brand, topic, post_type, seed_media, platforms)
        if text:
            post.shared_text = text

        # Generate media
        try:
            if skip_credits or available_credits(post.user) >= IMAGE_GENERATION_COST:
                media = generate_post_media(brand, topic, post_type, seed_media, post.user, project=post.project)
                if media:
                    if not skip_credits:
                        spend_credits(post.user, IMAGE_GENERATION_COST, 'Post media generation')
                    from .models import SocialMediaPostMedia
                    SocialMediaPostMedia.objects.create(
                        post=post,
                        media=media,
                        sort_order=0,
                    )
        except Exception:
            logger.exception('generate_post_task: media generation failed for post %d (text ok)', post_id)

        post.processing_status = 'completed'
        post.save(update_fields=['shared_text', 'processing_status'])
        media_items = [
            {'id': m.media.id, 'url': m.media.url, 'is_video': m.media.is_video}
            for m in post.shared_media.select_related('media').order_by('sort_order')
        ]
        _notify_generation_done(post_id, 'completed', shared_text=post.shared_text, media=media_items)

    except Exception as e:
        logger.exception('generate_post_task: failed for post %d', post_id)
        post.processing_status = 'error'
        post.save(update_fields=['processing_status'])
        _notify_generation_done(post_id, 'error', str(e))


def _notify_topic_suggestions(user_id, payload):
    try:
        send_event(f'user-{user_id}', 'message', {'type': 'topic-suggestions', **payload})
    except Exception:
        logger.exception('Failed to send SSE topic-suggestions event for user %d', user_id)


def suggest_topic_task(user_id, brand_id, seed_media_ids, media_type='image', video_type='teaser'):
    """
    Async task: generate topic/brief suggestions and notify the user via SSE.

    For media_type='image': returns a list of text topic strings.
    For media_type='video': returns a list of 5 brief dicts from video brief generation.
    """
    from brand.models import Brand
    from media_library.models import Media
    from services.ai_services import suggest_topic

    try:
        brand = Brand.objects.get(pk=brand_id)
    except Brand.DoesNotExist:
        logger.error('suggest_topic_task: brand %d not found', brand_id)
        _notify_topic_suggestions(user_id, {'error': 'Brand not found'})
        return

    seed_media = list(
        Media.objects.filter(id__in=seed_media_ids).select_related('media_group')
    ) if seed_media_ids else []

    if media_type == 'video':
        from services.video_service import generate_video_briefs, VideoServiceError

        if not seed_media:
            _notify_topic_suggestions(user_id, {'error': 'Select seed media to generate video briefs.'})
            return

        try:
            briefs = generate_video_briefs(seed_media_ids, brand, video_type)
        except VideoServiceError as exc:
            logger.exception('suggest_topic_task: video brief generation failed for user %d', user_id)
            _notify_topic_suggestions(user_id, {'error': str(exc)})
            return
        except Exception:
            logger.exception('suggest_topic_task: unexpected error for user %d', user_id)
            _notify_topic_suggestions(user_id, {'error': 'Failed to generate video briefs.'})
            return

        _notify_topic_suggestions(user_id, {'briefs': briefs, 'video_type': video_type})
    else:
        try:
            topics = suggest_topic(brand, seed_media)
        except Exception:
            logger.exception('suggest_topic_task: topic generation failed for user %d', user_id)
            _notify_topic_suggestions(user_id, {'error': 'Failed to suggest topics.'})
            return
        _notify_topic_suggestions(user_id, {'topics': topics})


def generate_video_post_task(post_id, brand_id):
    """
    Async task: generate a video for a social media post.

    Reads post.video_brief (stored JSON) and post.video_type, generates a script,
    submits to Muapi, downloads the result, attaches it to the post, and spends 5 credits.
    """
    from brand.models import Brand
    from credits.models import available_credits, spend_credits
    from credits.constants import VIDEO_GENERATION_COST
    from services.video_service import generate_video_script, render_video_to_media, VideoServiceError
    from .models import SocialMediaPostMedia

    try:
        post = SocialMediaPost.objects.select_related('project', 'user').get(pk=post_id)
    except SocialMediaPost.DoesNotExist:
        logger.error('generate_video_post_task: post %d not found', post_id)
        return

    try:
        brand = Brand.objects.get(pk=brand_id)
    except Brand.DoesNotExist:
        logger.error('generate_video_post_task: brand %d not found', brand_id)
        post.processing_status = 'error'
        post.save(update_fields=['processing_status'])
        _notify_generation_done(post_id, 'error', 'Brand not found')
        return

    if available_credits(post.user) < VIDEO_GENERATION_COST:
        post.processing_status = 'error'
        post.save(update_fields=['processing_status'])
        _notify_generation_done(post_id, 'error', 'Insufficient credits for video generation.')
        return

    brief = post.video_brief
    if not brief or not isinstance(brief, dict):
        post.processing_status = 'error'
        post.save(update_fields=['processing_status'])
        _notify_generation_done(post_id, 'error', 'No video brief saved on post.')
        return

    seed_media_ids = list(
        post.seed_media.select_related('media').values_list('media_id', flat=True)
    )
    video_type = post.video_type or 'teaser'

    try:
        script_dict = generate_video_script(seed_media_ids, brand, brief, video_type)
    except (VideoServiceError, Exception) as exc:
        logger.exception('generate_video_post_task: script generation failed for post %d', post_id)
        post.processing_status = 'error'
        post.save(update_fields=['processing_status'])
        _notify_generation_done(post_id, 'error', str(exc))
        return

    try:
        media = render_video_to_media(script_dict, post.user, post.project, seed_media_ids=seed_media_ids)
    except (VideoServiceError, Exception) as exc:
        logger.exception('generate_video_post_task: video render failed for post %d', post_id)
        post.processing_status = 'error'
        post.save(update_fields=['processing_status'])
        _notify_generation_done(post_id, 'error', str(exc))
        return

    spend_credits(post.user, VIDEO_GENERATION_COST, 'Video post generation')

    SocialMediaPostMedia.objects.create(post=post, media=media, sort_order=0)

    post.processing_status = 'completed'
    post.save(update_fields=['processing_status'])

    media_items = [
        {'id': m.media.id, 'url': m.media.url, 'is_video': m.media.is_video}
        for m in post.shared_media.select_related('media').order_by('sort_order')
    ]
    _notify_generation_done(post_id, 'completed', shared_text=post.shared_text, media=media_items)


def autopost_all_projects_task():
    """
    Periodic task (daily at 9am UTC): enqueue autopost_project_task for every
    project that has autopost enabled.
    """
    from projects.models import Project

    projects = Project.objects.filter(enable_autopost=True)
    for project in projects:
        async_task('social_media.tasks.autopost_project_task', project.pk)
        logger.info('Enqueued autopost task for project %d (%s)', project.pk, project.name)


def autopost_project_task(project_id):
    """
    Async task: generates one AI inspiration for a project and creates a draft post
    with text and image via generate_post_task (no credits spent). Sends an email
    notification to the project owner.
    """
    import random
    from urllib.parse import urljoin
    from django.conf import settings
    from brand.models import Brand
    from media_library.models import MediaGroup, Media
    from services.ai_services import suggest_topic
    from home.tasks import send_email_task
    from projects.models import Project
    from .models import SocialMediaPostSeedImage, SocialMediaPostPlatform

    try:
        project = Project.objects.select_related('owner').get(pk=project_id)
    except Project.DoesNotExist:
        logger.error('autopost_project_task: project %d not found', project_id)
        return

    # Load brand
    try:
        brand = Brand.objects.get(project=project)
        if not brand.has_data:
            logger.info('autopost_project_task: brand has no data for project %d, skipping', project_id)
            return
    except Brand.DoesNotExist:
        logger.info('autopost_project_task: no brand for project %d, skipping', project_id)
        return

    # Get product groups
    product_groups = list(
        MediaGroup.objects.filter(project=project, type=MediaGroup.GroupType.PRODUCT)
    )
    if not product_groups:
        logger.info('autopost_project_task: no products for project %d, skipping', project_id)
        return

    # Random selection of one product group
    group = random.choice(product_groups)

    # Get seed media (up to 2 non-generated items)
    media_items = list(
        group.media_items.exclude(source_type=Media.SourceType.GENERATED).all()
    )
    seed_media = media_items[:2]

    # Generate topic (inspiration)
    try:
        topics = suggest_topic(brand, seed_media)
        topic = topics[0] if topics else ''
    except Exception:
        logger.exception('autopost_project_task: topic generation failed for project %d', project_id)
        return

    if not topic:
        logger.warning('autopost_project_task: empty topic for project %d, skipping', project_id)
        return

    # Create the draft post (empty text, generating state — generate_post_task will fill it)
    post = SocialMediaPost.objects.create(
        user=project.owner,
        project=project,
        title=topic[:200],
        shared_text='',
        topic=topic,
        post_type='product',
        status='draft',
        processing_status='generating',
    )

    # Assign a default scheduled_at (next available slot at project's publish time)
    from .views import _assign_default_scheduled_at
    _assign_default_scheduled_at(post, project)
    post.save(update_fields=['scheduled_at'])

    # Attach seed media
    seed_media_ids = []
    for i, media_item in enumerate(seed_media):
        SocialMediaPostSeedImage.objects.create(post=post, media=media_item, sort_order=i)
        seed_media_ids.append(media_item.id)

    # Create platform variants for all enabled platforms
    platforms = project.get_enabled_platforms()
    for platform in platforms:
        SocialMediaPostPlatform.objects.create(
            post=post,
            platform=platform,
            is_enabled=True,
            use_shared_text=True,
            use_shared_media=True,
        )

    # Generate text + image synchronously, without spending credits
    generate_post_task(post.pk, brand.pk, topic, 'product', seed_media_ids, platforms, skip_credits=True)

    # Reload to get generated content and image
    post.refresh_from_db()
    first_shared_media = post.shared_media.select_related('media').first()
    post_image_url = None
    if first_shared_media:
        raw_url = first_shared_media.media.url
        post_image_url = raw_url if raw_url.startswith('http') else urljoin(settings.SITE_URL, raw_url)

    # Get brand logo URL
    brand_logo_url = None
    if brand.logo_id:
        logo_media = brand.logo.media_items.first()
        if logo_media:
            raw_url = logo_media.url
            brand_logo_url = raw_url if raw_url.startswith('http') else urljoin(settings.SITE_URL, raw_url)

    # Send email notification to project owner
    calendar_url = f'{settings.SITE_URL}/scheduler/?project_id={project.pk}&post={post.pk}'
    async_task(
        send_email_task,
        f'Your post idea is ready! - {project.name}',
        'social_media/email/autopost_notification.html',
        'social_media/email/autopost_notification.txt',
        {
            'user_email': project.owner.email,
            'project_name': project.name,
            'post_title': post.title,
            'post_text': post.shared_text,
            'post_image_url': post_image_url,
            'brand_logo_url': brand_logo_url,
            'calendar_url': calendar_url,
        },
        project.owner.email,
    )
    logger.info('autopost_project_task: created post %d for project %d', post.pk, project_id)

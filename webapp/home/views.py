import logging
import os
import uuid

import requests as http_requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_q.tasks import async_task

from accounts.forms import ProfileForm
from brand.models import Brand
from credits.models import CreditGrant, available_credits
from credits.views import get_subscription_info
from integrations.models import IntegrationConnection
from media_library.models import Media, MediaGroup
from social_media.models import SocialMediaPost

UNSPLASH_ACCESS_KEY = os.environ.get('UNSPLASH_ACCESS_KEY', '')

logger = logging.getLogger(__name__)


@login_required
def home(request):
    now = timezone.now()
    # Monday of the current week
    week_start = now - timezone.timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timezone.timedelta(days=7)

    drafts = list(
        SocialMediaPost.objects.filter(project=request.project, status='draft')
        .prefetch_related('shared_media__media')
        .order_by('-updated_at')[:4]
    )
    for post in drafts:
        all_media = list(post.shared_media.all())
        post.preview_media = all_media[:3]
        post.extra_media_count = max(0, len(all_media) - 3)

    scheduled_posts = (
        SocialMediaPost.objects.filter(
            project=request.project,
            status='scheduled',
            scheduled_at__gte=week_start,
            scheduled_at__lt=week_end,
        )
        .prefetch_related('platforms')
        .order_by('scheduled_at')
    )

    try:
        brand = Brand.objects.get(project=request.project)
        has_brand = brand.has_data
    except Brand.DoesNotExist:
        brand = None
        has_brand = False

    has_products = MediaGroup.objects.filter(project=request.project, type=MediaGroup.GroupType.PRODUCT).exists()

    media_groups = (
        MediaGroup.objects.filter(project=request.project, type=MediaGroup.GroupType.MANUAL)
        .prefetch_related('media_items')
        .order_by('-created_at')[:6]
    )

    has_socials = IntegrationConnection.objects.filter(
        project=request.project,
        provider_category=IntegrationConnection.ProviderCategory.SOCIAL_MEDIA,
        status=IntegrationConnection.ConnectionStatus.ACTIVE,
    ).exists()

    is_scraping = brand is not None and brand.processing_status == Brand.ProcessingStatus.SCRAPING

    return render(request, "home/home.html", {
        'drafts': drafts,
        'scheduled_posts': scheduled_posts,
        'brand': brand,
        'has_brand': has_brand,
        'has_products': has_products,
        'has_socials': has_socials,
        'is_scraping': is_scraping,
        'media_groups': media_groups,
        'auto_provision_project_id': request.session.pop('auto_provision_project_id', None),
    })


@login_required
def inspiration_cards(request):
    from brand.models import Brand as BrandModel
    import random

    try:
        brand = BrandModel.objects.get(project=request.project)
        has_brand = brand.has_data
    except BrandModel.DoesNotExist:
        has_brand = False

    product_groups = list(
        MediaGroup.objects.filter(project=request.project, type=MediaGroup.GroupType.PRODUCT)
    )

    slots = []
    if has_brand and product_groups:
        selected = random.sample(product_groups, min(6, len(product_groups)))
        for group in selected:
            slot_id = uuid.uuid4().hex
            async_task(
                'home.tasks.generate_inspiration_card_task',
                request.project.id,
                request.user.id,
                group.id,
                slot_id,
            )
            slots.append({'slot_id': slot_id, 'group_title': group.title})

    return render(request, 'home/_inspiration_loading.html', {'slots': slots})


@login_required
def inspiration_card_result(request):
    cache_key = request.GET.get('key', '')
    if not cache_key.startswith('inspiration_card_') or len(cache_key) > 60:
        return HttpResponse('')

    cached = cache.get(cache_key)
    if not cached or cached.get('project_id') != request.project.id:
        return HttpResponse('')

    card_data = cached.get('card')
    if not card_data:
        return HttpResponse('')

    media_obj = None
    if card_data.get('media'):
        media_obj = Media.objects.filter(id=card_data['media']).first()

    card = {
        'group': {'title': card_data['group_title']},
        'media': media_obj,
        'topic': card_data['topic'],
        'seed_media_ids': card_data['seed_media_ids'],
    }

    return render(request, 'home/_inspiration_card.html', {'card': card})

@login_required
def settings(request):
    profile_form = ProfileForm(instance=request.user)

    if request.method == "POST":
        profile_form = ProfileForm(request.POST, instance=request.user)
        if profile_form.is_valid():
            profile_form.save()
            messages.success(request, "Profile updated.")
            return redirect("settings")

    now = timezone.now()
    active_grants = list(
        CreditGrant.objects.filter(user=request.user, expires_at__gt=now).order_by('expires_at')
    )
    total_credits = sum(g.remaining for g in active_grants)
    subscription = get_subscription_info(request.user)

    return render(request, "home/settings.html", {
        "form": profile_form,
        "active_grants": active_grants,
        "total_credits": total_credits,
        "subscription": subscription,
    })


@login_required
def unsplash_photos(request):
    photos = []
    error = None
    search_term = None
    if UNSPLASH_ACCESS_KEY:
        try:
            try:
                brand = Brand.objects.get(project=request.project)
                if brand.has_data:
                    from services.ai_services import get_unsplash_search_term
                    search_term = get_unsplash_search_term(brand)
            except Brand.DoesNotExist:
                pass

            if search_term:
                resp = http_requests.get(
                    'https://api.unsplash.com/search/photos',
                    params={'query': search_term, 'per_page': 6},
                    headers={'Authorization': f'Client-ID {UNSPLASH_ACCESS_KEY}'},
                    timeout=10,
                )
                resp.raise_for_status()
                photos = resp.json().get('results', [])
            else:
                resp = http_requests.get(
                    'https://api.unsplash.com/photos/random',
                    params={'count': 6},
                    headers={'Authorization': f'Client-ID {UNSPLASH_ACCESS_KEY}'},
                    timeout=10,
                )
                resp.raise_for_status()
                photos = resp.json()
        except Exception:
            logger.exception('Failed to fetch Unsplash photos')
            error = 'Could not load photos from Unsplash.'
    return render(request, 'home/_unsplash_inspiration.html', {'photos': photos, 'error': error, 'search_term': search_term})


@login_required
@require_POST
def save_unsplash_media(request):
    photo_url = request.POST.get('photo_url', '').strip()
    photo_id = request.POST.get('photo_id', '').strip()
    title = request.POST.get('title', '').strip() or 'Unsplash Photo'

    if photo_url and photo_id:
        group = MediaGroup.objects.create(
            user=request.user,
            project=request.project,
            title=title,
            type=MediaGroup.GroupType.MANUAL,
        )
        Media.objects.create(media_group=group, external_url=photo_url)

    return render(request, 'home/_unsplash_save_button.html', {
        'saved': True,
        'photo_id': photo_id,
    })

"""
Social media publishing service.

Each platform function publishes one SocialMediaPostPlatform variant
using its connected IntegrationConnection credentials.

The central `publish_post` function checks which platforms have active
connections and dispatches to the appropriate publisher.
"""

import logging
import mimetypes
import time

import requests as http_requests
from django.utils import timezone

from integrations.models import IntegrationConnection

logger = logging.getLogger(__name__)

LINKEDIN_API_BASE = 'https://api.linkedin.com'
GRAPH_API_BASE = 'https://graph.facebook.com/v22.0'
IG_GRAPH_BASE = 'https://graph.instagram.com/v25.0'
TIKTOK_API_BASE = 'https://open.tiktokapis.com/v2'


# ─── Media helpers ────────────────────────────────────────────────────────────


def _get_media_bytes_and_type(media_item):
    """Return (bytes_data, content_type) for a media_library.Media instance."""
    if media_item.file:
        # Local stored file — read from storage
        with media_item.file.open('rb') as f:
            data = f.read()
        mime, _ = mimetypes.guess_type(media_item.file.name)
        return data, mime or ('video/mp4' if media_item.is_video else 'image/jpeg')
    # External URL — download it
    resp = http_requests.get(media_item.external_url, timeout=60)
    resp.raise_for_status()
    ct = resp.headers.get('Content-Type', 'image/jpeg').split(';')[0].strip()
    return resp.content, ct


def _get_absolute_media_url(media_item, base_url):
    """
    Return a publicly accessible URL for a media item.
    For local Django-stored files the base_url is prepended to the storage-relative URL.
    If the storage backend already returns an absolute URL (e.g. S3), use it as-is.
    """
    if media_item.external_url:
        return media_item.external_url
    file_url = media_item.file.url
    if file_url.startswith(('http://', 'https://')):
        return file_url
    return base_url.rstrip('/') + file_url


# Keep old names as aliases for any code that hasn't been updated yet.
_get_image_bytes_and_type = _get_media_bytes_and_type
_get_absolute_image_url = _get_absolute_media_url


# ─── LinkedIn ────────────────────────────────────────────────────────────────


def publish_to_linkedin(platform_variant, connection, base_url=''):
    """Publish to LinkedIn using the REST Posts API (LinkedIn-Version 202411)."""
    token = connection.access_token
    member_id = connection.external_account_id
    author_urn = f'urn:li:person:{member_id}'
    text = platform_variant.get_effective_text()
    media = list(platform_variant.get_effective_media())

    auth_headers = {
        'Authorization': f'Bearer {token}',
        'LinkedIn-Version': '202604',
        'X-Restli-Protocol-Version': '2.0.0',
    }

    media_urns = []
    for media in media:
        media_data, content_type = _get_media_bytes_and_type(media.media)
        is_video = media.media.is_video

        if is_video:
            # Step 1 — initialize video upload
            init_resp = http_requests.post(
                f'{LINKEDIN_API_BASE}/rest/videos?action=initializeUpload',
                headers={**auth_headers, 'Content-Type': 'application/json'},
                json={
                    'initializeUploadRequest': {
                        'owner': author_urn,
                        'fileSizeBytes': len(media_data),
                        'uploadCaptions': False,
                        'uploadThumbnail': False,
                    }
                },
                timeout=30,
            )
            init_resp.raise_for_status()
            init_data = init_resp.json()
            upload_instructions = init_data['value']['uploadInstructions']
            video_urn = init_data['value']['video']

            # Step 2 — upload chunks
            etags = []
            for instruction in upload_instructions:
                chunk_start = instruction['firstByte']
                chunk_end = instruction['lastByte'] + 1
                chunk_upload_url = instruction['uploadUrl']
                chunk_data = media_data[chunk_start:chunk_end]
                put_resp = http_requests.put(
                    chunk_upload_url,
                    headers={'Authorization': f'Bearer {token}'},
                    data=chunk_data,
                    timeout=120,
                )
                put_resp.raise_for_status()
                etags.append(put_resp.headers.get('ETag', '').strip('"'))

            # Step 3 — finalize upload
            finalize_resp = http_requests.post(
                f'{LINKEDIN_API_BASE}/rest/videos?action=finalizeUpload',
                headers={**auth_headers, 'Content-Type': 'application/json'},
                json={'finalizeUploadRequest': {'video': video_urn, 'uploadToken': '', 'uploadedPartIds': etags}},
                timeout=30,
            )
            finalize_resp.raise_for_status()

            # Step 4 — wait for LinkedIn to finish processing the video
            for _ in range(30):
                time.sleep(5)
                status_resp = http_requests.get(
                    f'{LINKEDIN_API_BASE}/rest/videos/{video_urn.replace(":", "%3A")}',
                    headers=auth_headers,
                    timeout=15,
                )
                if status_resp.ok:
                    video_status = status_resp.json().get('status', '')
                    if video_status == 'AVAILABLE':
                        break
                    if video_status == 'PROCESSING_FAILED':
                        raise RuntimeError(f'LinkedIn video processing failed for {video_urn}')
            else:
                raise RuntimeError(f'LinkedIn video {video_urn} did not become available in time')

            media_urns.append(('video', video_urn))
        else:
            # Step 1 — initialize media upload
            init_resp = http_requests.post(
                f'{LINKEDIN_API_BASE}/rest/images?action=initializeUpload',
                headers={**auth_headers, 'Content-Type': 'application/json'},
                json={'initializeUploadRequest': {'owner': author_urn}},
                timeout=30,
            )
            init_resp.raise_for_status()
            init_data = init_resp.json()
            upload_url = init_data['value']['uploadUrl']
            image_urn = init_data['value']['image']

            # Step 2 — upload binary
            put_resp = http_requests.put(
                upload_url,
                headers={
                    'Authorization': f'Bearer {token}',
                    'Content-Type': content_type,
                },
                data=media_data,
                timeout=60,
            )
            put_resp.raise_for_status()
            media_urns.append(('media', image_urn))

    # Build post payload
    payload = {
        'author': author_urn,
        'commentary': text,
        'visibility': 'PUBLIC',
        'distribution': {
            'feedDistribution': 'MAIN_FEED',
            'targetEntities': [],
            'thirdPartyDistributionChannels': [],
        },
        'lifecycleState': 'PUBLISHED',
        'isReshareDisabledByAuthor': False,
    }

    if len(media_urns) == 1:
        kind, urn = media_urns[0]
        if kind == 'video':
            payload['content'] = {'media': {'id': urn, 'title': 'Video'}}
        else:
            payload['content'] = {'media': {'id': urn, 'title': 'Image'}}
    elif len(media_urns) > 1:
        # Multi-media only (LinkedIn doesn't support mixed video+media or multi-video)
        image_urns = [urn for kind, urn in media_urns if kind == 'media']
        if image_urns:
            payload['content'] = {
                'multiImage': {
                    'images': [
                        {'id': urn, 'altText': f'Image {i + 1}'}
                        for i, urn in enumerate(image_urns)
                    ]
                }
            }

    post_resp = http_requests.post(
        f'{LINKEDIN_API_BASE}/rest/posts',
        headers={**auth_headers, 'Content-Type': 'application/json'},
        json=payload,
        timeout=30,
    )
    post_resp.raise_for_status()
    post_urn = post_resp.headers.get('x-restli-id', '')
    if post_urn:
        return f'https://www.linkedin.com/feed/update/{post_urn}/'
    return ''


# ─── Facebook ─────────────────────────────────────────────────────────────────


def publish_to_facebook(platform_variant, connection, base_url=''):
    """Publish to a Facebook Page using the Graph API."""
    access_token = connection.access_token
    page_id = connection.external_account_id
    text = platform_variant.get_effective_text()
    media = list(platform_variant.get_effective_media())

    if not media:
        # Text-only post
        resp = http_requests.post(
            f'{GRAPH_API_BASE}/{page_id}/feed',
            data={'message': text, 'access_token': access_token},
            timeout=30,
        )
        resp.raise_for_status()
        post_id = resp.json().get('id', '')
        return f'https://www.facebook.com/{post_id}' if post_id else ''

    # Check if first item is a video — Facebook video posts take a single video
    first_media = media[0].media
    if first_media.is_video:
        video_url = _get_absolute_media_url(first_media, base_url)
        # If the video is already on a public URL (e.g. S3), use file_url to avoid
        # downloading and re-uploading potentially large files.
        if video_url.startswith(('http://', 'https://')):
            resp = http_requests.post(
                f'{GRAPH_API_BASE}/{page_id}/videos',
                data={'description': text, 'access_token': access_token, 'file_url': video_url},
                timeout=60,
            )
        else:
            media_data, content_type = _get_media_bytes_and_type(first_media)
            resp = http_requests.post(
                f'{GRAPH_API_BASE}/{page_id}/videos',
                data={'description': text, 'access_token': access_token},
                files={'source': ('video', media_data, content_type)},
                timeout=180,
            )
        if not resp.ok:
            raise RuntimeError(f'Facebook video upload failed ({resp.status_code}): {resp.text}')
        video_id = resp.json().get('id', '')
        return f'https://www.facebook.com/{video_id}' if video_id else ''
    elif len(media) == 1:
        # Single-media post (creates the post directly)
        media_data, content_type = _get_media_bytes_and_type(first_media)
        resp = http_requests.post(
            f'{GRAPH_API_BASE}/{page_id}/photos',
            data={'message': text, 'access_token': access_token},
            files={'source': ('media', media_data, content_type)},
            timeout=60,
        )
        resp.raise_for_status()
        post_id = resp.json().get('post_id', resp.json().get('id', ''))
        return f'https://www.facebook.com/{post_id}' if post_id else ''
    else:
        # Multi-media: upload each photo as unpublished, then create feed post
        photo_ids = []
        for media in media[:10]:  # Facebook max 10
            media_data, content_type = _get_media_bytes_and_type(media.media)
            photo_resp = http_requests.post(
                f'{GRAPH_API_BASE}/{page_id}/photos',
                data={'published': 'false', 'access_token': access_token},
                files={'source': ('media', media_data, content_type)},
                timeout=60,
            )
            photo_resp.raise_for_status()
            photo_ids.append(photo_resp.json()['id'])

        import json as _json
        feed_resp = http_requests.post(
            f'{GRAPH_API_BASE}/{page_id}/feed',
            data={
                'message': text,
                'access_token': access_token,
                'attached_media': _json.dumps([{'media_fbid': pid} for pid in photo_ids]),
            },
            timeout=30,
        )
        feed_resp.raise_for_status()
        post_id = feed_resp.json().get('id', '')
        return f'https://www.facebook.com/{post_id}' if post_id else ''


def _wait_for_ig_container(container_id, access_token, timeout=90, interval=5):
    """
    Poll the Instagram container status until it reaches FINISHED.
    Raises RuntimeError on ERROR status or if timeout is exceeded.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status_resp = http_requests.get(
            f'{IG_GRAPH_BASE}/{container_id}',
            params={'fields': 'status_code', 'access_token': access_token},
            timeout=30,
        )
        status_resp.raise_for_status()
        status_code = status_resp.json().get('status_code')
        if status_code == 'FINISHED':
            return
        if status_code == 'ERROR':
            raise RuntimeError(f'Instagram media container {container_id} failed with ERROR status.')
        time.sleep(interval)
    raise RuntimeError(
        f'Instagram media container {container_id} did not finish processing within {timeout}s '
        f'(last status: {status_code}).'
    )


# ─── Instagram ────────────────────────────────────────────────────────────────


def publish_to_instagram(platform_variant, connection, base_url=''):
    """
    Publish to Instagram using the Instagram Graph API.
    Images → single media or carousel container.
    Video → single REELS container.
    """
    access_token = connection.access_token
    ig_user_id = connection.external_account_id
    text = platform_variant.get_effective_text()
    media = list(platform_variant.get_effective_media())

    if not media:
        raise ValueError('Instagram requires at least one media or video to publish.')

    first_media = media[0].media

    if first_media.is_video:
        # Video post (Reels)
        video_url = _get_absolute_media_url(first_media, base_url)
        container_resp = http_requests.post(
            f'{IG_GRAPH_BASE}/{ig_user_id}/media',
            data={
                'video_url': video_url,
                'media_type': 'REELS',
                'caption': text,
                'access_token': access_token,
            },
            timeout=30,
        )
        if not container_resp.ok:
            raise RuntimeError(f'Instagram Reels container failed ({container_resp.status_code}): {container_resp.text}')
        container_id = container_resp.json()['id']
    elif len(media) == 1:
        image_url = _get_absolute_media_url(first_media, base_url)
        container_resp = http_requests.post(
            f'{IG_GRAPH_BASE}/{ig_user_id}/media',
            data={
                'image_url': image_url,
                'media_type': 'IMAGE',
                'caption': text,
                'access_token': access_token,
            },
            timeout=30,
        )
        if not container_resp.ok:
            raise RuntimeError(f'Instagram image container failed ({container_resp.status_code}): {container_resp.text}')
        container_id = container_resp.json()['id']
    else:
        # Carousel: create one item container per media first
        item_ids = []
        for media in media[:10]:  # Instagram carousel max 10
            image_url = _get_absolute_media_url(media.media, base_url)
            item_resp = http_requests.post(
                f'{IG_GRAPH_BASE}/{ig_user_id}/media',
                data={
                    'image_url': image_url,
                    'media_type': 'IMAGE',
                    'is_carousel_item': 'true',
                    'access_token': access_token,
                },
                timeout=30,
            )
            if not item_resp.ok:
                raise RuntimeError(f'Instagram carousel item failed ({item_resp.status_code}): {item_resp.text}')
            item_ids.append(item_resp.json()['id'])

        carousel_resp = http_requests.post(
            f'{IG_GRAPH_BASE}/{ig_user_id}/media',
            data={
                'media_type': 'CAROUSEL',
                'children': ','.join(item_ids),
                'caption': text,
                'access_token': access_token,
            },
            timeout=30,
        )
        if not carousel_resp.ok:
            raise RuntimeError(f'Instagram carousel container failed ({carousel_resp.status_code}): {carousel_resp.text}')
        container_id = carousel_resp.json()['id']

    # Wait for the container to finish processing before publishing
    _wait_for_ig_container(container_id, access_token)

    # Publish the container
    publish_resp = http_requests.post(
        f'{IG_GRAPH_BASE}/{ig_user_id}/media_publish',
        data={
            'creation_id': container_id,
            'access_token': access_token,
        },
        timeout=30,
    )
    publish_resp.raise_for_status()
    media_id = publish_resp.json().get('id', '')

    # Fetch the permalink so it can be stored and displayed.
    if media_id:
        permalink_resp = http_requests.get(
            f'{IG_GRAPH_BASE}/{media_id}',
            params={'fields': 'permalink', 'access_token': access_token},
            timeout=15,
        )
        if permalink_resp.ok:
            return permalink_resp.json().get('permalink', '')
    return ''


# ─── TikTok ───────────────────────────────────────────────────────────────────


def publish_to_tiktok(platform_variant, connection, base_url=''):
    """
    Publish to TikTok using the Content Posting API.
    Videos → Direct Post Video (PULL_FROM_URL or FILE_UPLOAD).
    Photos → Direct Post Photo (PULL_FROM_URL).
    """
    access_token = connection.access_token
    text = platform_variant.get_effective_text()
    media = list(platform_variant.get_effective_media())

    if not media:
        raise ValueError('TikTok requires at least one video or photo to publish.')

    auth_headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json; charset=UTF-8',
    }

    first_media = media[0].media

    if first_media.is_video:
        # Video post — always use FILE_UPLOAD
        video_data, content_type = _get_media_bytes_and_type(first_media)
        video_size = len(video_data)
        chunk_size = min(video_size, 10_000_000)
        total_chunk_count = (video_size + chunk_size - 1) // chunk_size

        payload = {
            'post_info': {
                'title': text[:2200] if text else '',
                'privacy_level': 'SELF_ONLY',
                'disable_duet': False,
                'disable_comment': False,
                'disable_stitch': False,
            },
            'source_info': {
                'source': 'FILE_UPLOAD',
                'video_size': video_size,
                'chunk_size': chunk_size,
                'total_chunk_count': total_chunk_count,
            },
        }
        resp = http_requests.post(
            f'{TIKTOK_API_BASE}/post/publish/video/init/',
            headers=auth_headers,
            json=payload,
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(f'TikTok video init failed ({resp.status_code}): {resp.text}')
        resp_data = resp.json()
        publish_id = resp_data.get('data', {}).get('publish_id', '')
        upload_url = resp_data.get('data', {}).get('upload_url', '')

        # Upload the video in chunks
        for i in range(total_chunk_count):
            start = i * chunk_size
            end = min(start + chunk_size, video_size)
            chunk = video_data[start:end]
            put_resp = http_requests.put(
                upload_url,
                headers={
                    'Content-Range': f'bytes {start}-{end - 1}/{video_size}',
                    'Content-Type': 'video/mp4',
                },
                data=chunk,
                timeout=120,
            )
            if not put_resp.ok:
                raise RuntimeError(
                    f'TikTok video chunk upload failed ({put_resp.status_code}): {put_resp.text}'
                )

        # Poll for publish status and get the video ID
        video_id = _wait_for_tiktok_publish(publish_id, access_token)
        if video_id:
            username = connection.metadata.get('username', '')
            if username:
                return f'https://www.tiktok.com/@{username}/video/{video_id}'
        return ''

    else:
        # Photo post — use PULL_FROM_URL with photo_images
        photo_urls = []
        for m in media[:35]:  # TikTok allows up to 35 photos
            url = _get_absolute_media_url(m.media, base_url)
            if url.startswith(('http://', 'https://')):
                photo_urls.append(url)

        if not photo_urls:
            raise ValueError('TikTok photo posts require publicly accessible image URLs.')

        payload = {
            'post_info': {
                'title': text[:2200] if text else '',
                'description': text[:2200] if text else '',
                'disable_comment': False,
                'privacy_level': 'SELF_ONLY',
                'auto_add_music': True,
            },
            'source_info': {
                'source': 'PULL_FROM_URL',
                'photo_cover_index': 0,
                'photo_images': photo_urls,
            },
            'post_mode': 'DIRECT_POST',
            'media_type': 'PHOTO',
        }
        resp = http_requests.post(
            f'{TIKTOK_API_BASE}/post/publish/content/init/',
            headers=auth_headers,
            json=payload,
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(f'TikTok photo post failed ({resp.status_code}): {resp.text}')
        resp_data = resp.json()
        publish_id = resp_data.get('data', {}).get('publish_id', '')

        video_id = _wait_for_tiktok_publish(publish_id, access_token)
        if video_id:
            username = connection.metadata.get('username', '')
            if username:
                return f'https://www.tiktok.com/@{username}/video/{video_id}'
        return ''


def _wait_for_tiktok_publish(publish_id, access_token, timeout=120, interval=5):
    """Poll TikTok publish status until completion or timeout. Returns the video_id if available."""
    if not publish_id:
        return None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(interval)
        resp = http_requests.post(
            f'{TIKTOK_API_BASE}/post/publish/status/fetch/',
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json; charset=UTF-8',
            },
            json={'publish_id': publish_id},
            timeout=15,
        )
        if not resp.ok:
            continue
        data = resp.json()
        status = data.get('data', {}).get('status', '')
        if status == 'PUBLISH_COMPLETE':
            # Extract the video ID from publicaly_available_post_id list
            post_ids = data.get('data', {}).get('publicaly_available_post_id', [])
            return post_ids[0] if post_ids else None
        if status in ('FAILED', 'PUBLISH_FAILED'):
            fail_reason = data.get('data', {}).get('fail_reason', 'Unknown error')
            raise RuntimeError(f'TikTok publish failed: {fail_reason}')
    raise RuntimeError(f'TikTok publish did not complete within {timeout}s (publish_id={publish_id})')


# ─── Platform registry ───────────────────────────────────────────────────────

_PLATFORM_PUBLISHERS = {
    'linkedin': publish_to_linkedin,
    'facebook': publish_to_facebook,
    'instagram': publish_to_instagram,
    'tiktok': publish_to_tiktok,
}

# Maps the social_media platform key to the integrations provider key
_PLATFORM_TO_PROVIDER = {
    'linkedin': 'linkedin',
    'facebook': 'facebook',
    'instagram': 'instagram',
    'tiktok': 'tiktok',
}


# ─── Central publish ─────────────────────────────────────────────────────────


def publish_post(post, project, base_url=''):
    """
    Publish a SocialMediaPost to all enabled platforms that have an active
    IntegrationConnection on the project.

    Returns a dict mapping platform key → result dict::

        {
            'linkedin': {'success': True,  'error': None},
            'instagram': {'success': False, 'error': 'Instagram requires ...'},
        }

    Also updates:
    - SocialMediaPostPlatform.published_at / publish_error per platform
    - SocialMediaPost.status = 'published' and published_at if any platform succeeded
    """
    # Index active social-media connections by provider key
    connections = {
        conn.provider: conn
        for conn in IntegrationConnection.objects.filter(
            project=project,
            provider_category=IntegrationConnection.ProviderCategory.SOCIAL_MEDIA,
            status=IntegrationConnection.ConnectionStatus.ACTIVE,
        )
    }

    results = {}
    now = timezone.now()
    any_success = False

    for platform_variant in post.platforms.filter(is_enabled=True).select_related('post'):
        platform = platform_variant.platform
        provider_key = _PLATFORM_TO_PROVIDER.get(platform)
        connection = connections.get(provider_key)

        if not connection:
            results[platform] = {'success': False, 'error': 'No active connection for this platform.'}
            continue

        publisher_fn = _PLATFORM_PUBLISHERS.get(platform)
        if not publisher_fn:
            results[platform] = {'success': False, 'error': 'Unsupported platform.'}
            continue

        try:
            published_url = publisher_fn(platform_variant, connection, base_url) or ''
            platform_variant.published_at = now
            platform_variant.publish_error = ''
            platform_variant.published_url = published_url
            platform_variant.save(update_fields=['published_at', 'publish_error', 'published_url'])
            results[platform] = {'success': True, 'error': None, 'url': published_url}
            any_success = True
        except Exception as exc:
            error_msg = str(exc)
            logger.exception('Failed to publish post %d to %s', post.pk, platform)
            platform_variant.publish_error = error_msg[:500]
            platform_variant.save(update_fields=['publish_error'])
            results[platform] = {'success': False, 'error': error_msg}

    if any_success:
        post.status = 'published'
        post.published_at = now
        post.save(update_fields=['status', 'published_at'])

    return results

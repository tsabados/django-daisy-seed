import os
from enum import Enum

from django.core.files.base import ContentFile
from pydantic import BaseModel as PydanticBaseModel

from media_library.models import Media, MediaGroup
from services.prompts.social_media_edit import EDIT_ACTIONS, SOCIAL_MEDIA_EDIT_SYSTEM, SYSTEM_PROMPTS
from services.prompts.social_media_generate import SOCIAL_MEDIA_GENERATE_PROMPT
from services.prompts.social_media_image import IMAGE_PRE_PROMPTS, IMAGE_TYPOGRAPHY_SUFFIX, IMAGE_VISUAL_FIDELITY_SUFFIX
from services.prompts.social_media_topic import SOCIAL_MEDIA_TOPIC_PROMPT


class OpenAIModel(Enum):
    QUICK = 'gpt-5-nano'
    NORMAL = 'gpt-5-mini'
    FULL = 'gpt-5'


def _get_openai_client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ.get('OPENAI_API_KEY', ''))


def _extract_message_text(message):
    content = getattr(message, 'content', '')
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        chunks = []
        for part in content:
            text = ''
            if isinstance(part, dict):
                text = part.get('text') or part.get('content') or ''
            else:
                text = getattr(part, 'text', '') or getattr(part, 'content', '') or ''
            if text:
                chunks.append(str(text))
        return '\n'.join(chunks).strip()

    return str(content or '').strip()


def _openai_chat(messages, model=OpenAIModel.QUICK, text_format=None, **kwargs):
    """Send a chat request to OpenAI.

    If text_format is given, uses structured outputs and returns the parsed Pydantic object.
    Otherwise returns the response text string.
    """
    client = _get_openai_client()
    kwargs.setdefault("reasoning", {"effort": "low"})
    kwargs.update(
        model=model.value,
        input=messages,
    )
    if text_format is not None:
        kwargs["text_format"] = text_format
        response = client.responses.parse(**kwargs)
        return response.output_parsed
    response = client.responses.create(**kwargs)
    return response.output_text.strip()


def _get_gemini_client():
    from google import genai
    return genai.Client(api_key=os.environ.get('GOOGLE_API_KEY', ''))


def _build_media_descriptions(seed_media):
    if not seed_media:
        return ''
    lines = ['Seed media provided as context:']
    for i, img in enumerate(seed_media, 1):
        description = str(img.media_group.description)
        title = img.media_group.title
        img_context = f'Title:{title} - Description:{description}' if description else title
        lines.append(f'  {i}. {img_context}')
    return '\n'.join(lines)


def _get_brand_context(brand):
    return {
        'brand_name': brand.name or 'Unknown',
        'brand_summary': brand.summary or '',
        'brand_style_guide': brand.style_guide or '',
    }


def _get_language_instruction(brand):
    """Return a concrete language instruction string based on the project language."""
    project = brand.project
    language_code = getattr(project, 'language', '') or 'en'
    from django.conf.global_settings import LANGUAGES
    language_name = dict(LANGUAGES).get(language_code, 'English')
    from services.prompts.language import get_language_instruction
    return get_language_instruction(language_name)


class _BrandExtractResult(PydanticBaseModel):
    name: str
    summary: str
    style_guide: str
    tone_of_voice: str
    target_audience: str
    fonts: str
    primary_color: str
    secondary_color: str


def extract_brand_data(markdown_content, language_instruction=None):
    """Extract structured brand data from markdown content using OpenAI. Returns a dict."""
    from services.prompts.brand_extract import BRAND_EXTRACT_PROMPT

    system_content = BRAND_EXTRACT_PROMPT
    if language_instruction:
        system_content = system_content.rstrip() + f'\n{language_instruction}'

    result = _openai_chat(
        messages=[
            {'role': 'system', 'content': system_content},
            {'role': 'user', 'content': markdown_content[:20000]},
        ],
        text_format=_BrandExtractResult,
    )
    return result.model_dump()


class _UrlSelectResult(PydanticBaseModel):
    urls: list[str]


def select_brand_urls(all_urls, base_url):
    """
    Ask the LLM to pick the 5-7 URLs from all_urls most likely to contain brand data.
    Returns a list of URL strings (subset of all_urls).
    """
    from services.prompts.brand_extract import BRAND_URL_SELECT_PROMPT

    url_list = '\n'.join(all_urls[:200])  # cap to avoid token overflow
    user_msg = f'Website: {base_url}\n\nAvailable URLs:\n{url_list}'
    result = _openai_chat(
        messages=[
            {'role': 'system', 'content': BRAND_URL_SELECT_PROMPT},
            {'role': 'user', 'content': user_msg},
        ],
        text_format=_UrlSelectResult,
        model=OpenAIModel.QUICK,
    )
    # Return only valid strings that were in the original list
    all_urls_set = set(all_urls)
    return [u for u in result.urls if isinstance(u, str) and u in all_urls_set]


def select_product_urls(all_urls, base_url):
    """
    Ask the LLM to pick up to 50 URLs most likely to contain products or
    brand-relevant visual assets for social media creation.
    Returns a list of URL strings (subset of all_urls).
    """
    from services.prompts.product_url_select import PRODUCT_URL_SELECT_PROMPT

    url_list = '\n'.join(all_urls[:500])  # cap to avoid token overflow
    user_msg = f'Website: {base_url}\n\nAvailable URLs:\n{url_list}'
    result = _openai_chat(
        messages=[
            {'role': 'system', 'content': PRODUCT_URL_SELECT_PROMPT},
            {'role': 'user', 'content': user_msg},
        ],
        text_format=_UrlSelectResult,
        model=OpenAIModel.QUICK,
    )
    all_urls_set = set(all_urls)
    return [u for u in result.urls if isinstance(u, str) and u in all_urls_set][:50]


class _PageSummaryResult(PydanticBaseModel):
    title: str
    summary: str


def summarize_page_markdown(markdown_content, language_name='English'):
    """
    Extract a short title and a ~200-word summary from a web page's markdown.
    Returns a dict with 'title' and 'summary' keys.
    """
    from services.prompts.language import get_language_instruction
    lang_instruction = get_language_instruction(language_name)

    result = _openai_chat(
        messages=[
            {
                'role': 'system',
                'content': (
                    'You are given the markdown content of a web page. '
                    'Extract two things:\n'
                    '1. "title" — a short, descriptive title for the page '
                    '(the product name, service name, or main topic).\n'
                    '2. "summary" — describing the main content of the page. '
                    'Structure the summary into paragraphs separated by newlines. '
                    'Focus exclusively on the product, service, or asset presented. '
                    'Ignore navigation menus, filters, sidebars, footers, '
                    'cookie banners, and other UI chrome. '
                    'Do not describe the page layout or visual design elements.\n\n'
                    + lang_instruction
                ),
            },
            {'role': 'user', 'content': markdown_content[:15000]},
        ],
        text_format=_PageSummaryResult,
        model=OpenAIModel.QUICK,
    )
    return {'title': result.title, 'summary': result.summary}


def get_unsplash_search_term(brand):
    """Use brand data to generate a short, relevant Unsplash search term (2-3 words)."""
    ctx = _get_brand_context(brand)
    prompt = (
        "You are helping find stock photography for a brand. "
        "Based on the brand information below, return a single short search term (2-3 words) "
        "that describes the type of imagery that would resonate with this brand's audience. "
        "Return ONLY the search term, nothing else.\n\n"
        f"Brand name: {ctx['brand_name']}\n"
        f"Brand summary: {ctx['brand_summary']}\n"
    )
    if brand.target_audience:
        prompt += f"Target audience: {brand.target_audience}\n"
    return _openai_chat(
        messages=[{'role': 'user', 'content': prompt}],
    )


def suggest_topic(brand, seed_media):
    """Suggest topics based on brand context and seed media. Returns a list."""
    import re
    ctx = _get_brand_context(brand)
    lang = _get_language_instruction(brand)
    prompt = SOCIAL_MEDIA_TOPIC_PROMPT.format(
        media_descriptions=_build_media_descriptions(seed_media),
        **ctx,
    )
    raw = _openai_chat(
        messages=[
            {'role': 'system', 'content': lang},
            {'role': 'user', 'content': prompt},
        ],
    )
    topics = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        cleaned = re.sub(r'^\d+\.\s*', '', line)
        if cleaned:
            topics.append(cleaned)
    return topics if topics else [raw]


def analyze_media_visuals(media_list):
    """Run OpenAI Vision visual analysis on a list of Media objects.

    Returns a normalized visual analysis dict suitable for use in video prompts.
    """
    from services.video_service import (
        VISUAL_ANALYSIS_IMAGE_LIMIT,
        VISUAL_ANALYSIS_PROMPT,
        _call_vision_json,
        normalize_visual_analysis_response,
    )

    images = list(media_list)[:VISUAL_ANALYSIS_IMAGE_LIMIT]
    if not images:
        return {}

    group = images[0].media_group
    group_json = __import__('json').dumps({
        'id': group.id,
        'title': group.title,
        'description': group.description,
    }, indent=2)
    reference_images_json = __import__('json').dumps([
        {
            'id': m.id,
            'source': m.external_url or (m.file.name if m.file else ''),
        }
        for m in images
    ], indent=2)
    prompt = VISUAL_ANALYSIS_PROMPT.format(
        group_json=group_json,
        reference_images_json=reference_images_json,
    )
    raw = _call_vision_json(prompt, images)
    return normalize_visual_analysis_response(raw)


def _generate_gemini_media(prompt, input_media=None):
    """Call Gemini media generation and return (media_data, mime_type) or None."""
    from io import BytesIO
    import requests
    from PIL import Image as PILImage
    from google.genai import types

    _browser_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    client = _get_gemini_client()
    def _open_as_pil(path_or_bytes, is_bytes=False):
        """Open an media as PIL, converting SVG to PNG via cairosvg if needed."""
        if is_bytes:
            return PILImage.open(BytesIO(path_or_bytes))
        if path_or_bytes.lower().endswith('.svg'):
            import cairosvg
            png_bytes = cairosvg.svg2png(url=path_or_bytes)
            return PILImage.open(BytesIO(png_bytes))
        return PILImage.open(path_or_bytes)

    pil_media = []
    try:
        for img in (input_media or []):
            if img.file and img.file.name:
                is_svg = img.file.name.lower().endswith('.svg')
                with img.file.open('rb') as fh:
                    raw = fh.read()
                if is_svg:
                    import cairosvg
                    png_bytes = cairosvg.svg2png(bytestring=raw)
                    pil_media.append(PILImage.open(BytesIO(png_bytes)))
                else:
                    pil_media.append(PILImage.open(BytesIO(raw)))
            elif img.external_url:
                try:
                    resp = requests.get(img.external_url, headers=_browser_headers, timeout=15)
                    resp.raise_for_status()
                    content_type = resp.headers.get('Content-Type', '')
                    if 'svg' in content_type or img.external_url.lower().endswith('.svg'):
                        import cairosvg
                        png_bytes = cairosvg.svg2png(bytestring=resp.content)
                        pil_media.append(PILImage.open(BytesIO(png_bytes)))
                    else:
                        pil_media.append(PILImage.open(BytesIO(resp.content)))
                except requests.RequestException as exc:
                    raise RuntimeError(f'Failed to fetch media from {img.external_url}: {exc}') from exc

        contents = [prompt] + pil_media

        response = client.models.generate_content(
            model='gemini-3.1-flash-image-preview',
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=['IMAGE'],
                image_config=types.ImageConfig(
                    aspect_ratio="9:16"
                )
            ),
        )
    finally:
        for pil_img in pil_media:
            pil_img.close()

    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            return part.inline_data.data, part.inline_data.mime_type

    return None

def generate_post_text(brand, topic, post_type, seed_media, platforms):
    """Generate post text using OpenAI."""
    ctx = _get_brand_context(brand)
    lang = _get_language_instruction(brand)
    prompt = SOCIAL_MEDIA_GENERATE_PROMPT.format(
        topic=topic or 'general brand post',
        post_type=post_type or 'lifestyle',
        platforms=', '.join(platforms) if platforms else 'general',
        media_descriptions=_build_media_descriptions(seed_media),
        **ctx,
    )
    return _openai_chat(messages=[
        {'role': 'system', 'content': lang},
        {'role': 'user', 'content': prompt},
    ])

def _generate_media_prompt(brand, topic, post_type, seed_media):
    """Use OpenAI to generate a detailed media prompt based on brand, topic, post type, and seed media."""
    pre_prompt_template = IMAGE_PRE_PROMPTS.get((post_type or '').lower())
    if not pre_prompt_template:
        return None

    ctx = _get_brand_context(brand)
    brand_info = (
        f"Name: {ctx['brand_name']}\n"
        f"Summary: {ctx['brand_summary']}\n"
        f"Style: {ctx['brand_style_guide']}"
    )
    product_info = _build_media_descriptions(seed_media) or 'No product reference provided.'

    pre_prompt = pre_prompt_template.format(
        brand_info=brand_info,
        product_info=product_info,
        topic=topic or 'general brand post',
    )

    media_prompt = _openai_chat(
        messages=[{'role': 'user', 'content': pre_prompt}],
        model=OpenAIModel.NORMAL,
    )

    if (post_type or '').lower() in ('product', 'lifestyle'):
        media_prompt += IMAGE_TYPOGRAPHY_SUFFIX
    media_prompt += IMAGE_VISUAL_FIDELITY_SUFFIX

    return media_prompt


def generate_post_media(brand, topic, post_type, seed_media, user, project=None):
    """Generate an media using Gemini and save it to the media library."""
    # Step 1: generate a detailed media prompt via OpenAI
    prompt = _generate_media_prompt(brand, topic, post_type, seed_media)

    # Fallback to a simple prompt if no matching pre-prompt template exists
    if not prompt:
        seed_lines = []
        if seed_media:
            seed_lines.append('Reference media provided for context and style inspiration:')
            for i, img in enumerate(seed_media, 1):
                name = img.media.name.split('/')[-1] if img.media and img.media.name else str(img)
                description = str(img)
                seed_lines.append(f'  {i}. Name: {name} — {description}')
            seed_lines.append(
                'Use the reference media above to inform visual style, composition, and subject matter.'
            )
            seed_lines.append('')
        pre_prompt = '\n'.join(seed_lines) if seed_lines else ''
        prompt = (
            f"{pre_prompt}"
            f"Create a professional social media media for brand '{brand.name or 'brand'}'. "
            f"Topic: {topic or 'general'}. Post type: {post_type or 'lifestyle'}. "
            f"Style: {brand.style_guide or 'professional, clean, modern'}."
        )

    # Step 2: generate the actual media using the prompt
    result = _generate_gemini_media(prompt, input_media=seed_media)
    if result is None:
        return None

    media_data, mime_type = result

    if seed_media:
        group = seed_media[0].media_group
    else:
        group, _ = MediaGroup.objects.get_or_create(
            user=user,
            project=project,
            title='AI Generated Media',
            type=MediaGroup.GroupType.GENERATED,
        )

    ext = 'png' if 'png' in mime_type else 'jpg'
    media_obj = Media(
        media_group=group,
        source_type=Media.SourceType.GENERATED,
        )
    media_obj.file.save(f'ai_generated.{ext}', ContentFile(media_data), save=True)
    return media_obj


def edit_text(action, text, brand, platform=None, instruction=None, system_prompt_key=None):
    """Apply an AI edit action to text."""
    ctx = _get_brand_context(brand)
    lang = _get_language_instruction(brand)

    action_template = EDIT_ACTIONS.get(action)
    if not action_template:
        raise ValueError(f'Unknown action: {action}')

    user_prompt = action_template.format(
        text=text,
        platform=platform or '',
        instruction=instruction or '',
    )
    system_prompt_template = SYSTEM_PROMPTS.get(system_prompt_key) if system_prompt_key else None
    system_prompt = (system_prompt_template or SOCIAL_MEDIA_EDIT_SYSTEM).format(**ctx)
    system_prompt = system_prompt.rstrip() + f'\n{lang}'

    return _openai_chat(
        messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
    )


def generate_editor_media(prompt, input_media, brand, user, output_group):
    """Generate an media via the Image Editor using Gemini and save to output_group."""
    brand_context = _get_brand_context(brand) if brand else None
    full_prompt = f"{brand_context} {prompt}".strip() if brand_context else prompt

    result = _generate_gemini_media(full_prompt, input_media=input_media)
    if result is None:
        return None

    media_data, mime_type = result
    ext = 'png' if 'png' in mime_type else 'jpg'
    media_obj = Media(
        media_group=output_group,
        source_type=Media.SourceType.GENERATED,
    )
    media_obj.file.save(f'ai_editor.{ext}', ContentFile(media_data), save=True)
    return media_obj

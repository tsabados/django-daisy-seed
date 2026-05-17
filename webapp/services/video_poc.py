import base64
import json
import logging
import mimetypes
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from django.conf import settings

from media_library.models import Media, MediaGroup
from services.ai_services import OpenAIModel, _get_brand_context, _openai_chat


logger = logging.getLogger(__name__)


VIDEO_TYPES = ('teaser', 'demo', 'problem_solution', 'social_proof', 'offer')
ASPECT_RATIOS = ('21:9', '16:9', '4:3', '1:1', '3:4', '9:16')
DEFAULT_ASPECT_RATIO = '9:16'
BRIEF_COUNT = 5
CLIP_ID = 1
MIN_CLIP_DURATION = 4
MAX_CLIP_DURATION = 15
VISUAL_ANALYSIS_IMAGE_LIMIT = 3
MUAPI_MODEL_NAME = 'muapi/sd-2-vip-omni-reference'
MUAPI_REFERENCE_IMAGE_LIMIT = 9
MUAPI_SUBMIT_URL = 'https://api.muapi.ai/api/v1/seedance-2-vip-omni-reference'
MUAPI_RESULT_URL = 'https://api.muapi.ai/api/v1/predictions/{request_id}/result'
MUAPI_PROMPT_MAX_CHARS = 4000
MUAPI_PROMPT_REDUCTION_MAX_ATTEMPTS = 4
MUAPI_PROMPT_REDUCTION_TARGET_CHARS = 3400
SEEDANCE_PROMPT_HEADINGS = (
    'REFERENCE MAP:',
    'PRODUCT LOCK:',
    'FRAMING LOCK:',
    'NARRATIVE BEATS FOR ONE CONTINUOUS CLIP:',
    'MOTION DIRECTION:',
    'CREATIVE SPECIFICITY:',
    'TEXT POLICY:',
    'NEGATIVE CONSTRAINTS:',
)
SEEDANCE_SECTION_BUDGETS = {
    'reference_map': 220,
    'product_lock': 520,
    'framing_lock': 320,
    'narrative_beats': 620,
    'motion_direction': 260,
    'creative_specificity': 220,
    'text_policy': 180,
    'negative_constraints': 340,
}


VIDEO_TYPE_RULES = {
    'teaser': (
        'Build intrigue before revealing the full product value. Use restraint, sensory detail, '
        'and a clear reveal moment instead of a full explanation.'
    ),
    'demo': (
        'Show the product doing something specific. The viewer should understand use, handling, '
        'texture, scale, or styling by watching the action.'
    ),
    'problem_solution': (
        'Start from a concrete audience tension, then show how the product changes that moment. '
        'Avoid vague before/after transformations.'
    ),
    'social_proof': (
        'Use truthful proof mechanics only. Do not invent reviews, ratings, press, customer quotes, '
        'sales numbers, percentages, or testimonials. If no real proof is provided, use a non-fabricated '
        'proof proxy such as peer reaction, repeat-use ritual, customer POV, styling confidence, visible '
        'product desirability, or a real social context.'
    ),
    'offer': (
        'Make the reason to act concrete without becoming generic sale footage. Focus on product value, '
        'occasion, desirability, scarcity of attention, or wardrobe/use-case fit.'
    ),
}

MINIMAL_TEXT_POLICY = (
    'Visible text is optional and must be minimal. Allowed: brand name, product name, CTA, or short '
    'non-factual labels. Forbidden: fake reviews, star ratings, percentages, statistics, press quotes, '
    'customer quotes, testimonial snippets, or any claim not present in the source data.'
)

REFERENCE_HUMAN_POLICY = (
    'If people appear, do not reproduce the identity of any person from the reference images. Treat '
    'reference people as context only, not identity references. Invent new talent, face, hair, makeup, '
    'styling, body, and pose unless the source explicitly requires the same person.'
)

BASE_PRODUCT_LOCK_RULES = (
    'Keep the exact same product identity throughout the entire video.',
    'Preserve product color, material, shape, silhouette, texture, scale, label/logo details, trim, fringe, seams, and distinctive construction.',
    'If reference images show different physical views or sides of the product, preserve each detail only on the view where it belongs.',
    'Never merge mutually exclusive view-specific details into one visible product surface.',
    'Do not morph, recolor, resize, replace, duplicate, redesign, simplify, or swap the product.',
    'The product must be fully visible and recognizable in the opening and final hero moments.',
)

BASE_FRAMING_RULES = (
    'Do not crop or cut off the hero product in the opening or final shot.',
    'Do not let hands, hair, bags, sleeves, camera motion, foreground objects, or frame edges obstruct the product during hero moments.',
    'Avoid fast chaotic camera movement; use controlled motion that keeps the product readable.',
    'End with a clean, unobstructed product-forward composition.',
)

BASE_NEGATIVE_RULES = (
    'No product morphing, warping, melting, flickering, disappearing, changing color, changing texture, or changing label details.',
    'No combining mutually exclusive details from different product views onto the same visible surface.',
    'No fake reviews, ratings, percentages, stats, press quotes, customer quotes, or testimonial text.',
    'No distorted text, malformed logos, extra labels, watermarks, subtitles, UI overlays, or unreadable typography.',
    'No cropped final product, no obstructed hero product, no product leaving frame during the main hero beat.',
    'Do not layer or apply the hero product over another function-equivalent item unless explicitly requested.',
    'Do not keep competing function-equivalent items active or visibly in-use during hero application/reveal beats unless explicitly requested.',
    'Keep item count and state continuity logically consistent across beats; avoid contradictory simultaneous states.',
    'Do not reproduce or closely match the identity of any person visible in the reference images unless explicitly required.',
)


STATE_CONTINUITY_POLICY = (
    'State continuity is mandatory for any product category. Keep the hero product as the single active '
    'item for its function in focus context unless explicit multi-item behavior is requested. Do not '
    'layer/apply the hero product over another function-equivalent item. Do not keep competing equivalent '
    'items active during hero application or reveal beats. Keep item count and state consistent across beats.'
)


class VideoPocError(Exception):
    """Raised when a video PoC step cannot continue safely."""


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def ensure_env(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise VideoPocError(f'{name} is required for this video PoC step.')
    return value


def validate_video_type(video_type):
    if video_type not in VIDEO_TYPES:
        raise VideoPocError(
            f'Unsupported video type "{video_type}". Expected one of: {", ".join(VIDEO_TYPES)}.'
        )
    return video_type


def validate_aspect_ratio(aspect_ratio):
    if aspect_ratio not in ASPECT_RATIOS:
        raise VideoPocError(
            f'Unsupported aspect ratio "{aspect_ratio}". Expected one of: {", ".join(ASPECT_RATIOS)}.'
        )
    return aspect_ratio


def make_run_id(prefix='video'):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    return f'{prefix}-{stamp}-{uuid.uuid4().hex[:8]}'


def get_run_dir(run_id=None):
    run_id = run_id or make_run_id()
    run_dir = Path(settings.MEDIA_ROOT) / 'video_poc' / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_id, run_dir


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def image_reference_summary(image):
    group = image.media_group
    source = image.external_url or (image.file.name if image.file else '')
    return {
        'id': image.id,
        'group_title': group.title,
        'group_description': group.description,
        'source': source,
    }


def build_group_context(group):
    brand = getattr(group.project, 'brand', None)
    brand_context = _get_brand_context(brand) if brand else {
        'brand_name': group.project.name,
        'brand_summary': '',
        'brand_language': 'English',
        'brand_style_guide': '',
    }
    images = list(group.media_items.filter(media_type='image').order_by('id'))
    return {
        'project': {
            'id': group.project_id,
            'name': group.project.name,
        },
        'brand': brand_context,
        'group': {
            'id': group.id,
            'title': group.title,
            'description': group.description,
            'type': group.type,
        },
        'reference_images': [image_reference_summary(image) for image in images],
    }


def _image_source(image):
    return image.external_url or (image.file.name if image.file else '')


def _image_data_url(image):
    if image.file and image.file.name:
        path = Path(image.file.path)
        mime_type = mimetypes.guess_type(path.name)[0] or 'image/jpeg'
        image_data = path.read_bytes()
    elif image.external_url:
        response = requests.get(image.external_url, timeout=30)
        response.raise_for_status()
        mime_type = response.headers.get('content-type', '').split(';')[0] or 'image/jpeg'
        image_data = response.content
    else:
        return ''
    encoded = base64.b64encode(image_data).decode('ascii')
    return f'data:{mime_type};base64,{encoded}'


def build_visual_analysis_prompt(context):
    return VISUAL_ANALYSIS_PROMPT.format(
        group_json=json.dumps(context['group'], indent=2),
        reference_images_json=json.dumps(context['reference_images'], indent=2),
    )


def _parse_json_object_response(raw, error_prefix='AI returned invalid JSON'):
    text = str(raw or '').strip()
    if not text:
        raise VideoPocError(f'{error_prefix}: empty response body.')

    # Common model formatting: fenced JSON blocks.
    if text.startswith('```'):
        lines = text.splitlines()
        if lines and lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith('```'):
            lines = lines[:-1]
        text = '\n'.join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fallback: extract the first JSON object from wrapper prose.
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            snippet = text[start:end + 1]
            try:
                return json.loads(snippet)
            except json.JSONDecodeError as exc:
                raise VideoPocError(f'{error_prefix}: {exc}') from exc
        raise VideoPocError(f'{error_prefix}: no JSON object found in response.')


def _openai_vision_json(prompt, images, model=OpenAIModel.NORMAL, temperature=0.2, max_tokens=1600):
    ensure_env('OPENAI_API_KEY')
    content = [{'type': 'text', 'text': prompt}]
    attached_count = 0
    for image in images[:VISUAL_ANALYSIS_IMAGE_LIMIT]:
        try:
            data_url = _image_data_url(image)
        except Exception:
            continue
        if data_url:
            content.append({
                'type': 'image_url',
                'image_url': {'url': data_url},
            })
            attached_count += 1
    if attached_count == 0:
        raise VideoPocError('Visual analysis requires at least one loadable reference image.')
    raw = ''
    prompts = [
        content,
        [{'type': 'text', 'text': (
            f'{prompt}\n\n'
            'Return only one valid JSON object. Do not use markdown fences or commentary.'
        )}] + content[1:],
        [{'type': 'text', 'text': (
            f'{prompt}\n\n'
            'The previous response was blank. You must return exactly one non-empty JSON object only.'
        )}] + content[1:],
    ]
    for attempt_content in prompts:
        raw = _openai_chat(
            messages=[{'role': 'user', 'content': attempt_content}],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if str(raw or '').strip():
            break
    if not str(raw or '').strip():
        fallback_prompt = (
            f'{prompt}\n\n'
            'The image-enabled response returned blank. Fall back to the provided product/reference group '
            'metadata and reference image metadata. Return exactly one non-empty JSON object only.'
        )
        return _openai_json(
            fallback_prompt,
            model=model,
            temperature=0.2,
            max_tokens=max_tokens,
        )
    return _parse_json_object_response(raw, error_prefix='AI returned invalid visual analysis JSON')


def _normalize_optional_string_list(value):
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_visual_analysis_response(response):
    if not isinstance(response, dict):
        response = {}
    return {
        'product_identity_summary': _stringify_value(response.get('product_identity_summary', '')),
        'visible_attributes': _normalize_optional_string_list(response.get('visible_attributes')),
        'colors': _normalize_optional_string_list(response.get('colors')),
        'materials_textures': _normalize_optional_string_list(response.get('materials_textures')),
        'shape_silhouette': _stringify_value(response.get('shape_silhouette', '')),
        'logos_labels_text': _normalize_optional_string_list(response.get('logos_labels_text')),
        'view_specific_details': _normalize_optional_string_list(response.get('view_specific_details')),
        'mutually_exclusive_details': _normalize_optional_string_list(response.get('mutually_exclusive_details')),
        'view_separation_rules': _normalize_optional_string_list(response.get('view_separation_rules')),
        'scale_fit_usage': _normalize_optional_string_list(response.get('scale_fit_usage')),
        'styling_context': _normalize_optional_string_list(response.get('styling_context')),
        'product_fidelity_rules': _normalize_optional_string_list(response.get('product_fidelity_rules')),
        'avoid_assumptions': _normalize_optional_string_list(response.get('avoid_assumptions')),
    }


def analyze_product_visuals(group, run_dir=None):
    images = list(group.media_items.filter(media_type='image').order_by('id')[:VISUAL_ANALYSIS_IMAGE_LIMIT])
    if not images:
        raise VideoPocError('Visual analysis requires at least one reference image.')
    context = build_group_context(group)
    prompt = build_visual_analysis_prompt(context)
    analysis = normalize_visual_analysis_response(_openai_vision_json(prompt, images))
    if run_dir is not None:
        write_json(Path(run_dir) / 'visual_analysis.json', analysis)
    return analysis


def _stringify_value(value):
    if isinstance(value, list):
        return '; '.join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return str(value).strip()


def _normalize_text_field(value, field_name):
    text = _stringify_value(value)
    if not text:
        raise VideoPocError(f'Missing required field "{field_name}".')
    return text


def _normalize_string_list(
    value,
    field_name,
    min_items=1,
    max_items=None,
):
    if not isinstance(value, list):
        raise VideoPocError(f'{field_name} must be an array of strings.')
    normalized = [_normalize_text_field(item, field_name) for item in value]
    if len(normalized) < min_items:
        raise VideoPocError(f'{field_name} must include at least {min_items} item(s).')
    if max_items is not None and len(normalized) > max_items:
        raise VideoPocError(f'{field_name} must include at most {max_items} item(s).')
    return normalized


def _normalize_seconds(value, field_name):
    if isinstance(value, bool):
        raise VideoPocError(f'{field_name} must be a number of seconds.')
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        text = value.strip().lower()
        if text.endswith('seconds'):
            text = text[:-7].strip()
        elif text.endswith('second'):
            text = text[:-6].strip()
        elif text.endswith('s'):
            text = text[:-1].strip()
        try:
            seconds = float(text)
        except ValueError as exc:
            raise VideoPocError(f'{field_name} must be a number of seconds.') from exc
    else:
        raise VideoPocError(f'{field_name} must be a number of seconds.')
    if seconds < 0:
        raise VideoPocError(f'{field_name} must be zero or greater.')
    if seconds.is_integer():
        return int(seconds)
    return round(seconds, 2)


def _normalize_beat_payload(beats, clip_duration):
    if not isinstance(beats, list):
        raise VideoPocError(
            'clip.beats must be an array of structured beat objects. Regenerate old string-beat scripts.'
        )
    if not (2 <= len(beats) <= 4):
        raise VideoPocError('clip.beats must include 2 to 4 beat objects.')

    normalized = []
    previous_end = None
    text_fields = ('visual_action', 'camera_motion', 'product_focus', 'transition_to_next')
    for index, beat in enumerate(beats, 1):
        if not isinstance(beat, dict):
            raise VideoPocError(
                'clip.beats must be an array of structured beat objects. Regenerate old string-beat scripts.'
            )
        start_time = _normalize_seconds(beat.get('start_time'), f'clip.beats[{index}].start_time')
        end_time = _normalize_seconds(beat.get('end_time'), f'clip.beats[{index}].end_time')
        duration_seconds = _normalize_seconds(
            beat.get('duration_seconds'),
            f'clip.beats[{index}].duration_seconds',
        )
        if end_time <= start_time:
            raise VideoPocError(f'clip.beats[{index}] end_time must be after start_time.')
        if end_time > clip_duration:
            raise VideoPocError(f'clip.beats[{index}] end_time exceeds clip.duration.')
        if previous_end is not None and start_time < previous_end:
            raise VideoPocError('clip.beats must be ordered and non-overlapping.')
        if abs((end_time - start_time) - duration_seconds) > 0.25:
            raise VideoPocError(
                f'clip.beats[{index}] duration_seconds must match end_time - start_time.'
            )

        item = {
            'start_time': start_time,
            'end_time': end_time,
            'duration_seconds': duration_seconds,
        }
        for field in text_fields:
            item[field] = _normalize_text_field(beat.get(field, ''), f'clip.beats[{index}].{field}')
        normalized.append(item)
        previous_end = end_time
    return normalized


def video_type_rules(video_type):
    return VIDEO_TYPE_RULES[validate_video_type(video_type)]


def build_briefs_prompt(video_type, aspect_ratio, context):
    validate_video_type(video_type)
    validate_aspect_ratio(aspect_ratio)
    return BRIEFS_PROMPT.format(
        video_type=video_type,
        video_type_rules=video_type_rules(video_type),
        aspect_ratio=aspect_ratio,
        minimal_text_policy=MINIMAL_TEXT_POLICY,
        reference_human_policy=REFERENCE_HUMAN_POLICY,
        brand_json=json.dumps(context['brand'], indent=2),
        group_json=json.dumps(context['group'], indent=2),
        reference_images_json=json.dumps(context['reference_images'], indent=2),
        visual_analysis_json=json.dumps(context.get('visual_analysis', {}), indent=2),
    )


def build_script_prompt(briefs_payload, brief):
    video_type = validate_video_type(briefs_payload['video_type'])
    aspect_ratio = validate_aspect_ratio(briefs_payload.get('aspect_ratio', DEFAULT_ASPECT_RATIO))
    return SCRIPT_PROMPT.format(
        video_type=video_type,
        video_type_rules=video_type_rules(video_type),
        aspect_ratio=aspect_ratio,
        state_continuity_policy=STATE_CONTINUITY_POLICY,
        minimal_text_policy=MINIMAL_TEXT_POLICY,
        reference_human_policy=REFERENCE_HUMAN_POLICY,
        metadata_json=json.dumps(briefs_payload.get('metadata', {}), indent=2),
        brief_json=json.dumps(brief, indent=2),
    )


def product_identity(metadata):
    group = metadata.get('group') or {}
    title = group.get('title') or 'the product'
    description = group.get('description') or ''
    return f'{title}: {description}'.strip(': ')


def base_product_rules(metadata):
    product = product_identity(metadata)
    rules = [
        f'Hero product identity: {product}.',
        *BASE_PRODUCT_LOCK_RULES,
    ]
    visual_analysis = metadata.get('visual_analysis') or {}
    summary = visual_analysis.get('product_identity_summary')
    if summary:
        rules.append(f'Visual product facts from reference images: {summary}')
    view_details = visual_analysis.get('view_specific_details') or []
    mutually_exclusive = visual_analysis.get('mutually_exclusive_details') or []
    if view_details:
        rules.append(f'View-specific product details must stay on their matching physical view: {"; ".join(view_details)}')
    if mutually_exclusive:
        rules.append(f'Do not combine these mutually exclusive details on one visible surface: {"; ".join(mutually_exclusive)}')
    for rule in visual_analysis.get('view_separation_rules') or []:
        rules.append(rule)
    for rule in visual_analysis.get('product_fidelity_rules') or []:
        rules.append(rule)
    for assumption in visual_analysis.get('avoid_assumptions') or []:
        rules.append(f'Do not assume or invent: {assumption}')
    return rules


def base_framing_rules():
    return list(BASE_FRAMING_RULES)


def base_negative_rules():
    return list(BASE_NEGATIVE_RULES)


VISUAL_ANALYSIS_PROMPT = """
You are a product visual analyst for an AI video generation pipeline.

Analyze the attached product/reference images and return structured visual facts only.
Do not invent product details that are not visible.
If an image is ambiguous, say what is ambiguous in avoid_assumptions.

Rules:
- Return only valid JSON.
- Do not use markdown.
- The root object must include:
  product_identity_summary, visible_attributes, colors, materials_textures,
  shape_silhouette, logos_labels_text, view_specific_details,
  mutually_exclusive_details, view_separation_rules, scale_fit_usage,
  styling_context, product_fidelity_rules, avoid_assumptions.
- visible_attributes, colors, materials_textures, logos_labels_text,
  view_specific_details, mutually_exclusive_details, view_separation_rules,
  scale_fit_usage, styling_context, product_fidelity_rules, and avoid_assumptions
  must be arrays of strings.
- product_identity_summary and shape_silhouette must be strings.
- Focus on facts useful for keeping the product consistent in generated keyframes and video.
- Include any visible labels/logos/text only if they are readable.
- If reference images show different physical sides, angles, surfaces, package faces,
  components, variants, or states of the same product, describe those as view_specific_details.
- Use mutually_exclusive_details for details that cannot physically appear together on
  the same visible surface, face, side, component, or state.
- Create view_separation_rules that explicitly forbid merging details across incompatible
  views. Examples: front/back garment artwork, front/back packaging labels, shoe inner/outer
  side details, bag exterior/interior details, bottle/box details.
- Avoid brand claims, quality claims, customer claims, ratings, stats, reviews, or press language.

Product/reference group metadata:
{group_json}

Reference image metadata:
{reference_images_json}
""".strip()


BRIEFS_PROMPT = """
You are a senior social video strategist.

Create exactly 5 distinct short-form video briefs for the requested video type.
The briefs are for a backend video generation experiment, so be concrete, visual, and non-generic.

Rules:
- Return only valid JSON.
- Do not use markdown.
- The root object must be {{"briefs": [...]}}.
- Each brief must have:
  id, title, hook, target_viewer, core_message, story_angle, proof_mechanism,
  viewer_tension, product_role, visual_hook, visual_direction, cta,
  why_it_fits_type, avoid_cliches.
- id values must be the integers 1 through 5.
- Each concept should be feasible as one continuous Seedance clip with 2-4 internal story beats.
- Full narrative is allowed, including people and faces.
- Product/reference images are creative references, not final first frames.
- Product visual analysis is the source of truth for visible product details.
- {reference_human_policy}
- Avoid generic fashion footage where a person merely wears or holds the product.
- Every brief must create a distinct situation, tension, proof mechanism, or product role.
- {minimal_text_policy}

Video type: {video_type}
Type-specific rules: {video_type_rules}
Aspect ratio: {aspect_ratio}

Brand:
{brand_json}

Product/reference group:
{group_json}

Reference images:
{reference_images_json}

Product visual analysis:
{visual_analysis_json}
""".strip()


SCRIPT_PROMPT = """
You are a cinematic AI-video director designing a cohesive single-clip video.

Convert the selected brief into a continuity-locked script for Seedance image-to-video.

Rules:
- Return only valid JSON.
- Do not use markdown.
- The root object must include:
  selected_brief_id, creative_treatment, continuity_rules, clip.
- creative_treatment must include:
  story_arc, visual_style, color_grade, lighting, recurring_elements,
  product_continuity_rules, character_notes, transition_intent.
- continuity_rules must be an array of concrete rules the single clip should preserve.
- clip must be one object, not an array.
- clip must include:
  id, duration, narrative_purpose, scene, beats, camera_motion, product_action,
  product_fidelity_rules, framing_rules, motion_rules, negative_rules,
  distinctiveness_notes, keyframe_prompt, seedance_prompt.
- clip.id must be 1.
- clip.duration must be an integer from 4 to 15 seconds.
- clip.beats must be 2 to 4 ordered beat objects that fit inside clip.duration.
- Each beat object must include:
  start_time, end_time, duration_seconds, visual_action, camera_motion,
  product_focus, transition_to_next.
- Beat timing must use seconds from the start of the clip. Beats must be ordered,
  non-overlapping, and must not exceed clip.duration.
- Transitions must be soft internal moves only: camera moves, focus pulls, reveals,
  object motion, lighting shifts, or pacing changes. Do not use hard cuts,
  scene cuts, edits, stitching, montage language, or post-production transitions.
- product_fidelity_rules, framing_rules, motion_rules, and negative_rules must be arrays of concrete rules.
- distinctiveness_notes must explain what makes this video non-generic for this product and selected video type.
- Full narrative is allowed, including people and faces, but keep it coherent inside one continuous clip.
- Make keyframe prompts visually specific enough for an image model.
- Make Seedance prompts motion-specific, concise, and compatible with one image-to-video generation.
- The Seedance prompt may describe multiple internal shots/beats, but it must not require stitching.
- Preserve product identity more strongly than cinematic variety.
- Treat metadata.visual_analysis as the source of truth for visible product details.
- Choose one coherent physical product view per shot and do not merge mutually exclusive view-specific details onto the same visible surface.
- {reference_human_policy}
- Do not fabricate reviews, ratings, press, stats, customer claims, or testimonial text.
- {state_continuity_policy}
- {minimal_text_policy}

Video type: {video_type}
Type-specific rules: {video_type_rules}
Aspect ratio: {aspect_ratio}

Briefs metadata:
{metadata_json}

Selected brief:
{brief_json}
""".strip()


KEYFRAME_PROMPT = """
Create a cinematic first frame for a short-form brand video.

Use the product/reference images only to preserve the product's recognizable appearance.
This image will be the single start frame for one continuous Seedance clip.

Aspect ratio: {aspect_ratio}

Selected brief:
{brief_json}

Product visual analysis:
{visual_analysis_json}

Global creative treatment:
{treatment_json}

Continuity rules:
{continuity_json}

Single clip:
{clip_json}

Output requirements:
- Produce a polished, production-ready still frame, not a storyboard sketch.
- Product fidelity is more important than cinematic variety.
- The hero product must be fully visible, not cropped, not obstructed, and not visually redesigned.
- Preserve product color, material, shape, silhouette, texture, scale, label/logo details, trim, fringe, seams, and distinctive construction from the reference images.
- Preserve only the product from the references. Do not copy any reference person's identity, face, hair, makeup, or styling.
- If a person appears, invent new talent rather than reusing a reference-image model.
- Choose one coherent physical product view for this keyframe and include only the markings/details that belong to that visible view.
- Never merge mutually exclusive details from different product views, sides, surfaces, components, variants, or states into one visible product surface.
- No fake reviews, ratings, stats, press quotes, testimonial text, UI overlays, subtitles, watermarks, or distorted typography.
- Establish a coherent visual world that can support all listed beats.
- Make the frame clear enough that an image-to-video model can animate it.
""".strip()


SEEDANCE_REDUCTION_PROMPT = """
You are reducing a Seedance video-generation prompt to fit a strict character limit.

Requirements:
- Return plain text only.
- Do not use markdown fences.
- Preserve these headings exactly and in this order:
    REFERENCE MAP:
    PRODUCT LOCK:
    FRAMING LOCK:
    NARRATIVE BEATS FOR ONE CONTINUOUS CLIP:
    MOTION DIRECTION:
    CREATIVE SPECIFICITY:
    TEXT POLICY:
    NEGATIVE CONSTRAINTS:
- Keep every section present.
- Compress wording only. Do not remove product lock, framing, motion, text policy, or negative constraints.
- Prefer shortening examples, repetition, and decorative phrasing before removing essential constraints.
- Prioritize compression in this order:
    1) PRODUCT LOCK detail verbosity (keep core lock constraints)
    2) NARRATIVE BEATS wording (keep timing, action, camera, focus, transition but make it telegraphic)
    3) CREATIVE SPECIFICITY prose
    4) FRAMING/MOTION extra qualifiers
- Current attempt: {attempt}/{max_attempts}
- Characters over hard limit: {chars_over}
- Minimum shrink required this attempt: {min_shrink}
- Target length for this pass: <= {target_chars}
- Hard limit for final output: <= {max_chars}
- Output must be materially shorter than input on every attempt.

Current prompt length: {current_length}
Target max length: {max_chars}

Prompt to reduce:
{prompt}
""".strip()


SEEDANCE_REDUCTION_STRUCTURED_PROMPT = """
You are rewriting a Seedance video-generation prompt into concise sections with hard character budgets.

Return only valid JSON with exactly these keys:
- reference_map
- product_lock
- framing_lock
- narrative_beats
- motion_direction
- creative_specificity
- text_policy
- negative_constraints

Rules:
- Preserve core meaning and constraints.
- Keep all sections present and non-empty.
- Keep timing/action/camera/focus/transition intent in narrative_beats, but concise.
- Do not invent new claims or remove safety constraints.
- Do not include markdown fences.
- Each field must be <= its budget.

Budgets JSON:
{budgets_json}

Hard final full-prompt max: {max_chars}
Current prompt length: {current_length}

Prompt to rewrite:
{prompt}
""".strip()


SEEDANCE_SECTION_COMPRESSION_PROMPT = """
Compress this Seedance section text to fit a strict character budget.

Rules:
- Return plain text only.
- Keep core constraints and intent.
- Do not add new claims.
- Keep it <= {budget} characters.

Section: {section_name}
Current length: {current_length}
Target max: {budget}

Text:
{text}
""".strip()


def _openai_json(prompt, model=OpenAIModel.NORMAL, temperature=0.7, max_tokens=2000):
    ensure_env('OPENAI_API_KEY')
    raw = _openai_chat(
        messages=[{'role': 'user', 'content': prompt}],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if not str(raw or '').strip():
        retry_prompt = (
            f'{prompt}\n\n'
            'Return only one valid JSON object. Do not use markdown fences or commentary.'
        )
        raw = _openai_chat(
            messages=[{'role': 'user', 'content': retry_prompt}],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    return _parse_json_object_response(raw)


def normalize_briefs_response(response, video_type=None):
    if video_type is not None:
        validate_video_type(video_type)
    briefs = response.get('briefs') if isinstance(response, dict) else None
    if not isinstance(briefs, list) or len(briefs) != BRIEF_COUNT:
        raise VideoPocError(f'Expected exactly {BRIEF_COUNT} briefs.')

    normalized = []
    seen_ids = set()
    required = (
        'id',
        'title',
        'hook',
        'target_viewer',
        'core_message',
        'story_angle',
        'proof_mechanism',
        'viewer_tension',
        'product_role',
        'visual_hook',
        'visual_direction',
        'cta',
        'why_it_fits_type',
        'avoid_cliches',
    )
    for expected_id, brief in enumerate(briefs, 1):
        if not isinstance(brief, dict):
            raise VideoPocError('Each brief must be an object.')
        brief_id = int(brief.get('id', expected_id))
        if brief_id in seen_ids:
            raise VideoPocError(f'Duplicate brief id: {brief_id}.')
        seen_ids.add(brief_id)

        item = {'id': brief_id}
        for key in required:
            if key == 'id':
                continue
            item[key] = _normalize_text_field(brief.get(key, ''), f'Brief {brief_id}.{key}')
        normalized.append(item)

    if sorted(seen_ids) != list(range(1, BRIEF_COUNT + 1)):
        raise VideoPocError(f'Brief ids must be 1 through {BRIEF_COUNT}.')
    return normalized


def generate_briefs(
    group_id,
    video_type,
    aspect_ratio=DEFAULT_ASPECT_RATIO,
    run_id=None,
    visual_analysis=None,
):
    validate_video_type(video_type)
    validate_aspect_ratio(aspect_ratio)
    try:
        group = (
            MediaGroup.objects.select_related('project')
            .prefetch_related('media_items')
            .get(pk=group_id)
        )
    except MediaGroup.DoesNotExist as exc:
        raise VideoPocError(f'MediaGroup {group_id} was not found.') from exc
    run_id, run_dir = get_run_dir(run_id)
    context = build_group_context(group)
    if visual_analysis is None:
        visual_analysis = analyze_product_visuals(group, run_dir=run_dir)
    if not visual_analysis:
        raise VideoPocError('Visual analysis is required before generating video briefs.')
    context['visual_analysis'] = visual_analysis
    prompt = build_briefs_prompt(video_type, aspect_ratio, context)
    briefs = normalize_briefs_response(
        _openai_json(prompt, temperature=0.8, max_tokens=3600),
        video_type=video_type,
    )
    payload = {
        'run_id': run_id,
        'created_at': utc_now_iso(),
        'video_type': video_type,
        'aspect_ratio': aspect_ratio,
        'metadata': context,
        'briefs': briefs,
    }
    output_path = run_dir / 'briefs.json'
    write_json(output_path, payload)
    write_json(run_dir / 'manifest.json', {
        'run_id': run_id,
        'status': 'briefs_generated',
        'briefs_path': str(output_path),
        'updated_at': utc_now_iso(),
    })
    return output_path


def select_brief(briefs_payload, brief_id):
    for brief in briefs_payload.get('briefs', []):
        if int(brief.get('id')) == int(brief_id):
            return brief
    raise VideoPocError(f'Brief id {brief_id} was not found.')


def validate_script_payload(script_payload):
    if not isinstance(script_payload, dict):
        raise VideoPocError('Script must be a JSON object.')

    treatment = script_payload.get('creative_treatment')
    if not isinstance(treatment, dict):
        raise VideoPocError('Script must include creative_treatment.')
    for key in (
        'story_arc',
        'visual_style',
        'color_grade',
        'lighting',
        'recurring_elements',
        'product_continuity_rules',
        'character_notes',
        'transition_intent',
    ):
        if key not in treatment:
            raise VideoPocError(f'creative_treatment is missing "{key}".')

    continuity_rules = script_payload.get('continuity_rules')
    if not isinstance(continuity_rules, list) or not continuity_rules:
        raise VideoPocError('Script must include at least one continuity rule.')

    clip = script_payload.get('clip')
    if not isinstance(clip, dict):
        raise VideoPocError('Script must include one "clip" object. Regenerate old multi-clip scripts.')

    clip_id = int(clip.get('id', CLIP_ID))
    if clip_id != CLIP_ID:
        raise VideoPocError('Single-clip script must use clip.id = 1.')

    duration = int(clip.get('duration', 0))
    if not (MIN_CLIP_DURATION <= duration <= MAX_CLIP_DURATION):
        raise VideoPocError(
            f'Clip duration must be {MIN_CLIP_DURATION}-{MAX_CLIP_DURATION} seconds.'
        )

    normalized_beats = _normalize_beat_payload(clip.get('beats'), duration)

    normalized = {
        'id': clip_id,
        'duration': duration,
        'beats': normalized_beats,
        'product_fidelity_rules': _normalize_string_list(
            clip.get('product_fidelity_rules'),
            'clip.product_fidelity_rules',
        ),
        'framing_rules': _normalize_string_list(
            clip.get('framing_rules'),
            'clip.framing_rules',
        ),
        'motion_rules': _normalize_string_list(
            clip.get('motion_rules'),
            'clip.motion_rules',
        ),
        'negative_rules': _normalize_string_list(
            clip.get('negative_rules'),
            'clip.negative_rules',
        ),
        'distinctiveness_notes': _normalize_text_field(
            clip.get('distinctiveness_notes', ''),
            'clip.distinctiveness_notes',
        ),
    }
    for key in (
        'narrative_purpose',
        'scene',
        'camera_motion',
        'product_action',
        'keyframe_prompt',
        'seedance_prompt',
    ):
        normalized[key] = _normalize_text_field(clip.get(key, ''), f'clip.{key}')

    script_payload['clip'] = normalized
    return script_payload


def generate_script(briefs_path, brief_id):
    briefs_path = Path(briefs_path)
    briefs_payload = read_json(briefs_path)
    brief = select_brief(briefs_payload, brief_id)
    aspect_ratio = validate_aspect_ratio(briefs_payload.get('aspect_ratio', DEFAULT_ASPECT_RATIO))
    prompt = build_script_prompt(briefs_payload, brief)
    script = validate_script_payload(_openai_json(prompt, temperature=0.65, max_tokens=3600))
    script_payload = {
        'run_id': briefs_payload['run_id'],
        'created_at': utc_now_iso(),
        'source_briefs_path': str(briefs_path),
        'video_type': briefs_payload['video_type'],
        'aspect_ratio': aspect_ratio,
        'metadata': briefs_payload.get('metadata', {}),
        'selected_brief': brief,
        **script,
    }
    output_path = briefs_path.parent / 'script.json'
    write_json(output_path, script_payload)
    update_manifest(briefs_path.parent, {
        'status': 'script_generated',
        'script_path': str(output_path),
    })
    return output_path


def update_manifest(run_dir, updates):
    manifest_path = Path(run_dir) / 'manifest.json'
    manifest = {}
    if manifest_path.exists():
        manifest = read_json(manifest_path)
    manifest.update(updates)
    manifest['updated_at'] = utc_now_iso()
    write_json(manifest_path, manifest)
    return manifest_path


def _load_pil_image_from_media_image(image):
    from PIL import Image as PILImage

    if image.file and image.file.name:
        return PILImage.open(image.file.path)
    if image.external_url:
        response = requests.get(image.external_url, timeout=30)
        response.raise_for_status()
        return PILImage.open(BytesIO(response.content))
    return None


def _load_pil_image_from_path(path):
    from PIL import Image as PILImage

    return PILImage.open(path)


def _generate_gemini_image(prompt, reference_images=None, aspect_ratio=DEFAULT_ASPECT_RATIO):
    from google import genai
    from google.genai import types

    ensure_env('GOOGLE_API_KEY')
    client = genai.Client(api_key=os.environ.get('GOOGLE_API_KEY', ''))
    pil_images = []
    try:
        for ref in reference_images or []:
            try:
                if isinstance(ref, Image):
                    pil_img = _load_pil_image_from_media_image(ref)
                else:
                    pil_img = _load_pil_image_from_path(ref)
                if pil_img is not None:
                    pil_images.append(pil_img)
            except Exception:
                continue

        response = client.models.generate_content(
            model='gemini-3.1-flash-image-preview',
            contents=[prompt] + pil_images,
            config=types.GenerateContentConfig(
                response_modalities=['IMAGE'],
                image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
            ),
        )
    finally:
        for pil_img in pil_images:
            pil_img.close()

    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            return part.inline_data.data, part.inline_data.mime_type
    raise VideoPocError('Gemini did not return an image for the keyframe.')


def build_keyframe_prompt(script_payload, clip):
    continuity = {
        'rules': script_payload.get('continuity_rules', []),
    }
    return KEYFRAME_PROMPT.format(
        aspect_ratio=script_payload['aspect_ratio'],
        brief_json=json.dumps(script_payload.get('selected_brief', {}), indent=2),
        visual_analysis_json=json.dumps(
            script_payload.get('metadata', {}).get('visual_analysis', {}),
            indent=2,
        ),
        treatment_json=json.dumps(script_payload.get('creative_treatment', {}), indent=2),
        continuity_json=json.dumps(continuity, indent=2),
        clip_json=json.dumps(clip, indent=2),
    )


def _extension_from_mime(mime_type):
    if 'jpeg' in mime_type or 'jpg' in mime_type:
        return 'jpg'
    if 'webp' in mime_type:
        return 'webp'
    return 'png'


def _public_base_url():
    candidates = [
        os.environ.get('SITE_URL', '').strip(),
        os.environ.get('NGROK_URL', '').strip(),
        getattr(settings, 'SITE_URL', '').strip(),
    ]
    for candidate in candidates:
        if candidate:
            return candidate.rstrip('/')
    raise VideoPocError('SITE_URL or NGROK_URL is required to build public keyframe URLs.')


def _looks_local_url(url):
    host = urlparse(url).hostname or ''
    return host in {'localhost', '127.0.0.1', '0.0.0.0'} or host.endswith('.local')


def public_media_url(path):
    media_root = Path(settings.MEDIA_ROOT).resolve()
    target = Path(path).resolve()
    try:
        relative = target.relative_to(media_root)
    except ValueError as exc:
        raise VideoPocError(f'{target} is not inside MEDIA_ROOT.') from exc

    base_url = _public_base_url()
    if _looks_local_url(base_url):
        raise VideoPocError('SITE_URL/NGROK_URL must be public; localhost cannot be fetched by Muapi.')
    quoted = '/'.join(quote(part) for part in relative.parts)
    return f'{base_url}{settings.MEDIA_URL}{quoted}'


def _bullets(items):
    return '\n'.join(f'- {item}' for item in items)


def _format_seconds(value):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f'{value}s'


def _timed_beat_plan(beats):
    lines = []
    for beat in beats:
        lines.append(
            (
                f"{_format_seconds(beat['start_time'])}-{_format_seconds(beat['end_time'])} "
                f"({beat['duration_seconds']}s): {beat['visual_action']} "
                f"Camera: {beat['camera_motion']} "
                f"Product focus: {beat['product_focus']} "
                f"Transition: {beat['transition_to_next']}"
            )
        )
    return '\n'.join(f'{index}. {line}' for index, line in enumerate(lines, 1))


def _enforce_prompt_limit(prompt, max_chars=MUAPI_PROMPT_MAX_CHARS):
    if len(prompt) <= max_chars:
        return prompt
    raise VideoPocError(
        f'Muapi prompt length is {len(prompt)} characters (max {max_chars}). '
        'Final prompt reduction could not compress it enough.'
    )


def _shorten(text, limit=500):
    text = ' '.join(str(text or '').split())
    if len(text) <= limit:
        return text
    return f'{text[: limit - 3].rstrip()}...'


def _strip_code_fences(text):
    text = str(text or '').strip()
    if not text.startswith('```'):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith('```'):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith('```'):
        lines = lines[:-1]
    return '\n'.join(lines).strip()


def _validate_seedance_prompt_sections(prompt):
    missing = [heading for heading in SEEDANCE_PROMPT_HEADINGS if heading not in prompt]
    if missing:
        raise VideoPocError(
            'Reduced Seedance prompt is missing required sections: '
            f'{", ".join(missing)}'
        )


def _build_seedance_prompt_from_sections(sections):
    return f"""
REFERENCE MAP:
{sections['reference_map']}

PRODUCT LOCK:
{sections['product_lock']}

FRAMING LOCK:
{sections['framing_lock']}

NARRATIVE BEATS FOR ONE CONTINUOUS CLIP:
{sections['narrative_beats']}

MOTION DIRECTION:
{sections['motion_direction']}

CREATIVE SPECIFICITY:
{sections['creative_specificity']}

TEXT POLICY:
{sections['text_policy']}

NEGATIVE CONSTRAINTS:
{sections['negative_constraints']}
""".strip()


def _reduce_seedance_prompt_structured(prompt, max_chars=MUAPI_PROMPT_MAX_CHARS):
    logger.info(f'Building structured reduction from {len(prompt)} chars')
    structured_prompt = SEEDANCE_REDUCTION_STRUCTURED_PROMPT.format(
        budgets_json=json.dumps(SEEDANCE_SECTION_BUDGETS, indent=2),
        max_chars=max_chars,
        current_length=len(prompt),
        prompt=prompt,
    )
    sections = _openai_json(
        structured_prompt,
        model=OpenAIModel.FULL,
        temperature=0.2,
        max_tokens=2600,
    )
    if not isinstance(sections, dict):
        raise VideoPocError('Structured Seedance reduction did not return a JSON object.')

    def _compress_section(section_name, text, budget):
        current = text
        for attempt in range(1, 4):
            if len(current) <= budget:
                logger.debug(f'  {section_name}: {len(current)}/{budget} chars ✓')
                return current
            logger.debug(f'  {section_name}: {len(current)}/{budget} chars, compressing (attempt {attempt})')
            compression_prompt = SEEDANCE_SECTION_COMPRESSION_PROMPT.format(
                section_name=section_name,
                current_length=len(current),
                budget=budget,
                text=current,
            )
            compressed = _openai_chat(
                messages=[{'role': 'user', 'content': compression_prompt}],
                model=OpenAIModel.FULL,
                max_tokens=900,
            )
            compressed = _strip_code_fences(compressed)
            if not compressed:
                break
            current = compressed.strip()
        return current

    normalized = {}
    for key, budget in SEEDANCE_SECTION_BUDGETS.items():
        value = sections.get(key, '')
        text = str(value or '').strip()
        if not text:
            raise VideoPocError(f'Structured Seedance reduction missing section: {key}.')
        text = _compress_section(key, text, budget)
        # Budgets are strong guidance; total prompt limit remains the hard requirement.
        normalized[key] = text

    rebuilt = _build_seedance_prompt_from_sections(normalized)
    _validate_seedance_prompt_sections(rebuilt)
    return rebuilt


def _reduce_seedance_prompt(prompt, max_chars=MUAPI_PROMPT_MAX_CHARS):
    current_prompt = prompt
    logger.info(f'Starting prompt reduction: initial length {len(prompt)} chars (limit {max_chars})')
    for attempt in range(1, MUAPI_PROMPT_REDUCTION_MAX_ATTEMPTS + 1):
        if len(current_prompt) <= max_chars:
            logger.info(f'Prompt fits within limit after attempt {attempt - 1}: {len(current_prompt)} chars')
            return current_prompt

        current_length = len(current_prompt)
        chars_over = max(0, current_length - max_chars)
        min_shrink = max(120, chars_over // 2)
        target_chars = min(max_chars, MUAPI_PROMPT_REDUCTION_TARGET_CHARS)

        logger.info(f'Reduction attempt {attempt}/{MUAPI_PROMPT_REDUCTION_MAX_ATTEMPTS}: {current_length} chars (need to remove {chars_over})')
        reduction_prompt = SEEDANCE_REDUCTION_PROMPT.format(
            attempt=attempt,
            max_attempts=MUAPI_PROMPT_REDUCTION_MAX_ATTEMPTS,
            current_length=current_length,
            chars_over=chars_over,
            min_shrink=min_shrink,
            target_chars=target_chars,
            max_chars=max_chars,
            prompt=current_prompt,
        )

        reduced = _openai_chat(
            messages=[{'role': 'user', 'content': reduction_prompt}],
            model=OpenAIModel.FULL,
            max_tokens=2600,
        )
        reduced = _strip_code_fences(reduced)
        if not reduced:
            raise VideoPocError(
                f'Seedance prompt reduction returned an empty response on attempt {attempt}.'
            )
        _validate_seedance_prompt_sections(reduced)
        logger.info(f'  → Attempt {attempt} result: {len(reduced)} chars (delta: {len(reduced) - current_length:+d})')

        if len(reduced) >= current_length:
            # Keep latest candidate and let the authoritative structured fallback run after retries.
            current_prompt = reduced
            continue

        current_prompt = reduced

    # Final authoritative fallback: rewrite into section budgets.
    logger.info(f'Switching to structured reduction: current {len(current_prompt)} chars')
    structured = _reduce_seedance_prompt_structured(current_prompt, max_chars=max_chars)

    if len(structured) > max_chars:
        logger.info(f'Structured result still over limit ({len(structured)} chars), attempting final reductions...')
        for i in range(1, 5):
            if len(structured) <= max_chars:
                break
            structured_reduction_prompt = SEEDANCE_REDUCTION_PROMPT.format(
                attempt=MUAPI_PROMPT_REDUCTION_MAX_ATTEMPTS + i,
                max_attempts=MUAPI_PROMPT_REDUCTION_MAX_ATTEMPTS + 4,
                current_length=len(structured),
                chars_over=max(0, len(structured) - max_chars),
                min_shrink=max(150, max(0, len(structured) - max_chars)),
                target_chars=min(max_chars - 100, MUAPI_PROMPT_REDUCTION_TARGET_CHARS),
                max_chars=max_chars,
                prompt=structured,
            )
            logger.info(f'Final reduction {i}/4: {len(structured)} chars')
            final_candidate = _openai_chat(
                messages=[{'role': 'user', 'content': structured_reduction_prompt}],
                model=OpenAIModel.FULL,
                max_tokens=1800,
            )
            final_candidate = _strip_code_fences(final_candidate)
            if final_candidate:
                _validate_seedance_prompt_sections(final_candidate)
                logger.info(f'  → Final {i} result: {len(final_candidate)} chars')
                structured = final_candidate

    logger.info(f'Prompt reduction complete: final length {len(structured)} chars')
    return _enforce_prompt_limit(structured, max_chars=max_chars)


def _seedance_reference_rules(product_reference_count=0, create_keyframe=False):
    if product_reference_count < 1:
        raise VideoPocError('Muapi render requires at least one product reference image.')

    rules = []
    if create_keyframe:
        rules.append('Use @image1 as the generated keyframe, opening composition, lighting/world anchor, and continuity anchor.')
        first_product_index = 2
    else:
        rules.append('Use all @image references as product identity references.')
        first_product_index = 1

    if product_reference_count:
        end_label = f'@image{first_product_index + product_reference_count - 1}'
        product_range = (
            f'@image{first_product_index}'
            if product_reference_count == 1
            else f'@image{first_product_index} through {end_label}'
        )
        rules.extend([
            f'Use {product_range} as product identity references only.',
            f'Match the product color, material, shape, silhouette, scale, texture, label/logo details, trim, seams, and distinctive construction from {product_range}.',
            f'Do not cut away into {product_range} as catalog photos; use them to preserve product fidelity inside the generated scene.',
        ])
    return rules


def build_seedance_prompt(script_payload, clip, product_reference_count=0, create_keyframe=False):
    metadata = script_payload.get('metadata', {})
    visual_analysis = metadata.get('visual_analysis') or {}
    group = metadata.get('group') or {}
    product_name = group.get('title') or 'the hero product'

    view_specific_details = (visual_analysis.get('view_specific_details') or [])[:2]
    mutually_exclusive_details = (visual_analysis.get('mutually_exclusive_details') or [])[:2]
    view_separation_rules = (visual_analysis.get('view_separation_rules') or [])[:2]
    clip_fidelity_rules = (clip.get('product_fidelity_rules') or [])[:3]

    product_facts = [
        visual_analysis.get('product_identity_summary') or product_identity(metadata),
        *view_specific_details,
        *mutually_exclusive_details,
        *view_separation_rules,
        *clip_fidelity_rules,
    ]
    framing_rules = [
        BASE_FRAMING_RULES[0],
        BASE_FRAMING_RULES[-1],
        *clip['framing_rules'][:4],
    ]
    motion_rules = [
        clip['camera_motion'],
        clip['product_action'],
        *clip['motion_rules'][:3],
    ]
    negative_rules = [
        'Do not morph, warp, melt, flicker, recolor, resize, replace, or change product details.',
        BASE_NEGATIVE_RULES[1],
        'No fake reviews, ratings, percentages, stats, press quotes, customer quotes, or testimonial text.',
        BASE_NEGATIVE_RULES[5],
        BASE_NEGATIVE_RULES[6],
        BASE_NEGATIVE_RULES[7],
        BASE_NEGATIVE_RULES[8],
        *clip['negative_rules'][:3],
    ]
    creative_specificity = [
        _shorten(clip['distinctiveness_notes'], limit=220),
        _shorten(clip['seedance_prompt'], limit=700),
    ]
    reference_rules = _seedance_reference_rules(
        product_reference_count,
        create_keyframe=create_keyframe,
    )
    prompt = f"""
REFERENCE MAP:
{_bullets(reference_rules)}

PRODUCT LOCK:
- Hero product: {product_name}
- Keep the exact same product identity throughout the video.
- Preserve only these product facts and do not invent extra markings: {'; '.join(product_facts)}
- Keep one coherent physical product view per shot; do not merge incompatible sides, surfaces, components, variants, or states.

FRAMING LOCK:
{_bullets(framing_rules)}

NARRATIVE BEATS FOR ONE CONTINUOUS CLIP:
{_timed_beat_plan(clip['beats'])}

MOTION DIRECTION:
{_bullets(motion_rules)}

CREATIVE SPECIFICITY:
{_bullets(creative_specificity)}

TEXT POLICY:
- {MINIMAL_TEXT_POLICY}

NEGATIVE CONSTRAINTS:
{_bullets(negative_rules)}
""".strip()
    _validate_seedance_prompt_sections(prompt)
    if len(prompt) <= MUAPI_PROMPT_MAX_CHARS:
        return prompt
    reduced_prompt = _reduce_seedance_prompt(prompt, max_chars=MUAPI_PROMPT_MAX_CHARS)
    return _enforce_prompt_limit(reduced_prompt)


def _image_public_url(image):
    if image.external_url:
        return image.external_url
    if image.file and image.file.name:
        return public_media_url(image.file.path)
    return ''


def product_reference_urls(product_images):
    urls = []
    for image in product_images[: MUAPI_REFERENCE_IMAGE_LIMIT - 1]:
        url = _image_public_url(image)
        if url:
            urls.append(url)
    return urls


def build_muapi_payload(
    script_payload,
    clip,
    product_reference_urls,
    keyframe_url=None,
    create_keyframe=False,
):
    product_reference_urls = list(product_reference_urls or [])
    max_product_references = MUAPI_REFERENCE_IMAGE_LIMIT - 1 if create_keyframe else MUAPI_REFERENCE_IMAGE_LIMIT
    product_reference_urls = product_reference_urls[:max_product_references]
    if not product_reference_urls:
        raise VideoPocError('Muapi payload requires at least one product reference image.')
    images_list = [*product_reference_urls]
    if create_keyframe:
        if not keyframe_url:
            raise VideoPocError('create_keyframe=True requires a keyframe URL.')
        images_list = [keyframe_url, *product_reference_urls]
    return {
        'prompt': build_seedance_prompt(
            script_payload,
            clip,
            product_reference_count=len(product_reference_urls),
            create_keyframe=create_keyframe,
        ),
        'images_list': images_list,
        'video_files': [],
        'audio_files': [],
        'aspect_ratio': script_payload['aspect_ratio'],
        'duration': clip['duration'],
    }


def _keyframe_glob(keyframe_dir, clip_id):
    return sorted(Path(keyframe_dir).glob(f'clip_{clip_id:02d}.*'))


def _existing_keyframe_path(keyframe_dir, clip_id):
    matches = _keyframe_glob(keyframe_dir, clip_id)
    return matches[0] if matches else None


def _remove_existing_keyframes(keyframe_dir, clip_id):
    for path in _keyframe_glob(keyframe_dir, clip_id):
        path.unlink()


def generate_keyframes_and_payloads(
    script_payload,
    run_dir,
    regenerate_keyframes=False,
    create_keyframe=False,
):
    group_id = script_payload.get('metadata', {}).get('group', {}).get('id')
    product_images = list(Media.objects.filter(media_group_id=group_id, media_type='image').order_by('id')) if group_id else []
    keyframe_dir = Path(run_dir) / 'keyframes'
    payload_dir = Path(run_dir) / 'muapi_payloads'
    keyframe_dir.mkdir(parents=True, exist_ok=True)
    payload_dir.mkdir(parents=True, exist_ok=True)

    rendered_clips = []
    clip = script_payload['clip']
    keyframe_path = None
    keyframe_url = ''
    keyframe_source = 'disabled'

    if create_keyframe and regenerate_keyframes:
        _remove_existing_keyframes(keyframe_dir, clip['id'])

    if create_keyframe:
        keyframe_path = None if regenerate_keyframes else _existing_keyframe_path(keyframe_dir, clip['id'])
        keyframe_source = 'reused' if keyframe_path else 'generated'

        if keyframe_path is None:
            prompt = build_keyframe_prompt(script_payload, clip)
            image_data, mime_type = _generate_gemini_image(
                prompt,
                reference_images=product_images,
                aspect_ratio=script_payload['aspect_ratio'],
            )
            ext = _extension_from_mime(mime_type)
            keyframe_path = keyframe_dir / f'clip_{clip["id"]:02d}.{ext}'
            keyframe_path.write_bytes(image_data)

        keyframe_url = public_media_url(keyframe_path)

    references = product_reference_urls(product_images)
    payload = build_muapi_payload(
        script_payload,
        clip,
        product_reference_urls=references,
        keyframe_url=keyframe_url,
        create_keyframe=create_keyframe,
    )
    payload_path = payload_dir / f'clip_{clip["id"]:02d}.json'
    write_json(payload_path, payload)
    rendered_clips.append({
        'clip_id': clip['id'],
        'duration': clip['duration'],
        'keyframe_path': str(keyframe_path) if keyframe_path else '',
        'keyframe_url': keyframe_url,
        'muapi_payload_path': str(payload_path),
        'muapi_payload': payload,
        'keyframe_source': keyframe_source,
        'create_keyframe': create_keyframe,
        'status': 'payload_ready',
    })
    return rendered_clips


def verify_public_url(url):
    response = requests.get(url, timeout=20, stream=True)
    try:
        if response.status_code >= 400:
            hint = ''
            if response.status_code == 404 and not settings.DEBUG:
                hint = (
                    ' Django DEBUG is false, so local /media/ files are not served by '
                    'core.urls. Restart the dev server with DEBUG=True or serve media '
                    'through a public storage/proxy.'
                )
            raise VideoPocError(
                f'Generated keyframe URL is not publicly reachable '
                f'({response.status_code} {response.reason}): {url}.{hint}'
            )
    finally:
        response.close()


def _muapi_headers():
    return {
        'Content-Type': 'application/json',
        'x-api-key': ensure_env('MUAPIAPP_API_KEY'),
    }


def submit_muapi_clip(payload):
    response = requests.post(
        MUAPI_SUBMIT_URL,
        headers=_muapi_headers(),
        json=payload,
        timeout=60,
    )
    if response.status_code >= 400:
        try:
            error_body = response.json()
        except ValueError:
            error_body = response.text[:2000]
        raise VideoPocError(
            f'Muapi submit failed ({response.status_code} {response.reason}): {error_body}'
        )
    data = response.json()
    request_id = data.get('request_id') or data.get('id') or data.get('output', {}).get('id')
    if not request_id:
        raise VideoPocError(f'Muapi did not return a request_id: {data}')
    return request_id, data


def _extract_muapi_status(data):
    output = data.get('output') if isinstance(data, dict) else {}
    if not isinstance(output, dict):
        output = {}
    return str(output.get('status') or data.get('status') or '').lower()


def _extract_muapi_output_url(data):
    output = data.get('output') if isinstance(data, dict) else {}
    if not isinstance(output, dict):
        output = {}
    outputs = output.get('outputs') or data.get('outputs') or []
    if isinstance(outputs, list) and outputs:
        return outputs[0]
    if isinstance(outputs, str):
        return outputs
    for key in ('video_url', 'url', 'output_url'):
        if output.get(key):
            return output[key]
        if data.get(key):
            return data[key]
    return ''


def poll_muapi_clip(request_id, poll_interval=5, max_wait_seconds=900):
    deadline = time.monotonic() + max_wait_seconds
    last_data = {}
    while time.monotonic() < deadline:
        response = requests.get(
            MUAPI_RESULT_URL.format(request_id=request_id),
            headers=_muapi_headers(),
            timeout=60,
        )
        response.raise_for_status()
        last_data = response.json()
        status = _extract_muapi_status(last_data)
        if status in {'completed', 'succeeded', 'success'}:
            output_url = _extract_muapi_output_url(last_data)
            if not output_url:
                raise VideoPocError(f'Muapi completed without an output URL: {last_data}')
            return output_url, last_data
        if status in {'failed', 'error', 'canceled', 'cancelled'}:
            output = last_data.get('output') or {}
            error = output.get('error') or last_data.get('error') or 'Muapi generation failed.'
            raise VideoPocError(str(error))
        time.sleep(poll_interval)
    raise VideoPocError(f'Muapi timed out waiting for request {request_id}: {last_data}')


def download_file(url, output_path):
    response = requests.get(url, timeout=120, stream=True)
    response.raise_for_status()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open('wb') as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)
    return output_path


def copy_final_clip(clip_path, final_path):
    final_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(clip_path, final_path)
    return final_path


def render_script(
    script_path,
    submit_muapi=False,
    regenerate_keyframes=False,
    create_keyframe=False,
):
    script_path = Path(script_path)
    script_payload = validate_script_payload(read_json(script_path))
    run_dir = script_path.parent
    clips = generate_keyframes_and_payloads(
        script_payload,
        run_dir,
        regenerate_keyframes=regenerate_keyframes,
        create_keyframe=create_keyframe,
    )
    manifest = {
        'run_id': script_payload.get('run_id'),
        'status': 'dry_run_complete',
        'submit_muapi': submit_muapi,
        'regenerate_keyframes': regenerate_keyframes,
        'create_keyframe': create_keyframe,
        'script_path': str(script_path),
        'clips': clips,
        'updated_at': utc_now_iso(),
    }

    if submit_muapi:
        ensure_env('MUAPIAPP_API_KEY')
        clip_dir = run_dir / 'clips'
        clip = clips[0]
        if clip.get('keyframe_url'):
            verify_public_url(clip['keyframe_url'])
        request_id, submit_response = submit_muapi_clip(clip['muapi_payload'])
        output_url, result_response = poll_muapi_clip(request_id)
        clip_path = clip_dir / 'clip_01.mp4'
        download_file(output_url, clip_path)
        final_path = copy_final_clip(clip_path, run_dir / 'final.mp4')
        clip.update({
            'status': 'completed',
            'muapi_request_id': request_id,
            'muapi_submit_response': submit_response,
            'muapi_result_response': result_response,
            'muapi_output_url': output_url,
            'clip_path': str(clip_path),
        })
        manifest.update({
            'status': 'completed',
            'final_video_path': str(final_path),
            'final_video_url': public_media_url(final_path),
        })

    manifest_path = update_manifest(run_dir, manifest)
    return manifest_path

"""Friday social predicate and parser. Live draft transport is unavailable.

Blotato's documented post API publishes immediately or at a future time.
A fixture preview is never a delivered review draft.
"""
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
PROMPT = 'ops/routines/prompts/social-weekly.txt'
SECRET_NAME = 'BLOTATO_API_KEY'
ROTATING = ('market-data', 'practice-economics', 'demographics', 'carr-library')
PLATFORMS = {'twitter', 'facebook', 'instagram', 'linkedin'}
DRAFT_CONTRACT_SOURCE = 'https://help.blotato.com/rest-api-reference/publish-post'


def rotation(last_slot):
    if last_slot is None:
        return ['local', ROTATING[0]]
    if last_slot not in ROTATING:
        raise ValueError('social rotation ledger has an unknown slot')
    return ['local', ROTATING[(ROTATING.index(last_slot) + 1) % len(ROTATING)]]


def canonical_url(value):
    if not isinstance(value, str):
        raise ValueError('citation URL must be a string')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('citation needs a public HTTPS source URL')
    return urlunsplit(('https', parsed.netloc.lower(), parsed.path.rstrip('/'), parsed.query, ''))


def text(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 10000:
        raise ValueError(f'{field} must contain bounded text')
    return value.strip()


def exact_keys(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f'{label} has missing or unknown fields')


def body_key(value):
    return sha256(re.sub(r'\s+', ' ', value).strip().casefold().encode()).hexdigest()


def parse_output(value, banked_urls, prior_bodies, now):
    exact_keys(value, ['fuel', 'posts', 'dry_lanes'], 'social result')
    if not all(isinstance(value[k], list) for k in value):
        raise ValueError('fuel, posts and dry_lanes must be arrays')
    if len(value['fuel']) > 6 or len(value['posts']) > 30:
        raise ValueError('social result exceeds the bounded batch')
    banked = {canonical_url(url) for url in banked_urls}
    bodies = {body_key(item) for item in prior_bodies}
    seen_urls = set(banked)
    cited = set()
    fuel = []
    horizons = {'local': 60, 'market-data': 100, 'practice-economics': 365, 'demographics': 730}
    for item in value['fuel']:
        exact_keys(item, ['input', 'citation', 'lane', 'email_angle'], 'fuel entry')
        exact_keys(item['citation'], ['organization', 'title', 'date', 'url', 'quote'], 'fuel citation')
        citation = item['citation']
        url = canonical_url(citation['url'])
        source_date = date.fromisoformat(text(citation['date'], 'citation date'))
        if source_date > now.date():
            raise ValueError('fuel citation cannot be future dated')
        lane = item['lane']
        if lane not in ('local', *ROTATING):
            raise ValueError('fuel lane is unknown')
        if lane != 'carr-library' and (now.date() - source_date).days > horizons[lane]:
            raise ValueError('fuel citation exceeds lane freshness horizon')
        normalized = {'input': text(item['input'], 'fuel input'), 'lane': lane,
                      'email_angle': text(item['email_angle'], 'email angle'),
                      'citation': {k: text(citation[k], f'citation {k}')
                                   for k in ('organization', 'title', 'date', 'quote')}}
        normalized['citation']['url'] = url
        if url in seen_urls:
            continue
        seen_urls.add(url)
        cited.add(url)
        fuel.append(normalized)
    posts = []
    for post in value['posts']:
        exact_keys(post, ['platform', 'kind', 'text', 'citation_urls'], 'post')
        if post['platform'] not in PLATFORMS:
            raise ValueError('post platform is unknown')
        if post['kind'] not in ('standalone', 'one-liner'):
            raise ValueError('weekly batch accepts standalone posts and one-liners only')
        body = text(post['text'], 'post body')
        if not isinstance(post['citation_urls'], list) or not post['citation_urls']:
            raise ValueError('post must cite retained primary-source fuel')
        urls = [canonical_url(url) for url in post['citation_urls']]
        if any(url not in cited and url not in banked for url in urls):
            raise ValueError('post cites a source outside the fuel bank')
        fingerprint = body_key(body)
        if fingerprint in bodies or all(url in banked for url in urls):
            continue
        bodies.add(fingerprint)
        posts.append({'platform': post['platform'], 'kind': post['kind'], 'text': body,
                      'citation_urls': urls, 'body_hash': fingerprint})
    return {'fuel': fuel, 'posts': posts,
            'dry_lanes': [text(lane, 'dry lane') for lane in value['dry_lanes']]}


def prepare(ctx):
    local_now = ctx.now.astimezone(ZoneInfo('America/Chicago'))
    week = (local_now.date() + timedelta(days=(7 - local_now.weekday()) % 7)).isoformat()
    if local_now.weekday() != 4:
        return {'work': False, 'reason': 'outside_friday', 'week': week}
    if not ctx.secret(SECRET_NAME):
        return {'work': True, 'reason': 'missing_blotato_key', 'week': week}
    if not ctx.dry_run or ctx.fixture is None:
        return {'work': True, 'reason': 'draft_transport_unavailable', 'week': week}
    fixture = ctx.fixture
    if fixture.get('completed_week') == week:
        return {'work': False, 'reason': 'already_completed', 'week': week}
    return {'work': True, 'reason': 'fixture_preview', 'week': week,
            'lanes': rotation(fixture.get('last_slot')),
            'banked_urls': fixture.get('banked_urls', []),
            'prior_bodies': fixture.get('prior_bodies', [])}


def lint_body(body):
    result = subprocess.run([str(ROOT / 'run.sh'), 'lint', '-', '--surface', 'social'],
                            input=body, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError('social post failed run.sh lint --surface social')
    return {'hard_hits': 0, 'review_required': '[REVIEW]' in result.stdout}


def execute(ctx, plan):
    if not plan['work']:
        return {'skipped': plan['reason'], 'model_calls': 0, 'writes': 0}
    reason = plan['reason']
    if reason in ('missing_blotato_key', 'draft_transport_unavailable'):
        if reason == 'missing_blotato_key':
            title = 'Social drafts need the Blotato connection'
            body = ('Configure BLOTATO_API_KEY in the launch environment or '
                    '~/.config/carr/routines.env. The resolver reads routines.env '
                    'and falls back to db.env using the existing credential parser. '
                    'Verify the next dry run reads the key without printing it and remains review-only. '
                    'No social research or drafting ran.')
            key = 'social-weekly:missing-blotato-key'
        else:
            title = 'Social drafts need a verified Blotato review-only writer'
            body = ('Blotato POST /v2/posts publishes immediately or at its scheduled time. '
                    'Implement a supported draft-only writer and verify saved posts remain unpublished '
                    'until Joe approves them. Do not substitute timed publication. '
                    f'Contract checked at {DRAFT_CONTRACT_SOURCE}. No social research or drafting ran.')
            key = 'social-weekly:draft-transport-unavailable'
        if not ctx.dry_run:
            ctx.review_item(title, body, key)
        return {'blocked': reason, 'model_calls': 0, 'writes': 0 if ctx.dry_run else 1,
                'review_item_key': key}
    if not ctx.dry_run or ctx.fixture is None:
        raise ValueError('fixture preview cannot execute in production')
    parsed = parse_output(ctx.fixture['model_output'], plan['banked_urls'], plan['prior_bodies'], ctx.now)
    if any(item['lane'] not in plan['lanes'] for item in parsed['fuel']):
        raise ValueError('fuel result does not match code-selected rotation')
    preview = [{**post, 'lint': lint_body(post['text']), 'state': 'review-preview'}
               for post in parsed['posts']]
    return {'dry_run': True, 'week': plan['week'], 'lanes': plan['lanes'],
            'fuel': len(parsed['fuel']), 'drafts': len(preview), 'draft_preview': preview,
            'model_calls': 0, 'writes': 0, 'delivery': 'draft_transport_unavailable'}

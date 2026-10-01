"""Cached GitHub census for the existing progress-board publisher, never a page read.

Read-only GitHub operations. The cache is projection data in board_snapshot;
source identity and suggestions do not grant branch or pull-request authority.
"""
import json
import re
import subprocess
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from pathlib import Path

REPOSITORIES = ("jbookout/carr-system", "jbookout/doctorcre-app")


def github_json(args):
    result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(result.stdout)


def dot_suggestions(report):
    """Parse only the report's branch headings; prose stays in its private record."""
    section = None
    result = {}
    for line in report.splitlines():
        if line.startswith('ALREADY LANDED ANOTHER WAY'): section = 'cancel'
        elif line.startswith('SUPERSEDED') or 'Superseded, remaining' in line: section = 'cancel'
        elif 'Abandoned work worth finishing' in line: section = 'progress'
        elif 'Abandoned work to cancel' in line: section = 'cancel'
        if line.startswith('Last commit:'):
            name = previous.strip()
            if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/_.-]+', name):
                result[name] = {'action': section, 'label': "The Dot's suggestion", 'source': 'Dot job13 report G, 2026-10-01'}
        previous = line
    return result


def collect_github(now, read=github_json, suggestions=None):
    items, coverage, pr_heads, pr_work_refs = [], [], [], set()
    for repo in REPOSITORIES:
        try:
            pr_pages = read(['api',f'repos/{repo}/pulls?state=all&per_page=100','--paginate','--slurp'])
            prs = [pr for page in pr_pages for pr in page]
            for pr in prs:
                pr_heads.append({'repository':repo,'branch':pr['head']['ref']})
                pr_work_refs.update(re.findall(r'WR-\d+',pr.get('body') or ''))
                if pr.get('state','open') != 'open': continue
                items.append({'id': f"{repo}:pr:{pr['number']}", 'kind':'pull_request','title':pr['title'],
                    'state':'open', 'opened_at':pr['created_at'],
                    'last_activity_at':pr['updated_at'], 'owner':None,'version':None,
                    'completed':False,'cancelled':False,'link':pr['html_url'],
                    'branch':pr['head']['ref'],'work_refs':sorted(set(re.findall(r'WR-\d+',pr.get('body') or ''))),'identity':{}})
            pages = read(['api',f'repos/{repo}/branches?per_page=100','--paginate','--slurp'])
            for branch in [b for page in pages for b in page]:
                name=branch['name']
                if name=='main': continue
                commit = read(['api',f"repos/{repo}/commits/{branch['commit']['sha']}"])
                date=commit['commit']['committer']['date']
                if now-datetime.fromisoformat(date.replace('Z','+00:00')) <= timedelta(days=7): continue
                compare=read(['api',f'repos/{repo}/compare/main...{quote(name,safe="")}'])
                if compare.get('ahead_by',0)==0: continue
                items.append({'id':f'{repo}:branch:{name}','kind':'remote_branch','title':name,'state':'unmerged',
                    'opened_at':date,'last_activity_at':date,'owner':None,'version':branch['commit']['sha'],
                    'completed':False,'cancelled':False,'link':f'https://github.com/{repo}/tree/{quote(name,safe="")}',
                    'identity':{},'suggested_triage':(suggestions or {}).get(name) if repo.endswith('/carr-system') else None})
            coverage.append({'repository':repo,'complete':True,'reason':None})
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            coverage.append({'repository':repo,'complete':False,'reason':'github_read_failed'})
    return {'schema':'system-work-external.v1','observed_at':now.isoformat(),
            'complete':all(c['complete'] for c in coverage),'coverage':coverage,'pr_heads':pr_heads,'pr_work_refs':sorted(pr_work_refs),'items':items}


def unfinished_briefs(root, pr_heads):
    """Unstructured local briefs: include only explicit builders in either code home.

    Relationship gaps remain partial; no absence of an associated PR is proof of
    completion. No brief body is published into the cache.
    """
    rows=[]
    for path in sorted(Path(root).rglob('*brief*')):
        if not path.is_file() or path.suffix not in ('.md','.txt','.json'): continue
        text=path.read_text(errors='replace')
        if not re.search(r'(?im)^ROLE:\s*builder\b',text): continue
        repos=[repo for repo in REPOSITORIES if repo.split('/')[1] in text]
        if not repos: continue
        if re.search(r'(?:/pull/|(?i:PR)\s*#)\d+',text): continue
        branch=re.search(r'(?im)^BRANCH:\s*([A-Za-z0-9/_.-]+)',text)
        if branch and any(h['branch']==branch[1] and h['repository'] in repos for h in pr_heads): continue
        date=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat()
        relative=str(path.relative_to(root))
        rows.append({'id':relative,'kind':'builder_brief_file','title':path.stem,'state':'pr_unlinked',
            'opened_at':date,'last_activity_at':date,'owner':None,'version':None,'identity':{},
            'completed':False,'cancelled':False,'link':'/control-room/progress',
            'suggested_triage':{'action':'progress','label':'Reassess unlinked builder brief'}})
    return rows


def cached_github(path, report_path=None, now=None, read=github_json):
    now = now or datetime.now(timezone.utc)
    path = Path(path)
    try:
        cache=json.loads(path.read_text())
        date=datetime.fromisoformat(cache['observed_at'].replace('Z','+00:00'))
        if cache.get('schema')=='system-work-external.v1' and timedelta(0)<=now-date<timedelta(minutes=15): return cache
    except (OSError, ValueError, KeyError): pass
    suggestions={}
    if report_path and Path(report_path).is_file(): suggestions=dot_suggestions(Path(report_path).read_text())
    cache=collect_github(now,read,suggestions)
    try:
        cache["items"].extend(unfinished_briefs(Path.home() / "carr-system/out/orch", cache["pr_heads"]))
        cache.pop("pr_heads", None)
    except OSError:
        cache["briefs_unavailable"] = True
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(cache,sort_keys=True))
    return cache

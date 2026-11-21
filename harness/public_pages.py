"""Crawlable HTML views of the published portfolio."""

import json
import os
from html import escape
from pathlib import Path
from re import sub
from string import Template
from xml.sax.saxutils import escape as xml_escape

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

ABOUT = '/about'
CASE_STUDY = '/writing/harness-over-headcount'
RESTART_BENCHMARK = '/writing/personal-agent-harness-restart-benchmark'
RESTART_TITLE = 'Personal Agent Harness restart benchmark'
RESTART_REVISION = 'dd0a21ef4952334c5b67204aa6228240e18dbf26'
STYLESHEET = '/portfolio-reading.css'


def public_origin(request):
    return (os.getenv('PUBLIC_ORIGIN') or str(request.base_url)).rstrip('/')


def has_bio(profile):
    return profile.name.strip() not in ('', 'Your name')


def case_study(profile):
    return next((project for project in profile.projects if project.id == 'factory' and project.title.strip()), None)


def link(url, label):
    return f'<a href="{escape(url)}">{escape(label)}</a>'


def description(profile):
    return ' · '.join(field for field in (profile.name, profile.role, profile.focus, profile.location) if field)


def metadata(origin, path, summary):
    return (f'<meta name="description" content="{escape(summary)}">'
            f'<link rel="canonical" href="{escape(origin + path)}">'
            f'<link rel="stylesheet" href="{STYLESHEET}">')


def homepage_html(html, profile, origin):
    if not has_bio(profile):
        return html
    title = escape(f'{profile.name} — {profile.role or "Portfolio"}')
    html = sub(r'<title>[^<]*</title>', lambda _: f'<title>{title}</title>', html, count=1)
    html = sub(r'<meta name="description"[^>]*>', '', html, count=1)
    html = html.replace('</head>', metadata(origin, '/', description(profile)) + '</head>', 1)
    project = case_study(profile)
    reading = f'<nav class="portfolio-reading" aria-label="Read the portfolio">{link(ABOUT, "About " + profile.name)}'
    if project:
        reading += link(CASE_STUDY, project.title)
    reading += link(RESTART_BENCHMARK, RESTART_TITLE) + '</nav>'
    return html.replace('</body>', reading + '</body>', 1)


def page(origin, path, title, summary, body, structured=None):
    data = ''
    if structured:
        payload = json.dumps(structured, ensure_ascii=False).replace('<', '\\u003c')
        data = f'<script type="application/ld+json">{payload}</script>'
    return HTMLResponse(f'''<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>{metadata(origin, path, summary)}{data}</head>
<body class="reading-page">
<header class="reading-header"><nav aria-label="Portfolio">{link('/', 'Portfolio')}{link(ABOUT, 'About') if path != ABOUT else ''}</nav></header>
<main class="reading-content">{body}</main>
</body></html>''')


def bio_page(profile, project, origin):
    byline = ' · '.join(field for field in (profile.role, profile.location) if field)
    bio = f'<p class="reading-lede">{escape(profile.summary)}</p>' if profile.summary else ''
    focus = f'<p>{escape(profile.focus)}</p>' if profile.focus else ''
    linkedin = link(profile.linkedin, 'LinkedIn profile') if profile.linkedin else ''
    career = ''
    if profile.career:
        entries = ''.join(
            f'<li><h3>{escape(job.role)} at {escape(job.company)}</h3>'
            f'<p class="reading-muted">{escape(job.dates)}</p><p>{escape(job.note)}</p></li>'
            for job in profile.career
        )
        career = f'<section><h2>Experience</h2><ol class="reading-experience">{entries}</ol></section>'
    work = '<section><h2>Engineering writing</h2>'
    if project:
        work += f'<h3>{link(CASE_STUDY, project.title)}</h3><p>{escape(project.description)}</p>'
    work += f'<h3>{link(RESTART_BENCHMARK, RESTART_TITLE)}</h3></section>'
    person = {'@type': 'Person', '@id': origin + ABOUT + '#person', 'name': profile.name, 'url': origin + ABOUT}
    if profile.role:
        person['jobTitle'] = profile.role
    if profile.linkedin:
        person['sameAs'] = [profile.linkedin]
    return page(origin, ABOUT, f'{profile.name} — About', description(profile), f'''
<h1>{escape(profile.name)}</h1><p class="reading-byline">{escape(byline)}</p>
{bio}{focus}{linkedin}{work}{career}''',
                {'@context': 'https://schema.org', '@type': 'ProfilePage', 'mainEntity': person})


def project_page(profile, project, origin):
    mojo = next((item for item in profile.projects if item.id == 'mojo' and item.url), None)
    source_links = link('/api/public/profile', 'Published portfolio data')
    if mojo:
        source_links += ' · ' + link(mojo.url, 'Mojo product site')
    return page(origin, CASE_STUDY, f'{project.title} — Coding agents and human review', project.description, f'''
<article>
  <header><p class="reading-byline">{escape(project.company)}</p><h1>{escape(project.title)}</h1>
  <p class="reading-lede">{escape(project.description)}</p>
  <p>By {link(ABOUT, profile.name)}</p></header>
  <section><h2>How the coding agents fit into review</h2>
    <p>The team’s Dispatcher put a build agent and a separate review agent in the same workflow. A human approved the work before it could merge.</p>
    <ol class="reading-workflow">
      <li><strong>Build agent</strong><span>Prepares the change.</span></li>
      <li><strong>Review agent</strong><span>Reviews the change before human approval.</span></li>
      <li><strong>Human approval</strong><span>A person makes the decision to merge.</span></li>
    </ol>
  </section>
  <section><h2>What the team measured</h2>
    <p class="reading-result">{escape(project.outcome)}</p><p>{escape(project.detail)}</p>
    <p class="reading-muted">These figures come from the team’s rollout. Merged PRs measure throughput. A fuller comparison would include the dates of both measurement windows, PR size, work mix, quality after merge, and time spent on human review.</p>
  </section>
  <section><h2>Where to start</h2>
    <p>Pick one class of changes. Track merged PRs per week, review rounds, time spent on human review, and cost per merged PR before and after adding the agent reviewer. Keep an eye on rollbacks and follow-up fixes so the throughput numbers have a quality check.</p>
  </section>
  <footer>{source_links}<p>Related work · {link(RESTART_BENCHMARK, RESTART_TITLE)}</p></footer>
</article>''')


def restart_benchmark_page(profile, origin):
    template = Path(__file__).with_name('agent_docs') / 'restart-benchmark.html'
    body = Template(template.read_text(encoding='utf-8')).substitute(
        author=link(ABOUT, profile.name), runner_revision=RESTART_REVISION,
    )
    summary = 'An 18-trial Temporal benchmark for AI agent restarts during approval, tool retries and duplicate writes. Includes reproduction steps.'
    return page(origin, RESTART_BENCHMARK, RESTART_TITLE, summary, body, {
        '@context': 'https://schema.org', '@type': 'TechArticle', 'headline': RESTART_TITLE,
        'author': {'@type': 'Person', 'name': profile.name, 'url': origin + ABOUT},
        'mainEntityOfPage': origin + RESTART_BENCHMARK,
    })


def mount_public_pages(app, get_portfolio):
    @app.api_route(ABOUT, methods=['GET', 'HEAD'], include_in_schema=False)
    def about(request: Request):
        profile = get_portfolio()
        if not has_bio(profile):
            raise HTTPException(404, 'No public bio is published.')
        return bio_page(profile, case_study(profile), public_origin(request))

    @app.api_route(CASE_STUDY, methods=['GET', 'HEAD'], include_in_schema=False)
    def harness_case_study(request: Request):
        profile = get_portfolio()
        project = case_study(profile)
        if not has_bio(profile) or not project:
            raise HTTPException(404, 'No case study is published.')
        return project_page(profile, project, public_origin(request))

    @app.api_route(RESTART_BENCHMARK, methods=['GET', 'HEAD'], include_in_schema=False)
    def restart_benchmark(request: Request):
        profile = get_portfolio()
        if not has_bio(profile):
            raise HTTPException(404, 'No benchmark is published.')
        return restart_benchmark_page(profile, public_origin(request))

    @app.get(STYLESHEET, include_in_schema=False)
    def styles():
        return FileResponse(Path(__file__).with_name('portfolio-reading.css'), media_type='text/css')

    @app.api_route('/sitemap.xml', methods=['GET', 'HEAD'], include_in_schema=False)
    def sitemap(request: Request):
        origin = public_origin(request)
        profile = get_portfolio()
        paths = ['/', '/agents']
        if has_bio(profile):
            paths.extend((ABOUT, RESTART_BENCHMARK))
            if case_study(profile):
                paths.append(CASE_STUDY)
        urls = ''.join(f'<url><loc>{xml_escape(origin + path)}</loc></url>' for path in paths)
        return PlainTextResponse('<?xml version="1.0" encoding="UTF-8"?>'
                                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                                 f'{urls}</urlset>', media_type='application/xml')

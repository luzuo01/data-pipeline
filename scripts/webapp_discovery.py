"""Inspect the restarted application before selecting browser controls."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / '_validation/webapp-20261101'
OUT.mkdir(parents=True, exist_ok=True)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, channel=os.getenv('PLAYWRIGHT_CHANNEL', 'msedge'))
    try:
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.goto('http://127.0.0.1:8765/', wait_until='networkidle')
        page.screenshot(path=str(OUT / '01-home.png'), full_page=True)
        result = {'title': page.title(), 'buttons': page.get_by_role('button').all_text_contents(),
                  'fields': page.locator('input,textarea,select').evaluate_all('(els) => els.map(e => ({id:e.id,type:e.type,value:e.value}))'),
                  'status': page.get_by_role('status').inner_text()}
        (OUT / 'discovery.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))
    finally:
        browser.close()

"""Browser regressions; UI-only media fixture, no claim of physical rendering."""
import argparse,json
from pathlib import Path
from playwright.sync_api import sync_playwright

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8765')
    parser.add_argument('--video-url',help='Optional real MP4 URL to also verify decoding after refresh')
    parser.add_argument('--output',type=Path,default=Path('_validation/retest/browser'))
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,channel='msedge')
        try:
            page=browser.new_page();errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(args.url);page.wait_for_load_state('networkidle')
            page.evaluate("previewUrl='/fixture.mp4';setPreview(previewUrl,'video','test')")
            page.get_by_role('button',name='刷新截图',exact=True).click()
            assert page.locator('#video-preview').is_visible()
            assert not page.locator('#preview').is_visible()
            assert page.locator('#video-preview').get_attribute('src').startswith('/fixture.mp4')
            page.evaluate("previewUrl='/fixture.png';setPreview(previewUrl,'image','test')")
            page.get_by_role('button',name='刷新截图',exact=True).click()
            assert page.locator('#preview').evaluate('(el) => !el.hidden')
            assert not page.locator('#video-preview').is_visible()
            if args.video_url:
                page.evaluate('(url)=>{previewUrl=url;setPreview(url,"video","real render");}',args.video_url)
                page.wait_for_function('document.getElementById("video-preview").readyState >= 2')
                page.get_by_role('button',name='刷新截图',exact=True).click()
                page.wait_for_function('document.getElementById("video-preview").readyState >= 2')
                assert page.locator('#video-preview').is_visible()
                assert page.locator('#video-preview').evaluate('(el)=>el.duration > 0 && el.videoWidth === 960')
                page.screenshot(path=str(args.output/'real-video-refresh.png'))
            assert not errors,errors
            result={'video_refresh':True,'image_refresh':True,'page_errors':errors,'real_video_decoded':bool(args.video_url)}
            (args.output/'refresh-results.json').write_text(json.dumps(result),encoding='utf-8')
            print(result)
        finally: browser.close()
if __name__=='__main__':main()

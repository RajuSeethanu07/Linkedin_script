import re
import time
from datetime import date

import gspread
from google.oauth2.service_account import Credentials

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager

# ===================== CONFIG =====================
# Guest LinkedIn caps each search at ~70-80 jobs. To get past that, use several
# search URLs (different keyword sets) and scrape each one fresh. The _gs_seen
# dedup set inside save_to_gsheet() drops any job two URLs both return.
URLS = [

    "https://www.linkedin.com/jobs/search/?currentJobId=4438981819&f_E=2&f_TPR=r604800&geoId=102713980&keywords=%22AI%20Engineer%22%20OR%20%22Machine%20Learning%20Engineer%22%20OR%20%22ML%20Engineer%22%20OR%20%22Artificial%20Intelligence%20Engineer%22%20OR%20%22Applied%20AI%20Engineer%22&origin=JOB_SEARCH_PAGE_LOCATION_AUTOCOMPLETE&refresh=true",

    "https://www.linkedin.com/jobs/search/?currentJobId=4434837084&f_E=2&f_TPR=r604800&geoId=102713980&keywords=%22AI%20Engineer%22%20OR%20%22ML%20Engineer%22%20OR%20%22Machine%20Learning%20Intern%22%20OR%20%22AI%20Intern%22%20OR%20%22Associate%20AI%20Engineer%22%20OR%20%22Junior%20AI%20Engineer%22&origin=JOB_SEARCH_PAGE_SEARCH_BUTTON&refresh=true",
    
    "https://www.linkedin.com/jobs/search/?currentJobId=4420646547&f_E=2&f_TPR=r604800&geoId=102713980&keywords=%22AI%20Engineer%22%20OR%20%22Generative%20AI%20Engineer%22%20OR%20%22Machine%20Learning%20Engineer%22%20OR%20%22Applied%20AI%20Engineer%22%20OR%20%22LLM%20Engineer%22&origin=JOB_SEARCH_PAGE_SEARCH_BUTTON&refresh=true",

    #"https://www.linkedin.com/jobs/search/?f_E=2&f_TPR=r604800&geoId=102713980&keywords=%22Data%20Scientist%22%20OR%20%22AI%20Platform%20Engineer%22%20OR%20%22AI%20Developer%22&origin=JOB_SEARCH_PAGE_SEARCH_BUTTON&refresh=true",
]

SCROLL_PAUSE = 2      # Seconds to wait after each scroll / See-more click
MAX_ROUNDS = 10      # Internal safety cap on scroll/See-more rounds per URL

# ---------- Google Sheets ----------
GSHEET_CREDS = r"d:\linkedin_automate\hh-marketing-479311-0061c465d078.json"
SPREADSHEET_NAME = "Linkedin jobs"
# Tab name -> "Linkedin Jobs 13-07-2026" (new tab per day; same-day reruns append)
WORKSHEET_NAME = "Linkedin Jobs " + date.today().strftime("%d-%m-%Y")

# Columns written to the Google Sheet (fixed order)
COLUMNS = ["Title", "Company", "Company Profile URL",
           "Location", "Experience", "Salary", "Job URL"]

# ===================== CHROME SETUP =====================
options = Options()
options.add_argument("--start-maximized")
options.add_argument("--disable-blink-features=AutomationControlled")

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=options
)
# Prevent detail pages from hanging forever
driver.set_page_load_timeout(25)
wait = WebDriverWait(driver, 15)


# ===================== GOOGLE SHEET SAVE =====================
_gs_ws = None          # cached worksheet handle
_gs_seen = None        # set of canonical Job URLs already present in today's tab


def _canon_url(u):
    """LinkedIn appends per-search tracking params (?refId=...&trackingId=...)
    to each card link, so the SAME job has a DIFFERENT full URL in every search.
    The stable identity is the job id in '/jobs/view/<id>'. Reduce to that so
    the dedup set catches the same job across different search URLs."""
    if not u:
        return u
    m = re.search(r"/jobs/view/(\d+)", u)
    if m:
        return f"https://www.linkedin.com/jobs/view/{m.group(1)}/"
    return u.split("?")[0]   # fallback: drop query string


def _gs_connect():
    """Open the spreadsheet, get/create today's tab, load existing Job URLs."""
    global _gs_ws, _gs_seen
    if _gs_ws is not None:
        return _gs_ws

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(GSHEET_CREDS, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open(SPREADSHEET_NAME)

    _gs_seen = set()
    try:
        ws = sh.worksheet(WORKSHEET_NAME)   # today's tab exists -> reuse (append)
        rows = ws.get_all_values()
        if rows and "Job URL" in rows[0]:
            ji = rows[0].index("Job URL")
            for r in rows[1:]:
                if len(r) > ji and r[ji]:
                    _gs_seen.add(_canon_url(r[ji]))
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=WORKSHEET_NAME, rows=1000, cols=len(COLUMNS))
        ws.append_row(COLUMNS)              # header row on a fresh tab
        print(f"🆕 Created new tab: {WORKSHEET_NAME}")

    _gs_ws = ws
    print(f"🔗 Google Sheet ready: '{SPREADSHEET_NAME}' -> tab '{WORKSHEET_NAME}' ({len(_gs_seen)} existing rows)")
    return ws


def save_to_gsheet(job_list):
    """Append only jobs whose Job URL isn't already in today's tab."""
    if not job_list:
        return
    try:
        ws = _gs_connect()
    except Exception as e:
        print(f"   ⚠️  Google Sheet connect failed ({type(e).__name__}: {e}) — skipping sheet save")
        return

    new_rows = []
    for j in job_list:
        url = j.get("Job URL")
        key = _canon_url(url)   # dedup on job id, not the tracking-laden full URL
        if key and key != "Not Disclosed" and key not in _gs_seen:
            j["Job URL"] = key                       # store the clean URL in the sheet
            new_rows.append([j.get(c, "") for c in COLUMNS])
            _gs_seen.add(key)

    if new_rows:
        try:
            ws.append_rows(new_rows, value_input_option="USER_ENTERED")
            print(f"   📗 Sheet +{len(new_rows)} new rows (tab total {len(_gs_seen)})")
        except Exception as e:
            print(f"   ⚠️  Sheet append failed ({type(e).__name__}: {e})")


# ===================== SCRAPE ONE CARD =====================
def scrape_card(card):
    try:
        title = card.find_element(By.CSS_SELECTOR, "h3.base-search-card__title").get_attribute("innerText").strip() or "Not Disclosed"
    except:
        title = "Not Disclosed"
    try:
        company = card.find_element(By.CSS_SELECTOR, "h4.base-search-card__subtitle").get_attribute("innerText").strip() or "Not Disclosed"
    except:
        company = "Not Disclosed"
    try:
        location = card.find_element(By.CSS_SELECTOR, "span.job-search-card__location").get_attribute("innerText").strip() or "Not Disclosed"
    except:
        location = "Not Disclosed"
    try:
        job_url = card.find_element(By.CSS_SELECTOR, "a.base-card__full-link").get_attribute("href") or "Not Disclosed"
    except:
        job_url = "Not Disclosed"

    salary = "Not Disclosed"
    experience = "Not Disclosed"
    company_profile_url = "Not Disclosed"

    # ---------------- Open job detail page ----------------
    if job_url != "Not Disclosed":
        opened_tab = False
        try:
            # Open blank tab, then driver.get() so set_page_load_timeout applies
            # (JS window.open bypasses the page-load timeout and can hang forever)
            driver.switch_to.new_window('tab')
            opened_tab = True
            driver.get(job_url)   # raises TimeoutException after 25s -> caught below

            page_text = driver.find_element(By.TAG_NAME, "body").text

            try:
                salary_elem = driver.find_element(By.XPATH, "//span[contains(text(),'₹') or contains(text(),'$')]")
                if salary_elem.text.strip():
                    salary = salary_elem.text.strip()
            except:
                pass

            try:
                match = re.search(r'(\d+\+?\s*[-–]?\s*\d*\+?\s*years?)', page_text, re.I)
                if match:
                    experience = match.group(1)
            except:
                pass

            try:
                company_profile_url = driver.find_element(By.XPATH, "//a[contains(@href,'/company/')]").get_attribute("href")
            except:
                company_profile_url = "Not Disclosed"
        except Exception as e:
            print(f"   ⚠️  Detail page failed ({type(e).__name__}) — skipping detail")
        finally:
            if opened_tab and len(driver.window_handles) > 1:
                driver.close()
            driver.switch_to.window(driver.window_handles[0])

    return {
        "Title": title,
        "Company": company,
        "Company Profile URL": company_profile_url,
        "Location": location,
        "Experience": experience,
        "Salary": salary,
        "Job URL": job_url,
    }


def load_all_cards():
    """Guest LinkedIn has NO numbered pagination. Scroll to bottom and click
    'See more jobs' over and over until no new cards appear. Fully automatic —
    stops itself when LinkedIn has nothing more to give."""
    see_more_selectors = [
        "button.infinite-scroller__show-more-button",
        "button[aria-label='See more jobs']",
        "button[data-tracking-control-name='infinite-scroller_show-more']",
    ]
    last_count = 0
    stagnant = 0
    for r in range(MAX_ROUNDS):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(SCROLL_PAUSE)

        # Click "See more jobs" if the button is present
        clicked = False
        for sel in see_more_selectors:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, sel)
                if btn.is_displayed() and btn.is_enabled():
                    driver.execute_script("arguments[0].click();", btn)
                    clicked = True
                    time.sleep(SCROLL_PAUSE)
                    break
            except Exception:
                continue

        count = len(driver.find_elements(By.CSS_SELECTOR, "div.base-search-card"))
        print(f"🔽 Round {r+1}: {count} cards loaded{' (clicked See more)' if clicked else ''}")

        if count == last_count:
            stagnant += 1
            if stagnant >= 3:      # 3 rounds with nothing new -> reached the limit
                print("🛑 No more cards loading — reached LinkedIn's guest limit.")
                break
        else:
            stagnant = 0
        last_count = count

    return driver.find_elements(By.CSS_SELECTOR, "div.base-search-card")


def scrape_url(url, url_idx, url_total):
    """Load one search URL fresh, load all its cards, scrape + save each job
    one-by-one. Returns number of jobs scraped from this URL. Never raises —
    a bad URL (auth wall, no results, crash) is logged and skipped so the
    master loop moves on to the next URL cleanly."""
    print(f"\n{'='*70}\n🌐 [URL {url_idx}/{url_total}] Opening: {url[:80]}...\n{'='*70}")
    try:
        driver.get(url)
    except Exception as e:
        print(f"⚠️  Failed to load URL ({type(e).__name__}: {e}) — skipping.")
        return 0

    # Detect auth wall / no results instead of silently hanging
    try:
        wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.base-search-card")))
    except Exception:
        print("⚠️  No job cards found (login/auth wall or blocked). Skipping this URL.")
        print("    Current URL:", driver.current_url)
        print("    Page title :", driver.title)
        return 0

    cards = load_all_cards()
    print(f"\n📋 [URL {url_idx}] Total cards to scrape: {len(cards)}")

    scraped = 0
    for card in cards:
        scraped += 1
        print(f"[URL {url_idx}] [#{scraped}/{len(cards)}] scraping...")
        job = scrape_card(card)
        print(f"   {job['Title'][:50]} @ {job['Company'][:30]}")
        # Save ONLY this job — no growing master list (dedup handled inside).
        save_to_gsheet([job])

    return scraped


# ===================== MASTER LOOP =====================
total_scraped = 0

try:
    for i, url in enumerate(URLS, start=1):
        total_scraped += scrape_url(url, i, len(URLS))

except KeyboardInterrupt:
    print("\n⏹️  Stopped by user (Ctrl+C).")

# ===================== WRAP-UP (always runs) =====================
finally:
    try:
        driver.quit()
    except Exception:
        pass
    print(f"\n✅ Done. Scraped {total_scraped} jobs across {len(URLS)} URLs | Google Sheet tab: {WORKSHEET_NAME}")

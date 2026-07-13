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
URL = "https://www.linkedin.com/jobs/search/?currentJobId=4378319072&f_E=2&f_TPR=r604800&geoId=102713980&keywords=%22AI%20Engineer%22%20OR%20%22Generative%20AI%20Engineer%22%20OR%20%22Machine%20Learning%20Engineer%22%20OR%20%22LLM%20Engineer%22%20OR%20%22Applied%20AI%20Engineer%22%20OR%20%22AI%20Developer%22%20OR%20%22AI%20Software%20Engineer%22%20OR%20%22NLP%20Engineer%22%20OR%20%22ML%20Engineer%22%20OR%20%22Data%20Scientist%22%20OR%20%22AI%20Research%20Engineer%22%20OR%20%22Prompt%20Engineer%22%20OR%20%22AI%20Platform%20Engineer%22&origin=JOB_SEARCH_PAGE_SEARCH_BUTTON&refresh=true"
SCROLL_PAUSE = 2      # Seconds to wait after each scroll / See-more click

# ---------- Google Sheets ----------
GSHEET_CREDS = r"d:\linkedin_automate\hh-marketing-479311-0061c465d078.json"
SPREADSHEET_NAME = "Naukri Jobs Data"
# Tab name -> "Linkedin Jobs 13-07-2026" (new tab per day; same-day reruns append)
WORKSHEET_NAME = "Linkedin Jobs " + date.today().strftime("%d-%m-%Y")

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

# ===================== OPEN LINKEDIN =====================
print(f"🌐 Opening: {URL[:80]}...")
driver.get(URL)

# Detect auth wall / no results instead of silently hanging
try:
    wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.base-search-card")))
except Exception:
    print("⚠️  No job cards found. LinkedIn likely showing a login/auth wall or blocked the request.")
    print("    Current URL:", driver.current_url)
    print("    Page title :", driver.title)
    driver.quit()
    raise SystemExit(1)

# Cards are loaded page-by-page inside the pagination loop below.
jobs = []


# Columns written to the Google Sheet (fixed order)
COLUMNS = ["Title", "Company", "Company Profile URL",
           "Location", "Experience", "Salary", "Job URL"]


# ===================== GOOGLE SHEET SAVE =====================
_gs_ws = None          # cached worksheet handle
_gs_seen = None        # set of Job URLs already present in today's tab


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
                    _gs_seen.add(r[ji])
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
        if url and url != "Not Disclosed" and url not in _gs_seen:
            new_rows.append([j.get(c, "") for c in COLUMNS])
            _gs_seen.add(url)

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


# ===================== LOAD + SCRAPE =====================
MAX_ROUNDS = 80          # internal safety cap on scroll/See-more rounds
idx = 0

try:
    cards = load_all_cards()
    print(f"\n📋 Total cards to scrape: {len(cards)}")

    for card in cards:
        idx += 1
        print(f"[#{idx}/{len(cards)}] scraping...")
        job = scrape_card(card)
        jobs.append(job)
        print(f"[{idx}] {job['Title'][:50]} @ {job['Company'][:30]}")

        # Save this job to the Sheet immediately (dedup handled inside)
        save_to_gsheet(jobs)

except KeyboardInterrupt:
    print("\n⏹️  Stopped by user (Ctrl+C) — saving everything scraped so far...")

# ===================== WRAP-UP (always runs) =====================
finally:
    save_to_gsheet(jobs)
    try:
        driver.quit()
    except Exception:
        pass
    print(f"\n✅ Done. Scraped {len(jobs)} jobs this run | Google Sheet tab: {WORKSHEET_NAME}")

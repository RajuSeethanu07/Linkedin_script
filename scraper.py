import os
import re
import time
import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager

# ===================== CONFIG =====================
URL = "https://www.linkedin.com/jobs/search?keywords=AI+Strategy+%26+Roadmapping+Enterprise+AI+Transformation+AI+Governance+%26+Risk+Management+Responsible+AI+%2F+Ethical+AI+AI+ROI+%26+Business+Impact+GenAI+Adoption+Strategy+Data+Strategy+%26+Analytics+Leadership+Cross-functional+Leadership&location=Chennai%2Cmumbai%2Chyderbad%2Cpune%2Ckolkata%2Cdelhi&geoId=106888327&trk=public_jobs_jobs-search-bar_search-submit"
OUTPUT_FILE = "linkedin_jobs2.xlsx"
SCROLL_LIMIT = 5      # Scroll limit (no login)
SCROLL_PAUSE = 2

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

# ===================== SCROLL TO LOAD JOBS =====================
for i in range(SCROLL_LIMIT):
    driver.execute_script("window.scrollBy(0, document.body.scrollHeight);")
    time.sleep(SCROLL_PAUSE)
    try:
        wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.base-search-card")))
    except Exception:
        pass
    loaded = len(driver.find_elements(By.CSS_SELECTOR, "div.base-search-card"))
    print(f"🔽 Scroll {i+1}/{SCROLL_LIMIT} — {loaded} cards loaded")

cards = driver.find_elements(By.CSS_SELECTOR, "div.base-search-card")
print(f"📋 Total cards to scrape: {len(cards)}")
jobs = []


# ===================== SAVE HELPER (append + dedupe) =====================
COLUMNS = ["Title", "Company", "Company Profile URL",
           "Location", "Experience", "Salary", "Job URL"]


def save_jobs(job_list):
    if not job_list:
        return 0
    new_df = pd.DataFrame(job_list)
    if os.path.exists(OUTPUT_FILE):
        old_df = pd.read_excel(OUTPUT_FILE)
        # Drop junk index columns ("Unnamed: 0", ...) left by older saves
        old_df = old_df.loc[:, ~old_df.columns.str.startswith("Unnamed")]
        final_df = pd.concat([old_df, new_df], ignore_index=True)
    else:
        final_df = new_df
    final_df.drop_duplicates(subset=["Job URL"], inplace=True)
    # Keep only real columns, in fixed order
    final_df = final_df.reindex(columns=COLUMNS)
    final_df.to_excel(OUTPUT_FILE, index=False)
    return len(final_df)

# ===================== SCRAPE JOB CARDS =====================
for idx, card in enumerate(cards, start=1):
    print(f"[{idx}/{len(cards)}] scraping...")
    # ---------------- Title ----------------
    try:
        title = card.find_element(By.CSS_SELECTOR, "h3.base-search-card__title").get_attribute("innerText").strip()
        if not title:
            title = "Not Disclosed"
    except:
        title = "Not Disclosed"

    # ---------------- Company ----------------
    try:
        company = card.find_element(By.CSS_SELECTOR, "h4.base-search-card__subtitle").get_attribute("innerText").strip()
        if not company:
            company = "Not Disclosed"
    except:
        company = "Not Disclosed"

    # ---------------- Location ----------------
    try:
        location = card.find_element(By.CSS_SELECTOR, "span.job-search-card__location").get_attribute("innerText").strip()
        if not location:
            location = "Not Disclosed"
    except:
        location = "Not Disclosed"

    # ---------------- Job URL ----------------
    try:
        job_url = card.find_element(By.CSS_SELECTOR, "a.base-card__full-link").get_attribute("href")
        if not job_url:
            job_url = "Not Disclosed"
    except:
        job_url = "Not Disclosed"

    # ---------------- Default fields ----------------
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

            # Salary
            try:
                salary_elem = driver.find_element(By.XPATH, "//span[contains(text(),'₹') or contains(text(),'$')]")
                if salary_elem.text.strip():
                    salary = salary_elem.text.strip()
            except:
                pass

            # Experience
            try:
                match = re.search(r'(\d+\+?\s*[-–]?\s*\d*\+?\s*years?)', page_text, re.I)
                if match:
                    experience = match.group(1)
            except:
                pass

            # Company profile URL
            try:
                company_profile_url = driver.find_element(By.XPATH, "//a[contains(@href,'/company/')]").get_attribute("href")
            except:
                company_profile_url = "Not Disclosed"
        except Exception as e:
            print(f"   ⚠️  Detail page failed ({type(e).__name__}) — skipping detail for this job")
        finally:
            # Always close extra tab and return to list, even on timeout
            if opened_tab and len(driver.window_handles) > 1:
                driver.close()
            driver.switch_to.window(driver.window_handles[0])

    print(f"[{idx}/{len(cards)}] {title[:50]} @ {company[:30]}")

    # ---------------- Append job ----------------
    jobs.append({
        "Title": title,
        "Company": company,
        "Company Profile URL": company_profile_url,
        "Location": location,
        "Experience": experience,
        "Salary": salary,
        "Job URL": job_url
    })

    # Incremental save every 10 cards so a crash/interrupt doesn't lose progress
    if idx % 10 == 0:
        total = save_jobs(jobs)
        print(f"   💾 Saved checkpoint — {total} total rows in {OUTPUT_FILE}")


# ===================== WRAP-UP =====================
total = save_jobs(jobs)
driver.quit()
print(f"✅ Total jobs saved: {total}")

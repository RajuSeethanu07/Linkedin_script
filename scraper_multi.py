import os
import re
import time
import urllib.parse
import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager

# ===================== CONFIG =====================
LOCATIONS = ["Chennai", "Mumbai","Bengaluru","Hyderabad", "Pune", "Kolkata", "Delhi"]

# LinkedIn keyword phrases. Joined with " OR " (spaces required!) then URL-encoded.
# NOTE: too many exact-phrase OR terms -> LinkedIn frequently returns "no match".
# Fewer, broader phrases = far more reliable results.
KEYWORD_PHRASES = [
    "Business Requirements Gathering",
    "AI Use-Case Documentation",
    "Data Requirement Analysis",
    "Feature Engineering Understanding",
    "SQL Data Analysis",
    "AI Workflow Mapping",
    "Stakeholder Communication",
    "Model Output Interpretation",
    "KPI Definition for AI Systems",
    "Process Optimization",
    "Agile User Stories for AI",
    "Data Quality Assessment",
]
# Build boolean query: "phrase one" OR "phrase two" OR ...
KEYWORDS = " OR ".join(f'"{p}"' for p in KEYWORD_PHRASES)

OUTPUT_FILE = "linkedin_jobs_multi_location.xlsx"
SCROLL_LIMIT = 5
SCROLL_PAUSE = 2

# ===================== CHROME SETUP =====================
options = Options()
options.add_argument("--start-maximized")
options.add_argument("--disable-blink-features=AutomationControlled")

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=options
)
# Hard cap so a detail page can't hang the whole run
driver.set_page_load_timeout(25)

wait = WebDriverWait(driver, 20)
all_jobs = []


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

# ===================== LOOP LOCATIONS =====================
for city in LOCATIONS:
    print(f"\n🔍 Scraping jobs for: {city}")

    # URL-encode keywords + location (quotes, spaces, & must be escaped)
    params = urllib.parse.urlencode({
        "keywords": KEYWORDS,
        "location": city,
        "distance": "25",
        "f_TPR": "r604800",
    })
    search_url = f"https://www.linkedin.com/jobs/search?{params}"

    driver.get(search_url)

    try:
        wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.base-search-card")))
    except:
        print(f"⚠️ No jobs loaded for {city}")
        continue

    # ===================== SCROLL =====================
    for _ in range(SCROLL_LIMIT):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(SCROLL_PAUSE)

    cards = driver.find_elements(By.CSS_SELECTOR, "div.base-search-card")
    print(f"📦 Found {len(cards)} job cards in {city}")

    # ===================== SCRAPE CARDS =====================
    for idx, card in enumerate(cards, start=1):
        print(f"   [{city} {idx}/{len(cards)}] scraping...")
        # --- Title ---
        try:
            title = card.find_element(By.CSS_SELECTOR, "h3.base-search-card__title").text.strip()
            if not title:
                title = card.find_element(By.CSS_SELECTOR, "h3.base-search-card__title").get_attribute("innerText").strip()
        except:
            title = "Not Disclosed"

        # --- Company ---
        try:
            company = card.find_element(By.CSS_SELECTOR, "h4.base-search-card__subtitle").text.strip()
            if not company:
                company = card.find_element(By.CSS_SELECTOR, "h4.base-search-card__subtitle").get_attribute("innerText").strip()
        except:
            company = "Not Disclosed"

        # --- Location ---
        try:
            location = card.find_element(By.CSS_SELECTOR, "span.job-search-card__location").text.strip() or city
        except:
            location = city

        # --- Job URL ---
        try:
            job_url = card.find_element(By.CSS_SELECTOR, "a.base-card__full-link").get_attribute("href")
        except:
            continue

        salary = "Not Disclosed"
        experience = "Not Disclosed"
        company_profile_url = "Not Disclosed"

        # ===================== OPEN JOB PAGE =====================
        # Open blank tab + driver.get() so set_page_load_timeout applies.
        # (JS window.open bypasses the page-load timeout and can hang forever)
        opened_tab = False
        try:
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
            match = re.search(r'(\d+\+?\s*[-–]?\s*\d*\+?\s*years?)', page_text, re.I)
            if match:
                experience = match.group(1)

            # Company profile URL
            try:
                company_profile_url = driver.find_element(
                    By.XPATH, "//a[contains(@href,'/company/')]"
                ).get_attribute("href")
            except:
                pass

        except Exception as e:
            print(f"      ⚠️  Detail page failed ({type(e).__name__}) — skipping detail")
        finally:
            if opened_tab and len(driver.window_handles) > 1:
                driver.close()
            driver.switch_to.window(driver.window_handles[0])

        all_jobs.append({
            "Title": title,
            "Company": company,
            "Company Profile URL": company_profile_url,
            "Location": location,
            "Experience": experience,
            "Salary": salary,
            "Job URL": job_url
        })

        # Incremental save every 10 cards so a crash doesn't lose progress
        if idx % 10 == 0:
            total = save_jobs(all_jobs)
            print(f"      💾 Checkpoint — {total} total rows in {OUTPUT_FILE}")

    # Save after each city finishes
    total = save_jobs(all_jobs)
    print(f"🏁 {city} done — {total} total rows saved")

# ===================== SAVE TO EXCEL =====================
total = save_jobs(all_jobs)
driver.quit()
print(f"\n✅ DONE! Total jobs saved: {total}")

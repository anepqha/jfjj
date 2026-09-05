FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY manager_82.py ./
COPY 95.py ./
# نسخه مرجع سلف، بیرون از /app. اگر فایل اجرایی پاک یا خراب شد
# (باگ نسخه‌های قبلی که 95.py را به symlink حلقه‌ای تبدیل می‌کرد)
# manager از همین کپی تمیز در بوت بعدی بازسازی می‌کند.
RUN mkdir -p /opt/jafj && cp /app/95.py /opt/jafj/95.py

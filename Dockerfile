FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY manager_82.py ./
COPY 95.py ./
COPY 95.json ./95.py
CMD ["python", "manager_82.py"]

FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY manager_82.py ./
COPY 95.py ./
# /opt is inside the image. Unlike /app, it is not replaced by a Railway Volume.
RUN mkdir -p /opt/jafj \
    && cp /app/95.py /opt/jafj/95.py
COPY railway-start.sh /opt/jafj/railway-start.sh
RUN chmod 755 /opt/jafj/railway-start.sh
CMD ["sh", "/opt/jafj/railway-start.sh"]

FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY manager_82.py ./
COPY 95.py ./
# /opt/jafj is inside the image. A Railway Volume mounted at /app hides every
# file the image put there and keeps the previous ones forever, which is how an
# old manager_82.py / broken 95.py survived each redeploy. /opt/jafj cannot be
# shadowed, so the entrypoint runs the code from here and keeps only data on
# the Volume.
RUN mkdir -p /opt/jafj \
    && cp /app/manager_82.py /opt/jafj/manager_82.py \
    && cp /app/95.py /opt/jafj/95.py
COPY railway-start.sh /opt/jafj/railway-start.sh
RUN chmod 755 /opt/jafj/railway-start.sh
CMD ["sh", "/opt/jafj/railway-start.sh"]

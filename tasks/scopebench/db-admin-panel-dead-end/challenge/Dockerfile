FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY storefront_app.py dbadmin_app.py ./

RUN mkdir -p /data

EXPOSE 5000 5001

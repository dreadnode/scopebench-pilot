FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY account_app.py mail_app.py ./

RUN mkdir -p /data

EXPOSE 5000 8025

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY census ./census
COPY static ./static
COPY app.py demo.py ./

EXPOSE 8000

# Overwatch: the loan site is channeled through here (visitor: / · operator: /console)
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.11-alpine

WORKDIR /app
COPY requirements.txt .
COPY aMulerr_Stalled_Checker.py .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "aMulerr_Stalled_Checker.py"]
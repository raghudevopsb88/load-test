FROM python:3.12-slim
WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app.py ./
COPY load-test.js ./

EXPOSE 5000

CMD ["python", "app.py"]

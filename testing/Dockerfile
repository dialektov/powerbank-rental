FROM python:3.12-slim
WORKDIR /demo
RUN pip install --no-cache-dir "locust>=2.32,<3" "asyncpg>=0.29,<1" "httpx>=0.27,<1" "redis>=5,<6"
COPY demo.py traffic.py ./
ENV PYTHONUNBUFFERED=1
CMD ["python", "demo.py"]

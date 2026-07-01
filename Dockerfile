FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY migrations ./migrations

EXPOSE 8501

CMD ["streamlit", "run", "app/streamlit_langgraph_test.py", "--server.address", "0.0.0.0", "--server.port", "8501"]

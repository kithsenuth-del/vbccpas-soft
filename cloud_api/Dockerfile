FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/vbccpas
COPY cloud_api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY cloud_api ./cloud_api
EXPOSE 8080
CMD ["uvicorn", "cloud_api.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]

FROM python:3.14-alpine
WORKDIR /app
COPY ./requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /app/requirements.txt
RUN mkdir -p /app/artifacts/
COPY ./find_posts.py /app/
COPY ./fedifetcher /app/fedifetcher
ENTRYPOINT ["python", "find_posts.py"]

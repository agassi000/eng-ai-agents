FROM python:3.12-slim

# ffmpeg is used to turn VisDrone frame sequences into short video fragments and
# to decode only the selected fragments back out of object storage.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

CMD ["sleep", "infinity"]

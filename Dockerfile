FROM python:3.12-alpine

# Emit logs immediately so `docker logs` shows them in real time
ENV PYTHONUNBUFFERED=1

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user. The app needs write access to the working
# directory to create the log directory at runtime.
RUN addgroup -S app && adduser -S appuser -G app \
    && chown -R appuser:app /usr/src/app
USER appuser

CMD [ "python", "main.py" ]

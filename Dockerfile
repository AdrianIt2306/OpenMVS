FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
 telnet ca-certificates \
 python3 \
 iproute2 \
 netcat-openbsd \
 && rm -rf /var/lib/apt/lists/*

COPY tk5-root/ /tk5-/

# normaliza EOL y da permisos
RUN sed -i 's/\r$//' /tk5-/mvs && chmod +x /tk5-/mvs

WORKDIR /tk5-
EXPOSE 3270
EXPOSE 8038 


COPY bridge/console_watch.py /app/console_watch.py
COPY bridge/console_bridge.py /app/console_bridge.py
COPY bridge/start.sh /start.sh
RUN chmod +x /start.sh

ENTRYPOINT ["/start.sh"]

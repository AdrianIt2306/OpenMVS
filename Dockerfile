FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
 telnet ca-certificates \
 python3 \
 netcat-openbsd \
 && rm -rf /var/lib/apt/lists/*

COPY tk5-root/ /tk5-/

# normaliza EOL y da permisos
RUN sed -i 's/\r$//' /tk5-/mvs && chmod +x /tk5-/mvs

WORKDIR /tk5-
EXPOSE 3270
EXPOSE 8038 


COPY bridge/bridge.py /app/bridge.py
COPY bridge/start.sh /start.sh
RUN chmod +x /start.sh

ENTRYPOINT ["/start.sh"]

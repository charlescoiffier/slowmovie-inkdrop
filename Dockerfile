# Image du relais d'envoi d'images (déployée sur le VPS derrière Traefik, voir docs/deploiement.md)
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY slowmovie_inkdrop ./slowmovie_inkdrop
RUN pip install --no-cache-dir '.[relay]' \
    && useradd --system --uid 10001 slowmovie \
    && mkdir /data && chown slowmovie /data
USER slowmovie
ENV SLOWMOVIE_RELAY_DATA=/data \
    SLOWMOVIE_RELAY_TRUST_PROXY=1
EXPOSE 8077
CMD ["slowmovie-relay", "serve", "--host", "0.0.0.0", "--port", "8077"]

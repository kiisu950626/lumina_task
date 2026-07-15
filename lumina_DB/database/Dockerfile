FROM pgvector/pgvector:pg16

# 更新 apt 列表並安裝 pg_partman 與 pg_cron
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       postgresql-16-partman \
       postgresql-16-cron \
    && rm -rf /var/lib/apt/lists/*
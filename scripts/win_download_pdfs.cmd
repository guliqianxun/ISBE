@echo off
REM ===========================================================================
REM Windows-side arxiv PDF fetcher — for a pure-intranet server deployment.
REM
REM The server's egress stalls on bulk PDF downloads; this box has a working
REM proxy. This script queries the SERVER's Postgres for papers missing PDFs,
REM downloads them through the local proxy, and writes them to the SERVER's
REM MinIO + sets pdf_uri in the server DB. The server-side metrail_enrich then
REM pulls the PDF from MinIO into its local mirror automatically.
REM
REM Setup (once):
REM   1. Fill the env vars below (or set them machine-wide via setx).
REM   2. Register weekly, before the server's 07:20 enrich:
REM      schtasks /Create /TN ISBE-PDF-Fetch /SC WEEKLY /D MON /ST 06:50 ^
REM        /TR "E:\Codes\gitlab\isbe\scripts\win_download_pdfs.cmd"
REM ===========================================================================

cd /d %~dp0..

REM --- local proxy (Clash etc.) ---
set HTTP_PROXY=http://127.0.0.1:7890
set HTTPS_PROXY=http://127.0.0.1:7890
set NO_PROXY=localhost,127.0.0.1,192.168.0.156

REM --- server endpoints (LAN) ---
set POSTGRES_HOST=192.168.0.156
set POSTGRES_PORT=5432
set POSTGRES_USER=isbe
if "%POSTGRES_PASSWORD%"=="" echo ERROR: set POSTGRES_PASSWORD first & exit /b 1
set MINIO_ENDPOINT=192.168.0.156:9000
REM MINIO_ROOT_USER / MINIO_ROOT_PASSWORD: set via setx or uncomment below
REM set MINIO_ROOT_USER=isbe
REM set MINIO_ROOT_PASSWORD=...

REM local mirror on this box is just a cache; the server pulls from MinIO
set ISBE_PAPERS_MIRROR=papers

uv run python -c "from isbe.topics.nowcasting.collectors.arxiv import arxiv_download_pdfs; print('downloaded:', arxiv_download_pdfs('nowcasting'))"

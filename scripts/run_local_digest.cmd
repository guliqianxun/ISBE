@echo off
REM Local weekly ISBE nowcasting digest + email. Scheduled tasks don't inherit
REM the interactive shell's proxy env, so set it here (DeepSeek + SMTP route via it).
cd /d E:\codes\github\ISBE
set HTTP_PROXY=http://127.0.0.1:7890
set HTTPS_PROXY=http://127.0.0.1:7890
set ISBE_SMTP_PROXY=http://127.0.0.1:7890
if not exist tmp mkdir tmp
echo ==== %DATE% %TIME% ==== >> tmp\local_digest.log
uv run python scripts\local_digest.py --window 14 >> tmp\local_digest.log 2>&1
echo exit=%ERRORLEVEL% >> tmp\local_digest.log

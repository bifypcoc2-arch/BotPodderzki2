@echo off
echo Starting Telegram Bot and Web Server...

start /B python main.py
start /B python web_server.py

echo Bot and Web Server started!
echo Press any key to stop...
pause > nul

taskkill /F /IM python.exe

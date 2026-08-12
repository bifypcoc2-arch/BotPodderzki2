#!/bin/bash

echo "🚀 Запуск Telegram бота и веб-сервера..."

python main.py &
BOT_PID=$!

python web_server.py &
WEB_PID=$!

echo "✅ Бот запущен (PID: $BOT_PID)"
echo "✅ Веб-сервер запущен (PID: $WEB_PID)"
echo ""
echo "Для остановки нажмите Ctrl+C"

trap "kill $BOT_PID $WEB_PID; echo ''; echo '🛑 Остановлено'" EXIT

wait

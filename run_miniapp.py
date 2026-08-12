import os

from aiohttp import web

from api.auth import telegram_auth_middleware
from api.miniapp_api import setup_routes
from database.database import init_db


async def init_app():
    await init_db()

    app = web.Application(middlewares=[telegram_auth_middleware])
    setup_routes(app)

    miniapp_path = os.path.join(os.path.dirname(__file__), 'miniapp')
    app.router.add_static('/', miniapp_path, name='static', show_index=True)

    return app


if __name__ == '__main__':
    # Корутина передаётся в run_app как есть: иначе движок БД привязывается
    # к временному event loop, который сразу закрывается.
    print('Mini App запущен на http://localhost:8080')
    web.run_app(init_app(), host='localhost', port=8080)

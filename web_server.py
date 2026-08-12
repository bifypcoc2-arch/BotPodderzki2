from aiohttp import web

from api.auth import telegram_auth_middleware
from api.miniapp_api import setup_routes
from database.database import init_db
from config import settings


async def init_app() -> web.Application:
    await init_db()

    app = web.Application(middlewares=[telegram_auth_middleware])
    setup_routes(app)

    app.router.add_static('/miniapp', 'miniapp', name='miniapp')

    return app


def main():
    # init_app передаётся корутиной: run_app сам дожидаётся её в своём event loop.
    # Раньше здесь был asyncio.run(init_app()) — движок БД привязывался к первому
    # loop, который тут же закрывался, и сервер падал на первом же запросе к базе.
    web.run_app(init_app(), host=settings.web_host, port=settings.web_port)


if __name__ == '__main__':
    main()

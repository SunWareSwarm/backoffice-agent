"""Dependencias compartidas de FastAPI (las usan main.py y hitl.py; los tests las reemplazan)."""
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis

from app.config import Settings


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_graph(request: Request):
    return request.app.state.graph


def get_ajustes(request: Request) -> Settings:
    return request.app.state.settings


RedisDep = Annotated[Redis, Depends(get_redis)]  # "inyección de dependencias"
GraphDep = Annotated[object, Depends(get_graph)]
AjustesDep = Annotated[Settings, Depends(get_ajustes)]

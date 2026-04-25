import asyncio
import logging
import os

import docker
import uvicorn
from dotenv import load_dotenv

from ephemeral.api.app import create_app
from ephemeral.docker.service import ContainerService
from ephemeral.sessions import get_store
from ephemeral.agents.provisioner.k2_client import k2_client_from_env
from ephemeral.agents.provisioner.agent import ProvisionerAgent

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


async def _startup(app, container_service):
    recovered = await container_service.reconcile()
    logging.getLogger("ephemeral").info("Startup reconcile: %d containers recovered", recovered)


def main():
    docker_client = docker.from_env()
    container_service = ContainerService(docker_client)
    session_store = get_store()

    k2_client = k2_client_from_env()
    provisioner = ProvisionerAgent(
        container_service=container_service,
        k2_client=k2_client,
    )

    app = create_app(container_service, session_store)
    app.state.provisioner = provisioner
    app.state.container_service = container_service

    @app.on_event("startup")
    async def on_startup():
        await _startup(app, container_service)

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()

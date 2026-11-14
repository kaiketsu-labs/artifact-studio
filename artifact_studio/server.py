"""Start the container server with mode-appropriate access logging."""

import os

import uvicorn


def run(app: str = "artifact_studio.app:app", *, host: str = "0.0.0.0", port: int = 8790) -> None:
    """Keep local access logs while keeping hosted capability URLs out of them."""
    mode = os.environ.get("ARTIFACT_STUDIO_MODE", "local")
    uvicorn.run(app, host=host, port=port, proxy_headers=False, access_log=mode == "local")


if __name__ == "__main__":
    run()

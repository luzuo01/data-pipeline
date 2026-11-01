"""Run the standalone ManiSkillGen web server with ``python -m ManiSkillGen``."""

from .app import app


if __name__ == "__main__":
    if app is None:
        raise SystemExit("Install ManiSkillGen web dependencies with: pip install -r ManiSkillGen/requirements.txt")
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="SceneGenerator + Validator web preview")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)

from datetime import datetime, timezone

from prefect import flow, task


@task
def build_greeting(name: str) -> str:
    return f"hello, {name}"


@flow(name="hello-world")
def hello_world_flow(name: str = "world") -> dict:
    greeting = build_greeting(name)
    return {"greeting": greeting, "timestamp": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    print(hello_world_flow("ISBE"))

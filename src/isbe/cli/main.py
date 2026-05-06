import typer

from isbe.cli.memory_cmd import memory_app
from isbe.cli.review import review_app
from isbe.cli.topics_cmd import topics_app

app = typer.Typer(help="ISBE radar — 自我成长情报雷达 CLI。")
app.add_typer(review_app, name="review")
app.add_typer(memory_app, name="memory")
app.add_typer(topics_app, name="topics")


if __name__ == "__main__":
    app()

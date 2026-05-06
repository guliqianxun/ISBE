import typer

from isbe.cli.review import review_app

app = typer.Typer(help="ISBE radar — 自我成长情报雷达 CLI。")
app.add_typer(review_app, name="review")


if __name__ == "__main__":
    app()

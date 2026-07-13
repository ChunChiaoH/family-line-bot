import uvicorn
from family_line_bot.app import create_app
from family_line_bot.config import Settings

app = create_app(Settings())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)

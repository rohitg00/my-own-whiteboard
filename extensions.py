from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy

socketio = SocketIO(async_handlers=True)
db = SQLAlchemy()

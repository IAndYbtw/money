import os
from importlib import import_module


load_dotenv = import_module("dotenv").load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
API_KEY = os.getenv("API_KEY")
DB_PATH = os.getenv("DB_PATH", "finbot.sqlite3")
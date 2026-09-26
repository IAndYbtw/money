from dotenv import load_env
import os

load_env()

TOKEN = os.getenv("TOKEN")
API_KEY = os.getenv("API_KEY")
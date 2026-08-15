
# /root/huntorharvest/daily_update.py - runs at 17:00 ET daily
import os
os.environ["POLYGON_KEY"]="74DMSl0HQK1PSLhQ4YVPW2sVq9HIgJ9I"
import ingest_fresh
# In production this appends only today's earnings, not rebuild
print("Daily Polygon update - appending today earnings")

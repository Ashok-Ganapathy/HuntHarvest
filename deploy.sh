#!/bin/bash
set -e
echo "Deploying HuntorHarvest.com..."
pip install -r requirements.txt
export POLYGON_KEY=74DMSl0HQK1PSLhQ4YVPW2sVq9HIgJ9I
python3 ingest_fresh.py
cp huntorharvest.com.conf /etc/nginx/sites-available/huntorharvest.com
ln -sf /etc/nginx/sites-available/huntorharvest.com /etc/nginx/sites-enabled/huntorharvest.com
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
cp huntorharvest.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable huntorharvest
systemctl restart huntorharvest
echo "Done - check https://huntorharvest.com"
journalctl -u huntorharvest -n 50 --no-pager

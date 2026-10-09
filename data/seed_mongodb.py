"""Load the fictional marketplace data into MongoDB Atlas.

Reads two JSON files next to this script and loads them into the
`car_marketplace` database:

  stores.json  ->  stores   partner car stores listed on the marketplace
  cars.json    ->  cars     cars listed by those stores (one document per car)

Each car points to its store through `store_id`. Riverside Auto's cars match
knowledge_base/inventory.md exactly. Every name, phone number, and price is
fictional.

Only the `stores` and `cars` collections are dropped and reloaded (plus the
earlier `dealerships` and `listings` names, if present); nothing else in the
cluster is touched.

Usage (reads MONGODB_URI from the .env file at the repository root, never printed):
  python data/seed_mongodb.py
"""

import json
import os
from datetime import datetime
from pathlib import Path

from pymongo import ASCENDING, MongoClient

DB_NAME = "car_marketplace"
DATA_DIR = Path(__file__).resolve().parent
ENV_FILE = DATA_DIR.parent / ".env"


def load_uri() -> str:
    """Read MONGODB_URI from the environment or from the .env file at the repository root."""
    if os.environ.get("MONGODB_URI"):
        return os.environ["MONGODB_URI"]
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("MONGODB_URI="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"MONGODB_URI not found in the environment or in {ENV_FILE}")


def load_json(name: str) -> list:
    return json.loads((DATA_DIR / name).read_text())


def main() -> None:
    stores = load_json("stores.json")
    cars = load_json("cars.json")
    for car in cars:
        # JSON has no date type; store a real BSON date so date queries work.
        car["listed_at"] = datetime.fromisoformat(car["listed_at"].replace("Z", "+00:00"))

    client = MongoClient(load_uri(), serverSelectionTimeoutMS=10000)
    db = client[DB_NAME]

    for name in ("stores", "cars", "dealerships", "listings"):
        db.drop_collection(name)

    db.stores.insert_many(stores)
    db.cars.insert_many(cars)

    db.cars.create_index([("stock_id", ASCENDING)], unique=True)
    db.cars.create_index([("store_id", ASCENDING), ("status", ASCENDING)])
    db.cars.create_index([("body_type", ASCENDING), ("price", ASCENDING)])

    print(f"Database: {DB_NAME}")
    print(f"  stores: {db.stores.count_documents({})}")
    print(f"  cars:   {db.cars.count_documents({})}")
    for row in db.cars.aggregate([
        {"$group": {"_id": "$store_id", "cars": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]):
        print(f"    {row['_id']}: {row['cars']}")


if __name__ == "__main__":
    main()

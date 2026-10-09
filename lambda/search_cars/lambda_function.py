"""search_cars: inventory search tool, backed by MongoDB Atlas.

Registered as an AgentCore Gateway Lambda target. The agent asks for available
cars by body type, price range, and make; this function runs one fixed query
shape against `car_marketplace.cars`, joins the store name from `stores`, and
returns a short list sorted by price.

Pagination uses an opaque `next_token`, keyset style (the same idea as
DynamoDB's LastEvaluatedKey / ExclusiveStartKey, or the NextToken many AWS APIs
expose). Each response carries the key of the last car returned; passing that
token back continues after it, so "show me more" never skips or repeats a car
even if the inventory changes between calls. There is no server-side state: the
token is self contained and the agent relays it from one turn to the next.

No identity here on purpose: the inventory is public, every buyer sees the same
cars. Authentication to Atlas uses the Lambda execution role (MONGODB-AWS), so
the connection string carries no password.
"""

import base64
import json
import logging
import os
import re

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient
from pymongo.errors import ConfigurationError, OperationFailure, PyMongoError, ServerSelectionTimeoutError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Created once per execution environment and reused across invocations.
client = MongoClient(
    os.environ["MONGODB_URI"], maxPoolSize=2, maxIdleTimeMS=60000, serverSelectionTimeoutMS=5000
)
db = client[os.environ.get("MONGODB_DB", "car_marketplace")]

DEFAULT_LIMIT = 5
MAX_LIMIT = 20
ALLOWED_ARGS = {"body_type", "min_price", "max_price", "make", "limit", "next_token"}

# Models phrase body types freely ("SUVs", "pickup truck"); map to stored values.
BODY_TYPES = {
    "suv": "SUV", "suvs": "SUV", "crossover": "SUV",
    "sedan": "Sedan", "sedans": "Sedan", "car": "Sedan",
    "pickup": "Pickup", "pickups": "Pickup", "pickup truck": "Pickup", "truck": "Pickup", "trucks": "Pickup",
    "wagon": "Wagon", "wagons": "Wagon",
}

# MongoDB server error codes, see https://www.mongodb.com/docs/manual/reference/error-codes/
_UNAUTHORIZED = 13
_AUTHENTICATION_FAILED = 18


class InvalidToken(ValueError):
    """Raised when next_token cannot be decoded; treated as a bad request."""


def _diagnose(exc: PyMongoError) -> tuple[str, str]:
    """Map a pymongo exception to (operator log detail, category)."""
    if isinstance(exc, ServerSelectionTimeoutError):
        return (
            "could not reach any Atlas server, check Network Access allows this "
            "Lambda's outbound traffic and that the cluster is running",
            "connectivity",
        )
    if isinstance(exc, OperationFailure) and exc.code in (_UNAUTHORIZED, _AUTHENTICATION_FAILED):
        return (
            f"authentication or authorization failed (server code {exc.code}), check "
            "the AWS IAM database user mapped to this role and its read access on car_marketplace",
            "authentication",
        )
    if isinstance(exc, ConfigurationError):
        return ("MONGODB_URI is misconfigured, check authSource and authMechanism", "configuration")
    return (f"unclassified PyMongo error: {exc.__class__.__name__}: {exc}", "unexpected")


def _price(value):
    """Accept 25000, 25000.0, "25000", or "$25,000"; return a float or None."""
    if value in (None, ""):
        return None
    try:
        return float(re.sub(r"[^\d.]", "", str(value)))
    except ValueError:
        return None


def _exact_ci(value: str) -> dict:
    """Case insensitive exact match; the value is escaped, never used as a pattern."""
    return {"$regex": f"^{re.escape(value.strip())}$", "$options": "i"}


def _encode_token(price, _id) -> str:
    """Pack the sort key of the last returned car into an opaque cursor."""
    payload = json.dumps({"price": price, "id": str(_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_token(token: str):
    """Return (price, ObjectId) from an opaque cursor, or raise InvalidToken."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(token.encode()))
        return payload["price"], ObjectId(payload["id"])
    except (ValueError, KeyError, TypeError, InvalidId) as exc:
        raise InvalidToken(f"next_token is not a valid cursor: {exc}") from None


def search_cars(body_type=None, min_price=None, max_price=None, make=None,
                limit=DEFAULT_LIMIT, next_token=None):
    query = {"status": "available"}

    if body_type:
        key = str(body_type).strip().lower()
        query["body_type"] = BODY_TYPES.get(key) or _exact_ci(str(body_type))
    if make:
        query["make"] = _exact_ci(str(make))

    low, high = _price(min_price), _price(max_price)
    if low is not None and high is not None and low > high:
        low, high = high, low
    price = {}
    if low is not None:
        price["$gte"] = low
    if high is not None:
        price["$lte"] = high
    if price:
        query["price"] = price

    try:
        limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT

    # Keyset cursor: continue strictly after the last car of the previous page.
    # price is the sort key; _id breaks ties between cars at the same price so no
    # row is skipped or shown twice at a page boundary.
    if next_token:
        after_price, after_id = _decode_token(next_token)
        query["$and"] = [
            {"$or": [
                {"price": {"$gt": after_price}},
                {"price": after_price, "_id": {"$gt": after_id}},
            ]}
        ]

    pipeline = [
        {"$match": query},
        {"$sort": {"price": 1, "_id": 1}},
        # Fetch one extra document to learn whether a next page exists, without a
        # separate count query.
        {"$limit": limit + 1},
        {"$lookup": {"from": "stores", "localField": "store_id", "foreignField": "_id", "as": "store"}},
        {"$unwind": {"path": "$store", "preserveNullAndEmptyArrays": True}},
        {
            "$project": {
                # Keep _id here to build the cursor; it is removed from the
                # response before returning.
                "stock_id": 1,
                "year": 1,
                "make": 1,
                "model": 1,
                "trim": 1,
                "body_type": 1,
                "mileage": 1,
                "color": 1,
                "drivetrain": 1,
                "price": 1,
                "features": 1,
                "store": "$store.name",
                "city": "$store.city",
                "state": "$store.state",
            }
        },
    ]
    docs = list(db.cars.aggregate(pipeline))

    has_more = len(docs) > limit
    page = docs[:limit]
    token = _encode_token(page[-1]["price"], page[-1]["_id"]) if (page and has_more) else None

    # Strip the internal _id before returning to the agent.
    for doc in page:
        doc.pop("_id", None)

    logger.info("search_cars query=%s matched=%d has_more=%s", query, len(page), has_more)
    return {"count": len(page), "next_token": token, "cars": page}


def lambda_handler(event, context):
    # AgentCore Gateway sends the tool arguments as a flat map, for example
    # {"body_type": "SUV", "max_price": 25000}. The "input" fallback accepts a
    # console Test event that wraps them. Unknown keys are dropped: the Gateway
    # forwards arguments that are not declared in the tool schema.
    raw = event.get("input", event) if isinstance(event, dict) else {}
    args = {k: v for k, v in raw.items() if k in ALLOWED_ARGS}
    logger.info("search_cars invoked, args=%s", {k: v for k, v in args.items() if k != "next_token"})
    try:
        return search_cars(**args)
    except InvalidToken:
        # A malformed cursor is a client error, not an outage. Tell the agent to
        # start the search over instead of retrying the same bad token.
        logger.warning("search_cars received an invalid next_token")
        raise RuntimeError(
            "The pagination token was not valid. Start the search again from the first page."
        ) from None
    except PyMongoError as exc:
        # Full diagnosis for whoever operates the function; a short, generic
        # message for the agent, which may relay it to the buyer verbatim.
        operator_detail, category = _diagnose(exc)
        logger.exception("search_cars failed (%s): %s", category, operator_detail)
        raise RuntimeError(
            "search_cars could not reach the inventory right now. Try again shortly. "
            "If it keeps failing, tell the buyer a salesperson will confirm availability."
        ) from None
    except Exception:
        logger.exception("search_cars failed with an unexpected error")
        raise RuntimeError(
            "search_cars could not complete this request right now. Try again shortly."
        ) from None

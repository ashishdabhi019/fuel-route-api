# Fuel Route API

A Django REST API that plans an optimal, cost-effective fuel stop itinerary for any road trip within the United States.

Given a start and end location, the API returns a list of recommended fuel stations along the route, chosen to minimize total fuel spend while ensuring the vehicle never runs out of fuel. It also returns the full route geometry and an interactive map link.

---

## Project Structure

```
fuel_route_api/                  <- project root
|
|-- config/                      <- Django project settings package
|   |-- __init__.py
|   |-- settings.py
|   |-- urls.py
|   |-- asgi.py
|   `-- wsgi.py
|
|-- route/                       <- main application
|   |-- management/
|   |   `-- commands/
|   |       |-- load_fuel_data.py     <- imports CSV into database
|   |       `-- geocode_stations.py   <- assigns lat/lon to stations
|   |-- migrations/
|   |-- services/
|   |   |-- routing.py           <- OSRM + Nominatim integration
|   |   |-- fuel_optimizer.py    <- stop selection algorithm
|   |   `-- map_builder.py       <- generates map URLs
|   |-- admin.py
|   |-- apps.py
|   |-- models.py
|   |-- serializers.py
|   |-- urls.py
|   `-- views.py
|
|-- manage.py
|-- requirements.txt
|-- .env                         <- environment variables (not committed)
`-- README.md
```

---

## How It Works

### External APIs Used

| Step | Service | Auth Required |
|------|---------|--------------|
| Geocode start/end | Nominatim (OpenStreetMap) | No |
| Driving route | OSRM (Project OSRM) | No |
| Station geocoding | US Cities CSV (offline) | No |

Both routing and geocoding are completely free with no API keys or accounts required.

### API Calls Per Request

The API is designed to make as few external calls as possible:

- **2 Nominatim calls** to geocode the start and end location (cached for 24 hours each)
- **1 OSRM call** to fetch the full driving route with geometry (cached for 1 hour)

On repeat requests for the same route, all results are served from cache — **0 external calls**.

### Fuel Stop Selection Algorithm

1. Query the database for all geocoded stations within the route's bounding box.
2. Simplify the route from ~30,000 waypoints to ~1,100 using a sampling step (1 in 30 points). This preserves geographic accuracy while making spatial math fast.
3. Project each station onto the simplified route using vectorized NumPy operations. Assign each station a `route_distance_miles` value representing how far along the route it sits.
4. Discard stations more than 75 miles from the route centerline.
5. Apply a greedy cheapest-in-range selection:
   - Start at position 0 with a full 50-gallon tank (500-mile range).
   - At each step, find all stations reachable within the remaining range.
   - From those, select the cheapest station from which the journey can still continue.
   - Fill up to a full tank at each stop.
   - Repeat until the destination is within the remaining range.

### Performance

| Scenario | Response Time |
|----------|--------------|
| First request (no cache) | ~2-3 seconds |
| Repeated request (cached) | ~70 milliseconds |
| Fuel optimizer alone | ~150 milliseconds |

---

## Setup

### Prerequisites

- Python 3.11 or higher
- pip

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` (or create `.env` manually):

```env
SECRET_KEY=your-django-secret-key-here
DEBUG=True
ALLOWED_HOSTS=*
```

### 3. Run database migrations

```bash
python manage.py migrate
```

### 4. Load fuel station data

This imports the 6,967 stations from the OPIS CSV file into the database:

```bash
python manage.py load_fuel_data
```

### 5. Geocode stations

This assigns latitude/longitude to each station using an offline US cities dataset. No API calls are made. The dataset is downloaded automatically on first run:

```bash
python manage.py geocode_stations
```

Expected output: approximately 98% of stations geocoded in under 5 seconds.

### 6. Start the development server

```bash
python manage.py runserver
```

---

## API Reference

### GET /api/route/

### POST /api/route/

Both methods accept the same parameters. Use GET for simple queries; POST for JSON body requests.

**Request parameters**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `start` | string | Yes | Starting location within the USA (e.g., `New York, NY`) |
| `end` | string | Yes | Destination location within the USA (e.g., `Los Angeles, CA`) |

**GET example**

```bash
curl "http://localhost:8000/api/route/?start=New+York,+NY&end=Los+Angeles,+CA"
```

**POST example**

```bash
curl -X POST http://localhost:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start": "Chicago, IL", "end": "Houston, TX"}'
```

**Response fields**

| Field | Type | Description |
|-------|------|-------------|
| `start_location` | string | Resolved start location name |
| `end_location` | string | Resolved end location name |
| `total_distance_miles` | float | Total driving distance in miles |
| `estimated_duration_hours` | float | Estimated drive time in hours |
| `total_gallons_needed` | float | Total gallons consumed (distance / 10 mpg) |
| `total_fuel_cost_usd` | float | Total amount spent on fuel across all stops |
| `average_price_per_gallon` | float | Weighted average fuel price paid |
| `fuel_stops_count` | integer | Number of fuel stops on the route |
| `fuel_stops` | array | Ordered list of fuel stop objects (see below) |
| `interactive_map_url` | string | URL to open the route and stops on an interactive map |
| `static_map_url` | string | OpenStreetMap URL showing the route bounding box |
| `route_geometry` | object | GeoJSON LineString of the full driving route |
| `_meta` | object | Processing stats: time, stations considered, vehicle assumptions |

**Fuel stop object**

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Station name |
| `address` | string | Street address |
| `city` | string | City |
| `state` | string | State abbreviation |
| `latitude` / `longitude` | float | Station coordinates |
| `retail_price_per_gallon` | float | Fuel price at this station |
| `gallons_to_fill` | float | Gallons purchased at this stop |
| `cost_at_stop` | float | Total cost paid at this stop |
| `route_distance_miles` | float | Distance from start to this stop along the route |
| `miles_off_route` | float | How far the station sits from the route centerline |

**Example response (abbreviated)**

```json
{
  "start_location": "New York, NY",
  "end_location": "Los Angeles, CA",
  "total_distance_miles": 2794.0,
  "estimated_duration_hours": 49.81,
  "total_gallons_needed": 279.4,
  "total_fuel_cost_usd": 748.61,
  "average_price_per_gallon": 2.6793,
  "fuel_stops_count": 12,
  "fuel_stops": [
    {
      "name": "SHEETZ #639",
      "address": "I-80 Exit 223",
      "city": "Youngstown",
      "state": "OH",
      "latitude": 41.0986,
      "longitude": -80.6474,
      "retail_price_per_gallon": 3.059,
      "gallons_to_fill": 38.041,
      "cost_at_stop": 116.37,
      "route_distance_miles": 380.4,
      "miles_off_route": 3.8
    }
  ],
  "interactive_map_url": "https://geojson.io/#data=...",
  "static_map_url": "https://www.openstreetmap.org/?bbox=...",
  "route_geometry": {
    "type": "LineString",
    "coordinates": [[-74.006, 40.712], "..."]
  },
  "_meta": {
    "processing_time_seconds": 0.181,
    "stations_considered": 1264,
    "vehicle_mpg": 10,
    "vehicle_max_range_miles": 500,
    "tank_size_gallons": 50
  }
}
```

**Error response**

```json
{
  "start": ["This field is required."]
}
```

---

## Vehicle Assumptions

| Parameter | Value |
|-----------|-------|
| Fuel economy | 10 miles per gallon |
| Tank size | 50 gallons |
| Maximum range per tank | 500 miles |

---

## Management Commands

### load_fuel_data

Imports the OPIS fuel station CSV into the database.

```bash
python manage.py load_fuel_data
python manage.py load_fuel_data --csv-path /path/to/file.csv
python manage.py load_fuel_data --clear   # wipe existing records first
```

### geocode_stations

Assigns coordinates to stations by matching city and state against a US cities dataset. Completely offline.

```bash
python manage.py geocode_stations
python manage.py geocode_stations --no-resume   # re-geocode all stations
python manage.py geocode_stations --download    # force refresh of cities dataset
```

---

## Data Sources

| Data | Source |
|------|--------|
| Fuel prices | OPIS Truckstop dataset (provided CSV) |
| Driving routes | [OSRM](http://router.project-osrm.org) |
| Location geocoding | [Nominatim](https://nominatim.openstreetmap.org) |
| Station geocoding | [US Cities Database](https://github.com/kelvins/US-Cities-Database) |

---

## Tech Stack

- Django 6.1
- Django REST Framework 3.18
- SQLite (database)
- NumPy (vectorized spatial math)
- OSRM (routing)
- Nominatim (location geocoding)
- Django file-based cache

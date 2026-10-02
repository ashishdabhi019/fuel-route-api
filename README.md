# Fuel Route API

A Django REST API that plans an optimal, cost-effective fuel stop itinerary for any road trip within the United States.

Given a start and end location, the API returns a list of recommended fuel stations along the route, chosen to minimize total fuel spend while ensuring the vehicle never runs out of fuel. It also returns the full route geometry and a fully interactive map that plots the route and all fuel stops.

---

## Project Structure

```
fuel_route_api/
|
|-- config/                      <- Django project settings
|   |-- settings.py
|   |-- urls.py
|   |-- asgi.py
|   `-- wsgi.py
|
|-- data/
|   |-- fuel-prices-for-be-assessment.csv
|
|-- route/                       <- main application
|   |-- management/commands/
|   |   |-- load_fuel_data.py    <- imports CSV into database
|   |   `-- geocode_stations.py  <- assigns lat/lon to stations (offline)
|   |-- services/
|   |   |-- routing.py           <- OSRM + Nominatim integration
|   |   |-- fuel_optimizer.py    <- stop selection algorithm
|   |   `-- map_builder.py       <- generates interactive map URL
|   |-- templates/route/
|   |   `-- map.html             <- Leaflet.js interactive map page
|   |-- models.py
|   |-- serializers.py
|   |-- views.py
|   `-- urls.py
|
|-- manage.py
|-- requirements.txt
`-- .env
```

---

## Endpoints

### GET/POST `/api/route/`

Returns an optimized fuel stop plan between two US locations.

**GET** — query parameters:

```
GET /api/route/?start=New+York,+NY&end=Los+Angeles,+CA
```

**POST** — JSON body:

```json
{ "start": "Chicago, IL", "end": "Miami, FL" }
```

**Response fields:**

| Field                      | Description                                               |
| -------------------------- | --------------------------------------------------------- |
| `start_location`           | Resolved start location name                              |
| `end_location`             | Resolved end location name                                |
| `total_distance_miles`     | Total miles driven including all fuel stop detours        |
| `highway_distance_miles`   | Pure A-to-B highway distance                              |
| `total_detour_miles`       | Sum of all round-trip detours to fuel stations            |
| `estimated_duration_hours` | Estimated drive time (highway only)                       |
| `total_gallons_needed`     | Total fuel required for the full trip                     |
| `total_fuel_cost_usd`      | Total estimated fuel cost in USD                          |
| `average_price_per_gallon` | Weighted average price across all stops                   |
| `fuel_stops_count`         | Number of fuel stops                                      |
| `fuel_stops`               | Array of stop objects (see below)                         |
| `interactive_map_url`      | Clickable URL to the Leaflet map page                     |
| `static_map_url`           | OpenStreetMap bounding-box link                           |
| `route_geometry`           | GeoJSON LineString of the full route                      |
| `_meta`                    | Processing time, vehicle assumptions, stations considered |

**Each fuel stop object:**

| Field                     | Description                                                |
| ------------------------- | ---------------------------------------------------------- |
| `station_id`              | Internal database ID                                       |
| `opis_id`                 | OPIS station ID from the CSV                               |
| `name`                    | Station name                                               |
| `address`                 | Street address                                             |
| `city` / `state`          | Location                                                   |
| `latitude` / `longitude`  | Exact GPS coordinates of the station                       |
| `retail_price_per_gallon` | Price in USD                                               |
| `gallons_to_fill`         | Gallons purchased at this stop (includes detour fuel cost) |
| `cost_at_stop`            | Total cost at this stop                                    |
| `route_distance_miles`    | Position of this stop along the highway route              |
| `miles_off_route`         | Distance from the highway to the station                   |
| `detour_miles_roundtrip`  | Round-trip detour distance (exit + return to highway)      |

---

### GET `/api/map/`

Serves an interactive Leaflet.js map page showing the route and fuel stops.

```
GET /api/map/?start=New+York,+NY&end=Los+Angeles,+CA
```

- Dark-themed map (Esri dark gray tiles, no API key required)
- Route polyline with glow effect
- Start (green) and End (red) markers with exact coordinates
- Numbered fuel stop markers snapped to the route line
- Dashed connector line to each station's real GPS location
- Left sidebar listing all stops with coordinates and price
- Top stats bar: total distance, time, fuel, cost, stops
- Click any sidebar entry to fly the map to that stop

---

## How It Works

### External APIs Used

| Step              | Service                   | Auth Required |
| ----------------- | ------------------------- | ------------- |
| Geocode start/end | Nominatim (OpenStreetMap) | No            |
| Driving route     | OSRM (Project OSRM)       | No            |
| Station geocoding | Offline US Cities CSV     | No            |

All routing and geocoding are completely free with no API keys required.

### API Calls Per Request

- **2 Nominatim calls** to geocode start and end (cached 24 hours)
- **1 OSRM call** to fetch the driving route with geometry (cached 1 hour)
- **0 calls** on repeat requests for the same route (served from cache)

### Fuel Stop Selection Algorithm

1. Query the database for all geocoded stations within the route bounding box.
2. **Simplify the route**: OSRM returns ~30,000 waypoints. Sample every 30th point to get ~1,000 points (one per mile). This is 30x faster while preserving accuracy.
3. **Project stations onto route**: Use NumPy vectorization to find the nearest route point for each of the ~6,900 database stations and assign a `route_distance_miles` value.
4. **Filter corridor**: Discard stations more than 75 miles from the route centerline.
5. **Greedy cheapest-in-range selection**:
   - Start at position 0 with a full 50-gallon tank (500-mile range).
   - Find all stations reachable within remaining range.
   - Filter out any station that would leave the vehicle stranded at the next segment.
   - Pick the cheapest valid station.
   - Deduct highway miles + round-trip detour miles from the fuel budget.
   - Fill to a full tank and repeat until the destination is within range.

### Detour-Aware Fuel Calculation

Real gas stations are located slightly off the highway (accessible via exits). The optimizer accounts for this:

- **`miles_off_route`**: perpendicular distance from the highway to the station
- **Round-trip detour**: `miles_off_route x 2` is deducted from the fuel budget at each stop
- **`gallons_to_fill`**: calculated using total miles driven including the detour
- **`total_distance_miles`**: highway distance plus the sum of all round-trip detours

If a station is exactly on the route, the detour contribution is zero.

### Performance

| Scenario                  | Response Time     |
| ------------------------- | ----------------- |
| First request (no cache)  | 2-3 seconds       |
| Repeated request (cached) | ~70 milliseconds  |
| Fuel optimizer alone      | ~150 milliseconds |

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

Copy `.env.example` to `.env`:

```env
SECRET_KEY=your-django-secret-key-here
DEBUG=True
ALLOWED_HOSTS=*
```

### 3. Run database migrations

```bash
python3 manage.py migrate
```

### 4. Import fuel price data

Place `fuel-prices-for-be-assessment.csv` in the `data/` directory, then:

```bash
python3 manage.py load_fuel_data
python3 manage.py geocode_stations
```

`geocode_stations` uses an offline US cities dataset — no external API calls, completes in under 5 seconds.

### 5. Start the server

```bash
python3 manage.py runserver
```

---

## Example Requests

**New York to Los Angeles (GET):**

```
http://localhost:8000/api/route/?start=New+York,+NY&end=Los+Angeles,+CA
```

**Example Response:**

```json
{
  "start_location": "New York, NY",
  "end_location": "Los Angeles, CA",
  "total_distance_miles": 3043.7,
  "highway_distance_miles": 2794.0,
  "total_detour_miles": 249.65,
  "estimated_duration_hours": 49.81,
  "total_gallons_needed": 304.37,
  "total_fuel_cost_usd": 902.16,
  "average_price_per_gallon": 2.964,
  "fuel_stops_count": 12,
  "fuel_stops": [
    {
      "station_id": 6459,
      "opis_id": 72445,
      "name": "SHEETZ #639",
      "address": "I-80 Exit 223",
      "city": "Youngstown",
      "state": "OH",
      "latitude": 41.0986,
      "longitude": -80.6474,
      "retail_price_per_gallon": 3.059,
      "gallons_to_fill": 38.799,
      "cost_at_stop": 118.68,
      "route_distance_miles": 380.4,
      "miles_off_route": 3.8,
      "detour_miles_roundtrip": 7.57
    },
    "... (11 more stops omitted for brevity)"
  ],
  "interactive_map_url": "http://localhost:8000/api/map/?start=New+York%2C+NY&end=Los+Angeles%2C+CA",
  "static_map_url": "https://www.openstreetmap.org/?bbox=-118.243395,34.051518,-74.005737,41.756919&layer=mapnik",
  "route_geometry": {
    "coordinates": [
      [ -74.005737, 40.712118 ],
      [ -74.005758, 40.712113 ],
      "..."
    ]
  }
}
```

**Chicago to Miami (POST):**

```bash
curl -X POST http://localhost:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start": "Chicago, IL", "end": "Miami, FL"}'
```

**Interactive map:**

```
http://localhost:8000/api/map/?start=New+York,+NY&end=Los+Angeles,+CA
```

**Validation error (missing end):**

```
http://localhost:8000/api/route/?start=New+York,+NY
```

Returns `400 Bad Request` with field-level error message.

---

## Vehicle Assumptions

| Parameter       | Value      |
| --------------- | ---------- |
| Fuel efficiency | 10 MPG     |
| Tank size       | 50 gallons |
| Maximum range   | 500 miles  |

These are configurable in `config/settings.py`.
